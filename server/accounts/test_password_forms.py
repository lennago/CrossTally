"""A new password writes the password and nothing else, wherever it is set.

Each test changes the account in between, while the form checks: the request
has read the account by then, and nothing is saved yet. Django's forms saved
the whole row as read and undid what came in between. A deleted account came
back with its old password.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from unittest import mock

from axes.models import AccessAttempt
from django.contrib.auth.forms import SetPasswordMixin
from django.contrib.auth.tokens import default_token_generator
from django.test import TestCase
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.models import CustomUser
from django_project.testing import administrator, fuehrungskraft

from .invitation import einladungs_token

PASSWORT = "ziemlich-geheim-2026"
NEU = "ganz-anderes-Passwort-47"


@contextmanager
def dazwischen(tun: Callable[[], object]) -> Iterator[None]:
    """Runs 'tun' once while a password form checks the two new fields."""
    echt = SetPasswordMixin.validate_passwords
    getan = []

    def pruefen(form, *args, **kwargs):
        if not getan:
            getan.append(tun())
        return echt(form, *args, **kwargs)

    with mock.patch.object(SetPasswordMixin, "validate_passwords", pruefen):
        yield


def zurueckgestuft(konto: CustomUser) -> Callable[[], object]:
    """Someone else makes the account a counter, switches it off and gives it English."""
    return lambda: CustomUser.objects.filter(pk=konto.pk).update(
        is_staff=False, is_superuser=False, is_active=False, sprache="en"
    )


def geloescht(konto: CustomUser) -> Callable[[], object]:
    return lambda: CustomUser.objects.filter(pk=konto.pk).delete()


class NurDasPasswort(TestCase):
    def setUp(self):
        self.leitung = fuehrungskraft(
            email="leitung@example.com", first_name="Lea", last_name="Leitung", password=PASSWORT
        )

    def gespeichert(self) -> CustomUser:
        return CustomUser.objects.get(pk=self.leitung.pk)

    def pruefe_zurueckgestuft(self) -> None:
        konto = self.gespeichert()
        self.assertTrue(konto.check_password(NEU))
        self.assertEqual(
            (konto.is_staff, konto.is_superuser, konto.is_active, konto.sprache),
            (False, False, False, "en"),
        )

    def eigenes_aendern(self):
        return self.client.post(
            reverse("password_change"),
            {"old_password": PASSWORT, "new_password1": NEU, "new_password2": NEU},
        )

    def link_einloesen(self, adresse: str):
        weiter = self.client.get(adresse)
        self.assertEqual(weiter.status_code, 302)
        return self.client.post(weiter["Location"], {"new_password1": NEU, "new_password2": NEU})

    def link_zum_zuruecksetzen(self) -> str:
        return reverse(
            "password_reset_confirm",
            kwargs={
                "uidb64": urlsafe_base64_encode(force_bytes(self.leitung.pk)),
                "token": default_token_generator.make_token(self.leitung),
            },
        )

    def test_das_eigene_passwort_laesst_den_rest_stehen(self):
        self.client.force_login(self.leitung)
        with dazwischen(zurueckgestuft(self.leitung)):
            antwort = self.eigenes_aendern()
        self.assertEqual(antwort.status_code, 302)
        self.pruefe_zurueckgestuft()

    def test_der_link_aus_passwort_vergessen_laesst_den_rest_stehen(self):
        with dazwischen(zurueckgestuft(self.leitung)):
            antwort = self.link_einloesen(self.link_zum_zuruecksetzen())
        self.assertRedirects(antwort, reverse("password_reset_complete"))
        self.pruefe_zurueckgestuft()

    def test_die_verwaltung_laesst_den_rest_stehen(self):
        self.client.force_login(administrator(email="chef@example.com"))
        with dazwischen(zurueckgestuft(self.leitung)):
            antwort = self.client.post(
                reverse("admin:auth_user_password_change", args=[self.leitung.pk]),
                {"usable_password": "true", "password1": NEU, "password2": NEU},
            )
        self.assertEqual(antwort.status_code, 302)
        self.pruefe_zurueckgestuft()

    def test_die_einladung_schaltet_ein_und_laesst_den_rest_stehen(self):
        eingeladen = CustomUser.objects.create_user(
            username="nele",
            email="nele@example.com",
            first_name="Nele",
            last_name="Nachtigall",
            password=None,
            is_active=False,
        )
        adresse = reverse(
            "einladung",
            kwargs={
                "uidb64": urlsafe_base64_encode(force_bytes(eingeladen.pk)),
                "token": einladungs_token.make_token(eingeladen),
            },
        )

        def befoerdert():
            return CustomUser.objects.filter(pk=eingeladen.pk).update(
                is_staff=True, last_name="Nachtfalter"
            )

        with dazwischen(befoerdert):
            antwort = self.link_einloesen(adresse)
        self.assertRedirects(antwort, reverse("einladung_fertig"))
        konto = CustomUser.objects.get(pk=eingeladen.pk)
        self.assertTrue(konto.check_password(NEU))
        self.assertTrue(konto.is_active)
        self.assertTrue(konto.is_staff)
        self.assertEqual(konto.last_name, "Nachtfalter")

    def test_ein_geloeschtes_konto_kommt_nicht_wieder(self):
        """Saving raises instead of writing the row anew, with its old password."""
        self.client.force_login(self.leitung)
        with (
            dazwischen(geloescht(self.leitung)),
            self.assertRaises(CustomUser.NotUpdated),
            self.assertLogs("django.request", level="ERROR"),
        ):
            self.eigenes_aendern()
        self.assertFalse(CustomUser.objects.filter(username="leitung").exists())

    def test_ein_geloeschtes_konto_kommt_auch_ueber_den_link_nicht_wieder(self):
        with (
            dazwischen(geloescht(self.leitung)),
            self.assertRaises(CustomUser.NotUpdated),
            self.assertLogs("django.request", level="ERROR"),
        ):
            self.link_einloesen(self.link_zum_zuruecksetzen())
        self.assertFalse(CustomUser.objects.filter(username="leitung").exists())


class AltesPasswortZaehltWieBeimAnmelden(TestCase):
    """A wrong old password counts as a failed sign-in, on the website and in the administration.

    Django's form checked it with 'check_password()', past 'axes': wrong ones
    went uncounted, as many as one liked, and the right one changed the
    password afterwards (until 10/2026). The app has counted at its door all
    along. The fifth wrong one locks, as at the sign-in.
    """

    def raten(self, adresse: str, alt: str = "daneben"):
        return self.client.post(
            adresse, {"old_password": alt, "new_password1": NEU, "new_password2": NEU}
        )

    def pruefe_tuer(self, konto: CustomUser, adresse: str) -> None:
        self.client.force_login(konto)
        for _ in range(4):
            antwort = self.raten(adresse)
            self.assertEqual(antwort.status_code, 200)
            self.assertTrue(antwort.context["form"].has_error("old_password"))
        antwort = self.raten(adresse)
        self.assertContains(antwort, "Zu viele Fehlversuche", status_code=429)
        self.assertEqual(AccessAttempt.objects.get().failures_since_start, 5)

        # The lock holds against the right one too, and the password stays.
        self.assertEqual(self.raten(adresse, alt=PASSWORT).status_code, 429)
        konto.refresh_from_db()
        self.assertTrue(konto.check_password(PASSWORT))
        # It is the bolt of the sign-in: that door is locked from here as well.
        self.client.logout()
        antwort = self.client.post(
            reverse("login"), {"username": konto.username, "password": PASSWORT}
        )
        self.assertEqual(antwort.status_code, 429)

    def test_auf_der_website(self):
        konto = fuehrungskraft(email="leitung@example.com", password=PASSWORT)
        self.pruefe_tuer(konto, reverse("password_change"))

    def test_in_der_verwaltung(self):
        konto = administrator(email="chef@example.com", password=PASSWORT)
        self.pruefe_tuer(konto, reverse("admin:password_change"))

    def test_das_richtige_passwort_aendert_es_weiter(self):
        for konto, adresse in (
            (fuehrungskraft(email="leitung@example.com", password=PASSWORT), "password_change"),
            (administrator(email="chef@example.com", password=PASSWORT), "admin:password_change"),
        ):
            with self.subTest(adresse):
                self.client.force_login(konto)
                self.assertEqual(self.raten(reverse(adresse), alt=PASSWORT).status_code, 302)
                konto.refresh_from_db()
                self.assertTrue(konto.check_password(NEU))
        self.assertFalse(AccessAttempt.objects.exists())
