"""Signing in with a passkey: registering, signing in, withdrawing, and the decoy.

The browser is replaced by a small software authenticator that does what the
standard asks of it: a key pair, an attestation of the form "none" when
registering, and a signature over authenticator data and client data when
signing in. So the real cryptographic path of the library is checked, not a
mock of it.
"""

import hashlib
import json
import secrets
import struct
import time
from collections.abc import Callable
from datetime import timedelta
from unittest import mock

import cbor2
from axes.models import AccessAttempt
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from django.conf import settings
from django.core import mail
from django.db import connection
from django.test import Client, SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse, reverse_lazy
from django.utils import timezone, translation
from webauthn.helpers import bytes_to_base64url

from accounts.models import Anmeldeversuch, CustomUser, Passkey, Sitzung
from accounts.passkey import vorgeschlagener_name
from django_project.testing import administrator, fuehrungskraft

from . import passkey as passkey_modul
from .passkey import user_handle

RP_ID = "testserver"
ORIGIN = "http://testserver"

#: The id of the iCloud keychain. The name of a new key is suggested from it.
ICLOUD = bytes.fromhex("fbfc3007154e4ecc8c0b6e020557d7bd")

#: The password of the accounts from 'django_project.testing'. A new key asks for it.
PASSWORT = "geheim"

#: How long a question holds, on the website as in the app.
ZWEI_MINUTEN = 120

#: What a step of an account without a password hears, as the person reads it.
BESTAETIGUNG_FEHLT = "Bitte bestätige zuerst mit einem deiner Passkeys."
BESTAETIGUNG_ABGELAUFEN = (
    "Die Bestätigung ist abgelaufen. Bitte bestätige noch einmal mit einem deiner Passkeys."
)
KEIN_PASSKEY = (
    "Dein Konto hat keinen Passkey, mit dem du bestätigen könntest. "
    "Ein Administrator kann dir ein Passwort setzen."
)
NICHT_BESTAETIGT = "Die Bestätigung mit diesem Passkey ist fehlgeschlagen."


class Authenticator:
    """Enough of a device to register a key and to sign with it."""

    def __init__(self, aaguid: bytes = b"\x00" * 16):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = secrets.token_bytes(32)
        self.aaguid = aaguid
        self.counter = 0

    @property
    def kennung(self) -> str:
        return bytes_to_base64url(self.credential_id)

    def cose_key(self) -> bytes:
        zahlen = self.key.public_key().public_numbers()
        return cbor2.dumps(
            {1: 2, 3: -7, -1: 1, -2: zahlen.x.to_bytes(32, "big"), -3: zahlen.y.to_bytes(32, "big")}
        )

    @staticmethod
    def client_data(art: str, challenge: str, origin: str = ORIGIN) -> bytes:
        return json.dumps({"type": art, "challenge": challenge, "origin": origin}).encode()

    def registrierung(self, challenge: str, flags: int = 0x45, origin: str = ORIGIN) -> dict:
        """The attestation: 'UP | UV | AT' is 0x45, the credential data follow the flags."""
        auth_data = (
            hashlib.sha256(RP_ID.encode()).digest()
            + bytes([flags])
            + struct.pack(">I", self.counter)
            + self.aaguid
            + struct.pack(">H", len(self.credential_id))
            + self.credential_id
            + self.cose_key()
        )
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": self.kennung,
            "rawId": self.kennung,
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(
                    self.client_data("webauthn.create", challenge, origin)
                ),
                "attestationObject": bytes_to_base64url(attestation),
                "transports": ["internal"],
            },
        }

    def anmeldung(
        self,
        challenge: str,
        handle: bytes,
        flags: int = 0x05,
        origin: str = ORIGIN,
        counter: int | None = None,
    ) -> dict:
        """The assertion: 'UP | UV' is 0x05, the counter climbs with every use."""
        self.counter = self.counter + 1 if counter is None else counter
        client_data = self.client_data("webauthn.get", challenge, origin)
        auth_data = (
            hashlib.sha256(RP_ID.encode()).digest()
            + bytes([flags])
            + struct.pack(">I", self.counter)
        )
        signatur = self.key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": self.kennung,
            "rawId": self.kennung,
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "authenticatorData": bytes_to_base64url(auth_data),
                "signature": bytes_to_base64url(signatur),
                "userHandle": bytes_to_base64url(handle),
            },
        }


def hinterlegen(konto: CustomUser, geraet: Authenticator, name: str = "Handy") -> Passkey:
    """A key the device made earlier, stored as registering stores it.

    Past the two steps of registering, which an account without a password
    can only take with a key it already has.
    """
    return Passkey.objects.create(
        benutzer=konto,
        kennung=geraet.kennung,
        oeffentlicher_schluessel=geraet.cose_key(),
        name=name,
    )


def bestaetigt(browser: Client, konto: CustomUser) -> None:
    """A confirmation with one of the account's keys just now, as 'PasskeyBestaetigenView' notes it.

    For the steps of an account without a password that come after it. The
    ceremony itself is tested in 'BestaetigenOhnePasswort'.
    """
    sitzung = browser.session
    sitzung[passkey_modul.BESTAETIGT] = {"konto": konto.get_username(), "gefragt": time.time()}
    sitzung.save()


def spaeter(sekunden: float):
    """The clock this many seconds after now, for the period of a question or a confirmation."""
    return mock.patch("time.time", return_value=time.time() + sekunden)


class PasskeyTests(TestCase):
    """Shared: an account, a device, and the two steps of each flow as methods."""

    @classmethod
    def setUpTestData(cls):
        cls.leitung = fuehrungskraft(email="leitung@example.com", first_name="L", last_name="M")

    def setUp(self):
        self.geraet = Authenticator()

    def json_post(self, name, daten, **kwargs):
        return self.client.post(
            reverse(name, **kwargs), json.dumps(daten), content_type="application/json"
        )

    def anlegeoptionen(self, passwort=PASSWORT):
        """Step one of registering, with the account's password."""
        return self.json_post("passkey_anlegen_optionen", {"passwort": passwort})

    def registrieren(self, name="", geraet=None, **abweichend) -> tuple:
        """Both steps of registering, signed in as the manager."""
        geraet = geraet or self.geraet
        optionen = self.anlegeoptionen().json()
        credential = geraet.registrierung(optionen["challenge"], **abweichend)
        antwort = self.json_post("passkey_anlegen", {"credential": credential, "name": name})
        return optionen, antwort

    def anmelden(self, konto, geraet=None, weiter="", **abweichend):
        """Both steps of signing in, from a signed-out browser."""
        geraet = geraet or self.geraet
        optionen = self.client.post(reverse("passkey_anmelden_optionen")).json()
        credential = geraet.anmeldung(optionen["challenge"], user_handle(konto), **abweichend)
        return self.json_post("passkey_anmelden", {"credential": credential, "next": weiter})

    def angemeldet_als(self, konto) -> bool:
        return self.client.session.get("_auth_user_id") == str(konto.pk)


class FremdeTypen(PasskeyTests):
    """What a script sends instead of the browser: a list, a text, a response that is none.

    Until 10/2026 each of them ended in a server error with a traceback, on
    two endpoints that need no sign-in.
    """

    FAELLE = ([1], "x", 7, {"id": "a", "response": "x"}, {"id": ["a"], "response": []})

    def test_anmelden_weist_ab_statt_zu_scheitern(self):
        for credential in self.FAELLE:
            with self.subTest(credential=credential):
                # Every attempt uses up the challenge, so each case asks anew.
                self.client.post(reverse("passkey_anmelden_optionen"))
                antwort = self.json_post("passkey_anmelden", {"credential": credential})
                self.assertEqual(antwort.status_code, 400, antwort.content)
                self.assertIn("keinen gültigen Passkey", antwort.json()["fehler"])

    def test_registrieren_weist_ab_statt_zu_scheitern(self):
        self.client.force_login(self.leitung)
        for credential in self.FAELLE:
            with self.subTest(credential=credential):
                self.anlegeoptionen()
                antwort = self.json_post("passkey_anlegen", {"credential": credential, "name": ""})
                self.assertEqual(antwort.status_code, 400, antwort.content)
                self.assertIn("keinen gültigen Passkey", antwort.json()["fehler"])
        self.assertFalse(Passkey.objects.exists())

    def test_die_attrappe_notiert_den_versuch_ohne_kennung(self):
        for credential in self.FAELLE:
            with self.subTest(credential=credential):
                antwort = self.client.post(
                    "/admin/passkey/anmelden/",
                    json.dumps({"credential": credential}),
                    content_type="application/json",
                )
                self.assertLess(antwort.status_code, 500, antwort.content)
        self.assertEqual(Anmeldeversuch.objects.count(), len(self.FAELLE))


class NulZeichen(PasskeyTests):
    """A NUL in the key's id or name: PostgreSQL refuses it in a text column.

    Looking up such an id ended in a 'DataError', a server error at the real
    door and at the decoy, with a question that anyone gets (until 10/2026).
    Now the id is refused like any answer that is no browser's. SQLite
    compares and stores the NUL: there such an id was merely not known, and
    the name kept it.
    """

    def setUp(self):
        super().setUp()
        self.client.raise_request_exception = False

    def mit_nul(self, challenge: str) -> dict:
        credential = self.geraet.anmeldung(challenge, user_handle(self.leitung))
        return credential | {"id": "AA\x00AA", "rawId": "AA\x00AA"}

    def test_die_echte_tuer_weist_ab(self):
        challenge = self.client.post(reverse("passkey_anmelden_optionen")).json()["challenge"]

        antwort = self.json_post("passkey_anmelden", {"credential": self.mit_nul(challenge)})

        self.assertEqual(antwort.status_code, 400, antwort.content)
        self.assertEqual(
            antwort.json(), {"fehler": "Der Browser hat keinen gültigen Passkey geliefert."}
        )

    def test_die_attrappe_weist_ebenso_ab_und_zeichnet_auf(self):
        challenge = self.client.post("/admin/login/passkey/options/").json()["challenge"]

        antwort = self.client.post(
            "/admin/login/passkey/anmelden/",
            json.dumps({"credential": self.mit_nul(challenge)}),
            content_type="application/json",
        )

        self.assertEqual(antwort.status_code, 400, antwort.content)
        self.assertEqual(
            antwort.json(), {"fehler": "Der Browser hat keinen gültigen Passkey geliefert."}
        )
        self.assertEqual(Anmeldeversuch.objects.get().username, "passkey:AA\ufffdAA")

    def test_der_name_eines_neuen_schluessels_verliert_das_nul(self):
        self.client.force_login(self.leitung)

        _optionen, antwort = self.registrieren(name="Lap\x00top")

        self.assertEqual(antwort.status_code, 200, antwort.content)
        self.assertEqual(Passkey.objects.get().name, "Laptop")


