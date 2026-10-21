"""A removed passkey signs the account out everywhere, except where it is removed.

Until 10/2026 removing only deleted the row. The website sessions and the
counting app's refresh tokens of the account kept working and renewing,
although the help promises that a lost device loses its access there. Tested
on the three ways in: the website, the counting app and the administration.
"""

import io
from datetime import timedelta
from http import HTTPStatus

from django.conf import settings
from django.core.management import call_command
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import CustomUser, Passkey
from accounts.test_passkey import bestaetigt
from api.tests import adresse
from django_project.testing import administrator


def zaehlerin() -> CustomUser:
    return CustomUser.objects.create_user(
        username="zoe", email="zoe@example.com", password="x", first_name="Z", last_name="Z"
    )


def schluessel(konto: CustomUser, kennung: str = "abc") -> Passkey:
    return Passkey.objects.create(
        benutzer=konto, kennung=kennung, oeffentlicher_schluessel=b"x", name="Handy"
    )


def erneuern(refresh: str) -> int:
    return APIClient().post(adresse("token_erneuern"), {"refresh": refresh}).status_code


class Abmelden(TestCase):
    def setUp(self):
        self.zoe = zaehlerin()
        self.passkey = schluessel(self.zoe)

    def anderer_browser(self, konto: CustomUser) -> Client:
        browser = Client()
        browser.force_login(konto)
        return browser

    def angemeldet(self, browser: Client) -> bool:
        return browser.get(reverse("passkey_list")).status_code == HTTPStatus.OK

    def ein_geraet(self) -> str:
        """The refresh token of a phone that has renewed once already."""
        antwort = APIClient().post(
            adresse("token_erneuern"), {"refresh": str(RefreshToken.for_user(self.zoe))}
        )
        return antwort.data["refresh"]


class AufDerWebsite(Abmelden):
    def test_die_anderen_sitzungen_und_geraete_sind_abgemeldet(self):
        anderswo = self.anderer_browser(self.zoe)
        handy = self.ein_geraet()
        self.client.force_login(self.zoe)

        self.client.post(reverse("passkey_delete", args=[self.passkey.pk]), {"passwort": "x"})

        self.assertFalse(self.angemeldet(anderswo))
        self.assertEqual(erneuern(handy), 401)

    def test_hier_bleibt_die_anmeldung(self):
        self.client.force_login(self.zoe)

        self.client.post(reverse("passkey_delete", args=[self.passkey.pk]), {"passwort": "x"})

        self.assertTrue(self.angemeldet(self.client))

    def test_fremde_konten_bleiben_angemeldet(self):
        nele = CustomUser.objects.create_user(
            username="nele", email="nele@example.com", password="x", first_name="N", last_name="N"
        )
        nele_im_browser = self.anderer_browser(nele)
        nele_am_handy = str(RefreshToken.for_user(nele))
        self.client.force_login(self.zoe)

        self.client.post(reverse("passkey_delete", args=[self.passkey.pk]), {"passwort": "x"})

        self.assertTrue(self.angemeldet(nele_im_browser))
        self.assertEqual(erneuern(nele_am_handy), 200)

    def test_ein_abgewiesenes_entfernen_meldet_niemanden_ab(self):
        """The last way of the last administrator stays, and so does every sign-in."""
        chef = administrator(email="chef@example.com")
        chef.set_unusable_password()
        chef.save()
        letzter = schluessel(chef, "def")
        anderswo = self.anderer_browser(chef)
        self.client.force_login(chef)
        bestaetigt(self.client, chef)

        self.client.post(reverse("passkey_delete", args=[letzter.pk]))

        self.assertTrue(Passkey.objects.filter(pk=letzter.pk).exists())
        self.assertTrue(self.angemeldet(anderswo))


class InDerZaehlApp(Abmelden):
    def entfernen(self, app: APIClient):
        """As the app removes: with its access token, its refresh token and the password."""
        eigenes = RefreshToken.for_user(self.zoe)
        app.credentials(HTTP_AUTHORIZATION=f"Bearer {eigenes.access_token}")
        antwort = app.delete(
            adresse("passkey_loeschen_api", self.passkey.pk),
            {"refresh": str(eigenes), "passwort": "x"},
            format="json",
        )
        return antwort, str(eigenes)

    def test_die_anderen_sind_abgemeldet_dieses_geraet_bleibt_angemeldet(self):
        anderswo = self.anderer_browser(self.zoe)
        handy = self.ein_geraet()

        antwort, eigenes = self.entfernen(APIClient())

        self.assertEqual(antwort.status_code, 204)
        self.assertEqual(erneuern(eigenes), 200)
        self.assertEqual(erneuern(handy), 401)
        self.assertFalse(self.angemeldet(anderswo))

    def test_die_sitzung_im_selben_browser_bleibt(self):
        """A 'from_session' app would otherwise sign out on its next start."""
        app = APIClient()
        app.force_login(self.zoe)

        self.entfernen(app)

        self.assertTrue(self.angemeldet(app))


