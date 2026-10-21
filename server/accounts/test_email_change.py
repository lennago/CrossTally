"""A new e-mail address: by the person, by an administrator, and the link that confirms it."""

import json
from smtplib import SMTPException
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts.models import CustomUser, Passkey
from django_project.testing import administrator, fuehrungskraft

from .email_change import token
from .passkey import user_handle
from .test_notification import PASSWORT, formular, konto_anlegen, verwaltungsformular
from .test_passkey import (
    BESTAETIGUNG_ABGELAUFEN,
    BESTAETIGUNG_FEHLT,
    KEIN_PASSKEY,
    ZWEI_MINUTEN,
    Authenticator,
    hinterlegen,
    spaeter,
)

#: The console backend: the mail would only end in the log.
NUR_LOG = {"default": {"BACKEND": "django.core.mail.backends.console.EmailBackend"}}

NEU = "nele@example.org"


def link_aus(nachricht) -> str:
    """The address of the link in a mail, as the person would click it."""
    return next(zeile for zeile in nachricht.body.splitlines() if "/bestaetigen/" in zeile)


class SelbstAufDerWebsite(TestCase):
    """Counters get here too: the page lies under '/accounts/' like the password."""

    def setUp(self):
        self.nele = konto_anlegen()
        self.client.force_login(self.nele)

    def eintragen(self, email=NEU, passwort=PASSWORT):
        return self.client.post(reverse("email_aendern"), {"email": email, "passwort": passwort})

    def test_die_seite_nennt_die_bisherige_adresse(self):
        antwort = self.client.get(reverse("email_aendern"))
        self.assertContains(antwort, "nele@example.com")
        self.assertContains(antwort, "Erst wenn du ihn bestätigt hast")

    def test_die_neue_adresse_bekommt_den_link_und_gilt_noch_nicht(self):
        antwort = self.eintragen()

        self.assertRedirects(antwort, reverse("email_fertig"))
        self.nele.refresh_from_db()
        self.assertEqual(self.nele.email, "nele@example.com")
        self.assertEqual(len(mail.outbox), 1)
        nachricht = mail.outbox[0]
        self.assertEqual(nachricht.to, [NEU])
        self.assertIn("Neue E-Mail-Adresse", nachricht.subject)
        self.assertIn("du hast diese Adresse", nachricht.body)
        self.assertIn("/accounts/email/bestaetigen/", nachricht.body)
        self.assertIn("Der Link gilt 3 Tage.", nachricht.body)
        # Nothing a stranger could use if the address was mistyped.
        self.assertNotIn("nele@example.com", nachricht.body)
        self.assertNotIn(self.nele.username, nachricht.body)

    def test_die_seite_danach_sagt_dass_die_mail_unterwegs_ist(self):
        antwort = self.client.get(self.eintragen()["Location"])
        self.assertContains(antwort, "Mail unterwegs")
        self.assertContains(antwort, NEU)
        # Counters came from the app and go back there.
        self.assertContains(antwort, reverse("pwa"))

    def test_ohne_etwas_eingetragen_zu_haben_fuehrt_die_seite_zum_formular(self):
        self.assertRedirects(self.client.get(reverse("email_fertig")), reverse("email_aendern"))

    def test_ein_falsches_passwort_schickt_nichts(self):
        antwort = self.eintragen(passwort="daneben")
        self.assertContains(antwort, "Das Passwort stimmt nicht.")
        self.assertEqual(mail.outbox, [])

    def test_die_eigene_adresse_noch_einmal_ist_keine_neue(self):
        antwort = self.eintragen(email="Nele@Example.com")
        self.assertContains(antwort, "Das ist schon deine Adresse.")
        self.assertEqual(mail.outbox, [])

    def test_die_adresse_eines_anderen_kontos_wird_abgewiesen(self):
        konto_anlegen(username="zora", email="zora@example.com")
        antwort = self.eintragen(email="ZORA@example.com")
        self.assertContains(antwort, "gehört schon zu einem anderen Konto")
        self.assertEqual(mail.outbox, [])

    @override_settings(MAILERS=NUR_LOG, DEBUG=False)
    def test_ohne_mailserver_gilt_die_adresse_sofort(self):
        antwort = self.client.get(reverse("email_aendern"))
        self.assertContains(antwort, "Die neue Adresse gilt sofort.")

        antwort = self.eintragen()

        self.nele.refresh_from_db()
        self.assertEqual(self.nele.email, NEU)
        seite = self.client.get(antwort["Location"])
        self.assertContains(seite, "Deine Adresse ist jetzt")
        self.assertContains(seite, NEU)

    def test_die_seite_bietet_keine_bestaetigung_mit_passkey_an(self):
        """With a password the password is asked, as before."""
        antwort = self.client.get(reverse("email_aendern"))
        self.assertContains(antwort, "Dein Passwort")
        self.assertNotContains(antwort, "data-passkey-bestaetigen")

    def test_ein_stummer_mailserver_laesst_das_formular_offen(self):
        with mock.patch("accounts.notification.send_mail", side_effect=SMTPException):
            antwort = self.eintragen()
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "ging nicht hinaus")
        self.nele.refresh_from_db()
        self.assertEqual(self.nele.email, "nele@example.com")

    def test_die_mail_spricht_die_sprache_des_kontos(self):
        CustomUser.objects.filter(pk=self.nele.pk).update(sprache="en")
        self.eintragen()
        nachricht = mail.outbox[0]
        self.assertIn("New e-mail address", nachricht.subject)
        self.assertIn("You entered this address", nachricht.body)