class KennungAusDemAlphabet(PasskeyTests):
    """A credential id is base64url, WebAuthn's alphabet, or the answer is no browser's.

    A lone surrogate in it ('\\ud800') reached the database, and no backend
    can encode one: a server error at the real door and at the decoy, for a
    question anyone gets (until 10/2026). Anything outside the alphabet is
    refused now before the first query on the keys, in the words of an
    answer that is none.
    """

    #: Lone surrogates, characters outside the alphabet, padding, and nothing at all.
    FAELLE = ("\ud800", "AA\udfffAA", "AA+/AA", "AA AA", "AA==", "Ärger", "")

    def setUp(self):
        super().setUp()
        self.client.raise_request_exception = False

    def mit_kennung(self, challenge: str, kennung: str) -> dict:
        credential = self.geraet.anmeldung(challenge, user_handle(self.leitung))
        return credential | {"id": kennung, "rawId": kennung}

    def pruefe_abgewiesen(self, antwort, abfragen) -> None:
        self.assertEqual(antwort.status_code, 400, antwort.content)
        self.assertEqual(antwort.json(), {"fehler": str(passkey_modul.UNGUELTIG)})
        self.assertEqual([a["sql"] for a in abfragen if "accounts_passkey" in a["sql"]], [])

    def test_die_echte_tuer(self):
        for kennung in self.FAELLE:
            with self.subTest(kennung=repr(kennung)):
                challenge = self.client.post(reverse("passkey_anmelden_optionen")).json()
                with CaptureQueriesContext(connection) as abfragen:
                    antwort = self.json_post(
                        "passkey_anmelden",
                        {"credential": self.mit_kennung(challenge["challenge"], kennung)},
                    )
                self.pruefe_abgewiesen(antwort, abfragen)
        self.assertFalse(self.angemeldet_als(self.leitung))

    def test_die_attrappe_sagt_dasselbe_und_zeichnet_auf(self):
        """A body with a lone surrogate is no text at all: recorded without the id."""
        for kennung in self.FAELLE:
            with self.subTest(kennung=repr(kennung)):
                challenge = self.client.post("/admin/login/passkey/options/").json()["challenge"]
                with CaptureQueriesContext(connection) as abfragen:
                    antwort = self.client.post(
                        "/admin/login/passkey/anmelden/",
                        json.dumps({"credential": self.mit_kennung(challenge, kennung)}),
                        content_type="application/json",
                    )
                self.pruefe_abgewiesen(antwort, abfragen)
        self.assertEqual(
            list(Anmeldeversuch.objects.order_by("pk").values_list("username", flat=True)),
            ["passkey:", "passkey:", "passkey:AA+/AA", "passkey:AA AA"]
            + ["passkey:AA==", "passkey:Ärger", "passkey:"],
        )

    def test_ein_passwort_mit_einzelnem_surrogat_ist_keines(self):
        """Hashing it raised before 'axes' saw it, a server error for a signed-in session."""
        self.client.force_login(self.leitung)

        antwort = self.json_post("passkey_anlegen_optionen", {"passwort": "geh\ud800eim"})

        self.assertEqual(antwort.status_code, 400, antwort.content)
        self.assertEqual(antwort.json(), {"fehler": "Bitte gib dein Passwort ein."})
        self.assertNotIn("passkey_anlegen", self.client.session)


class Registrieren(PasskeyTests):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.leitung)

    def test_die_optionen_nennen_konto_und_adresse(self):
        optionen = self.anlegeoptionen().json()
        self.assertEqual(optionen["rp"], {"id": RP_ID, "name": "CrossTally"})
        self.assertEqual(optionen["user"]["name"], "leitung")
        self.assertEqual(optionen["user"]["id"], bytes_to_base64url(user_handle(self.leitung)))
        self.assertEqual(optionen["authenticatorSelection"]["residentKey"], "required")
        self.assertEqual(optionen["authenticatorSelection"]["userVerification"], "required")
        self.assertEqual(optionen["attestation"], "none")
        # Under a key of its own: the question of signing in is open to anyone.
        self.assertIn("passkey_anlegen", self.client.session)
        self.assertNotIn("passkey_challenge", self.client.session)

    def test_ein_echter_schluessel_wird_gespeichert(self):
        _, antwort = self.registrieren(name="Laptop")
        self.assertEqual(antwort.status_code, 200, antwort.content)
        passkey = Passkey.objects.get(benutzer=self.leitung)
        self.assertEqual(passkey.kennung, self.geraet.kennung)
        self.assertEqual(bytes(passkey.oeffentlicher_schluessel), self.geraet.cose_key())
        self.assertEqual((passkey.name, passkey.signaturzaehler), ("Laptop", 0))
        self.assertIsNone(passkey.zuletzt_benutzt)
        # The challenge is used up with it.
        self.assertNotIn("passkey_anlegen", self.client.session)

    def test_ohne_namen_schlaegt_der_anbieter_einen_vor(self):
        self.registrieren(geraet=Authenticator(aaguid=ICLOUD))
        self.assertEqual(Passkey.objects.get().name, "iCloud-Schlüsselbund")
        Passkey.objects.all().delete()
        self.registrieren()
        self.assertTrue(Passkey.objects.get().name.startswith("Passkey vom "))

    def test_der_vorschlag_spricht_die_sprache_des_kontos(self):
        """German for every account until 10/2026."""
        CustomUser.objects.filter(pk=self.leitung.pk).update(sprache="en")
        self.registrieren(geraet=Authenticator(aaguid=ICLOUD))
        self.assertEqual(Passkey.objects.get().name, "iCloud Keychain")

    def test_die_meldung_kommt_mit_der_naechsten_seite(self):
        self.registrieren(name="Handy")
        seite = self.client.get(reverse("passkey_list"))
        self.assertContains(seite, "Passkey „Handy“ ist angelegt.")

    def test_ohne_pruefung_der_person_wird_abgelehnt(self):
        """'UP | AT' alone (0x41): the device did not verify the person."""
        _, antwort = self.registrieren(flags=0x41)
        self.assertEqual(antwort.status_code, 400)
        self.assertIn("keinen gültigen Passkey", antwort.json()["fehler"])
        self.assertFalse(Passkey.objects.exists())

    def test_eine_fremde_herkunft_wird_abgelehnt(self):
        _, antwort = self.registrieren(origin="https://example.org")
        self.assertEqual(antwort.status_code, 400)
        self.assertFalse(Passkey.objects.exists())

    def test_ohne_offene_frage_wird_abgelehnt(self):
        credential = self.geraet.registrierung(bytes_to_base64url(b"x" * 32))
        antwort = self.json_post("passkey_anlegen", {"credential": credential})
        self.assertEqual(antwort.status_code, 400)
        self.assertIn("abgelaufen", antwort.json()["fehler"])

    def test_dieselbe_kennung_nur_einmal(self):
        self.registrieren(name="Laptop")
        _, antwort = self.registrieren(name="noch einmal")
        self.assertEqual(antwort.status_code, 400)
        self.assertEqual(Passkey.objects.count(), 1)

    def test_vorhandene_schluessel_werden_ausgeschlossen(self):
        self.registrieren()
        optionen = self.anlegeoptionen().json()
        self.assertEqual([e["id"] for e in optionen["excludeCredentials"]], [self.geraet.kennung])

    def test_unsinn_statt_json_wird_abgelehnt(self):
        self.anlegeoptionen()
        antwort = self.client.post(
            reverse("passkey_anlegen"), "kein json", content_type="application/json"
        )
        self.assertEqual(antwort.status_code, 400)

    def test_nur_angemeldet(self):
        self.client.logout()
        for name in ("passkey_anlegen_optionen", "passkey_anlegen"):
            with self.subTest(name):
                antwort = self.client.post(reverse(name))
                self.assertEqual(antwort.status_code, 302)
                self.assertIn("/accounts/login/", antwort["Location"])


