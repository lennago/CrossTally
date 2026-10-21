"""The API for the counting PWA.

Checked above all is what does **not** work: foreign inventories,
deactivated accounts, writing outside the count and changing everything
that is not one's own input. The counters are the first user group that
reaches the data from outside. The access rule hangs here.
"""

import threading
import time
import warnings
from datetime import date, timedelta
from decimal import Decimal
from http import HTTPStatus
from smtplib import SMTPException
from unittest import mock, skipUnless

from django.core import mail
from django.db import connection
from django.db.models.sql.compiler import SQLCompiler
from django.test import RequestFactory, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.authentication import SessionAuthentication
from rest_framework.test import APIClient, APITestCase, APITransactionTestCase
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import CustomUser, Sitzung
from api import serializers as api_serializers
from api.checks import NichtInDerZaehlung
from api.serializers import counting as counting_serializers
from django_project.testing import inventur_anlegen, jahresinventur, monatsinventur
from inventur.models import Inventur, Inventurstatus, Wiegung, Zaehlposition
from katalog.models import (
    Artikelstatus,
    Basiseinheit,
    Behaelter,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Inventurart,
    Rezepturartikel,
    Rezepturkomponente,
    Ware,
    Warenfamilie,
    Warengruppe,
)
from organisation.models import Bereich, Erscheinungsbild, Haus, Ort

STICHTAG = date(2026, 1, 31)

#: The console backend: the mail would only end in the log.
NUR_LOG = {"default": {"BACKEND": "django.core.mail.backends.console.EmailBackend"}}


def adresse(name: str, *args) -> str:
    return reverse(name, args=["v1", *args])


