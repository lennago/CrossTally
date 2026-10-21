"""Signing out everywhere and a fresh token for the same account at the same moment.

Removing a passkey reads the refresh tokens of the account that still apply
and blocks them, then it ends the account's sessions ('accounts/sign_out.py').
The app renews by swapping its token for a fresh one ('ROTATE_REFRESH_TOKENS').
Until 10/2026 a renewal that asked the blocklist just before that read and
swapped just after it kept its fresh token, and the lost device its access.
'sign_out_everyone' had the same gap, and so had a pair from a website
session ('token_aus_sitzung') read just before the removal ended it, and a
sign-in with the very key being removed. Now both sides lock the account's
row first. The renewal asks the blocklist once more under the lock, the pair
its session, the sign-in its key. One side is held, in a forced order,
until the other one waits for a lock or is done
('api.test_closing_race._bis_jemand_wartet'). Runs in the CI, which tests
against PostgreSQL. The order of the locks is looked at on the queries in
'SperrenVorDemLesen', which runs on SQLite too.
"""

import json
import threading
from collections.abc import Callable
from http import HTTPStatus
from unittest import mock, skipUnless

from django.contrib.sessions.middleware import SessionMiddleware
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from accounts import passkey as website_passkey
from accounts import sign_out
from accounts.models import CustomUser, Passkey, Sitzung
from accounts.passkey import user_handle
from accounts.test_passkey import Authenticator
from accounts.test_sign_out import erneuern, schluessel, zaehlerin
from api.test_closing_race import _bis_jemand_wartet
from api.test_merge_split_race import abfragen_waehrend
from api.tests import adresse
from django_project.testing import administrator


def erneuerung(refresh: str):
    return APIClient().post(adresse("token_erneuern"), {"refresh": refresh})


