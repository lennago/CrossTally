"""Passkeys through the API: the counting app registers a key and signs in with it.

The software authenticator of the website's tests stands in for the phone.
"""

import hashlib
import json
from datetime import timedelta
from unittest import mock

from axes.models import AccessAttempt
from django.core import mail, signing
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

from accounts import passkey as website_passkey
from accounts import sign_out
from accounts.models import CustomUser, Passkey
from accounts.passkey import bestaetigung_signiert, user_handle
from accounts.test_passkey import (
    BESTAETIGUNG_ABGELAUFEN,
    BESTAETIGUNG_FEHLT,
    ICLOUD,
    KEIN_PASSKEY,
    NICHT_BESTAETIGT,
    ZWEI_MINUTEN,
    Authenticator,
    KennungAusDemAlphabet,
    bestaetigt,
    hinterlegen,
    spaeter,
)
from accounts.test_token_bounds import ausgegeben
from api import passkey as api_passkey
from api.models import Passkeyfrage
from api.serializers import account as konto_serializer

from .passkey import SALZ
from .tests import adresse

#: What a refused question of the app says, whichever it was.
ABGELAUFEN = "Die Anfrage ist abgelaufen. Bitte noch einmal versuchen."


def entfernen(app: APIClient, pk: int, refresh: object, **beweis):
    """Removes a key as the app does it: the device's own refresh token goes along.

    And what adding asks: the password, or without one the confirmation ('beweis_fuer').
    """
    koerper = {"refresh": refresh} | beweis
    return app.delete(adresse("passkey_loeschen_api", pk), koerper, format="json")


def beweis_fuer(konto: CustomUser, passwort: str | None = None) -> dict:
    """The password of the account, or without one a confirmation with a key just now."""
    if konto.has_usable_password():
        return {"passwort": passwort}
    return {"bestaetigung": bestaetigung_signiert(konto)}


def geraet(konto: CustomUser) -> tuple[APIClient, str]:
    """A device signed in to the app: a client with its access token, and its refresh token."""
    erneuerung = RefreshToken.for_user(konto)
    app = APIClient()
    app.credentials(HTTP_AUTHORIZATION=f"Bearer {erneuerung.access_token}")
    return app, str(erneuerung)


def in_der_app_entfernen(konto: CustomUser, pk: int, passwort: str | None = None):
    """Removes a key from a device of the account signed in to the app."""
    app, refresh = geraet(konto)
    return entfernen(app, pk, refresh, **beweis_fuer(konto, passwort))


def erneuern(refresh: str) -> int:
    return APIClient().post(adresse("token_erneuern"), {"refresh": refresh}).status_code


