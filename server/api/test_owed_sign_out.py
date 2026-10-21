"""The sign-out the app still owes after it signed out without network.

The device cleared itself at once. The server hears of it only on a later
start, by the website's session and its CSRF header, for the app holds no
token any more: kept, the refresh token would stay behind on a shared phone
as a sign-in. So the app names the token by its id ('jti'), and the server
blocks it if it belongs to the session's account. Whoever signed in on the
website since keeps their session ('sitzung_behalten'). Without a session
the id alone goes to 'abmelden/nachgeholt/' ('NachgeholtOhneSitzung'). The
app's half is 'fromSession' in 'app/src/services/api.js'.
"""

import json
from datetime import timedelta
from http import HTTPStatus

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import CustomUser
from api.test_merge_split_race import abfragen_waehrend
from api.tests import adresse


def konto(name: str) -> CustomUser:
    return CustomUser.objects.create_user(
        username=name,
        password="geheim",
        email=f"{name}@example.org",
        first_name=name.title(),
        last_name="Probe",
    )


def kennung(token: RefreshToken) -> str:
    return str(token[api_settings.JTI_CLAIM])


def erneuern(token: RefreshToken) -> int:
    """The status of a renewal with this refresh token, asked as the app asks."""
    rsp = APIClient().post(adresse("token_erneuern"), {"refresh": str(token)}, format="json")
    return rsp.status_code


