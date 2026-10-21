"""Two answers to the same question of the app at the same moment, and one of them opens.

Whoever sees the body of a request but not its answer (a proxy, a firewall,
an extension of the browser) sends the same challenge and credential again.
Until 10/2026 the signature of the question was all the server asked, for
two minutes. The library compares the counter only where the key counts,
and even then both answers read the stored count before either wrote it:
both got a token pair. Now the first answer deletes the row of its question
('Passkeyfrage'), and the second finds none. The first side is held until
the other one waits for a lock or is done
('api.test_closing_race._bis_jemand_wartet'). Runs in the CI, which tests
against PostgreSQL.
"""

import threading
from http import HTTPStatus
from unittest import mock, skipUnless

from django.db import connection, transaction
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

from accounts import passkey as website_passkey
from accounts.passkey import user_handle
from accounts.test_sign_out_race import MitEchtemSchluessel, Nebeneinander
from api import passkey as api_passkey
from api.test_closing_race import _bis_jemand_wartet

from .tests import adresse


def anmelden(koerper: dict):
    return APIClient(raise_request_exception=False).post(
        adresse("passkey_anmelden_api"), koerper, format="json"
    )


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class ZweiAntwortenGleichzeitig(MitEchtemSchluessel, Nebeneinander):
    """The phone answers, and a copy of its request arrives beside it."""

    def setUp(self):
        super().setUp()
        self.passkey = self.schluessel_des_geraets()

    def ohne_zaehler(self) -> dict:
        """The answer of a synced passkey, which counts nothing."""
        schritt = APIClient().post(adresse("passkey_anmelden_optionen_api")).data
        credential = self.geraet.anmeldung(
            schritt["options"]["challenge"], user_handle(self.zoe), counter=0
        )
        return {"credential": credential, "challenge": schritt["challenge"]}

    def pruefe_einmal(self, ergebnis: dict) -> None:
        """One pair, and the other side hears that the question is gone."""
        antworten = sorted((ergebnis["zuerst"], ergebnis["danach"]), key=lambda a: a.status_code)
        self.assertEqual(
            [antwort.status_code for antwort in antworten], [HTTPStatus.OK, HTTPStatus.BAD_REQUEST]
        )
        self.assertEqual(antworten[1].data, {"detail": str(website_passkey.ABGELAUFEN)})
        self.assertEqual(OutstandingToken.objects.filter(user=self.zoe).count(), 1)

    def test_mit_einem_schluessel_der_zaehlt(self):
        """Both read the count before either wrote it, so the library let both through."""
        koerper = self.in_der_app_vorbereitet()
        ergebnis = self.gleichzeitig(
            lambda: anmelden(koerper),
            lambda: anmelden(koerper),
            (website_passkey, "verify_authentication_response"),
        )
        self.pruefe_einmal(ergebnis)

    def test_mit_einem_schluessel_der_nicht_zaehlt(self):
        koerper = self.ohne_zaehler()
        ergebnis = self.gleichzeitig(
            lambda: anmelden(koerper),
            lambda: anmelden(koerper),
            (website_passkey, "verify_authentication_response"),
        )
        self.pruefe_einmal(ergebnis)

    def test_zwei_loeschungen_derselben_zeile(self):
        """One statement takes the row: of two deletes in flight, the second waits and finds none.

        The first side deletes in a transaction held open, as a slow
        database would hold it, and the second one waits for its row.
        """
        koerper = self.ohne_zaehler()
        echt = api_passkey.frage_beantworten
        gehalten, fertig = threading.Event(), threading.Event()
        ergebnis: dict = {}

        def offen_gehalten(challenge: bytes) -> bool:
            if gehalten.is_set():
                return echt(challenge)
            with transaction.atomic():
                beantwortet = echt(challenge)
                gehalten.set()
                _bis_jemand_wartet(fertig)
            return beantwortet

        def laufen(seite: str) -> None:
            if seite == "danach":
                gehalten.wait(timeout=10)
            try:
                ergebnis[seite] = anmelden(koerper)
            finally:
                if seite == "danach":
                    fertig.set()
                connection.close()

        with mock.patch.object(api_passkey, "frage_beantworten", side_effect=offen_gehalten):
            faeden = [
                threading.Thread(target=laufen, args=(seite,)) for seite in ("zuerst", "danach")
            ]
            for faden in faeden:
                faden.start()
            for faden in faeden:
                faden.join()

        self.assertTrue(gehalten.is_set(), "ohne Halt prüft der Test nichts")
        self.pruefe_einmal(ergebnis)
