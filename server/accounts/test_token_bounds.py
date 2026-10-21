"""The refresh tokens of an account have bounds: how many apply at once, how many come per hour.

Until 10/2026 every sign-in and every pair from a website session issued
one more token for two weeks, without any limit, and signing the account out
loaded each that still applied as a whole row and blocked them one by one.
Measured: 100,000 tokens took 5.4 s and 225 MB under SQLite. A counter
could make every removal of a passkey or deactivation of their account run
into the worker's timeout and roll back, while it held the administrators'
rows. Now issuing blocks the oldest beyond 'HOECHSTENS_GUELTIG', an account
gets at most 'HOECHSTENS_JE_STUNDE' new ones within the hour, renewals
included, and blocking works in bounded steps ('_sperren').
"""

from datetime import timedelta
from http import HTTPStatus

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from accounts import sign_out
from accounts.models import CustomUser, Passkey
from api.tests import adresse

PASSWORT = "ziemlich-geheim-2026"
VIERZIG_MINUTEN = 40 * 60


def konto(name: str = "zoe") -> CustomUser:
    return CustomUser.objects.create_user(
        username=name,
        email=f"{name}@example.com",
        password=PASSWORT,
        first_name=name.title(),
        last_name="Probe",
    )


def anmelden(name: str = "zoe"):
    return APIClient().post(
        adresse("token_holen"), {"username": name, "password": PASSWORT}, format="json"
    )


def erneuern(refresh: str):
    return APIClient().post(adresse("token_erneuern"), {"refresh": refresh}, format="json")


def offen(benutzer: CustomUser):
    return OutstandingToken.objects.filter(user=benutzer, blacklistedtoken__isnull=True)


def ausgegeben(benutzer: CustomUser, anzahl: int, vor: timedelta = timedelta(0)) -> None:
    """Tokens the account got earlier, written as the package writes them, all valid.

    A millisecond apart, the newest 'vor' ago: thousands of them still fit into an hour.
    """
    jetzt = timezone.now() - vor
    OutstandingToken.objects.bulk_create(
        OutstandingToken(
            user=benutzer,
            jti=f"101{benutzer.pk:04d}{i:025d}",
            token="x",
            created_at=jetzt - timedelta(milliseconds=i),
            expires_at=jetzt + timedelta(days=14),
        )
        for i in range(anzahl)
    )


class HoechstensSoVieleGelten(TestCase):
    def setUp(self):
        self.zoe = konto()

    def test_die_aeltesten_ueber_der_grenze_kommen_auf_die_sperrliste(self):
        grenze = sign_out.HOECHSTENS_GUELTIG
        ausgegeben(self.zoe, grenze + 3, vor=timedelta(hours=2))
        aelteste = set(
            OutstandingToken.objects.order_by("created_at").values_list("jti", flat=True)[:4]
        )

        antwort = anmelden()

        self.assertEqual(antwort.status_code, HTTPStatus.OK)
        self.assertEqual(offen(self.zoe).count(), grenze)
        gesperrt = set(BlacklistedToken.objects.values_list("token__jti", flat=True))
        self.assertEqual(gesperrt, aelteste)
        self.assertEqual(erneuern(antwort.json()["refresh"]).status_code, HTTPStatus.OK)

    def test_jeder_weg_zu_einem_paar_haelt_die_grenze(self):
        """The password, the website's session and a new password issue through one helper."""
        grenze = sign_out.HOECHSTENS_GUELTIG
        website = APIClient()
        website.force_login(self.zoe)
        app = APIClient()
        app.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(self.zoe).access_token}")
        wege = {
            "aus der Sitzung": lambda: website.post(adresse("token_aus_sitzung")),
            "neues Passwort": lambda: app.post(
                adresse("passwort_aendern"),
                {"altes_passwort": PASSWORT, "neues_passwort": PASSWORT},
                format="json",
            ),
        }
        for weg, tun in wege.items():
            with self.subTest(weg=weg):
                OutstandingToken.objects.all().delete()
                ausgegeben(self.zoe, grenze, vor=timedelta(hours=2))

                self.assertEqual(tun().status_code, HTTPStatus.OK)

                self.assertEqual(offen(self.zoe).count(), grenze)

    def test_eine_erneuerung_tauscht_und_zaehlt_nicht_dazu(self):
        grenze = sign_out.HOECHSTENS_GUELTIG
        ausgegeben(self.zoe, grenze - 1, vor=timedelta(hours=2))
        refresh = anmelden().json()["refresh"]

        self.assertEqual(erneuern(refresh).status_code, HTTPStatus.OK)

        self.assertEqual(offen(self.zoe).count(), grenze)
        getauscht = RefreshToken(refresh, verify=False)["jti"]
        self.assertFalse(BlacklistedToken.objects.exclude(token__jti=getauscht).exists())

    def test_die_konten_der_anderen_bleiben_unberuehrt(self):
        fred = konto("fred")
        ausgegeben(fred, sign_out.HOECHSTENS_GUELTIG + 5, vor=timedelta(hours=2))

        anmelden()

        self.assertEqual(offen(fred).count(), sign_out.HOECHSTENS_GUELTIG + 5)