class GeschuldeteAbmeldung(TestCase):
    def setUp(self):
        self.zoe = konto("zoe")
        self.fred = konto("fred")
        self.website = APIClient()
        self.website.force_login(self.zoe)

    def abmelden(self, **koerper):
        return self.website.post(adresse("abmelden"), koerper, format="json")

    def sitzung_offen(self) -> bool:
        return self.website.post(adresse("token_aus_sitzung")).status_code == HTTPStatus.OK

    def stelle(self, abfragen, gesucht) -> int:
        stelle = next((i for i, abfrage in enumerate(abfragen) if gesucht(abfrage)), None)
        if stelle is None:
            self.fail(f"keine solche Abfrage unter {[abfrage[4][:60] for abfrage in abfragen]}")
        return stelle

    def test_sperrt_das_eigene_token_nach_seiner_kennung(self):
        token = RefreshToken.for_user(self.zoe)

        self.assertEqual(self.abmelden(jti=kennung(token)).status_code, 204)

        self.assertEqual(erneuern(token), 401)
        self.assertFalse(self.sitzung_offen())

    def test_ein_fremdes_token_bleibt(self):
        """The id alone shows nobody that the token is theirs, so only the session's own goes."""
        fremd = RefreshToken.for_user(self.fred)

        self.assertEqual(self.abmelden(jti=kennung(fremd)).status_code, 204)

        self.assertFalse(BlacklistedToken.objects.exists())
        self.assertEqual(erneuern(fremd), 200)

    def test_sitzung_behalten_laesst_die_sitzung_stehen(self):
        """Someone signed in on the website after the sign-out the app owes, on purpose."""
        token = RefreshToken.for_user(self.zoe)

        rsp = self.abmelden(jti=kennung(token), sitzung_behalten=True)

        self.assertEqual(rsp.status_code, 204)
        self.assertEqual(erneuern(token), 401)
        self.assertTrue(self.sitzung_offen())

    def test_nur_ein_echtes_ja_behaelt_die_sitzung(self):
        for wert in ("true", 1, "ja", None):
            with self.subTest(wert=wert):
                self.website.force_login(self.zoe)
                self.assertEqual(self.abmelden(sitzung_behalten=wert).status_code, 204)
                self.assertFalse(self.sitzung_offen())

    def test_eine_unsinnige_kennung_sperrt_nichts_und_meldet_trotzdem_ab(self):
        """Signed out is signed out, whatever the app sends along as the id."""
        token = RefreshToken.for_user(self.zoe)
        for wert in (
            "",
            "x" * 256,
            101,
            ["101"],
            {"jti": "101"},
            "keine-kennung",
            kennung(token).upper(),
            kennung(token)[:-1],
            f"{kennung(token)}0",
        ):
            with self.subTest(wert=wert):
                self.website.force_login(self.zoe)
                self.assertEqual(self.abmelden(jti=wert).status_code, 204)
                self.assertFalse(self.sitzung_offen())
        self.assertFalse(BlacklistedToken.objects.exists())

    def test_ein_nul_oder_ein_halbes_zeichen_endet_nicht_in_einem_fehler(self):
        """Only an id as SimpleJWT makes it goes into the query: 32 hex digits in lower case.

        Any text of up to 255 characters went in until 10/2026. A NUL ended
        in a server error under PostgreSQL, half of a surrogate pair on every
        database, and the sign-out never happened.
        """
        token = RefreshToken.for_user(self.zoe)
        for roh in ('"\\u0000"', '"\\ud800"', f'"{kennung(token)[:31]}\\u0000"'):
            with self.subTest(roh=roh):
                self.website.force_login(self.zoe)
                rsp = self.website.post(
                    adresse("abmelden"), f'{{"jti": {roh}}}', content_type="application/json"
                )
                self.assertEqual(rsp.status_code, 204)
                self.assertFalse(self.sitzung_offen())
        self.assertEqual(erneuern(token), 200)

    def test_ein_halbes_zeichen_im_token_endet_nicht_in_einem_fehler(self):
        """A token is ASCII. One with a lone surrogate raised past 'TokenError' (until 10/2026)."""
        self.website.force_login(self.zoe)
        antwort = self.website.post(
            adresse("abmelden"), '{"refresh": "\\ud800"}', content_type="application/json"
        )
        self.assertEqual(antwort.status_code, 204)
        self.assertFalse(self.sitzung_offen())

    def test_sperrt_das_konto_bevor_es_das_token_liest(self):
        """Under the account's lock, as the renewal takes it ('accounts/sign_out.py').

        SQLite locks nothing, so the lock is read off the queries
        ('abfragen_waehrend'). Without it a renewal of the same token could
        ask the blocklist just before the entry and swap just after it, and
        its fresh token would stay valid.
        """
        token = RefreshToken.for_user(self.zoe)

        abfragen = abfragen_waehrend(lambda: self.abmelden(jti=kennung(token)))

        sperre = self.stelle(
            abfragen, lambda abfrage: abfrage[0] is CustomUser and abfrage[1] and abfrage[2]
        )
        gelesen = self.stelle(abfragen, lambda abfrage: abfrage[0] is OutstandingToken)
        self.assertLess(sperre, gelesen)


