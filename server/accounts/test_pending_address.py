"""A new address waits in the database for its confirmation, and every revocation takes it back.

Until 10/2026 the link carried the change itself, signed, and nothing
stored it. Nothing could take it back either: a new password, signing out
everywhere and deactivating left it valid for its three days, a used one
came back to life once the address went back to the old one, a newer one
did not replace it, and a deactivated account took it without hearing of
it. Now the link signs the number of a row ('Adresswechsel'), and whatever
should end it deletes the row.
"""

import io
from datetime import timedelta

from django.contrib.auth.tokens import default_token_generator
from django.core import mail, signing
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import Adresswechsel, CustomUser, Passkey
from api.tests import adresse
from django_project import backup
from django_project.testing import administrator

from . import email_change
from .email_change import SALZ
from .invitation import einladungslink
from .test_email_change import link_aus
from .test_notification import PASSWORT, formular, konto_anlegen, verwaltungsformular

NEU = "nele@example.org"
NOCH_NEUER = "nele.neu@example.org"
NEUES_PASSWORT = "ganz-anderes-Passwort-47"


class OffenerWechsel(TestCase):
    """Nele asks for a new address on the website. The link of the mail is the test's."""

    def setUp(self):
        self.nele = konto_anlegen()
        self.chef = administrator(email="chef@example.com", first_name="Conrad", last_name="Chef")

    def eintragen(self, email: str = NEU) -> str:
        """The person's own request, signed in on the website. Returns the link of the mail."""
        browser = self.client_class()
        browser.force_login(self.nele)
        antwort = browser.post(reverse("email_aendern"), {"email": email, "passwort": PASSWORT})
        self.assertRedirects(antwort, reverse("email_fertig"))
        return link_aus(mail.outbox[-1])

    def bestaetigen(self, link: str) -> str:
        """Presses the button behind the link, as the person would. Returns the address then."""
        self.client_class().post(link)
        self.nele.refresh_from_db()
        return self.nele.email

    def pruefe_tot(self, link: str, *, zurueckgenommen: bool = True) -> None:
        """The link opens nothing, and its button writes nothing.

        Taken back, the account has no waiting change left. An expired or
        replaced link, or one of an account switched off past the forms,
        leaves its row to the clean-up.
        """
        self.assertContains(self.client_class().get(link), "Link ungültig")
        self.assertEqual(self.bestaetigen(link), "nele@example.com")
        if zurueckgenommen:
            self.assertFalse(Adresswechsel.objects.filter(konto=self.nele).exists())