class MitPasswort(PasskeyTests):
    """A new key asks for the account's password, before the device's dialog.

    A session does not say who sits at the device. Until 10/2026 it sufficed,
    and whoever found a browser open added a key of their own, which kept
    working after the owner changed the password.
    """

    def setUp(self):
        super().setUp()
        self.client.force_login(self.leitung)

    def test_das_richtige_passwort_bringt_die_frage(self):
        antwort = self.anlegeoptionen()

        self.assertEqual(antwort.status_code, 200, antwort.content)
        self.assertIn("challenge", antwort.json())
        self.assertIn("passkey_anlegen", self.client.session)

    def test_ein_falsches_passwort_bringt_keine_frage(self):
        antwort = self.anlegeoptionen("daneben")

        self.assertEqual(antwort.status_code, 400)
        self.assertEqual(antwort.json(), {"fehler": "Das Passwort stimmt nicht."})
        self.assertNotIn("passkey_anlegen", self.client.session)

    def test_ohne_passwort_keine_frage(self):
        for daten in ({}, {"passwort": ""}, {"passwort": [PASSWORT]}):
            with self.subTest(daten=daten):
                antwort = self.json_post("passkey_anlegen_optionen", daten)

                self.assertEqual(antwort.status_code, 400)
                self.assertEqual(antwort.json(), {"fehler": "Bitte gib dein Passwort ein."})
        self.assertNotIn("passkey_anlegen", self.client.session)
        # Nothing typed is no guess: it does not count towards the lock.
        self.assertFalse(AccessAttempt.objects.exists())

    def test_die_frage_der_anmeldung_legt_keinen_schluessel_an(self):
        """It is open to anyone. Under the shared key it served for a new key too."""
        optionen = self.client.post(reverse("passkey_anmelden_optionen")).json()
        credential = self.geraet.registrierung(optionen["challenge"])

        antwort = self.json_post("passkey_anlegen", {"credential": credential, "name": ""})

        self.assertEqual(antwort.status_code, 400)
        self.assertIn("abgelaufen", antwort.json()["fehler"])
        self.assertFalse(Passkey.objects.exists())

    def test_ein_konto_ohne_passwort_wird_nicht_nach_einem_gefragt(self):
        """An invited account switched on by hand, say: there is nothing to type.

        Until 10/2026 it was asked nothing at all. Now it confirms with a key
        of its own ('BestaetigenOhnePasswort'), and without one it needs a
        password from the administration first.
        """
        self.leitung.set_unusable_password()
        self.leitung.save()
        self.client.force_login(self.leitung)
        seite = self.client.get(reverse("passkey_list"))
        self.assertNotContains(seite, "data-passkey-passwort")
        self.assertNotContains(seite, "data-passkey-anlegen")

        antwort = self.json_post("passkey_anlegen_optionen", {})

        self.assertEqual(antwort.status_code, 400)
        self.assertEqual(antwort.json(), {"fehler": KEIN_PASSKEY})
        self.assertNotIn("passkey_anlegen", self.client.session)
        self.assertFalse(Passkey.objects.exists())

    def test_falsche_passwoerter_sperren_wie_beim_anmelden(self):
        """The fifth wrong one locks, and the lock holds against the right one too."""
        for _ in range(5):
            antwort = self.anlegeoptionen("daneben")
        self.assertContains(antwort, "Zu viele Fehlversuche", status_code=429)
        self.assertEqual(AccessAttempt.objects.get().failures_since_start, 5)

        self.assertEqual(self.anlegeoptionen().status_code, 429)
        self.assertNotIn("passkey_anlegen", self.client.session)
        # It is the bolt of the sign-in: that is locked from here as well.
        self.client.logout()
        antwort = self.client.post(reverse("login"), {"username": "leitung", "password": PASSWORT})
        self.assertEqual(antwort.status_code, 429)

    def test_die_seite_fragt_nach_dem_passwort(self):
        html = self.client.get(reverse("passkey_list")).content.decode()
        for stueck in (
            'type="password"',
            'autocomplete="current-password"',
            "data-passkey-passwort",
            "Dein Passwort",
            "damit niemand einen Passkey anlegt",
            # What the script says after the fifth wrong one.
            'data-gesperrt="Zu viele Fehlversuche.',
        ):
            with self.subTest(stueck=stueck):
                self.assertIn(stueck, html)