class PasskeyApi(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.zoe = CustomUser.objects.create_user(
            username="zoe", email="zoe@example.com", password="x", first_name="Z", last_name="Z"
        )

    def setUp(self):
        self.geraet = Authenticator()

    def als(self, konto) -> str:
        """Signed in as the app is, with the access token. Returns the refresh token."""
        erneuerung = RefreshToken.for_user(konto)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {erneuerung.access_token}")
        return str(erneuerung)

    def anlegeoptionen(self, passwort: str | None = "x"):
        """Step one of registering, with the account's password ('None' sends none)."""
        daten = {} if passwort is None else {"passwort": passwort}
        return self.client.post(adresse("passkey_anlegen_optionen_api"), daten, format="json")

    def registrieren(self, name="Handy", **abweichend):
        schritt = self.anlegeoptionen().data
        credential = self.geraet.registrierung(schritt["options"]["challenge"], **abweichend)
        return self.client.post(
            adresse("passkey_anlegen_api"),
            {"credential": credential, "challenge": schritt["challenge"], "name": name},
            format="json",
        )

    def anmelden(self, konto=None, challenge=None, **abweichend):
        schritt = self.client.post(adresse("passkey_anmelden_optionen_api")).data
        credential = self.geraet.anmeldung(
            schritt["options"]["challenge"], user_handle(konto or self.zoe), **abweichend
        )
        return self.client.post(
            adresse("passkey_anmelden_api"),
            {"credential": credential, "challenge": challenge or schritt["challenge"]},
            format="json",
        )

    # -- Registering -----------------------------------------------------------

    def test_die_optionen_brauchen_ein_token(self):
        self.assertEqual(self.client.post(adresse("passkey_anlegen_optionen_api")).status_code, 401)

    def test_die_optionen_tragen_die_signierte_challenge(self):
        """Under a salt of its own and with the account: it proves the password held for it."""
        self.als(self.zoe)
        schritt = self.anlegeoptionen().data
        self.assertEqual(schritt["options"]["user"]["name"], "zoe")
        self.assertEqual(
            signing.loads(schritt["challenge"], salt="api.passkey.anlegen"),
            [schritt["options"]["challenge"], "zoe"],
        )

    def test_ein_schluessel_wird_gespeichert(self):
        self.als(self.zoe)
        antwort = self.registrieren(name="Handy")
        self.assertEqual(antwort.status_code, 201, antwort.data)
        self.assertEqual(antwort.data["name"], "Handy")
        passkey = Passkey.objects.get(benutzer=self.zoe)
        self.assertEqual(passkey.kennung, self.geraet.kennung)

    def test_der_vorgeschlagene_name_spricht_die_sprache_des_kontos(self):
        """The app signs in with a token, so the request has the server's language."""
        CustomUser.objects.filter(pk=self.zoe.pk).update(sprache="en")
        self.als(self.zoe)
        self.geraet = Authenticator(aaguid=ICLOUD)
        antwort = self.registrieren(name="")
        self.assertEqual(antwort.status_code, 201, antwort.data)
        self.assertEqual(Passkey.objects.get(benutzer=self.zoe).name, "iCloud Keychain")

    def test_eine_fremde_challenge_wird_abgewiesen(self):
        self.als(self.zoe)
        schritt = self.anlegeoptionen().data
        credential = self.geraet.registrierung(schritt["options"]["challenge"])
        for falsch in ("", "abc", signing.dumps("x", salt="anderes")):
            with self.subTest(falsch=falsch):
                antwort = self.client.post(
                    adresse("passkey_anlegen_api"),
                    {"credential": credential, "challenge": falsch},
                    format="json",
                )
                self.assertEqual(antwort.status_code, 400)
                self.assertIn("abgelaufen", antwort.data["detail"])
        self.assertFalse(Passkey.objects.exists())

    def test_ohne_pruefung_der_person_wird_abgelehnt(self):
        self.als(self.zoe)
        antwort = self.registrieren(flags=0x41)
        self.assertEqual(antwort.status_code, 400)
        self.assertIn("keinen gültigen Passkey", antwort.data["detail"])

    # -- The password before a new key -----------------------------------------
    # A token does not say who holds the device. Until 10/2026 it sufficed.

    def test_das_richtige_passwort_bringt_die_frage(self):
        self.als(self.zoe)
        antwort = self.anlegeoptionen()
        self.assertEqual(antwort.status_code, 200, antwort.data)
        self.assertEqual(sorted(antwort.data), ["challenge", "options"])

    def test_ein_falsches_passwort_bringt_keine_frage(self):
        self.als(self.zoe)
        antwort = self.anlegeoptionen("daneben")
        self.assertEqual(antwort.status_code, 400)
        self.assertEqual(antwort.data, {"passwort": ["Das Passwort stimmt nicht."]})

    def test_ohne_passwort_keine_frage(self):
        self.als(self.zoe)
        for passwort in (None, ""):
            with self.subTest(passwort=passwort):
                antwort = self.anlegeoptionen(passwort)
                self.assertEqual(antwort.status_code, 400)
                self.assertEqual(list(antwort.data), ["passwort"])
        # Nothing typed is no guess: it does not count towards the lock.
        self.assertFalse(AccessAttempt.objects.exists())

    def test_ein_konto_ohne_passwort_wird_nicht_nach_einem_gefragt(self):
        """Signed in by passkey, say, after the password was switched off.

        It confirms with a key of its own instead ('BestaetigenOhnePasswort').
        This one has none left, and the refusal says who helps.
        """
        self.zoe.set_unusable_password()
        self.zoe.save()
        self.als(self.zoe)

        antwort = self.anlegeoptionen(None)

        self.assertEqual(antwort.status_code, 400)
        self.assertEqual(antwort.data, {"bestaetigung": [KEIN_PASSKEY]})

    def test_falsche_passwoerter_sperren_wie_beim_anmelden(self):
        """The fifth wrong one locks, and the lock holds against the right one too."""
        self.als(self.zoe)
        for _ in range(5):
            antwort = self.anlegeoptionen("daneben")
        self.assertEqual(antwort.status_code, 429)
        self.assertTrue(antwort.json()["gesperrt"])

        self.assertEqual(self.anlegeoptionen().status_code, 429)
        # It is the bolt of the sign-in: the app's is locked from here as well.
        self.client.credentials()
        antwort = self.client.post(adresse("token_holen"), {"username": "zoe", "password": "x"})
        self.assertEqual(antwort.status_code, 429)

    def test_eine_frage_der_anmeldung_legt_keinen_schluessel_an(self):
        """It needs no sign-in. Under the shared salt it served for a new key too."""
        self.als(self.zoe)
        schritt = self.client.post(adresse("passkey_anmelden_optionen_api")).data
        credential = self.geraet.registrierung(schritt["options"]["challenge"])

        antwort = self.client.post(
            adresse("passkey_anlegen_api"),
            {"credential": credential, "challenge": schritt["challenge"], "name": ""},
            format="json",
        )

        self.assertEqual(antwort.status_code, 400)
        self.assertIn("abgelaufen", antwort.data["detail"])
        self.assertFalse(Passkey.objects.exists())

    def test_eine_frage_fuer_ein_anderes_konto_legt_keinen_schluessel_an(self):
        """Whoever knows their own password adds nothing to the account on a phone left lying."""
        ann = CustomUser.objects.create_user(
            username="ann", email="ann@example.com", password="y", first_name="A", last_name="A"
        )
        self.als(ann)
        schritt = self.anlegeoptionen("y").data
        credential = self.geraet.registrierung(schritt["options"]["challenge"])
        self.als(self.zoe)

        antwort = self.client.post(
            adresse("passkey_anlegen_api"),
            {"credential": credential, "challenge": schritt["challenge"], "name": ""},
            format="json",
        )

        self.assertEqual(antwort.status_code, 400)
        self.assertIn("abgelaufen", antwort.data["detail"])
        self.assertFalse(Passkey.objects.exists())

    def test_die_meldung_spricht_die_sprache_des_kontos(self):
        """As for a new address: for Django a call with a token is anonymous."""
        CustomUser.objects.filter(pk=self.zoe.pk).update(sprache="en")
        self.als(self.zoe)
        antwort = self.anlegeoptionen("daneben")
        self.assertEqual(antwort.data["passwort"], ["The password is wrong."])

    def test_die_liste_und_das_loeschen(self):
        eigenes = self.als(self.zoe)
        self.registrieren(name="Handy")
        liste = self.client.get(adresse("passkey_liste_api")).data
        self.assertEqual([eintrag["name"] for eintrag in liste], ["Handy"])
        self.assertIsNone(liste[0]["zuletzt_benutzt"])
        antwort = entfernen(self.client, liste[0]["id"], eigenes, passwort="x")
        # No tokens: this device keeps the refresh token it sent ('accounts/sign_out.py').
        self.assertEqual(antwort.status_code, 204)
        self.assertIsNone(antwort.data)
        self.assertFalse(Passkey.objects.exists())

    def test_anlegen_und_entfernen_gehen_als_mail_hinaus(self):
        """As on the website: whoever did not do it learns of it."""
        eigenes = self.als(self.zoe)
        self.registrieren(name="Handy")
        passkey = Passkey.objects.get(benutzer=self.zoe)
        entfernen(self.client, passkey.pk, eigenes, passwort="x")
        self.assertEqual([nachricht.to for nachricht in mail.outbox], [["zoe@example.com"]] * 2)
        self.assertIn("Neuer Passkey", mail.outbox[0].subject)
        self.assertIn("„Handy“", mail.outbox[1].body)

    def test_fremde_schluessel_bleiben_unsichtbar(self):
        andere = CustomUser.objects.create_user(
            username="ann", email="ann@example.com", password="x", first_name="A", last_name="A"
        )
        fremd = Passkey.objects.create(
            benutzer=andere, kennung="fremd", oeffentlicher_schluessel=b"\x00", name="Laptop"
        )
        eigenes = self.als(self.zoe)
        self.assertEqual(self.client.get(adresse("passkey_liste_api")).data, [])
        antwort = entfernen(self.client, fremd.pk, eigenes, passwort="x")
        self.assertEqual(antwort.status_code, 404)
        self.assertTrue(Passkey.objects.filter(pk=fremd.pk).exists())

    # -- Signing in ----------------------------------------------------------------

    def test_die_signatur_bringt_ein_tokenpaar(self):
        self.als(self.zoe)
        self.registrieren()
        self.client.credentials()
        antwort = self.anmelden()
        self.assertEqual(antwort.status_code, 200, antwort.data)
        self.assertEqual(antwort.data["ich"]["benutzername"], "zoe")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {antwort.data['access']}")
        self.assertEqual(self.client.get(adresse("ich")).data["benutzername"], "zoe")
        passkey = Passkey.objects.get()
        self.assertEqual(passkey.signaturzaehler, 1)
        self.assertIsNotNone(passkey.zuletzt_benutzt)

    def test_die_anmeldung_mit_passkey_ist_die_letzte_anmeldung(self):
        """As with the password: "Last sign-in" knows the app (until 10/2026 it did not)."""
        self.als(self.zoe)
        self.registrieren()
        self.client.credentials()
        self.zoe.refresh_from_db()
        self.assertIsNone(self.zoe.last_login)
        self.assertEqual(self.anmelden().status_code, 200)
        self.zoe.refresh_from_db()
        self.assertIsNotNone(self.zoe.last_login)

    def test_die_anmeldung_mit_passkey_haelt_die_grenze_der_stunde(self):
        """Like the password and the session: every pair counts ('paar_ausstellen')."""
        self.als(self.zoe)
        self.registrieren()
        self.client.credentials()
        ausgegeben(self.zoe, sign_out.HOECHSTENS_JE_STUNDE)

        antwort = self.anmelden()

        self.assertEqual(antwort.status_code, 429)
        self.assertNotIn("refresh", antwort.data)

    def test_die_anmeldung_mit_passkey_sperrt_die_aeltesten_ueber_der_grenze(self):
        self.als(self.zoe)
        self.registrieren()
        self.client.credentials()
        OutstandingToken.objects.filter(user=self.zoe).delete()
        ausgegeben(self.zoe, sign_out.HOECHSTENS_GUELTIG, vor=timedelta(hours=2))

        self.assertEqual(self.anmelden().status_code, 200)

        gueltig = OutstandingToken.objects.filter(user=self.zoe, blacklistedtoken__isnull=True)
        self.assertEqual(gueltig.count(), sign_out.HOECHSTENS_GUELTIG)

    def test_ein_koerper_ohne_felder_ist_eine_absage(self):
        """A JSON array, a string or 'null': until 10/2026 a server error, without signing in."""
        self.client.raise_request_exception = False
        for koerper in ("[]", '"x"', "null", "5"):
            with self.subTest(koerper=koerper):
                antwort = self.client.post(
                    adresse("passkey_anmelden_api"), koerper, content_type="application/json"
                )
                self.assertEqual(antwort.status_code, 400)

    def test_beim_anlegen_ebenso(self):
        self.client.raise_request_exception = False
        self.als(self.zoe)
        for koerper in ("[]", '"x"', "null"):
            with self.subTest(koerper=koerper):
                antwort = self.client.post(
                    adresse("passkey_anlegen_api"), koerper, content_type="application/json"
                )
                self.assertEqual(antwort.status_code, 400)

    def test_anmelden_braucht_kein_token_und_keine_sitzung(self):
        antwort = self.client.post(adresse("passkey_anmelden_optionen_api"))
        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(antwort.data["options"]["userVerification"], "required")

    def test_ein_unbekannter_schluessel_oeffnet_nichts(self):
        antwort = self.anmelden()
        self.assertEqual(antwort.status_code, 400)
        self.assertIn("nicht bekannt", antwort.data["detail"])
        self.assertNotIn("access", antwort.data)

    def test_eine_abgelaufene_challenge_wird_abgewiesen(self):
        self.als(self.zoe)
        self.registrieren()
        self.client.credentials()
        alt = signing.dumps("x", salt=SALZ)
        with self.settings(SECRET_KEY="anderer-schluessel-fuer-den-test"):
            antwort = self.anmelden(challenge=alt)
        self.assertEqual(antwort.status_code, 400)
        self.assertIn("abgelaufen", antwort.data["detail"])

    def test_ein_eben_entfernter_schluessel_ist_eine_absage(self):
        """Removed on another device between reading the key and writing its counter.

        Until 10/2026 writing the counter found no row: a server error.
        """
        self.als(self.zoe)
        self.registrieren()
        self.client.credentials()
        self.client.raise_request_exception = False
        vorher = OutstandingToken.objects.filter(user=self.zoe).count()
        echt = website_passkey.verify_authentication_response

        def entfernt(**kwargs):
            ergebnis = echt(**kwargs)
            Passkey.objects.filter(benutzer=self.zoe).delete()
            return ergebnis

        with mock.patch.object(
            website_passkey, "verify_authentication_response", side_effect=entfernt
        ):
            antwort = self.anmelden()

        self.assertEqual(antwort.status_code, 400)
        self.assertEqual(antwort.data["detail"], "Dieser Passkey ist hier nicht bekannt.")
        self.assertEqual(OutstandingToken.objects.filter(user=self.zoe).count(), vorher)

    def test_eine_kennung_mit_nul_ist_eine_absage(self):
        """PostgreSQL refuses a NUL in a text column. Until 10/2026 a server error."""
        self.client.raise_request_exception = False
        schritt = self.client.post(adresse("passkey_anmelden_optionen_api")).data
        credential = self.geraet.anmeldung(schritt["options"]["challenge"], user_handle(self.zoe))

        antwort = self.client.post(
            adresse("passkey_anmelden_api"),
            {"credential": credential | {"id": "AA\x00AA"}, "challenge": schritt["challenge"]},
            format="json",
        )

        self.assertEqual(antwort.status_code, 400)
        self.assertEqual(
            antwort.data["detail"], "Der Browser hat keinen gültigen Passkey geliefert."
        )

    def test_eine_kennung_ausserhalb_des_alphabets_ist_eine_absage(self):
        """A lone surrogate reached the database: a server error for anyone (until 10/2026).

        The body is written by hand: DRF's renderer would refuse to encode it.
        """
        self.client.raise_request_exception = False
        for kennung in KennungAusDemAlphabet.FAELLE:
            with self.subTest(kennung=repr(kennung)):
                schritt = self.client.post(adresse("passkey_anmelden_optionen_api")).data
                credential = self.geraet.anmeldung(
                    schritt["options"]["challenge"], user_handle(self.zoe)
                )
                koerper = {
                    "credential": credential | {"id": kennung},
                    "challenge": schritt["challenge"],
                }
                with CaptureQueriesContext(connection) as abfragen:
                    antwort = self.client.post(
                        adresse("passkey_anmelden_api"),
                        json.dumps(koerper),
                        content_type="application/json",
                    )
                self.assertEqual(antwort.status_code, 400, antwort.content)
                self.assertEqual(
                    antwort.data, {"detail": "Der Browser hat keinen gültigen Passkey geliefert."}
                )
                self.assertEqual([a for a in abfragen if "accounts_passkey" in a["sql"]], [])

    def test_ein_name_mit_einzelnem_surrogat_verliert_es(self):
        """Like a NUL: no database stores it, and the new key ended in a server error."""
        self.client.raise_request_exception = False
        self.als(self.zoe)
        schritt = self.anlegeoptionen().data
        credential = self.geraet.registrierung(schritt["options"]["challenge"])

        antwort = self.client.post(
            adresse("passkey_anlegen_api"),
            json.dumps(
                {"credential": credential, "challenge": schritt["challenge"], "name": "Han\ud800dy"}
            ),
            content_type="application/json",
        )

        self.assertEqual(antwort.status_code, 201, antwort.content)
        self.assertEqual(Passkey.objects.get().name, "Handy")

    def test_ein_deaktiviertes_konto_bleibt_zu(self):
        self.als(self.zoe)
        self.registrieren()
        self.client.credentials()
        CustomUser.objects.filter(pk=self.zoe.pk).update(is_active=False)
        antwort = self.anmelden()
        self.assertEqual(antwort.status_code, 400)
        self.assertIn("deaktiviert", antwort.data["detail"])

    def test_ein_eben_deaktiviertes_konto_bekommt_kein_paar(self):
        """Switched off between the checks before the signature and the pair.

        Until 10/2026 the account read before the lock got its pair, after the
        deactivation had blocked every token. Switched on again, it renewed.
        """
        self.als(self.zoe)
        self.registrieren()
        self.client.credentials()
        vorher = OutstandingToken.objects.filter(user=self.zoe).count()
        echt = website_passkey.verify_authentication_response

        def deaktiviert(**kwargs):
            ergebnis = echt(**kwargs)
            CustomUser.objects.filter(pk=self.zoe.pk).update(is_active=False)
            return ergebnis

        with mock.patch.object(
            website_passkey, "verify_authentication_response", side_effect=deaktiviert
        ):
            antwort = self.anmelden()

        self.assertEqual(antwort.status_code, 400)
        self.assertEqual(antwort.data, {"detail": "Dieses Konto ist deaktiviert."})
        self.assertEqual(OutstandingToken.objects.filter(user=self.zoe).count(), vorher)

    def test_die_meldung_folgt_dem_sprachparameter(self):
        antwort = self.client.post(
            adresse("passkey_anmelden_api") + "?sprache=en",
            {"credential": {}, "challenge": ""},
            format="json",
        )
        self.assertEqual(antwort.data["detail"], "The request has expired. Please try again.")


class BestaetigenOhnePasswort(APITestCase):
    """An account without a password confirms with one of its keys, in the app as on the website.

    A token does not say who holds the device, and such an account has no
    password to type. Until 10/2026 it was asked nothing before a new key or
    a new address. Now a fresh signature of one of its own keys brings a
    signed confirmation, and that goes along with the step.
    """

    def setUp(self):
        self.zoe = CustomUser.objects.create_user(
            username="zoe", email="zoe@example.com", password=None, first_name="Z", last_name="Z"
        )
        self.geraet = Authenticator()
        self.passkey = hinterlegen(self.zoe, self.geraet)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(self.zoe).access_token}"
        )

    def optionen(self):
        return self.client.post(adresse("passkey_bestaetigen_optionen_api"), {}, format="json")

    def bestaetigen(self, geraet=None, handle=None, challenge=None, **abweichend):
        """Both steps of the confirmation. 'challenge' sends back another token."""
        schritt = self.optionen().data
        credential = (geraet or self.geraet).anmeldung(
            schritt["options"]["challenge"], handle or user_handle(self.zoe), **abweichend
        )
        return self.client.post(
            adresse("passkey_bestaetigen_api"),
            {"credential": credential, "challenge": challenge or schritt["challenge"]},
            format="json",
        )

    def bestaetigung(self) -> str:
        antwort = self.bestaetigen()
        self.assertEqual(antwort.status_code, 200, antwort.data)
        return antwort.data["bestaetigung"]

    def anlegeoptionen(self, **daten):
        return self.client.post(adresse("passkey_anlegen_optionen_api"), daten, format="json")

    def neuer_schluessel(self, bestaetigung: str):
        """Both steps of registering a second device, with the confirmation."""
        schritt = self.anlegeoptionen(bestaetigung=bestaetigung)
        self.assertEqual(schritt.status_code, 200, schritt.data)
        credential = Authenticator().registrierung(schritt.data["options"]["challenge"])
        return self.client.post(
            adresse("passkey_anlegen_api"),
            {"credential": credential, "challenge": schritt.data["challenge"], "name": "Laptop"},
            format="json",
        )

    def adresse_aendern(self, **daten):
        daten = {"email": "zoe.neu@example.com"} | daten
        return self.client.post(adresse("email_aendern_api"), daten, format="json")

    def pruefe_abgewiesen(self, antwort, detail: str) -> None:
        self.assertEqual(antwort.status_code, 400, antwort.data)
        self.assertEqual(antwort.data, {"detail": detail})

    # -- The two steps -------------------------------------------------------------

    def test_die_frage_nennt_nur_die_eigenen_schluessel(self):
        andere = CustomUser.objects.create_user(
            username="ann", email="ann@example.com", password="y", first_name="A", last_name="A"
        )
        hinterlegen(andere, Authenticator())
        zweiter = hinterlegen(self.zoe, Authenticator(), name="Laptop")

        antwort = self.optionen()

        self.assertEqual(antwort.status_code, 200, antwort.data)
        self.assertEqual(sorted(antwort.data), ["challenge", "options"])
        optionen = antwort.data["options"]
        self.assertEqual(optionen["userVerification"], "required")
        self.assertEqual(
            sorted(eintrag["id"] for eintrag in optionen["allowCredentials"]),
            sorted([self.passkey.kennung, zweiter.kennung]),
        )
        # Under a salt of its own, and for this account.
        self.assertEqual(
            signing.loads(antwort.data["challenge"], salt="api.passkey.bestaetigen"),
            [optionen["challenge"], "zoe"],
        )

    def test_die_signatur_bringt_die_bestaetigung(self):
        antwort = self.bestaetigen()

        self.assertEqual(antwort.status_code, 200, antwort.data)
        self.assertEqual(list(antwort.data), ["bestaetigung"])
        self.assertEqual(
            signing.loads(antwort.data["bestaetigung"], salt="api.passkey.bestaetigung"), "zoe"
        )
        # A use of the key like a sign-in: the counter climbs.
        self.passkey.refresh_from_db()
        self.assertEqual(self.passkey.signaturzaehler, 1)
        self.assertIsNotNone(self.passkey.zuletzt_benutzt)

    def test_ohne_passkey_keine_frage(self):
        Passkey.objects.all().delete()
        self.pruefe_abgewiesen(self.optionen(), KEIN_PASSKEY)

    def test_die_optionen_brauchen_ein_token(self):
        self.client.credentials()
        for name in ("passkey_bestaetigen_optionen_api", "passkey_bestaetigen_api"):
            with self.subTest(name=name):
                self.assertEqual(self.client.post(adresse(name)).status_code, 401)

    def test_die_frage_gilt_zwei_minuten(self):
        schritt = self.optionen().data
        credential = self.geraet.anmeldung(schritt["options"]["challenge"], user_handle(self.zoe))

        with spaeter(ZWEI_MINUTEN + 1):
            antwort = self.client.post(
                adresse("passkey_bestaetigen_api"),
                {"credential": credential, "challenge": schritt["challenge"]},
                format="json",
            )

        self.pruefe_abgewiesen(antwort, ABGELAUFEN)

    def test_ein_schluessel_eines_anderen_kontos_bestaetigt_nicht(self):
        andere = CustomUser.objects.create_user(
            username="ann", email="ann@example.com", password=None, first_name="A", last_name="A"
        )
        fremd = Authenticator()
        hinterlegen(andere, fremd)

        antwort = self.bestaetigen(geraet=fremd, handle=user_handle(andere))

        self.pruefe_abgewiesen(antwort, "Dieser Passkey gehört zu einem anderen Konto.")

    def test_ohne_pruefung_der_person_bestaetigt_nichts(self):
        self.pruefe_abgewiesen(self.bestaetigen(flags=0x01), NICHT_BESTAETIGT)

    def test_eine_fremde_herkunft_bestaetigt_nichts(self):
        self.pruefe_abgewiesen(self.bestaetigen(origin="https://example.org"), NICHT_BESTAETIGT)

    def test_ein_unbekannter_schluessel_bestaetigt_nichts(self):
        antwort = self.bestaetigen(geraet=Authenticator())
        self.pruefe_abgewiesen(antwort, "Dieser Passkey ist hier nicht bekannt.")

    def test_ein_eben_deaktiviertes_konto_bestaetigt_nichts(self):
        """Switched off after the token let the request in: asked once more under the lock."""
        echt = website_passkey.verify_authentication_response

        def deaktiviert(**kwargs):
            ergebnis = echt(**kwargs)
            CustomUser.objects.filter(pk=self.zoe.pk).update(is_active=False)
            return ergebnis

        with mock.patch.object(
            website_passkey, "verify_authentication_response", side_effect=deaktiviert
        ):
            antwort = self.bestaetigen()

        self.pruefe_abgewiesen(antwort, "Dieses Konto ist deaktiviert.")

    def test_ein_koerper_ohne_felder_ist_eine_absage(self):
        self.client.raise_request_exception = False
        for koerper in ("[]", '"x"', "null"):
            with self.subTest(koerper=koerper):
                antwort = self.client.post(
                    adresse("passkey_bestaetigen_api"), koerper, content_type="application/json"
                )
                self.assertEqual(antwort.status_code, 400)

    # -- Questions and confirmations do not stand in for one another ---------------

    def test_eine_fremde_frage_bestaetigt_nichts(self):
        """Signing in is open to anyone, and a new key's question is not a confirmation."""
        anmeldung = self.client.post(adresse("passkey_anmelden_optionen_api")).data["challenge"]
        anlegen = self.anlegeoptionen(bestaetigung=self.bestaetigung()).data["challenge"]
        ann = CustomUser.objects.create_user(
            username="ann", email="ann@example.com", password=None, first_name="A", last_name="A"
        )
        hinterlegen(ann, Authenticator())
        app = APIClient()
        app.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(ann).access_token}")
        fremdes_konto = app.post(adresse("passkey_bestaetigen_optionen_api")).data["challenge"]
        for fall, falsch in {
            "Anmelden": anmeldung,
            "Anlegen": anlegen,
            "anderes Konto": fremdes_konto,
            "Bestätigung": self.bestaetigung(),
            "fremdes Salz": signing.dumps(["x", "zoe"], salt="anderes"),
        }.items():
            with self.subTest(fall=fall):
                self.pruefe_abgewiesen(self.bestaetigen(challenge=falsch), ABGELAUFEN)

    def test_die_frage_der_bestaetigung_meldet_nicht_an_und_legt_nichts_an(self):
        schritt = self.optionen().data
        frage = schritt["options"]["challenge"]
        anmeldung = self.geraet.anmeldung(frage, user_handle(self.zoe))
        registrierung = Authenticator().registrierung(frage)

        angemeldet = APIClient().post(
            adresse("passkey_anmelden_api"),
            {"credential": anmeldung, "challenge": schritt["challenge"]},
            format="json",
        )
        angelegt = self.client.post(
            adresse("passkey_anlegen_api"),
            {"credential": registrierung, "challenge": schritt["challenge"], "name": ""},
            format="json",
        )

        for antwort in (angemeldet, angelegt):
            self.pruefe_abgewiesen(antwort, ABGELAUFEN)
        self.assertNotIn("access", angemeldet.data)
        self.assertEqual(Passkey.objects.count(), 1)

    # -- A new key ----------------------------------------------------------------

    def test_ohne_bestaetigung_kein_neuer_schluessel(self):
        for daten in ({}, {"bestaetigung": ""}, {"bestaetigung": None}, {"passwort": "x"}):
            with self.subTest(daten=daten):
                antwort = self.anlegeoptionen(**daten)
                self.assertEqual(antwort.status_code, 400)
                self.assertEqual(antwort.data, {"bestaetigung": [BESTAETIGUNG_FEHLT]})

    def test_bestaetigt_kommt_der_neue_schluessel(self):
        antwort = self.neuer_schluessel(self.bestaetigung())

        self.assertEqual(antwort.status_code, 201, antwort.data)
        self.assertEqual(Passkey.objects.filter(benutzer=self.zoe).count(), 2)

    def test_eine_falsche_bestaetigung_gilt_nicht(self):
        for fall, falsch in {
            "Unsinn": "abc",
            "fremdes Salz": signing.dumps("zoe", salt="anderes"),
            "anderes Konto": signing.dumps("ann", salt="api.passkey.bestaetigung"),
            "Frage statt Bestätigung": self.optionen().data["challenge"],
            "Anmelden statt Bestätigung": signing.dumps("zoe", salt=SALZ),
            "Zahl": 7,
        }.items():
            with self.subTest(fall=fall):
                antwort = self.anlegeoptionen(bestaetigung=falsch)
                self.assertEqual(antwort.status_code, 400)
                self.assertEqual(antwort.data, {"bestaetigung": [BESTAETIGUNG_ABGELAUFEN]})

    def test_eine_liste_statt_der_bestaetigung(self):
        antwort = self.anlegeoptionen(bestaetigung=[self.bestaetigung()])
        self.assertEqual(antwort.status_code, 400)
        self.assertEqual(list(antwort.data), ["bestaetigung"])

    def test_die_bestaetigung_gilt_zwei_minuten(self):
        """For every step in that time: a dialog of the device broken off may be tried again."""
        bestaetigung = self.bestaetigung()

        with spaeter(ZWEI_MINUTEN - 1):
            self.assertEqual(self.anlegeoptionen(bestaetigung=bestaetigung).status_code, 200)
            self.assertEqual(self.anlegeoptionen(bestaetigung=bestaetigung).status_code, 200)
        with spaeter(ZWEI_MINUTEN + 1):
            antwort = self.anlegeoptionen(bestaetigung=bestaetigung)

        self.assertEqual(antwort.status_code, 400)
        self.assertEqual(antwort.data, {"bestaetigung": [BESTAETIGUNG_ABGELAUFEN]})

    def test_die_meldung_spricht_die_sprache_des_kontos(self):
        CustomUser.objects.filter(pk=self.zoe.pk).update(sprache="en")
        self.assertEqual(
            self.anlegeoptionen().data,
            {"bestaetigung": ["Please confirm with one of your passkeys first."]},
        )
        self.assertEqual(
            self.bestaetigen(flags=0x01).data,
            {"detail": "Confirming with this passkey failed."},
        )

    def test_ein_konto_mit_passwort_bleibt_beim_passwort(self):
        """The confirmation does not stand in for a password the account has."""
        self.zoe.set_password("x")
        self.zoe.save()
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(self.zoe).access_token}"
        )
        bestaetigung = self.bestaetigung()

        antwort = self.anlegeoptionen(bestaetigung=bestaetigung)

        self.assertEqual(antwort.status_code, 400)
        self.assertEqual(list(antwort.data), ["passwort"])
        self.assertEqual(self.adresse_aendern(bestaetigung=bestaetigung).status_code, 400)
        self.assertEqual(self.anlegeoptionen(passwort="x").status_code, 200)

    # -- A new address ------------------------------------------------------------

    def test_ohne_bestaetigung_keine_neue_adresse(self):
        antwort = self.adresse_aendern()

        self.assertEqual(antwort.status_code, 400)
        self.assertEqual(antwort.data, {"bestaetigung": [BESTAETIGUNG_FEHLT]})
        self.assertEqual(mail.outbox, [])

    def test_bestaetigt_geht_die_mail_an_die_neue_adresse(self):
        antwort = self.adresse_aendern(bestaetigung=self.bestaetigung())

        self.assertEqual(antwort.status_code, 200, antwort.data)
        self.assertEqual(antwort.data, {"email": "zoe.neu@example.com", "bestaetigung": True})
        self.assertEqual(mail.outbox[0].to, ["zoe.neu@example.com"])

    def test_eine_abgelaufene_bestaetigung_aendert_keine_adresse(self):
        bestaetigung = self.bestaetigung()
        with spaeter(ZWEI_MINUTEN + 1):
            antwort = self.adresse_aendern(bestaetigung=bestaetigung)
        self.assertEqual(antwort.status_code, 400)
        self.assertEqual(antwort.data, {"bestaetigung": [BESTAETIGUNG_ABGELAUFEN]})
        self.assertEqual(mail.outbox, [])