class NachgeholtOhneSitzung(TestCase):
    """The owed sign-out without a website session: the refresh token by its id alone.

    The app settles a sign-out it could not deliver on its next start. Until
    10/2026 that went only through 'abmelden', which needs the website's
    session. Signed out there or expired, nothing blocked the token, and the
    app counted the sign-out as settled. Knowing a refresh token's id means
    having held the token, and the only effect is a sign-out. So the
    endpoint asks for no sign-in, and its answer is the same for every
    well-formed id: it tells nobody whether a token exists.
    """

    def setUp(self):
        self.zoe = konto("zoe")
        self.app = APIClient()

    def nachholen(self, koerper, **optionen):
        if not isinstance(koerper, str):
            koerper = json.dumps(koerper)
        optionen.setdefault("content_type", "application/json")
        return self.app.post(adresse("abmelden_nachgeholt_api"), koerper, **optionen)

    def test_sperrt_das_token_nach_seiner_kennung(self):
        token = RefreshToken.for_user(self.zoe)

        self.assertEqual(self.nachholen({"jti": kennung(token)}).status_code, 204)

        self.assertEqual(erneuern(token), 401)

    def test_ein_token_eines_anderen_kontos_ebenso(self):
        """Who names the id held the token. There is no account to compare it with."""
        fremd = RefreshToken.for_user(konto("fred"))

        self.assertEqual(self.nachholen({"jti": kennung(fremd)}).status_code, 204)

        self.assertEqual(erneuern(fremd), 401)

    def test_eine_unbekannte_kennung_bekommt_dieselbe_antwort(self):
        rsp = self.nachholen({"jti": "101" + "0" * 29})

        self.assertEqual(rsp.status_code, 204)
        self.assertEqual(rsp.content, b"")
        self.assertFalse(BlacklistedToken.objects.exists())

    def test_ein_abgelaufenes_oder_schon_gesperrtes_token_ebenso(self):
        abgelaufen = RefreshToken.for_user(self.zoe)
        OutstandingToken.objects.filter(jti=kennung(abgelaufen)).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        gesperrt = RefreshToken.for_user(self.zoe)
        gesperrt.blacklist()

        for token in (abgelaufen, gesperrt):
            with self.subTest(token=kennung(token)):
                self.assertEqual(self.nachholen({"jti": kennung(token)}).status_code, 204)
        self.assertEqual(
            list(BlacklistedToken.objects.values_list("token__jti", flat=True)),
            [kennung(gesperrt)],
        )

    def test_alles_andere_ist_eine_400(self):
        token = RefreshToken.for_user(self.zoe)
        for koerper in (
            "kein json",
            "[]",
            '"101"',
            "null",
            {},
            {"jti": None},
            {"jti": 101},
            {"jti": [kennung(token)]},
            {"jti": kennung(token).upper()},
            {"jti": kennung(token)[:-1]},
            {"jti": kennung(token) + "0"},
            f'{{"jti": "{kennung(token)[:31]}\\u0000"}}',
            '{"jti": "\\ud800"}',
        ):
            with self.subTest(koerper=koerper):
                self.assertEqual(self.nachholen(koerper).status_code, 400)
        self.assertFalse(BlacklistedToken.objects.exists())
        self.assertEqual(erneuern(token), 200)

    def test_nur_json(self):
        """A form from a foreign page reaches no endpoint without a preflight here either."""
        token = RefreshToken.for_user(self.zoe)

        rsp = self.app.post(
            adresse("abmelden_nachgeholt_api"),
            {"jti": kennung(token)},
            format="multipart",
        )

        self.assertEqual(rsp.status_code, 400)
        self.assertEqual(erneuern(token), 200)

    def test_eine_offene_sitzung_verlangt_kein_csrf_und_bleibt(self):
        """Without authentication a parallel website session asks for no CSRF value.

        And the session is none of its business: someone may have signed in
        on the website since, on purpose.
        """
        browser = APIClient(enforce_csrf_checks=True)
        browser.force_login(self.zoe)
        token = RefreshToken.for_user(self.zoe)

        rsp = browser.post(
            adresse("abmelden_nachgeholt_api"),
            json.dumps({"jti": kennung(token)}),
            content_type="application/json",
        )

        self.assertEqual(rsp.status_code, 204)
        self.assertEqual(erneuern(token), 401)
        self.assertEqual(browser.get(adresse("ich")).status_code, 200)

    def test_sperrt_das_konto_bevor_es_das_token_sperrt(self):
        """Under the account's lock, as for a renewal ('accounts/sign_out.py')."""
        token = RefreshToken.for_user(self.zoe)

        abfragen = abfragen_waehrend(lambda: self.nachholen({"jti": kennung(token)}))

        sperre = next(
            i
            for i, abfrage in enumerate(abfragen)
            if abfrage[0] is CustomUser and abfrage[1] and abfrage[2]
        )
        eingetragen = next(
            i
            for i, abfrage in enumerate(abfragen)
            if abfrage[0] is BlacklistedToken and abfrage[4].startswith("INSERT")
        )
        nachgesehen = [
            i for i, abfrage in enumerate(abfragen) if i > sperre and abfrage[0] is OutstandingToken
        ]
        self.assertTrue(nachgesehen, "unter der Sperre fragt niemand nach dem Token")
        self.assertLess(nachgesehen[0], eingetragen)
