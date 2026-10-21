"""Mails about an account: changed, deactivated, deleted, a new password, a passkey."""

from smtplib import SMTPException
from unittest import mock

from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.models import CustomUser, Passkey
from accounts.notification import Kontostand, aenderung_melden
from accounts.test_passkey import PASSWORT as PASSWORT_DER_LEITUNG
from accounts.test_passkey import PasskeyTests
from django_project.testing import administrator, fuehrungskraft
from organisation.mail_texts import Mailart
from organisation.models import Erscheinungsbild, Haus, Mailtext

from .invitation import einladungs_token

PASSWORT = "ziemlich-geheim-2026"
NEU = "ganz-anderes-Passwort-47"

#: The console backend: the mail would only end in the log.
NUR_LOG = {"default": {"BACKEND": "django.core.mail.backends.console.EmailBackend"}}


def konto_anlegen(**abweichend) -> CustomUser:
    angaben = {
        "username": "nele",
        "email": "nele@example.com",
        "password": PASSWORT,
        "first_name": "Nele",
        "last_name": "Nachtigall",
    } | abweichend
    return CustomUser.objects.create_user(**angaben)


def formular(konto: CustomUser, **abweichend) -> dict:
    """The site's user form as the browser sends it. An unticked box is missing."""
    daten = {
        "username": konto.username,
        "email": konto.email,
        "first_name": konto.first_name,
        "last_name": konto.last_name,
        "is_staff": konto.is_staff,
        "is_active": konto.is_active,
        "haeuser": [haus.pk for haus in konto.haeuser.all()],
    } | abweichend
    return {
        feld: "on" if wert is True else wert for feld, wert in daten.items() if wert is not False
    }


def vorher_von(konto: CustomUser) -> dict:
    """The stored state as a dict, to build a 'Kontostand' with one field changed."""
    stand = Kontostand.aus_der_datenbank(konto)
    return {feld: getattr(stand, feld) for feld in stand.__dataclass_fields__}


def verwaltungsformular(konto: CustomUser, **abweichend) -> dict:
    """The administration's change form as the browser sends it."""
    verwaltung = {
        "is_superuser": konto.is_superuser,
        "date_joined_0": konto.date_joined.strftime("%Y-%m-%d"),
        "date_joined_1": konto.date_joined.strftime("%H:%M:%S"),
    }
    return formular(konto, **(verwaltung | abweichend))


