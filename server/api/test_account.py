"""The account endpoints of the app: signing in, and what a request writes of the account."""

import json
from unittest import mock
from urllib.parse import urlencode

from axes.models import AccessAttempt
from django.test import Client, TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import CustomUser, Passkey
from api.serializers import EinstellungenSerializer
from api.views import account as konto_ansichten

from .tests import adresse

PASSWORT = "ziemlich-geheim-2026"
NEU = "ganz-anderes-Passwort-47"


class Dazwischen(EinstellungenSerializer):
    """Runs 'tun' after the request has read its account and before the serializer writes."""

    tun = staticmethod(lambda: None)

    def is_valid(self, *, raise_exception=False):
        type(self).tun()
        return super().is_valid(raise_exception=raise_exception)


class EinstellungenSchreibenNurSichSelbst(TestCase):
    """PATCH on '/ich/' writes appearance and language and nothing else of the account.

    DRF's 'ModelSerializer' saved the whole row, as the authentication read
    it. What was saved in between went back: a new password, a demotion, a
    deactivation. A deleted account came back with its number and its old
    password, and the old tokens worked again (until 10/2026).
    """

    def setUp(self):
        self.konto = CustomUser.objects.create_user(
            username="chefin",
            password=PASSWORT,
            email="chefin@example.com",
            first_name="Carla",
            last_name="Chefin",
            is_staff=True,
            is_superuser=True,
        )
        Passkey.objects.create(
            benutzer=self.konto, kennung="k101", oeffentlicher_schluessel=b"x", name="Handy"
        )
        self.erneuerung = RefreshToken.for_user(self.konto)
        self.app = APIClient()
        self.app.credentials(HTTP_AUTHORIZATION=f"Bearer {self.erneuerung.access_token}")

    def dunkel_mit(self, tun):
        """Switches to dark, with 'tun' done by someone else in between."""
        Dazwischen.tun = staticmethod(tun)
        with mock.patch.object(konto_ansichten, "EinstellungenSerializer", Dazwischen):
            return self.app.patch(adresse("ich"), {"darstellung": "dark"}, format="json")

    def gespeichert(self) -> CustomUser:
        return CustomUser.objects.get(pk=self.konto.pk)

    def test_ein_neues_passwort_bleibt(self):
        def neues_passwort():
            anderes = CustomUser.objects.get(pk=self.konto.pk)
            anderes.set_password(NEU)
            anderes.save(update_fields=["password"])

        self.assertEqual(self.dunkel_mit(neues_passwort).status_code, 200)

        konto = self.gespeichert()
        self.assertTrue(konto.check_password(NEU))
        self.assertEqual(konto.darstellung, "dark")
        # The token from before the new password stays refused.
        self.assertEqual(self.app.get(adresse("ich")).status_code, 401)

    def test_rolle_und_sperre_bleiben(self):
        def zurueckgestuft():
            CustomUser.objects.filter(pk=self.konto.pk).update(
                is_superuser=False, is_staff=False, is_active=False, sprache="en"
            )

        self.assertEqual(self.dunkel_mit(zurueckgestuft).status_code, 200)

        konto = self.gespeichert()
        self.assertEqual(
            (konto.is_superuser, konto.is_staff, konto.is_active, konto.sprache),
            (False, False, False, "en"),
        )
        self.assertEqual(konto.darstellung, "dark")

    def test_ein_geloeschtes_konto_kommt_nicht_wieder(self):
        """401, as for a token whose account is gone: the app asks for a new sign-in."""
        antwort = self.dunkel_mit(lambda: CustomUser.objects.filter(pk=self.konto.pk).delete())

        self.assertEqual(antwort.status_code, 401)
        self.assertFalse(CustomUser.objects.filter(username="chefin").exists())
        self.assertFalse(Passkey.objects.exists())
        erneuert = APIClient().post(
            adresse("token_erneuern"), {"refresh": str(self.erneuerung)}, format="json"
        )
        self.assertEqual(erneuert.status_code, 401)

    def test_ohne_aenderung_wird_nichts_geschrieben(self):
        antwort = self.app.patch(adresse("ich"), {}, format="json")
        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(self.gespeichert().darstellung, "auto")


class AnmeldenNurMitJson(TestCase):
    """The token endpoint reads JSON only, so a foreign page cannot lock a name.

    The endpoint needs no session and no CSRF token, it is the door of the
    app. A form on any page reaches it from the visitor's browser without a
    preflight: as a form, as multipart or as plain text. 'axes' counted such
    a failed sign-in under the name and the visitor's address, and the fifth
    locked that name for everyone behind the address, at every door (until
    10/2026). A body in JSON needs a preflight, and the server answers none.
    """

    def setUp(self):
        self.zoe = CustomUser.objects.create_user(
            username="zoe",
            password=PASSWORT,
            email="zoe@example.com",
            first_name="Zoe",
            last_name="Zähler",
        )

    def test_was_ein_fremdes_formular_schicken_kann_zaehlt_nicht(self):
        fremd = Client(enforce_csrf_checks=True)
        felder = {"username": "zoe", "password": "falsch"}
        arten = {
            "Formular": ("application/x-www-form-urlencoded", urlencode(felder)),
            "Text": ("text/plain", json.dumps(felder)),
        }
        for art, (typ, inhalt) in arten.items():
            with self.subTest(art):
                for _ in range(5):
                    antwort = fremd.post(
                        adresse("token_holen"),
                        inhalt,
                        content_type=typ,
                        REMOTE_ADDR="198.51.100.7",
                        HTTP_ORIGIN="https://fremd.example",
                    )
                    self.assertEqual(antwort.status_code, 415)
        with self.subTest("Multipart"):
            for _ in range(5):
                antwort = fremd.post(adresse("token_holen"), felder, REMOTE_ADDR="198.51.100.7")
                self.assertEqual(antwort.status_code, 415)
        self.assertFalse(AccessAttempt.objects.exists())

        # The owner signs in from the same address as before.
        antwort = APIClient().post(
            adresse("token_holen"),
            {"username": "zoe", "password": PASSWORT},
            format="json",
            REMOTE_ADDR="198.51.100.7",
        )
        self.assertEqual(antwort.status_code, 200)

    def test_die_anderen_tueren_die_zaehlen_verlangen_eine_anmeldung(self):
        """A token the page cannot send, a session the CSRF check guards."""
        fremd = Client(enforce_csrf_checks=True)
        felder = {
            "altes_passwort": "falsch",
            "neues_passwort": "x",
            "passwort": "falsch",
            "email": "fremd@example.org",
        }

        def senden(tuer: str) -> int:
            return fremd.post(
                adresse(tuer), urlencode(felder), content_type="application/x-www-form-urlencoded"
            ).status_code

        tueren = ("passwort_aendern", "email_aendern_api", "passkey_anlegen_optionen_api")
        for tuer in tueren:
            with self.subTest(tuer, angemeldet=False):
                self.assertEqual(senden(tuer), 401)
        fremd.force_login(self.zoe)
        for tuer in tueren:
            with self.subTest(tuer, angemeldet=True):
                self.assertEqual(senden(tuer), 403)
        self.assertFalse(AccessAttempt.objects.exists())

    def test_die_app_meldet_sich_mit_json_an(self):
        antwort = Client(enforce_csrf_checks=True).post(
            adresse("token_holen"),
            {"username": "zoe", "password": PASSWORT},
            content_type="application/json",
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(set(antwort.json()), {"access", "refresh"})