class OhnePasswortMitPasskey(TestCase):
    """An account without a password confirms with one of its keys before a new address.

    It has nothing to type, and until 10/2026 it was asked nothing: whoever
    found its session open pointed the account at their own mailbox, and the
    link in the mail did the rest.
    """

    def setUp(self):
        self.nele = konto_anlegen()
        self.nele.set_unusable_password()
        self.nele.save()
        self.geraet = Authenticator()
        hinterlegen(self.nele, self.geraet)
        self.client.force_login(self.nele)

    def bestaetigen(self):
        frage = self.client.post(
            reverse("passkey_bestaetigen_optionen"), "{}", content_type="application/json"
        ).json()["challenge"]
        credential = self.geraet.anmeldung(frage, user_handle(self.nele))
        return self.client.post(
            reverse("passkey_bestaetigen"),
            json.dumps({"credential": credential}),
            content_type="application/json",
        )

    def eintragen(self):
        return self.client.post(reverse("email_aendern"), {"email": NEU})

    def test_eine_offene_sitzung_aendert_keine_adresse(self):
        """The gap: until 10/2026 the mail went out without any question."""
        antwort = self.eintragen()

        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, BESTAETIGUNG_FEHLT)
        self.assertEqual(mail.outbox, [])

    def test_bestaetigt_geht_die_mail_hinaus(self):
        """No password asked: the passkey stands in for it."""
        self.assertEqual(self.bestaetigen().status_code, 200)

        antwort = self.eintragen()

        self.assertRedirects(antwort, reverse("email_fertig"))
        self.assertEqual(mail.outbox[0].to, [NEU])

    def test_eine_abgelaufene_bestaetigung_gilt_nicht(self):
        self.bestaetigen()

        with spaeter(ZWEI_MINUTEN + 1):
            antwort = self.eintragen()

        self.assertContains(antwort, BESTAETIGUNG_ABGELAUFEN)
        self.assertEqual(mail.outbox, [])

    @override_settings(MAILERS=NUR_LOG, DEBUG=False)
    def test_ohne_mailserver_ebenso(self):
        """There the address applies at once, so the question matters all the more."""
        antwort = self.eintragen()

        self.assertContains(antwort, BESTAETIGUNG_FEHLT)
        self.nele.refresh_from_db()
        self.assertEqual(self.nele.email, "nele@example.com")

    def test_die_seite_bietet_die_bestaetigung_an(self):
        antwort = self.client.get(reverse("email_aendern"))

        self.assertNotContains(antwort, "Dein Passwort")
        for stueck in (
            "data-passkey-bestaetigen",
            f'data-passkey-optionen="{reverse("passkey_bestaetigen_optionen")}"',
            f'data-passkey-ziel="{reverse("passkey_bestaetigen")}"',
            "Mit Passkey bestätigen",
            "data-passkey-meldung",
            "data-passkey-ohne",
            "js/passkey.js",
        ):
            with self.subTest(stueck=stueck):
                self.assertContains(antwort, stueck)

    def test_ohne_passkey_sagt_die_seite_wer_hilft(self):
        Passkey.objects.all().delete()

        seite = self.client.get(reverse("email_aendern"))
        antwort = self.eintragen()

        self.assertContains(seite, KEIN_PASSKEY)
        self.assertNotContains(seite, "data-passkey-bestaetigen")
        self.assertContains(antwort, KEIN_PASSKEY)
        self.assertEqual(mail.outbox, [])