class Nebeneinander(TransactionTestCase):
    """An account with a passkey, and two sides in two threads, the first one held at 'halt'.

    The administrator comes after the counter, with the higher number: the
    passkey's removal locks the administrators first ('administratoren_sperren').
    """

    serialized_rollback = True

    def setUp(self):
        self.zoe = zaehlerin()
        self.chef = administrator(email="chef@example.com")
        self.passkey = schluessel(self.zoe)

    def gleichzeitig(
        self, zuerst, danach, halt, *, nach_dem_aufruf: bool = False, autospec: bool = True
    ) -> dict:
        """Runs 'zuerst' until it reaches 'halt', then 'danach' beside it.

        'halt' names the class or module and the function to stop at, before
        it is called or, with 'nach_dem_aufruf', after it ran. A classmethod
        is held without 'autospec'.
        """
        ziel, name = halt
        echt = getattr(ziel, name)
        gehalten = threading.Event()
        fertig = threading.Event()
        ergebnis: dict = {}

        def halten(*args, **kwargs):
            if gehalten.is_set():
                return echt(*args, **kwargs)
            gehalten.set()
            if not nach_dem_aufruf:
                _bis_jemand_wartet(fertig)
                return echt(*args, **kwargs)
            antwort = echt(*args, **kwargs)
            _bis_jemand_wartet(fertig)
            return antwort

        def laufen(seite, tun):
            if seite == "danach":
                gehalten.wait(timeout=10)
            try:
                ergebnis[seite] = tun()
            except Exception as fehler:  # The test names it, the thread would hide it.
                ergebnis[f"{seite}_fehler"] = fehler
            finally:
                if seite == "danach":
                    fertig.set()
                connection.close()

        with mock.patch.object(ziel, name, autospec=autospec, side_effect=halten):
            faeden = [
                threading.Thread(target=laufen, args=("zuerst", zuerst)),
                threading.Thread(target=laufen, args=("danach", danach)),
            ]
            for faden in faeden:
                faden.start()
            for faden in faeden:
                faden.join()
        self.assertTrue(gehalten.is_set(), "ohne Halt prüft der Test nichts")
        self.assertNotIn("zuerst_fehler", ergebnis)
        self.assertNotIn("danach_fehler", ergebnis)
        return ergebnis

    def pruefe_nichts_gilt_mehr(self, *tokens: str) -> None:
        """Every token the phone holds afterwards is refused."""
        for token in tokens:
            with self.subTest(token=token[-12:]):
                self.assertEqual(erneuern(token), HTTPStatus.UNAUTHORIZED)


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class AbmeldenUndErneuernGleichzeitig(Nebeneinander):
    """The phone renews while its account is signed out everywhere, in both orders."""

    def setUp(self):
        super().setUp()
        self.handy = str(RefreshToken.for_user(self.zoe))
        # The app that removes: it shows its own sign-in ('api/passkey.py').
        self.eigenes = str(RefreshToken.for_user(self.zoe))

    def auf_der_website_entfernen(self):
        browser = Client()
        browser.force_login(self.zoe)
        return browser.post(reverse("passkey_delete", args=[self.passkey.pk]), {"passwort": "x"})

    def in_der_app_entfernen(self, passkey: Passkey | None = None):
        app = APIClient()
        app.force_authenticate(self.zoe)
        return app.delete(
            adresse("passkey_loeschen_api", (passkey or self.passkey).pk),
            {"refresh": self.eigenes, "passwort": "x"},
            format="json",
        )

    def test_erneuert_vor_dem_entfernen_auf_der_website(self):
        """The renewal holds the account first: the removal waits and blocks the fresh token."""
        ergebnis = self.gleichzeitig(
            lambda: erneuerung(self.handy),
            self.auf_der_website_entfernen,
            (RefreshToken, "blacklist"),
        )

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.OK)
        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.FOUND)
        self.pruefe_nichts_gilt_mehr(self.handy, ergebnis["zuerst"].data["refresh"])

    def test_erneuert_waehrend_des_entfernens_in_der_app(self):
        """The removal holds the account first: the renewal waits and finds its token blocked."""
        ergebnis = self.gleichzeitig(
            self.in_der_app_entfernen,
            lambda: erneuerung(self.handy),
            (sign_out, "_sperren"),
            nach_dem_aufruf=True,
        )

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.NO_CONTENT)
        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.UNAUTHORIZED)
        self.pruefe_nichts_gilt_mehr(self.handy)
        # The phone got nothing new. The only token still open is the removing device's.
        offen = OutstandingToken.objects.filter(user=self.zoe, blacklistedtoken__isnull=True)
        self.assertEqual([token.token for token in offen], [self.eigenes])

    def test_die_app_entfernt_waehrend_die_website_abmeldet(self):
        """The website's removal holds the account first: the app finds its own token blocked.

        Asked before the lock, the token still held, and a device signed out
        at that moment removed a key all the same.
        """
        laptop = schluessel(self.zoe, "def")
        ergebnis = self.gleichzeitig(
            self.auf_der_website_entfernen,
            lambda: self.in_der_app_entfernen(laptop),
            (sign_out, "_sperren"),
            nach_dem_aufruf=True,
        )

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.FOUND)
        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.FORBIDDEN)
        self.assertTrue(Passkey.objects.filter(pk=laptop.pk).exists())
        self.pruefe_nichts_gilt_mehr(self.eigenes)

    def test_erneuert_vor_dem_abmelden_aller(self):
        ergebnis = self.gleichzeitig(
            lambda: erneuerung(self.handy),
            sign_out.alle_abmelden,
            (RefreshToken, "blacklist"),
        )

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.OK)
        self.pruefe_nichts_gilt_mehr(self.handy, ergebnis["zuerst"].data["refresh"])

    def test_erneuert_waehrend_alle_abgemeldet_werden(self):
        ergebnis = self.gleichzeitig(
            sign_out.alle_abmelden,
            lambda: erneuerung(self.handy),
            (sign_out, "_sperren"),
            nach_dem_aufruf=True,
        )

        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.UNAUTHORIZED)
        self.pruefe_nichts_gilt_mehr(self.handy)
        self.assertFalse(
            OutstandingToken.objects.filter(blacklistedtoken__isnull=True).exists(),
            "ein Token ist nach dem Abmelden aller noch offen",
        )

    def test_alle_abmelden_waehrend_eines_entfernens_wartet_ohne_deadlock(self):
        """Both take the administrators first, then the accounts by number.

        The removal holds the administrator while it asks for the last way
        in. By number alone, signing everyone out would hold the counter (the
        lower number) and wait for the administrator, and the removal would
        wait for the counter: PostgreSQL ends one of the two with "deadlock
        detected".
        """
        ergebnis = self.gleichzeitig(
            self.auf_der_website_entfernen,
            sign_out.alle_abmelden,
            (website_passkey, "nimmt_den_letzten_weg"),
        )

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.FOUND)
        self.pruefe_nichts_gilt_mehr(self.handy)


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class PaarAusDerSitzungUndEntfernenGleichzeitig(Nebeneinander):
    """The app takes its sign-in from a website session while a removal ends that session.

    The key is removed on one browser, the app beside another one asks its
    session for a token pair ('token_aus_sitzung'). Both sessions exist
    before either side starts.
    """

    def setUp(self):
        super().setUp()
        self.hier = Client()
        self.hier.force_login(self.zoe)
        self.dort = APIClient()
        self.dort.force_login(self.zoe)

    def paar_aus_der_sitzung(self):
        return self.dort.post(adresse("token_aus_sitzung"))

    def entfernen(self):
        return self.hier.post(reverse("passkey_delete", args=[self.passkey.pk]), {"passwort": "x"})

    def test_ein_paar_vor_dem_entfernen_wird_gesperrt(self):
        """The pair holds the account first: the removal waits and blocks the fresh token."""
        ergebnis = self.gleichzeitig(
            self.paar_aus_der_sitzung, self.entfernen, (RefreshToken, "for_user"), autospec=False
        )

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.OK)
        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.FOUND)
        self.pruefe_nichts_gilt_mehr(ergebnis["zuerst"].data["refresh"])
        self.assertEqual(self.paar_aus_der_sitzung().status_code, HTTPStatus.FORBIDDEN)

    def test_nach_dem_entfernen_gibt_die_sitzung_keins_mehr(self):
        """The removal holds the account first: the session it ended gets nothing."""
        ergebnis = self.gleichzeitig(
            self.entfernen,
            self.paar_aus_der_sitzung,
            (sign_out, "sitzungen_beenden"),
            nach_dem_aufruf=True,
        )

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.FOUND)
        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.FORBIDDEN)
        self.assertFalse(
            OutstandingToken.objects.filter(user=self.zoe, blacklistedtoken__isnull=True).exists()
        )