class HoechstensSoVieleJeStunde(TestCase):
    def setUp(self):
        self.zoe = konto()
        ausgegeben(self.zoe, sign_out.HOECHSTENS_JE_STUNDE, vor=timedelta(minutes=20))

    def pruefe_abgewiesen(self, antwort) -> None:
        self.assertEqual(antwort.status_code, HTTPStatus.TOO_MANY_REQUESTS)
        self.assertEqual(
            antwort.json()["detail"],
            "Dieses Konto hat sich in der letzten Stunde zu oft angemeldet. Bitte später noch "
            "einmal versuchen.",
        )
        # The oldest of the hour leaves the window forty minutes on.
        warten = int(antwort["Retry-After"])
        self.assertLessEqual(warten, VIERZIG_MINUTEN)
        self.assertGreater(warten, VIERZIG_MINUTEN - 60)

    def test_die_anmeldung_wird_abgewiesen(self):
        vorher = OutstandingToken.objects.count()

        self.pruefe_abgewiesen(anmelden())

        self.assertEqual(OutstandingToken.objects.count(), vorher)

    def test_die_erneuerung_ebenso(self):
        """A renewal issues a token too. The app takes a 429 there for "later"."""
        refresh = RefreshToken.for_user(self.zoe)
        OutstandingToken.objects.filter(jti=refresh["jti"]).update(
            created_at=timezone.now() - timedelta(hours=2)
        )

        self.pruefe_abgewiesen(erneuern(str(refresh)))

        self.assertFalse(BlacklistedToken.objects.exists())
        OutstandingToken.objects.exclude(jti=refresh["jti"]).update(
            created_at=timezone.now() - timedelta(hours=2)
        )
        self.assertEqual(erneuern(str(refresh)).status_code, HTTPStatus.OK)

    def test_das_paar_aus_der_sitzung_ebenso(self):
        website = APIClient()
        website.force_login(self.zoe)

        self.pruefe_abgewiesen(website.post(adresse("token_aus_sitzung")))

    def test_aeltere_zaehlen_nicht_mit(self):
        OutstandingToken.objects.update(created_at=timezone.now() - timedelta(minutes=61))

        self.assertEqual(anmelden().status_code, HTTPStatus.OK)

    def test_ein_anderes_konto_meldet_sich_an(self):
        konto("fred")

        self.assertEqual(anmelden("fred").status_code, HTTPStatus.OK)

    def test_die_grenze_liegt_weit_ueber_dem_gewoehnlichen(self):
        """A device renews about once an hour while in use, a sign-in comes once a day or so."""
        self.assertGreaterEqual(sign_out.HOECHSTENS_JE_STUNDE, 100)
        self.assertGreaterEqual(sign_out.HOECHSTENS_GUELTIG, 20)