class BestaetigenOhnePasswort(PasskeyTests):
    """An account without a password confirms with one of its own keys before a new key.

    It has nothing to type, and until 10/2026 it was asked nothing: whoever
    found its session open added a key of their own and kept the account.
    Now a fresh signature of one of its keys stands in for the password, with
    the person verified, and holds two minutes.
    """

    def setUp(self):
        super().setUp()
        self.leitung.set_unusable_password()
        self.leitung.save()
        self.passkey = hinterlegen(self.leitung, self.geraet)
        self.client.force_login(self.leitung)

    def bestaetigungsoptionen(self):
        return self.json_post("passkey_bestaetigen_optionen", {})

    def bestaetigen(self, geraet=None, handle=None, **abweichend):
        """Both steps of the confirmation, with this device's key and its account's handle."""
        frage = self.bestaetigungsoptionen().json()["challenge"]
        credential = (geraet or self.geraet).anmeldung(
            frage, handle or user_handle(self.leitung), **abweichend
        )
        return self.json_post("passkey_bestaetigen", {"credential": credential})

    def neuer_schluessel(self):
        """Both steps of registering a second device, without a password."""
        optionen = self.json_post("passkey_anlegen_optionen", {})
        self.assertEqual(optionen.status_code, 200, optionen.content)
        credential = Authenticator().registrierung(optionen.json()["challenge"])
        return self.json_post("passkey_anlegen", {"credential": credential, "name": "Laptop"})

    def pruefe_nicht_bestaetigt(self, antwort, fehler: str) -> None:
        self.assertEqual(antwort.status_code, 400, antwort.content)
        self.assertEqual(antwort.json(), {"fehler": fehler})
        self.assertNotIn("passkey_bestaetigt", self.client.session)
        self.assertEqual(self.json_post("passkey_anlegen_optionen", {}).status_code, 400)

    def test_eine_offene_sitzung_legt_keinen_schluessel_an(self):
        """The gap: until 10/2026 the options came without any question."""
        antwort = self.json_post("passkey_anlegen_optionen", {})

        self.assertEqual(antwort.status_code, 400)
        self.assertEqual(antwort.json(), {"fehler": BESTAETIGUNG_FEHLT})
        self.assertNotIn("passkey_anlegen", self.client.session)
        self.assertEqual(Passkey.objects.count(), 1)

    def test_die_frage_nennt_nur_die_eigenen_schluessel(self):
        andere = administrator(email="chef@example.com", first_name="C", last_name="D")
        hinterlegen(andere, Authenticator())
        zweiter = hinterlegen(self.leitung, Authenticator(), name="Laptop")

        optionen = self.bestaetigungsoptionen().json()

        self.assertEqual(optionen["rpId"], RP_ID)
        self.assertEqual(optionen["userVerification"], "required")
        self.assertEqual(
            sorted(eintrag["id"] for eintrag in optionen["allowCredentials"]),
            sorted([self.passkey.kennung, zweiter.kennung]),
        )
        # Under a key of its own, neither the one of signing in nor of registering.
        self.assertIn("passkey_bestaetigen", self.client.session)
        self.assertNotIn("passkey_challenge", self.client.session)
        self.assertNotIn("passkey_anlegen", self.client.session)

    def test_bestaetigt_kommt_der_neue_schluessel(self):
        antwort = self.bestaetigen()

        self.assertEqual(antwort.status_code, 200, antwort.content)
        self.assertEqual(antwort.json(), {"ok": True})
        # A use of the key like a sign-in: the counter climbs.
        self.passkey.refresh_from_db()
        self.assertEqual(self.passkey.signaturzaehler, 1)
        self.assertIsNotNone(self.passkey.zuletzt_benutzt)
        antwort = self.neuer_schluessel()
        self.assertEqual(antwort.status_code, 200, antwort.content)
        self.assertTrue(Passkey.objects.filter(benutzer=self.leitung, name="Laptop").exists())

    def test_die_bestaetigung_gilt_zwei_minuten(self):
        """For every step in that time: a dialog of the device broken off may be tried again."""
        self.bestaetigen()

        with spaeter(ZWEI_MINUTEN - 1):
            self.assertEqual(self.json_post("passkey_anlegen_optionen", {}).status_code, 200)
            self.assertEqual(self.json_post("passkey_anlegen_optionen", {}).status_code, 200)
        with spaeter(ZWEI_MINUTEN + 1):
            antwort = self.json_post("passkey_anlegen_optionen", {})

        self.assertEqual(antwort.status_code, 400)
        self.assertEqual(antwort.json(), {"fehler": BESTAETIGUNG_ABGELAUFEN})

    def test_die_frage_gilt_zwei_minuten_und_nur_einmal(self):
        frage = self.bestaetigungsoptionen().json()["challenge"]
        credential = self.geraet.anmeldung(frage, user_handle(self.leitung))
        with spaeter(ZWEI_MINUTEN + 1):
            spaet = self.json_post("passkey_bestaetigen", {"credential": credential})
        self.pruefe_nicht_bestaetigt(spaet, str(passkey_modul.ABGELAUFEN))

        frage = self.bestaetigungsoptionen().json()["challenge"]
        credential = self.geraet.anmeldung(frage, user_handle(self.leitung))
        self.assertEqual(
            self.json_post("passkey_bestaetigen", {"credential": credential}).status_code, 200
        )
        noch_einmal = self.json_post("passkey_bestaetigen", {"credential": credential})
        self.assertEqual(noch_einmal.status_code, 400)
        self.assertIn("abgelaufen", noch_einmal.json()["fehler"])

    def test_die_frage_der_anmeldung_bestaetigt_nichts(self):
        """It is open to anyone, without an account."""
        frage = self.client.post(reverse("passkey_anmelden_optionen")).json()["challenge"]
        credential = self.geraet.anmeldung(frage, user_handle(self.leitung))

        antwort = self.json_post("passkey_bestaetigen", {"credential": credential})

        self.pruefe_nicht_bestaetigt(antwort, str(passkey_modul.ABGELAUFEN))

    def test_die_frage_der_bestaetigung_meldet_nicht_an_und_legt_nichts_an(self):
        frage = self.bestaetigungsoptionen().json()["challenge"]
        anmeldung = self.geraet.anmeldung(frage, user_handle(self.leitung))
        registrierung = Authenticator().registrierung(frage)

        angemeldet = self.json_post("passkey_anmelden", {"credential": anmeldung})
        angelegt = self.json_post("passkey_anlegen", {"credential": registrierung, "name": ""})

        for antwort in (angemeldet, angelegt):
            self.assertEqual(antwort.status_code, 400, antwort.content)
            self.assertIn("abgelaufen", antwort.json()["fehler"])
        self.assertEqual(Passkey.objects.count(), 1)

    def test_ein_schluessel_eines_anderen_kontos_bestaetigt_nicht(self):
        """Whoever holds a key of their own account confirms nothing on this one."""
        andere = administrator(email="chef@example.com", first_name="C", last_name="D")
        fremd = Authenticator()
        fremder = hinterlegen(andere, fremd)

        antwort = self.bestaetigen(geraet=fremd, handle=user_handle(andere))

        self.pruefe_nicht_bestaetigt(antwort, str(passkey_modul.ANDERES_KONTO))
        # Refused before the signature: the other key was not used.
        fremder.refresh_from_db()
        self.assertEqual((fremder.signaturzaehler, fremder.zuletzt_benutzt), (0, None))

    def test_ein_fremder_handle_bestaetigt_nicht(self):
        """The own key, but the browser names another account."""
        andere = administrator(email="chef@example.com", first_name="C", last_name="D")
        antwort = self.bestaetigen(handle=user_handle(andere))
        self.pruefe_nicht_bestaetigt(antwort, str(passkey_modul.ANDERES_KONTO))

    def test_ohne_handle_genuegt_der_eigene_schluessel(self):
        """The question names the account's keys, so the device may leave the handle out."""
        frage = self.bestaetigungsoptionen().json()["challenge"]
        credential = self.geraet.anmeldung(frage, b"")
        credential["response"]["userHandle"] = None

        antwort = self.json_post("passkey_bestaetigen", {"credential": credential})

        self.assertEqual(antwort.status_code, 200, antwort.content)
        self.assertEqual(self.neuer_schluessel().status_code, 200)

    def test_ohne_pruefung_der_person_bestaetigt_nichts(self):
        """'UP' alone (0x01): the device did not verify the person."""
        self.pruefe_nicht_bestaetigt(self.bestaetigen(flags=0x01), NICHT_BESTAETIGT)

    def test_eine_fremde_herkunft_bestaetigt_nichts(self):
        antwort = self.bestaetigen(origin="https://example.org")
        self.pruefe_nicht_bestaetigt(antwort, NICHT_BESTAETIGT)

    def test_eine_falsche_signatur_bestaetigt_nichts(self):
        """Same id, other key pair."""
        falsch = Authenticator()
        falsch.credential_id = self.geraet.credential_id
        self.pruefe_nicht_bestaetigt(self.bestaetigen(geraet=falsch), NICHT_BESTAETIGT)

    def test_ein_geklonter_schluessel_faellt_am_zaehler_auf(self):
        self.assertEqual(self.bestaetigen().status_code, 200)
        sitzung = self.client.session
        del sitzung["passkey_bestaetigt"]
        sitzung.save()

        self.pruefe_nicht_bestaetigt(self.bestaetigen(counter=1), NICHT_BESTAETIGT)

    def test_ein_unbekannter_schluessel_bestaetigt_nichts(self):
        antwort = self.bestaetigen(geraet=Authenticator())
        self.pruefe_nicht_bestaetigt(antwort, str(passkey_modul.UNBEKANNTER_SCHLUESSEL))

    def test_unsinn_statt_einer_antwort_bestaetigt_nichts(self):
        for credential in FremdeTypen.FAELLE:
            with self.subTest(credential=credential):
                self.bestaetigungsoptionen()
                antwort = self.json_post("passkey_bestaetigen", {"credential": credential})
                self.pruefe_nicht_bestaetigt(antwort, str(passkey_modul.UNGUELTIG))

    def test_ein_eben_entfernter_schluessel_bestaetigt_nicht(self):
        """Removed on another device between the signature and the note."""
        self.client.raise_request_exception = False
        echt = passkey_modul.verify_authentication_response

        def entfernt(**kwargs):
            ergebnis = echt(**kwargs)
            Passkey.objects.filter(pk=self.passkey.pk).delete()
            return ergebnis

        with mock.patch.object(
            passkey_modul, "verify_authentication_response", side_effect=entfernt
        ):
            antwort = self.bestaetigen()

        self.assertEqual(antwort.status_code, 400)
        self.assertEqual(antwort.json(), {"fehler": str(passkey_modul.UNBEKANNTER_SCHLUESSEL)})
        self.assertNotIn("passkey_bestaetigt", self.client.session)

    def test_die_bestaetigung_gilt_nur_fuer_ihr_konto(self):
        """Signing in as another account empties the session. Here it is carried over by hand."""
        self.bestaetigen()
        notiz = self.client.session["passkey_bestaetigt"]
        zoe = CustomUser.objects.create_user(
            username="zoe", email="zoe@example.com", password=None, first_name="Z", last_name="Z"
        )
        hinterlegen(zoe, Authenticator())
        self.client.force_login(zoe)
        sitzung = self.client.session
        sitzung["passkey_bestaetigt"] = notiz
        sitzung.save()

        antwort = self.json_post("passkey_anlegen_optionen", {})

        self.assertEqual(antwort.status_code, 400)
        self.assertEqual(antwort.json(), {"fehler": BESTAETIGUNG_ABGELAUFEN})

    def test_ohne_passkey_gibt_es_nichts_zu_bestaetigen(self):
        """Removed the last one, say, and still signed in: only a password helps now."""
        Passkey.objects.all().delete()

        for name in ("passkey_bestaetigen_optionen", "passkey_anlegen_optionen"):
            with self.subTest(name=name):
                antwort = self.json_post(name, {})
                self.assertEqual(antwort.status_code, 400)
                self.assertEqual(antwort.json(), {"fehler": KEIN_PASSKEY})
        self.assertNotIn("passkey_bestaetigen", self.client.session)
        seite = self.client.get(reverse("passkey_list"))
        self.assertContains(seite, KEIN_PASSKEY)
        self.assertNotContains(seite, "data-passkey-anlegen")
        self.assertNotContains(seite, "Mit dem Passwort geht es weiter")

    def test_nur_angemeldet(self):
        self.client.logout()
        for name in ("passkey_bestaetigen_optionen", "passkey_bestaetigen"):
            with self.subTest(name=name):
                antwort = self.client.post(reverse(name))
                self.assertEqual(antwort.status_code, 302)
                self.assertIn("/accounts/login/", antwort["Location"])
                # The script hears 401, not the sign-in page ('NurAngemeldet').
                antwort = self.client.post(reverse(name), headers={"X-Requested-With": "fetch"})
                self.assertEqual(antwort.status_code, 401)

    def test_die_seite_bietet_die_bestaetigung_an(self):
        html = self.client.get(reverse("passkey_list")).content.decode()
        for stueck in (
            "data-passkey-bestaetigen",
            f'data-passkey-optionen="{reverse("passkey_bestaetigen_optionen")}"',
            f'data-passkey-ziel="{reverse("passkey_bestaetigen")}"',
            "Mit Passkey bestätigen",
            "Dein Konto hat kein Passwort.",
            "data-passkey-bestaetigt",
            "Bestätigt. Die Bestätigung gilt zwei Minuten.",
            # What the script says when the session ran out meanwhile.
            'data-nicht-bestaetigt="Nicht bestätigt, denn die Anmeldung ist abgelaufen.',
            "data-passkey-anlegen",
        ):
            with self.subTest(stueck=stueck):
                self.assertIn(stueck, html)
        self.assertNotIn("data-passkey-passwort", html)

    def test_die_adressen(self):
        self.assertEqual(
            reverse("passkey_bestaetigen_optionen"), "/accounts/passkeys/bestaetigen/options/"
        )
        self.assertEqual(reverse("passkey_bestaetigen"), "/accounts/passkeys/bestaetigen/")

    def test_seite_und_hilfe_versprechen_was_gilt(self):
        """Both say two minutes: the period and the texts belong together."""
        self.assertEqual(passkey_modul.GUELTIG_SEKUNDEN, ZWEI_MINUTEN)
        for datei in (
            "components/passkey_confirm.html",
            "help/de/passkey_list.html",
            "help/de/email_change.html",
        ):
            with self.subTest(datei=datei):
                text = (settings.BASE_DIR / "templates" / datei).read_text(encoding="utf-8")
                self.assertIn("zwei Minuten", " ".join(text.split()))

    def test_der_letzte_passkey_warnt_vor_dem_entfernen(self):
        """Without a password it is the last way in, and no new one comes without it."""
        warnung = "Das ist dein letzter Passkey, und dein Konto hat kein Passwort."
        self.assertContains(self.client.get(reverse("passkey_list")), warnung)
        hinterlegen(self.leitung, Authenticator(), name="Laptop")
        self.assertNotContains(self.client.get(reverse("passkey_list")), warnung)


class BestaetigenMitPasswort(PasskeyTests):
    """An account with a password keeps typing it. A confirmation does not stand in for it."""

    def setUp(self):
        super().setUp()
        hinterlegen(self.leitung, self.geraet)
        self.client.force_login(self.leitung)

    def test_eine_bestaetigung_ersetzt_das_passwort_nicht(self):
        frage = self.json_post("passkey_bestaetigen_optionen", {}).json()["challenge"]
        credential = self.geraet.anmeldung(frage, user_handle(self.leitung))
        self.assertEqual(
            self.json_post("passkey_bestaetigen", {"credential": credential}).status_code, 200
        )

        antwort = self.json_post("passkey_anlegen_optionen", {})

        self.assertEqual(antwort.status_code, 400)
        self.assertEqual(antwort.json(), {"fehler": "Bitte gib dein Passwort ein."})
        self.assertEqual(self.anlegeoptionen().status_code, 200)

    def test_die_frage_zum_anlegen_bestaetigt_nichts(self):
        frage = self.anlegeoptionen().json()["challenge"]
        credential = self.geraet.anmeldung(frage, user_handle(self.leitung))

        antwort = self.json_post("passkey_bestaetigen", {"credential": credential})

        self.assertEqual(antwort.status_code, 400)
        self.assertIn("abgelaufen", antwort.json()["fehler"])
        self.assertNotIn("passkey_bestaetigt", self.client.session)

    def test_die_seite_fragt_nach_dem_passwort_und_nicht_nach_dem_passkey(self):
        html = self.client.get(reverse("passkey_list")).content.decode()
        self.assertIn("data-passkey-passwort", html)
        self.assertNotIn("data-passkey-bestaetigen", html)
        self.assertNotIn("Das ist dein letzter Passkey, und dein Konto hat kein Passwort.", html)


