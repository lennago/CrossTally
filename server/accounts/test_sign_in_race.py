"""A password sign-in and a deactivation of the same account at the same moment, in both orders.

The deactivation locks the account's row, writes the tick and signs the
account out everywhere under the lock ('UserUpdateView', 'ueberall_abmelden').
The sign-in checks password and tick first ('authenticate()') and writes its
token pair or session afterwards. Until 10/2026 it took no lock: written
after the sign-out had read, the sign-in outlived it, refused while the
account was off and valid again once it was switched on. Now it locks the
account's row too and asks once more. One side is held, in a forced order,
until the other one waits for a lock or is done
('test_sign_out_race.Nebeneinander'). Runs in the CI, which tests against
PostgreSQL.
"""

from http import HTTPStatus
from unittest import skipUnless

import rest_framework_simplejwt.serializers
from django.contrib.auth import forms as auth_forms
from django.db import connection
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

from accounts import views as konto_ansichten
from accounts.models import CustomUser, Sitzung
from accounts.test_notification import formular
from accounts.test_sign_out_race import Nebeneinander
from api.tests import adresse


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class AnmeldenUndDeaktivierenGleichzeitig(Nebeneinander):
    def setUp(self):
        super().setUp()
        self.browser = Client(raise_request_exception=False)

    def deaktivieren(self):
        verwaltung = Client(raise_request_exception=False)
        verwaltung.force_login(self.chef)
        return verwaltung.post(
            reverse("user_edit", args=[self.zoe.pk]), formular(self.zoe, is_active=False)
        )

    def in_der_app(self):
        return APIClient(raise_request_exception=False).post(
            adresse("token_holen"), {"username": "zoe", "password": "x"}, format="json"
        )

    def auf_der_website(self):
        return self.browser.post(reverse("login"), {"username": "zoe", "password": "x"})

    def pruefe_nach_dem_einschalten_abgemeldet(self) -> None:
        CustomUser.objects.filter(pk=self.zoe.pk).update(is_active=True)
        offen = OutstandingToken.objects.filter(user=self.zoe, blacklistedtoken__isnull=True)
        self.assertFalse(offen.exists(), "ein Token der Anmeldung gilt nach dem Einschalten")
        self.assertFalse(Sitzung.objects.filter(konto=self.zoe).exists())
        self.assertNotEqual(self.browser.get(reverse("passkey_list")).status_code, HTTPStatus.OK)

    def test_die_app_hat_das_passwort_geprueft_als_deaktiviert_wird(self):
        ergebnis = self.gleichzeitig(
            self.in_der_app,
            self.deaktivieren,
            (rest_framework_simplejwt.serializers, "authenticate"),
            nach_dem_aufruf=True,
        )

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.UNAUTHORIZED)
        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.FOUND)
        self.pruefe_nach_dem_einschalten_abgemeldet()

    def test_die_app_meldet_sich_an_waehrend_deaktiviert_wird(self):
        """The deactivation holds the account and has signed it out: the sign-in waits for it."""
        ergebnis = self.gleichzeitig(
            self.deaktivieren,
            self.in_der_app,
            (konto_ansichten, "ueberall_abmelden"),
            nach_dem_aufruf=True,
        )

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.FOUND)
        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.UNAUTHORIZED)
        self.pruefe_nach_dem_einschalten_abgemeldet()

    def test_die_website_hat_das_passwort_geprueft_als_deaktiviert_wird(self):
        ergebnis = self.gleichzeitig(
            self.auf_der_website,
            self.deaktivieren,
            (auth_forms, "authenticate"),
            nach_dem_aufruf=True,
        )

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.OK)
        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.FOUND)
        self.pruefe_nach_dem_einschalten_abgemeldet()

    def test_die_website_meldet_an_waehrend_deaktiviert_wird(self):
        ergebnis = self.gleichzeitig(
            self.deaktivieren,
            self.auf_der_website,
            (konto_ansichten, "ueberall_abmelden"),
            nach_dem_aufruf=True,
        )

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.FOUND)
        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.OK)
        self.pruefe_nach_dem_einschalten_abgemeldet()

    def test_eine_anmeldung_vor_dem_deaktivieren_wird_beendet(self):
        """The sign-in holds the account first: the deactivation waits and ends its session."""
        ergebnis = self.gleichzeitig(
            self.auf_der_website,
            self.deaktivieren,
            (konto_ansichten, "auth_login"),
            nach_dem_aufruf=True,
        )

        # 400 where the deactivation ended the session before the middleware
        # saved it once more ('SessionInterrupted'). Which comes first is left open.
        self.assertIn(ergebnis["zuerst"].status_code, (HTTPStatus.FOUND, HTTPStatus.BAD_REQUEST))
        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.FOUND)
        self.pruefe_nach_dem_einschalten_abgemeldet()