class LiegengebliebenesZugangstoken(APITestCase):
    """A phone signed out by a removal still holds its access token, for up to an hour.

    For an account without a password nothing else was asked before a new key
    or a new address. The phone added a key of its own with its last access
    token and signed in with it for good, or pointed the account at another
    mailbox (found by a review, until 10/2026). Its own key is gone, so it
    cannot confirm either.
    """

    def setUp(self):
        self.zoe = CustomUser.objects.create_user(
            username="zoe", email="zoe@example.com", password=None, first_name="Z", last_name="Z"
        )
        self.handy = Authenticator()
        handy = hinterlegen(self.zoe, self.handy, name="Handy")
        hinterlegen(self.zoe, Authenticator(), name="Laptop")
        verloren = RefreshToken.for_user(self.zoe)
        self.verloren = str(verloren)
        self.dieb = APIClient()
        self.dieb.credentials(HTTP_AUTHORIZATION=f"Bearer {verloren.access_token}")
        # The owner removes the lost phone's key on the website, confirmed with the laptop.
        web = Client()
        web.force_login(self.zoe)
        bestaetigt(web, self.zoe)
        web.post(reverse("passkey_delete", args=[handy.pk]))
        mail.outbox.clear()

    def test_das_zugangstoken_gilt_noch(self):
        """What the attack rests on: the refresh token is blocked, the access token is not."""
        erneuert = APIClient().post(adresse("token_erneuern"), {"refresh": self.verloren})
        self.assertEqual(erneuert.status_code, 401)
        self.assertEqual(self.dieb.get(adresse("ich")).status_code, 200)

    def test_kein_eigener_schluessel(self):
        schritt = self.dieb.post(adresse("passkey_anlegen_optionen_api"), {}, format="json")

        self.assertEqual(schritt.status_code, 400)
        self.assertEqual(schritt.data, {"bestaetigung": [BESTAETIGUNG_FEHLT]})
        self.assertEqual(
            list(Passkey.objects.filter(benutzer=self.zoe).values_list("name", flat=True)),
            ["Laptop"],
        )

    def test_keine_neue_adresse(self):
        antwort = self.dieb.post(
            adresse("email_aendern_api"), {"email": "fremd@example.org"}, format="json"
        )

        self.assertEqual(antwort.status_code, 400)
        self.assertEqual(antwort.data, {"bestaetigung": [BESTAETIGUNG_FEHLT]})
        self.assertEqual(mail.outbox, [])

    def test_der_entfernte_schluessel_bestaetigt_nichts(self):
        schritt = self.dieb.post(adresse("passkey_bestaetigen_optionen_api")).data
        credential = self.handy.anmeldung(schritt["options"]["challenge"], user_handle(self.zoe))

        antwort = self.dieb.post(
            adresse("passkey_bestaetigen_api"),
            {"credential": credential, "challenge": schritt["challenge"]},
            format="json",
        )

        self.assertEqual(antwort.status_code, 400)
        self.assertEqual(antwort.data, {"detail": "Dieser Passkey ist hier nicht bekannt."})