class EntfernenMitPasswort(PasskeyTests):
    """Removing a key asks what adding one asks: the account's password.

    Removing signs the account out everywhere but here. Until 10/2026 the open
    session alone sufficed. Whoever found it removed the owner's keys and kept
    the session, and through the app's pair from the session a sign-in that
    renews itself.
    """

    def setUp(self):
        super().setUp()
        self.passkey = hinterlegen(self.leitung, self.geraet)
        self.client.force_login(self.leitung)

    def entfernen(self, **daten):
        return self.client.post(reverse("passkey_delete", args=[self.passkey.pk]), daten)

    def pruefe_abgelehnt(self, antwort, absage: str) -> None:
        """Back to the list, the key still there, and the sentence at its field."""
        self.assertRedirects(antwort, reverse("passkey_list"), fetch_redirect_response=False)
        self.assertTrue(Passkey.objects.filter(pk=self.passkey.pk).exists())
        html = self.client.get(reverse("passkey_list")).content.decode()
        feld = f'id="passkey-entfernen-{self.passkey.pk}"'
        self.assertIn(feld, html)
        self.assertIn('aria-invalid="true"', html)
        self.assertEqual(html.count(absage), 1, html)
        # Shown once: the next visit has a clean form again.
        self.assertNotContains(self.client.get(reverse("passkey_list")), absage)
        self.assertEqual(mail.outbox, [])

    def test_ohne_passwort_bleibt_der_schluessel(self):
        for daten in ({}, {"passwort": ""}):
            with self.subTest(daten=daten):
                self.pruefe_abgelehnt(self.entfernen(**daten), "Bitte gib dein Passwort ein.")
        # Nothing typed is no guess: it does not count towards the lock.
        self.assertFalse(AccessAttempt.objects.exists())

    def test_ein_falsches_passwort_bleibt_der_schluessel(self):
        self.pruefe_abgelehnt(self.entfernen(passwort="daneben"), "Das Passwort stimmt nicht.")
        self.assertEqual(AccessAttempt.objects.get().failures_since_start, 1)

    def test_mit_dem_passwort_geht_er(self):
        antwort = self.client.post(
            reverse("passkey_delete", args=[self.passkey.pk]), {"passwort": PASSWORT}, follow=True
        )

        self.assertRedirects(antwort, reverse("passkey_list"))
        self.assertContains(antwort, "Passkey „Handy“ ist entfernt.")
        self.assertFalse(Passkey.objects.exists())

    def test_eine_abgelehnte_entfernung_meldet_niemanden_ab(self):
        anderswo = Client()
        anderswo.force_login(self.leitung)

        self.entfernen(passwort="daneben")

        self.assertEqual(anderswo.get(reverse("passkey_list")).status_code, 200)

    def test_falsche_passwoerter_sperren_wie_beim_anmelden(self):
        """The fifth wrong one locks, and the lock holds against the right one too."""
        for _ in range(5):
            antwort = self.entfernen(passwort="daneben")
        self.assertContains(antwort, "Zu viele Fehlversuche", status_code=429)

        self.assertEqual(self.entfernen(passwort=PASSWORT).status_code, 429)
        self.assertTrue(Passkey.objects.filter(pk=self.passkey.pk).exists())
        self.client.logout()
        antwort = self.client.post(reverse("login"), {"username": "leitung", "password": PASSWORT})
        self.assertEqual(antwort.status_code, 429)

    def test_eine_bestaetigung_ersetzt_das_passwort_nicht(self):
        bestaetigt(self.client, self.leitung)

        self.pruefe_abgelehnt(self.entfernen(), "Bitte gib dein Passwort ein.")

    def test_das_passwort_wird_vor_der_sperre_geprueft(self):
        """Hashing under the administrators' lock would hold every other removal as long."""
        folge = []
        echt_passwort, echt_sperre = (
            passkey_modul.passwort_haelt,
            passkey_modul.administratoren_sperren,
        )

        def passwort(*args):
            folge.append("Passwort")
            return echt_passwort(*args)

        def sperre():
            folge.append("Sperre")
            return echt_sperre()

        with (
            mock.patch.object(passkey_modul, "passwort_haelt", side_effect=passwort),
            mock.patch.object(passkey_modul, "administratoren_sperren", side_effect=sperre),
        ):
            self.entfernen(passwort=PASSWORT)

        self.assertEqual(folge, ["Passwort", "Sperre"])
        self.assertFalse(Passkey.objects.exists())

    def test_ein_fremder_schluessel_ist_unbekannt(self):
        """Before the password: a key of another account does not exist from here."""
        andere = administrator(email="chef@example.com", first_name="C", last_name="D")
        fremd = hinterlegen(andere, Authenticator(), name="Fremdgerät")

        antwort = self.client.post(reverse("passkey_delete", args=[fremd.pk]), {"passwort": "x"})

        self.assertEqual(antwort.status_code, 404)
        self.assertTrue(Passkey.objects.filter(pk=fremd.pk).exists())
        self.assertFalse(AccessAttempt.objects.exists())

    def test_jeder_schluessel_hat_sein_feld(self):
        laptop = hinterlegen(self.leitung, Authenticator(), name="Laptop")

        html = self.client.get(reverse("passkey_list")).content.decode()

        for passkey in (self.passkey, laptop):
            with self.subTest(passkey=passkey.name):
                self.assertIn(f'action="{reverse("passkey_delete", args=[passkey.pk])}"', html)
                self.assertIn(f'for="passkey-entfernen-{passkey.pk}"', html)
        self.assertEqual(html.count('name="passwort"'), 2)
        self.assertIn("Entfernen fragt nach deinem Passwort", html)
        # The field of a new key stays the only one the script reads ('passkey.js').
        self.assertEqual(html.count("data-passkey-passwort"), 1)


class EntfernenOhnePasswort(PasskeyTests):
    """Without a password one of the account's keys confirms the removal, as before a new key.

    Such an account has nothing to type. Until 10/2026 whoever found its
    session open removed every key, and the owner had neither a key nor a
    password left: "Forgot your password?" passes accounts without one by.
    """

    def setUp(self):
        super().setUp()
        self.leitung.set_unusable_password()
        self.leitung.save()
        self.passkey = hinterlegen(self.leitung, self.geraet)
        self.laptop = hinterlegen(self.leitung, Authenticator(), name="Laptop")
        self.client.force_login(self.leitung)

    def entfernen(self, passkey: Passkey):
        return self.client.post(reverse("passkey_delete", args=[passkey.pk]))

    def bestaetigen(self):
        """The ceremony of 'BestaetigenOhnePasswort', with this device's key."""
        frage = self.json_post("passkey_bestaetigen_optionen", {}).json()["challenge"]
        credential = self.geraet.anmeldung(frage, user_handle(self.leitung))
        antwort = self.json_post("passkey_bestaetigen", {"credential": credential})
        self.assertEqual(antwort.status_code, 200, antwort.content)

    def pruefe_abgelehnt(self, antwort, absage: str) -> None:
        self.assertRedirects(antwort, reverse("passkey_list"), fetch_redirect_response=False)
        self.assertEqual(Passkey.objects.filter(benutzer=self.leitung).count(), 2)
        self.assertContains(self.client.get(reverse("passkey_list")), absage, count=1)

    def test_eine_offene_sitzung_entfernt_keinen_schluessel(self):
        """The gap: the session alone removed both keys, and the owner kept no way in."""
        anderswo = Client()
        anderswo.force_login(self.leitung)

        for passkey in (self.passkey, self.laptop):
            with self.subTest(passkey=passkey.name):
                self.pruefe_abgelehnt(self.entfernen(passkey), BESTAETIGUNG_FEHLT)

        self.assertEqual(anderswo.get(reverse("passkey_list")).status_code, 200)

    def test_bestaetigt_geht_er(self):
        self.bestaetigen()

        antwort = self.client.post(reverse("passkey_delete", args=[self.laptop.pk]), follow=True)

        self.assertContains(antwort, "Passkey „Laptop“ ist entfernt.")
        self.assertEqual(list(Passkey.objects.all()), [self.passkey])

    def test_die_bestaetigung_gilt_zwei_minuten_fuer_jeden_schritt(self):
        """Not used up, as before a new key: a second removal in that time asks nothing more."""
        self.bestaetigen()
        dritter = hinterlegen(self.leitung, Authenticator(), name="Tablet")

        with spaeter(ZWEI_MINUTEN - 1):
            self.entfernen(self.laptop)
            self.entfernen(dritter)
        self.assertEqual(list(Passkey.objects.all()), [self.passkey])

        zweiter = hinterlegen(self.leitung, Authenticator(), name="Laptop")
        with spaeter(ZWEI_MINUTEN + 1):
            antwort = self.entfernen(zweiter)
        self.assertRedirects(antwort, reverse("passkey_list"), fetch_redirect_response=False)
        self.assertTrue(Passkey.objects.filter(pk=zweiter.pk).exists())
        self.assertContains(self.client.get(reverse("passkey_list")), BESTAETIGUNG_ABGELAUFEN)

    def test_die_bestaetigung_eines_anderen_kontos_gilt_nicht(self):
        zoe = CustomUser.objects.create_user(
            username="zoe", email="zoe@example.com", password=None, first_name="Z", last_name="Z"
        )
        bestaetigt(self.client, zoe)

        self.pruefe_abgelehnt(self.entfernen(self.laptop), BESTAETIGUNG_ABGELAUFEN)

    def test_die_seite_bietet_die_bestaetigung_einmal_an(self):
        """Above both cards: it holds for a new key and for removing one."""
        html = self.client.get(reverse("passkey_list")).content.decode()

        self.assertEqual(html.count("data-passkey-bestaetigen"), 1)
        self.assertLess(html.index("data-passkey-bestaetigen"), html.index("Deine Passkeys"))
        self.assertNotIn('name="passwort"', html)
        self.assertIn("Bestätige dafür zuerst oben mit einem deiner Passkeys.", html)


