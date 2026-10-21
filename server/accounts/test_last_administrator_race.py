"""Two administrators take each other's way into the administration at the same moment.

Each removes the other's last passkey, deletes the other, deactivates or
demotes the other. One at a time, the second step is refused
('test_administrators'). Side by side, each one asked while the other was
still there, and until 10/2026 both went through: nobody got into the
administration any more. Now the step that writes asks under the lock of the
administrators' rows ('administratoren_sperren', 'letzter_administrator_geht').
The first side is held after its question or right before it writes, until
the second one waits for the lock or is done. Runs in the CI, which tests
against PostgreSQL.
"""

import threading
from http import HTTPStatus
from unittest import mock, skipUnless

from django.db import connection
from django.test import Client, TransactionTestCase
from django.urls import reverse

from accounts import views as konto_ansichten
from accounts.admin import CustomUserAdmin
from accounts.models import CustomUser, Passkey
from accounts.test_notification import formular, verwaltungsformular
from api import passkey as api_passkey
from api.test_closing_race import _bis_jemand_wartet
from api.test_passkey import in_der_app_entfernen
from django_project.roles import administratoren
from django_project.testing import administrator


def nur_mit_passkey(username: str, kennung: str) -> CustomUser:
    konto = administrator(username=username, email=f"{username}@example.com")
    konto.set_unusable_password()
    konto.save()
    Passkey.objects.create(
        benutzer=konto, kennung=kennung, oeffentlicher_schluessel=b"x", name="Handy"
    )
    return konto


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class ZweiLetztePasskeysGleichzeitig(TransactionTestCase):
    serialized_rollback = True

    def entfernen(self, konto: CustomUser) -> int:
        return in_der_app_entfernen(konto, Passkey.objects.get(benutzer=konto).pk).status_code

    def test_einer_bleibt(self):
        anna = nur_mit_passkey("anna", "abc")
        bernd = nur_mit_passkey("bernd", "def")
        echt = api_passkey.nimmt_den_letzten_weg
        gehalten = threading.Event()
        fertig = threading.Event()
        ergebnis: dict = {}

        def halten(passkeys):
            antwort = echt(passkeys)
            if not gehalten.is_set():
                gehalten.set()
                _bis_jemand_wartet(fertig)
            return antwort

        def laufen(seite, konto):
            if seite == "danach":
                gehalten.wait(timeout=10)
            try:
                ergebnis[seite] = self.entfernen(konto)
            except Exception as fehler:  # The test names it, the thread would hide it.
                ergebnis[f"{seite}_fehler"] = fehler
            finally:
                if seite == "danach":
                    fertig.set()
                connection.close()

        with mock.patch.object(api_passkey, "nimmt_den_letzten_weg", side_effect=halten):
            faeden = [
                threading.Thread(target=laufen, args=("zuerst", anna)),
                threading.Thread(target=laufen, args=("danach", bernd)),
            ]
            for faden in faeden:
                faden.start()
            for faden in faeden:
                faden.join()

        self.assertNotIn("zuerst_fehler", ergebnis)
        self.assertNotIn("danach_fehler", ergebnis)
        self.assertEqual(ergebnis["zuerst"], 204)
        self.assertEqual(ergebnis["danach"], 400)
        self.assertFalse(Passkey.objects.filter(benutzer=anna).exists())
        self.assertTrue(Passkey.objects.filter(benutzer=bernd).exists())


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class ZweiAdministratorenNehmenEinanderGleichzeitig(TransactionTestCase):
    """Anna takes Bernd and Bernd takes Anna, on the website and in the administration.

    Exactly one of the two stays, whichever order the lock gives them.
    """

    serialized_rollback = True

    def setUp(self):
        self.anna = administrator(
            "anna", email="anna@example.com", first_name="Anna", last_name="Adler"
        )
        self.bernd = administrator(
            "bernd", email="bernd@example.com", first_name="Bernd", last_name="Bär"
        )

    def gleichzeitig(self, zuerst, danach, halt) -> dict:
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
            if not gehalten.is_set():
                gehalten.set()
                _bis_jemand_wartet(fertig)
            return echt(*args, **kwargs)

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

        with mock.patch.object(ziel, name, autospec=True, side_effect=halten):
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

    def senden(self, wer: CustomUser, adresse: str, daten: dict | None = None):
        """Signed in now, before the race: signing in writes the account's row."""
        client = Client(raise_request_exception=False)
        client.force_login(wer)
        return lambda: client.post(adresse, daten or {})

    def pruefe_es_bleibt(self, konto: CustomUser) -> None:
        self.assertEqual(
            list(administratoren().values_list("username", flat=True)), [konto.username]
        )

    def test_loeschen_auf_der_website(self):
        """Anna holds the lock while she deletes. Bernd waits, asks again and is refused."""
        ergebnis = self.gleichzeitig(
            self.senden(self.anna, reverse("user_delete", args=[self.bernd.pk])),
            self.senden(self.bernd, reverse("user_delete", args=[self.anna.pk])),
            (konto_ansichten, "spuren_loeschen"),
        )

        self.assertRedirects(
            ergebnis["zuerst"], reverse("user_list"), fetch_redirect_response=False
        )
        self.assertRedirects(
            ergebnis["danach"], self.anna.get_absolute_url(), fetch_redirect_response=False
        )
        self.assertFalse(CustomUser.objects.filter(pk=self.bernd.pk).exists())
        self.pruefe_es_bleibt(self.anna)

    def test_deaktivieren_auf_der_website(self):
        """Anna is held before she saves. Bernd saves first, and Anna asks again and is refused."""
        ergebnis = self.gleichzeitig(
            self.senden(
                self.anna,
                reverse("user_edit", args=[self.bernd.pk]),
                formular(self.bernd, is_active=False),
            ),
            self.senden(
                self.bernd,
                reverse("user_edit", args=[self.anna.pk]),
                formular(self.anna, is_active=False),
            ),
            (konto_ansichten.UserUpdateView, "form_valid"),
        )

        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.FOUND)
        self.assertContains(
            ergebnis["zuerst"], "Das ist der letzte Administrator", status_code=HTTPStatus.OK
        )
        self.pruefe_es_bleibt(self.bernd)

    def test_loeschen_in_der_verwaltung(self):
        ergebnis = self.gleichzeitig(
            self.senden(
                self.anna,
                reverse("admin:accounts_customuser_delete", args=[self.bernd.pk]),
                {"post": "yes"},
            ),
            self.senden(
                self.bernd,
                reverse("admin:accounts_customuser_delete", args=[self.anna.pk]),
                {"post": "yes"},
            ),
            (CustomUserAdmin, "delete_model"),
        )

        self.assertRedirects(
            ergebnis["zuerst"],
            reverse("admin:accounts_customuser_changelist"),
            fetch_redirect_response=False,
        )
        self.assertRedirects(
            ergebnis["danach"],
            reverse("admin:accounts_customuser_change", args=[self.anna.pk]),
            fetch_redirect_response=False,
        )
        self.assertFalse(CustomUser.objects.filter(pk=self.bernd.pk).exists())
        self.pruefe_es_bleibt(self.anna)

    def test_ausgewaehlt_loeschen_in_der_verwaltung(self):
        def auswahl(konto: CustomUser) -> dict:
            return {"action": "delete_selected", "_selected_action": [konto.pk], "post": "yes"}

        liste = reverse("admin:accounts_customuser_changelist")
        ergebnis = self.gleichzeitig(
            self.senden(self.anna, liste, auswahl(self.bernd)),
            self.senden(self.bernd, liste, auswahl(self.anna)),
            (CustomUserAdmin, "delete_queryset"),
        )

        for seite in ("zuerst", "danach"):
            self.assertEqual(ergebnis[seite].status_code, HTTPStatus.FOUND, seite)
        self.assertFalse(CustomUser.objects.filter(pk=self.bernd.pk).exists())
        self.pruefe_es_bleibt(self.anna)

    def test_herabstufen_und_deaktivieren_in_der_verwaltung(self):
        """Anna is held before she saves. Bernd saves first, and Anna's change goes back whole."""
        bernd_aendern = reverse("admin:accounts_customuser_change", args=[self.bernd.pk])
        ergebnis = self.gleichzeitig(
            self.senden(
                self.anna,
                bernd_aendern,
                verwaltungsformular(self.bernd, is_superuser=False, last_name="Biber"),
            ),
            self.senden(
                self.bernd,
                reverse("admin:accounts_customuser_change", args=[self.anna.pk]),
                verwaltungsformular(self.anna, is_active=False),
            ),
            (CustomUserAdmin, "save_model"),
        )

        self.assertRedirects(
            ergebnis["danach"],
            reverse("admin:accounts_customuser_changelist"),
            fetch_redirect_response=False,
        )
        self.assertRedirects(ergebnis["zuerst"], bernd_aendern, fetch_redirect_response=False)
        self.pruefe_es_bleibt(self.bernd)
        self.assertEqual(CustomUser.objects.get(pk=self.bernd.pk).last_name, "Bär")
