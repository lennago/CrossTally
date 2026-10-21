"""An empty password field is no password, whoever asks.

Nobody signs in with it: no hash matches an empty field. The last way into
the administration therefore counted it as none ('roles.mit_passwort'), while
Django's 'has_usable_password' counted it as one. An account with it that
signs in with a passkey was asked for a password it could not type, before a
new address and before another passkey (until 10/2026).
"""

from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import CustomUser, Passkey
from accounts.test_passkey import BESTAETIGUNG_FEHLT
from api.tests import adresse
from django_project.roles import mit_passwort


class LeeresPasswort(TestCase):
    def setUp(self):
        self.zoe = CustomUser.objects.create_user(
            username="zoe",
            email="zoe@example.com",
            first_name="Zoe",
            last_name="Zähler",
            password=None,
        )
        # Written past the forms, as only code that bypasses them can.
        CustomUser.objects.filter(pk=self.zoe.pk).update(password="", last_login=timezone.now())
        self.zoe.refresh_from_db()
        Passkey.objects.create(
            benutzer=self.zoe, kennung="a2V5MTAx", oeffentlicher_schluessel=b"x", name="Handy"
        )

    def test_beide_fragen_geben_dieselbe_antwort(self):
        for gespeichert in ("", make_password(None), make_password("ziemlich-geheim-2026")):
            with self.subTest(gespeichert=gespeichert[:12]):
                CustomUser.objects.filter(pk=self.zoe.pk).update(password=gespeichert)
                self.zoe.refresh_from_db()
                self.assertEqual(
                    self.zoe.has_usable_password(),
                    CustomUser.objects.filter(mit_passwort(), pk=self.zoe.pk).exists(),
                )

    def test_die_website_fragt_nicht_danach(self):
        """It asks for a confirmation with the passkey instead ('test_passkey.py')."""
        self.client.force_login(self.zoe)
        formular = self.client.get(reverse("email_aendern")).context["form"]
        self.assertNotIn("passwort", formular.fields)
        antwort = self.client.post(
            reverse("passkey_anlegen_optionen"),
            "{}",
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="fetch",
        )
        self.assertEqual(antwort.status_code, 400)
        self.assertEqual(antwort.json(), {"fehler": BESTAETIGUNG_FEHLT})

    def test_die_app_fragt_nicht_danach(self):
        """Here too the passkey stands in for it, and the refusal names that field."""
        app = APIClient()
        app.force_authenticate(self.zoe)
        self.assertFalse(app.get(adresse("ich")).data["hat_passwort"])
        antwort = app.post(
            adresse("email_aendern_api"), {"email": "zoe.neu@example.com"}, format="json"
        )
        self.assertEqual(antwort.data, {"bestaetigung": [BESTAETIGUNG_FEHLT]})
        antwort = app.post(adresse("passkey_anlegen_optionen_api"), {}, format="json")
        self.assertEqual(antwort.data, {"bestaetigung": [BESTAETIGUNG_FEHLT]})