class EntfernenMitDerEigenenAnmeldung(APITestCase):
    """Removing a key needs the refresh token of the device that removes it.

    Removing signs the account out everywhere, but an access token runs on for
    up to an hour. Until 10/2026 any access token got a fresh pair here. A
    phone signed out by an earlier removal took a lasting sign-in from it and
    removed the owner's other keys.
    """

    NEU_ANMELDEN = "Bitte melde dich neu an, um den Passkey zu entfernen."

    def setUp(self):
        self.zoe = CustomUser.objects.create_user(
            username="zoe", email="zoe@example.com", password="x", first_name="Z", last_name="Z"
        )
        self.handy = Passkey.objects.create(
            benutzer=self.zoe, kennung="aaa", oeffentlicher_schluessel=b"x", name="Handy"
        )
        self.laptop = Passkey.objects.create(
            benutzer=self.zoe, kennung="bbb", oeffentlicher_schluessel=b"x", name="Laptop"
        )

    def pruefe_abgewiesen(self, antwort) -> None:
        self.assertEqual(antwort.status_code, 403, antwort.data)
        self.assertEqual(antwort.data, {"detail": self.NEU_ANMELDEN})

    def test_ein_abgemeldetes_geraet_entfernt_keinen_schluessel(self):
        verloren, verloren_refresh = geraet(self.zoe)
        besitzerin, besitzerin_refresh = geraet(self.zoe)
        self.assertEqual(
            entfernen(besitzerin, self.handy.pk, besitzerin_refresh, passwort="x").status_code, 204
        )
        ausgegeben = OutstandingToken.objects.count()

        # The lost phone still holds its access token, its refresh token is blocked.
        antwort = entfernen(verloren, self.laptop.pk, verloren_refresh, passwort="x")

        self.pruefe_abgewiesen(antwort)
        self.assertTrue(Passkey.objects.filter(pk=self.laptop.pk).exists())
        self.assertEqual(OutstandingToken.objects.count(), ausgegeben)
        self.assertEqual(erneuern(besitzerin_refresh), 200)

    def test_ohne_eigenes_gueltiges_erneuerungstoken_bleibt_alles(self):
        nele = CustomUser.objects.create_user(
            username="nele", email="nele@example.com", password="y", first_name="N", last_name="N"
        )
        vor_dem_passwort = str(RefreshToken.for_user(self.zoe))
        self.zoe.set_password("neu-und-lang")
        self.zoe.save()
        app, eigenes = geraet(self.zoe)
        gesperrt = RefreshToken.for_user(self.zoe)
        gesperrt.blacklist()
        abgelaufen = RefreshToken.for_user(self.zoe)
        abgelaufen.set_exp(lifetime=-timedelta(seconds=1))
        unbekannt = RefreshToken.for_user(self.zoe)
        OutstandingToken.objects.filter(jti=unbekannt["jti"]).delete()
        faelle = {
            "fremdes Konto": str(RefreshToken.for_user(nele)),
            "gesperrt": str(gesperrt),
            "abgelaufen": str(abgelaufen),
            "dem Server unbekannt": str(unbekannt),
            "vor dem neuen Passwort": vor_dem_passwort,
            "Zugangstoken": str(RefreshToken.for_user(self.zoe).access_token),
            "leer": "",
            "Unsinn": "abc",
            "Liste": [eigenes],
            "Zahl": 7,
        }
        gesperrte = BlacklistedToken.objects.count()
        adresse_des_handys = adresse("passkey_loeschen_api", self.handy.pk)
        for fall, refresh in faelle.items():
            with self.subTest(fall=fall):
                antwort = entfernen(app, self.handy.pk, refresh, passwort="neu-und-lang")
                self.pruefe_abgewiesen(antwort)
        with self.subTest(koerper="ohne Token"):
            antwort = app.delete(adresse_des_handys, {"passwort": "neu-und-lang"}, format="json")
            self.pruefe_abgewiesen(antwort)
        # Without a password nothing gets as far as the token ('PasswortOderPasskeySerializer').
        for fall, koerper in {"ohne": None, "JSON-Liste": [eigenes]}.items():
            with self.subTest(koerper=fall):
                antwort = app.delete(adresse_des_handys, koerper, format="json")
                self.assertEqual(antwort.status_code, 400, antwort.data)
        self.assertEqual(Passkey.objects.filter(benutzer=self.zoe).count(), 2)
        self.assertEqual(BlacklistedToken.objects.count(), gesperrte)
        self.assertEqual(erneuern(eigenes), 200)

    def test_das_eigene_token_bleibt_und_es_kommt_kein_neues(self):
        anderswo = str(RefreshToken.for_user(self.zoe))
        app, eigenes = geraet(self.zoe)
        ausgegeben = OutstandingToken.objects.count()

        antwort = entfernen(app, self.handy.pk, eigenes, passwort="x")

        self.assertEqual(antwort.status_code, 204)
        self.assertEqual(antwort.content, b"")
        self.assertEqual(OutstandingToken.objects.count(), ausgegeben)
        self.assertEqual(erneuern(eigenes), 200)
        self.assertEqual(erneuern(anderswo), 401)
        self.assertFalse(Passkey.objects.filter(pk=self.handy.pk).exists())

    def test_ein_erneuerungstoken_mit_einzelnem_surrogat_ist_keins(self):
        """Read as a token it raised past 'TokenError': a server error under the lock."""
        app, eigenes = geraet(self.zoe)
        app.raise_request_exception = False

        antwort = app.delete(
            adresse("passkey_loeschen_api", self.handy.pk),
            json.dumps({"refresh": "eyJ\ud800", "passwort": "x"}),
            content_type="application/json",
        )

        self.pruefe_abgewiesen(antwort)
        self.assertEqual(Passkey.objects.filter(benutzer=self.zoe).count(), 2)
        self.assertEqual(erneuern(eigenes), 200)

    def test_die_absage_spricht_die_sprache_der_app(self):
        app, _eigenes = geraet(self.zoe)
        antwort = app.delete(
            adresse("passkey_loeschen_api", self.handy.pk),
            {"passwort": "x"},
            format="json",
            HTTP_X_SPRACHE="en",
        )
        self.assertEqual(antwort.data, {"detail": "Please sign in again to remove the passkey."})