class Frist(PasskeyTests):
    """A question holds two minutes, on the website as in the app.

    Until 10/2026 it stayed in the session as long as the session lived. A
    dialog of the device that was broken off left it lying for whoever sat
    down at the browser later.
    """

    def spaeter(self, sekunden: float):
        """The clock this many seconds after the question."""
        return mock.patch("time.time", return_value=time.time() + sekunden)

    def test_ein_spaeter_schluessel_wird_nicht_angelegt(self):
        self.client.force_login(self.leitung)
        optionen = self.anlegeoptionen().json()
        credential = self.geraet.registrierung(optionen["challenge"])

        with self.spaeter(ZWEI_MINUTEN + 1):
            antwort = self.json_post("passkey_anlegen", {"credential": credential, "name": ""})

        self.assertEqual(antwort.status_code, 400)
        self.assertEqual(
            antwort.json(), {"fehler": "Die Anfrage ist abgelaufen. Bitte noch einmal versuchen."}
        )
        self.assertFalse(Passkey.objects.exists())
        # Used up all the same: a second answer gets no second look.
        self.assertNotIn("passkey_anlegen", self.client.session)

    def test_in_der_frist_wird_er_angelegt(self):
        self.client.force_login(self.leitung)
        optionen = self.anlegeoptionen().json()
        credential = self.geraet.registrierung(optionen["challenge"])

        with self.spaeter(ZWEI_MINUTEN - 1):
            antwort = self.json_post("passkey_anlegen", {"credential": credential, "name": ""})

        self.assertEqual(antwort.status_code, 200, antwort.content)
        self.assertTrue(Passkey.objects.exists())

    def test_eine_spaete_anmeldung_oeffnet_nichts(self):
        self.client.force_login(self.leitung)
        self.registrieren(name="Laptop")
        self.client.logout()
        optionen = self.client.post(reverse("passkey_anmelden_optionen")).json()
        credential = self.geraet.anmeldung(optionen["challenge"], user_handle(self.leitung))

        with self.spaeter(ZWEI_MINUTEN + 1):
            antwort = self.json_post("passkey_anmelden", {"credential": credential})

        self.assertEqual(antwort.status_code, 400)
        self.assertIn("abgelaufen", antwort.json()["fehler"])
        self.assertFalse(self.angemeldet_als(self.leitung))

    def test_die_attrappe_laesst_die_frage_ebenso_ablaufen(self):
        """Otherwise a late answer with a known key told the two apart."""
        self.client.force_login(self.leitung)
        self.registrieren(name="Laptop")
        self.client.logout()
        antworten = []
        for optionen, ziel in (
            (reverse("passkey_anmelden_optionen"), reverse("passkey_anmelden")),
            ("/admin/login/passkey/options/", "/admin/login/passkey/anmelden/"),
        ):
            challenge = self.client.post(optionen).json()["challenge"]
            credential = self.geraet.anmeldung(challenge, user_handle(self.leitung))
            with self.spaeter(ZWEI_MINUTEN + 1):
                antwort = self.client.post(
                    ziel, json.dumps({"credential": credential}), content_type="application/json"
                )
            self.assertEqual(antwort.status_code, 400, ziel)
            antworten.append(antwort.json()["fehler"])

        echt, nachgemacht = antworten
        self.assertIn("abgelaufen", echt)
        self.assertEqual(nachgemacht, echt)

    def test_eine_frage_ohne_zeit_gilt_als_abgelaufen(self):
        """As the state before the period stored it: the challenge alone."""
        sitzung = self.client.session
        sitzung["passkey_challenge"] = bytes_to_base64url(b"x" * 32)
        sitzung.save()
        credential = self.geraet.anmeldung(bytes_to_base64url(b"x" * 32), user_handle(self.leitung))

        antwort = self.json_post("passkey_anmelden", {"credential": credential})

        self.assertEqual(antwort.status_code, 400)
        self.assertIn("abgelaufen", antwort.json()["fehler"])


class Anmelden(PasskeyTests):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.leitung)
        self.registrieren(name="Laptop")
        self.client.logout()

    def test_die_optionen_fragen_ohne_benutzernamen(self):
        optionen = self.client.post(reverse("passkey_anmelden_optionen")).json()
        self.assertEqual(optionen["rpId"], RP_ID)
        self.assertEqual(optionen["userVerification"], "required")
        self.assertEqual(optionen.get("allowCredentials", []), [])
        self.assertIn("passkey_challenge", self.client.session)

    def test_die_signatur_meldet_das_konto_an(self):
        antwort = self.anmelden(self.leitung, weiter=reverse("haus_list"))
        self.assertEqual(antwort.status_code, 200, antwort.content)
        self.assertEqual(antwort.json(), {"weiter": reverse("haus_list")})
        self.assertTrue(self.angemeldet_als(self.leitung))
        passkey = Passkey.objects.get()
        self.assertEqual(passkey.signaturzaehler, 1)
        self.assertIsNotNone(passkey.zuletzt_benutzt)
        # And the site is open afterwards.
        self.assertEqual(self.client.get(reverse("haus_list")).status_code, 200)

    def test_ohne_ziel_geht_es_zur_startseite(self):
        self.assertEqual(self.anmelden(self.leitung).json(), {"weiter": reverse("home")})

    def test_ein_fremdes_ziel_wird_verworfen(self):
        antwort = self.anmelden(self.leitung, weiter="https://example.org/")
        self.assertEqual(antwort.json(), {"weiter": reverse("home")})

    def test_eine_frage_gilt_nur_einmal(self):
        optionen = self.client.post(reverse("passkey_anmelden_optionen")).json()
        credential = self.geraet.anmeldung(optionen["challenge"], user_handle(self.leitung))
        erste = self.json_post("passkey_anmelden", {"credential": credential})
        self.assertEqual(erste.status_code, 200)
        self.client.logout()
        antwort = self.json_post("passkey_anmelden", {"credential": credential})
        self.assertEqual(antwort.status_code, 400)
        self.assertFalse(self.angemeldet_als(self.leitung))

    def test_ein_fremder_schluessel_oeffnet_nichts(self):
        antwort = self.anmelden(self.leitung, geraet=Authenticator())
        self.assertEqual(antwort.status_code, 400)
        self.assertIn("nicht bekannt", antwort.json()["fehler"])
        self.assertFalse(self.angemeldet_als(self.leitung))

    def test_eine_falsche_signatur_oeffnet_nichts(self):
        """Same id, other key pair: what a tampered answer would look like."""
        falsch = Authenticator()
        falsch.credential_id = self.geraet.credential_id
        antwort = self.anmelden(self.leitung, geraet=falsch)
        self.assertEqual(antwort.status_code, 400)
        self.assertIn("fehlgeschlagen", antwort.json()["fehler"])
        self.assertFalse(self.angemeldet_als(self.leitung))

    def test_ein_geklonter_schluessel_faellt_am_zaehler_auf(self):
        self.anmelden(self.leitung)
        self.client.logout()
        antwort = self.anmelden(self.leitung, counter=1)
        self.assertEqual(antwort.status_code, 400)
        self.assertFalse(self.angemeldet_als(self.leitung))

    def test_ohne_pruefung_der_person_wird_abgelehnt(self):
        antwort = self.anmelden(self.leitung, flags=0x01)
        self.assertEqual(antwort.status_code, 400)
        self.assertFalse(self.angemeldet_als(self.leitung))

    def test_ein_fremder_benutzer_handle_wird_abgelehnt(self):
        andere = administrator(email="chef@example.com", first_name="C", last_name="D")
        optionen = self.client.post(reverse("passkey_anmelden_optionen")).json()
        credential = self.geraet.anmeldung(optionen["challenge"], user_handle(andere))
        antwort = self.json_post("passkey_anmelden", {"credential": credential})
        self.assertEqual(antwort.status_code, 400)
        self.assertIn("anderen Konto", antwort.json()["fehler"])
        self.assertFalse(self.angemeldet_als(andere))

    def test_ein_eben_entfernter_schluessel_ist_eine_absage(self):
        """Removed on another device between reading the key and writing its counter.

        Until 10/2026 writing the counter found no row: a server error.
        """
        self.client.raise_request_exception = False
        echt = passkey_modul.verify_authentication_response

        def entfernt(**kwargs):
            ergebnis = echt(**kwargs)
            Passkey.objects.filter(benutzer=self.leitung).delete()
            return ergebnis

        with mock.patch.object(
            passkey_modul, "verify_authentication_response", side_effect=entfernt
        ):
            antwort = self.anmelden(self.leitung)

        self.assertEqual(antwort.status_code, 400)
        self.assertEqual(antwort.json(), {"fehler": "Dieser Passkey ist hier nicht bekannt."})
        self.assertFalse(self.angemeldet_als(self.leitung))

    def test_ein_deaktiviertes_konto_bleibt_zu(self):
        CustomUser.objects.filter(pk=self.leitung.pk).update(is_active=False)
        antwort = self.anmelden(self.leitung)
        self.assertEqual(antwort.status_code, 400)
        self.assertIn("deaktiviert", antwort.json()["fehler"])
        self.assertFalse(self.angemeldet_als(self.leitung))

    def test_ein_eben_deaktiviertes_konto_bleibt_zu(self):
        """Switched off between the checks before the signature and the session.

        Until 10/2026 the account read before the lock was let in. Its session
        came after the deactivation had signed the account out everywhere, and
        it held again once the account was switched on.
        """
        echt = passkey_modul.verify_authentication_response

        def deaktiviert(**kwargs):
            ergebnis = echt(**kwargs)
            CustomUser.objects.filter(pk=self.leitung.pk).update(is_active=False)
            return ergebnis

        with mock.patch.object(
            passkey_modul, "verify_authentication_response", side_effect=deaktiviert
        ):
            antwort = self.anmelden(self.leitung)

        self.assertEqual(antwort.status_code, 400)
        self.assertEqual(antwort.json(), {"fehler": "Dieses Konto ist deaktiviert."})
        CustomUser.objects.filter(pk=self.leitung.pk).update(is_active=True)
        self.assertFalse(self.angemeldet_als(self.leitung))

    def test_eingelassen_wird_das_konto_wie_es_unter_der_sperre_steht(self):
        """A new password in between: the session carries it and not the one read before."""
        echt = passkey_modul.verify_authentication_response

        def neues_passwort(**kwargs):
            ergebnis = echt(**kwargs)
            konto = CustomUser.objects.get(pk=self.leitung.pk)
            konto.set_password("ein-neues-Passwort-9")
            konto.save(update_fields=["password"])
            return ergebnis

        with mock.patch.object(
            passkey_modul, "verify_authentication_response", side_effect=neues_passwort
        ):
            self.assertEqual(self.anmelden(self.leitung).status_code, 200)

        # Signed with the hash read before, the session would end on the next page.
        self.assertEqual(self.client.get(reverse("haus_list")).status_code, 200)

    def test_das_anmeldeformular_traegt_den_knopf(self):
        html = self.client.get(reverse("login")).content.decode()
        for stueck in (
            "data-passkey-anmelden",
            'data-passkey-optionen="passkey/options/"',
            'data-passkey-ziel="passkey/anmelden/"',
            "Mit Passkey anmelden",
            "js/passkey.js",
        ):
            with self.subTest(stueck=stueck):
                self.assertIn(stueck, html)

    def test_die_adressen_liegen_relativ_zum_formular(self):
        """'passkey/options/' next to '/accounts/login/' is the real endpoint."""
        self.assertEqual(reverse("passkey_anmelden_optionen"), "/accounts/login/passkey/options/")
        self.assertEqual(reverse("passkey_anmelden"), "/accounts/login/passkey/anmelden/")