class SperrenInBegrenztenSchritten(TestCase):
    """Signing out does not depend on how many tokens the account ever had."""

    def setUp(self):
        self.zoe = konto()

    def abfragen(self, tun) -> list[str]:
        with CaptureQueriesContext(connection) as erfasst:
            tun()
        return [abfrage["sql"] for abfrage in erfasst.captured_queries]

    def test_ein_abmelden_fragt_gleich_oft_bei_jeder_zahl_gesperrter_tokens(self):
        """Beyond the bound the tokens are blocked already. They cost the next sign-out nothing."""
        grenze = sign_out.HOECHSTENS_GUELTIG
        anzahlen = []
        for jenseits in (5, 600):
            with self.subTest(jenseits=jenseits):
                OutstandingToken.objects.all().delete()
                ausgegeben(self.zoe, grenze + jenseits, vor=timedelta(hours=2))
                self.assertEqual(anmelden().status_code, HTTPStatus.OK)
                self.assertEqual(offen(self.zoe).count(), grenze)

                abfragen = self.abfragen(lambda: sign_out.ueberall_abmelden(self.zoe))

                self.assertFalse(offen(self.zoe).exists())
                anzahlen.append(len(abfragen))
        self.assertEqual(anzahlen[0], anzahlen[1])

    def test_gelesen_wird_nur_die_nummer_und_stapelweise(self):
        """Not the whole row with the token's text, and never more than 'STAPEL' at once."""
        stapel = sign_out.STAPEL
        ausgegeben(self.zoe, 2 * stapel + 7)

        abfragen = self.abfragen(lambda: sign_out.tokens_sperren(self.zoe))

        gelesen = [
            sql
            for sql in abfragen
            if sql.startswith("SELECT") and "token_blacklist_outstandingtoken" in sql
        ]
        self.assertEqual(len(gelesen), 3)
        for sql in gelesen:
            with self.subTest(sql=sql[:80]):
                self.assertNotIn('"token_blacklist_outstandingtoken"."token"', sql)
                self.assertIn(f"LIMIT {stapel}", sql)
        self.assertFalse(offen(self.zoe).exists())
        self.assertEqual(BlacklistedToken.objects.count(), 2 * stapel + 7)

    def test_alle_abmelden_ebenso(self):
        fred = konto("fred")
        ausgegeben(self.zoe, sign_out.STAPEL + 1)
        ausgegeben(fred, 3)

        tokens, _sitzungen = sign_out.alle_abmelden()

        self.assertEqual(tokens, sign_out.STAPEL + 4)
        self.assertFalse(OutstandingToken.objects.filter(blacklistedtoken__isnull=True).exists())

    def test_ein_abgelaufenes_oder_schon_gesperrtes_bleibt_wie_es_ist(self):
        ausgegeben(self.zoe, 3)
        abgelaufen, gesperrt, gueltig = OutstandingToken.objects.order_by("pk")
        OutstandingToken.objects.filter(pk=abgelaufen.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        BlacklistedToken.objects.create(token=gesperrt)

        self.assertEqual(sign_out._sperren(OutstandingToken.objects.filter(user=self.zoe)), 1)

        self.assertEqual(
            set(BlacklistedToken.objects.values_list("token_id", flat=True)),
            {gesperrt.pk, gueltig.pk},
        )


class DieAbmeldungDerWebsiteBleibt(TestCase):
    """A sign-out on the website still works, however many tokens there were."""

    def test_entfernen_eines_passkeys(self):
        zoe = konto()
        passkey = Passkey.objects.create(
            benutzer=zoe, kennung="k101", oeffentlicher_schluessel=b"x", name="Handy"
        )
        ausgegeben(zoe, sign_out.STAPEL + 2)
        browser = self.client_class()
        browser.force_login(zoe)

        antwort = browser.post(reverse("passkey_delete", args=[passkey.pk]), {"passwort": PASSWORT})

        self.assertEqual(antwort.status_code, HTTPStatus.FOUND)
        self.assertFalse(offen(zoe).exists())