class DerLinkAusDerMail(TestCase):
    def setUp(self):
        self.nele = konto_anlegen()

    def link(self, von=None, email=NEU) -> str:
        return reverse("email_bestaetigen", kwargs={"token": token(self.nele, email, von)})

    def test_oeffnen_fragt_und_aendert_nichts(self):
        antwort = self.client.get(self.link())

        self.assertContains(antwort, "Soll <strong>nele@example.org</strong>")
        self.assertContains(antwort, "<strong>nele</strong>")
        self.nele.refresh_from_db()
        self.assertEqual(self.nele.email, "nele@example.com")
        self.assertEqual(mail.outbox, [])

    def test_der_knopf_uebernimmt_die_adresse_und_beide_erfahren_es(self):
        antwort = self.client.post(self.link())

        # Not 'assertRedirects': it would fetch the page and use up what it shows.
        self.assertEqual(
            (antwort.status_code, antwort["Location"]), (302, reverse("email_bestaetigt"))
        )
        self.nele.refresh_from_db()
        self.assertEqual(self.nele.email, NEU)
        nachricht = mail.outbox[0]
        self.assertEqual(nachricht.to, ["nele@example.com", NEU])
        self.assertIn("E-Mail-Adresse: nele@example.com → nele@example.org", nachricht.body)
        self.assertIn("Warst du das nicht?", nachricht.body)
        seite = self.client.get(antwort["Location"])
        self.assertContains(seite, "<strong>nele@example.org</strong> ist jetzt die Adresse")

    def test_wer_sie_eingetragen_hat_steht_in_der_mail(self):
        chef = administrator(email="chef@example.com", first_name="Conrad", last_name="Chef")
        self.client.post(self.link(von=chef))
        self.assertIn("Geändert von Conrad Chef.", mail.outbox[0].body)

    def test_ein_benutzter_link_ist_tot(self):
        link = self.link()
        self.client.post(link)
        antwort = self.client.get(link)
        self.assertContains(antwort, "Link ungültig")
        self.assertEqual(self.client.post(link).status_code, 200)
        self.assertEqual(len(mail.outbox), 1)

    def test_ein_abgelaufener_link_ist_tot(self):
        link = self.link()
        with override_settings(PASSWORD_RESET_TIMEOUT=0):
            antwort = self.client.get(link)
        self.assertContains(antwort, "Link ungültig")

    def test_ein_gefaelschter_link_ist_tot(self):
        link = self.link()
        verdreht = link[:-6] + ("a" if link[-6] != "a" else "b") + link[-5:]
        self.assertContains(self.client.get(verdreht), "Link ungültig")

    def test_eine_inzwischen_vergebene_adresse_bleibt_beim_anderen(self):
        link = self.link()
        konto_anlegen(username="zora", email=NEU)

        antwort = self.client.post(link)

        self.assertContains(antwort, "Adresse schon vergeben")
        self.nele.refresh_from_db()
        self.assertEqual(self.nele.email, "nele@example.com")

    def test_der_link_gilt_nur_fuer_die_adresse_von_damals(self):
        """Changed in the meantime, say by an administrator at once: the old link is void."""
        link = self.link()
        CustomUser.objects.filter(pk=self.nele.pk).update(email="anders@example.com")
        self.assertContains(self.client.get(link), "Link ungültig")


