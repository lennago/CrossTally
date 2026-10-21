"""The decoy under '/admin/' keeps no session key: for whoever is signed in, it is the sign-in.

Until 10/2026 every knock recorded the key of the visitor's session. For a
manager who typed '/admin/' that was the key of their live session. The
administration showed it on the detail page, every backup carried the row
for 30 days, and set as a cookie the key signed in as that manager.
"""

from importlib import import_module

from django.apps import apps
from django.test import Client, TestCase
from django.urls import reverse

from accounts.models import Anmeldeversuch
from accounts.test_passkey import Authenticator
from django_project.testing import administrator, fuehrungskraft

migration = import_module("accounts.migrations.0006_anmeldeversuch_ohne_sitzung")


def sitzungsschluessel(browser: Client) -> str:
    return browser.cookies["sessionid"].value


class KeinSchluesselImVersuch(TestCase):
    def setUp(self):
        self.leitung = fuehrungskraft(email="leitung@example.com")
        self.browser = Client()
        self.browser.force_login(self.leitung)

    def klopfen(self, browser: Client) -> Anmeldeversuch:
        browser.post(reverse("honeypot:login"), {"username": "admin", "password": "101"})
        return Anmeldeversuch.objects.latest("pk")

    def test_eine_angemeldete_fuehrungskraft_hinterlaesst_ihren_schluessel_nicht(self):
        schluessel = sitzungsschluessel(self.browser)

        versuch = self.klopfen(self.browser)

        self.assertIsNone(versuch.session_key)
        self.assertNotIn(schluessel, repr(Anmeldeversuch.objects.values().get(pk=versuch.pk)))
        # The decoy left the session alone: the manager is still signed in.
        self.assertEqual(self.browser.get(reverse("passkey_list")).status_code, 200)

    def test_ein_besucher_ohne_anmeldung_ebenso(self):
        versuch = self.klopfen(Client())

        self.assertIsNone(versuch.session_key)
        self.assertEqual(versuch.username, "admin")

    def test_der_passkey_der_attrappe_ebenso(self):
        geraet = Authenticator()
        frage = self.browser.post("/admin/passkey/options/").json()
        credential = geraet.anmeldung(frage["challenge"], b"101")

        self.browser.post(
            "/admin/passkey/anmelden/",
            {"credential": credential},
            content_type="application/json",
        )

        versuch = Anmeldeversuch.objects.latest("pk")
        self.assertTrue(versuch.username.startswith("passkey:"))
        self.assertIsNone(versuch.session_key)

    def test_die_verwaltung_zeigt_keine_spalte_dafuer(self):
        """An old row may still carry a key, until the migration has run or a backup is loaded."""
        alt = Anmeldeversuch.objects.create(
            username="admin", session_key="schluessel101", path="/admin/"
        )
        verwaltung = Client()
        verwaltung.force_login(administrator(email="chef@example.com"))

        seite = verwaltung.get(reverse("admin:accounts_anmeldeversuch_change", args=[alt.pk]))

        self.assertEqual(seite.status_code, 200)
        self.assertNotContains(seite, "schluessel101")


class DieMigrationLeertDieSpalte(TestCase):
    """'accounts/0006' clears what the decoy wrote before. The column stays, it is the package's."""

    def test_vorhandene_schluessel_verschwinden(self):
        mit = Anmeldeversuch.objects.create(username="a", session_key="schluessel101")
        ohne = Anmeldeversuch.objects.create(username="b")

        migration.leeren(apps, None)

        self.assertEqual(
            list(Anmeldeversuch.objects.order_by("pk").values_list("pk", "session_key")),
            [(mit.pk, None), (ohne.pk, None)],
        )

    def test_rueckwaerts_kommt_nichts_wieder(self):
        operation = migration.Migration.operations[0]
        self.assertIs(operation.reverse_code, operation.noop)