class MitEchtemSchluessel:
    """The counter's own key, registered on a software device ('test_passkey.Authenticator')."""

    zoe: CustomUser

    def schluessel_des_geraets(self) -> Passkey:
        self.geraet = Authenticator()
        return Passkey.objects.create(
            benutzer=self.zoe,
            kennung=self.geraet.kennung,
            oeffentlicher_schluessel=self.geraet.cose_key(),
            name="Handy",
        )

    def in_der_app_vorbereitet(self) -> dict:
        """The answer of the device to the app's question, before anyone starts."""
        schritt = APIClient().post(adresse("passkey_anmelden_optionen_api")).data
        credential = self.geraet.anmeldung(schritt["options"]["challenge"], user_handle(self.zoe))
        return {"credential": credential, "challenge": schritt["challenge"]}


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class PasskeyAnmeldungUndEntfernenGleichzeitig(MitEchtemSchluessel, Nebeneinander):
    """One device signs in with the key while another one removes it.

    The removal comes from the website, on a browser that stays signed in.
    Neither side raises what a request raises: the test client catches it by
    a signal for every client with a request under way, and an error of the
    one thread came out in the other one.
    """

    def setUp(self):
        super().setUp()
        self.passkey = self.schluessel_des_geraets()
        self.hier = Client(raise_request_exception=False)
        self.hier.force_login(self.zoe)

    def entfernen(self):
        return self.hier.post(reverse("passkey_delete", args=[self.passkey.pk]), {"passwort": "x"})

    def in_der_app_anmelden(self, antwort: dict):
        app = APIClient(raise_request_exception=False)
        return app.post(adresse("passkey_anmelden_api"), antwort, format="json")

    def test_eine_anmeldung_vor_dem_entfernen_wird_abgemeldet(self):
        """The sign-in holds the account first: the removal waits and blocks its pair."""
        antwort = self.in_der_app_vorbereitet()
        ergebnis = self.gleichzeitig(
            lambda: self.in_der_app_anmelden(antwort),
            self.entfernen,
            (RefreshToken, "for_user"),
            autospec=False,
        )

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.OK)
        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.FOUND)
        self.pruefe_nichts_gilt_mehr(ergebnis["zuerst"].data["refresh"])

    def test_nach_dem_entfernen_ist_der_schluessel_unbekannt(self):
        """The removal holds key and account first: the sign-in hears the usual refusal."""
        antwort = self.in_der_app_vorbereitet()
        ergebnis = self.gleichzeitig(
            self.entfernen,
            lambda: self.in_der_app_anmelden(antwort),
            (sign_out, "_sperren"),
            nach_dem_aufruf=True,
        )

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.FOUND)
        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(
            ergebnis["danach"].data["detail"], "Dieser Passkey ist hier nicht bekannt."
        )
        self.assertFalse(OutstandingToken.objects.filter(user=self.zoe).exists())

    def test_waehrend_das_entfernen_den_schluessel_loescht(self):
        """The removal has deleted the key and not yet locked the account.

        Written under the account's lock, the counter waited here for the
        deleted row while the removal waited for the account.
        """
        antwort = self.in_der_app_vorbereitet()
        ergebnis = self.gleichzeitig(
            self.entfernen,
            lambda: self.in_der_app_anmelden(antwort),
            (website_passkey, "ueberall_abmelden"),
        )

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.FOUND)
        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.BAD_REQUEST)
        self.assertFalse(OutstandingToken.objects.filter(user=self.zoe).exists())

    def test_waehrend_ein_administrator_seinen_schluessel_entfernt(self):
        """The removal holds the administrators, the account among them, before it deletes.

        Held in a transaction while waiting for the account, the counter's row
        stopped the removal at the key.
        """
        CustomUser.objects.filter(pk=self.zoe.pk).update(is_staff=True, is_superuser=True)
        antwort = self.in_der_app_vorbereitet()
        ergebnis = self.gleichzeitig(
            self.entfernen,
            lambda: self.in_der_app_anmelden(antwort),
            (website_passkey, "nimmt_den_letzten_weg"),
        )

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.FOUND)
        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.BAD_REQUEST)
        self.assertFalse(OutstandingToken.objects.filter(user=self.zoe).exists())

    def auf_der_website(self) -> tuple[Client, Callable]:
        """A signed-out browser with the device's answer, and how it sends it."""
        browser = Client(raise_request_exception=False)
        optionen = browser.post(reverse("passkey_anmelden_optionen")).json()
        credential = self.geraet.anmeldung(optionen["challenge"], user_handle(self.zoe))

        def anmelden():
            return browser.post(
                reverse("passkey_anmelden"),
                json.dumps({"credential": credential}),
                content_type="application/json",
            )

        return browser, anmelden

    def test_eine_anmeldung_vor_dem_speichern_der_sitzung_wird_beendet(self):
        """The removal runs between the sign-in and the middleware's save of its session.

        Until 10/2026 the session named the account only from that save on:
        the removal passed it by, and the browser stayed signed in.
        """
        browser, anmelden = self.auf_der_website()
        ergebnis = self.gleichzeitig(
            anmelden, self.entfernen, (SessionMiddleware, "process_response")
        )

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.FOUND)
        self.assertNotEqual(
            browser.get(reverse("passkey_list")).status_code, HTTPStatus.OK, "noch angemeldet"
        )

    def test_eine_anmeldung_auf_der_website_wird_beendet(self):
        """The same on the website: the session of the sign-in ends with the removal."""
        browser, anmelden = self.auf_der_website()
        ergebnis = self.gleichzeitig(anmelden, self.entfernen, (website_passkey, "login"))

        # 400 where the removal ended the session before the middleware saved
        # it once more ('SessionInterrupted'). Which comes first is left open.
        self.assertIn(ergebnis["zuerst"].status_code, (HTTPStatus.OK, HTTPStatus.BAD_REQUEST))
        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.FOUND)
        self.assertNotEqual(
            browser.get(reverse("passkey_list")).status_code, HTTPStatus.OK, "noch angemeldet"
        )