class GrosserZaehler(PasskeyTests):
    """A signature count beyond 31 bits: the standard counts in 32 without a sign.

    PostgreSQL's 'integer' holds 31. Storing such a count ended in a server
    error there, and that key could never sign in (until 10/2026). Then the
    column kept the largest count it held, and a clone counting above that
    went unnoticed. The column takes the whole count now ('accounts/0004').
    """

    #: The largest count the standard allows, four bytes without a sign.
    GROESSTER = 2**32 - 1

    def setUp(self):
        super().setUp()
        self.client.raise_request_exception = False
        self.client.force_login(self.leitung)

    def test_beim_anlegen_genau(self):
        self.geraet.counter = 2**31

        _optionen, antwort = self.registrieren(name="Laptop")

        self.assertEqual(antwort.status_code, 200, antwort.content)
        self.assertEqual(Passkey.objects.get().signaturzaehler, 2**31)

    def test_beim_anmelden_genau_bis_zum_groessten(self):
        self.registrieren(name="Laptop")
        self.client.logout()
        for zaehler in (2**31 + 5, self.GROESSTER):
            with self.subTest(zaehler=zaehler):
                antwort = self.anmelden(self.leitung, counter=zaehler)

                self.assertEqual(antwort.status_code, 200, antwort.content)
                self.assertTrue(self.angemeldet_als(self.leitung))
                self.assertEqual(Passkey.objects.get().signaturzaehler, zaehler)
                self.client.logout()

    def test_ein_klon_faellt_auch_darueber_auf(self):
        """A count below the stored one is refused, as the library refuses a clone.

        With the cap, the count stored was 31 bits, and a clone at
        '2**31 + 5' beat it after the key had counted to '2**31 + 6'.
        """
        self.registrieren(name="Laptop")
        self.client.logout()
        self.assertEqual(self.anmelden(self.leitung, counter=2**31 + 6).status_code, 200)
        self.client.logout()

        antwort = self.anmelden(self.leitung, counter=2**31 + 5)

        self.assertEqual(antwort.status_code, 400)
        self.assertEqual(antwort.json(), {"fehler": str(passkey_modul.FEHLGESCHLAGEN)})
        self.assertFalse(self.angemeldet_als(self.leitung))
        self.assertEqual(Passkey.objects.get().signaturzaehler, 2**31 + 6)


class DieAttrappe(PasskeyTests):
    """The decoy under '/admin/' answers the same questions and refuses every answer."""

    def test_die_attrappe_stellt_dieselbe_frage(self):
        optionen = self.client.post("/admin/login/passkey/options/").json()
        self.assertEqual(optionen["rpId"], RP_ID)
        self.assertEqual(optionen["userVerification"], "required")
        # In the session like the real one, under a key of its own.
        self.assertIn("passkey_attrappe", self.client.session)
        self.assertNotIn("passkey_challenge", self.client.session)

    def test_die_attrappe_antwortet_unter_jeder_adresse(self):
        for adresse in ("/admin/passkey/options/", "/admin/irgendwo/tief/passkey/options/"):
            with self.subTest(adresse=adresse):
                self.assertEqual(self.client.post(adresse).status_code, 200)

    def test_die_attrappe_zeichnet_auf_und_meldet_niemanden_an(self):
        self.client.force_login(self.leitung)
        self.registrieren(name="Laptop")
        self.client.logout()
        optionen = self.client.post("/admin/login/passkey/options/").json()
        credential = self.geraet.anmeldung(optionen["challenge"], user_handle(self.leitung))
        antwort = self.client.post(
            "/admin/login/passkey/anmelden/",
            json.dumps({"credential": credential}),
            content_type="application/json",
        )
        self.assertEqual(antwort.status_code, 400)
        self.assertIn("fehlgeschlagen", antwort.json()["fehler"])
        self.assertFalse(self.angemeldet_als(self.leitung))
        versuch = Anmeldeversuch.objects.get()
        self.assertEqual(versuch.username, f"passkey:{self.geraet.kennung[:40]}")
        self.assertEqual(versuch.path, "/admin/login/passkey/anmelden/")

    def test_die_attrappe_antwortet_in_derselben_reihenfolge_wie_die_echte(self):
        """Until 10/2026 two requests told them apart: the decoy always said "fehlgeschlagen"."""
        erfunden = {"credential": {"id": "AAAA", "response": {}}}
        attrappe = "/admin/login/passkey/anmelden/"

        # Without a challenge both say "abgelaufen".
        echt = self.json_post("passkey_anmelden", erfunden).json()["fehler"]
        nachgemacht = self.client.post(
            attrappe, json.dumps(erfunden), content_type="application/json"
        ).json()["fehler"]
        self.assertIn("abgelaufen", echt)
        self.assertEqual(echt, nachgemacht)

        # With a challenge and an unknown key both say "nicht bekannt".
        self.client.post(reverse("passkey_anmelden_optionen"))
        echt = self.json_post("passkey_anmelden", erfunden).json()["fehler"]
        self.client.post("/admin/login/passkey/options/")
        nachgemacht = self.client.post(
            attrappe, json.dumps(erfunden), content_type="application/json"
        ).json()["fehler"]
        self.assertIn("nicht bekannt", echt)
        self.assertEqual(echt, nachgemacht)

    def beide_tueren(self, credential_fuer) -> tuple[str, str]:
        """The refusal of the real door and of the decoy, each to its own question."""
        antworten = []
        for optionen, ziel in (
            (reverse("passkey_anmelden_optionen"), reverse("passkey_anmelden")),
            ("/admin/login/passkey/options/", "/admin/login/passkey/anmelden/"),
        ):
            challenge = self.client.post(optionen).json()["challenge"]
            antwort = self.client.post(
                ziel,
                json.dumps({"credential": credential_fuer(challenge)}),
                content_type="application/json",
            )
            self.assertEqual(antwort.status_code, 400, ziel)
            antworten.append(antwort.json()["fehler"])
        return antworten[0], antworten[1]

    def test_die_attrappe_prueft_vor_der_signatur_wie_die_echte(self):
        """The same checks up to the signature, in the same words ('vor_der_signatur').

        Its own copy had drifted (until 10/2026). What is no browser's answer
        heard "not known" there, a known key with a foreign handle or of a
        deactivated account "failed".
        """
        self.client.force_login(self.leitung)
        self.registrieren(name="Laptop")
        self.client.logout()
        andere = administrator(email="chef@example.com", first_name="C", last_name="D")
        faelle: dict[str, Callable[[str], object]] = {
            repr(fall): (lambda _frage, fall=fall: fall) for fall in FremdeTypen.FAELLE
        }
        faelle["fremder Handle"] = lambda frage: self.geraet.anmeldung(frage, user_handle(andere))
        for fall, credential_fuer in faelle.items():
            with self.subTest(fall=fall):
                echt, nachgemacht = self.beide_tueren(credential_fuer)
                self.assertEqual(nachgemacht, echt)
        self.assertIn("anderen Konto", echt)

        CustomUser.objects.filter(pk=self.leitung.pk).update(is_active=False)
        echt, nachgemacht = self.beide_tueren(
            lambda frage: self.geraet.anmeldung(frage, user_handle(self.leitung))
        )
        self.assertIn("deaktiviert", echt)
        self.assertEqual(nachgemacht, echt)

    def test_die_koepfe_verraten_die_attrappe_nicht(self):
        """Until 10/2026 only the decoy said 'no-store'. One request told them apart."""
        erfunden = json.dumps({"credential": {"id": "AAAA", "response": {}}})

        def koepfe(adresse, koerper=None):
            antwort = self.client.post(adresse, koerper, content_type="application/json")
            return {name.lower() for name in antwort.headers}, antwort.get("Cache-Control", "")

        echt = reverse("passkey_anmelden_optionen"), reverse("passkey_anmelden")
        attrappe = "/admin/login/passkey/options/", "/admin/login/passkey/anmelden/"
        self.assertEqual(koepfe(echt[0]), koepfe(attrappe[0]))
        self.assertEqual(koepfe(echt[1], erfunden), koepfe(attrappe[1], erfunden))
        self.assertIn("no-store", koepfe(echt[1], erfunden)[1])

    def test_die_echte_frage_oeffnet_die_attrappe_nicht_und_umgekehrt(self):
        """A challenge of the decoy must not open the real door."""
        self.client.force_login(self.leitung)
        self.registrieren(name="Laptop")
        self.client.logout()
        optionen = self.client.post("/admin/login/passkey/options/").json()
        credential = self.geraet.anmeldung(optionen["challenge"], user_handle(self.leitung))
        antwort = self.json_post("passkey_anmelden", {"credential": credential})
        self.assertEqual(antwort.status_code, 400)
        self.assertFalse(self.angemeldet_als(self.leitung))