class InDerVerwaltung(Abmelden):
    def setUp(self):
        super().setUp()
        self.chef = administrator(email="chef@example.com")
        self.client.force_login(self.chef)

    def test_die_person_ist_ueberall_abgemeldet(self):
        anderswo = self.anderer_browser(self.zoe)
        handy = self.ein_geraet()

        self.client.post(
            reverse("admin:accounts_passkey_delete", args=[self.passkey.pk]), {"post": "yes"}
        )

        self.assertFalse(Passkey.objects.filter(pk=self.passkey.pk).exists())
        self.assertFalse(self.angemeldet(anderswo))
        self.assertEqual(erneuern(handy), 401)
        self.assertTrue(self.angemeldet(self.client))

    def test_in_einer_auswahl_ebenso(self):
        handy = self.ein_geraet()

        self.client.post(
            reverse("admin:accounts_passkey_changelist"),
            {"action": "delete_selected", "_selected_action": [self.passkey.pk], "post": "yes"},
        )

        self.assertFalse(Passkey.objects.filter(pk=self.passkey.pk).exists())
        self.assertEqual(erneuern(handy), 401)

    def test_der_eigene_schluessel_laesst_die_verwaltung_angemeldet(self):
        eigener = schluessel(self.chef, "def")
        anderswo = self.anderer_browser(self.chef)

        self.client.post(
            reverse("admin:accounts_passkey_delete", args=[eigener.pk]), {"post": "yes"}
        )

        self.assertTrue(self.angemeldet(self.client))
        self.assertFalse(self.angemeldet(anderswo))


class AlleAbmelden(TestCase):
    """'manage.py sign_out_everyone', after a dump was loaded with psql.

    The dump holds the sessions and tokens of its moment. Without the command
    they applied again, whatever had shut them out since.
    """

    def test_jede_sitzung_und_jedes_gueltige_token(self):
        zoe = zaehlerin()
        nele = CustomUser.objects.create_user(
            username="nele", email="nele@example.com", password="x", first_name="N", last_name="N"
        )
        browser = {konto.username: Client() for konto in (zoe, nele)}
        browser["zoe"].force_login(zoe)
        browser["nele"].force_login(nele)
        erneuert = APIClient().post(
            adresse("token_erneuern"), {"refresh": str(RefreshToken.for_user(zoe))}
        )
        handy = [erneuert.data["refresh"], str(RefreshToken.for_user(nele))]
        RefreshToken.for_user(nele).blacklist()
        ausgabe = io.StringIO()

        call_command("sign_out_everyone", stdout=ausgabe)

        for name, client in browser.items():
            with self.subTest(browser=name):
                self.assertNotEqual(client.get(reverse("passkey_list")).status_code, HTTPStatus.OK)
        self.assertEqual([erneuern(token) for token in handy], [401, 401])
        self.assertIn("Beendete Sitzungen der Website: 2.", ausgabe.getvalue())
        self.assertIn("Gesperrte Erneuerungstoken der Zähl-App: 2.", ausgabe.getvalue())

    def test_ein_token_von_nach_dem_abbild_kennt_der_server_nicht(self):
        """A device that renewed after the dump: its token is not in the loaded database.

        The package entered it on renewing and swapped it for a fresh one
        (until 10/2026), and the command could not block what it did not see.
        """
        zoe = zaehlerin()
        nach_dem_abbild = RefreshToken.for_user(zoe)
        # Loaded with psql: the database is that of the dump, without this token.
        OutstandingToken.objects.filter(jti=nach_dem_abbild["jti"]).delete()
        call_command("sign_out_everyone", stdout=io.StringIO())

        antwort = APIClient().post(adresse("token_erneuern"), {"refresh": str(nach_dem_abbild)})

        self.assertEqual(antwort.status_code, HTTPStatus.UNAUTHORIZED)
        self.assertEqual(
            str(antwort.data["detail"]),
            "Diese Anmeldung kennt der Server nicht. Bitte melde dich neu an.",
        )
        self.assertFalse(OutstandingToken.objects.filter(user=zoe).exists())

    def test_danach_meldet_man_sich_neu_an(self):
        zoe = zaehlerin()
        call_command("sign_out_everyone", stdout=io.StringIO())

        self.assertEqual(erneuern(str(RefreshToken.for_user(zoe))), 200)
        self.client.force_login(zoe)
        self.assertEqual(self.client.get(reverse("passkey_list")).status_code, HTTPStatus.OK)


class WasDieTexteVersprechen(SimpleTestCase):
    def test_in_der_zaehl_app_spaetestens_nach_einer_stunde(self):
        """The help and the mail promise it: the access token is not blocked, it runs out."""
        self.assertEqual(settings.SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"], timedelta(hours=1))
        for datei in ("help/de/passkey_list.html", "mails/de/passkey_entfernt.txt"):
            with self.subTest(datei=datei):
                text = (settings.BASE_DIR / "templates" / datei).read_text(encoding="utf-8")
                self.assertIn("spätestens nach einer Stunde", text)
