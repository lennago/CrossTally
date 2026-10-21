"""A sign-in with a passkey while a manager switches the account off.

The checks before the signature read the account ('vor_der_signatur'), the
session or the token pair comes after the account's lock ('anmelden'). Until
10/2026 the account read before the lock was let in. A deactivation that
committed in between had already signed the account out everywhere, and the
session or pair that came afterwards held again once the account was
switched on. On the website and in the app, and the app's confirmation came
just the same. Now the account is read once more under the lock, and one
that is switched off or gone is refused there.

Both orders: the deactivation done while the sign-in waits before its
signature, and the deactivation holding the account's row while the
sign-in waits for it. The first side is held until the other one waits for
a lock or is done ('api.test_closing_race._bis_jemand_wartet'). Runs in the
CI, which tests against PostgreSQL.
"""

from http import HTTPStatus
from unittest import skipUnless

from django.db import connection
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from accounts import passkey as website_passkey
from accounts import views as konto_ansichten
from accounts.models import CustomUser
from accounts.passkey import user_handle
from accounts.test_notification import formular
from accounts.test_sign_out import erneuern
from accounts.test_sign_out_race import MitEchtemSchluessel, Nebeneinander
from api.tests import adresse
from django_project.testing import fuehrungskraft

#: Where the sign-in stands after its checks and before the signature.
VOR_DER_SIGNATUR = (website_passkey, "verify_authentication_response")

#: Where the deactivation holds the account: signed out everywhere, not yet committed.
NACH_DEM_ABMELDEN = (konto_ansichten, "ueberall_abmelden")


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class AnmeldenUndDeaktivierenGleichzeitig(MitEchtemSchluessel, Nebeneinander):
    """The counter signs in with the key while the manager switches the account off."""

    def setUp(self):
        super().setUp()
        self.passkey = self.schluessel_des_geraets()
        self.verwaltung = Client(raise_request_exception=False)
        self.verwaltung.force_login(fuehrungskraft(email="leitung@example.com"))

    def schalten(self, aktiv: bool):
        """The tick in the user form of the website, as the manager sends it."""
        konto = CustomUser.objects.get(pk=self.zoe.pk)
        return self.verwaltung.post(
            reverse("user_edit", args=[konto.pk]), formular(konto, is_active=aktiv)
        )

    def auf_der_website(self) -> tuple[Client, object]:
        """A signed-out browser with the device's answer, and how it sends it."""
        browser = Client(raise_request_exception=False)
        optionen = browser.post(reverse("passkey_anmelden_optionen")).json()
        credential = self.geraet.anmeldung(optionen["challenge"], user_handle(self.zoe))

        def anmelden():
            return browser.post(
                reverse("passkey_anmelden"), {"credential": credential}, "application/json"
            )

        return browser, anmelden

    def in_der_app(self):
        antwort = self.in_der_app_vorbereitet()
        return lambda: APIClient(raise_request_exception=False).post(
            adresse("passkey_anmelden_api"), antwort, format="json"
        )

    def pruefe_abgewiesen(self, ergebnis: dict, seite: str) -> None:
        antwort = ergebnis[seite]
        self.assertEqual(antwort.status_code, HTTPStatus.BAD_REQUEST, antwort.content)
        self.assertIn(b"Dieses Konto ist deaktiviert.", antwort.content)
        self.assertEqual(self.schalten(aktiv=True).status_code, HTTPStatus.FOUND)

    def pruefe_abgemeldet(self, browser: Client) -> None:
        self.assertNotEqual(
            browser.get(reverse("passkey_list")).status_code, HTTPStatus.OK, "noch angemeldet"
        )

    def test_auf_der_website_vor_der_signatur_abgeschaltet(self):
        browser, anmelden = self.auf_der_website()
        ergebnis = self.gleichzeitig(anmelden, lambda: self.schalten(aktiv=False), VOR_DER_SIGNATUR)

        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.FOUND)
        self.pruefe_abgewiesen(ergebnis, "zuerst")
        self.pruefe_abgemeldet(browser)

    def test_auf_der_website_waehrend_das_abschalten_das_konto_haelt(self):
        browser, anmelden = self.auf_der_website()
        ergebnis = self.gleichzeitig(
            lambda: self.schalten(aktiv=False), anmelden, NACH_DEM_ABMELDEN, nach_dem_aufruf=True
        )

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.FOUND)
        self.pruefe_abgewiesen(ergebnis, "danach")
        self.pruefe_abgemeldet(browser)

    def test_in_der_app_vor_der_signatur_abgeschaltet(self):
        ergebnis = self.gleichzeitig(
            self.in_der_app(), lambda: self.schalten(aktiv=False), VOR_DER_SIGNATUR
        )

        self.pruefe_abgewiesen(ergebnis, "zuerst")
        self.assertFalse(OutstandingToken.objects.filter(user=self.zoe).exists())

    def test_in_der_app_waehrend_das_abschalten_das_konto_haelt(self):
        ergebnis = self.gleichzeitig(
            lambda: self.schalten(aktiv=False),
            self.in_der_app(),
            NACH_DEM_ABMELDEN,
            nach_dem_aufruf=True,
        )

        self.pruefe_abgewiesen(ergebnis, "danach")
        offen = OutstandingToken.objects.filter(user=self.zoe, blacklistedtoken__isnull=True)
        self.assertFalse(offen.exists(), "ein Token gilt nach dem Einschalten wieder")

    def test_eine_anmeldung_vor_dem_abschalten_wird_abgemeldet(self):
        """The sign-in holds the account first: the deactivation waits and blocks its pair."""
        ergebnis = self.gleichzeitig(
            self.in_der_app(),
            lambda: self.schalten(aktiv=False),
            (RefreshToken, "for_user"),
            autospec=False,
        )

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.OK)
        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.FOUND)
        self.assertEqual(self.schalten(aktiv=True).status_code, HTTPStatus.FOUND)
        self.assertEqual(erneuern(ergebnis["zuerst"].data["refresh"]), HTTPStatus.UNAUTHORIZED)

    def test_die_bestaetigung_in_der_app(self):
        """Signed in already, the token let the request in before the switch."""
        app = APIClient(raise_request_exception=False)
        app.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(self.zoe).access_token}")
        schritt = app.post(adresse("passkey_bestaetigen_optionen_api")).data
        credential = self.geraet.anmeldung(schritt["options"]["challenge"], user_handle(self.zoe))

        def bestaetigen():
            return app.post(
                adresse("passkey_bestaetigen_api"),
                {"credential": credential, "challenge": schritt["challenge"]},
                format="json",
            )

        ergebnis = self.gleichzeitig(
            bestaetigen, lambda: self.schalten(aktiv=False), VOR_DER_SIGNATUR
        )

        self.pruefe_abgewiesen(ergebnis, "zuerst")
        self.assertNotIn(b"bestaetigung", ergebnis["zuerst"].content)