class DerLinkNenntEineZeile(OffenerWechsel):
    def test_die_anfrage_legt_eine_zeile_an(self):
        self.eintragen()

        wechsel = Adresswechsel.objects.get()
        self.assertEqual(
            (wechsel.konto, wechsel.neu, wechsel.alt, wechsel.von, wechsel.fremd),
            (self.nele, NEU, "nele@example.com", None, False),
        )
        self.assertEqual(CustomUser.objects.get(pk=self.nele.pk).email, "nele@example.com")

    def test_der_link_signiert_nur_die_nummer_der_zeile(self):
        link = self.eintragen()

        signiert = link.rstrip("/").rsplit("/", 1)[1]
        self.assertEqual(signing.loads(signiert, salt=SALZ), Adresswechsel.objects.get().pk)
        self.assertNotIn(NEU, signing.b64_decode(signiert.split(":")[0].encode()).decode())

    def test_benutzt_ist_die_zeile_weg(self):
        link = self.eintragen()

        self.assertEqual(self.bestaetigen(link), NEU)

        self.assertFalse(Adresswechsel.objects.exists())

    def test_ein_benutzter_link_lebt_nicht_wieder_auf(self):
        """The address goes back to the old one: the link of before stays dead.

        Until 10/2026 it only asked whether the account carried the old
        address, and then the same link moved it once more.
        """
        link = self.eintragen()
        self.assertEqual(self.bestaetigen(link), NEU)
        CustomUser.objects.filter(pk=self.nele.pk).update(email="nele@example.com")

        self.pruefe_tot(link)

    def test_ein_neuer_wunsch_ersetzt_den_alten(self):
        erster = self.eintragen()
        zweiter = self.eintragen(NOCH_NEUER)

        self.pruefe_tot(erster, zurueckgenommen=False)
        self.assertEqual(self.bestaetigen(zweiter), NOCH_NEUER)

    def test_ein_link_im_alten_format_oeffnet_nichts(self):
        """Signed before the rollout, it names the change itself and no row: it is dead."""
        alt = signing.dumps(
            {"pk": self.nele.pk, "neu": NEU, "alt": "nele@example.com", "von": None},
            salt="accounts.email_change",
        )
        link = reverse("email_bestaetigen", kwargs={"token": alt})

        self.pruefe_tot(link)

    def test_ein_abgelaufener_wird_abgewiesen_und_beim_aufraeumen_entfernt(self):
        link = self.eintragen()
        frist = timedelta(days=3, seconds=1)
        Adresswechsel.objects.update(erstellt_am=timezone.now() - frist)
        frisch = Adresswechsel.objects.create(
            konto=self.chef, neu="chef.neu@example.com", alt=self.chef.email
        )

        self.pruefe_tot(link, zurueckgenommen=False)
        call_command("cleanup", stdout=io.StringIO())

        self.assertEqual(list(Adresswechsel.objects.values_list("pk", flat=True)), [frisch.pk])

    def test_ein_deaktiviertes_konto_nimmt_den_link_nicht_an(self):
        """Switched off past the forms: the link is refused on the page and under the lock."""
        link = self.eintragen()
        CustomUser.objects.filter(pk=self.nele.pk).update(is_active=False)

        self.pruefe_tot(link, zurueckgenommen=False)
        self.assertEqual(len(mail.outbox), 1)

    def test_wer_sie_eingetragen_hat_bleibt_auch_wenn_sein_konto_geht(self):
        """The administrator is deleted meanwhile: the mail names nobody and asks nobody either."""
        browser = self.client_class()
        browser.force_login(self.chef)
        browser.post(reverse("user_edit", args=[self.nele.pk]), formular(self.nele, email=NEU))
        link = link_aus(mail.outbox[-1])
        self.assertEqual(Adresswechsel.objects.get().von, self.chef)
        self.chef.delete()

        self.assertEqual(self.bestaetigen(link), NEU)

        nachricht = mail.outbox[-1]
        self.assertNotIn("Geändert von", nachricht.body)
        self.assertNotIn("Warst du das nicht?", nachricht.body)

    def test_die_zeile_gehoert_nicht_ins_backup(self):
        """A restored state signs everyone out, and yesterday's link has no business coming back."""
        self.eintragen()
        ausgabe = io.StringIO()

        call_command("dumpdata", *backup.APPS, stdout=ausgabe)

        self.assertNotIn("accounts.adresswechsel", ausgabe.getvalue())
        self.assertIn("accounts.customuser", ausgabe.getvalue())

    def test_ohne_mailserver_entsteht_keine_zeile(self):
        """There the address applies at once. Nothing waits, nothing to take back."""
        with override_settings(
            MAILERS={"default": {"BACKEND": "django.core.mail.backends.console.EmailBackend"}},
            DEBUG=False,
        ):
            browser = self.client_class()
            browser.force_login(self.nele)
            browser.post(reverse("email_aendern"), {"email": NEU, "passwort": PASSWORT})

        self.assertEqual(CustomUser.objects.get(pk=self.nele.pk).email, NEU)
        self.assertFalse(Adresswechsel.objects.exists())