class SperrenVorDemLesen(TestCase):
    """Who reads the tokens or swaps one locks the account's row first, and without its key.

    SQLite locks nothing, so the lock is read off the queries
    ('api.test_merge_split_race.abfragen_waehrend'): 'select_for_no_key_update'
    is the 'FOR NO KEY UPDATE' PostgreSQL gets.
    """

    def setUp(self):
        self.zoe = zaehlerin()
        self.handy = str(RefreshToken.for_user(self.zoe))

    def stelle(self, abfragen, gesucht) -> int:
        stelle = next((i for i, abfrage in enumerate(abfragen) if gesucht(abfrage)), None)
        if stelle is None:
            self.fail(f"keine solche Abfrage unter {[abfrage[4][:60] for abfrage in abfragen]}")
        return stelle

    @staticmethod
    def konto_gesperrt(abfrage) -> bool:
        modell, gesperrt, ohne_schluessel, _folge, _sql = abfrage
        return modell is CustomUser and gesperrt and ohne_schluessel

    def test_die_erneuerung_sperrt_vor_der_sperrliste_und_dem_tausch(self):
        abfragen = abfragen_waehrend(lambda: self.assertEqual(erneuern(self.handy), 200))

        sperre = self.stelle(abfragen, self.konto_gesperrt)
        nachgesehen = self.stelle(
            abfragen, lambda abfrage: "token_blacklist_blacklistedtoken" in abfrage[4]
        )
        geschrieben = self.stelle(abfragen, lambda abfrage: abfrage[4].startswith("INSERT"))
        self.assertLess(sperre, geschrieben)
        # Asked before the lock (the package, on reading the token) and once more after it.
        nachher = [
            i
            for i, abfrage in enumerate(abfragen)
            if i > sperre and "token_blacklist_blacklistedtoken" in abfrage[4]
        ]
        self.assertLess(nachgesehen, sperre)
        self.assertTrue(nachher and nachher[0] < geschrieben)

    def test_das_abmelden_sperrt_bevor_es_die_tokens_liest(self):
        abfragen = abfragen_waehrend(lambda: sign_out.tokens_sperren(self.zoe))

        sperre = self.stelle(abfragen, self.konto_gesperrt)
        gelesen = self.stelle(abfragen, lambda abfrage: abfrage[0] is OutstandingToken)
        self.assertLess(sperre, gelesen)

    def test_das_paar_aus_der_sitzung_sperrt_vor_der_sitzung_und_dem_token(self):
        browser = APIClient()
        browser.force_login(self.zoe)

        abfragen = abfragen_waehrend(
            lambda: self.assertEqual(browser.post(adresse("token_aus_sitzung")).status_code, 200)
        )

        sperre = self.stelle(abfragen, self.konto_gesperrt)
        sitzung = [i for i, abfrage in enumerate(abfragen) if i > sperre and abfrage[0] is Sitzung]
        neu = self.stelle(
            abfragen,
            lambda abfrage: abfrage[0] is OutstandingToken and abfrage[4].startswith("INSERT"),
        )
        self.assertTrue(sitzung, "unter der Sperre fragt niemand nach der Sitzung")
        self.assertLess(sitzung[0], neu)

    def test_die_passkey_anmeldung_zaehlt_vor_der_sperre_und_fragt_danach(self):
        """The counter on its own, the key once more under the lock, then the pair.

        Written under the lock, or held while waiting for it, the counter's
        row ran into a deadlock against a removal ('accounts.passkey.anmelden').
        """
        hilfe = MitEchtemSchluessel()
        hilfe.zoe = self.zoe
        passkey = hilfe.schluessel_des_geraets()
        antwort = hilfe.in_der_app_vorbereitet()

        abfragen = abfragen_waehrend(
            lambda: self.assertEqual(
                APIClient()
                .post(adresse("passkey_anmelden_api"), antwort, format="json")
                .status_code,
                200,
            )
        )

        gezaehlt = self.stelle(
            abfragen, lambda abfrage: abfrage[0] is Passkey and abfrage[4].startswith("UPDATE")
        )
        sperre = self.stelle(abfragen, self.konto_gesperrt)
        nachgesehen = [
            i for i, abfrage in enumerate(abfragen) if i > sperre and abfrage[0] is Passkey
        ]
        neu = self.stelle(
            abfragen,
            lambda abfrage: abfrage[0] is OutstandingToken and abfrage[4].startswith("INSERT"),
        )
        self.assertLess(gezaehlt, sperre)
        self.assertTrue(nachgesehen, "unter der Sperre fragt niemand nach dem Schlüssel")
        self.assertLess(nachgesehen[0], neu)
        self.assertEqual(Passkey.objects.get(pk=passkey.pk).signaturzaehler, 1)

    def test_alle_abmelden_sperrt_erst_die_administratoren_dann_jedes_konto_nach_nummer(self):
        administrator(email="chef@example.com")

        abfragen = abfragen_waehrend(sign_out.alle_abmelden)

        sperren = [abfrage for abfrage in abfragen if abfrage[0] is CustomUser and abfrage[1]]
        self.assertEqual(len(sperren), 2, sperren)
        administratoren, alle = sperren
        self.assertIn("is_superuser", administratoren[4])
        self.assertTrue(alle[2], "jedes Konto mit FOR UPDATE statt FOR NO KEY UPDATE")
        self.assertNotIn("WHERE", alle[4])
        self.assertEqual(alle[3], ("pk",))
        gelesen = self.stelle(abfragen, lambda abfrage: abfrage[0] is OutstandingToken)
        self.assertLess(abfragen.index(alle), gelesen)