class AenderungAufDerWebsite(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.chefin = fuehrungskraft(
            email="chefin@example.com", first_name="Carla", last_name="Chefin"
        )
        cls.haus = Haus.objects.create(name="Kino Nord")

    def setUp(self):
        self.nele = konto_anlegen()
        self.client.force_login(self.chefin)

    def bearbeiten(self, konto=None, **abweichend):
        konto = konto or self.nele
        return self.client.post(
            reverse("user_edit", args=[konto.pk]), formular(konto, **abweichend)
        )

    def test_ein_neuer_name_geht_als_mail_hinaus(self):
        self.bearbeiten(first_name="Nela")

        self.assertEqual(len(mail.outbox), 1)
        nachricht = mail.outbox[0]
        self.assertEqual(nachricht.to, ["nele@example.com"])
        self.assertIn("wurde geändert", nachricht.subject)
        self.assertIn("Hallo Nela,", nachricht.body)
        self.assertIn("Vorname: Nele → Nela", nachricht.body)
        self.assertIn("Geändert von Carla Chefin.", nachricht.body)
        self.assertIn("sprich mit Carla Chefin", nachricht.body)

    def test_ohne_aenderung_keine_mail(self):
        antwort = self.bearbeiten()
        self.assertEqual(antwort.status_code, 302)
        self.assertEqual(mail.outbox, [])

    def test_rolle_und_haeuser_stehen_in_worten(self):
        self.bearbeiten(is_staff=True, haeuser=[self.haus.pk])
        text = mail.outbox[0].body
        self.assertIn("Rolle: Zähler → Führungskraft", text)
        self.assertIn("Häuser: – → Kino Nord", text)

    def test_deaktivieren_sagt_es_im_betreff(self):
        self.bearbeiten(is_active=False)
        nachricht = mail.outbox[0]
        self.assertIn("ist deaktiviert", nachricht.subject)
        self.assertIn("Anmelden geht damit nicht mehr", nachricht.body)
        self.assertIn("Deaktiviert von Carla Chefin.", nachricht.body)

    def test_deaktivieren_nimmt_die_uebrigen_aenderungen_mit(self):
        self.bearbeiten(is_active=False, first_name="Nela")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Vorname: Nele → Nela", mail.outbox[0].body)

    def test_wieder_aktivieren_ebenso(self):
        CustomUser.objects.filter(pk=self.nele.pk).update(is_active=False)
        self.nele.refresh_from_db()
        self.bearbeiten(is_active=True)
        self.assertIn("ist wieder aktiv", mail.outbox[0].subject)
        self.assertIn("Wieder aktiviert von Carla Chefin.", mail.outbox[0].body)

    def test_eine_neue_adresse_erfahren_alte_und_neue(self):
        """The site's form no longer takes it from a manager ('test_email_change.py')."""
        CustomUser.objects.filter(pk=self.nele.pk).update(email="nele@example.org")
        self.nele.refresh_from_db()
        vorher = Kontostand(**{**vorher_von(self.nele), "email": "nele@example.com"})
        aenderung_melden(self.nele, vorher, self.chefin)
        nachricht = mail.outbox[0]
        self.assertEqual(nachricht.to, ["nele@example.com", "nele@example.org"])
        self.assertIn("E-Mail-Adresse: nele@example.com → nele@example.org", nachricht.body)

    def test_ein_deaktiviertes_konto_hoert_nur_von_der_deaktivierung(self):
        self.bearbeiten(is_active=False)
        self.assertEqual(len(mail.outbox), 1)
        self.nele.refresh_from_db()

        self.bearbeiten(is_active=False, first_name="Nela")
        self.client.post(reverse("user_delete", args=[self.nele.pk]))

        self.assertFalse(CustomUser.objects.filter(pk=self.nele.pk).exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("ist deaktiviert", mail.outbox[0].subject)

    def test_die_mail_spricht_die_sprache_des_kontos(self):
        CustomUser.objects.filter(pk=self.nele.pk).update(sprache="en")
        self.nele.refresh_from_db()
        self.bearbeiten(first_name="Nela")
        nachricht = mail.outbox[0]
        self.assertIn("Hello Nela,", nachricht.body)
        self.assertIn("First name: Nele → Nela", nachricht.body)
        self.assertIn("Changed by Carla Chefin.", nachricht.body)

    def test_wer_sich_selbst_aendert_wird_nicht_genannt(self):
        self.bearbeiten(self.chefin, first_name="Carola")
        text = mail.outbox[0].body
        self.assertNotIn("Geändert von", text)
        self.assertIn("Warst du das nicht?", text)

    def test_wer_sich_selbst_umbenennt_bleibt_selbst(self):
        self.bearbeiten(self.chefin, username="carola")
        text = mail.outbox[0].body
        self.assertIn("Benutzername: leitung → carola", text)
        self.assertNotIn("Geändert von", text)

    def test_ein_stummer_mailserver_laesst_die_aenderung_stehen(self):
        with mock.patch("accounts.notification.send_mail", side_effect=SMTPException):
            antwort = self.bearbeiten(first_name="Nela")
        self.nele.refresh_from_db()
        self.assertEqual(self.nele.first_name, "Nela")
        seite = self.client.get(antwort["Location"])
        self.assertContains(seite, "ging nicht hinaus")

    @override_settings(MAILERS=NUR_LOG)
    def test_ohne_mailserver_geht_nichts_hinaus_und_niemand_wird_gewarnt(self):
        with mock.patch("accounts.notification.send_mail") as senden:
            antwort = self.bearbeiten(first_name="Nela")
        senden.assert_not_called()
        seite = self.client.get(antwort["Location"])
        self.assertNotContains(seite, "ging nicht hinaus")

    def test_loeschen_schickt_eine_mail(self):
        self.client.post(reverse("user_delete", args=[self.nele.pk]))
        self.assertFalse(CustomUser.objects.filter(pk=self.nele.pk).exists())
        nachricht = mail.outbox[0]
        self.assertEqual(nachricht.to, ["nele@example.com"])
        self.assertIn("ist gelöscht", nachricht.subject)
        self.assertIn("Gelöscht von Carla Chefin.", nachricht.body)

    def test_ohne_adresse_keine_mail(self):
        ohne = konto_anlegen(username="ohne", email="")
        self.bearbeiten(ohne, first_name="Otto")
        self.assertEqual(mail.outbox, [])


class NamenUndEigeneTexte(TestCase):
    """What the administration sets on the mail texts reaches the mail."""

    @classmethod
    def setUpTestData(cls):
        cls.chefin = fuehrungskraft(
            email="chefin@example.com", first_name="Carla", last_name="Chefin"
        )

    def setUp(self):
        self.nele = konto_anlegen()
        self.client.force_login(self.chefin)

    def bearbeiten(self, **abweichend):
        return self.client.post(
            reverse("user_edit", args=[self.nele.pk]), formular(self.nele, **abweichend)
        )

    def test_ohne_namen_verweist_die_mail_auf_die_fuehrungskraft(self):
        Mailtext.objects.create(art=Mailart.KONTO_GEAENDERT, nennt_namen=False)
        self.bearbeiten(first_name="Nela")
        text = mail.outbox[0].body
        self.assertNotIn("Carla", text)
        self.assertIn("Wenn du das nicht erwartet hast, sprich mit deiner Führungskraft.", text)

    def test_die_loeschseite_sagt_was_mitgeht(self):
        """Until 10/2026 the page only asked, and the help said "only the assignments"."""
        seite = self.client.get(reverse("user_delete", args=[self.nele.pk]))
        self.assertContains(seite, "Mit dem Konto gehen seine Passkeys")
        self.assertContains(seite, "Die Person bekommt darüber eine Mail.")

        CustomUser.objects.filter(pk=self.nele.pk).update(is_active=False)
        seite = self.client.get(reverse("user_delete", args=[self.nele.pk]))
        self.assertContains(seite, "Mit dem Konto gehen seine Passkeys")
        self.assertNotContains(seite, "bekommt darüber eine Mail")

    @override_settings(MAILERS=NUR_LOG, DEBUG=False)
    def test_ohne_mailserver_verspricht_die_loeschseite_keine_mail(self):
        seite = self.client.get(reverse("user_delete", args=[self.nele.pk]))
        self.assertContains(seite, "Mit dem Konto gehen seine Passkeys")
        self.assertNotContains(seite, "bekommt darüber eine Mail")

    def test_ohne_namen_gilt_nur_fuer_diese_mail(self):
        Mailtext.objects.create(art=Mailart.KONTO_GEAENDERT, nennt_namen=False)
        self.client.post(reverse("user_delete", args=[self.nele.pk]))
        self.assertIn("Gelöscht von Carla Chefin.", mail.outbox[0].body)

    def test_ein_eigener_text_aus_der_verwaltung(self):
        Mailtext.objects.create(
            art=Mailart.KONTO_GEAENDERT,
            betreff="Neues von {{ anwendung }}",
            text=(
                "Moin {{ vorname }}!\n\n{{ aenderungen }}\n\n"
                "{% if wer %}Das war {{ wer }}.{% endif %}"
            ),
        )
        self.bearbeiten(first_name="Nela")
        nachricht = mail.outbox[0]
        self.assertEqual(nachricht.subject, "Neues von CrossTally")
        self.assertEqual(
            nachricht.body, "Moin Nela!\n\nVorname: Nele → Nela\n\nDas war Carla Chefin."
        )

    def test_ein_eigener_text_nur_auf_deutsch_laesst_den_englischen_wie_geliefert(self):
        Mailtext.objects.create(art=Mailart.KONTO_GEAENDERT, text="Moin {{ vorname }}!")
        CustomUser.objects.filter(pk=self.nele.pk).update(sprache="en")
        self.nele.refresh_from_db()
        self.bearbeiten(first_name="Nela")
        nachricht = mail.outbox[0]
        self.assertIn("Hello Nela,", nachricht.body)
        self.assertEqual(nachricht.subject, "Your account at CrossTally was changed")

    def test_der_name_der_anwendung_kommt_aus_dem_erscheinungsbild(self):
        fassung = Erscheinungsbild.standard().kopie("Eigene")
        fassung.anwendungsname = "Kino-Inventur"
        fassung.save()
        fassung.aktivieren()
        self.bearbeiten(first_name="Nela")
        self.assertIn("Kino-Inventur", mail.outbox[0].subject)


class AenderungInDerVerwaltung(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.chef = administrator(email="chef@example.com", first_name="Conrad", last_name="Chef")

    def setUp(self):
        self.nele = konto_anlegen()
        self.client.force_login(self.chef)

    def test_eine_aenderung_geht_als_mail_hinaus(self):
        # The mail leaves after the commit ('test_mail_after_commit').
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse("admin:accounts_customuser_change", args=[self.nele.pk]),
                verwaltungsformular(self.nele, last_name="Nachtfalter"),
            )
        self.nele.refresh_from_db()
        self.assertEqual(self.nele.last_name, "Nachtfalter")
        text = mail.outbox[0].body
        self.assertIn("Nachname: Nachtigall → Nachtfalter", text)
        self.assertIn("Geändert von Conrad Chef.", text)

    def test_ein_gesetztes_passwort_geht_als_mail_hinaus(self):
        self.client.post(
            reverse("admin:auth_user_password_change", args=[self.nele.pk]),
            {"usable_password": "true", "password1": NEU, "password2": NEU},
        )
        self.nele.refresh_from_db()
        self.assertTrue(self.nele.check_password(NEU))
        nachricht = mail.outbox[0]
        self.assertIn("in der Verwaltung ein neues Passwort gesetzt", nachricht.body)
        self.assertIn("Gesetzt von Conrad Chef.", nachricht.body)

    def test_ein_abgeschaltetes_passwort_heisst_so(self):
        self.client.post(
            reverse("admin:auth_user_password_change", args=[self.nele.pk]),
            {"usable_password": "false", "unset-password": "1"},
        )
        nachricht = mail.outbox[0]
        self.assertIn("abgeschaltet", nachricht.subject)
        self.assertIn("die Anmeldung mit Passwort abgeschaltet", nachricht.body)
        self.assertIn("Abgeschaltet von Conrad Chef.", nachricht.body)

    def test_das_eigene_passwort_abgeschaltet_raet_nicht_zu_passwort_vergessen(self):
        """Without a usable password "Forgot your password?" sends no mail."""
        # The last administrator keeps the password unless a passkey opens the door.
        Passkey.objects.create(
            benutzer=self.chef, kennung="abc", oeffentlicher_schluessel=b"x", name="Handy"
        )
        self.client.post(
            reverse("admin:auth_user_password_change", args=[self.chef.pk]),
            {"usable_password": "false", "unset-password": "1"},
        )
        text = mail.outbox[0].body
        self.assertIn("Warst du das nicht? Dann gib einem Administrator Bescheid.", text)
        self.assertNotIn("Passwort vergessen?", text)

    def test_das_formular_allein_schickt_nichts(self):
        self.client.get(reverse("admin:auth_user_password_change", args=[self.nele.pk]))
        self.assertEqual(mail.outbox, [])

    def test_ein_deaktiviertes_konto_hoert_von_keinem_neuen_passwort(self):
        CustomUser.objects.filter(pk=self.nele.pk).update(is_active=False)
        self.client.post(
            reverse("admin:auth_user_password_change", args=[self.nele.pk]),
            {"usable_password": "true", "password1": NEU, "password2": NEU},
        )
        self.nele.refresh_from_db()
        self.assertTrue(self.nele.check_password(NEU))
        self.assertEqual(mail.outbox, [])

    def test_loeschen_schickt_eine_mail(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse("admin:accounts_customuser_delete", args=[self.nele.pk]), {"post": "yes"}
            )
        self.assertFalse(CustomUser.objects.filter(pk=self.nele.pk).exists())
        self.assertIn("Gelöscht von Conrad Chef.", mail.outbox[0].body)

    def test_gemeinsam_geloescht_bekommt_jedes_konto_seine_mail(self):
        zweite = konto_anlegen(username="zora", email="zora@example.com", first_name="Zora")
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse("admin:accounts_customuser_changelist"),
                {
                    "action": "delete_selected",
                    "_selected_action": [self.nele.pk, zweite.pk],
                    "post": "yes",
                },
            )
        self.assertFalse(CustomUser.objects.filter(pk__in=[self.nele.pk, zweite.pk]).exists())
        empfaenger = sorted(nachricht.to[0] for nachricht in mail.outbox)
        self.assertEqual(empfaenger, ["nele@example.com", "zora@example.com"])

    def test_ein_entfernter_passkey_geht_als_mail_hinaus(self):
        """After the commit ('test_mail_after_commit'), which a 'TestCase' only plays."""
        passkey = Passkey.objects.create(
            benutzer=self.nele, kennung="abc", oeffentlicher_schluessel=b"x", name="Handy"
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse("admin:accounts_passkey_delete", args=[passkey.pk]), {"post": "yes"}
            )
        self.assertFalse(Passkey.objects.filter(pk=passkey.pk).exists())
        nachricht = mail.outbox[0]
        self.assertEqual(nachricht.to, ["nele@example.com"])
        self.assertIn("„Handy“", nachricht.body)
        self.assertIn("Entfernt von Conrad Chef.", nachricht.body)


class EigenesPasswort(TestCase):
    """What the person does themselves is told too: whoever did not do it learns of it."""

    @classmethod
    def setUpTestData(cls):
        cls.nele = konto_anlegen()

    def test_auf_der_website_geaendert(self):
        self.client.force_login(self.nele)
        self.client.post(
            reverse("password_change"),
            {"old_password": PASSWORT, "new_password1": NEU, "new_password2": NEU},
        )
        self.nele.refresh_from_db()
        self.assertTrue(self.nele.check_password(NEU))
        nachricht = mail.outbox[0]
        self.assertIn("auf der Website geändert", nachricht.body)
        self.assertIn("Passwort vergessen?", nachricht.body)
        self.assertNotIn("Geändert von", nachricht.body)

    def test_ueber_passwort_vergessen_neu_gesetzt(self):
        uid = urlsafe_base64_encode(force_bytes(self.nele.pk))
        token = default_token_generator.make_token(self.nele)
        weiter = self.client.get(reverse("password_reset_confirm", args=[uid, token]))
        self.client.post(weiter["Location"], {"new_password1": NEU, "new_password2": NEU})
        self.nele.refresh_from_db()
        self.assertTrue(self.nele.check_password(NEU))
        self.assertIn("wurde über „Passwort vergessen?“ geändert", mail.outbox[0].body)

    def test_die_einladung_schickt_keine_bestaetigung(self):
        """Setting the first password is the point of the invitation, not news."""
        eingeladen = konto_anlegen(username="ina", email="ina@example.com")
        eingeladen.set_unusable_password()
        eingeladen.is_active = False
        eingeladen.save()
        uid = urlsafe_base64_encode(force_bytes(eingeladen.pk))
        weiter = self.client.get(
            reverse("einladung", args=[uid, einladungs_token.make_token(eingeladen)])
        )
        self.client.post(weiter["Location"], {"new_password1": NEU, "new_password2": NEU})
        eingeladen.refresh_from_db()
        self.assertTrue(eingeladen.is_active)
        self.assertEqual(mail.outbox, [])


class EigenePasskeys(PasskeyTests):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.leitung)

    def test_ein_neuer_passkey_geht_als_mail_hinaus(self):
        _optionen, antwort = self.registrieren(name="Laptop")
        self.assertEqual(antwort.status_code, 200)
        nachricht = mail.outbox[0]
        self.assertEqual(nachricht.to, ["leitung@example.com"])
        self.assertIn("Neuer Passkey", nachricht.subject)
        self.assertIn("„Laptop“", nachricht.body)
        self.assertIn("Warst du das nicht?", nachricht.body)

    def test_ein_entfernter_passkey_ebenso(self):
        self.registrieren(name="Laptop")
        passkey = Passkey.objects.get(benutzer=self.leitung)
        self.client.post(
            reverse("passkey_delete", args=[passkey.pk]), {"passwort": PASSWORT_DER_LEITUNG}
        )
        nachricht = mail.outbox[-1]
        self.assertIn("entfernt", nachricht.subject)
        self.assertIn("„Laptop“", nachricht.body)
        self.assertNotIn("Entfernt von", nachricht.body)