class EinNeuesPasswortNimmtIhnZurueck(OffenerWechsel):
    """Every way to a new password: website, app, reset, invitation, administration."""

    def test_auf_der_website(self):
        link = self.eintragen()
        browser = self.client_class()
        browser.force_login(self.nele)

        browser.post(
            reverse("password_change"),
            {
                "old_password": PASSWORT,
                "new_password1": NEUES_PASSWORT,
                "new_password2": NEUES_PASSWORT,
            },
        )

        self.pruefe_tot(link)

    def test_in_der_app(self):
        link = self.eintragen()
        app = APIClient()
        app.credentials(
            HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(self.nele).access_token}"
        )

        antwort = app.post(
            adresse("passwort_aendern"),
            {"altes_passwort": PASSWORT, "neues_passwort": NEUES_PASSWORT},
            format="json",
        )

        self.assertEqual(antwort.status_code, 200)
        self.pruefe_tot(link)

    def test_ueber_passwort_vergessen(self):
        link = self.eintragen()
        browser = self.client_class()
        uid = urlsafe_base64_encode(force_bytes(self.nele.pk))
        schritt = browser.get(
            reverse(
                "password_reset_confirm",
                kwargs={"uidb64": uid, "token": default_token_generator.make_token(self.nele)},
            )
        )

        browser.post(
            schritt["Location"],
            {"new_password1": NEUES_PASSWORT, "new_password2": NEUES_PASSWORT},
        )

        self.assertTrue(CustomUser.objects.get(pk=self.nele.pk).check_password(NEUES_PASSWORT))
        self.pruefe_tot(link)

    def test_ueber_die_einladung(self):
        """Switched on by hand while invited, an administrator gave it a new address."""
        CustomUser.objects.filter(pk=self.nele.pk).update(password="!", last_login=None)
        self.nele.refresh_from_db()
        browser = self.client_class()
        browser.force_login(self.chef)
        browser.post(reverse("user_edit", args=[self.nele.pk]), formular(self.nele, email=NEU))
        link = link_aus(mail.outbox[-1])
        einladung = einladungslink(self.nele, browser.get(reverse("user_list")).wsgi_request)
        CustomUser.objects.filter(pk=self.nele.pk).update(last_login=None)

        gast = self.client_class()
        schritt = gast.get(einladung)
        gast.post(
            schritt["Location"],
            {"new_password1": NEUES_PASSWORT, "new_password2": NEUES_PASSWORT},
        )

        self.assertTrue(CustomUser.objects.get(pk=self.nele.pk).check_password(NEUES_PASSWORT))
        self.pruefe_tot(link)

    def test_in_der_verwaltung_gesetzt_oder_abgeschaltet(self):
        for felder in (
            {"usable_password": "true", "password1": NEUES_PASSWORT, "password2": NEUES_PASSWORT},
            {"usable_password": "false", "unset-password": "1"},
        ):
            with self.subTest(felder=felder):
                link = self.eintragen()
                browser = self.client_class()
                browser.force_login(self.chef)

                antwort = browser.post(
                    reverse("admin:auth_user_password_change", args=[self.nele.pk]), felder
                )

                self.assertEqual(antwort.status_code, 302)
                self.pruefe_tot(link)
                self.nele.set_password(PASSWORT)
                self.nele.save(update_fields=["password"])


