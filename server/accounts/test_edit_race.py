"""Another request changes or deletes the account while its form is saved, on PostgreSQL.

'test_edit_changed_fields' changes the account inside the request itself.
There, refusing the change of a deleted account in the administration rolls
the deletion back with it. Here the other side is a connection of its own
that commits: a new password stays, and a deleted account stays deleted. The
first side is held while its form checks the values, or right after its
saving step has read the stored state under the account's lock. Runs in the
CI, which tests against PostgreSQL.
"""

import threading
from http import HTTPStatus
from unittest import mock, skipUnless

from django.contrib.auth.hashers import make_password
from django.db import connection
from django.test import Client, TransactionTestCase
from django.urls import reverse

from accounts.models import CustomUser
from api.test_closing_race import _bis_jemand_wartet
from django_project.testing import administrator

from .forms import RollenbeschriftungMixin
from .notification import Kontostand
from .test_notification import formular, konto_anlegen, verwaltungsformular

NEU = "ganz-anderes-Passwort-47"


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class NebenDerAenderung(TransactionTestCase):
    serialized_rollback = True

    def setUp(self):
        self.nele = konto_anlegen()
        self.chef = administrator(email="chef@example.com")

    def gleichzeitig(self, zuerst, danach, halt, *, nach_dem_aufruf: bool = False) -> dict:
        """Runs 'zuerst' until it reaches 'halt', then 'danach' beside it.

        'zuerst' goes on once 'danach' waits for a lock or is done. A server
        error comes back as the answer ('raise_request_exception=False').
        """
        ziel, name = halt
        echt = getattr(ziel, name)
        gehalten = threading.Event()
        fertig = threading.Event()
        ergebnis: dict = {}

        def halten(*args, **kwargs):
            if gehalten.is_set():
                return echt(*args, **kwargs)
            gehalten.set()
            if not nach_dem_aufruf:
                _bis_jemand_wartet(fertig)
                return echt(*args, **kwargs)
            gelesen = echt(*args, **kwargs)
            _bis_jemand_wartet(fertig)
            return gelesen

        def laufen(seite, tun):
            if seite == "danach":
                gehalten.wait(timeout=10)
            try:
                ergebnis[seite] = tun()
            except Exception as fehler:  # The test names it, the thread would hide it.
                ergebnis[f"{seite}_fehler"] = fehler
            finally:
                if seite == "danach":
                    fertig.set()
                connection.close()

        with mock.patch.object(ziel, name, halten):
            faeden = [
                threading.Thread(target=laufen, args=("zuerst", zuerst)),
                threading.Thread(target=laufen, args=("danach", danach)),
            ]
            for faden in faeden:
                faden.start()
            for faden in faeden:
                faden.join()
        self.assertTrue(gehalten.is_set(), "ohne Halt prüft der Test nichts")
        self.assertNotIn("zuerst_fehler", ergebnis)
        self.assertNotIn("danach_fehler", ergebnis)
        return ergebnis

    def bearbeiten(self, adresse_name: str, daten: dict):
        """Signed in now, before the race: signing in writes the account's row."""
        client = Client(raise_request_exception=False)
        client.force_login(self.chef)
        return lambda: client.post(reverse(adresse_name, args=[self.nele.pk]), daten)

    def loeschen(self):
        return CustomUser.objects.filter(pk=self.nele.pk).delete()

    def in_der_verwaltung(self):
        """Renamed in the administration's change form."""
        daten = verwaltungsformular(self.nele, first_name="Nela")
        return self.bearbeiten("admin:accounts_customuser_change", daten)

    def test_geloescht_waehrend_die_verwaltung_prueft(self):
        ergebnis = self.gleichzeitig(
            self.in_der_verwaltung(),
            self.loeschen,
            (RollenbeschriftungMixin, "clean"),
        )

        self.assertRedirects(
            ergebnis["zuerst"],
            reverse("admin:accounts_customuser_changelist"),
            fetch_redirect_response=False,
        )
        self.assertFalse(CustomUser.objects.filter(pk=self.nele.pk).exists())

    def test_geloescht_waehrend_die_website_prueft(self):
        ergebnis = self.gleichzeitig(
            self.bearbeiten("user_edit", formular(self.nele, first_name="Nela")),
            self.loeschen,
            (RollenbeschriftungMixin, "clean"),
        )

        self.assertRedirects(
            ergebnis["zuerst"], reverse("user_list"), fetch_redirect_response=False
        )
        self.assertFalse(CustomUser.objects.filter(pk=self.nele.pk).exists())

    def test_das_loeschen_wartet_auf_die_aenderung(self):
        """Held under the account's lock: the deletion waits, then deletes what was saved."""
        ergebnis = self.gleichzeitig(
            self.bearbeiten("user_edit", formular(self.nele, first_name="Nela")),
            self.loeschen,
            (Kontostand, "aus_der_datenbank"),
            nach_dem_aufruf=True,
        )

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.FOUND)
        self.assertEqual(ergebnis["danach"][1]["accounts.CustomUser"], 1)
        self.assertFalse(CustomUser.objects.filter(pk=self.nele.pk).exists())

    def test_ein_neues_passwort_von_nebenan_bleibt(self):
        ergebnis = self.gleichzeitig(
            self.in_der_verwaltung(),
            lambda: CustomUser.objects.filter(pk=self.nele.pk).update(password=make_password(NEU)),
            (RollenbeschriftungMixin, "clean"),
        )

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.FOUND)
        konto = CustomUser.objects.get(pk=self.nele.pk)
        self.assertTrue(konto.check_password(NEU))
        self.assertEqual(konto.first_name, "Nela")