class EntfernenMitPasswort(APITestCase):
    """Removing a key in the app asks what adding one asks: the account's password.

    The refresh token shows the device's sign-in, not who holds the phone.
    Until 10/2026 an unlocked phone sufficed to remove every key, and the
    owner was signed out everywhere else.
    """

    def setUp(self):
        self.zoe = CustomUser.objects.create_user(
            username="zoe", email="zoe@example.com", password="x", first_name="Z", last_name="Z"
        )
        self.handy = hinterlegen(self.zoe, Authenticator(), name="Handy")
        self.app, self.eigenes = geraet(self.zoe)
        # Another device of the owner, which a removal would sign out.
        self.anderswo = str(RefreshToken.for_user(self.zoe))

    def entfernen(self, **beweis):
        return entfernen(self.app, self.handy.pk, self.eigenes, **beweis)

    def pruefe_abgelehnt(self, antwort, daten: dict) -> None:
        self.assertEqual(antwort.status_code, 400, antwort.data)
        self.assertEqual(antwort.data, daten)
        self.assertTrue(Passkey.objects.filter(pk=self.handy.pk).exists())
        self.assertEqual(erneuern(self.anderswo), 200)
        self.assertEqual(mail.outbox, [])

    def test_ohne_passwort_bleibt_alles(self):
        for beweis in ({}, {"passwort": ""}, {"passwort": None}):
            with self.subTest(beweis=beweis):
                antwort = self.entfernen(**beweis)
                self.assertEqual(antwort.status_code, 400, antwort.data)
                self.assertEqual(list(antwort.data), ["passwort"])
        self.assertTrue(Passkey.objects.filter(pk=self.handy.pk).exists())
        self.assertEqual(erneuern(self.anderswo), 200)
        # Nothing typed is no guess: it does not count towards the lock.
        self.assertFalse(AccessAttempt.objects.exists())

    def test_ein_falsches_passwort_bleibt_alles(self):
        self.pruefe_abgelehnt(
            self.entfernen(passwort="daneben"), {"passwort": ["Das Passwort stimmt nicht."]}
        )
        self.assertEqual(AccessAttempt.objects.get().failures_since_start, 1)

    def test_mit_dem_passwort_geht_er(self):
        antwort = self.entfernen(passwort="x")

        self.assertEqual(antwort.status_code, 204, antwort.data)
        self.assertFalse(Passkey.objects.exists())
        self.assertEqual(erneuern(self.anderswo), 401)
        self.assertEqual(erneuern(self.eigenes), 200)

    def test_falsche_passwoerter_sperren_wie_beim_anmelden(self):
        """The fifth wrong one locks, and the lock holds against the right one too."""
        for _ in range(5):
            antwort = self.entfernen(passwort="daneben")
        self.assertEqual(antwort.status_code, 429)
        self.assertTrue(antwort.json()["gesperrt"])

        self.assertEqual(self.entfernen(passwort="x").status_code, 429)
        self.assertTrue(Passkey.objects.filter(pk=self.handy.pk).exists())
        antwort = APIClient().post(adresse("token_holen"), {"username": "zoe", "password": "x"})
        self.assertEqual(antwort.status_code, 429)

    def test_erst_das_passwort_dann_das_erneuerungstoken(self):
        """Without the password the token is not even looked at."""
        abgemeldet = entfernen(self.app, self.handy.pk, "abc", passwort="daneben")
        self.assertEqual(abgemeldet.data, {"passwort": ["Das Passwort stimmt nicht."]})

        abgemeldet = entfernen(self.app, self.handy.pk, "abc", passwort="x")
        self.assertEqual(abgemeldet.status_code, 403)
        self.assertTrue(Passkey.objects.filter(pk=self.handy.pk).exists())

    def test_das_passwort_wird_vor_der_sperre_geprueft(self):
        """Hashing under the administrators' lock would hold every other removal as long."""
        folge = []
        echt_passwort, echt_sperre = (
            konto_serializer.passwort_haelt,
            api_passkey.administratoren_sperren,
        )

        def passwort(*args):
            folge.append("Passwort")
            return echt_passwort(*args)

        def sperre():
            folge.append("Sperre")
            return echt_sperre()

        with (
            mock.patch.object(konto_serializer, "passwort_haelt", side_effect=passwort),
            mock.patch.object(api_passkey, "administratoren_sperren", side_effect=sperre),
        ):
            self.assertEqual(self.entfernen(passwort="x").status_code, 204)

        self.assertEqual(folge, ["Passwort", "Sperre"])

    def test_ein_fremder_schluessel_ist_unbekannt(self):
        ann = CustomUser.objects.create_user(
            username="ann", email="ann@example.com", password="y", first_name="A", last_name="A"
        )
        fremd = hinterlegen(ann, Authenticator(), name="Laptop")

        self.assertEqual(entfernen(self.app, fremd.pk, self.eigenes, passwort="x").status_code, 404)
        self.assertTrue(Passkey.objects.filter(pk=fremd.pk).exists())

    def test_eine_bestaetigung_ersetzt_das_passwort_nicht(self):
        antwort = self.entfernen(bestaetigung=bestaetigung_signiert(self.zoe))

        self.assertEqual(antwort.status_code, 400)
        self.assertEqual(list(antwort.data), ["passwort"])

    def test_die_absage_spricht_die_sprache_des_kontos(self):
        """As for a new key: for Django a call with a token is anonymous."""
        CustomUser.objects.filter(pk=self.zoe.pk).update(sprache="en")

        antwort = self.entfernen(passwort="daneben")

        self.assertEqual(antwort.data, {"passwort": ["The password is wrong."]})


