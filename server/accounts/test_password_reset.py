"""The „Passwort vergessen?“ with a limit and our own mail, see 'accounts/password_reset.py'."""

import re
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser, Passwortanfrage
from accounts.password_reset import GRENZE
from organisation.mail_texts import Mailart
from organisation.models import Mailtext

#: The console backend: the mail would only end in the log.
NUR_LOG = {"default": {"BACKEND": "django.core.mail.backends.console.EmailBackend"}}


def sofort(arbeit):
    """The mail inline instead of in a thread: the test looks at the outbox right away."""
    arbeit()


@override_settings(EMAIL_HOST="mail.example.org", DEFAULT_FROM_EMAIL="x@example.org")
@mock.patch("accounts.password_reset.im_hintergrund", side_effect=sofort)
class PasswortVergessen(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.konto = CustomUser.objects.create_user(
            username="zoe",
            password="geheim-genug-2026",
            email="Zoe@example.org",
            first_name="Zoe",
            last_name="Zander",
        )

    def anfragen(self, email="zoe@example.org", ip="10.0.0.1"):
        return self.client.post(reverse("password_reset"), {"email": email}, REMOTE_ADDR=ip)

    def test_die_mail_geht_hinaus_und_die_anfrage_wird_notiert(self, _hintergrund):
        antwort = self.anfragen()
        self.assertRedirects(antwort, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["Zoe@example.org"])
        anfrage = Passwortanfrage.objects.get()
        self.assertEqual((anfrage.email, anfrage.ip_address), ("zoe@example.org", "10.0.0.1"))

    def test_ueber_der_grenze_kommt_dieselbe_seite_ohne_mail(self, _hintergrund):
        for _versuch in range(GRENZE):
            self.anfragen()
        self.assertEqual(len(mail.outbox), GRENZE)

        antwort = self.anfragen()

        self.assertRedirects(antwort, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), GRENZE)
        self.assertEqual(Passwortanfrage.objects.count(), GRENZE + 1)

    def test_die_schreibweise_der_adresse_zaehlt_nicht(self, _hintergrund):
        for _versuch in range(GRENZE):
            self.anfragen(email="ZOE@example.org")
        self.anfragen(email="zoe@Example.org")
        self.assertEqual(len(mail.outbox), GRENZE)

    def test_der_absender_ist_auch_begrenzt(self, _hintergrund):
        """One sender, many addresses: probing which have an account stops at the limit."""
        for nummer in range(GRENZE):
            self.anfragen(email=f"niemand{nummer}@example.org")
        self.anfragen()
        self.assertEqual(len(mail.outbox), 0)

    def test_alte_anfragen_zaehlen_nicht_mehr(self, _hintergrund):
        for _versuch in range(GRENZE):
            self.anfragen()
        Passwortanfrage.objects.update(zeitpunkt=timezone.now() - timedelta(hours=2))
        self.anfragen()
        self.assertEqual(len(mail.outbox), GRENZE + 1)

    def test_eine_unbekannte_adresse_wird_genauso_notiert(self, _hintergrund):
        antwort = self.anfragen(email="niemand@example.org")
        self.assertRedirects(antwort, reverse("password_reset_done"))
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(Passwortanfrage.objects.count(), 1)

    def test_die_mail_kommt_aus_den_mailtexten_und_duzt(self, _hintergrund):
        """Until 10/2026 Django's own text went out: formal, with the domain as the name."""
        self.anfragen()
        nachricht = mail.outbox[0]
        self.assertEqual(nachricht.subject, "Neues Passwort für CrossTally")
        self.assertIn("Hallo Zoe,", nachricht.body)
        self.assertIn("Dein Benutzername: zoe", nachricht.body)
        self.assertIn("Der Link gilt 3 Tage", nachricht.body)
        self.assertNotIn("Sie ", nachricht.body)

    def test_der_link_in_der_mail_fuehrt_zum_neuen_passwort(self, _hintergrund):
        self.anfragen()
        link = re.search(r"https?://\S+", str(mail.outbox[0].body))
        assert link is not None
        self.assertIn("/accounts/reset/", link.group())
        antwort = self.client.get(link.group())
        self.assertEqual(antwort.status_code, 302)
        self.assertTrue(antwort["Location"].endswith("/set-password/"))
        seite = self.client.get(antwort["Location"])
        self.assertContains(seite, 'name="new_password1"')

    def test_die_mail_spricht_die_sprache_des_kontos(self, _hintergrund):
        """The mail leaves in a thread of its own, which knows no language of the request."""
        CustomUser.objects.filter(pk=self.konto.pk).update(sprache="en")
        self.anfragen()
        nachricht = mail.outbox[0]
        self.assertEqual(nachricht.subject, "New password for CrossTally")
        self.assertIn("Hello Zoe,", nachricht.body)

    def test_ein_eigener_text_aus_der_verwaltung(self, _hintergrund):
        Mailtext.objects.create(
            art=Mailart.PASSWORT_VERGESSEN, text="Hier entlang, {{ vorname }}: {{ link }}"
        )
        self.anfragen()
        self.assertRegex(
            str(mail.outbox[0].body), r"^Hier entlang, Zoe: http://testserver/accounts/"
        )

    def test_ein_stummer_mailserver_bleibt_im_log(self, _hintergrund):
        with (
            mock.patch("accounts.password_reset.send_mail", side_effect=OSError("zu")),
            self.assertLogs("accounts.password_reset", level="ERROR"),
        ):
            antwort = self.anfragen()
        self.assertRedirects(antwort, reverse("password_reset_done"))


@override_settings(MAILERS=NUR_LOG, DEBUG=False)
class OhneMailserver(TestCase):
    """Without a mail server the page would promise a mail that never comes."""

    def test_die_anmeldung_verlinkt_nicht_dorthin(self):
        antwort = self.client.get(reverse("login"))
        self.assertNotContains(antwort, "Passwort vergessen?")
        self.assertNotContains(antwort, reverse("password_reset"))
        self.assertContains(antwort, "Du hast kein Konto?")

    def test_die_seiten_gibt_es_nicht(self):
        for name in ("password_reset", "password_reset_done"):
            with self.subTest(name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 404)
        antwort = self.client.post(reverse("password_reset"), {"email": "zoe@example.org"})
        self.assertEqual(antwort.status_code, 404)
        self.assertEqual(Passwortanfrage.objects.count(), 0)


class MitMailserver(TestCase):
    def test_die_anmeldung_verlinkt_dorthin(self):
        antwort = self.client.get(reverse("login"))
        self.assertContains(antwort, "Passwort vergessen?")
        self.assertContains(antwort, reverse("password_reset"))