class AbmeldenUeberallNimmtIhnZurueck(OffenerWechsel):
    """A removed passkey, a deactivation and signing everyone out end the link with the sign-ins."""

    def schluessel(self) -> Passkey:
        return Passkey.objects.create(
            benutzer=self.nele, kennung="k101", oeffentlicher_schluessel=b"x", name="Handy"
        )

    def test_ein_entfernter_passkey_auf_der_website(self):
        passkey = self.schluessel()
        link = self.eintragen()
        browser = self.client_class()
        browser.force_login(self.nele)

        browser.post(reverse("passkey_delete", args=[passkey.pk]), {"passwort": PASSWORT})

        self.pruefe_tot(link)

    def test_ein_entfernter_passkey_in_der_app(self):
        passkey = self.schluessel()
        link = self.eintragen()
        eigenes = RefreshToken.for_user(self.nele)
        app = APIClient()
        app.credentials(HTTP_AUTHORIZATION=f"Bearer {eigenes.access_token}")

        antwort = app.delete(
            adresse("passkey_loeschen_api", passkey.pk),
            {"refresh": str(eigenes), "passwort": PASSWORT},
            format="json",
        )

        self.assertEqual(antwort.status_code, 204)
        self.pruefe_tot(link)

    def test_ein_entfernter_passkey_in_der_verwaltung(self):
        passkey = self.schluessel()
        link = self.eintragen()
        browser = self.client_class()
        browser.force_login(self.chef)

        with self.captureOnCommitCallbacks(execute=True):
            browser.post(
                reverse("admin:accounts_passkey_delete", args=[passkey.pk]), {"post": "yes"}
            )

        self.assertFalse(Passkey.objects.exists())
        self.pruefe_tot(link)

    def test_deaktivieren_auf_der_website(self):
        link = self.eintragen()
        browser = self.client_class()
        browser.force_login(self.chef)

        browser.post(
            reverse("user_edit", args=[self.nele.pk]), formular(self.nele, is_active=False)
        )
        browser.post(reverse("user_edit", args=[self.nele.pk]), formular(self.nele, is_active=True))

        self.assertTrue(CustomUser.objects.get(pk=self.nele.pk).is_active)
        self.pruefe_tot(link)

    def test_deaktivieren_in_der_verwaltung(self):
        link = self.eintragen()
        browser = self.client_class()
        browser.force_login(self.chef)
        ziel = reverse("admin:accounts_customuser_change", args=[self.nele.pk])

        with self.captureOnCommitCallbacks(execute=True):
            browser.post(ziel, verwaltungsformular(self.nele, is_active=False))
        with self.captureOnCommitCallbacks(execute=True):
            browser.post(ziel, verwaltungsformular(self.nele, is_active=True))

        self.assertTrue(CustomUser.objects.get(pk=self.nele.pk).is_active)
        self.pruefe_tot(link)

    def test_jeden_abmelden(self):
        link = self.eintragen()

        call_command("sign_out_everyone", stdout=io.StringIO())

        self.pruefe_tot(link)

    def test_eine_andere_aenderung_laesst_ihn_stehen(self):
        link = self.eintragen()
        browser = self.client_class()
        browser.force_login(self.chef)

        browser.post(
            reverse("user_edit", args=[self.nele.pk]), formular(self.nele, last_name="Nachtfalter")
        )

        self.assertEqual(self.bestaetigen(link), NEU)


class EineAdresseOhneBestaetigungNimmtIhnZurueck(OffenerWechsel):
    """Written at once, a new address takes a waiting one back. The old one may come again."""

    def test_eine_sofort_gesetzte_adresse(self):
        link = self.eintragen()
        konto = CustomUser.objects.get(pk=self.nele.pk)

        email_change.sofort_setzen(konto, "nele.anders@example.org")
        email_change.sofort_setzen(konto, "nele@example.com")

        self.pruefe_tot(link)


class DieMigration(TransactionTestCase):
    """'accounts/0005' adds the table of pending changes, and backwards it goes again."""

    # Like every test here that commits: the next one finds the database as it was.
    serialized_rollback = True

    VORHER = ("accounts", "0004_signaturzaehler_32_bit")
    NACHHER = ("accounts", "0005_adresswechsel")

    def tabellen(self) -> list[str]:
        return connection.introspection.table_names()

    def test_vorwaerts_und_zurueck(self):
        tabelle = Adresswechsel._meta.db_table
        self.assertIn(tabelle, self.tabellen())
        executor = MigrationExecutor(connection)
        try:
            executor.migrate([self.VORHER])
            self.assertNotIn(tabelle, self.tabellen())
            executor = MigrationExecutor(connection)
            executor.loader.build_graph()
            executor.migrate([self.NACHHER])
            self.assertIn(tabelle, self.tabellen())
        finally:
            executor = MigrationExecutor(connection)
            executor.loader.build_graph()
            executor.migrate(executor.loader.graph.leaf_nodes())