class EntfernenOhnePasswort(APITestCase):
    """Without a password one of the account's keys confirms the removal, as before a new key.

    Until 10/2026 the unlocked phone of such an account removed every key, and
    the owner had neither a key nor a password left.
    """

    def setUp(self):
        self.zoe = CustomUser.objects.create_user(
            username="zoe", email="zoe@example.com", password=None, first_name="Z", last_name="Z"
        )
        self.geraet = Authenticator()
        self.handy = hinterlegen(self.zoe, self.geraet, name="Handy")
        self.laptop = hinterlegen(self.zoe, Authenticator(), name="Laptop")
        self.app, self.eigenes = geraet(self.zoe)

    def entfernen(self, passkey: Passkey, **beweis):
        return entfernen(self.app, passkey.pk, self.eigenes, **beweis)

    def bestaetigung(self) -> str:
        """The two steps of the app's confirmation, with the phone's key."""
        schritt = self.app.post(adresse("passkey_bestaetigen_optionen_api")).data
        credential = self.geraet.anmeldung(schritt["options"]["challenge"], user_handle(self.zoe))
        antwort = self.app.post(
            adresse("passkey_bestaetigen_api"),
            {"credential": credential, "challenge": schritt["challenge"]},
            format="json",
        )
        self.assertEqual(antwort.status_code, 200, antwort.data)
        return antwort.data["bestaetigung"]

    def test_ohne_bestaetigung_bleibt_alles(self):
        for beweis in ({}, {"bestaetigung": ""}, {"passwort": "x"}):
            with self.subTest(beweis=beweis):
                antwort = self.entfernen(self.laptop, **beweis)
                self.assertEqual(antwort.status_code, 400)
                self.assertEqual(antwort.data, {"bestaetigung": [BESTAETIGUNG_FEHLT]})
        self.assertEqual(Passkey.objects.filter(benutzer=self.zoe).count(), 2)

    def test_eine_falsche_bestaetigung_gilt_nicht(self):
        bestaetigung = self.bestaetigung()
        for fall, falsch in {
            "anderes Konto": signing.dumps("ann", salt="api.passkey.bestaetigung"),
            "fremdes Salz": signing.dumps("zoe", salt="anderes"),
            "Unsinn": "abc",
        }.items():
            with self.subTest(fall=fall):
                antwort = self.entfernen(self.laptop, bestaetigung=falsch)
                self.assertEqual(antwort.data, {"bestaetigung": [BESTAETIGUNG_ABGELAUFEN]})
        with spaeter(ZWEI_MINUTEN + 1):
            antwort = self.entfernen(self.laptop, bestaetigung=bestaetigung)
        self.assertEqual(antwort.data, {"bestaetigung": [BESTAETIGUNG_ABGELAUFEN]})
        self.assertEqual(Passkey.objects.filter(benutzer=self.zoe).count(), 2)

    def test_bestaetigt_geht_er_und_die_bestaetigung_gilt_weiter(self):
        """Not used up, as before a new key: in its two minutes it removes a second one too."""
        bestaetigung = self.bestaetigung()

        self.assertEqual(self.entfernen(self.laptop, bestaetigung=bestaetigung).status_code, 204)
        self.assertEqual(self.entfernen(self.handy, bestaetigung=bestaetigung).status_code, 204)

        self.assertFalse(Passkey.objects.filter(benutzer=self.zoe).exists())