class DurchAndere(TestCase):
    """Only an administrator changes someone else's address, and the person confirms it."""

    @classmethod
    def setUpTestData(cls):
        cls.chefin = fuehrungskraft(
            email="chefin@example.com", first_name="Carla", last_name="Chefin"
        )
        cls.chef = administrator(email="chef@example.com", first_name="Conrad", last_name="Chef")

    def setUp(self):
        self.nele = konto_anlegen()

    def bearbeiten(self, **abweichend):
        return self.client.post(
            reverse("user_edit", args=[self.nele.pk]), formular(self.nele, **abweichend)
        )

    def test_eine_fuehrungskraft_sieht_das_feld_nur_grau(self):
        self.client.force_login(self.chefin)
        antwort = self.client.get(reverse("user_edit", args=[self.nele.pk]))
        self.assertContains(antwort, "Die Adresse ändert die Person selbst")
        self.assertContains(antwort, 'name="email"')
        self.assertContains(antwort, "disabled")

        self.bearbeiten(email=NEU, first_name="Nela")

        self.nele.refresh_from_db()
        self.assertEqual((self.nele.email, self.nele.first_name), ("nele@example.com", "Nela"))
        self.assertEqual(mail.outbox[0].to, ["nele@example.com"])
        self.assertNotIn("E-Mail-Adresse:", mail.outbox[0].body)

    def test_ein_administrator_schickt_den_link_und_die_adresse_wartet(self):
        self.client.force_login(self.chef)
        antwort = self.client.get(reverse("user_edit", args=[self.nele.pk]))
        self.assertContains(antwort, "sobald die Person ihn bestätigt hat")

        antwort = self.bearbeiten(email=NEU)

        self.nele.refresh_from_db()
        self.assertEqual(self.nele.email, "nele@example.com")
        self.assertEqual(len(mail.outbox), 1)
        nachricht = mail.outbox[0]
        self.assertEqual(nachricht.to, [NEU])
        self.assertIn("Conrad Chef hat diese Adresse", nachricht.body)
        seite = self.client.get(antwort["Location"])
        self.assertContains(seite, "ist eine Mail mit einem Link unterwegs")

        self.client.logout()
        self.client.post(link_aus(nachricht))

        self.nele.refresh_from_db()
        self.assertEqual(self.nele.email, NEU)
        self.assertEqual(mail.outbox[1].to, ["nele@example.com", NEU])
        self.assertIn("Geändert von Conrad Chef.", mail.outbox[1].body)

    def test_die_uebrigen_aenderungen_gelten_sofort(self):
        self.client.force_login(self.chef)
        self.bearbeiten(email=NEU, first_name="Nela")
        self.nele.refresh_from_db()
        self.assertEqual((self.nele.email, self.nele.first_name), ("nele@example.com", "Nela"))
        empfaenger = sorted(nachricht.to[0] for nachricht in mail.outbox)
        self.assertEqual(empfaenger, ["nele@example.com", NEU])
        aenderung = next(n for n in mail.outbox if n.to == ["nele@example.com"])
        self.assertIn("Vorname: Nele → Nela", aenderung.body)
        self.assertNotIn("E-Mail-Adresse:", aenderung.body)

    def test_ein_eingeladenes_konto_bekommt_die_adresse_sofort(self):
        """The invitation went astray on a typo. The corrected address is where it goes next."""
        self.nele.set_unusable_password()
        self.nele.is_active = False
        self.nele.save()
        self.client.force_login(self.chef)

        self.bearbeiten(email=NEU, is_active=False)

        self.nele.refresh_from_db()
        self.assertEqual(self.nele.email, NEU)
        self.assertEqual(mail.outbox, [])

    def test_ein_deaktiviertes_konto_bekommt_die_adresse_sofort_und_keine_mail(self):
        CustomUser.objects.filter(pk=self.nele.pk).update(is_active=False)
        self.nele.refresh_from_db()
        self.client.force_login(self.chef)

        self.bearbeiten(email=NEU, is_active=False)

        self.nele.refresh_from_db()
        self.assertEqual(self.nele.email, NEU)
        self.assertEqual(mail.outbox, [])

    @override_settings(MAILERS=NUR_LOG, DEBUG=False)
    def test_ohne_mailserver_gilt_sie_sofort(self):
        self.client.force_login(self.chef)
        antwort = self.client.get(reverse("user_edit", args=[self.nele.pk]))
        self.assertNotContains(antwort, "sobald die Person ihn bestätigt hat")

        self.bearbeiten(email=NEU)

        self.nele.refresh_from_db()
        self.assertEqual(self.nele.email, NEU)

    def test_ein_stummer_mailserver_laesst_die_adresse_wie_sie_war(self):
        self.client.force_login(self.chef)
        with mock.patch("accounts.notification.send_mail", side_effect=SMTPException):
            antwort = self.bearbeiten(email=NEU)
        self.nele.refresh_from_db()
        self.assertEqual(self.nele.email, "nele@example.com")
        self.assertContains(self.client.get(antwort["Location"]), "bleibt, wie sie war")

    def test_in_der_verwaltung_ebenso(self):
        self.client.force_login(self.chef)
        # The mails leave after the commit ('test_mail_after_commit').
        with self.captureOnCommitCallbacks(execute=True):
            antwort = self.client.post(
                reverse("admin:accounts_customuser_change", args=[self.nele.pk]),
                verwaltungsformular(self.nele, email=NEU, last_name="Nachtfalter"),
            )
        self.assertEqual(antwort.status_code, 302)
        self.nele.refresh_from_db()
        self.assertEqual(
            (self.nele.email, self.nele.last_name), ("nele@example.com", "Nachtfalter")
        )
        empfaenger = sorted(nachricht.to[0] for nachricht in mail.outbox)
        self.assertEqual(empfaenger, ["nele@example.com", NEU])
        # The notice comes with the mail after the commit. A 'TestCase' runs
        # those callbacks only after the request, so its page holds it in
        # 'test_mail_after_commit.KontenInDerVerwaltung'.

        self.client.post(link_aus(next(n for n in mail.outbox if n.to == [NEU])))

        self.nele.refresh_from_db()
        self.assertEqual(self.nele.email, NEU)


