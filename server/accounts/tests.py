import io
import re
import time
from datetime import timedelta
from unittest import mock

from axes.models import AccessAttempt
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth.hashers import get_hasher
from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.http import http_date
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

from accounts.models import Anmeldeversuch, CustomUser, Passwortanfrage, Sitzung
from django_project.testing import administrator, fuehrungskraft, oeffnet_neuen_tab, skript
from organisation.models import Haus

from .forms import CustomUserChangeForm, CustomUserCreationForm
from .lockout import NAMENSLAENGE

User = CustomUser

PASSWORT = "ziemlich-geheim-2026"


def benutzer_anlegen(**kwargs):
    vorgaben = {
        "username": "testuser",
        "email": "testuser@example.com",
        "password": "testpass1234",
        "first_name": "Test",
        "last_name": "User",
    }
    return User.objects.create_user(**(vorgaben | kwargs))


class UsersManagersTests(TestCase):
    def test_create_user(self):
        user = benutzer_anlegen()
        self.assertEqual(user.username, "testuser")
        self.assertEqual(user.email, "testuser@example.com")
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(str(user), "Test User")

    def test_create_superuser(self):
        admin_user = User.objects.create_superuser(
            username="testsuperuser",
            email="testsuperuser@example.com",
            password="testpass1234",
            first_name="Test",
            last_name="Superuser",
        )
        self.assertEqual(admin_user.username, "testsuperuser")
        self.assertEqual(admin_user.email, "testsuperuser@example.com")
        self.assertTrue(admin_user.is_active)
        self.assertTrue(admin_user.is_staff)
        self.assertTrue(admin_user.is_superuser)
        self.assertEqual(str(admin_user), "Test Superuser")

    def test_get_absolute_url(self):
        user = benutzer_anlegen()
        self.assertEqual(user.get_absolute_url(), f"/users/{user.pk}/")


class HausZuordnungTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.nord = Haus.objects.create(name="Kino Nord")
        cls.sued = Haus.objects.create(name="Kino Süd")

    def test_benutzer_ohne_haus_ist_erlaubt(self):
        user = benutzer_anlegen()
        self.assertEqual(user.haeuser.count(), 0)

    def test_benutzer_mehreren_haeusern_zuordnen(self):
        user = benutzer_anlegen()
        user.haeuser.set([self.nord, self.sued])
        self.assertEqual([h.name for h in user.haeuser.all()], ["Kino Nord", "Kino Süd"])

    def test_rueckwaertsbeziehung_mitarbeitende(self):
        user = benutzer_anlegen()
        user.haeuser.add(self.nord)
        self.assertEqual(list(self.nord.mitarbeitende.all()), [user])

    def test_haus_loeschen_entfernt_nur_die_zuordnung(self):
        user = benutzer_anlegen()
        user.haeuser.add(self.nord)
        self.nord.delete()
        user.refresh_from_db()
        self.assertTrue(User.objects.filter(pk=user.pk).exists())
        self.assertEqual(user.haeuser.count(), 0)


class FormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.nord = Haus.objects.create(name="Kino Nord")

    def test_creation_form_speichert_haeuser(self):
        form = CustomUserCreationForm(
            data={
                "username": "neu",
                "email": "neu@example.com",
                "first_name": "Neue",
                "last_name": "Person",
                "passwort_setzen": True,
                "password1": PASSWORT,
                "password2": PASSWORT,
                "haeuser": [self.nord.pk],
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertEqual(list(user.haeuser.all()), [self.nord])

    def test_change_form_ohne_haus_ist_gueltig(self):
        user = benutzer_anlegen()
        user.haeuser.add(self.nord)
        form = CustomUserChangeForm(
            data={
                "username": user.username,
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "is_active": True,
            },
            instance=user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(user.haeuser.count(), 0)


class ZugriffsschutzTests(TestCase):
    """StaffOnlyMiddleware protects all non-API paths, differently by role."""

    #: An address that does not exist. Deliberately with a trailing slash, so
    #: that APPEND_SLASH does not answer instead of the middleware.
    NIRGENDWO = "/gibt-es-nicht/"

    def test_anonym_wird_zum_login_geschickt(self):
        rsp = self.client.get(reverse("user_list"))
        self.assertEqual(rsp.status_code, 302)
        self.assertIn("/accounts/login/", rsp["Location"])

    def test_anonym_bekommt_auf_erfundene_adressen_eine_404(self):
        """Otherwise every typo would look like a hidden page."""
        rsp = self.client.get(self.NIRGENDWO)

        self.assertEqual(rsp.status_code, 404)
        self.assertTemplateUsed(rsp, "404.html")

    def test_zaehler_landet_auf_der_sperrseite(self):
        self.client.force_login(benutzer_anlegen())
        rsp = self.client.get(reverse("user_list"))
        self.assertRedirects(rsp, reverse("kein_zugriff"))

    def test_zaehler_landet_auch_bei_erfundenen_adressen_dort(self):
        """A 404 would be an invitation for them to keep trying."""
        self.client.force_login(benutzer_anlegen())
        rsp = self.client.get(self.NIRGENDWO)

        self.assertRedirects(rsp, reverse("kein_zugriff"))

    def test_die_sperrseite_verlangt_selbst_eine_anmeldung(self):
        rsp = self.client.get(reverse("kein_zugriff"))

        self.assertEqual(rsp.status_code, 302)
        self.assertIn("/accounts/login/", rsp["Location"])

    #: What the website's scripts send with every request ('im_hintergrund').
    SKRIPT = {"X-Requested-With": "fetch"}

    def test_ein_skript_hoert_401_statt_der_anmeldeseite(self):
        """'fetch' follows a redirect by itself, and the sign-in page answers 200.

        Until 10/2026 a save in the background counted as done after the
        session had expired. GET as for "Refresh", POST as for a save.
        """
        for methode in (self.client.get, self.client.post):
            with self.subTest(methode=methode.__name__):
                rsp = methode(reverse("user_list"), headers=self.SKRIPT)

                self.assertEqual(rsp.status_code, 401)
                self.assertFalse(rsp.has_header("Location"))
                self.assertEqual(rsp.content, b"")

    def test_ein_skript_hoert_auf_erfundenen_adressen_weiter_404(self):
        """A 401 there would say that something lies behind the address."""
        rsp = self.client.get(self.NIRGENDWO, headers=self.SKRIPT)

        self.assertEqual(rsp.status_code, 404)

    def test_ein_skript_findet_auch_den_echten_admin_nicht(self):
        rsp = self.client.get(reverse("admin:index"), headers=self.SKRIPT)

        self.assertEqual(rsp.status_code, 404)

    def test_das_skript_eines_zaehlers_hoert_403(self):
        """The role is gone, the session is not: signing in anew would not help."""
        self.client.force_login(benutzer_anlegen())
        for adresse in (reverse("user_list"), self.NIRGENDWO):
            with self.subTest(adresse=adresse):
                rsp = self.client.post(adresse, headers=self.SKRIPT)

                self.assertEqual(rsp.status_code, 403)
                self.assertFalse(rsp.has_header("Location"))

    def test_gesperrte_seite_zeigt_den_weg_in_die_app(self):
        """Whoever lands here wants to count and should not have to search for the address."""
        self.client.force_login(benutzer_anlegen())
        rsp = self.client.get(reverse("user_list"), follow=True)
        self.assertContains(rsp, reverse("pwa"))
        self.assertTrue(oeffnet_neuen_tab(rsp, reverse("pwa")))

    def test_fuehrungskraft_sieht_die_404_mit_navigationsleiste(self):
        """They may be here. A click back is worth more than a bare page."""
        self.client.force_login(benutzer_anlegen(is_staff=True))
        rsp = self.client.get(self.NIRGENDWO)

        self.assertEqual(rsp.status_code, 404)
        self.assertContains(rsp, reverse("inventur_list"), status_code=404)
        self.assertContains(rsp, "Stammdaten", status_code=404)

    def test_fuehrungskraft_findet_die_app_in_der_navigationsleiste(self):
        """The app opens in a new tab. The website stays where it was."""
        self.client.force_login(benutzer_anlegen(is_staff=True))
        rsp = self.client.get(reverse("inventur_list"))
        self.assertContains(rsp, "Zähl-App")
        self.assertTrue(oeffnet_neuen_tab(rsp, reverse("pwa")))

    def test_nur_admins_finden_die_admin_oberflaeche_in_der_leiste(self):
        """It lies under a random address. Without a link it would have to be memorised."""
        adresse = reverse("admin:index")

        self.client.force_login(benutzer_anlegen(is_staff=True))
        self.assertNotContains(self.client.get(reverse("inventur_list")), adresse)

        self.client.force_login(
            benutzer_anlegen(
                username="ada", email="ada@example.com", is_staff=True, is_superuser=True
            )
        )
        self.assertContains(self.client.get(reverse("inventur_list")), adresse)

    def test_login_seite_ist_frei(self):
        self.assertEqual(self.client.get(reverse("login")).status_code, 200)

    def test_honeypot_ist_frei(self):
        """The wrong admin must be reachable anonymously, otherwise it records nothing.

        Without the exception the middleware would send the seeker to the real
        login page and thereby tell them where the real sign-in is.
        """
        rsp = self.client.get(reverse("honeypot:login"))
        self.assertEqual(rsp.status_code, 200)

    def test_honeypot_zeichnet_den_versuch_auf(self):
        self.client.post(reverse("honeypot:login"), {"username": "admin", "password": "hunter2"})
        self.assertEqual(Anmeldeversuch.objects.get().username, "admin")

    def test_attrappe_sieht_aus_wie_die_echte_anmeldung(self):
        """Down to the character: a foreign form would immediately give away what lies here.

        The whole source is compared, not only what the eye sees. The message of
        the admin form ("staff account") and its required-field marker would
        otherwise stand invisibly in it.
        """
        for daten in (None, {"username": "root", "password": "hunter2"}):
            with self.subTest(daten=daten):
                echt = self._maske(reverse("login"), daten)
                attrappe = self._maske(reverse("honeypot:login"), daten)

                self.assertEqual(attrappe, echt)

    def test_attrappe_traegt_dieselben_cache_kopfzeilen(self):
        """The real login says 'no-store'. Without it the decoy was told apart in one request.

        Under a stopped clock: 'Expires' is the time of the response, and two
        requests can lie on either side of a second. Then the test failed,
        now and again, until 10/2026.
        """
        jetzt = time.time()
        with mock.patch("time.time", return_value=jetzt):
            echt = self.client.get(reverse("login"))
            attrappe = self.client.get(reverse("honeypot:login"))
        # The clock stood for both. Otherwise this check would wobble again unnoticed.
        self.assertEqual(echt["Expires"], http_date(jetzt))
        for kopfzeile in ("Cache-Control", "Expires"):
            with self.subTest(kopfzeile=kopfzeile):
                self.assertEqual(attrappe.get(kopfzeile), echt.get(kopfzeile))

    def _maske(self, adresse: str, daten: dict | None) -> str:
        """The page's source without the CSRF token, because that always changes."""
        rsp = self.client.post(adresse, daten) if daten else self.client.get(adresse)
        self.assertEqual(rsp.status_code, 200)
        quelltext = re.sub(r'value="[A-Za-z0-9]{32,}"', 'value="TOKEN"', rsp.content.decode())
        # The language choice returns to its own page. That the decoy lies under
        # another address gives nothing away.
        return re.sub(r'name="weiter" value="[^"]*"', 'name="weiter" value="HIER"', quelltext)

    def test_echter_admin_bleibt_geschuetzt(self):
        """The exception applies to '/admin/'. The real admin lies under ADMIN_URL."""
        self.client.force_login(benutzer_anlegen())
        rsp = self.client.get(reverse("admin:index"))
        self.assertRedirects(rsp, reverse("kein_zugriff"))

    def test_echter_admin_gibt_es_fuer_anonyme_gar_nicht(self):
        """A redirect to the login would be the confirmation that the guessed
        address is right, while every other one gets a 404.
        """
        rsp = self.client.get(reverse("admin:index"))

        self.assertEqual(rsp.status_code, 404)

    def test_auch_ohne_schraegstrich_verraet_der_admin_sich_nicht(self):
        """Otherwise APPEND_SLASH would show the way: the redirect only exists
        when the page exists.
        """
        rsp = self.client.get(reverse("admin:index").rstrip("/"))

        self.assertEqual(rsp.status_code, 404)

    def test_angemeldeter_admin_kommt_hinein(self):
        self.client.force_login(
            benutzer_anlegen(
                username="ada", email="ada2@example.com", is_staff=True, is_superuser=True
            )
        )
        rsp = self.client.get(reverse("admin:index"))

        self.assertEqual(rsp.status_code, 200)


class KontenInDerVerwaltung(TestCase):
    """The administration creates accounts with the same form as the site.

    The fields of 'UserAdmin' do not fit: the page died of 'usable_password',
    and it did not even ask for the e-mail (required and unique here). No test
    called it, which is why it only showed up in the browser.
    """

    @classmethod
    def setUpTestData(cls):
        cls.chef = benutzer_anlegen(
            username="chef", email="chef@example.com", is_staff=True, is_superuser=True
        )
        cls.haus = Haus.objects.create(name="Kino Ost")

    def setUp(self):
        self.client.force_login(self.chef)

    def _angaben(self, username, email):
        return {
            "username": username,
            "email": email,
            "first_name": "Ada",
            "last_name": "Lovelace",
            "passwort_setzen": "on",
            "password1": "ein-langes-Passwort-7",  # gitleaks:allow
            "password2": "ein-langes-Passwort-7",  # gitleaks:allow
            "haeuser": [self.haus.pk],
        }

    def test_die_seiten_laden(self):
        for name, adresse in {
            "Liste": reverse("admin:accounts_customuser_changelist"),
            "Anlegen": reverse("admin:accounts_customuser_add"),
            "Ändern": reverse("admin:accounts_customuser_change", args=[self.chef.pk]),
        }.items():
            with self.subTest(name):
                self.assertEqual(self.client.get(adresse).status_code, 200)

    def test_anlegen_fragt_nach_der_email_und_dem_haus(self):
        seite = self.client.get(reverse("admin:accounts_customuser_add")).content.decode()
        for feld in ("email", "first_name", "last_name", "password1", "is_staff", "haeuser"):
            with self.subTest(feld):
                self.assertIn(f'name="{feld}"', seite)
        # An account without a password does not exist, not even through the administration.
        self.assertNotIn('name="usable_password"', seite)

    def test_zwei_konten_nacheinander(self):
        """Without e-mail in the form the second one would have failed on the empty address."""
        for username, email in (("ada", "ada@example.com"), ("bob", "bob@example.com")):
            antwort = self.client.post(
                reverse("admin:accounts_customuser_add"), self._angaben(username, email)
            )
            self.assertEqual(antwort.status_code, 302, username)

        ada = CustomUser.objects.get(username="ada")
        self.assertEqual(ada.email, "ada@example.com")
        self.assertTrue(ada.check_password("ein-langes-Passwort-7"))
        self.assertEqual(list(ada.haeuser.all()), [self.haus])

    def test_vergebene_email_wird_gemeldet(self):
        antwort = self.client.post(
            reverse("admin:accounts_customuser_add"), self._angaben("ada", "chef@example.com")
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertFalse(CustomUser.objects.filter(username="ada").exists())


class UserViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.nord = Haus.objects.create(name="Kino Nord")
        cls.sued = Haus.objects.create(name="Kino Süd")

    def setUp(self):
        self.chefin = benutzer_anlegen(
            username="chefin",
            email="chefin@example.com",
            first_name="Carla",
            last_name="Chefin",
            is_staff=True,
        )
        self.client.force_login(self.chefin)

    def zaehlerin(self):
        return benutzer_anlegen(
            username="zoe",
            email="zoe@example.com",
            first_name="Zoe",
            last_name="Zählerin",
        )

    def test_liste(self):
        zaehlerin = self.zaehlerin()
        zaehlerin.haeuser.add(self.nord)
        rsp = self.client.get(reverse("user_list"))
        self.assertEqual(rsp.status_code, 200)
        self.assertTemplateUsed(rsp, "accounts/user_list.html")
        self.assertContains(rsp, "Zoe Zählerin")
        self.assertContains(rsp, "Kino Nord")

    def test_liste_zeigt_fuehrungskraefte_ueberall(self):
        rsp = self.client.get(reverse("user_list"))
        self.assertContains(rsp, "Carla Chefin")
        self.assertContains(rsp, "alle Häuser")

    def test_detail(self):
        zaehlerin = self.zaehlerin()
        zaehlerin.haeuser.add(self.nord)
        rsp = self.client.get(zaehlerin.get_absolute_url())
        self.assertEqual(rsp.status_code, 200)
        self.assertContains(rsp, "zoe@example.com")
        self.assertContains(rsp, "Kino Nord")

    def test_detail_ohne_haus(self):
        rsp = self.client.get(self.zaehlerin().get_absolute_url())
        self.assertContains(rsp, "keinem Haus zugeordnet")

    def test_detail_einer_fuehrungskraft_nennt_alle_haeuser(self):
        rsp = self.client.get(self.chefin.get_absolute_url())
        self.assertContains(rsp, "Führungskraft")
        self.assertContains(rsp, "alle Häuser")
        self.assertNotContains(rsp, "zählen überall")

    def test_anlegen_mit_haeusern(self):
        rsp = self.client.post(
            reverse("user_add"),
            {
                "username": "zaehlerin",
                "email": "zaehlerin@example.com",
                "first_name": "Zoe",
                "last_name": "Zählerin",
                "passwort_setzen": "on",
                "password1": PASSWORT,
                "password2": PASSWORT,
                "haeuser": [self.nord.pk, self.sued.pk],
            },
        )
        self.assertRedirects(rsp, reverse("user_list"))
        neu = User.objects.get(username="zaehlerin")
        self.assertEqual([h.name for h in neu.haeuser.all()], ["Kino Nord", "Kino Süd"])
        self.assertFalse(neu.is_staff)

    def test_anlegen_ohne_pflichtfelder_schlaegt_fehl(self):
        rsp = self.client.post(reverse("user_add"), {"username": "unvollständig"})
        self.assertEqual(rsp.status_code, 200)
        for feld in ("email", "first_name", "last_name"):
            self.assertIn(feld, rsp.context["form"].errors)

    def test_bearbeiten_aendert_hauszuordnung(self):
        self.chefin.haeuser.add(self.nord)
        rsp = self.client.post(
            reverse("user_edit", args=[self.chefin.pk]),
            {
                "username": self.chefin.username,
                "email": self.chefin.email,
                "first_name": self.chefin.first_name,
                "last_name": self.chefin.last_name,
                "is_staff": "on",
                "is_active": "on",
                "haeuser": [self.sued.pk],
            },
        )
        self.assertRedirects(rsp, self.chefin.get_absolute_url())
        self.assertEqual(list(self.chefin.haeuser.all()), [self.sued])

    def test_loeschen(self):
        opfer = benutzer_anlegen(username="weg", email="weg@example.com")
        rsp = self.client.post(reverse("user_delete", args=[opfer.pk]))
        self.assertRedirects(rsp, reverse("user_list"))
        self.assertFalse(User.objects.filter(pk=opfer.pk).exists())


class AdminsSindGeschuetztTests(TestCase):
    """Administrators may only be edited by admins, everyone else also by
    managers.
    """

    @classmethod
    def setUpTestData(cls):
        cls.chefin = benutzer_anlegen(
            username="chefin",
            email="chefin@example.com",
            first_name="Carla",
            last_name="Chefin",
            is_staff=True,
        )
        cls.admin = User.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password=PASSWORT,
            first_name="Ada",
            last_name="Admin",
        )
        cls.zaehlerin = benutzer_anlegen(
            username="zoe", email="zoe@example.com", first_name="Zoe", last_name="Zählerin"
        )

    def setUp(self):
        self.client.force_login(self.chefin)

    def test_wer_wessen_formular_oeffnen_darf(self):
        faelle = (
            # who, whose account, status
            ("chefin", "admin", 403),
            ("chefin", "zaehlerin", 200),
            ("chefin", "chefin", 200),
            ("admin", "admin", 200),
            ("admin", "chefin", 200),
        )
        for wer, wen, status in faelle:
            with self.subTest(wer=wer, wen=wen):
                self.client.force_login(getattr(self, wer))
                antwort = self.client.get(reverse("user_edit", args=[getattr(self, wen).pk]))
                self.assertEqual(antwort.status_code, status)

    def test_fuehrungskraft_kann_admin_nicht_loeschen(self):
        rsp = self.client.post(reverse("user_delete", args=[self.admin.pk]))
        self.assertEqual(rsp.status_code, 403)
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())

    def test_admin_bleibt_auch_ueber_das_formular_unangetastet(self):
        """Not only block the page. The POST has to bounce off as well."""
        rsp = self.client.post(
            reverse("user_edit", args=[self.admin.pk]),
            {
                "username": "admin",
                "email": "admin@example.com",
                "first_name": "Ada",
                "last_name": "Admin",
                "is_active": "",
            },
        )
        self.assertEqual(rsp.status_code, 403)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_detailseite_bleibt_sichtbar_ohne_knoepfe(self):
        rsp = self.client.get(reverse("user_detail", args=[self.admin.pk]))
        self.assertEqual(rsp.status_code, 200)
        self.assertContains(rsp, "Administratoren ändert nur ein Administrator")
        self.assertNotContains(rsp, reverse("user_delete", args=[self.admin.pk]))

    def test_admin_sieht_die_knoepfe(self):
        self.client.force_login(self.admin)
        rsp = self.client.get(reverse("user_detail", args=[self.admin.pk]))
        self.assertContains(rsp, reverse("user_delete", args=[self.admin.pk]))


class RollenwahlImFormular(TestCase):
    """What the role is called and what it does with the house assignment."""

    @classmethod
    def setUpTestData(cls):
        cls.chef = CustomUser.objects.create_user(
            username="chef99",
            password="geheim",
            email="chef99@example.com",
            first_name="Chris",
            last_name="Chef",
            is_staff=True,
            is_superuser=True,
        )
        cls.haus = Haus.objects.create(name="Kino Ost")

    def setUp(self):
        self.client.force_login(self.chef)

    def test_feld_heisst_fuehrungskraft(self):
        """The label "Staff status" is misleading, so the field is called "manager" instead."""
        rsp = self.client.get(reverse("user_add"))
        self.assertContains(rsp, "Führungskraft")
        self.assertNotContains(rsp, "Mitarbeiter-Status")

    def test_aktiv_raet_nicht_zum_deaktivieren_statt_loeschen(self):
        """Django's text said "instead of deleting". Here whoever leaves is deleted.

        On the site's form and in the administration (until 10/2026 both showed Django's).
        """
        zaehlerin = CustomUser.objects.create_user(
            username="zoe", email="zoe@example.com", password="geheim"
        )
        for adresse in (
            reverse("user_edit", args=[zaehlerin.pk]),
            reverse("admin:accounts_customuser_change", args=[zaehlerin.pk]),
        ):
            with self.subTest(adresse=adresse):
                rsp = self.client.get(adresse)
                self.assertContains(rsp, "Wer den Betrieb verlässt, wird gelöscht.")
                self.assertNotContains(rsp, "anstatt Benutzer zu löschen")
        with translation.override("en"):
            hilfe = CustomUserChangeForm(instance=zaehlerin).fields["is_active"].help_text
            self.assertIn("Whoever leaves the business is deleted.", str(hilfe))

    def test_skript_findet_seine_beiden_stellen(self):
        """The hiding hangs on these ids from crispy-forms."""
        rsp = self.client.get(reverse("user_add"))
        self.assertContains(rsp, 'id="id_is_staff"')
        self.assertContains(rsp, 'id="div_id_haeuser"')
        # And the script that looks for exactly these two.
        self.assertContains(rsp, "js/role_choice.js")
        self.assertIn('querySelector("#div_id_haeuser")', skript("role_choice"))

    def test_fuehrungskraft_ohne_haeuser_anlegen(self):
        rsp = self.client.post(
            reverse("user_add"),
            {
                "username": "neue_chefin",
                "email": "neue_chefin@example.com",
                "first_name": "Nina",
                "last_name": "Neu",
                "is_staff": "on",
                "passwort_setzen": "on",
                "password1": "sehr-geheimes-passwort-123",  # gitleaks:allow
                "password2": "sehr-geheimes-passwort-123",  # gitleaks:allow
            },
        )
        self.assertRedirects(rsp, reverse("user_list"))
        neue = CustomUser.objects.get(username="neue_chefin")
        self.assertTrue(neue.is_staff)
        self.assertEqual(neue.haeuser.count(), 0)

    def test_zuordnung_bleibt_beim_aufstieg_erhalten(self):
        """Hidden, not emptied. A way back should not be punished."""
        zaehler = CustomUser.objects.create_user(
            username="zaehler99",
            password="geheim",
            email="zaehler99@example.com",
            first_name="Zoe",
            last_name="Zähler",
        )
        zaehler.haeuser.add(self.haus)

        self.client.post(
            reverse("user_edit", args=[zaehler.pk]),
            {
                "username": "zaehler99",
                "email": "zaehler99@example.com",
                "first_name": "Zoe",
                "last_name": "Zähler",
                "is_staff": "on",
                "is_active": "on",
                "haeuser": [self.haus.pk],
            },
        )
        zaehler.refresh_from_db()
        self.assertTrue(zaehler.is_staff)
        self.assertEqual(list(zaehler.haeuser.all()), [self.haus])


class AnmeldeversucheBegrenzen(TestCase):
    """After five failed attempts it is over.

    Both login paths go through Django's 'authenticate()': the form here and
    the token endpoint of the counting app. The bolt ('axes') sits in front
    and thus applies to both. It is checked at both doors because the
    rejection looks different (page here, JSON there).

    Locked is **user name and address together**: the name alone would let
    anyone who knows it lock out an account.
    """

    @classmethod
    def setUpTestData(cls):
        cls.benutzer = benutzer_anlegen(username="kasse", password=PASSWORT)
        benutzer_anlegen(username="theke", email="theke@example.com", password=PASSWORT)

    def daneben(self, benutzername="kasse", male=1):
        for _ in range(male):
            antwort = self.client.post(
                reverse("login"), {"username": benutzername, "password": "falsch"}
            )
        return antwort

    def test_vier_fehlversuche_gehen_durch(self):
        """Whoever mistypes gets the form again."""
        self.assertEqual(self.daneben(male=4).status_code, 200)

    def test_beim_fuenften_ist_schluss(self):
        """The attempt that reaches the limit is already rejected itself."""
        antwort = self.daneben(male=5)
        self.assertEqual(antwort.status_code, 429)
        self.assertContains(antwort, "Zu viele Fehlversuche", status_code=429)

    def test_die_sperrseite_nennt_die_dauer_in_minuten(self):
        """Until 10/2026 the raw 'timedelta' stood there: "Die Sperre gilt 0:30:00"."""
        antwort = self.daneben(male=5)
        self.assertContains(antwort, "Die Sperre gilt 30 Minuten", status_code=429)

    def test_die_sperrseite_sagt_dass_auch_ein_neues_passwort_wartet(self):
        """A new password does not lift the lock. Until 10/2026 the page left that out."""
        antwort = self.daneben(male=5)
        self.assertContains(
            antwort, "Auch mit einem neuen Passwort geht es von hier aus erst", status_code=429
        )
        self.assertContains(antwort, "kann sie in der Verwaltung früher aufheben", status_code=429)

    def test_die_sperre_gilt_auch_fuers_richtige_passwort(self):
        """Otherwise it would only be a brake that does not stop guessing."""
        self.daneben(male=5)
        antwort = self.client.post(
            reverse("login"), {"username": "kasse", "password": PASSWORT}, follow=True
        )
        self.assertEqual(antwort.status_code, 429)
        self.assertFalse(antwort.wsgi_request.user.is_authenticated)

    def test_ein_anderes_konto_bleibt_offen(self):
        """Otherwise an attacker with foreign names would lock out everyone in turn."""
        self.daneben(male=5)
        antwort = self.client.post(
            reverse("login"), {"username": "theke", "password": PASSWORT}, follow=True
        )
        self.assertTrue(antwort.wsgi_request.user.is_authenticated)

    def test_wer_sich_richtig_anmeldet_faengt_wieder_bei_null_an(self):
        """Three typos and then correct. That must not linger."""
        self.daneben(male=3)
        self.client.post(reverse("login"), {"username": "kasse", "password": PASSWORT})
        self.client.logout()
        self.assertEqual(self.daneben(male=4).status_code, 200)


class DieAttrappeSperrtWieDieEchte(TestCase):
    """Wrong passwords at the decoy count as at the real login, under the same bolt.

    Until 10/2026 the decoy answered every attempt with the form, at once and
    without hashing. Seven wrong passwords told it apart: the real login said
    429 from the fifth on, the decoy never. And a lock at the decoy left the
    real login open, which a real admin behind the same sign-in would not.
    """

    @classmethod
    def setUpTestData(cls):
        benutzer_anlegen(username="kasse", password=PASSWORT)

    def versuche(self, adresse: str, name: str, male: int = 7) -> list[int]:
        """The status of each attempt, every one with another wrong password."""
        daten = [{"username": name, "password": f"falsch-{nummer}"} for nummer in range(male)]
        return [self.client.post(adresse, eintrag).status_code for eintrag in daten]

    @staticmethod
    def quelltext(antwort) -> str:
        """The page without what always differs: the CSRF token and the way back."""
        text = re.sub(r'value="[A-Za-z0-9]{32,}"', 'value="TOKEN"', antwort.content.decode())
        return re.sub(r'name="weiter" value="[^"]*"', 'name="weiter" value="HIER"', text)

    def test_sieben_falsche_passwoerter(self):
        for name in ("kasse", "niemand"):
            with self.subTest(name=name):
                AccessAttempt.objects.all().delete()
                echt = self.versuche(reverse("login"), name)
                AccessAttempt.objects.all().delete()
                attrappe = self.versuche(reverse("honeypot:login"), name)

                self.assertEqual(echt, [200] * 4 + [429] * 3)
                self.assertEqual(attrappe, echt)

    def test_die_sperrseite_ist_dieselbe(self):
        daten = {"username": "kasse", "password": "falsch"}
        self.versuche(reverse("login"), "kasse", male=4)
        echt = self.client.post(reverse("login"), daten)
        AccessAttempt.objects.all().delete()
        self.versuche(reverse("honeypot:login"), "kasse", male=4)
        attrappe = self.client.post(reverse("honeypot:login"), daten)

        self.assertEqual(attrappe.status_code, 429)
        self.assertEqual(self.quelltext(attrappe), self.quelltext(echt))

    def test_die_sperre_gilt_an_beiden_tueren(self):
        self.versuche(reverse("honeypot:login"), "kasse", male=5)

        antwort = self.client.post(reverse("login"), {"username": "kasse", "password": PASSWORT})

        self.assertEqual(antwort.status_code, 429)
        self.assertFalse(antwort.wsgi_request.user.is_authenticated)

    def test_der_hasher_rechnet_so_oft_wie_an_der_echten_tuer(self):
        """Once per attempt, and not at all while locked: the time tells nothing either."""
        hasher = get_hasher()
        gezaehlt = {}
        for adresse in (reverse("login"), reverse("honeypot:login")):
            AccessAttempt.objects.all().delete()
            with mock.patch.object(
                type(hasher), "encode", autospec=True, side_effect=type(hasher).encode
            ) as encode:
                self.versuche(adresse, "kasse")
            gezaehlt[adresse] = encode.call_count

        self.assertEqual(list(gezaehlt.values()), [5, 5])

    def test_ohne_passwort_nur_die_meldung_am_feld(self):
        """As at the real login: a missing field is no wrong password, and counts nothing."""
        daten = {"username": "kasse", "password": ""}
        echt = self.client.post(reverse("login"), daten)
        attrappe = self.client.post(reverse("honeypot:login"), daten)

        self.assertEqual(self.quelltext(attrappe), self.quelltext(echt))
        self.assertFalse(AccessAttempt.objects.exists())


class NulZeichenAnDerAttrappe(TestCase):
    """A NUL in what the decoy records: PostgreSQL refuses it in a text column.

    The real login refuses such a name at its field and shows the form. The
    decoy answered with a server error, and one request told it apart (until
    10/2026). SQLite stores the NUL, so the tests also look at what was kept.
    """

    def setUp(self):
        self.client.raise_request_exception = False

    @staticmethod
    def quelltext(antwort) -> str:
        return DieAttrappeSperrtWieDieEchte.quelltext(antwort)

    def test_ein_name_mit_nul(self):
        daten = {"username": "ad\x00min", "password": "falsch"}
        echt = self.client.post(reverse("login"), daten)
        attrappe = self.client.post(reverse("honeypot:login"), daten)

        self.assertEqual(attrappe.status_code, 200)
        self.assertEqual(self.quelltext(attrappe), self.quelltext(echt))
        self.assertEqual(Anmeldeversuch.objects.get().username, "ad\ufffdmin")


class AttrappeSpeichertBegrenzt(TestCase):
    """One knock at the decoy stored up to 12 KB for 30 days, with no limit per sender.

    Until 10/2026 a flood of anonymous attempts could fill the database.
    """

    def klopfen(self, **kopf):
        return self.client.post(
            reverse("honeypot:login"), {"username": "admin", "password": "x"}, **kopf
        )

    def test_browserkennung_und_adresse_gekuerzt(self):
        self.client.post(
            reverse("honeypot:login") + "?" + "q" * 3000,
            {"username": "admin", "password": "x"},
            HTTP_USER_AGENT="B" * 8000,
        )
        versuch = Anmeldeversuch.objects.get()
        self.assertEqual(len(versuch.user_agent or ""), 255)
        self.assertEqual(len(versuch.path or ""), 255)

    def test_je_absender_und_stunde_zehn(self):
        antworten = [self.klopfen() for _ in range(12)]
        self.assertEqual(Anmeldeversuch.objects.count(), 10)
        # The decoy answers as the real login, recorded or not: locked from the fifth on.
        self.assertEqual([antwort.status_code for antwort in antworten], [200] * 4 + [429] * 8)

    def test_ein_anderer_absender_wird_weiter_aufgezeichnet(self):
        for _ in range(10):
            self.klopfen()
        self.klopfen(REMOTE_ADDR="10.0.0.2")
        self.assertEqual(Anmeldeversuch.objects.filter(ip_address="10.0.0.2").count(), 1)

    def test_nach_einer_stunde_wieder(self):
        for _ in range(10):
            self.klopfen()
        Anmeldeversuch.objects.update(timestamp=timezone.now() - timedelta(hours=2))
        self.klopfen()
        self.assertEqual(Anmeldeversuch.objects.count(), 11)

    def test_die_zaehlung_je_absender_hat_einen_index(self):
        """Counted before every knock. Without it every knock read the whole table."""
        tabelle = Anmeldeversuch._meta.db_table
        with connection.cursor() as cursor:
            schranken = connection.introspection.get_constraints(cursor, tabelle)
        spalten = [eintrag["columns"] for eintrag in schranken.values() if eintrag["index"]]
        self.assertIn(["ip_address", "timestamp"], spalten)


class UeberlangeBenutzernamen(TestCase):
    """A name longer than any account's is refused like a wrong one, and the attempt counts.

    'axes' and the decoy stored it as typed. Their columns hold 255
    characters, and under PostgreSQL a longer name ended in a server error at
    the login, at the token endpoint of the app and at the decoy (until
    10/2026). The attempt counted nowhere. SQLite refuses nothing, so the
    tests look at what was stored.
    """

    LANG = "x" * 300

    def anmelden(self):
        return self.client.post(reverse("login"), {"username": self.LANG, "password": "falsch"})

    def test_die_anmeldung_weist_ab_wie_immer(self):
        antwort = self.anmelden()

        self.assertEqual(antwort.status_code, 200)
        [fehler] = antwort.context["form"].non_field_errors().as_data()
        self.assertEqual(fehler.code, "invalid_login")
        versuch = AccessAttempt.objects.get()
        self.assertEqual(versuch.username, self.LANG[:NAMENSLAENGE])
        self.assertEqual(versuch.failures_since_start, 1)

    def test_der_fehlversuch_zaehlt_bis_zur_sperre(self):
        for _ in range(4):
            self.assertEqual(self.anmelden().status_code, 200)
        self.assertEqual(self.anmelden().status_code, 429)

    def test_der_token_endpunkt_weist_ab_wie_immer(self):
        antwort = self.client.post(
            reverse("token_holen", args=["v1"]),
            {"username": self.LANG, "password": "falsch"},
            content_type="application/json",
        )

        self.assertEqual(antwort.status_code, 401)
        self.assertEqual(AccessAttempt.objects.get().username, self.LANG[:NAMENSLAENGE])

    def test_die_attrappe_zeichnet_gekappt_auf(self):
        antwort = self.client.post(
            reverse("honeypot:login"), {"username": self.LANG, "password": "falsch"}
        )

        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(Anmeldeversuch.objects.get().username, self.LANG[:NAMENSLAENGE])

    def test_ein_name_in_voller_laenge_bleibt_wie_er_ist(self):
        """Cut is only what no account can carry."""
        name = "y" * NAMENSLAENGE
        self.client.post(reverse("login"), {"username": name, "password": "falsch"})
        self.assertEqual(AccessAttempt.objects.get().username, name)


@override_settings(
    AXES_IPWARE_META_PRECEDENCE_ORDER=("HTTP_X_FORWARDED_FOR", "REMOTE_ADDR"),
    AXES_IPWARE_PROXY_COUNT=0,
)
class SperreHinterDemProxy(TestCase):
    """Behind Caddy the caller is in 'X-Forwarded-For', not in 'REMOTE_ADDR'.

    There only Caddy remains, the same address for everyone. If 'axes' read
    only that, it would find none at all behind the proxy and lock the name
    alone: anyone who knows a user name could lock out the account for everyone.
    """

    @classmethod
    def setUpTestData(cls):
        benutzer_anlegen(username="kasse", password=PASSWORT)

    def anmelden(self, passwort, von):
        return self.client.post(
            reverse("login"),
            {"username": "kasse", "password": passwort},
            HTTP_X_FORWARDED_FOR=von,
            REMOTE_ADDR="172.18.0.4",
        )

    def test_ein_fremder_sperrt_den_namen_nicht_fuer_alle(self):
        for _ in range(5):
            self.anmelden("falsch", "198.51.100.23")
        antwort = self.anmelden(PASSWORT, "203.0.113.7")
        self.assertEqual(antwort.status_code, 302)
        self.assertTrue(antwort.wsgi_request.user.is_authenticated)

    def test_von_derselben_adresse_bleibt_es_gesperrt(self):
        for _ in range(5):
            self.anmelden("falsch", "198.51.100.23")
        self.assertEqual(self.anmelden(PASSWORT, "198.51.100.23").status_code, 429)

    def test_gezaehlt_wird_die_weitergereichte_adresse(self):
        from axes.models import AccessAttempt  # noqa: PLC0415

        self.anmelden("falsch", "198.51.100.23")
        self.assertEqual(
            list(AccessAttempt.objects.values_list("ip_address", flat=True)), ["198.51.100.23"]
        )


class LetzterAdministrator(TestCase):
    """Without an administrator nobody gets into the administration any more."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = administrator()

    def setUp(self):
        self.client.force_login(self.admin)

    def daten(self, **abweichend):
        return {
            "username": self.admin.username,
            "email": "chef@example.org",
            "first_name": "C",
            "last_name": "D",
            "is_staff": "on",
        } | abweichend

    def test_laesst_sich_nicht_deaktivieren(self):
        antwort = self.client.post(reverse("user_edit", args=[self.admin.pk]), self.daten())
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "letzte Administrator")
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_laesst_sich_nicht_loeschen(self):
        antwort = self.client.post(reverse("user_delete", args=[self.admin.pk]), follow=True)
        self.assertContains(antwort, "letzte Administrator")
        self.assertTrue(CustomUser.objects.filter(pk=self.admin.pk).exists())

    def test_mit_einem_zweiten_geht_beides(self):
        administrator(username="zweiter", email="zweiter@example.org")
        self.client.post(reverse("user_edit", args=[self.admin.pk]), self.daten())
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.is_active)


class Aufraeumen(TestCase):
    """The daily clean-up of what operation leaves behind."""

    def test_entfernt_alte_versuche_und_abgelaufene_sitzungen(self):
        alt = Anmeldeversuch.objects.create(username="x", ip_address="127.0.0.1", path="/admin/")
        Anmeldeversuch.objects.filter(pk=alt.pk).update(
            timestamp=timezone.now() - timedelta(days=31)
        )
        frisch = Anmeldeversuch.objects.create(username="y", ip_address="127.0.0.1", path="/admin/")
        fehlversuch = AccessAttempt.objects.create(
            username="x", ip_address="127.0.0.1", failures_since_start=1
        )
        AccessAttempt.objects.filter(pk=fehlversuch.pk).update(
            attempt_time=timezone.now() - timedelta(days=31)
        )
        Sitzung.objects.create(
            session_key="abgelaufen",
            session_data="",
            expire_date=timezone.now() - timedelta(days=1),
        )
        Sitzung.objects.create(
            session_key="gueltig", session_data="", expire_date=timezone.now() + timedelta(days=1)
        )
        ausgabe = io.StringIO()

        call_command("cleanup", stdout=ausgabe)

        self.assertEqual(list(Anmeldeversuch.objects.values_list("pk", flat=True)), [frisch.pk])
        self.assertFalse(AccessAttempt.objects.exists())
        self.assertEqual(list(Sitzung.objects.values_list("session_key", flat=True)), ["gueltig"])
        self.assertIn("2 alte Anmeldeversuche", ausgabe.getvalue())

    def test_das_protokoll_der_verwaltung_geht_nach_einem_jahr(self):
        """It names the changed record, a person too. Until 10/2026 it stayed for good."""
        konto = benutzer_anlegen()
        art = ContentType.objects.get_for_model(CustomUser)
        alt, frisch = (
            LogEntry.objects.create(
                user=konto,
                content_type=art,
                object_id=str(konto.pk),
                object_repr=name,
                action_flag=CHANGE,
            )
            for name in ("Zoe Zeisig", "Kim Kranich")
        )
        LogEntry.objects.filter(pk=alt.pk).update(action_time=timezone.now() - timedelta(days=366))
        LogEntry.objects.filter(pk=frisch.pk).update(
            action_time=timezone.now() - timedelta(days=364)
        )
        ausgabe = io.StringIO()

        call_command("cleanup", stdout=ausgabe)

        self.assertEqual(list(LogEntry.objects.values_list("pk", flat=True)), [frisch.pk])
        self.assertIn("1 Einträge der Verwaltung", ausgabe.getvalue())

    def test_alte_anfragen_zum_zuruecksetzen_gehen_mit(self):
        alt = Passwortanfrage.objects.create(email="a@example.org", ip_address="10.0.0.1")
        Passwortanfrage.objects.filter(pk=alt.pk).update(
            zeitpunkt=timezone.now() - timedelta(days=31)
        )
        frisch = Passwortanfrage.objects.create(email="b@example.org", ip_address="10.0.0.1")
        call_command("cleanup", stdout=io.StringIO())
        self.assertEqual(list(Passwortanfrage.objects.values_list("pk", flat=True)), [frisch.pk])

    def test_abgelaufene_token_der_app_gehen_mit(self):
        konto = benutzer_anlegen()
        OutstandingToken.objects.create(
            user=konto, jti="alt", token="x", expires_at=timezone.now() - timedelta(days=1)
        )
        OutstandingToken.objects.create(
            user=konto, jti="neu", token="y", expires_at=timezone.now() + timedelta(days=1)
        )
        call_command("cleanup", stdout=io.StringIO())
        self.assertEqual(list(OutstandingToken.objects.values_list("jti", flat=True)), ["neu"])


class GeloeschtesKonto(TestCase):
    """What the lock-out kept about the name goes with the account."""

    def test_fehlversuche_und_attrappenbesuche_verschwinden(self):
        konto = benutzer_anlegen(username="geht")
        AccessAttempt.objects.create(
            username="geht", ip_address="127.0.0.1", failures_since_start=1
        )
        Anmeldeversuch.objects.create(username="geht", ip_address="127.0.0.1", path="/admin/")
        AccessAttempt.objects.create(
            username="bleibt", ip_address="127.0.0.1", failures_since_start=1
        )
        self.client.force_login(administrator())

        self.client.post(reverse("user_delete", args=[konto.pk]))

        self.assertFalse(CustomUser.objects.filter(pk=konto.pk).exists())
        self.assertEqual(list(AccessAttempt.objects.values_list("username", flat=True)), ["bleibt"])
        self.assertFalse(Anmeldeversuch.objects.filter(username="geht").exists())


class SpracheBeimAnlegen(TestCase):
    """The invitation goes out in the language chosen for the account, not the server's."""

    def setUp(self):
        self.leitung = fuehrungskraft(sprache="en", email="leitung@example.org")
        self.client.force_login(self.leitung)

    def test_die_eigene_sprache_ist_vorbelegt(self):
        antwort = self.client.get(reverse("user_add"))
        self.assertEqual(antwort.context["form"].initial["sprache"], "en")

    def test_die_gewaehlte_sprache_gilt_fuer_konto_und_einladung(self):
        with self.settings(EMAIL_HOST="mail.example.org", DEFAULT_FROM_EMAIL="x@example.org"):
            self.client.post(
                reverse("user_add"),
                {
                    "username": "neu",
                    "email": "neu@example.org",
                    "first_name": "N",
                    "last_name": "N",
                    "haeuser": [],
                    "sprache": "en",
                },
            )
        konto = CustomUser.objects.get(username="neu")
        self.assertEqual(konto.sprache, "en")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Your access", mail.outbox[0].subject)


class AbgelaufeneSitzung(TestCase):
    """The sign-in page can say why typed inputs are gone."""

    def test_der_hinweis_steht_versteckt_bereit(self):
        antwort = self.client.get(reverse("login"))
        self.assertContains(antwort, "data-abgemeldet hidden")
        self.assertContains(antwort, "Die Anmeldung war abgelaufen")
        self.assertContains(antwort, "js/login_notice.js")
        self.assertIn('sessionStorage.getItem("abgemeldet")', skript("login_notice"))