class KurzeSitzungOhneAnmeldung(PasskeyTests):
    """The question to a visitor who is not signed in holds the session for two minutes only.

    Every anonymous click on the button, here and at the decoy, wrote a
    session of two weeks (until 10/2026), a row anyone could add without
    signing in. Removing a passkey read every open one then. Signing in
    afterwards gives the session its full age.
    """

    def frist(self) -> timedelta:
        """How long the browser's session still holds, as the table says."""
        schluessel = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        return Sitzung.objects.get(session_key=schluessel).expire_date - timezone.now()

    def pruefe_kurz(self) -> None:
        self.assertLessEqual(self.frist(), timedelta(seconds=ZWEI_MINUTEN))
        self.assertGreater(self.frist(), timedelta(seconds=ZWEI_MINUTEN - 30))

    def pruefe_voll(self) -> None:
        voll = timedelta(seconds=settings.SESSION_COOKIE_AGE)
        self.assertGreater(self.frist(), voll - timedelta(minutes=1))
        self.assertNotIn("_session_expiry", self.client.session)

    def test_die_frage_haelt_die_sitzung_nur_zwei_minuten(self):
        antwort = self.client.post(reverse("passkey_anmelden_optionen"))

        self.pruefe_kurz()
        self.assertEqual(antwort.cookies[settings.SESSION_COOKIE_NAME]["max-age"], ZWEI_MINUTEN)

    def test_die_frage_der_attrappe_ebenso(self):
        """Otherwise the cookie told the two apart."""
        echt = self.client.post(reverse("passkey_anmelden_optionen"))
        self.client.cookies.clear()
        attrappe = self.client.post("/admin/login/passkey/options/")

        self.pruefe_kurz()
        name = settings.SESSION_COOKIE_NAME
        self.assertEqual(attrappe.cookies[name]["max-age"], echt.cookies[name]["max-age"])

    def test_wer_angemeldet_ist_behaelt_seine_frist(self):
        self.client.force_login(self.leitung)

        self.assertEqual(self.anlegeoptionen().status_code, 200)
        self.client.post(reverse("passkey_anmelden_optionen"))

        self.pruefe_voll()

    def test_das_passwort_danach_meldet_mit_der_vollen_frist_an(self):
        """Django keeps the session's content when it signs in, the short expiry with it."""
        self.client.post(reverse("passkey_anmelden_optionen"))
        self.pruefe_kurz()

        antwort = self.client.post(reverse("login"), {"username": "leitung", "password": PASSWORT})

        self.assertEqual(antwort.status_code, 302)
        self.assertTrue(self.angemeldet_als(self.leitung))
        self.pruefe_voll()

    def test_der_passkey_danach_meldet_mit_der_vollen_frist_an(self):
        self.client.force_login(self.leitung)
        self.registrieren(name="Laptop")
        self.client.logout()

        antwort = self.anmelden(self.leitung)

        self.assertEqual(antwort.status_code, 200, antwort.content)
        self.assertTrue(self.angemeldet_als(self.leitung))
        self.pruefe_voll()


class AnonymeFragenBleibenWenige(PasskeyTests):
    """What questions without a sign-in leave behind goes with the next new one.

    Every click without a session wrote a row of its own, at the real door
    and at the decoy, and anyone could send millions without a cookie. They
    held two minutes and stayed until the nightly cleanup (until 10/2026).
    Now a visitor who sends its session keeps its one row, and each new row
    takes the ones without an account that ran out: the table holds the
    questions of the last two minutes.
    """

    TUEREN = {
        "echt": reverse_lazy("passkey_anmelden_optionen"),
        "Attrappe": "/admin/login/passkey/options/",
    }

    def ohne_cookie(self, adresse) -> None:
        self.assertEqual(Client().post(adresse).status_code, 200)

    def ablaufen_lassen(self) -> None:
        Sitzung.objects.update(expire_date=timezone.now() - timedelta(seconds=1))

    def test_wer_seine_sitzung_mitschickt_behaelt_eine_zeile(self):
        for name, adresse in self.TUEREN.items():
            with self.subTest(tuer=name):
                Sitzung.objects.all().delete()
                for _ in range(3):
                    self.client.post(adresse)
                self.assertEqual(Sitzung.objects.count(), 1)

    def test_eine_neue_frage_nimmt_die_abgelaufenen_mit(self):
        for name, adresse in self.TUEREN.items():
            with self.subTest(tuer=name):
                Sitzung.objects.all().delete()
                for _ in range(3):
                    self.ohne_cookie(adresse)
                angemeldet = Client()
                angemeldet.force_login(self.leitung)
                self.ablaufen_lassen()
                offen = Client()

                offen.post(adresse)
                self.ohne_cookie(adresse)

                ohne_konto = Sitzung.objects.filter(konto__isnull=True)
                self.assertEqual(ohne_konto.count(), 2)
                schluessel = offen.cookies[settings.SESSION_COOKIE_NAME].value
                self.assertTrue(ohne_konto.filter(session_key=schluessel).exists())
                # The account's rows are the nightly cleanup's ('manage.py cleanup').
                self.assertTrue(Sitzung.objects.filter(konto=self.leitung).exists())

    def test_die_frage_selbst_bleibt_beantwortbar(self):
        """Its own session is not among the ones that go."""
        self.client.force_login(self.leitung)
        self.registrieren(name="Laptop")
        self.client.logout()
        for _ in range(2):
            self.ohne_cookie(self.TUEREN["echt"])
        self.ablaufen_lassen()

        antwort = self.anmelden(self.leitung)

        self.assertEqual(antwort.status_code, 200, antwort.content)
        self.assertTrue(self.angemeldet_als(self.leitung))


class OhneAnmeldung(PasskeyTests):
    """The session expired while the page with the button was open."""

    def test_das_skript_hoert_401_statt_der_anmeldeseite(self):
        """'fetch' follows the redirect by itself, and the sign-in page answers 200.

        Until 10/2026 'passkey.js' took that page for the options, and after
        the second step it reloaded as if the key were stored.
        """
        for name in ("passkey_anlegen_optionen", "passkey_anlegen"):
            with self.subTest(name=name):
                rsp = self.client.post(reverse(name), headers={"X-Requested-With": "fetch"})

                self.assertEqual(rsp.status_code, 401)
                self.assertFalse(rsp.has_header("Location"))
        self.assertFalse(Passkey.objects.exists())

    def test_ohne_skript_bleibt_es_die_anmeldeseite(self):
        rsp = self.client.post(reverse("passkey_anlegen_optionen"))

        self.assertEqual(rsp.status_code, 302)
        self.assertIn("/accounts/login/", rsp["Location"])


class DieSeite(PasskeyTests):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.leitung)

    def test_die_seite_zeigt_die_schluessel_und_den_knopf(self):
        self.registrieren(name="Laptop im Büro")
        html = self.client.get(reverse("passkey_list")).content.decode()
        for stueck in (
            "Laptop im Büro",
            "noch nie benutzt",
            "data-passkey-anlegen",
            'data-passkey-optionen="add/options/"',
            'data-passkey-ziel="add/"',
            "data-passkey-name",
            "js/passkey.js",
        ):
            with self.subTest(stueck=stueck):
                self.assertIn(stueck, html)

    def test_die_adressen_liegen_relativ_zur_seite(self):
        self.assertEqual(reverse("passkey_list"), "/accounts/passkeys/")
        self.assertEqual(reverse("passkey_anlegen_optionen"), "/accounts/passkeys/add/options/")
        self.assertEqual(reverse("passkey_anlegen"), "/accounts/passkeys/add/")

    def test_entfernen_nimmt_den_eigenen_schluessel(self):
        self.registrieren(name="Laptop")
        passkey = Passkey.objects.get()
        antwort = self.client.post(
            reverse("passkey_delete", args=[passkey.pk]), {"passwort": PASSWORT}, follow=True
        )
        self.assertRedirects(antwort, reverse("passkey_list"))
        self.assertContains(antwort, "Passkey „Laptop“ ist entfernt.")
        self.assertFalse(Passkey.objects.exists())

    def test_fremde_schluessel_bleiben(self):
        andere = administrator(email="chef@example.com", first_name="C", last_name="D")
        fremd = Passkey.objects.create(
            benutzer=andere, kennung="fremd", oeffentlicher_schluessel=b"\x00", name="Fremdgerät"
        )
        adresse = reverse("passkey_delete", args=[fremd.pk])
        self.assertEqual(self.client.post(adresse).status_code, 404)
        self.assertEqual(self.client.get(adresse).status_code, 405)
        self.assertNotContains(self.client.get(reverse("passkey_list")), "Fremdgerät")

    def test_das_kontomenue_und_die_sperrseite_fuehren_hin(self):
        self.assertContains(self.client.get(reverse("haus_list")), reverse("passkey_list"))
        zaehler = CustomUser.objects.create_user(
            username="zoe", email="zoe@example.com", password="x", first_name="Z", last_name="Z"
        )
        self.client.force_login(zaehler)
        self.assertContains(self.client.get(reverse("kein_zugriff")), reverse("passkey_list"))

    def test_zaehlende_kommen_auf_die_seite(self):
        """Under '/accounts/', like changing the password: the block does not apply there."""
        zaehler = CustomUser.objects.create_user(
            username="zoe", email="zoe@example.com", password="x", first_name="Z", last_name="Z"
        )
        self.client.force_login(zaehler)
        self.assertEqual(self.client.get(reverse("passkey_list")).status_code, 200)

    def test_anonyme_werden_zur_anmeldung_geschickt(self):
        self.client.logout()
        antwort = self.client.get(reverse("passkey_list"))
        self.assertEqual(antwort.status_code, 302)
        self.assertIn("/accounts/login/", antwort["Location"])

    def test_die_verwaltung_listet_die_schluessel(self):
        self.registrieren(name="Laptop")
        chef = administrator(email="chef@example.com", first_name="C", last_name="D")
        self.client.force_login(chef)
        self.assertContains(self.client.get(reverse("admin:accounts_passkey_changelist")), "Laptop")


class VorgeschlagenerName(SimpleTestCase):
    def test_der_anbieter_folgt_der_sprache(self):
        google = "ea9b8d66-4d01-1d21-3ce4-b6b48cb575d4"
        with translation.override("de"):
            self.assertEqual(vorgeschlagener_name(google), "Google Passwortmanager")
        with translation.override("en"):
            self.assertEqual(vorgeschlagener_name(google), "Google Password Manager")
            # A product name stays as it is.
            windows = "08987058-cadc-4b81-b6e1-30de50dcbe96"
            self.assertEqual(vorgeschlagener_name(windows), "Windows Hello")

    def test_das_datum_folgt_der_sprache(self):
        unbekannt = "00000000-0000-0000-0000-000000000000"
        with translation.override("de"):
            self.assertRegex(vorgeschlagener_name(unbekannt), r"Passkey vom \d{2}\.\d{2}\.\d{4}$")
        with translation.override("en"):
            self.assertRegex(vorgeschlagener_name(unbekannt), r"\d{2}/\d{2}/\d{4}$")