class AdresseInAndererSchreibweise(TestCase):
    """One address, one account, whatever its spelling. Until 10/2026 creating compared exactly."""

    @classmethod
    def setUpTestData(cls):
        cls.chef = administrator(email="chef@example.com", first_name="Conrad", last_name="Chef")

    def setUp(self):
        self.nele = konto_anlegen()
        self.client.force_login(self.chef)

    def anlegen(self, adresse, ziel="user_add"):
        return self.client.post(
            reverse(ziel),
            {
                "username": "nele2",
                "email": adresse,
                "first_name": "Nele",
                "last_name": "Zwei",
                "password1": PASSWORT,
                "password2": PASSWORT,
                "passwort_setzen": "on",
                "sprache": "de",
            },
        )

    def test_auf_der_website_wird_abgewiesen(self):
        antwort = self.anlegen("NELE@example.com")
        self.assertContains(antwort, "gehört schon zu einem anderen Konto")
        self.assertFalse(CustomUser.objects.filter(username="nele2").exists())

    def test_in_der_verwaltung_ebenso(self):
        antwort = self.anlegen("Nele@Example.com", ziel="admin:accounts_customuser_add")
        self.assertContains(antwort, "gehört schon zu einem anderen Konto")
        self.assertFalse(CustomUser.objects.filter(username="nele2").exists())

    def test_eine_andere_adresse_geht(self):
        self.anlegen("nele.zwei@example.com")
        self.assertTrue(CustomUser.objects.filter(username="nele2").exists())

    def test_beim_bearbeiten_eines_anderen_kontos(self):
        anna = konto_anlegen(username="anna", email="anna@example.com", first_name="Anna")
        antwort = self.client.post(
            reverse("user_edit", args=[anna.pk]), formular(anna, email="NELE@example.com")
        )
        self.assertContains(antwort, "gehört schon zu einem anderen Konto")
        anna.refresh_from_db()
        self.assertEqual(anna.email, "anna@example.com")
        self.assertEqual(mail.outbox, [])

    def test_in_der_verwaltung_beim_bearbeiten(self):
        anna = konto_anlegen(username="anna", email="anna@example.com", first_name="Anna")
        antwort = self.client.post(
            reverse("admin:accounts_customuser_change", args=[anna.pk]),
            verwaltungsformular(anna, email="nele@EXAMPLE.com"),
        )
        self.assertContains(antwort, "gehört schon zu einem anderen Konto")
        self.assertEqual(mail.outbox, [])

    def test_die_eigene_adresse_in_anderer_schreibweise_geht(self):
        antwort = self.client.post(
            reverse("user_edit", args=[self.nele.pk]), formular(self.nele, email="Nele@example.com")
        )
        self.assertEqual(antwort.status_code, 302)

    def test_ein_altes_paar_haelt_andere_aenderungen_nicht_auf(self):
        """Checked only when the address changes. A pair from before still lets a name change."""
        CustomUser.objects.create_user(
            username="alt",
            email="NELE@example.com",
            password=PASSWORT,
            first_name="A",
            last_name="B",
        )
        antwort = self.client.post(
            reverse("user_edit", args=[self.nele.pk]), formular(self.nele, first_name="Nela")
        )
        self.assertEqual(antwort.status_code, 302)
        self.nele.refresh_from_db()
        self.assertEqual(self.nele.first_name, "Nela")