class JedeFrageNurEinmal(APITestCase):
    """A question of the app answers once, whatever its signature says.

    It travels signed and held two minutes, as often as it came back (until
    10/2026). A synced passkey counts nothing, so the library does not refuse
    a second answer by its counter. Whoever saw the body of a sign-in and not
    its answer, a proxy say, got a token pair of their own with it.
    """

    def setUp(self):
        self.zoe = CustomUser.objects.create_user(
            username="zoe", email="zoe@example.com", password="x", first_name="Z", last_name="Z"
        )
        self.geraet = Authenticator()
        hinterlegen(self.zoe, self.geraet)
        self.app, _eigenes = geraet(self.zoe)

    def frage(self) -> tuple[str, str]:
        """A new question of signing in: the challenge, and the signed token that travels."""
        schritt = APIClient().post(adresse("passkey_anmelden_optionen_api")).data
        return schritt["options"]["challenge"], schritt["challenge"]

    def antwort(self, challenge: str, token: str, geraet_: Authenticator | None = None) -> dict:
        """The device's answer as a synced passkey gives it: without a count."""
        credential = (geraet_ or self.geraet).anmeldung(challenge, user_handle(self.zoe), counter=0)
        return {"credential": credential, "challenge": token}

    def anmelden(self, koerper: dict):
        return APIClient().post(adresse("passkey_anmelden_api"), koerper, format="json")

    def ausgegeben(self) -> int:
        return OutstandingToken.objects.filter(user=self.zoe).count()

    def pruefe_abgelaufen(self, antwort) -> None:
        self.assertEqual(antwort.status_code, 400, antwort.data)
        self.assertEqual(antwort.data, {"detail": ABGELAUFEN})

    def test_eine_anmeldung_gilt_nur_einmal(self):
        koerper = self.antwort(*self.frage())
        self.assertEqual(self.anmelden(koerper).status_code, 200)
        vorher = self.ausgegeben()

        self.pruefe_abgelaufen(self.anmelden(koerper))
        self.assertEqual(self.ausgegeben(), vorher)

    def test_eine_abgewiesene_antwort_verbraucht_die_frage_ebenso(self):
        """As on the website: whoever answers twice is refused the second time."""
        challenge, token = self.frage()

        antwort = self.anmelden(self.antwort(challenge, token, Authenticator()))

        self.assertEqual(antwort.data, {"detail": "Dieser Passkey ist hier nicht bekannt."})
        self.pruefe_abgelaufen(self.anmelden(self.antwort(challenge, token)))

    def test_eine_frage_die_der_server_nicht_gestellt_hat(self):
        """Signed with the server's key, but never asked: a signature alone opens nothing."""
        erfunden = bytes_to_base64url(b"\x07" * 32)
        vorher = self.ausgegeben()

        antwort = self.anmelden(self.antwort(erfunden, signing.dumps(erfunden, salt=SALZ)))

        self.pruefe_abgelaufen(antwort)
        self.assertEqual(self.ausgegeben(), vorher)

    def test_eine_bestaetigung_nur_einmal(self):
        schritt = self.app.post(adresse("passkey_bestaetigen_optionen_api")).data
        koerper = self.antwort(schritt["options"]["challenge"], schritt["challenge"])
        ziel = adresse("passkey_bestaetigen_api")
        self.assertEqual(self.app.post(ziel, koerper, format="json").status_code, 200)

        self.pruefe_abgelaufen(self.app.post(ziel, koerper, format="json"))

    def test_ein_neuer_schluessel_nur_einmal(self):
        """A second device answering the same question would have been stored as well."""
        schritt = self.app.post(
            adresse("passkey_anlegen_optionen_api"), {"passwort": "x"}, format="json"
        ).data
        ziel = adresse("passkey_anlegen_api")
        for erwartet in (201, 400):
            with self.subTest(erwartet=erwartet):
                credential = Authenticator().registrierung(schritt["options"]["challenge"])
                antwort = self.app.post(
                    ziel,
                    {"credential": credential, "challenge": schritt["challenge"], "name": ""},
                    format="json",
                )
                self.assertEqual(antwort.status_code, erwartet, antwort.data)
        self.assertEqual(Passkey.objects.filter(benutzer=self.zoe).count(), 2)

    def test_die_zeile_haelt_die_pruefsumme_nicht_die_frage(self):
        challenge, _token = self.frage()

        zeile = Passkeyfrage.objects.get()

        self.assertEqual(
            zeile.pruefsumme, hashlib.sha256(base64url_to_bytes(challenge)).hexdigest()
        )

    def test_eine_frage_ueber_ihre_zeit_oeffnet_nichts(self):
        """Its row counts, not only the age the signature names."""
        challenge, token = self.frage()
        Passkeyfrage.objects.update(gefragt_am=timezone.now() - timedelta(seconds=ZWEI_MINUTEN + 1))

        self.pruefe_abgelaufen(self.anmelden(self.antwort(challenge, token)))

    def test_was_niemand_beantwortet_geht_mit_der_naechsten_frage(self):
        """Asking needs no sign-in: the table holds the questions of two minutes and no more."""
        for _ in range(3):
            self.frage()
        Passkeyfrage.objects.update(gefragt_am=timezone.now() - timedelta(seconds=ZWEI_MINUTEN + 1))
        offen = self.frage()[0]
        self.app.post(adresse("passkey_bestaetigen_optionen_api"))

        self.assertEqual(Passkeyfrage.objects.count(), 2)
        self.assertTrue(
            Passkeyfrage.objects.filter(
                pruefsumme=hashlib.sha256(base64url_to_bytes(offen)).hexdigest()
            ).exists()
        )