class ApiGrundlagen(APITestCase):
    """A house with two locations, a few products, an assigned counter."""

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Ost")
        cls.fremdes_haus = Haus.objects.create(name="Kino Süd")
        cls.theke = Ort.objects.create(
            name="Thekenfront", bereich=Bereich.objects.create(name="Theke", haus=cls.haus)
        )
        cls.lager = Ort.objects.create(
            name="Trockenlager", bereich=Bereich.objects.create(name="Lager", haus=cls.haus)
        )
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        cls.stueck = Basiseinheit.objects.get(name="Stück(e)")
        cls.gramm = Basiseinheit.objects.get(name="g")

        cls.ware = Ware.objects.create(name="Artikel C 0,33", basiseinheit=cls.stueck)
        cls.kasten = Gebinde.objects.create(
            ware=cls.ware, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        cls.einzeln = Gebinde.objects.create(
            ware=cls.ware, name="Flasche", faktor_in_basiseinheit=Decimal("1")
        )
        cls.cola = Hausartikel.objects.create(
            haus=cls.haus,
            ware=cls.ware,
            warengruppe=cls.gruppe,
            artikelnummer="101001",
            name="Artikel C 0,33 Flasche",
        )

        cls.nachos_ware = Ware.objects.create(
            name="Artikel N", basiseinheit=cls.gramm, abwiegbar=True
        )
        cls.nachos = Hausartikel.objects.create(
            haus=cls.haus,
            ware=cls.nachos_ware,
            warengruppe=cls.gruppe,
            artikelnummer="101002",
            name="Artikel N lose",
        )
        cls.schale = Behaelter.objects.create(name="Schale", tara=Decimal("250"))
        cls.nachos_ware.behaelter.add(cls.schale)

        cls.portion = Rezepturartikel.objects.create(name="Anteil N", basiseinheit=cls.stueck)
        Rezepturkomponente.objects.create(
            rezepturartikel=cls.portion,
            ware=cls.nachos_ware,
            menge_in_basiseinheit=Decimal("140"),
        )

        erwartung = Erwartung.objects.create(ort=cls.theke, artikel=cls.cola)
        ErwartungGebinde.objects.create(erwartung=erwartung, gebinde=cls.kasten)

        cls.zaehler = CustomUser.objects.create_user(
            username="zaehler",
            password="geheim",
            email="zaehler@example.org",
            first_name="Zoe",
            last_name="Zähler",
        )
        cls.zaehler.haeuser.add(cls.haus)
        cls.fremder = CustomUser.objects.create_user(
            username="fremd",
            password="geheim",
            email="fremd@example.org",
            first_name="Fred",
            last_name="Fremd",
        )

        # The running inventory, with its rows. A test that changes it works
        # on a copy per test and in a transaction that is rolled back.
        cls.inventur = inventur_anlegen(cls.haus, stichtag=STICHTAG)
        cls.inventur.zaehlpositionen_anlegen()
        cls.inventur.zaehlende.add(cls.zaehler)
        cls.inventur.zaehlung_starten()

    def anmelden(self, benutzer=None) -> str:
        """Fetches an access token like the PWA and attaches it to the client."""
        benutzer = benutzer or self.zaehler
        rsp = self.client.post(
            adresse("token_holen"), {"username": benutzer.username, "password": "geheim"}
        )
        self.assertEqual(rsp.status_code, 200, rsp.data)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {rsp.data['access']}")
        return rsp.data["refresh"]


class Anmeldung(ApiGrundlagen):
    def test_ohne_token_kein_zutritt(self):
        self.assertEqual(self.client.get(adresse("inventur-list")).status_code, 401)

    def test_keine_antwort_der_api_wird_gespeichert(self):
        """'no-store' on everything under /api/. Until 10/2026 no answer said so."""
        self.anmelden()
        rsp = self.client.get(adresse("ich"))
        self.assertIn("no-store", rsp["Cache-Control"])

    def test_das_ich_nennt_den_namen_der_anwendung(self):
        """The tab of the app shows it. The precached start page cannot (until 10/2026 it tried)."""
        self.anmelden()
        rsp = self.client.get(adresse("ich"))
        self.assertEqual(rsp.data["anwendungsname"], Erscheinungsbild.aktuell().titel)

    def test_das_ich_nennt_die_adresse_fuer_rueckmeldungen(self):
        """Without one the account menu of the app has no "Report a problem or suggestion"."""
        self.anmelden()
        self.assertEqual(self.client.get(adresse("ich")).data["rueckmeldeadresse"], "")

        fassung = Erscheinungsbild.standard().kopie("Eigene")
        fassung.rueckmeldeadresse = "rueckmeldung@example.org"
        fassung.save()
        fassung.aktivieren()

        rsp = self.client.get(adresse("ich"))
        self.assertEqual(rsp.data["rueckmeldeadresse"], "rueckmeldung@example.org")

    def test_token_und_ich(self):
        self.anmelden()
        rsp = self.client.get(adresse("ich"))
        self.assertEqual(rsp.data["benutzername"], "zaehler")
        self.assertEqual(rsp.data["haeuser"], [{"id": self.haus.pk, "name": "Kino Ost"}])
        self.assertFalse(rsp.data["ist_fuehrungskraft"])

    def test_falsches_passwort(self):
        rsp = self.client.post(
            adresse("token_holen"), {"username": "zaehler", "password": "daneben"}
        )
        self.assertEqual(rsp.status_code, 401)

    def test_zu_viele_fehlversuche_sperren_den_token_endpunkt(self):
        """The bolt sits in front of Django's 'authenticate()' and thus applies here too.

        The rejection comes as **JSON**: the app reads every response with
        'JSON.parse', and that breaks on a line of text. Its error path would
        then take the lock for a fault of the line and resubmit the job again
        and again.
        """
        for _ in range(5):
            rsp = self.client.post(
                adresse("token_holen"), {"username": "zaehler", "password": "daneben"}
            )
        self.assertEqual(rsp.status_code, 429)
        self.assertEqual(rsp["Content-Type"], "application/json")
        self.assertTrue(rsp.json()["gesperrt"])

        # Even with the right password it is closed now.
        self.assertEqual(
            self.client.post(
                adresse("token_holen"), {"username": "zaehler", "password": "geheim"}
            ).status_code,
            429,
        )

    def test_deaktiviertes_konto_kommt_nicht_mehr_durch(self):
        """The check applies on **every** request, not only on the next sign-in."""
        self.anmelden()
        self.assertEqual(self.client.get(adresse("inventur-list")).status_code, 200)

        self.zaehler.is_active = False
        self.zaehler.save(update_fields=["is_active"])
        self.assertEqual(self.client.get(adresse("inventur-list")).status_code, 401)

    def test_token_erneuern(self):
        refresh = self.anmelden()
        rsp = self.client.post(adresse("token_erneuern"), {"refresh": refresh})
        self.assertEqual(rsp.status_code, 200)
        self.assertIn("access", rsp.data)

    def test_die_anmeldung_ist_die_letzte_anmeldung(self):
        """The column "Last sign-in" knows the app too (until 10/2026 it said "never").

        Only signing in counts. The silent renewal of the token leaves the time alone.
        """
        self.assertIsNone(self.zaehler.last_login)
        refresh = self.anmelden()
        self.zaehler.refresh_from_db()
        angemeldet = self.zaehler.last_login
        self.assertIsNotNone(angemeldet)

        self.assertEqual(
            self.client.post(adresse("token_erneuern"), {"refresh": refresh}).status_code, 200
        )
        self.zaehler.refresh_from_db()
        self.assertEqual(self.zaehler.last_login, angemeldet)

    def test_altes_erneuerungstoken_gilt_nach_dem_tausch_nicht_mehr(self):
        """Rotation with blocklist: a lost token is worthless with it."""
        refresh = self.anmelden()
        self.client.post(adresse("token_erneuern"), {"refresh": refresh})
        zweiter = self.client.post(adresse("token_erneuern"), {"refresh": refresh})
        self.assertEqual(zweiter.status_code, 401)

    def test_erneuern_ohne_konto_ist_abgemeldet_und_keine_stoerung(self):
        """The app reads 401 as "sign in again" and everything from 500 as a fault
        where it waits. A deleted account must therefore not be a 500.
        """
        refresh = self.anmelden()
        self.zaehler.delete()
        rsp = self.client.post(adresse("token_erneuern"), {"refresh": refresh})
        self.assertEqual(rsp.status_code, 401)
        self.assertEqual(rsp.data["code"], "token_not_valid")


class PasswortGeaendert(ApiGrundlagen):
    """A new password ends every sign-in made with the old one.

    Whoever changes it because a phone is gone wants the phone locked out.
    Until 09/2026 it kept counting: the whole access token lifetime, two
    weeks with the refresh token.
    """

    def neues_passwort(self):
        self.zaehler.set_password("ganz-anders")
        self.zaehler.save()

    def test_das_zugriffstoken_gilt_nicht_mehr(self):
        self.anmelden()
        self.assertEqual(self.client.get(adresse("ich")).status_code, 200)

        self.neues_passwort()

        rsp = self.client.get(adresse("ich"))
        self.assertEqual(rsp.status_code, 401)
        self.assertEqual(rsp.data["code"], "password_changed")

    def test_das_erneuerungstoken_gilt_nicht_mehr(self):
        """With 401 the app forgets its tokens and shows the login form."""
        refresh = self.anmelden()
        self.neues_passwort()

        rsp = self.client.post(adresse("token_erneuern"), {"refresh": refresh})

        self.assertEqual(rsp.status_code, 401)
        self.assertEqual(rsp.data["code"], "token_not_valid")
        self.assertIn("Passwort", str(rsp.data["detail"]))

    def test_die_abgelehnte_erneuerung_tauscht_nichts(self):
        """Nothing is issued and nothing blocked for a request that fails."""
        refresh = self.anmelden()
        self.neues_passwort()
        ausgestellt = OutstandingToken.objects.count()

        self.client.post(adresse("token_erneuern"), {"refresh": refresh})

        self.assertEqual(OutstandingToken.objects.count(), ausgestellt)
        self.assertFalse(BlacklistedToken.objects.exists())

    def test_ein_token_ohne_pruefwert_gilt_nicht_mehr(self):
        """Issued before the check existed: the device signs in once more."""
        erneuerung = RefreshToken.for_user(self.zaehler)
        del erneuerung.payload["hash_password"]
        zugriff = erneuerung.access_token

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {zugriff}")
        self.assertEqual(self.client.get(adresse("ich")).status_code, 401)
        self.client.credentials()
        rsp = self.client.post(adresse("token_erneuern"), {"refresh": str(erneuerung)})
        self.assertEqual(rsp.status_code, 401)

    def test_mit_dem_neuen_passwort_geht_es_weiter(self):
        self.anmelden()
        self.neues_passwort()

        rsp = self.client.post(
            adresse("token_holen"), {"username": "zaehler", "password": "ganz-anders"}
        )
        self.assertEqual(rsp.status_code, 200)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {rsp.data['access']}")
        self.assertEqual(self.client.get(adresse("ich")).status_code, 200)
        erneuert = self.client.post(adresse("token_erneuern"), {"refresh": rsp.data["refresh"]})
        self.assertEqual(erneuert.status_code, 200)

    def test_das_token_aus_der_sitzung_traegt_den_pruefwert(self):
        self.client.force_login(self.zaehler)
        rsp = self.client.post(adresse("token_aus_sitzung"))
        self.assertEqual(rsp.status_code, 200)
        self.client.logout()

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {rsp.data['access']}")
        self.assertEqual(self.client.get(adresse("ich")).status_code, 200)
        self.neues_passwort()
        self.assertEqual(self.client.get(adresse("ich")).status_code, 401)


class PasswortAendern(ApiGrundlagen):
    """The counters change their password in the app, the web interface is closed to them.

    Checked is above all what must not happen: a change without the old
    password, guessing without a limit, and a device that locks itself out
    with its own change.
    """

    NEU = "ganz-anders-47"

    def aendern(self, alt="geheim", neu=NEU):
        return self.client.post(
            adresse("passwort_aendern"), {"altes_passwort": alt, "neues_passwort": neu}
        )

    def test_ohne_anmeldung_geht_nichts(self):
        self.assertEqual(self.aendern().status_code, 401)

    def test_die_person_bekommt_eine_mail(self):
        self.anmelden()
        self.aendern()
        self.assertEqual(mail.outbox[0].to, ["zaehler@example.org"])
        self.assertIn("in der Zähl-App geändert", mail.outbox[0].body)

    def test_das_neue_passwort_gilt(self):
        self.anmelden()

        self.assertEqual(self.aendern().status_code, 200)

        self.client.credentials()
        for passwort, erwartet in ((self.NEU, 200), ("geheim", 401)):
            with self.subTest(passwort):
                rsp = self.client.post(
                    adresse("token_holen"), {"username": "zaehler", "password": passwort}
                )
                self.assertEqual(rsp.status_code, erwartet)

    def test_das_geraet_bleibt_angemeldet(self):
        """The old tokens end with the old password. The response brings new ones."""
        self.anmelden()

        rsp = self.aendern()

        self.assertEqual(self.client.get(adresse("ich")).status_code, 401)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {rsp.data['access']}")
        self.assertEqual(self.client.get(adresse("ich")).status_code, 200)
        self.client.credentials()
        erneuert = self.client.post(adresse("token_erneuern"), {"refresh": rsp.data["refresh"]})
        self.assertEqual(erneuert.status_code, 200)

    def test_jedes_andere_geraet_ist_abgemeldet(self):
        anderes = self.anmelden()
        self.anmelden()

        self.aendern()

        self.client.credentials()
        rsp = self.client.post(adresse("token_erneuern"), {"refresh": anderes})
        self.assertEqual(rsp.status_code, 401)

    def test_die_antwort_nennt_das_passwort_nicht(self):
        self.anmelden()
        self.assertEqual(set(self.aendern().data), {"access", "refresh"})

    def test_falsches_altes_passwort(self):
        """400 and not 401: the app reads 401 as "sign in again"."""
        self.anmelden()

        rsp = self.aendern(alt="daneben")

        self.assertEqual(rsp.status_code, 400)
        self.assertEqual(list(rsp.data), ["altes_passwort"])
        self.assertEqual(self.client.get(adresse("ich")).status_code, 200)
        self.zaehler.refresh_from_db()
        self.assertTrue(self.zaehler.check_password("geheim"))

    def test_das_neue_passwort_folgt_den_regeln_der_website(self):
        self.anmelden()
        for schwach in ("kurz", "12345678901", "zaehler"):
            with self.subTest(schwach):
                rsp = self.aendern(neu=schwach)
                self.assertEqual(rsp.status_code, 400)
                self.assertEqual(list(rsp.data), ["neues_passwort"])
        self.zaehler.refresh_from_db()
        self.assertTrue(self.zaehler.check_password("geheim"))

    def test_ohne_eingabe_wird_nichts_geaendert(self):
        self.anmelden()
        rsp = self.client.post(adresse("passwort_aendern"), {})
        self.assertEqual(rsp.status_code, 400)
        self.assertEqual(set(rsp.data), {"altes_passwort", "neues_passwort"})

    def test_leerzeichen_am_rand_gehoeren_zum_passwort(self):
        """DRF trims text fields by default. A password is taken as typed."""
        self.anmelden()
        self.assertEqual(self.aendern(neu=f" {self.NEU} ").status_code, 200)
        self.zaehler.refresh_from_db()
        self.assertTrue(self.zaehler.check_password(f" {self.NEU} "))

    def test_zu_viele_fehlversuche_sperren_auch_hier(self):
        """A token on a phone left lying must not be a licence to guess.

        The rejection is the same as at the sign-in, and it holds against the
        right password too.
        """
        self.anmelden()
        for _ in range(5):
            rsp = self.aendern(alt="daneben")
        self.assertEqual(rsp.status_code, 429)
        self.assertEqual(rsp["Content-Type"], "application/json")
        self.assertTrue(rsp.json()["gesperrt"])

        self.assertEqual(self.aendern().status_code, 429)
        self.zaehler.refresh_from_db()
        self.assertTrue(self.zaehler.check_password("geheim"))

    def test_die_meldungen_kommen_in_der_sprache_des_kontos(self):
        """For Django a call with a token is anonymous and would get the server's language."""
        self.zaehler.sprache = "en"
        self.zaehler.save(update_fields=["sprache"])
        self.anmelden()

        rsp = self.aendern(alt="daneben", neu="kurz")

        self.assertEqual(rsp.data["altes_passwort"], ["The old password is wrong."])
        self.assertIn("too short", str(rsp.data["neues_passwort"]))

    def test_die_sitzung_im_selben_browser_bleibt(self):
        """Otherwise the app would sign out on its next start.

        It asks the website's session first ('token_aus_sitzung'), and if its
        sign-in came from there, a rejection means "signed out".
        """
        self.client.force_login(self.zaehler)
        self.anmelden()

        self.aendern()

        self.client.credentials()
        self.assertEqual(self.client.post(adresse("token_aus_sitzung")).status_code, 200)

    def test_die_sitzung_in_einem_anderen_browser_endet(self):
        anderer = APIClient()
        anderer.force_login(self.zaehler)
        self.assertEqual(anderer.post(adresse("token_aus_sitzung")).status_code, 200)
        self.anmelden()

        self.aendern()

        self.assertEqual(anderer.post(adresse("token_aus_sitzung")).status_code, 403)

    def test_eine_fremde_sitzung_bleibt_was_sie_war(self):
        """Someone else is signed in on the website of the same browser."""
        self.client.force_login(self.fremder)
        self.anmelden()

        self.aendern()

        self.client.credentials()
        rsp = self.client.post(adresse("token_aus_sitzung"))
        self.assertEqual(rsp.status_code, 200)
        self.assertEqual(rsp.data["ich"]["benutzername"], "fremd")


class EmailAendern(ApiGrundlagen):
    """A new address from the app: against the password, and with the link where there is mail."""

    NEU = "zoe@example.com"

    def eintragen(self, email: str = NEU, passwort: str | None = "geheim"):
        daten = {"email": email}
        if passwort is not None:
            daten["passwort"] = passwort
        return self.client.post(adresse("email_aendern_api"), daten, format="json")

    def test_ohne_token_kein_zutritt(self):
        self.assertEqual(self.eintragen().status_code, 401)

    def test_die_neue_adresse_bekommt_den_link_und_gilt_noch_nicht(self):
        self.anmelden()

        rsp = self.eintragen()

        self.assertEqual(rsp.status_code, 200, rsp.data)
        self.assertEqual(rsp.data, {"email": self.NEU, "bestaetigung": True})
        self.zaehler.refresh_from_db()
        self.assertEqual(self.zaehler.email, "zaehler@example.org")
        nachricht = mail.outbox[0]
        self.assertEqual(nachricht.to, [self.NEU])
        self.assertIn("/accounts/email/bestaetigen/", nachricht.body)
        self.assertNotIn("zaehler@example.org", nachricht.body)

    def test_ein_falsches_passwort_steht_an_seinem_feld(self):
        self.anmelden()
        rsp = self.eintragen(passwort="daneben")
        self.assertEqual(rsp.status_code, 400)
        self.assertEqual(rsp.data["passwort"], ["Das Passwort stimmt nicht."])
        self.assertEqual(mail.outbox, [])

    def test_die_adresse_eines_anderen_kontos_steht_an_ihrem_feld(self):
        self.anmelden()
        rsp = self.eintragen(email="FREMD@example.org")
        self.assertEqual(rsp.status_code, 400)
        self.assertIn("anderen Konto", rsp.data["email"][0])

    def test_ohne_nutzbares_passwort_wird_keines_verlangt(self):
        # Signed in by passkey, say: the token is made directly, there is no password to post.
        # A confirmation with a passkey stands in for it ('test_passkey.BestaetigenOhnePasswort').
        # This account has no key left, and the refusal stands at that field.
        self.zaehler.set_unusable_password()
        self.zaehler.save()
        zugang = RefreshToken.for_user(self.zaehler).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {zugang}")
        rsp = self.eintragen(passwort=None)
        self.assertEqual(rsp.status_code, 400)
        self.assertEqual(list(rsp.data), ["bestaetigung"])
        self.assertEqual(mail.outbox, [])

    def test_die_antwort_von_ich_sagt_was_der_dialog_wissen_muss(self):
        self.anmelden()
        rsp = self.client.get(adresse("ich"))
        self.assertEqual(rsp.data["email"], "zaehler@example.org")
        self.assertTrue(rsp.data["hat_passwort"])
        self.assertTrue(rsp.data["mail"])

    @override_settings(MAILERS=NUR_LOG, DEBUG=False)
    def test_ohne_mailserver_gilt_die_adresse_sofort(self):
        self.anmelden()
        self.assertFalse(self.client.get(adresse("ich")).data["mail"])

        rsp = self.eintragen()

        self.assertEqual(rsp.data, {"email": self.NEU, "bestaetigung": False})
        self.zaehler.refresh_from_db()
        self.assertEqual(self.zaehler.email, self.NEU)

    def test_ein_stummer_mailserver_laesst_die_adresse_wie_sie_war(self):
        self.anmelden()
        with mock.patch("accounts.notification.send_mail", side_effect=SMTPException):
            rsp = self.eintragen()
        self.assertEqual(rsp.status_code, 503)
        self.assertIn("ging nicht hinaus", rsp.data["detail"])
        self.zaehler.refresh_from_db()
        self.assertEqual(self.zaehler.email, "zaehler@example.org")

    def test_die_meldungen_sprechen_die_sprache_des_kontos(self):
        CustomUser.objects.filter(pk=self.zaehler.pk).update(sprache="en")
        self.anmelden()
        rsp = self.eintragen(passwort="daneben")
        self.assertEqual(rsp.data["passwort"], ["The password is wrong."])


class Darstellung(ApiGrundlagen):
    """Light or dark hangs on the account, not on the device.

    And it is the only thing one changes about oneself through the API.
    """

    def test_ich_nennt_die_darstellung(self):
        self.anmelden()
        self.assertEqual(self.client.get(adresse("ich")).data["darstellung"], "auto")

    def test_darstellung_aendern(self):
        self.anmelden()
        rsp = self.client.patch(adresse("ich"), {"darstellung": "dark"})
        self.assertEqual(rsp.status_code, 200, rsp.data)
        self.assertEqual(rsp.data["darstellung"], "dark")
        self.zaehler.refresh_from_db()
        self.assertEqual(self.zaehler.darstellung, "dark")

    def test_unbekannte_darstellung_wird_abgewiesen(self):
        self.anmelden()
        rsp = self.client.patch(adresse("ich"), {"darstellung": "lila"})
        self.assertEqual(rsp.status_code, 400)

    def test_rolle_laesst_sich_nicht_mitschicken(self):
        """Whoever wants to make themselves a manager only changes their appearance."""
        self.anmelden()
        self.client.patch(adresse("ich"), {"darstellung": "light", "is_staff": True})
        self.zaehler.refresh_from_db()
        self.assertFalse(self.zaehler.is_staff)

    def test_website_schreibt_per_sitzung_mit_csrf(self):
        """The website has no token. It sends its session and the CSRF value."""
        from rest_framework.test import APIClient  # noqa: PLC0415

        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.zaehler)
        ohne = client.patch(adresse("ich"), {"darstellung": "dark"})
        self.assertEqual(ohne.status_code, 403)

        client.get(reverse("login"))
        csrf = client.cookies["csrftoken"].value
        mit = client.patch(adresse("ich"), {"darstellung": "dark"}, HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual(mit.status_code, 200)


class AnmeldungAusDerSitzung(ApiGrundlagen):
    """Whoever is signed in on the website should not type the password twice."""

    def test_sitzung_gibt_ein_tokenpaar(self):
        self.client.force_login(self.zaehler)
        rsp = self.client.post(adresse("token_aus_sitzung"))
        self.assertEqual(rsp.status_code, 200, rsp.data)
        self.assertEqual(rsp.data["ich"]["benutzername"], "zaehler")

        self.client.logout()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {rsp.data['access']}")
        self.assertEqual(self.client.get(adresse("inventur-list")).status_code, 200)

    def test_ohne_sitzung_nichts(self):
        # 403, not 401: the session check names no scheme with which one could
        # try again. The app treats both as "not allowed".
        self.assertEqual(self.client.post(adresse("token_aus_sitzung")).status_code, 403)

    def test_ein_mitgeschicktes_token_ueberstimmt_die_sitzung_nicht(self):
        """Otherwise the app would get the old account back after a switch on the website."""
        self.anmelden()
        andere = CustomUser.objects.create_user(username="zweite", password="geheim", is_staff=True)
        self.client.force_login(andere)
        rsp = self.client.post(adresse("token_aus_sitzung"))
        self.assertEqual(rsp.data["ich"]["benutzername"], "zweite")

    def test_deaktiviertes_konto_bekommt_keins(self):
        self.client.force_login(self.zaehler)
        self.zaehler.is_active = False
        self.zaehler.save(update_fields=["is_active"])
        self.assertIn(self.client.post(adresse("token_aus_sitzung")).status_code, (401, 403))

    def test_eine_eben_beendete_sitzung_bekommt_keins(self):
        """Ended right after it was read, as a passkey removed on another device ends it.

        Until 10/2026 the pair came all the same and outlived the session.
        """
        self.client.force_login(self.zaehler)
        echt = SessionAuthentication.authenticate

        def gelesen_dann_beendet(pruefung, request):
            ergebnis = echt(pruefung, request)
            Sitzung.objects.filter(session_key=request._request.session.session_key).delete()
            return ergebnis

        with mock.patch.object(
            SessionAuthentication, "authenticate", autospec=True, side_effect=gelesen_dann_beendet
        ):
            rsp = self.client.post(adresse("token_aus_sitzung"))

        self.assertEqual(rsp.status_code, 403)
        self.assertFalse(OutstandingToken.objects.filter(user=self.zaehler).exists())


class Abmelden(ApiGrundlagen):
    def test_sperrt_das_erneuerungstoken(self):
        refresh = self.anmelden()
        self.assertEqual(
            self.client.post(adresse("abmelden"), {"refresh": refresh}).status_code, 204
        )
        self.assertEqual(
            self.client.post(adresse("token_erneuern"), {"refresh": refresh}).status_code, 401
        )

    def test_beendet_auch_die_sitzung(self):
        """A sign-out in the app must not be healed from the session right away."""
        refresh = self.anmelden()
        self.client.force_login(self.zaehler)
        self.client.post(adresse("abmelden"), {"refresh": refresh})

        self.client.credentials()
        self.assertEqual(self.client.post(adresse("token_aus_sitzung")).status_code, 403)

    def test_abmelden_ist_nachsichtig(self):
        """Signed out is signed out, whatever the app still sends along.

        Whoever signed out offline may send nothing along next time, resubmit the
        job a second time (the token is blocked by then) or carry a token that
        never was one. An error would be information about something long
        achieved, and the app would keep trying in its queue.
        """
        gesperrt = self.anmelden()
        self.client.post(adresse("abmelden"), {"refresh": gesperrt})
        for fall, koerper in (
            ("ohne Token", {}),
            ("schon gesperrtes Token", {"refresh": gesperrt}),
            ("unsinniges Token", {"refresh": "kein-echtes-token"}),
        ):
            with self.subTest(fall):
                self.anmelden()
                self.assertEqual(self.client.post(adresse("abmelden"), koerper).status_code, 204)

    def test_mit_der_sitzung_allein(self):
        """What the app sends on its next start after a sign-out the server never heard.

        No token, only the session's cookie and the CSRF header: the session
        ends. Without a session it hears 401, without the header 403, and for
        the app both mean there is nothing left to end.
        """
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(self.zaehler)
        client.get(reverse("login"))
        marke = client.cookies["csrftoken"].value

        self.assertEqual(client.post(adresse("abmelden")).status_code, 403)
        self.assertEqual(client.post(adresse("abmelden"), HTTP_X_CSRFTOKEN=marke).status_code, 204)
        self.assertEqual(
            client.post(adresse("token_aus_sitzung"), HTTP_X_CSRFTOKEN=marke).status_code, 403
        )
        self.assertEqual(
            APIClient(enforce_csrf_checks=True).post(adresse("abmelden")).status_code, 401
        )

    def test_ein_koerper_ohne_felder_meldet_trotzdem_ab(self):
        """A JSON array, a string or 'null': until 10/2026 a server error, and still signed in."""
        self.client.raise_request_exception = False
        for koerper in ("[]", '"x"', "null", "5"):
            with self.subTest(koerper=koerper):
                self.anmelden()
                self.client.force_login(self.zaehler)

                rsp = self.client.post(
                    adresse("abmelden"), koerper, content_type="application/json"
                )

                self.assertEqual(rsp.status_code, 204)
                self.client.credentials()
                self.assertEqual(self.client.post(adresse("token_aus_sitzung")).status_code, 403)


class InventurenSehen(ApiGrundlagen):
    def test_nur_zugeordnete(self):
        self.anmelden(self.fremder)
        self.assertEqual(self.client.get(adresse("inventur-list")).data, [])

        self.anmelden()
        ergebnisse = self.client.get(adresse("inventur-list")).data
        self.assertEqual([eintrag["id"] for eintrag in ergebnisse], [self.inventur.pk])
        self.assertTrue(ergebnisse[0]["zaehlbar"])
        self.assertEqual(ergebnisse[0]["anzahl_positionen"], 1)

    def test_admin_sieht_alles_ohne_zuordnung(self):
        admin = CustomUser.objects.create_superuser(
            username="admin",
            password="geheim",
            email="admin@example.org",
            first_name="Ada",
            last_name="Admin",
        )
        self.anmelden(admin)
        self.assertEqual(len(self.client.get(adresse("inventur-list")).data), 1)

    def test_fuehrungskraft_ohne_zuordnung_sieht_nichts(self):
        """Unlike in the web interface, only whoever is entered counts here."""
        chef = CustomUser.objects.create_user(
            username="chef",
            password="geheim",
            email="chef@example.org",
            first_name="Chris",
            last_name="Chef",
            is_staff=True,
        )
        self.anmelden(chef)
        self.assertEqual(self.client.get(adresse("inventur-list")).data, [])

    def test_abgeschlossene_taucht_nicht_mehr_auf(self):
        self.inventur.abschliessen()
        self.anmelden()
        self.assertEqual(self.client.get(adresse("inventur-list")).data, [])

    def test_in_vorbereitung_taucht_noch_nicht_auf(self):
        """Only "Start counting" releases. Before that the target stock is not in place."""
        self.inventur.zurueck_in_vorbereitung()
        self.anmelden()
        self.assertEqual(self.client.get(adresse("inventur-list")).data, [])
        self.assertEqual(
            self.client.get(adresse("inventur-detail", self.inventur.pk)).status_code, 404
        )

    def test_detail_bringt_die_zaehlpositionen_mit(self):
        self.anmelden()
        daten = self.client.get(adresse("inventur-detail", self.inventur.pk)).data
        self.assertEqual(daten["haus"], "Kino Ost")
        position = daten["zaehlpositionen"][0]
        self.assertEqual(position["artikel"], "Artikel C 0,33")
        self.assertEqual(position["ort"]["name"], "Thekenfront")
        self.assertEqual(position["gebinde"]["name"], "Kasten")
        self.assertEqual(position["einheit"], "STUECK")
        self.assertIsNone(position["menge_basiseinheit"])

    def test_fremde_inventur_bleibt_verborgen(self):
        self.anmelden(self.fremder)
        self.assertEqual(
            self.client.get(adresse("inventur-detail", self.inventur.pk)).status_code, 404
        )

    def test_stammdaten_fuers_offline_anlegen(self):
        self.anmelden()
        daten = self.client.get(adresse("inventur-stammdaten", self.inventur.pk)).data
        # By area, then location: "Lager" comes before "Theke".
        self.assertEqual([ort["name"] for ort in daten["orte"]], ["Trockenlager", "Thekenfront"])
        namen = {artikel["name"] for artikel in daten["artikel"]}
        self.assertEqual(namen, {"Artikel C 0,33", "Artikel N", "Anteil N"})

        cola = next(a for a in daten["artikel"] if a["name"] == "Artikel C 0,33")
        self.assertEqual({g["name"] for g in cola["gebinde"]}, {"Kasten", "Flasche"})
        nachos = next(a for a in daten["artikel"] if a["name"] == "Artikel N")
        self.assertTrue(nachos["wird_gewogen"])
        self.assertIn(self.schale.pk, nachos["behaelter"])

    def test_stammdaten_nennen_auch_orte_ohne_erwartung(self):
        """The app shows such a location from here. No row of the inventory names it."""
        self.assertFalse(Erwartung.objects.filter(ort=self.lager).exists())
        self.assertFalse(self.inventur.zaehlpositionen.filter(ort=self.lager).exists())
        self.anmelden()
        daten = self.client.get(adresse("inventur-stammdaten", self.inventur.pk)).data
        self.assertIn(
            {"id": self.lager.pk, "name": "Trockenlager", "bereich": "Lager"},
            [dict(ort) for ort in daten["orte"]],
        )

    def test_rezepturartikel_ohne_ware_im_haus_faellt_weg(self):
        """Without a resolvable recipe the counted quantity could not be distributed."""
        self.cola.deaktivieren()
        self.nachos.einstellen()
        self.anmelden()
        daten = self.client.get(adresse("inventur-stammdaten", self.inventur.pk)).data
        namen = {artikel["name"]: artikel for artikel in daten["artikel"]}
        self.assertNotIn("Anteil N", namen)
        # The inactive product stays countable, at the back, as an inactive
        # article. The discontinued one too, but as an ingredient it carries nothing.
        self.assertFalse(namen["Artikel C 0,33"]["aktiv"])
        self.assertTrue(namen["Artikel N"]["eingestellt"])

    def test_liste_der_positionen_nur_in_der_zaehlung(self):
        """Like the list of inventories. A single row, by contrast, stays reachable."""
        self.anmelden()
        position = self.inventur.zaehlpositionen.get()
        self.inventur.zurueck_in_vorbereitung()
        liste = self.client.get(adresse("zaehlposition-list"), {"inventur": self.inventur.pk})
        self.assertEqual(liste.data, [])
        self.assertEqual(
            self.client.get(adresse("zaehlposition-detail", position.pk)).status_code, 200
        )


class InaktiveArtikel(ApiGrundlagen):
    """The shelf sometimes holds seasonal goods that are currently not in the range.

    They should be countable, but only if the inventory counts their type.
    The same applies to discontinued ones as long as number and
    product are free in the house: delisted goods are still on the shelf too.
    Ignored was never a product.
    """

    def neue_ware(self, name, **abweichend):
        return Ware.objects.create(name=name, basiseinheit=self.stueck, **abweichend)

    def nicht_aktiv(self, ware, nummer, status=Artikelstatus.INAKTIV, art=None):
        return Hausartikel.objects.create(
            haus=self.haus,
            ware=ware,
            warengruppe=self.gruppe,
            artikelnummer=nummer,
            name=ware.name,
            status=status,
            inventurart=art or monatsinventur(),
        )

    def artikel(self):
        self.anmelden()
        return self.client.get(adresse("inventur-stammdaten", self.inventur.pk)).data["artikel"]

    def test_inaktive_stehen_hinter_allen_aktiven(self):
        self.nicht_aktiv(self.neue_ware("Artikel A"), "200001")
        eintraege = self.artikel()
        self.assertEqual(eintraege[-1]["name"], "Artikel A")
        self.assertFalse(eintraege[-1]["aktiv"])
        self.assertTrue(all(eintrag["aktiv"] for eintrag in eintraege[:-1]))

    def test_inaktiver_einer_anderen_art_fehlt(self):
        self.nicht_aktiv(self.neue_ware("Artikel G"), "200002", art=jahresinventur())
        self.assertNotIn("Artikel G", {eintrag["name"] for eintrag in self.artikel()})

    def ignoriert(self, name, nummer):
        ware = self.neue_ware(name, warenfamilie=Warenfamilie.nicht_zaehlbar())
        return self.nicht_aktiv(ware, nummer, Artikelstatus.IGNORIERT)

    def test_ignorierte_werden_nicht_angeboten(self):
        self.ignoriert("Artikel P", "200004")
        self.assertNotIn("Artikel P", {eintrag["name"] for eintrag in self.artikel()})

    def test_eingestellte_mit_freiem_platz_stehen_ganz_hinten(self):
        self.nicht_aktiv(self.neue_ware("Artikel A"), "200001")
        self.nicht_aktiv(self.neue_ware("Artikel 0 alt"), "200003", Artikelstatus.EINGESTELLT)
        eintraege = self.artikel()
        self.assertEqual([e["name"] for e in eintraege[-2:]], ["Artikel A", "Artikel 0 alt"])
        self.assertTrue(eintraege[-1]["eingestellt"])
        # For an app from before the change: there it stands at the back as inactive.
        self.assertFalse(eintraege[-1]["aktiv"])
        self.assertFalse(any(e["eingestellt"] for e in eintraege[:-1]))

    def test_neu_belegte_nummer_oder_ware_nimmt_den_eingestellten_heraus(self):
        """There the new row is available. Two entries could not be told apart."""
        self.nicht_aktiv(
            self.neue_ware("Artikel C alt"), self.cola.artikelnummer, Artikelstatus.EINGESTELLT
        )
        alte_cola = self.nicht_aktiv(self.cola.ware, "200010", Artikelstatus.EINGESTELLT)
        eintraege = self.artikel()
        self.assertNotIn("Artikel C alt", {e["name"] for e in eintraege})
        self.assertNotIn(alte_cola.pk, {e["id"] for e in eintraege})

    def test_auch_ein_inaktiver_einer_anderen_art_haelt_den_platz(self):
        """It is not in the list itself. The house carries the number nonetheless."""
        self.nicht_aktiv(self.neue_ware("Artikel G"), "200011", art=jahresinventur())
        self.nicht_aktiv(self.neue_ware("Artikel G alt"), "200011", Artikelstatus.EINGESTELLT)
        self.assertNotIn("Artikel G alt", {e["name"] for e in self.artikel()})

    def test_von_zwei_eingestellten_derselben_ware_kommt_der_juengere(self):
        ware = self.neue_ware("Artikel D")
        self.nicht_aktiv(ware, "200012", Artikelstatus.EINGESTELLT)
        juenger = self.nicht_aktiv(ware, "200013", Artikelstatus.EINGESTELLT)
        treffer = [e["id"] for e in self.artikel() if e["name"] == "Artikel D"]
        self.assertEqual(treffer, [juenger.pk])

    def test_eingestellter_einer_anderen_art_fehlt(self):
        self.nicht_aktiv(
            self.neue_ware("Artikel G"), "200014", Artikelstatus.EINGESTELLT, jahresinventur()
        )
        self.assertNotIn("Artikel G", {e["name"] for e in self.artikel()})

    def test_rezepturartikel_ueber_inaktive_zutat_steht_hinten(self):
        """It hangs on its ingredient: if that is inactive, so is it, and it stays countable."""
        self.nachos.deaktivieren()
        eintraege = self.artikel()
        portion = next(e for e in eintraege if e["name"] == "Anteil N")
        self.assertFalse(portion["aktiv"])
        self.assertTrue(all(e["aktiv"] for e in eintraege[: eintraege.index(portion)]))

    def test_rezepturartikel_mit_eingestellter_zutat_faellt_weg(self):
        """Discontinued is over. The share could not be booked anywhere any more."""
        self.nachos.einstellen()
        self.assertNotIn("Anteil N", {eintrag["name"] for eintrag in self.artikel()})

    def test_rezepturartikel_ueber_inaktive_zutat_einer_anderen_art_fehlt(self):
        """As with the inactive house article: only if the inventory counts the type."""
        Hausartikel.objects.filter(pk=self.nachos.pk).update(inventurart=jahresinventur())
        self.nachos.deaktivieren()
        self.assertNotIn("Anteil N", {eintrag["name"] for eintrag in self.artikel()})

    def test_rezepturartikel_ueber_inaktive_zutat_laesst_sich_zaehlen(self):
        self.nachos.deaktivieren()
        self.anmelden()
        rsp = self.client.post(
            adresse("zaehlposition-list"),
            {
                "inventur": self.inventur.pk,
                "ort": self.theke.pk,
                "artikel": self.portion.pk,
                "eingabemenge": "3",
            },
        )
        self.assertEqual(rsp.status_code, 201, rsp.data)
        # 3 portions of 140 g land on the inactive row. It thus appears in the clarification.
        self.assertEqual(self.inventur.mengen_je_hausartikel(), {self.nachos.pk: Decimal("420")})

    def test_rezepturartikel_mit_eingestellter_zutat_wird_abgewiesen(self):
        self.nachos.einstellen()
        self.anmelden()
        rsp = self.client.post(
            adresse("zaehlposition-list"),
            {"inventur": self.inventur.pk, "ort": self.theke.pk, "artikel": self.portion.pk},
        )
        self.assertEqual(rsp.status_code, 400)
        self.assertIn("artikel", rsp.data)

    def test_angebotenen_inaktiven_artikel_zaehlen(self):
        ware = self.neue_ware("Artikel A")
        kiste = Gebinde.objects.create(ware=ware, name="Kiste", faktor_in_basiseinheit=Decimal("6"))
        artikel = self.nicht_aktiv(ware, "200006")
        self.anmelden()
        rsp = self.client.post(
            adresse("zaehlposition-list"),
            {
                "inventur": self.inventur.pk,
                "ort": self.theke.pk,
                "artikel": artikel.pk,
                "gebinde": kiste.pk,
                "eingabemenge": "2",
            },
        )
        self.assertEqual(rsp.status_code, 201, rsp.data)

    def test_nicht_angebotene_werden_auch_beim_anlegen_abgewiesen(self):
        """The same rule on the server. The app is not the only one that can send."""
        gluehwein = self.nicht_aktiv(self.neue_ware("Artikel G"), "200007", art=jahresinventur())
        # Discontinued, and the number is by now carried by another row.
        ersetzt = self.nicht_aktiv(
            self.neue_ware("Artikel E"), self.cola.artikelnummer, Artikelstatus.EINGESTELLT
        )
        ignoriert = self.ignoriert("Artikel P", "200009")
        self.anmelden()
        for artikel in (gluehwein, ersetzt, ignoriert):
            with self.subTest(artikel=artikel.name):
                rsp = self.client.post(
                    adresse("zaehlposition-list"),
                    {"inventur": self.inventur.pk, "ort": self.theke.pk, "artikel": artikel.pk},
                )
                self.assertEqual(rsp.status_code, 400)
                self.assertIn("artikel", rsp.data)

    def test_eingestellter_mit_freiem_platz_laesst_sich_zaehlen(self):
        ware = self.neue_ware("Artikel E")
        kiste = Gebinde.objects.create(ware=ware, name="Kiste", faktor_in_basiseinheit=Decimal("6"))
        artikel = self.nicht_aktiv(ware, "200015", Artikelstatus.EINGESTELLT)
        self.anmelden()
        rsp = self.client.post(
            adresse("zaehlposition-list"),
            {
                "inventur": self.inventur.pk,
                "ort": self.theke.pk,
                "artikel": artikel.pk,
                "gebinde": kiste.pk,
                "eingabemenge": "2",
            },
        )
        self.assertEqual(rsp.status_code, 201, rsp.data)
        self.assertTrue(Zaehlposition.objects.get(artikel=artikel).unerwartet)


class Zaehlen(ApiGrundlagen):
    def setUp(self):
        super().setUp()
        self.anmelden()

    def position(self) -> Zaehlposition:
        return self.inventur.zaehlpositionen.get()

    def gewogene_position(self, ort) -> int:
        """A row for the nachos that go on the scale at this location."""
        rsp = self.client.post(
            adresse("zaehlposition-list"),
            {"inventur": self.inventur.pk, "ort": ort.pk, "artikel": self.nachos.pk},
        )
        self.assertEqual(rsp.status_code, 201, rsp.data)
        return rsp.data["id"]

    def test_menge_eintragen(self):
        position = self.position()
        rsp = self.client.patch(adresse("zaehlposition-detail", position.pk), {"eingabemenge": "3"})
        self.assertEqual(rsp.status_code, 200)

        position.refresh_from_db()
        self.assertEqual(position.eingabemenge, Decimal("3"))
        self.assertIsNotNone(position.gezaehlt_am)
        # 3 crates of 20. Computed on the server.
        self.assertEqual(rsp.data["menge_basiseinheit"], "60")

    def test_zuruecknehmen_loescht_den_zeitpunkt(self):
        position = self.position()
        self.client.patch(adresse("zaehlposition-detail", position.pk), {"eingabemenge": "3"})
        self.client.patch(adresse("zaehlposition-detail", position.pk), {"eingabemenge": None})

        position.refresh_from_db()
        self.assertIsNone(position.eingabemenge)
        self.assertIsNone(position.gezaehlt_am)

    def test_abgleich_ueber_zuletzt_geaendert(self):
        """The core of offline operation: the PWA fetches only what has changed."""
        vorher = timezone.now()
        self.client.patch(
            adresse("zaehlposition-detail", self.position().pk), {"eingabemenge": "1"}
        )

        rsp = self.client.get(adresse("zaehlposition-list"), {"geaendert_seit": vorher.isoformat()})
        self.assertEqual(len(rsp.data), 1)

        spaeter = timezone.now() + timedelta(seconds=1)
        leer = self.client.get(
            adresse("zaehlposition-list"), {"geaendert_seit": spaeter.isoformat()}
        )
        self.assertEqual(leer.data, [])

    def test_unlesbarer_zeitpunkt_wird_abgewiesen(self):
        rsp = self.client.get(adresse("zaehlposition-list"), {"geaendert_seit": "gestern"})
        self.assertEqual(rsp.status_code, 400)

    def test_neue_position_ist_immer_unerwartet(self):
        rsp = self.client.post(
            adresse("zaehlposition-list"),
            {
                "inventur": self.inventur.pk,
                "ort": self.lager.pk,
                "artikel": self.cola.pk,
                "gebinde": self.einzeln.pk,
                "eingabemenge": "7",
            },
        )
        self.assertEqual(rsp.status_code, 201, rsp.data)
        self.assertTrue(rsp.data["unerwartet"])
        self.assertEqual(rsp.data["menge_basiseinheit"], "7")
        self.assertEqual(self.inventur.zaehlpositionen.count(), 2)

    def test_gewogene_position_anlegen_und_wiegen(self):
        rsp = self.client.post(
            adresse("zaehlposition-list"),
            {"inventur": self.inventur.pk, "ort": self.theke.pk, "artikel": self.nachos.pk},
        )
        self.assertEqual(rsp.status_code, 201, rsp.data)
        self.assertTrue(rsp.data["wird_gewogen"])
        # Nothing on the scale yet.
        self.assertIsNone(rsp.data["menge_basiseinheit"])

        position = rsp.data["id"]
        gewogen = self.client.post(
            adresse("wiegung-list"),
            {"zaehlposition": position, "brutto_gramm": "1250", "behaelter": self.schale.pk},
        )
        self.assertEqual(gewogen.status_code, 201, gewogen.data)
        # 1250 g gross minus 250 g tare.
        self.assertEqual(gewogen.data["netto"], "1000")

    def test_mehrere_schalen_ziehen_mehrfach_tara_ab(self):
        """The case the weighings exist for."""
        position = self.gewogene_position(self.theke)
        for brutto in ("1250", "900", "1100"):
            rsp = self.client.post(
                adresse("wiegung-list"),
                {"zaehlposition": position, "brutto_gramm": brutto, "behaelter": self.schale.pk},
            )
            self.assertEqual(rsp.status_code, 201, rsp.data)

        zeile = self.client.get(adresse("zaehlposition-detail", position)).data
        self.assertEqual(len(zeile["wiegungen"]), 3)
        # 1000 + 650 + 850 - three times 250 g tare, not once.
        self.assertEqual(zeile["menge_basiseinheit"], "2500")

    def test_wiegung_loeschen_macht_die_position_wieder_offen(self):
        position = self.gewogene_position(self.theke)
        wiegung = self.client.post(
            adresse("wiegung-list"),
            {"zaehlposition": position, "brutto_gramm": "1250", "behaelter": self.schale.pk},
        ).data["id"]

        self.assertEqual(self.client.delete(adresse("wiegung-detail", wiegung)).status_code, 204)
        zeile = self.client.get(adresse("zaehlposition-detail", position)).data
        self.assertEqual(zeile["wiegungen"], [])
        self.assertIsNone(zeile["menge_basiseinheit"])

    def test_eine_berichtigung_liest_keine_mitgeschickte_zeile(self):
        """Whether a row exists stays unsaid: a correction does not even read a row sent along.

        Until 10/2026 a number that does not exist got 400 and any other was
        dropped. Over a weighing of one's own every row number could be tried.
        """
        erste = self.gewogene_position(self.theke)
        wiegung = self.client.post(
            adresse("wiegung-list"),
            {"zaehlposition": erste, "brutto_gramm": "1250", "behaelter": self.schale.pk},
        ).data["id"]
        vorhanden = self.inventur.zaehlpositionen.get(artikel=self.cola).pk
        unbekannt = Zaehlposition.objects.latest("pk").pk + 1

        antworten = [
            self.client.patch(
                adresse("wiegung-detail", wiegung),
                {"zaehlposition": nummer, "brutto_gramm": "1300"},
            )
            for nummer in (unbekannt, vorhanden)
        ]

        self.assertEqual([rsp.status_code for rsp in antworten], [200, 200], antworten[0].data)
        self.assertEqual([rsp.data["zaehlposition"] for rsp in antworten], [erste, erste])

    def test_wiegung_laesst_sich_nicht_auf_eine_andere_zeile_schieben(self):
        """Otherwise the old row would stay untouched, and full for every other device.

        Its 'zuletzt_geaendert' would not move, the quantity would be gone there,
        and the sync would notice nothing of it.
        """
        erste, zweite = (self.gewogene_position(ort) for ort in (self.theke, self.lager))
        wiegung = self.client.post(
            adresse("wiegung-list"),
            {"zaehlposition": erste, "brutto_gramm": "1250", "behaelter": self.schale.pk},
        ).data["id"]

        rsp = self.client.patch(
            adresse("wiegung-detail", wiegung),
            {"zaehlposition": zweite, "brutto_gramm": "1300"},
        )

        self.assertEqual(rsp.status_code, 200, rsp.data)
        self.assertEqual(rsp.data["zaehlposition"], erste)
        # The weight itself stays correctable.
        self.assertEqual(rsp.data["netto"], "1050")
        self.assertEqual(
            self.client.get(adresse("zaehlposition-detail", zweite)).data["wiegungen"], []
        )

    def test_gewogenes_nimmt_keine_anzahl(self):
        """Otherwise the tare would be lost, and nobody would see it."""
        position = self.gewogene_position(self.theke)
        rsp = self.client.patch(adresse("zaehlposition-detail", position), {"eingabemenge": "1250"})
        self.assertEqual(rsp.status_code, 400)
        self.assertIn("gewogen", str(rsp.data))

    def neue_gewogene_zeile_mit_anzahl(self):
        return self.client.post(
            adresse("zaehlposition-list"),
            {
                "inventur": self.inventur.pk,
                "ort": self.theke.pk,
                "artikel": self.nachos.pk,
                "eingabemenge": "5",
            },
        )

    def test_eine_neue_gewogene_zeile_nimmt_keine_anzahl(self):
        """Otherwise it counted as counted without adding a gram, and nobody saw why."""
        rsp = self.neue_gewogene_zeile_mit_anzahl()

        self.assertEqual(rsp.status_code, 400, rsp.data)
        self.assertIn("Die Menge kommt über die Wiegungen", str(rsp.data["eingabemenge"]))
        self.assertFalse(self.inventur.zaehlpositionen.filter(artikel=self.nachos).exists())

    def test_eine_wiederbelebte_gewogene_zeile_nimmt_keine_anzahl(self):
        position = self.gewogene_position(self.theke)
        self.client.delete(adresse("zaehlposition-detail", position))

        rsp = self.neue_gewogene_zeile_mit_anzahl()

        self.assertEqual(rsp.status_code, 400, rsp.data)
        self.assertIn("Die Menge kommt über die Wiegungen", str(rsp.data["eingabemenge"]))
        zeile = Zaehlposition.objects.get(pk=position)
        self.assertEqual((zeile.aktiv, zeile.eingabemenge), (False, None))

    def test_wiegung_nur_wo_gewogen_wird(self):
        rsp = self.client.post(
            adresse("wiegung-list"),
            {
                "zaehlposition": self.inventur.zaehlpositionen.get().pk,
                "brutto_gramm": "500",
                "behaelter": self.schale.pk,
            },
        )
        self.assertEqual(rsp.status_code, 400)

    def test_dieselbe_kennung_zaehlt_nur_einmal(self):
        """After a dead spot the app sends the weighing again. It must not count twice."""
        position = self.gewogene_position(self.theke)
        nutzlast = {
            "zaehlposition": position,
            "brutto_gramm": "1250",
            "behaelter": self.schale.pk,
            "kennung": "6f1b2e1c-7a3d-4c8e-9f0a-1b2c3d4e5f60",
        }
        erste = self.client.post(adresse("wiegung-list"), nutzlast)
        zweite = self.client.post(adresse("wiegung-list"), nutzlast)
        self.assertEqual((erste.status_code, zweite.status_code), (201, 201))
        self.assertEqual(erste.data["id"], zweite.data["id"])
        self.assertEqual(Wiegung.objects.filter(zaehlposition_id=position).count(), 1)

    def test_dieselbe_kennung_mit_neuem_gewicht_nimmt_das_neue(self):
        """Corrected in a dead spot: the job goes out again with the new weight.

        The server kept the first weight until 10/2026, and the app took that
        answer as the truth without a word.
        """
        position = self.gewogene_position(self.theke)
        nutzlast = {
            "zaehlposition": position,
            "brutto_gramm": "900",
            "behaelter": self.schale.pk,
            "kennung": "6f1b2e1c-7a3d-4c8e-9f0a-1b2c3d4e5f60",
        }
        erste = self.client.post(adresse("wiegung-list"), nutzlast)
        zweite = self.client.post(adresse("wiegung-list"), nutzlast | {"brutto_gramm": "950"})
        self.assertEqual((erste.status_code, zweite.status_code), (201, 201))
        self.assertEqual(erste.data["id"], zweite.data["id"])
        wiegung = Wiegung.objects.get(zaehlposition_id=position)
        self.assertEqual(wiegung.brutto_gramm, Decimal("950"))
        self.assertEqual(zweite.data["netto"], "700")

    def test_eine_fremde_zeile_im_patch_umgeht_die_sperre_nicht(self):
        """When correcting, the weighing's own row is checked, not a row sent along.

        Until 10/2026 lock and preparation were checked against the sent row:
        with a row of a second inventory the weight of a locked one changed.
        """
        gesperrt = self.gewogene_position(self.theke)
        wiegung = self.client.post(
            adresse("wiegung-list"),
            {"zaehlposition": gesperrt, "brutto_gramm": "900", "behaelter": self.schale.pk},
        ).data["id"]
        zweite = inventur_anlegen(self.haus, stichtag=STICHTAG + timedelta(days=1))
        zweite.zaehlende.add(self.zaehler)
        zweite.zaehlung_starten()
        offen = Zaehlposition.objects.create(
            inventur=zweite, ort=self.lager, artikel=self.nachos, unerwartet=True
        )
        self.inventur.sperre_setzen(True)

        rsp = self.client.patch(
            adresse("wiegung-detail", wiegung),
            {"zaehlposition": offen.pk, "brutto_gramm": "950"},
        )

        self.assertEqual(rsp.status_code, HTTPStatus.CONFLICT, rsp.data)
        self.assertEqual(Wiegung.objects.get(pk=wiegung).brutto_gramm, Decimal("900"))

    def test_dieselbe_kennung_an_einer_anderen_zeile_wird_abgewiesen(self):
        """A repetition belongs to its row. Otherwise a foreign weighing would come back."""
        erste = self.gewogene_position(self.theke)
        zweite = self.gewogene_position(self.lager)
        kennung = "6f1b2e1c-7a3d-4c8e-9f0a-1b2c3d4e5f60"
        self.client.post(
            adresse("wiegung-list"),
            {
                "zaehlposition": erste,
                "brutto_gramm": "1250",
                "behaelter": self.schale.pk,
                "kennung": kennung,
            },
        )
        rsp = self.client.post(
            adresse("wiegung-list"),
            {
                "zaehlposition": zweite,
                "brutto_gramm": "900",
                "behaelter": self.schale.pk,
                "kennung": kennung,
            },
        )
        self.assertEqual(rsp.status_code, 400, rsp.data)
        self.assertIn("kennung", rsp.data)
        self.assertEqual(Wiegung.objects.filter(kennung=kennung).count(), 1)

    def test_die_kennung_laesst_sich_nicht_umschreiben(self):
        position = self.gewogene_position(self.theke)
        wiegung = self.client.post(
            adresse("wiegung-list"),
            {
                "zaehlposition": position,
                "brutto_gramm": "1250",
                "behaelter": self.schale.pk,
                "kennung": "6f1b2e1c-7a3d-4c8e-9f0a-1b2c3d4e5f60",
            },
        ).data
        rsp = self.client.patch(
            adresse("wiegung-detail", wiegung["id"]),
            {"kennung": "00000000-0000-0000-0000-000000000000", "brutto_gramm": "1300"},
        )
        self.assertEqual(rsp.status_code, 200, rsp.data)
        self.assertEqual(rsp.data["kennung"], wiegung["kennung"])

    def test_doppelte_zeile_nennt_die_vorhandene(self):
        """The normal case when resubmitting from the offline storage. The PWA can switch."""
        vorhanden = self.position()
        rsp = self.client.post(
            adresse("zaehlposition-list"),
            {
                "inventur": self.inventur.pk,
                "ort": vorhanden.ort_id,
                "artikel": vorhanden.artikel_id,
                "gebinde": vorhanden.gebinde_id,
                "eingabemenge": "2",
            },
        )
        self.assertEqual(rsp.status_code, 400)
        # DRF wraps error values as a list of texts.
        self.assertEqual(int(rsp.data["vorhanden_id"][0]), vorhanden.pk)

    def test_ort_eines_fremden_hauses_wird_abgewiesen(self):
        woanders = Ort.objects.create(
            name="Fremde Theke",
            bereich=Bereich.objects.create(name="Theke", haus=self.fremdes_haus),
        )
        rsp = self.client.post(
            adresse("zaehlposition-list"),
            {
                "inventur": self.inventur.pk,
                "ort": woanders.pk,
                "artikel": self.cola.pk,
                "gebinde": self.einzeln.pk,
            },
        )
        self.assertEqual(rsp.status_code, 400)

    def test_fremdes_gebinde_wird_abgewiesen(self):
        andere_ware = Ware.objects.create(name="Artikel F", basiseinheit=self.stueck)
        fremdes = Gebinde.objects.create(
            ware=andere_ware, name="Kiste", faktor_in_basiseinheit=Decimal("12")
        )
        rsp = self.client.post(
            adresse("zaehlposition-list"),
            {
                "inventur": self.inventur.pk,
                "ort": self.lager.pk,
                "artikel": self.cola.pk,
                "gebinde": fremdes.pk,
            },
        )
        self.assertEqual(rsp.status_code, 400)


class WasNichtGeht(ApiGrundlagen):
    """The limits of the API. It is there for counting, for nothing else."""

    def test_in_der_vorbereitung_wird_nicht_gezaehlt(self):
        """409 and not 400: the app takes a 400 as the verdict on the job and drops
        it. Taken back into preparation, the inventory is counted again later,
        and the numbers from a dead spot wait for that ('vorlaeufig').
        """
        self.inventur.zurueck_in_vorbereitung()
        self.anmelden()
        position = self.inventur.zaehlpositionen.get()
        rsp = self.client.patch(adresse("zaehlposition-detail", position.pk), {"eingabemenge": "3"})
        self.assertEqual(rsp.status_code, HTTPStatus.CONFLICT, rsp.data)
        self.assertEqual(
            (rsp.data["konflikt"], rsp.data["status"], rsp.data["vorlaeufig"]),
            ("status", "VORBEREITUNG", True),
        )
        self.assertIn("Vorbereitung", rsp.data["detail"])
        position.refresh_from_db()
        self.assertIsNone(position.eingabemenge)

    def test_mehr_als_ein_ergebnis_fasst(self):
        """99,999,999 crates of 20 are two billion bottles: a typo, said while counting.

        The app notes the job as rejected, with this reason. Until 10/2026 the
        number went in, and the closing failed on it.
        """
        meldung = ["Das wären 1999999980 Stk., mehr als ein Ergebnis fasst. Bitte die Zahl prüfen."]
        self.anmelden()
        position = self.inventur.zaehlpositionen.get()
        rsp = self.client.patch(
            adresse("zaehlposition-detail", position.pk), {"eingabemenge": "99999999"}
        )
        self.assertEqual(rsp.status_code, HTTPStatus.BAD_REQUEST, rsp.data)
        self.assertEqual(rsp.data["eingabemenge"], meldung)
        position.refresh_from_db()
        self.assertIsNone(position.eingabemenge)

        rsp = self.client.post(
            adresse("zaehlposition-list"),
            {
                "inventur": self.inventur.pk,
                "ort": self.lager.pk,
                "artikel": self.cola.pk,
                "gebinde": self.kasten.pk,
                "eingabemenge": "99999999",
            },
        )
        self.assertEqual(rsp.status_code, HTTPStatus.BAD_REQUEST, rsp.data)
        self.assertEqual(rsp.data["eingabemenge"], meldung)
        self.assertEqual(self.inventur.zaehlpositionen.count(), 1)

    def test_jeder_schreibweg_haelt_in_der_vorbereitung_an(self):
        gewogen = Zaehlposition.objects.create(
            inventur=self.inventur, ort=self.lager, artikel=self.nachos
        )
        wiegung = Wiegung.objects.create(
            zaehlposition=gewogen, brutto_gramm=Decimal("900"), behaelter=self.schale
        )
        position = self.inventur.zaehlpositionen.get(artikel=self.cola)
        self.inventur.zurueck_in_vorbereitung()
        self.anmelden()
        faelle = {
            "Zeile anlegen": lambda: self.client.post(
                adresse("zaehlposition-list"),
                {"inventur": self.inventur.pk, "ort": self.lager.pk, "artikel": self.cola.pk},
            ),
            "Zeile löschen": lambda: self.client.delete(
                adresse("zaehlposition-detail", position.pk)
            ),
            "Wiegung anlegen": lambda: self.client.post(
                adresse("wiegung-list"),
                {"zaehlposition": gewogen.pk, "brutto_gramm": "700", "behaelter": self.schale.pk},
            ),
            "Wiegung berichtigen": lambda: self.client.patch(
                adresse("wiegung-detail", wiegung.pk), {"brutto_gramm": "950"}
            ),
            "Wiegung löschen": lambda: self.client.delete(adresse("wiegung-detail", wiegung.pk)),
        }
        for name, aufruf in faelle.items():
            with self.subTest(name):
                rsp = aufruf()
                self.assertEqual(rsp.status_code, HTTPStatus.CONFLICT, rsp.data)
                self.assertEqual((rsp.data["konflikt"], rsp.data["vorlaeufig"]), ("status", True))
        self.assertEqual(self.inventur.zaehlpositionen.count(), 2)
        self.assertEqual(gewogen.wiegungen.get().brutto_gramm, Decimal("900"))

    def test_abgeschlossen_ist_endgueltig(self):
        """Closed is frozen. The app learns that waiting makes no sense and shows the number.

        Closing clears the rows away, so a quantity for one of them meets a 404.
        A new row still reaches the check of the state.
        """
        self.inventur.abschliessen()
        self.anmelden()
        rsp = self.client.post(
            adresse("zaehlposition-list"),
            {"inventur": self.inventur.pk, "ort": self.lager.pk, "artikel": self.cola.pk},
        )
        self.assertEqual(rsp.status_code, HTTPStatus.CONFLICT, rsp.data)
        self.assertEqual(
            (rsp.data["konflikt"], rsp.data["status"], rsp.data["vorlaeufig"]),
            ("status", "ABGESCHLOSSEN", False),
        )
        self.assertIn("Abgeschlossen", rsp.data["detail"])

    def test_eine_verschwundene_zeile_sagt_es_in_worten(self):
        """Closing clears the rows. A quantity held for one meets a sentence, not Django's."""
        position = self.inventur.zaehlpositionen.get()
        self.inventur.abschliessen()
        self.anmelden()
        rsp = self.client.patch(adresse("zaehlposition-detail", position.pk), {"eingabemenge": "3"})
        self.assertEqual(rsp.status_code, 404)
        self.assertEqual(rsp.data["detail"], "Diese Zählposition ist hier nicht (mehr) bekannt.")
        rsp = self.client.post(
            adresse("wiegung-list"),
            {"zaehlposition": position.pk, "brutto_gramm": "500", "behaelter": self.schale.pk},
        )
        self.assertEqual(rsp.status_code, 400)
        self.assertEqual(
            rsp.data["zaehlposition"], ["Diese Zählposition ist hier nicht (mehr) bekannt."]
        )

    def test_fremde_position_ist_nicht_erreichbar(self):
        self.anmelden(self.fremder)
        rsp = self.client.patch(
            adresse("zaehlposition-detail", self.inventur.zaehlpositionen.get().pk),
            {"eingabemenge": "3"},
        )
        self.assertEqual(rsp.status_code, 404)

    def test_fremde_wiegungen_und_stammdaten_bleiben_verborgen(self):
        """The same wall for the scale and the master data as for the rows."""
        gewogen = Zaehlposition.objects.create(
            inventur=self.inventur, ort=self.lager, artikel=self.nachos
        )
        wiegung = Wiegung.objects.create(
            zaehlposition=gewogen, brutto_gramm=Decimal("900"), behaelter=self.schale
        )
        self.anmelden(self.fremder)

        anlegen = self.client.post(
            adresse("wiegung-list"),
            {"zaehlposition": gewogen.pk, "brutto_gramm": "700", "behaelter": self.schale.pk},
        )
        self.assertEqual(anlegen.status_code, 400, anlegen.data)
        self.assertIn("zaehlposition", anlegen.data)
        aendern = self.client.patch(adresse("wiegung-detail", wiegung.pk), {"brutto_gramm": "950"})
        self.assertEqual(aendern.status_code, 404)
        loeschen = self.client.delete(adresse("wiegung-detail", wiegung.pk))
        self.assertEqual(loeschen.status_code, 404)
        stammdaten = self.client.get(adresse("inventur-stammdaten", self.inventur.pk))
        self.assertEqual(stammdaten.status_code, 404)
        wiegung.refresh_from_db()
        self.assertEqual((wiegung.brutto_gramm, gewogen.wiegungen.count()), (Decimal("900"), 1))

    def test_token_pruefen_sagt_ob_er_noch_gilt(self):
        """Not used by the app, but open: whoever asks gets a plain yes or no."""
        token = self.anmelden()
        self.assertEqual(
            self.client.post(adresse("token_pruefen"), {"token": token}).status_code, 200
        )
        rsp = self.client.post(adresse("token_pruefen"), {"token": "kein-token"})
        self.assertEqual(rsp.status_code, 401)

    def test_position_einer_fremden_inventur_anlegen_geht_nicht(self):
        andere = inventur_anlegen(self.fremdes_haus, stichtag=STICHTAG)
        andere.zaehlung_starten()
        self.anmelden()
        rsp = self.client.post(
            adresse("zaehlposition-list"),
            {"inventur": andere.pk, "ort": self.theke.pk, "artikel": self.cola.pk},
        )
        self.assertEqual(rsp.status_code, 400)


class FremdeInventurenBeimAnlegen(ApiGrundlagen):
    """Creating a row or a weighing names no inventory the account does not count in.

    Both checked the state before the assignment, and the answer about the
    state names the inventory. Over the numbers an account assigned to nothing
    learned house, day and types of every inventory in preparation or locked
    (until 10/2026). The paths of the check list were closed before.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.regal = regal = Ort.objects.create(
            name="Regal R", bereich=Bereich.objects.create(name="Lager", haus=cls.fremdes_haus)
        )
        cls.lose = lose = Hausartikel.objects.create(
            haus=cls.fremdes_haus,
            ware=cls.nachos_ware,
            warengruppe=cls.gruppe,
            artikelnummer="101902",
            name="Artikel N lose",
        )
        cls.vorbereitung = inventur_anlegen(cls.fremdes_haus, stichtag=date(2026, 2, 28))
        cls.gesperrt = inventur_anlegen(cls.fremdes_haus, stichtag=date(2026, 3, 31))
        cls.gesperrt.zaehlung_starten()
        cls.gesperrt.sperre_setzen(True)
        cls.abgeschlossen = inventur_anlegen(cls.fremdes_haus, stichtag=date(2026, 4, 30))
        cls.abgeschlossen.zaehlung_starten()
        cls.abgeschlossen.abschliessen()
        cls.zeilen = [
            Zaehlposition.objects.create(inventur=inventur, ort=regal, artikel=lose)
            for inventur in (cls.vorbereitung, cls.gesperrt)
        ]

    def zeile_anlegen(self, inventur_pk: int, **daten):
        return self.client.post(
            adresse("zaehlposition-list"),
            {"inventur": inventur_pk, "ort": self.lager.pk, "artikel": self.cola.pk} | daten,
        )

    def wiegen(self, zeile_pk: int):
        return self.client.post(
            adresse("wiegung-list"),
            {"zaehlposition": zeile_pk, "brutto_gramm": "700", "behaelter": self.schale.pk},
        )

    def assertVerraetNichts(self, rsp, inventur: Inventur):
        """Neither house nor day nor type, in any spelling the name has."""
        text = str(rsp.json())
        for merkmal in (
            self.fremdes_haus.name,
            inventur.stichtag.strftime("%d.%m.%Y"),
            inventur.stichtag.isoformat(),
            inventur.arten_anzeige,
        ):
            self.assertNotIn(merkmal, text)

    def test_eine_neue_zeile_hoert_was_eine_unbekannte_nummer_hoert(self):
        self.anmelden(self.fremder)
        unbekannt = self.zeile_anlegen(Inventur.objects.latest("pk").pk + 1)
        self.assertEqual(unbekannt.status_code, 400)

        for inventur in (self.vorbereitung, self.gesperrt):
            with self.subTest(inventur.get_status_display()):
                rsp = self.zeile_anlegen(inventur.pk)
                self.assertEqual((rsp.status_code, rsp.json()), (400, unbekannt.json()))
                self.assertVerraetNichts(rsp, inventur)

    def test_eine_abgeschlossene_sagt_nur_dass_sie_abgeschlossen_ist(self):
        """Closing clears who counted, so a device still holding a number learns it this way."""
        self.anmelden(self.fremder)

        rsp = self.zeile_anlegen(self.abgeschlossen.pk)

        self.assertEqual(rsp.status_code, HTTPStatus.CONFLICT, rsp.data)
        self.assertEqual((rsp.data["status"], rsp.data["vorlaeufig"]), ("ABGESCHLOSSEN", False))
        self.assertVerraetNichts(rsp, self.abgeschlossen)

    def test_eine_wiegung_hoert_was_eine_unbekannte_zeile_hoert(self):
        self.anmelden(self.fremder)
        unbekannt = self.wiegen(Zaehlposition.objects.latest("pk").pk + 1)
        self.assertEqual(unbekannt.status_code, 400)

        for zeile in self.zeilen:
            with self.subTest(zeile.inventur.get_status_display()):
                rsp = self.wiegen(zeile.pk)
                self.assertEqual((rsp.status_code, rsp.json()), (400, unbekannt.json()))
                self.assertVerraetNichts(rsp, zeile.inventur)

    def test_nichts_wird_geschrieben(self):
        vorher = (Zaehlposition.objects.count(), Wiegung.objects.count())
        self.anmelden(self.fremder)

        for inventur in (self.vorbereitung, self.gesperrt, self.abgeschlossen):
            self.zeile_anlegen(inventur.pk)
        for zeile in self.zeilen:
            self.wiegen(zeile.pk)

        self.assertEqual((Zaehlposition.objects.count(), Wiegung.objects.count()), vorher)

    def test_wer_hier_zaehlt_hoert_weiter_den_namen(self):
        """The assigned counter keeps the answer about the state, name included."""
        self.vorbereitung.zaehlende.add(self.fremder)
        self.anmelden(self.fremder)

        zeile = self.zeile_anlegen(self.vorbereitung.pk, ort=self.regal.pk, artikel=self.lose.pk)
        wiegung = self.wiegen(self.zeilen[0].pk)

        for rsp in (zeile, wiegung):
            self.assertEqual(rsp.status_code, HTTPStatus.CONFLICT, rsp.data)
            self.assertTrue(rsp.data["vorlaeufig"])
            self.assertIn(self.fremdes_haus.name, rsp.data["detail"])

    def test_artikel_eines_fremden_hauses_anlegen_geht_nicht(self):
        """Otherwise closing would freeze a quantity onto a foreign house article.

        The PWA offers only articles of the house, the number is passed in
        nonetheless. The rule is on the model and thus applies on every path.
        """
        fremder_artikel = Hausartikel.objects.create(
            haus=self.fremdes_haus,
            ware=self.ware,
            warengruppe=self.gruppe,
            artikelnummer="901001",
            name="Artikel C im anderen Haus",
        )
        self.anmelden()
        rsp = self.client.post(
            adresse("zaehlposition-list"),
            {
                "inventur": self.inventur.pk,
                "ort": self.theke.pk,
                "artikel": fremder_artikel.pk,
                "gebinde": self.kasten.pk,
                "eingabemenge": "3",
            },
        )
        self.assertEqual(rsp.status_code, 400, rsp.data)
        self.assertIn("artikel", rsp.data)
        self.assertFalse(Zaehlposition.objects.filter(artikel_id=fremder_artikel.pk).exists())

    def test_an_der_inventur_selbst_laesst_sich_nichts_aendern(self):
        self.anmelden()
        ziel = adresse("inventur-detail", self.inventur.pk)
        for methode in (self.client.patch, self.client.put, self.client.delete):
            with self.subTest(methode=methode.__name__):
                self.assertEqual(methode(ziel, {"status": "ABGESCHLOSSEN"}).status_code, 405)
        self.inventur.refresh_from_db()
        self.assertEqual(self.inventur.status, Inventurstatus.ZAEHLUNG)

    def test_loeschen_legt_still_statt_zu_entfernen(self):
        """A row that simply goes missing would never arrive at the second counting app."""
        self.anmelden()
        position = self.inventur.zaehlpositionen.get()
        vorher = position.zuletzt_geaendert

        rsp = self.client.delete(adresse("zaehlposition-detail", position.pk))

        self.assertEqual(rsp.status_code, 204)
        position.refresh_from_db()
        self.assertFalse(position.aktiv)
        self.assertGreater(position.zuletzt_geaendert, vorher)

    def test_ort_und_artikel_lassen_sich_nicht_nachtraeglich_drehen(self):
        """What is counted is fixed. Whoever counts something else creates a row."""
        self.anmelden()
        position = self.inventur.zaehlpositionen.get()
        self.client.patch(
            adresse("zaehlposition-detail", position.pk),
            {"eingabemenge": "1", "ort": self.lager.pk, "artikel": self.nachos.pk},
        )
        position.refresh_from_db()
        self.assertEqual(position.ort, self.theke)
        self.assertEqual(position.artikel_id, self.cola.pk)


class FremdeHaeuserBeimAnlegen(ApiGrundlagen):
    """A new row names nothing of another house, and an inventory one does not count in stops first.

    Location and article of another house were refused only by the model's
    rule, under the lock of the foreign rows and with their names, and a
    discontinued article even gave the number and the name of its successor.
    Over the numbers a counter of one house read every house. With a second
    wrong field, a number that does not exist got another answer than an
    inventory the account does not count in. Both until 10/2026. So did a
    location or an article of another house, against one that does not
    exist: a sentence of its own, and beside a second wrong field none.
    """

    #: A number no location and no article carries.
    UNBEKANNT = 99999999

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.kuehlraum = Ort.objects.create(
            name="Kühlraum Süd",
            bereich=Bereich.objects.create(name="Lager Süd", haus=cls.fremdes_haus),
        )
        cls.fremd = Hausartikel.objects.create(
            haus=cls.fremdes_haus,
            ware=cls.ware,
            warengruppe=cls.gruppe,
            artikelnummer="101501",
            name="Artikel C Süd",
        )
        ware_r = Ware.objects.create(name="Ware R", basiseinheit=cls.stueck)
        cls.karton = Gebinde.objects.create(
            ware=ware_r, name="Karton", faktor_in_basiseinheit=Decimal("6")
        )
        cls.alt = Hausartikel.objects.create(
            haus=cls.fremdes_haus,
            ware=ware_r,
            warengruppe=cls.gruppe,
            artikelnummer="101777",
            name="Ware R alt",
        )
        cls.alt.einstellen()
        cls.nachfolger = Hausartikel.objects.create(
            haus=cls.fremdes_haus,
            ware=ware_r,
            warengruppe=cls.gruppe,
            artikelnummer="101778",
            name="Ware R neu",
        )

    def anlegen(self, **daten):
        return self.client.post(
            adresse("zaehlposition-list"),
            {
                "inventur": self.inventur.pk,
                "ort": self.theke.pk,
                "artikel": self.cola.pk,
                "gebinde": self.kasten.pk,
                **daten,
            },
        )

    def assertNenntNichts(self, rsp):
        text = str(rsp.json())
        for merkmal in (
            self.fremdes_haus.name,
            "Lager Süd",
            self.kuehlraum.name,
            self.fremd.name,
            self.alt.name,
            self.nachfolger.name,
            self.nachfolger.artikelnummer,
        ):
            self.assertNotIn(merkmal, text)

    def test_ein_ort_eines_anderen_hauses_bleibt_ungenannt(self):
        self.anmelden()

        rsp = self.anlegen(ort=self.kuehlraum.pk)

        self.assertEqual(rsp.status_code, 400)
        self.assertNenntNichts(rsp)
        self.assertEqual(rsp.json(), {"ort": [str(api_serializers.ORT_WEG)]})

    def test_ein_artikel_eines_anderen_hauses_bleibt_ungenannt(self):
        self.anmelden()
        for artikel, gebinde in ((self.fremd, self.kasten), (self.alt, self.karton)):
            with self.subTest(artikel.name):
                rsp = self.anlegen(artikel=artikel.pk, gebinde=gebinde.pk)

                self.assertEqual(rsp.status_code, 400)
                self.assertNenntNichts(rsp)
                self.assertEqual(rsp.json(), {"artikel": [str(api_serializers.ARTIKEL_WEG)]})

    def test_ein_fremder_ort_oder_artikel_hoert_was_eine_unbekannte_nummer_hoert(self):
        """Beside every other wrong field too: whether the number exists elsewhere stays unsaid."""
        self.anmelden()
        fremde = (
            ("ort", self.kuehlraum.pk),
            ("artikel", self.fremd.pk),
            ("artikel", self.alt.pk),
        )
        daneben = (
            {},
            {"eingabemenge": "viel"},
            {"gebinde": self.UNBEKANNT},
            {"ort": self.UNBEKANNT},
            {"artikel": self.UNBEKANNT},
        )
        for feld, fremd in fremde:
            for weiter in daneben:
                if feld in weiter:
                    continue
                with self.subTest(feld=feld, fremd=fremd, daneben=weiter):
                    weg = self.anlegen(**{feld: self.UNBEKANNT}, **weiter)
                    da = self.anlegen(**{feld: fremd}, **weiter)

                    self.assertEqual(weg.status_code, 400)
                    self.assertEqual((da.status_code, da.json()), (400, weg.json()))

    def test_nichts_eines_anderen_hauses_wird_gesperrt(self):
        """Refused before any lock: the rows of another house wait for no one."""
        self.anmelden()
        with (
            mock.patch.object(
                counting_serializers, "ort_sperren", wraps=api_serializers.ort_sperren
            ) as ort,
            mock.patch.object(
                counting_serializers,
                "gesperrter_artikel",
                wraps=api_serializers.gesperrter_artikel,
            ) as artikel,
        ):
            self.anlegen(ort=self.kuehlraum.pk)
            self.anlegen(artikel=self.fremd.pk)

        self.assertEqual((ort.call_count, artikel.call_count), (0, 0))
        self.assertFalse(Zaehlposition.objects.filter(unerwartet=True).exists())

    def test_das_eigene_haus_nennt_weiter_was_hilft(self):
        """A discontinued article of the own house says which row carries its number now."""
        ware = Ware.objects.create(name="Ware X", basiseinheit=self.stueck)
        kiste = Gebinde.objects.create(ware=ware, name="Kiste", faktor_in_basiseinheit=Decimal("6"))
        alt = Hausartikel.objects.create(
            haus=self.haus,
            ware=ware,
            warengruppe=self.gruppe,
            artikelnummer="101601",
            name="Ware X alt",
        )
        alt.einstellen()
        Hausartikel.objects.create(
            haus=self.haus,
            ware=ware,
            warengruppe=self.gruppe,
            artikelnummer="101602",
            name="Ware X neu",
        )
        self.anmelden()

        rsp = self.anlegen(artikel=alt.pk, gebinde=kiste.pk)

        self.assertEqual(rsp.status_code, 400)
        self.assertIn("101602", rsp.data["artikel"][0])
        self.assertIn("Ware X neu", rsp.data["artikel"][0])

    def test_eine_fremde_inventur_antwortet_gleich_was_sonst_auch_falsch_ist(self):
        self.anmelden(self.fremder)
        unbekannt = Inventur.objects.latest("pk").pk + 1
        falsch = (
            {"ort": 99999999},
            {"ort": self.kuehlraum.pk},
            {"artikel": 99999999},
            {"gebinde": 99999999},
            {"eingabemenge": "viel"},
        )
        for feld in falsch:
            with self.subTest(feld=feld):
                weg = self.anlegen(inventur=unbekannt, **feld)
                da = self.anlegen(**feld)

                self.assertEqual(
                    (weg.status_code, weg.json()),
                    (400, {"inventur": [str(api_serializers.NICHT_ZUGEORDNET)]}),
                )
                self.assertEqual((da.status_code, da.json()), (400, weg.json()))

    def test_beim_wiegen_ebenso(self):
        zeile = Zaehlposition.objects.create(
            inventur=self.inventur, ort=self.theke, artikel=self.nachos, unerwartet=True
        )
        unbekannt = Zaehlposition.objects.latest("pk").pk + 1
        self.anmelden(self.fremder)
        for feld in ({"brutto_gramm": "schwer"}, {"behaelter": 99999999}):
            with self.subTest(feld=feld):
                antworten = [
                    self.client.post(
                        adresse("wiegung-list"),
                        {
                            "zaehlposition": nummer,
                            "brutto_gramm": "900",
                            "behaelter": self.schale.pk,
                            **feld,
                        },
                    )
                    for nummer in (unbekannt, zeile.pk)
                ]

                self.assertEqual(
                    [(rsp.status_code, rsp.json()) for rsp in antworten],
                    [(400, {"zaehlposition": [str(api_serializers.UNBEKANNTE_ZEILE)]})] * 2,
                )


class SchreibweiseDerZahlen(ApiGrundlagen):
    """Every number the app writes in, in the notation it writes: a sign, digits, a point or comma.

    DRF read what Python's 'Decimal' reads, "1e5" and "1_000" too, and
    refused the comma. The app refuses the one and sends the other as a
    point. Until 10/2026 any other client wrote what no form shows. The
    refusal is DRF's own sentence for a number that is none.
    """

    UNGUELTIG = ("1e5", "1E5", "1e-5", "1_000", "0x10", "inf", "nan", "1.000,5", "1 000", "--3")

    def setUp(self):
        super().setUp()
        self.anmelden()
        self.zeile = self.inventur.zaehlpositionen.get(artikel=self.cola)
        self.waage = Zaehlposition.objects.create(
            inventur=self.inventur, ort=self.lager, artikel=self.nachos, unerwartet=True
        )
        self.meldung = [str(api_serializers.Dezimalzahl.default_error_messages["invalid"])]

    def schreiben(self, feld, wert):
        """The four ways the app writes a number, each with 'wert' in 'feld'."""
        wege = {
            "eingabemenge": lambda: self.client.patch(
                adresse("zaehlposition-detail", self.zeile.pk), {feld: wert}, format="json"
            ),
            "eingabemenge_vorher": lambda: self.client.patch(
                adresse("zaehlposition-detail", self.zeile.pk),
                {"eingabemenge": "2", feld: wert},
                format="json",
            ),
            "brutto_gramm": lambda: self.client.post(
                adresse("wiegung-list"),
                {"zaehlposition": self.waage.pk, "behaelter": self.schale.pk, feld: wert},
                format="json",
            ),
            "differenz": lambda: self.client.patch(
                adresse("pruefliste_zeile", self.inventur.pk, self.cola.pk),
                {feld: wert},
                format="json",
            ),
            "differenz_vorher": lambda: self.client.patch(
                adresse("pruefliste_zeile", self.inventur.pk, self.cola.pk),
                {"differenz": "1", feld: wert},
                format="json",
            ),
        }
        return wege[feld]()

    def test_nur_die_einfache_schreibweise(self):
        self.inventur.differenz_von_hand_setzen(True)
        felder = ("eingabemenge", "eingabemenge_vorher", "brutto_gramm", "differenz")
        for feld in (*felder, "differenz_vorher"):
            for wert in self.UNGUELTIG:
                with self.subTest(feld=feld, wert=wert):
                    rsp = self.schreiben(feld, wert)

                    self.assertEqual(rsp.status_code, 400, rsp.data)
                    self.assertEqual(rsp.json(), {feld: self.meldung})
        self.zeile.refresh_from_db()
        self.assertIsNone(self.zeile.eingabemenge)
        self.assertFalse(self.waage.wiegungen.exists())

    def test_das_komma_gilt_wie_der_punkt(self):
        self.inventur.differenz_von_hand_setzen(True)
        faelle = (
            ("eingabemenge", "1,5", 200),
            ("brutto_gramm", "900,5", 201),
            ("differenz", "-2,5", 200),
            ("eingabemenge", "+2", 200),
        )
        for feld, wert, status in faelle:
            with self.subTest(feld=feld, wert=wert):
                self.assertEqual(self.schreiben(feld, wert).status_code, status)

        self.zeile.refresh_from_db()
        self.assertEqual(self.zeile.eingabemenge, Decimal("2"))
        self.assertEqual(self.waage.wiegungen.get().brutto_gramm, Decimal("900.5"))
        self.assertEqual(self.inventur.notizen.get().differenz, Decimal("-2.5"))

    def test_eine_zahl_aus_json_zaehlt_mit_ihrem_text(self):
        rsp = self.client.patch(
            adresse("zaehlposition-detail", self.zeile.pk), {"eingabemenge": 3}, format="json"
        )

        self.assertEqual(rsp.status_code, 200, rsp.data)
        self.assertEqual(rsp.data["eingabemenge"], "3.000")


class SpracheDerAntworten(ApiGrundlagen):
    """With a token the call is anonymous for Django. The app says its language in a header.

    Until 10/2026 every message came in the server's language, and English
    counters read German refusals in the app.
    """

    def test_der_kopf_bestimmt_die_sprache_der_meldung(self):
        self.anmelden()
        position = self.inventur.zaehlpositionen.get()
        rsp = self.client.patch(
            adresse("zaehlposition-detail", position.pk), {"eingabemenge": "x"}, HTTP_X_SPRACHE="en"
        )
        self.assertEqual(rsp.status_code, 400)
        self.assertIn("valid number", str(rsp.data["eingabemenge"]))
        rsp = self.client.patch(adresse("zaehlposition-detail", position.pk), {"eingabemenge": "x"})
        self.assertIn("Zahl", str(rsp.data["eingabemenge"]))

    def test_ein_unbekannter_kopf_zaehlt_nicht(self):
        self.anmelden()
        rsp = self.client.get(adresse("inventur-list"), HTTP_X_SPRACHE="xx")
        self.assertEqual(rsp.status_code, 200)
        self.assertEqual(rsp.headers["Content-Language"], "de")


class Randfaelle(ApiGrundlagen):
    """What a device may send and the suite did not pin down until 10/2026."""

    def test_eine_menge_an_einer_geloeschten_zeile_ist_404(self):
        self.anmelden()
        position = self.inventur.zaehlpositionen.get()
        position.delete()
        rsp = self.client.patch(adresse("zaehlposition-detail", position.pk), {"eingabemenge": "3"})
        self.assertEqual(rsp.status_code, 404)

    def test_eine_als_geloescht_markierte_zeile_sagt_es(self):
        """Another device removed the row. Until 10/2026 the answer was a bare "Nicht gefunden."."""
        self.anmelden()
        position = self.inventur.zaehlpositionen.get()
        position.aktiv = False
        position.save()
        rsp = self.client.patch(adresse("zaehlposition-detail", position.pk), {"eingabemenge": "3"})
        self.assertEqual(rsp.status_code, 404)
        self.assertEqual(rsp.data["detail"], "Die Zeile wurde inzwischen entfernt.")

    def test_eine_berichtigte_wiegung_an_einer_entfernten_zeile_ist_404(self):
        """As for a quantity. Until 10/2026 it got 200, and the bowl stayed on the removed row."""
        position = Zaehlposition.objects.create(
            inventur=self.inventur, ort=self.lager, artikel=self.nachos, unerwartet=True
        )
        wiegung = Wiegung.objects.create(
            zaehlposition=position, brutto_gramm=Decimal("900"), behaelter=self.schale
        )
        position.aktiv = False
        position.save()
        self.anmelden()

        rsp = self.client.patch(adresse("wiegung-detail", wiegung.pk), {"brutto_gramm": "950"})

        self.assertEqual(rsp.status_code, 404, rsp.data)
        self.assertEqual(rsp.data["detail"], "Die Zeile wurde inzwischen entfernt.")
        wiegung.refresh_from_db()
        position.refresh_from_db()
        self.assertEqual((wiegung.brutto_gramm, position.aktiv), (Decimal("900"), False))

    def test_dieselbe_schale_an_einer_entfernten_zeile_ist_404(self):
        """Sent once more after a dead spot, the row removed in between: as for a correction.

        Until 10/2026 the answer was 201, and the bowl stayed with its new
        weight on the removed row.
        """
        position = Zaehlposition.objects.create(
            inventur=self.inventur, ort=self.lager, artikel=self.nachos, unerwartet=True
        )
        self.anmelden()
        schale = {
            "zaehlposition": position.pk,
            "behaelter": self.schale.pk,
            "kennung": "6f1b2e1c-7a3d-4c8e-9f0a-1b2c3d4e5f60",
        }
        erste = self.client.post(adresse("wiegung-list"), {**schale, "brutto_gramm": "900"})
        self.assertEqual(erste.status_code, 201, erste.data)
        position.aktiv = False
        position.save()

        rsp = self.client.post(adresse("wiegung-list"), {**schale, "brutto_gramm": "950"})

        self.assertEqual(rsp.status_code, 404, rsp.data)
        self.assertEqual(rsp.data["detail"], "Die Zeile wurde inzwischen entfernt.")
        position.refresh_from_db()
        wiegung = Wiegung.objects.get(pk=erste.data["id"])
        self.assertEqual((wiegung.brutto_gramm, position.aktiv), (Decimal("900"), False))

    def test_ein_put_schiebt_eine_wiegung_nicht_auf_eine_andere_zeile(self):
        """On which row weighing happened is fixed, also with a whole PUT."""
        gewogen = Zaehlposition.objects.create(
            inventur=self.inventur, ort=self.lager, artikel=self.nachos
        )
        andere = Zaehlposition.objects.create(
            inventur=self.inventur, ort=self.theke, artikel=self.nachos
        )
        wiegung = Wiegung.objects.create(
            zaehlposition=gewogen, brutto_gramm=Decimal("900"), behaelter=self.schale
        )
        self.anmelden()
        rsp = self.client.put(
            adresse("wiegung-detail", wiegung.pk),
            {"zaehlposition": andere.pk, "brutto_gramm": "950", "behaelter": self.schale.pk},
        )
        self.assertEqual(rsp.status_code, 200, rsp.data)
        wiegung.refresh_from_db()
        self.assertEqual(
            (wiegung.zaehlposition_id, wiegung.brutto_gramm), (gewogen.pk, Decimal("950"))
        )

    def test_ein_zeitpunkt_ohne_zone_gilt_als_serverzeit(self):
        """Accepted, and without Django's warning about a naive value in the filter."""
        self.anmelden()
        position = self.inventur.zaehlpositionen.get()
        with warnings.catch_warnings():
            # Only that warning. Any other (the short key of CI makes PyJWT warn) says
            # nothing about this case.
            warnings.filterwarnings(
                "error", message=".*received a naive datetime", category=RuntimeWarning
            )
            rsp = self.client.get(
                adresse("inventur-detail", self.inventur.pk) + "?geaendert_seit=2000-01-01T00:00:00"
            )
        self.assertEqual(rsp.status_code, 200, rsp.data)
        self.assertEqual([zeile["id"] for zeile in rsp.data["zaehlpositionen"]], [position.pk])


class SchreibenWaehrendDesAbschlusses(ApiGrundlagen):
    """The check passes while the count still runs, then the closing commits in between.

    The closing clears the rows away. Until 10/2026 the write then ended in a
    server error ('DoesNotExist'), and a weighing could even create the row anew.
    """

    def anfrage(self):
        anfrage = RequestFactory().patch("/api/v1/")
        anfrage.user = self.zaehler
        return anfrage

    def test_die_menge_bekommt_die_antwort_der_pruefung(self):
        position = self.inventur.zaehlpositionen.get()
        serializer = api_serializers.ZaehlpositionEingabeSerializer(
            instance=position, data={"eingabemenge": "3"}, context={"request": self.anfrage()}
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.inventur.abschliessen()

        with self.assertRaises(NichtInDerZaehlung) as ausnahme:
            serializer.save()
        self.assertEqual(ausnahme.exception.daten["status"], "ABGESCHLOSSEN")

    def test_die_wiegung_legt_keine_zeile_neu_an(self):
        position = Zaehlposition.objects.create(
            inventur=self.inventur, ort=self.lager, artikel=self.nachos
        )
        serializer = api_serializers.WiegungSerializer(
            data={"zaehlposition": position.pk, "brutto_gramm": "900", "behaelter": self.schale.pk},
            context={"request": self.anfrage()},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.inventur.abschliessen()

        with self.assertRaises(NichtInDerZaehlung):
            serializer.save()
        self.assertFalse(Zaehlposition.objects.filter(pk=position.pk).exists())
        self.assertFalse(Wiegung.objects.exists())


class SperreOhneAeusserenJoin(ApiGrundlagen):
    """PostgreSQL refuses to lock the nullable side of an outer join.

    'Zaehlposition.Meta.ordering' sorts by the pack, and the pack may be empty.
    Whoever locks a row and lets that ordering in gets an outer join, and every
    write fails with a server error. Until 10/2026 every weighing did, in a
    running count. SQLite locks nothing, so the lock query itself is checked
    here: the CI on PostgreSQL only sees it once the commit is pushed.
    """

    def setUp(self):
        super().setUp()
        self.anmelden()

    def gesperrte_abfragen(self, schreiben) -> list[str]:
        """The SQL of every query that locks rows while 'schreiben' runs."""
        gesehen = []
        echt = SQLCompiler.as_sql

        def aufzeichnen(compiler, *args, **kwargs):
            sql, params = echt(compiler, *args, **kwargs)
            if compiler.query.select_for_update:
                gesehen.append(sql)
            return sql, params

        with mock.patch.object(SQLCompiler, "as_sql", aufzeichnen):
            schreiben()
        return gesehen

    def assertOhneAeusserenJoin(self, abfragen: list[str]):
        self.assertTrue(abfragen, "ohne gesperrte Abfrage prüft der Test nichts")
        for sql in abfragen:
            self.assertNotIn("OUTER JOIN", sql.upper())

    def gewogene_position(self) -> int:
        rsp = self.client.post(
            adresse("zaehlposition-list"),
            {"inventur": self.inventur.pk, "ort": self.lager.pk, "artikel": self.nachos.pk},
        )
        self.assertEqual(rsp.status_code, 201, rsp.data)
        return rsp.data["id"]

    def test_neue_wiegung(self):
        position = self.gewogene_position()
        antworten = []
        abfragen = self.gesperrte_abfragen(
            lambda: antworten.append(
                self.client.post(
                    adresse("wiegung-list"),
                    {"zaehlposition": position, "brutto_gramm": "900", "behaelter": self.schale.pk},
                )
            )
        )
        self.assertEqual(antworten[0].status_code, 201, antworten[0].data)
        self.assertOhneAeusserenJoin(abfragen)

    def test_geaenderte_wiegung(self):
        wiegung = self.client.post(
            adresse("wiegung-list"),
            {
                "zaehlposition": self.gewogene_position(),
                "brutto_gramm": "900",
                "behaelter": self.schale.pk,
            },
        ).data["id"]
        antworten = []
        abfragen = self.gesperrte_abfragen(
            lambda: antworten.append(
                self.client.patch(adresse("wiegung-detail", wiegung), {"brutto_gramm": "950"})
            )
        )
        self.assertEqual(antworten[0].status_code, 200, antworten[0].data)
        self.assertOhneAeusserenJoin(abfragen)

    def test_menge(self):
        position = self.inventur.zaehlpositionen.get()
        antworten = []
        abfragen = self.gesperrte_abfragen(
            lambda: antworten.append(
                self.client.patch(
                    adresse("zaehlposition-detail", position.pk), {"eingabemenge": "3"}
                )
            )
        )
        self.assertEqual(antworten[0].status_code, 200, antworten[0].data)
        self.assertOhneAeusserenJoin(abfragen)


class Abgleich(ApiGrundlagen):
    """The stumbling block when assembling the address, and the clean form."""

    def test_die_liste_gibt_es_nur_mit_filter(self):
        """Without one the answer carried every row of every inventory of the account at once.

        The app never asks so: the rows come with their inventory. A point in
        time without a value is none, so no filter either. An empty number is
        a wrong one, as before.
        """
        self.anmelden()
        ohne = (
            "Erwartet wird „inventur“ oder „geaendert_seit“. Alle Zeilen aller Inventuren "
            "auf einmal gibt es nicht."
        )
        for filter_, feld, meldung in (
            ({}, "detail", ohne),
            ({"geaendert_seit": ""}, "detail", ohne),
            ({"inventur": ""}, "inventur", "Erwartet wird eine Nummer."),
        ):
            with self.subTest(filter_=filter_):
                rsp = self.client.get(adresse("zaehlposition-list"), filter_)
                self.assertEqual(rsp.status_code, HTTPStatus.BAD_REQUEST, rsp.data)
                self.assertEqual(rsp.data, {feld: meldung})

    def test_die_wiegungen_gibt_es_nur_mit_filter(self):
        """Every row brings its weighings along, all of them at once nobody needs."""
        self.anmelden()
        for filter_, feld, meldung in (
            (
                {},
                "detail",
                "Erwartet wird „zaehlposition“. Alle Wiegungen aller Inventuren auf einmal "
                "gibt es nicht.",
            ),
            ({"zaehlposition": ""}, "zaehlposition", "Erwartet wird eine Nummer."),
        ):
            with self.subTest(filter_=filter_):
                rsp = self.client.get(adresse("wiegung-list"), filter_)
                self.assertEqual(rsp.status_code, HTTPStatus.BAD_REQUEST, rsp.data)
                self.assertEqual(rsp.data, {feld: meldung})

    def test_gefiltert_bleiben_die_wiegungen_wie_sie_waren(self):
        """The weighings of one row, each as it is fetched alone."""
        self.anmelden()
        position = Zaehlposition.objects.create(
            inventur=self.inventur, ort=self.lager, artikel=self.nachos
        )
        wiegung = Wiegung.objects.create(
            zaehlposition=position, brutto_gramm=Decimal("900"), behaelter=self.schale
        )

        rsp = self.client.get(adresse("wiegung-list"), {"zaehlposition": position.pk})

        self.assertEqual(rsp.status_code, HTTPStatus.OK, rsp.data)
        self.assertEqual(rsp.data, [self.client.get(adresse("wiegung-detail", wiegung.pk)).data])

    def test_gefiltert_bleibt_die_antwort_wie_sie_war(self):
        """One inventory, what changed since a point in time, or both: rows as one by one."""
        self.anmelden()
        position = self.inventur.zaehlpositionen.get()
        vorher = timezone.now()
        self.client.patch(adresse("zaehlposition-detail", position.pk), {"eingabemenge": "1"})
        einzeln = self.client.get(adresse("zaehlposition-detail", position.pk)).data

        for filter_ in (
            {"inventur": self.inventur.pk},
            {"geaendert_seit": vorher.isoformat()},
            {"inventur": self.inventur.pk, "geaendert_seit": vorher.isoformat()},
        ):
            with self.subTest(filter_=filter_):
                rsp = self.client.get(adresse("zaehlposition-list"), filter_)
                self.assertEqual(rsp.status_code, HTTPStatus.OK, rsp.data)
                self.assertEqual(rsp.data, [einzeln])

    def test_zeitzone_mit_und_ohne_kodierung(self):
        self.anmelden()
        marke = timezone.now() - timedelta(minutes=1)
        self.client.patch(
            adresse("zaehlposition-detail", self.inventur.zaehlpositionen.get().pk),
            {"eingabemenge": "1"},
        )

        for rohwert in (marke.isoformat(), marke.isoformat().replace("+", " ")):
            with self.subTest(rohwert=rohwert):
                rsp = self.client.get(f"{adresse('zaehlposition-list')}?geaendert_seit={rohwert}")
                self.assertEqual(rsp.status_code, 200)
                self.assertEqual(len(rsp.data), 1)

    def test_unlesbare_filter_werden_abgewiesen(self):
        """An app that assembles its filter wrongly would otherwise silently get too
        little, or everything. It notices neither.
        """
        self.anmelden()
        faelle = (
            (adresse("zaehlposition-list"), {"inventur": "alle"}, "inventur"),
            (adresse("wiegung-list"), {"zaehlposition": "letzte"}, "zaehlposition"),
            (adresse("zaehlposition-list"), {"geaendert_seit": "gestern"}, "geaendert_seit"),
        )
        for ziel, filter_, feld in faelle:
            with self.subTest(feld=feld):
                rsp = self.client.get(ziel, filter_)
                self.assertEqual(rsp.status_code, 400, rsp.data)
                self.assertIn(feld, rsp.data)

    def test_ein_tag_den_es_nicht_gibt_wird_abgewiesen(self):
        """Well-formed, but no day of the calendar. Until 10/2026 that was a server error."""
        self.anmelden()
        for ziel in (adresse("zaehlposition-list"), adresse("inventur-detail", self.inventur.pk)):
            with self.subTest(ziel=ziel):
                rsp = self.client.get(ziel, {"geaendert_seit": "2026-02-30T00:00:00"})

                self.assertEqual(rsp.status_code, 400, rsp.data)
                self.assertIn("geaendert_seit", rsp.data)

    def test_zuletzt_geaendert_wandert_mit_jeder_eingabe(self):
        self.anmelden()
        position = self.inventur.zaehlpositionen.get()
        vorher = position.zuletzt_geaendert

        self.client.patch(adresse("zaehlposition-detail", position.pk), {"eingabemenge": "1"})
        position.refresh_from_db()
        self.assertGreater(position.zuletzt_geaendert, vorher)


class GeloeschteZaehlpositionen(ApiGrundlagen):
    """Deleting means marking as deleted, or the other counting app would never learn of it."""

    def test_als_geloescht_markierte_fehlen_in_der_liste(self):
        self.anmelden()
        position = self.inventur.zaehlpositionen.get()
        self.client.delete(adresse("zaehlposition-detail", position.pk))

        daten = self.client.get(adresse("zaehlposition-list"), {"inventur": self.inventur.pk}).data

        self.assertEqual(daten, [])

    def test_beim_abgleich_kommt_die_loeschung_mit(self):
        """Only by that does the app recognise that it has to clear the row."""
        self.anmelden()
        position = self.inventur.zaehlpositionen.get()
        vorher = timezone.now()
        self.client.delete(adresse("zaehlposition-detail", position.pk))

        daten = self.client.get(
            adresse("inventur-detail", self.inventur.pk),
            {"geaendert_seit": vorher.isoformat()},
        ).data

        zeilen = daten["zaehlpositionen"]
        self.assertEqual([zeile["id"] for zeile in zeilen], [position.pk])
        self.assertFalse(zeilen[0]["aktiv"])

    def test_ohne_zeitpunkt_bleibt_die_geloeschte_draussen(self):
        """On the first load it would only be ballast, since it is deleted after all."""
        self.anmelden()
        position = self.inventur.zaehlpositionen.get()
        self.client.delete(adresse("zaehlposition-detail", position.pk))

        daten = self.client.get(adresse("inventur-detail", self.inventur.pk)).data

        self.assertEqual(daten["zaehlpositionen"], [])

    def test_geloeschte_zaehlen_nicht_mehr_mit(self):
        """Neither in the progress nor in the result. The row is gone."""
        self.anmelden()
        position = self.inventur.zaehlpositionen.get()
        self.client.patch(adresse("zaehlposition-detail", position.pk), {"eingabemenge": "2"})
        self.client.delete(adresse("zaehlposition-detail", position.pk))

        kopf = self.client.get(adresse("inventur-list")).data[0]

        self.assertEqual(kopf["anzahl_positionen"], 0)
        self.assertEqual(kopf["anzahl_gezaehlt"], 0)
        self.assertEqual(self.inventur.mengen_je_hausartikel(), {})

    def test_dieselbe_zeile_kommt_mit_neuem_wert_zurueck(self):
        """Whoever creates it again revives it. A second row would not work."""
        self.anmelden()
        position = self.inventur.zaehlpositionen.get()
        self.client.patch(adresse("zaehlposition-detail", position.pk), {"eingabemenge": "2"})
        self.client.delete(adresse("zaehlposition-detail", position.pk))

        rsp = self.client.post(
            adresse("zaehlposition-list"),
            {
                "inventur": self.inventur.pk,
                "ort": position.ort_id,
                "artikel": position.artikel_id,
                "gebinde": position.gebinde_id,
                "eingabemenge": "5",
            },
        )

        self.assertEqual(rsp.status_code, 201, rsp.data)
        self.assertEqual(rsp.data["id"], position.pk)
        position.refresh_from_db()
        self.assertTrue(position.aktiv)
        self.assertEqual(position.eingabemenge, Decimal("5.000"))
        self.assertEqual(Zaehlposition.objects.filter(inventur=self.inventur).count(), 1)

    def test_eine_aktive_zeile_bleibt_eine_dublette(self):
        """Only the one marked as deleted is revived. Otherwise it stays with the notice."""
        self.anmelden()
        position = self.inventur.zaehlpositionen.get()

        rsp = self.client.post(
            adresse("zaehlposition-list"),
            {
                "inventur": self.inventur.pk,
                "ort": position.ort_id,
                "artikel": position.artikel_id,
                "gebinde": position.gebinde_id,
                "eingabemenge": "5",
            },
        )

        self.assertEqual(rsp.status_code, 400)
        self.assertEqual(rsp.data["vorhanden_id"], [str(position.pk)])

    def test_geloescht_wird_nur_in_der_zaehlung(self):
        self.anmelden()
        position = self.inventur.zaehlpositionen.get()
        self.inventur.zurueck_in_vorbereitung()

        rsp = self.client.delete(adresse("zaehlposition-detail", position.pk))

        # 409, not 400: the app keeps the job for the count to come ('pruefe_zaehlbar').
        self.assertEqual(rsp.status_code, HTTPStatus.CONFLICT)
        position.refresh_from_db()
        self.assertTrue(position.aktiv)

    def test_wiederbelebte_zeile_bleibt_erwartet(self):
        """A row from the counting list, deleted and recreated, is no unexpected find.

        Otherwise the clarification before closing would overlook it in block 3.
        """
        self.anmelden()
        position = self.inventur.zaehlpositionen.get()
        self.client.delete(adresse("zaehlposition-detail", position.pk))

        rsp = self.client.post(
            adresse("zaehlposition-list"),
            {
                "inventur": self.inventur.pk,
                "ort": position.ort_id,
                "artikel": position.artikel_id,
                "gebinde": position.gebinde_id,
            },
        )

        self.assertEqual(rsp.status_code, 201, rsp.data)
        self.assertFalse(rsp.data["unerwartet"])

    def test_eine_wiegung_belebt_die_geloeschte_zeile_wieder(self):
        """As when creating a row: what was weighed in it before no longer applies."""
        self.anmelden()
        position = self.client.post(
            adresse("zaehlposition-list"),
            {"inventur": self.inventur.pk, "ort": self.theke.pk, "artikel": self.nachos.pk},
        ).data["id"]
        self.client.post(
            adresse("wiegung-list"),
            {"zaehlposition": position, "brutto_gramm": "1250", "behaelter": self.schale.pk},
        )
        self.client.delete(adresse("zaehlposition-detail", position))

        rsp = self.client.post(
            adresse("wiegung-list"),
            {"zaehlposition": position, "brutto_gramm": "900", "behaelter": self.schale.pk},
        )

        self.assertEqual(rsp.status_code, 201, rsp.data)
        zeile = self.client.get(adresse("zaehlposition-detail", position)).data
        self.assertTrue(zeile["aktiv"])
        self.assertEqual(len(zeile["wiegungen"]), 1)
        self.assertEqual(zeile["menge_basiseinheit"], "650")


class WiegungAnEinerEntferntenZeile(ApiGrundlagen):
    """A bowl brings a removed row back only where creating the row would work.

    Loose goods of their own: the product of 'nachos' is in a recipe and
    cannot be ignored.
    """

    def setUp(self):
        super().setUp()
        self.lose = Ware.objects.create(name="Ware L", basiseinheit=self.gramm, abwiegbar=True)
        self.lose.behaelter.add(self.schale)
        self.lose_artikel = Hausartikel.objects.create(
            haus=self.haus,
            ware=self.lose,
            warengruppe=self.gruppe,
            artikelnummer="101003",
            name="Ware L lose",
        )
        self.waage = Zaehlposition.objects.create(
            inventur=self.inventur, ort=self.lager, artikel=self.lose_artikel
        )
        self.anmelden()

    def wiegen(self):
        return self.client.post(
            adresse("wiegung-list"),
            {"zaehlposition": self.waage.pk, "brutto_gramm": "1250", "behaelter": self.schale.pk},
        )

    def pruefe_abgelehnt(self, rsp, *teile: str) -> None:
        """400 on the article, in the words the new row would hear, and nothing counts."""
        self.assertEqual(rsp.status_code, HTTPStatus.BAD_REQUEST, rsp.data)
        meldung = " ".join(str(text) for text in rsp.data["artikel"])
        for teil in teile:
            self.assertIn(teil, meldung)
        self.waage.refresh_from_db()
        self.assertFalse(self.waage.aktiv)
        self.assertFalse(self.waage.wiegungen.exists())
        self.assertNotIn(self.lose_artikel.pk, self.inventur.mengen_je_hausartikel())

    def test_nicht_bei_einem_nicht_zaehlbaren_artikel(self):
        """Ignoring took the uncounted row out. The bowl must not bring it into the result."""
        self.lose.ignorieren()
        self.waage.refresh_from_db()
        self.assertFalse(self.waage.aktiv)

        self.pruefe_abgelehnt(self.wiegen(), "nicht zählbar")

    def test_nicht_bei_einem_eingestellten_dessen_platz_ein_nachfolger_hat(self):
        """The count belongs to the successor, as when the row is created anew."""
        Hausartikel.objects.get(pk=self.lose_artikel.pk).einstellen()
        Hausartikel.objects.create(
            haus=self.haus,
            ware=self.lose,
            warengruppe=self.gruppe,
            artikelnummer="101004",
            name="Ware L neu",
        )
        geloescht = self.client.delete(adresse("zaehlposition-detail", self.waage.pk))
        self.assertEqual(geloescht.status_code, HTTPStatus.NO_CONTENT)

        self.pruefe_abgelehnt(self.wiegen(), "eingestellt", "101004")

    def test_bei_einem_aktiven_kommt_sie_wie_bisher_zurueck(self):
        self.client.delete(adresse("zaehlposition-detail", self.waage.pk))

        rsp = self.wiegen()

        self.assertEqual(rsp.status_code, HTTPStatus.CREATED, rsp.data)
        self.waage.refresh_from_db()
        self.assertTrue(self.waage.aktiv)
        self.assertEqual(self.waage.wiegungen.count(), 1)


class BerichtigteWiegungUnterDerSperre(ApiGrundlagen):
    """A correction writes onto the weighing as it stands once its row is locked.

    What another device does between loading and locking is done here by
    hand. The overlap itself is forced in 'test_weighing_race'.
    """

    def setUp(self):
        super().setUp()
        self.becher = Behaelter.objects.create(name="Becher", tara=Decimal("50"))
        self.nachos_ware.behaelter.add(self.becher)
        self.waage = Zaehlposition.objects.create(
            inventur=self.inventur, ort=self.lager, artikel=self.nachos
        )
        self.wiegung = Wiegung.objects.create(
            zaehlposition=self.waage, brutto_gramm=Decimal("900"), behaelter=self.schale
        )
        self.anmelden()

    def vor_der_sperre(self, tun):
        """'tun' happens after the weighing was loaded and checked, before its row is locked.

        Outside the transaction of the save: another device commits on its own.
        """
        echt = counting_serializers.WiegungSerializer.validate

        def pruefen_und_tun(serializer, daten):
            daten = echt(serializer, daten)
            tun()
            return daten

        return mock.patch.object(
            counting_serializers.WiegungSerializer,
            "validate",
            autospec=True,
            side_effect=pruefen_und_tun,
        )

    def berichtigen(self):
        return self.client.patch(
            adresse("wiegung-detail", self.wiegung.pk), {"brutto_gramm": "950"}
        )

    def test_eine_inzwischen_geloeschte_wiegung_kommt_nicht_wieder(self):
        """404, as for a weighing deleted before. Until 10/2026 it was inserted again."""
        with self.vor_der_sperre(lambda: Wiegung.objects.filter(pk=self.wiegung.pk).delete()):
            rsp = self.berichtigen()

        self.assertEqual(rsp.status_code, HTTPStatus.NOT_FOUND, rsp.data)
        self.assertFalse(Wiegung.objects.filter(pk=self.wiegung.pk).exists())

    def test_nur_das_gewicht_wird_geschrieben(self):
        """Another device took the cup meanwhile. The weight changes here, the cup stays."""
        with self.vor_der_sperre(
            lambda: Wiegung.objects.filter(pk=self.wiegung.pk).update(behaelter=self.becher)
        ):
            rsp = self.berichtigen()

        self.assertEqual(rsp.status_code, HTTPStatus.OK, rsp.data)
        self.wiegung.refresh_from_db()
        self.assertEqual(
            (self.wiegung.brutto_gramm, self.wiegung.behaelter_id), (Decimal("950"), self.becher.pk)
        )
        self.assertEqual(rsp.data["netto"], "900")


class ErwarteteMenge(ApiGrundlagen):
    """The usual refill stock stands on the row as an input aid."""

    def setUp(self):
        super().setUp()
        vorgabe = self.cola.erwartet_an.get(ort=self.theke).gebinde_vorgaben.get()
        vorgabe.standard_menge = Decimal("6")
        vorgabe.save()

    def test_vorgabe_kommt_mit(self):
        self.anmelden()
        daten = self.client.get(adresse("inventur-detail", self.inventur.pk)).data
        zeile = daten["zaehlpositionen"][0]
        self.assertEqual(zeile["erwartete_menge"], "6.000")

    def test_vorgabe_bleibt_nach_dem_zaehlen_stehen(self):
        """The PWA replaces its row with the response. The aid must not vanish."""
        self.anmelden()
        position = self.inventur.zaehlpositionen.get()
        rsp = self.client.patch(adresse("zaehlposition-detail", position.pk), {"eingabemenge": "2"})
        self.assertEqual(rsp.data["erwartete_menge"], "6.000")

    def test_ohne_vorgabe_bleibt_es_leer(self):
        self.cola.erwartet_an.get(ort=self.theke).gebinde_vorgaben.update(standard_menge=None)
        self.anmelden()
        daten = self.client.get(adresse("inventur-detail", self.inventur.pk)).data
        self.assertIsNone(daten["zaehlpositionen"][0]["erwartete_menge"])


class ReihenfolgeDerZaehlliste(ApiGrundlagen):
    """Counting happens in the order the goods stand on the shelf."""

    def test_stellung_kommt_beim_anlegen_aus_der_erwartung(self):
        """A **snapshot**: whoever re-sorts the counting list later turns nothing running around."""
        erwartung = self.cola.erwartet_an.get(ort=self.theke)
        erwartung.sortierung = 3
        erwartung.save()
        neue = inventur_anlegen(self.haus, stichtag=STICHTAG + timedelta(1))
        neue.zaehlpositionen_anlegen()
        neue.zaehlende.add(self.zaehler)
        neue.zaehlung_starten()

        self.anmelden()
        daten = self.client.get(adresse("inventur-detail", neue.pk)).data
        self.assertEqual(daten["zaehlpositionen"][0]["sortierung"], 3)

        # Re-sorting does not act retroactively.
        erwartung.sortierung = 9
        erwartung.save()
        daten = self.client.get(adresse("inventur-detail", neue.pk)).data
        self.assertEqual(daten["zaehlpositionen"][0]["sortierung"], 3)

    def test_unerwartetes_rutscht_ans_ende(self):
        self.anmelden()
        rsp = self.client.post(
            adresse("zaehlposition-list"),
            {
                "inventur": self.inventur.pk,
                "ort": self.lager.pk,
                "artikel": self.cola.pk,
                "gebinde": self.einzeln.pk,
            },
        )
        self.assertEqual(rsp.status_code, 201, rsp.data)
        self.assertEqual(rsp.data["sortierung"], 10_000)


class ReiterDerZeilen(ApiGrundlagen):
    """The app shows a tab per type the inventory counts and sorts the rows by 'inventurart'."""

    def setUp(self):
        super().setUp()
        self.inventur.zaehlpositionen.all().delete()
        self.inventur.inventurarten.set([monatsinventur(), jahresinventur()])
        Erwartung.objects.create(ort=self.theke, artikel=self.portion)
        self.inventur.zaehlpositionen_anlegen()

    def reiter(self) -> dict[str, int | None]:
        daten = self.client.get(adresse("inventur-detail", self.inventur.pk)).data
        return {zeile["artikel"]: zeile["inventurart"] for zeile in daten["zaehlpositionen"]}

    def test_die_liste_nennt_die_arten_in_ihrer_reihenfolge(self):
        monat, jahr = monatsinventur(), jahresinventur()
        jahr.verschieben(-1)
        self.anmelden()
        eintraege = {e["id"]: e for e in self.client.get(adresse("inventur-list")).data}
        eintrag = eintraege[self.inventur.pk]
        self.assertEqual(
            eintrag["inventurarten"],
            [{"id": jahr.pk, "name": "Jahresinventur"}, {"id": monat.pk, "name": "Monatsinventur"}],
        )
        self.assertEqual(eintrag["arten_anzeige"], "Jahresinventur + Monatsinventur")

    def test_zeile_nennt_die_art_ihres_artikels(self):
        self.anmelden()
        monat, jahr = monatsinventur().pk, jahresinventur().pk
        self.assertEqual(self.reiter(), {"Artikel C 0,33": monat, "Anteil N": monat})
        self.cola.inventurart_setzen(jahresinventur())
        self.assertEqual(self.reiter(), {"Artikel C 0,33": jahr, "Anteil N": monat})

    def test_rezepturartikel_steht_im_ersten_reiter_seiner_zutaten(self):
        """In the order of the inventory's types, wherever an ingredient is due."""
        self.anmelden()
        Rezepturkomponente.objects.create(
            rezepturartikel=self.portion,
            ware=self.ware,
            menge_in_basiseinheit=Decimal("1"),
        )
        self.nachos.inventurart_setzen(jahresinventur())
        self.assertEqual(self.reiter()["Anteil N"], monatsinventur().pk)
        self.cola.inventurart_setzen(jahresinventur())
        self.assertEqual(self.reiter()["Anteil N"], jahresinventur().pk)

    def test_ueber_alle_inventuren_nennt_jede_zeile_den_reiter_ihrer_inventur(self):
        """'?geaendert_seit=' alone answers for every inventory the account counts in.

        Kino Süd carries the ingredient in the annual inventory, Kino Ost in
        the monthly one: the same recipe article stands in another tab there.
        """
        theke_sued = Ort.objects.create(
            name="Theke Süd", bereich=Bereich.objects.create(name="Theke", haus=self.fremdes_haus)
        )
        Hausartikel.objects.create(
            haus=self.fremdes_haus,
            ware=self.nachos_ware,
            warengruppe=self.gruppe,
            artikelnummer="101002",
            name="Artikel N lose",
            inventurart=jahresinventur(),
        )
        Erwartung.objects.create(ort=theke_sued, artikel=self.portion)
        sued = inventur_anlegen(
            self.fremdes_haus, monatsinventur(), jahresinventur(), stichtag=STICHTAG
        )
        sued.zaehlpositionen_anlegen()
        self.zaehler.haeuser.add(self.fremdes_haus)
        sued.zaehlende.add(self.zaehler)
        sued.zaehlung_starten()
        self.anmelden()

        rsp = self.client.get(adresse("zaehlposition-list"), {"geaendert_seit": "2000-01-01T00:00"})

        self.assertEqual(rsp.status_code, 200, rsp.data)
        reiter = {
            (zeile["inventur_id"], zeile["artikel"]): zeile["inventurart"] for zeile in rsp.data
        }
        self.assertEqual(reiter[(self.inventur.pk, "Anteil N")], monatsinventur().pk)
        self.assertEqual(reiter[(sued.pk, "Anteil N")], jahresinventur().pk)

    def test_artikel_einer_art_ausserhalb_steht_unter_seiner_art(self):
        """Counted on top. The app shows it in its first tab and names the type."""
        becher = Inventurart.objects.create(name="Becherinventur")
        self.cola.inventurart_setzen(becher)
        self.anmelden()
        self.assertEqual(self.reiter()["Artikel C 0,33"], becher.pk)

    def test_stammdaten_sagen_es_fuer_selbst_angelegte_zeilen(self):
        self.nachos.inventurart_setzen(jahresinventur())
        self.anmelden()
        daten = self.client.get(adresse("inventur-stammdaten", self.inventur.pk)).data
        nach_name = {artikel["name"]: artikel["inventurart"] for artikel in daten["artikel"]}
        monat, jahr = monatsinventur().pk, jahresinventur().pk
        self.assertEqual(nach_name, {"Artikel C 0,33": monat, "Artikel N": jahr, "Anteil N": jahr})
        # The search names a type by this, also one the inventory does not count.
        self.assertEqual(
            [art["name"] for art in daten["inventurarten"]], ["Monatsinventur", "Jahresinventur"]
        )

    def test_angelegte_zeile_bringt_es_in_der_antwort_mit(self):
        self.cola.inventurart_setzen(jahresinventur())
        self.anmelden()
        rsp = self.client.post(
            adresse("zaehlposition-list"),
            {
                "inventur": self.inventur.pk,
                "ort": self.lager.pk,
                "artikel": self.cola.pk,
                "gebinde": self.einzeln.pk,
            },
        )
        self.assertEqual(rsp.status_code, 201, rsp.data)
        self.assertEqual(rsp.data["inventurart"], jahresinventur().pk)


class DritteInventurart(ApiGrundlagen):
    """An inventory of a third type alone: the app sees its name and only its products."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.becher = Inventurart.objects.create(name="Becherinventur")
        cls.nachos.inventurart_setzen(cls.becher)

    def setUp(self):
        super().setUp()
        self.eigene = inventur_anlegen(self.haus, self.becher, stichtag=STICHTAG + timedelta(1))
        self.eigene.zaehlpositionen_anlegen()
        self.eigene.zaehlende.add(self.zaehler)
        self.eigene.zaehlung_starten()

    def test_die_liste_nennt_den_namen_der_art(self):
        self.anmelden()
        eintraege = {e["id"]: e for e in self.client.get(adresse("inventur-list")).data}
        eintrag = eintraege[self.eigene.pk]
        self.assertEqual(
            eintrag["inventurarten"], [{"id": self.becher.pk, "name": "Becherinventur"}]
        )
        self.assertEqual(eintrag["arten_anzeige"], "Becherinventur")
        self.assertIn("Becherinventur", eintrag["bezeichnung"])

    def test_ein_inaktiver_ausserhalb_der_art_wird_abgewiesen(self):
        """Inactive and not of this type: not due, as an annual article in the month."""
        self.cola.deaktivieren()
        self.anmelden()
        daten = self.client.get(adresse("inventur-stammdaten", self.eigene.pk)).data
        self.assertNotIn("Artikel C 0,33", {eintrag["name"] for eintrag in daten["artikel"]})
        rsp = self.client.post(
            adresse("zaehlposition-list"),
            {
                "inventur": self.eigene.pk,
                "ort": self.theke.pk,
                "artikel": self.cola.pk,
                "gebinde": self.kasten.pk,
            },
        )
        self.assertEqual(rsp.status_code, HTTPStatus.BAD_REQUEST, rsp.data)

    def test_ohne_die_art_fehlt_ihr_inaktiver_artikel(self):
        """No type includes another: monthly and annual together still lack the cups."""
        self.nachos.deaktivieren()
        jahr = inventur_anlegen(
            self.haus, monatsinventur(), jahresinventur(), stichtag=STICHTAG + timedelta(2)
        )
        jahr.zaehlende.add(self.zaehler)
        jahr.zaehlung_starten()
        self.anmelden()
        daten = self.client.get(adresse("inventur-stammdaten", jahr.pk)).data
        self.assertNotIn("Artikel N", {eintrag["name"] for eintrag in daten["artikel"]})

    def test_die_positionen_sind_nur_die_der_art(self):
        """Artikel C is expected at the counter, but the inventory does not count it."""
        Erwartung.objects.create(ort=self.theke, artikel=self.nachos, wiegen_erwartet=True)
        self.eigene.zurueck_in_vorbereitung()
        self.eigene.zaehlpositionen_anlegen()
        self.eigene.zaehlung_starten()
        self.anmelden()
        daten = self.client.get(adresse("inventur-detail", self.eigene.pk)).data
        self.assertEqual(
            [(zeile["artikel"], zeile["inventurart"]) for zeile in daten["zaehlpositionen"]],
            [("Artikel N", self.becher.pk)],
        )


class HausZaehltAnders(ApiGrundlagen):
    """Kino Ost carries Artikel C in litres, one litre is two bottles."""

    def setUp(self):
        super().setUp()
        Hausartikel.objects.filter(pk=self.cola.pk).update(
            basiseinheit=Basiseinheit.objects.get(name="Liter"), faktor_zur_ware=Decimal("2")
        )
        self.anmelden()

    def test_die_app_bekommt_die_einheit_des_hauses(self):
        daten = self.client.get(adresse("inventur-stammdaten", self.inventur.pk)).data
        artikel = next(eintrag for eintrag in daten["artikel"] if eintrag["id"] == self.cola.pk)
        self.assertEqual(artikel["einheit"], "LITER")
        # The crate holds 20 bottles, for the house that is 10 litres.
        kasten = next(g for g in artikel["gebinde"] if g["name"] == "Kasten")
        self.assertEqual(kasten["faktor_in_basiseinheit"], "10.000")

    def test_ein_gebinde_wird_nicht_auf_die_stellen_des_hauses_gerundet(self):
        """A box of 2.4 kg in a house that counts pieces of 125 g is 19.2 pieces, not 19."""
        kilo = Basiseinheit.objects.get(name="kg")
        ware = Ware.objects.create(name="Ware K", basiseinheit=kilo)
        Gebinde.objects.create(ware=ware, name="Karton", faktor_in_basiseinheit=Decimal("2.4"))
        Hausartikel.objects.create(
            haus=self.haus,
            ware=ware,
            warengruppe=self.gruppe,
            artikelnummer="101009",
            name="Ware K im Stück",
            basiseinheit=self.stueck,
            faktor_zur_ware=Decimal("0.125"),
        )
        daten = self.client.get(adresse("inventur-stammdaten", self.inventur.pk)).data
        artikel = next(eintrag for eintrag in daten["artikel"] if eintrag["name"] == "Ware K")
        self.assertEqual(artikel["gebinde"][0]["faktor_in_basiseinheit"], "19.200")

    def test_die_menge_kommt_in_der_einheit_des_hauses(self):
        position = self.inventur.zaehlpositionen.get(artikel=self.cola)
        rsp = self.client.patch(adresse("zaehlposition-detail", position.pk), {"eingabemenge": "3"})
        self.assertEqual(rsp.status_code, 200)
        self.assertEqual(rsp.data["einheit"], "LITER")
        self.assertEqual(rsp.data["menge_basiseinheit"], "30.000")

    def test_die_zeile_nennt_ihr_gebinde_in_der_einheit_des_hauses(self):
        """Next to "LITER" the crate holds 10, as in the master data, not the product's 20."""
        position = self.inventur.zaehlpositionen.get(artikel=self.cola)
        daten = self.client.get(adresse("inventur-detail", self.inventur.pk)).data
        zeile = next(z for z in daten["zaehlpositionen"] if z["id"] == position.pk)
        self.assertEqual(zeile["einheit"], "LITER")
        self.assertEqual(zeile["gebinde"]["faktor_in_basiseinheit"], "10.000")

        rsp = self.client.patch(adresse("zaehlposition-detail", position.pk), {"eingabemenge": "3"})
        self.assertEqual(rsp.data["gebinde"]["faktor_in_basiseinheit"], "10.000")


class GrossesGebindeImHaus(ApiGrundlagen):
    """Every pack the catalog accepts reaches the app, however large it is in the house's unit.

    The field kept the model's ten digits. A silo of 10,000 kg in a house
    that counts in g reads ten million there, and every answer with its row
    ended in a server error, the PATCH after saving (until 10/2026). The app
    sent the quantity again on every sync and could no longer load the
    inventory.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.kilo = Basiseinheit.objects.get(name="kg")
        ware = Ware.objects.create(name="Ware K", basiseinheit=cls.kilo)
        cls.silo = Gebinde.objects.create(
            ware=ware, name="Silo", faktor_in_basiseinheit=Decimal("10000")
        )
        in_gramm = Hausartikel.objects.create(
            haus=cls.haus,
            ware=ware,
            warengruppe=cls.gruppe,
            artikelnummer="101009",
            name="Ware K in g",
            basiseinheit=cls.gramm,
        )
        cls.zeile = Zaehlposition.objects.create(
            inventur=cls.inventur,
            ort=cls.lager,
            artikel=in_gramm,
            gebinde=cls.silo,
            unerwartet=True,
        )

    def setUp(self):
        super().setUp()
        self.anmelden()

    def faktor_in(self, zeilen, pk) -> str:
        return next(zeile for zeile in zeilen if zeile["id"] == pk)["gebinde"][
            "faktor_in_basiseinheit"
        ]

    def test_die_inventur_laedt_mit_der_zeile(self):
        rsp = self.client.get(adresse("inventur-detail", self.inventur.pk))

        self.assertEqual(rsp.status_code, 200)
        self.assertEqual(self.faktor_in(rsp.data["zaehlpositionen"], self.zeile.pk), "10000000.000")

    def test_die_zeile_allein_und_im_abgleich(self):
        einzeln = self.client.get(adresse("zaehlposition-detail", self.zeile.pk))
        liste = self.client.get(adresse("zaehlposition-list"), {"inventur": self.inventur.pk})

        self.assertEqual(einzeln.status_code, 200)
        self.assertEqual(einzeln.data["gebinde"]["faktor_in_basiseinheit"], "10000000.000")
        self.assertEqual(self.faktor_in(liste.data, self.zeile.pk), "10000000.000")

    def test_die_menge_bekommt_ihre_antwort(self):
        rsp = self.client.patch(
            adresse("zaehlposition-detail", self.zeile.pk), {"eingabemenge": "2"}
        )

        self.assertEqual(rsp.status_code, 200, rsp.data)
        self.assertEqual(rsp.data["eingabemenge"], "2.000")
        self.assertEqual(rsp.data["gebinde"]["faktor_in_basiseinheit"], "10000000.000")

    def test_die_stammdaten_nennen_es(self):
        rsp = self.client.get(adresse("inventur-stammdaten", self.inventur.pk))

        self.assertEqual(rsp.status_code, 200)
        artikel = next(eintrag for eintrag in rsp.data["artikel"] if eintrag["name"] == "Ware K")
        self.assertEqual(artikel["gebinde"][0]["faktor_in_basiseinheit"], "10000000.000")

    def test_auch_das_groesste_das_der_katalog_fasst(self):
        """The largest pack, in a house whose piece is the smallest factor the article takes."""
        ware = Ware.objects.create(name="Ware W", basiseinheit=self.kilo)
        gebinde = Gebinde.objects.create(
            ware=ware, name="Lager", faktor_in_basiseinheit=Decimal("9999999.999")
        )
        im_stueck = Hausartikel.objects.create(
            haus=self.haus,
            ware=ware,
            warengruppe=self.gruppe,
            artikelnummer="101010",
            name="Ware W im Stück",
            basiseinheit=self.stueck,
            faktor_zur_ware=Decimal("0.000000000001"),
        )
        zeile = Zaehlposition.objects.create(
            inventur=self.inventur, ort=self.theke, artikel=im_stueck, gebinde=gebinde
        )
        erwartet = "9999999999000000000.000"

        inventur = self.client.get(adresse("inventur-detail", self.inventur.pk))
        stammdaten = self.client.get(adresse("inventur-stammdaten", self.inventur.pk))

        self.assertEqual(self.faktor_in(inventur.data["zaehlpositionen"], zeile.pk), erwartet)
        artikel = next(e for e in stammdaten.data["artikel"] if e["name"] == "Ware W")
        self.assertEqual(artikel["gebinde"][0]["faktor_in_basiseinheit"], erwartet)


class RezepturartikelInDenStammdaten(ApiGrundlagen):
    def test_rezepturartikel_ist_gekennzeichnet(self):
        """Its quantity is only distributed at closing. A sum says nothing before."""
        self.anmelden()
        daten = self.client.get(adresse("inventur-stammdaten", self.inventur.pk)).data
        nach_name = {artikel["name"]: artikel for artikel in daten["artikel"]}
        self.assertTrue(nach_name["Anteil N"]["ist_rezepturartikel"])
        self.assertFalse(nach_name["Artikel C 0,33"]["ist_rezepturartikel"])


class NamenDerWare(ApiGrundlagen):
    """When counting, the product is on the shelf, not the Compeso record."""

    def test_angezeigt_wird_der_ware(self):
        self.anmelden()
        zeile = self.client.get(adresse("inventur-detail", self.inventur.pk)).data[
            "zaehlpositionen"
        ][0]
        self.assertEqual(zeile["artikel"], "Artikel C 0,33")
        self.assertEqual(zeile["artikel_im_haus"], "Artikel C 0,33 Flasche")

    def test_stammdaten_fuehren_beide_namen_der_rezepturartikel_nur_seinen(self):
        self.anmelden()
        daten = self.client.get(adresse("inventur-stammdaten", self.inventur.pk)).data
        je_artikel = {a["id"]: (a["name"], a["name_im_haus"]) for a in daten["artikel"]}
        self.assertEqual(je_artikel[self.cola.pk], ("Artikel C 0,33", "Artikel C 0,33 Flasche"))
        self.assertEqual(
            je_artikel[self.portion.pk],
            ("Anteil N", ""),
            "Ein Rezepturartikel gehört keinem Haus und hat nur seinen eigenen Namen.",
        )


class BehaelterDerHaeuser(ApiGrundlagen):
    """Three groups in the app: on the article, in this house, in other houses.

    A container without a house that only hangs on other articles is not
    available: the spray can never holds anything but Posten S. The server
    checks the same rule by which the app builds its selection.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.eimer = Behaelter.objects.create(name="Eimer", tara=Decimal("300"))
        cls.eimer.haeuser.add(cls.haus)
        cls.wanne = Behaelter.objects.create(name="Wanne", tara=Decimal("900"))
        cls.wanne.haeuser.add(cls.fremdes_haus)
        cls.flasche = Behaelter.objects.create(name="Druckflasche", tara=Decimal("120"))
        sahne = Ware.objects.create(name="Posten S", basiseinheit=cls.gramm, abwiegbar=True)
        sahne.behaelter.add(cls.flasche)

    def waage(self) -> Zaehlposition:
        return Zaehlposition.objects.create(
            inventur=self.inventur, ort=self.theke, artikel=self.nachos, unerwartet=True
        )

    def test_stammdaten_sagen_wie_jeder_behaelter_zum_haus_steht(self):
        self.anmelden()
        daten = self.client.get(adresse("inventur-stammdaten", self.inventur.pk)).data
        lage = {b["name"]: b["haus"] for b in daten["behaelter"]}
        self.assertEqual(lage["Eimer"], "dieses")
        self.assertEqual(lage["Wanne"], "anderes")
        self.assertEqual(lage["Druckflasche"], "", "Ohne Haus steht er nur bei seiner Ware.")
        self.assertEqual(lage["Schale"], "")
        # On the article itself still only what is assigned to it. That is the first group.
        nachos = next(a for a in daten["artikel"] if a["id"] == self.nachos.pk)
        self.assertCountEqual(nachos["behaelter"], [self.schale.pk, Behaelter.ohne_behaelter().pk])

    def test_gewogen_wird_nur_in_dem_was_zur_wahl_steht(self):
        self.anmelden()
        position = self.waage()
        faelle = (
            ("dem Artikel zugeordnet", self.schale, 201),
            ("Ohne Behälter", Behaelter.ohne_behaelter(), 201),
            ("aus diesem Haus", self.eimer, 201),
            ("aus einem anderen Haus geliehen", self.wanne, 201),
            ("ohne Haus, an einer anderen Ware", self.flasche, 400),
        )
        for fall, behaelter, status in faelle:
            with self.subTest(fall=fall):
                antwort = self.client.post(
                    adresse("wiegung-list"),
                    {
                        "zaehlposition": position.pk,
                        "brutto_gramm": "900",
                        "behaelter": behaelter.pk,
                    },
                )
                self.assertEqual(antwort.status_code, status, antwort.data)
        self.assertEqual(position.wiegungen.count(), 4)

    def test_auch_eine_berichtigung_prueft_den_behaelter(self):
        self.anmelden()
        wiegung = Wiegung.objects.create(
            zaehlposition=self.waage(), brutto_gramm=Decimal("900"), behaelter=self.schale
        )
        antwort = self.client.patch(
            adresse("wiegung-detail", wiegung.pk), {"behaelter": self.flasche.pk}
        )
        self.assertEqual(antwort.status_code, 400)
        self.assertIn("behaelter", antwort.data)

    def test_die_stammdaten_fragen_nicht_je_behaelter(self):
        self.anmelden()
        ziel = adresse("inventur-stammdaten", self.inventur.pk)
        self.client.get(ziel)
        with CaptureQueriesContext(connection) as wenige:
            self.client.get(ziel)
        for i in range(5):
            Behaelter.objects.create(name=f"Kiste {i}", tara=Decimal("50")).haeuser.add(self.haus)
        with CaptureQueriesContext(connection) as viele:
            self.client.get(ziel)
        self.assertEqual(len(viele), len(wenige))


class AbfragenWachsenNichtMitDenZeilen(ApiGrundlagen):
    """The app's sync must not cost a query per row.

    Since the house article has a unit of its own, every row fetched it
    separately: 165 queries for the count positions of the demo, until 09/2026.
    """

    def setUp(self):
        super().setUp()
        self.anmelden()
        self.nummer = 101100

    def neue_zeile(self):
        """An unexpected row for an own product, as from the app."""
        self.nummer += 1
        ware = Ware.objects.create(name=f"Ware {self.nummer}", basiseinheit=self.stueck)
        artikel = Hausartikel.objects.create(
            haus=self.haus,
            ware=ware,
            warengruppe=self.gruppe,
            artikelnummer=str(self.nummer),
            name=f"Ware {self.nummer}",
        )
        kasten = Gebinde.objects.create(
            ware=ware, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        rsp = self.client.post(
            adresse("zaehlposition-list"),
            {
                "inventur": self.inventur.pk,
                "ort": self.lager.pk,
                "artikel": artikel.pk,
                "gebinde": kasten.pk,
            },
        )
        self.assertEqual(rsp.status_code, 201, rsp.data)

    def abfragen(self, ziel) -> int:
        with CaptureQueriesContext(connection) as gezaehlt:
            self.assertEqual(self.client.get(ziel).status_code, 200)
        return len(gezaehlt)

    def test_zaehlpositionen_und_inventur(self):
        rsp = self.client.post(
            adresse("zaehlposition-list"),
            {"inventur": self.inventur.pk, "ort": self.lager.pk, "artikel": self.portion.pk},
        )
        self.assertEqual(rsp.status_code, 201, rsp.data)
        for ziel in (
            f"{adresse('zaehlposition-list')}?inventur={self.inventur.pk}",
            adresse("inventur-detail", self.inventur.pk),
        ):
            with self.subTest(ziel):
                wenige = self.abfragen(ziel)
                for _ in range(4):
                    self.neue_zeile()
                self.assertEqual(self.abfragen(ziel), wenige)

    def test_wiegungen(self):
        rsp = self.client.post(
            adresse("zaehlposition-list"),
            {"inventur": self.inventur.pk, "ort": self.theke.pk, "artikel": self.nachos.pk},
        )
        self.assertEqual(rsp.status_code, 201, rsp.data)
        position = Zaehlposition.objects.get(pk=rsp.data["id"])

        def wiegen():
            Wiegung.objects.create(
                zaehlposition=position, brutto_gramm=Decimal("900"), behaelter=self.schale
            )

        wiegen()
        ziel = f"{adresse('wiegung-list')}?zaehlposition={position.pk}"
        wenige = self.abfragen(ziel)
        for _ in range(4):
            wiegen()
        self.assertEqual(self.abfragen(ziel), wenige)


class Mengenkonflikte(ApiGrundlagen):
    """Two count the same row. Normally that does not happen, and precisely
    therefore it must not happen silently.

    The app sends along which quantity it saw when opening the form
    ('eingabemenge_vorher'). If the server deviates from that, someone else
    counted without the second person knowing.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.zweite = CustomUser.objects.create_user(
            username="zweite",
            password="geheim",
            email="zweite@example.org",
            first_name="Zara",
            last_name="Zweit",
        )
        cls.zweite.haeuser.add(cls.haus)

    def setUp(self):
        super().setUp()
        self.inventur.zaehlende.add(self.zweite)
        self.position = self.inventur.zaehlpositionen.get()
        self.ziel = adresse("zaehlposition-detail", self.position.pk)
        self.erste = self.geraet(self.zaehler)
        self.andere = self.geraet(self.zweite)

    @staticmethod
    def geraet(benutzer) -> APIClient:
        """A device of its own per person, with its own sign-in."""
        client = APIClient()
        client.force_authenticate(benutzer)
        return client

    def eintragen(self, geraet, menge, **vorher):
        daten = {"eingabemenge": menge}
        if vorher:
            daten["eingabemenge_vorher"] = vorher["vorher"]
        return geraet.patch(self.ziel, daten, format="json")

    def menge(self):
        self.position.refresh_from_db()
        return self.position.eingabemenge

    def test_kein_konflikt_wo_niemand_uebergangen_wird(self):
        """The seen empty row, the own send after the dead spot (the response was
        lost), and a differently written, equal number.
        """
        faelle = (
            ("gesehene leere Zeile", None, "3", None),
            ("doppelt angekommenes eigenes Senden", "3", "3", None),
            ("Schreibweise der Zahl", "2.5", "4", "2.500"),
        )
        for fall, vorbelegt, menge, vorher in faelle:
            with self.subTest(fall):
                Zaehlposition.objects.filter(pk=self.position.pk).update(eingabemenge=None)
                if vorbelegt is not None:
                    self.eintragen(self.andere, vorbelegt, vorher=None)
                rsp = self.eintragen(self.erste, menge, vorher=vorher)
                self.assertEqual(rsp.status_code, HTTPStatus.OK, rsp.data)
                self.assertEqual(self.menge(), Decimal(menge))

    def test_ungesehener_fremder_wert_wird_zur_rueckfrage(self):
        self.eintragen(self.andere, "5", vorher=None)
        stand = Zaehlposition.objects.get(pk=self.position.pk).zuletzt_geaendert

        rsp = self.eintragen(self.erste, "3", vorher=None)

        self.assertEqual(rsp.status_code, HTTPStatus.CONFLICT)
        self.assertEqual(rsp.data["konflikt"], "eingabemenge")
        # The row as it stands now, so the app can store it.
        self.assertEqual(rsp.data["aktuell"]["id"], self.position.pk)
        self.assertEqual(Decimal(rsp.data["aktuell"]["eingabemenge"]), Decimal("5"))
        # Nothing was written, not even the timestamp.
        self.assertEqual(self.menge(), Decimal("5"))
        self.assertEqual(self.position.zuletzt_geaendert, stand)

    def test_gesehener_fremder_wert_wird_absichtlich_ueberschrieben(self):
        """The app had fetched the 5 already, and the person had it in front of them."""
        self.eintragen(self.andere, "5", vorher=None)
        rsp = self.eintragen(self.erste, "3", vorher="5")
        self.assertEqual(rsp.status_code, HTTPStatus.OK, rsp.data)
        self.assertEqual(self.menge(), Decimal("3"))

    def test_geleerte_zeile_ist_auch_ein_fremder_wert(self):
        self.eintragen(self.erste, "3", vorher=None)
        self.eintragen(self.andere, None, vorher="3")

        rsp = self.eintragen(self.erste, "4", vorher="3")

        self.assertEqual(rsp.status_code, HTTPStatus.CONFLICT)
        self.assertIsNone(rsp.data["aktuell"]["eingabemenge"])
        self.assertIsNone(self.menge())

    def test_die_entscheidung_nach_der_rueckfrage_geht_durch(self):
        """Choosing "Add": 5 there, 3 from me. The app now names the 5 as seen."""
        self.eintragen(self.andere, "5", vorher=None)
        self.assertEqual(self.eintragen(self.erste, "3", vorher=None).status_code, 409)

        rsp = self.eintragen(self.erste, "8", vorher="5")

        self.assertEqual(rsp.status_code, HTTPStatus.OK, rsp.data)
        self.assertEqual(self.menge(), Decimal("8"))

    def test_ohne_das_feld_wird_wie_frueher_ueberschrieben(self):
        """An app from before the change. It should be able to keep counting."""
        self.eintragen(self.andere, "5", vorher=None)
        rsp = self.eintragen(self.erste, "3")
        self.assertEqual(rsp.status_code, HTTPStatus.OK, rsp.data)
        self.assertEqual(self.menge(), Decimal("3"))

    def test_beide_legen_offline_dieselbe_unerwartete_zeile_an(self):
        """The app's path after the dead spot: create, on "already exists" take the
        number, then the quantity. The second person never saw the row filled.
        """
        neu = {"inventur": self.inventur.pk, "ort": self.theke.pk, "artikel": self.cola.pk}
        neu["gebinde"] = self.einzeln.pk
        angelegt = self.erste.post(adresse("zaehlposition-list"), neu, format="json")
        self.assertEqual(angelegt.status_code, HTTPStatus.CREATED, angelegt.data)
        zeile = adresse("zaehlposition-detail", angelegt.data["id"])
        self.erste.patch(zeile, {"eingabemenge": "3", "eingabemenge_vorher": None}, format="json")

        doppelt = self.andere.post(adresse("zaehlposition-list"), neu, format="json")
        self.assertEqual(doppelt.status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(doppelt.data["vorhanden_id"], [str(angelegt.data["id"])])
        rsp = self.andere.patch(
            zeile, {"eingabemenge": "5", "eingabemenge_vorher": None}, format="json"
        )

        self.assertEqual(rsp.status_code, HTTPStatus.CONFLICT)
        self.assertEqual(Decimal(rsp.data["aktuell"]["eingabemenge"]), Decimal("3"))


class GleichzeitigAngelegt(ApiGrundlagen):
    """Two devices create the same row at the same moment.

    Simulated by the search in 'validate' not finding the row yet: the other
    request was faster. The response must still be "already exists" with the
    number. As a mere rejection the app would discard the quantity.
    """

    def setUp(self):
        super().setUp()
        self.neu = {
            "inventur": self.inventur.pk,
            "ort": self.theke.pk,
            "artikel": self.cola.pk,
            "gebinde": self.einzeln.pk,
        }
        self.vorhanden = Zaehlposition.objects.create(
            inventur=self.inventur,
            ort=self.theke,
            artikel=self.cola,
            gebinde=self.einzeln,
            unerwartet=True,
        )
        self.anmelden()

    def anlegen(self):
        echt = api_serializers.vorhandene_zeile
        # On the first look in 'validate' it is still missing, afterwards it is there.
        erst_nicht = mock.patch.object(
            counting_serializers, "vorhandene_zeile", side_effect=[None, echt(self.neu_daten())]
        )
        with erst_nicht:
            return self.client.post(adresse("zaehlposition-list"), self.neu, format="json")

    def neu_daten(self):
        return {
            "inventur": self.inventur,
            "ort": self.theke,
            "artikel": self.cola,
            "gebinde": self.einzeln,
        }

    def test_die_modellpruefung_findet_sie(self):
        rsp = self.anlegen()
        self.assertEqual(rsp.status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(rsp.data["vorhanden_id"], [str(self.vorhanden.pk)])

    def test_erst_der_index_findet_sie(self):
        """Even later: 'full_clean' was already through, only saving fails."""
        with mock.patch.object(counting_serializers, "pruefen"):
            rsp = self.anlegen()
        self.assertEqual(rsp.status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(rsp.data["vorhanden_id"], [str(self.vorhanden.pk)])
        self.assertEqual(Zaehlposition.objects.filter(gebinde=self.einzeln).count(), 1)

    def test_andere_fehler_bleiben_was_sie_sind(self):
        """A location from a foreign house is no duplicate, even if one exists."""
        fremder_ort = Ort.objects.create(
            name="Fremde Theke",
            bereich=Bereich.objects.create(name="Theke", haus=self.fremdes_haus),
        )
        rsp = self.client.post(
            adresse("zaehlposition-list"), {**self.neu, "ort": fremder_ort.pk}, format="json"
        )
        self.assertEqual(rsp.status_code, HTTPStatus.BAD_REQUEST)
        self.assertNotIn("vorhanden_id", rsp.data)


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class GleichzeitigGeschrieben(APITransactionTestCase):
    """Two devices write into the same empty row at the same moment.

    Without the lock of the row both read "empty", and both got through:
    whoever wrote last won silently. With it the second waits until the first
    is done, then sees its value and gets the question back. The overlap is
    forced, not hoped for: the first holds the row until the second has sent
    its request (until 10/2026 a fixed sleep did that, and a slow runner could
    miss). Runs in the CI, which tests against PostgreSQL.
    """

    serialized_rollback = True

    def setUp(self):
        haus = Haus.objects.create(name="Kino Ost")
        ort = Ort.objects.create(
            name="Thekenfront", bereich=Bereich.objects.create(name="Theke", haus=haus)
        )
        ware = Ware.objects.create(
            name="Artikel C", basiseinheit=Basiseinheit.objects.get(name="Stück(e)")
        )
        kasten = Gebinde.objects.create(
            ware=ware, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        artikel = Hausartikel.objects.create(
            haus=haus,
            ware=ware,
            warengruppe=Warengruppe.objects.create(name="Warengruppe G"),
            artikelnummer="101001",
            name="Artikel C",
        )
        ErwartungGebinde.objects.create(
            erwartung=Erwartung.objects.create(ort=ort, artikel=artikel), gebinde=kasten
        )
        self.inventur = inventur_anlegen(haus, stichtag=STICHTAG)
        self.inventur.zaehlpositionen_anlegen()
        self.konten = []
        for name in ("erste", "zweite"):
            konto = CustomUser.objects.create_user(
                username=name, password="geheim", email=f"{name}@example.org"
            )
            konto.haeuser.add(haus)
            self.inventur.zaehlende.add(konto)
            self.konten.append(konto)
        self.inventur.zaehlung_starten()
        self.position = self.inventur.zaehlpositionen.get()

    def test_der_zweite_bekommt_die_rueckfrage(self):
        ziel = adresse("zaehlposition-detail", self.position.pk)
        echt = api_serializers.gleiche_menge
        # The first thread reaches the comparison inside the locked row and says
        # so. Only then does the second send its request, and the first lets go
        # once that request is on its way. The short grace covers the way from
        # the socket to the lock of the row.
        gesperrt = threading.Event()
        unterwegs = threading.Event()
        antworten = {}

        def haelt_die_zeile(links, rechts):
            if not gesperrt.is_set():
                gesperrt.set()
                unterwegs.wait(timeout=5)
                time.sleep(0.1)
            return echt(links, rechts)

        def schreiben(konto, menge, wartet: bool):
            client = APIClient()
            client.force_authenticate(konto)
            if wartet:
                gesperrt.wait(timeout=5)
                unterwegs.set()
            try:
                antworten[menge] = client.patch(
                    ziel, {"eingabemenge": menge, "eingabemenge_vorher": None}, format="json"
                ).status_code
            finally:
                connection.close()

        with mock.patch.object(counting_serializers, "gleiche_menge", side_effect=haelt_die_zeile):
            faeden = [
                threading.Thread(target=schreiben, args=(konto, menge, wartet))
                for konto, menge, wartet in zip(self.konten, ("3", "5"), (False, True), strict=True)
            ]
            for faden in faeden:
                faden.start()
            for faden in faeden:
                faden.join()

        self.assertEqual(sorted(antworten.values()), [HTTPStatus.OK, HTTPStatus.CONFLICT])
        gewinner = next(menge for menge, status in antworten.items() if status == HTTPStatus.OK)
        self.position.refresh_from_db()
        self.assertEqual(self.position.eingabemenge, Decimal(gewinner))


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class AbschlussUndAnlegenGleichzeitig(APITransactionTestCase):
    """The closing holds the inventory while a counter creates an unexpected row.

    The closing locks the inventory's row and holds it until the result is
    frozen. The new row waits for the same lock ('zaehlbar_unter_sperre') and
    then meets the closed state: 409, and the row stays out. Until 10/2026 it
    got a 201, landed in the closed inventory and was missing from the result.
    The overlap is forced as in 'GleichzeitigGeschrieben'. Runs in the CI,
    which tests against PostgreSQL.
    """

    serialized_rollback = True

    def setUp(self):
        haus = Haus.objects.create(name="Kino Ost")
        bereich = Bereich.objects.create(name="Lager", haus=haus)
        theke = Ort.objects.create(name="Thekenfront", bereich=bereich)
        self.lager = Ort.objects.create(name="Trockenlager", bereich=bereich)
        ware = Ware.objects.create(
            name="Artikel C", basiseinheit=Basiseinheit.objects.get(name="Stück(e)")
        )
        self.kasten = Gebinde.objects.create(
            ware=ware, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        self.artikel = Hausartikel.objects.create(
            haus=haus,
            ware=ware,
            warengruppe=Warengruppe.objects.create(name="Warengruppe G"),
            artikelnummer="101001",
            name="Artikel C",
        )
        ErwartungGebinde.objects.create(
            erwartung=Erwartung.objects.create(ort=theke, artikel=self.artikel), gebinde=self.kasten
        )
        self.inventur = inventur_anlegen(haus, stichtag=STICHTAG)
        self.inventur.zaehlpositionen_anlegen()
        self.konto = CustomUser.objects.create_user(
            username="erste", password="geheim", email="erste@example.org"
        )
        self.konto.haeuser.add(haus)
        self.inventur.zaehlende.add(self.konto)
        self.inventur.zaehlung_starten()
        self.inventur.zaehlpositionen.update(eingabemenge=Decimal("2"))

    def test_die_neue_zeile_wartet_und_bleibt_draussen(self):
        gesperrt = threading.Event()
        unterwegs = threading.Event()
        antwort = {}
        echt = Inventur._ergebnisse

        def haelt_die_inventur(inventur, *args, **kwargs):
            # Inside the closing transaction, after the lock, before the rows vanish.
            if not gesperrt.is_set():
                gesperrt.set()
                unterwegs.wait(timeout=5)
                time.sleep(0.5)
            return echt(inventur, *args, **kwargs)

        def schliessen():
            try:
                Inventur.objects.get(pk=self.inventur.pk).abschliessen()
            finally:
                connection.close()

        def anlegen():
            client = APIClient()
            client.force_authenticate(self.konto)
            gesperrt.wait(timeout=5)
            unterwegs.set()
            try:
                rsp = client.post(
                    adresse("zaehlposition-list"),
                    {
                        "inventur": self.inventur.pk,
                        "ort": self.lager.pk,
                        "artikel": self.artikel.pk,
                        "gebinde": self.kasten.pk,
                        "eingabemenge": "4",
                    },
                    format="json",
                )
                antwort["status"] = rsp.status_code
                antwort["daten"] = rsp.data
            finally:
                connection.close()

        with mock.patch.object(
            Inventur, "_ergebnisse", autospec=True, side_effect=haelt_die_inventur
        ):
            faeden = [threading.Thread(target=schliessen), threading.Thread(target=anlegen)]
            for faden in faeden:
                faden.start()
            for faden in faeden:
                faden.join()

        self.inventur.refresh_from_db()
        self.assertEqual(self.inventur.status, Inventurstatus.ABGESCHLOSSEN)
        self.assertEqual(antwort.get("status"), HTTPStatus.CONFLICT)
        self.assertEqual(antwort["daten"]["konflikt"], "status")
        self.assertFalse(Zaehlposition.objects.filter(inventur=self.inventur).exists())
        self.assertEqual(
            list(
                self.inventur.ergebnisse.values_list(
                    "hausartikel__artikelnummer", "gesamtmenge_basiseinheit"
                )
            ),
            [("101001", Decimal("40.000"))],
        )
