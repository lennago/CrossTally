"""Signing in with a password lets the account in under its lock, asked once more there.

The password and the tick "Active" are checked first ('authenticate()'),
the sign-in is written afterwards: the app's token pair, the website's
session, which the middleware saves only after the response. A deactivation
in between signs the account out everywhere ('ueberall_abmelden') and found
neither. Until 10/2026 such a sign-in was refused while the account was
off, and applied again once it was switched on within two weeks. Here the
deactivation runs right after the check ('dazwischen'). The two orders
under real locks are in 'test_sign_in_race'.

The website's sign-in is 'AnmeldenView', in front of Django's own under
'accounts/login/' ('django_project/urls.py').
"""

from collections.abc import Callable
from http import HTTPStatus
from unittest import mock

from django.conf import settings
from django.contrib.auth import authenticate
from django.test import Client, TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

from accounts.models import CustomUser, Sitzung
from accounts.sign_out import ueberall_abmelden
from api.test_merge_split_race import abfragen_waehrend
from api.tests import adresse

PASSWORT = "ziemlich-geheim-2026"


def zaehlerin() -> CustomUser:
    return CustomUser.objects.create_user(
        username="zoe", email="zoe@example.com", password=PASSWORT, first_name="Z", last_name="Z"
    )


def dazwischen(tun: Callable[[CustomUser], None]):
    """Django's 'authenticate()', and 'tun' with the account right after a password that held."""

    def pruefen(*args, **kwargs):
        konto = authenticate(*args, **kwargs)
        if isinstance(konto, CustomUser):
            tun(konto)
        return konto

    return pruefen


def deaktivieren(konto: CustomUser) -> None:
    """What switching the account off writes, on the website and in the administration."""
    gespeichert = CustomUser.objects.get(pk=konto.pk)
    gespeichert.is_active = False
    gespeichert.save(update_fields=["is_active"])
    ueberall_abmelden(gespeichert)


def neues_passwort(konto: CustomUser) -> None:
    gespeichert = CustomUser.objects.get(pk=konto.pk)
    gespeichert.set_password("ganz-anderes-Passwort-47")
    gespeichert.save(update_fields=["password"])


def wieder_einschalten(konto: CustomUser) -> None:
    CustomUser.objects.filter(pk=konto.pk).update(is_active=True)


class InDerApp(TestCase):
    def setUp(self):
        self.zoe = zaehlerin()

    def anmelden(self, tun: Callable[[CustomUser], None]):
        with mock.patch("rest_framework_simplejwt.serializers.authenticate", dazwischen(tun)):
            return APIClient().post(
                adresse("token_holen"), {"username": "zoe", "password": PASSWORT}, format="json"
            )

    def test_eine_deaktivierung_dazwischen_laesst_kein_paar_zurueck(self):
        antwort = self.anmelden(deaktivieren)
        wieder_einschalten(self.zoe)

        self.assertEqual(antwort.status_code, HTTPStatus.UNAUTHORIZED)
        self.assertNotIn("refresh", antwort.json())
        self.assertFalse(
            OutstandingToken.objects.filter(user=self.zoe, blacklistedtoken__isnull=True).exists()
        )

    def test_ein_neues_passwort_dazwischen_kommt_nicht_ins_token(self):
        """Read under the lock, the account would carry the new password into the pair."""
        antwort = self.anmelden(neues_passwort)

        self.assertEqual(antwort.status_code, HTTPStatus.UNAUTHORIZED)
        self.assertFalse(OutstandingToken.objects.filter(user=self.zoe).exists())

    def test_ohne_etwas_dazwischen_kommt_das_paar(self):
        antwort = self.anmelden(lambda konto: None)

        self.assertEqual(antwort.status_code, HTTPStatus.OK)
        erneuert = APIClient().post(
            adresse("token_erneuern"), {"refresh": antwort.json()["refresh"]}, format="json"
        )
        self.assertEqual(erneuert.status_code, HTTPStatus.OK)

    def test_sperrt_das_konto_bevor_es_das_paar_ausstellt(self):
        """Locked without its key, as for a renewal ('accounts/sign_out.py')."""
        abfragen = abfragen_waehrend(lambda: self.anmelden(lambda konto: None))

        sperre = next(
            i
            for i, abfrage in enumerate(abfragen)
            if abfrage[0] is CustomUser and abfrage[1] and abfrage[2]
        )
        neu = next(
            i
            for i, abfrage in enumerate(abfragen)
            if abfrage[0] is OutstandingToken and abfrage[4].startswith("INSERT")
        )
        self.assertLess(sperre, neu)


class AufDerWebsite(TestCase):
    def setUp(self):
        self.zoe = zaehlerin()
        self.browser = Client()

    def anmelden(self, tun: Callable[[CustomUser], None]):
        with mock.patch("django.contrib.auth.forms.authenticate", dazwischen(tun)):
            return self.browser.post(reverse("login"), {"username": "zoe", "password": PASSWORT})

    def angemeldet(self) -> bool:
        return self.browser.get(reverse("passkey_list")).status_code == HTTPStatus.OK

    def test_eine_deaktivierung_dazwischen_laesst_keine_sitzung_zurueck(self):
        antwort = self.anmelden(deaktivieren)
        wieder_einschalten(self.zoe)

        self.assertEqual(antwort.status_code, HTTPStatus.OK)
        # The message of a wrong password: the form tells nobody more than a guess would.
        fehler = antwort.context["form"].non_field_errors().as_data()
        self.assertEqual([grund.code for grund in fehler], ["invalid_login"])
        self.assertFalse(self.angemeldet())
        self.assertFalse(Sitzung.objects.filter(konto=self.zoe).exists())

    def test_ein_neues_passwort_dazwischen_meldet_nicht_an(self):
        antwort = self.anmelden(neues_passwort)

        self.assertEqual(antwort.status_code, HTTPStatus.OK)
        self.assertFalse(self.angemeldet())

    def test_ohne_etwas_dazwischen_ist_die_person_angemeldet(self):
        antwort = self.anmelden(lambda konto: None)

        self.assertRedirects(
            antwort, reverse(settings.LOGIN_REDIRECT_URL), fetch_redirect_response=False
        )
        self.assertTrue(self.angemeldet())

    def test_die_sitzung_steht_unter_der_sperre_in_der_datenbank(self):
        """The middleware saves after the response. Saved under the lock, it names the account."""
        abfragen = abfragen_waehrend(lambda: self.anmelden(lambda konto: None))

        sperre = next(
            i
            for i, abfrage in enumerate(abfragen)
            if abfrage[0] is CustomUser and abfrage[1] and abfrage[2]
        )
        gespeichert = [
            i
            for i, abfrage in enumerate(abfragen)
            if i > sperre and abfrage[0] is Sitzung and abfrage[4].startswith("UPDATE")
        ]
        self.assertTrue(gespeichert, "unter der Sperre wird keine Sitzung gespeichert")


class DieVerwaltungHatKeineEigeneAnmeldung(TestCase):
    """Administrators sign in at the front. Signed out, the administration has no sign-in."""

    def test_ohne_anmeldung_antwortet_ihre_anmeldung_mit_404(self):
        administrator = CustomUser.objects.create_user(
            username="chef",
            email="chef@example.com",
            password=PASSWORT,
            is_staff=True,
            is_superuser=True,
        )
        browser = Client()

        antwort = browser.post(
            f"/{settings.ADMIN_URL}login/", {"username": "chef", "password": PASSWORT}
        )

        self.assertEqual(antwort.status_code, HTTPStatus.NOT_FOUND)
        self.assertFalse(Sitzung.objects.filter(konto=administrator).exists())
