"""A mail text that never went through 'clean()', and a link hidden in a branch.

Both came up in the audit of 2026-10-13: a backup edited by hand made every
change to an account a server error, and the check for the required link
counted a placeholder inside a condition that need not apply.
"""

from http import HTTPStatus

from django.core import mail
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser
from django_project.testing import administrator

from .mail_texts import Mailart, pruefen
from .models import Mailtext

WERTE = {
    "vorname": "Nele",
    "nachname": "N",
    "benutzername": "nele",
    "anwendung": "Probe",
    "wer": "Kim Leitung",
    "link": "https://example.org/accounts/einladung/x/y/",
    "tage": 3,
    "app": "https://example.org/app/",
    "website": "https://example.org/accounts/login/",
}


class UnlesbarerText(TestCase):
    """Past 'clean()', as 'loaddata' writes it: an unclosed condition."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = administrator(email="chef@example.com")
        cls.mailtext = Mailtext.objects.create(
            art=Mailart.KONTO_GEAENDERT, text="Hallo {{ vorname }} {% if selbst %}"
        )
        cls.nele = CustomUser.objects.create_user(
            username="nele",
            password="geheim",
            email="nele@example.com",
            first_name="Nele",
            last_name="Neu",
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_die_mail_geht_mit_dem_mitgelieferten_text_hinaus(self):
        with self.assertLogs("organisation.models", level="WARNING"):
            betreff, text = Mailtext.fuer(Mailart.KONTO_GEAENDERT).ausfuellen(
                "de", {"vorname": "Nele", "wer": "", "selbst": True, "aenderungen": "x"}
            )
        self.assertTrue(betreff)
        self.assertNotIn("{%", text)

    def test_die_verwaltungsseite_des_textes_laedt(self):
        antwort = self.client.get(
            reverse("admin:organisation_mailtext_change", args=[self.mailtext.pk])
        )
        self.assertEqual(antwort.status_code, HTTPStatus.OK)

    def test_eine_aenderung_am_konto_geht_durch(self):
        antwort = self.client.post(
            reverse("user_edit", args=[self.nele.pk]),
            {
                "username": "nele",
                "email": "nele@example.com",
                "first_name": "Nelly",
                "last_name": "Neu",
                "is_active": "on",
            },
        )
        self.assertEqual(antwort.status_code, HTTPStatus.FOUND)
        self.nele.refresh_from_db()
        self.assertEqual(self.nele.first_name, "Nelly")
        self.assertEqual(len(mail.outbox), 1)


class LinkNurInEinemZweig(TestCase):
    def test_wird_abgewiesen(self):
        with self.assertRaises(ValidationError):
            pruefen("{% if wer %}{{ link }}{% endif %}", Mailart.EINLADUNG)

    def test_ausserhalb_der_bedingung_genuegt_er(self):
        pruefen("{% if wer %}von {{ wer }}{% endif %} {{ link }}", Mailart.EINLADUNG)

    def test_ohne_namen_traegt_die_einladung_den_link(self):
        mailtext = Mailtext(art=Mailart.EINLADUNG, nennt_namen=False)
        _betreff, text = mailtext.ausfuellen("de", WERTE)
        self.assertIn(WERTE["link"], text)
