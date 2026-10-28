"""Two writers on one SQLite file: the second waits for the first instead of failing.

Development and the end-to-end run work on SQLite. A transaction there begins
as a reader unless told otherwise. If it reads first and writes after, while
another connection writes, SQLite refuses the step from reading to writing at
once, without the wait of the busy timeout. The end-to-end run met it
(10/2026): the app's owed sign-out ('nach_kennung_sperren') reads the token
and then blocks it, another request wrote at the same moment, and the answer
was a 500 "database is locked". The probe takes the settings of
'settings.py' and an empty file of its own, not the database of the suite.
"""

import tempfile
import threading
import time
from pathlib import Path

from django.db import connections, transaction
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.utils import ConnectionHandler
from django.test import SimpleTestCase

from .test_settings import geladen

PROBE = "sqlite_probe"


class ZweiSchreibende(SimpleTestCase):
    def setUp(self):
        ordner = tempfile.TemporaryDirectory()
        self.addCleanup(ordner.cleanup)
        datei = Path(ordner.name) / "probe.sqlite3"
        datenbank = geladen(DATABASE_URL=f"sqlite:///{datei}").DATABASES["default"]
        # One connection per thread, as for the requests of 'runserver'. A
        # handler insists on a 'default', which stays unused. Under that name
        # 'SimpleTestCase' forbids every connection.
        self.verbindungen = ConnectionHandler({"default": datenbank, PROBE: datenbank})
        verbindung = self.eigene_verbindung()
        try:
            with verbindung.cursor() as cursor:
                cursor.execute("CREATE TABLE zeile (nummer integer)")
        finally:
            self.schliessen(verbindung)

    def eigene_verbindung(self) -> BaseDatabaseWrapper:
        """This thread's connection to the file, reachable for 'transaction.atomic'."""
        verbindung = self.verbindungen[PROBE]
        connections[PROBE] = verbindung
        return verbindung

    def schliessen(self, verbindung: BaseDatabaseWrapper) -> None:
        verbindung.close()
        del connections[PROBE]

    def test_wer_erst_liest_und_dann_schreibt_wartet_auf_den_anderen(self):
        schreibt = threading.Event()

        def erster():
            verbindung = self.eigene_verbindung()
            try:
                with transaction.atomic(using=PROBE), verbindung.cursor() as cursor:
                    cursor.execute("INSERT INTO zeile VALUES (1)")
                    schreibt.set()
                    # Holds the lock while the second one begins.
                    time.sleep(0.3)
            finally:
                self.schliessen(verbindung)

        faden = threading.Thread(target=erster)
        faden.start()
        self.addCleanup(faden.join)
        self.assertTrue(schreibt.wait(5))

        verbindung = self.eigene_verbindung()
        try:
            with transaction.atomic(using=PROBE), verbindung.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM zeile")
                cursor.execute("INSERT INTO zeile VALUES (2)")
            with verbindung.cursor() as cursor:
                cursor.execute("SELECT nummer FROM zeile ORDER BY nummer")
                self.assertEqual(cursor.fetchall(), [(1,), (2,)])
        finally:
            self.schliessen(verbindung)
