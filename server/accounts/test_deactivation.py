"""Deactivating an account signs it out everywhere, on the website and in the administration.

Until 10/2026 the tick only made every sign-in wait. The refresh tokens of
the app and the website sessions stayed valid, and switching the account on
again brought them all back, a lost phone's too.
"""

from http import HTTPStatus

from django.test import Client, TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import CustomUser
from accounts.test_notification import formular, verwaltungsformular
from api.tests import adresse
from django_project.testing import administrator, fuehrungskraft


def erneuern(refresh: str):
    return APIClient().post(adresse("token_erneuern"), {"refresh": refresh}, format="json")


class DeaktivierenMeldetUeberallAb(TestCase):
    def setUp(self):
        self.zoe = CustomUser.objects.create_user(
            username="zoe",
            email="zoe@example.com",
            password="x",
            first_name="Zoe",
            last_name="Zähler",
        )
        # A phone that has renewed once, and a browser with a website session.
        self.handy = erneuern(str(RefreshToken.for_user(self.zoe))).data["refresh"]
        self.browser = Client()
        self.browser.force_login(self.zoe)
        self.assertTrue(self.angemeldet())

    def angemeldet(self) -> bool:
        return self.browser.get(reverse("passkey_list")).status_code == HTTPStatus.OK

    def pruefe_abgemeldet(self, wieder_einschalten) -> None:
        self.zoe.refresh_from_db()
        self.assertFalse(self.zoe.is_active)
        wieder_einschalten()
        self.zoe.refresh_from_db()
        self.assertTrue(self.zoe.is_active)
        self.assertEqual(erneuern(self.handy).status_code, HTTPStatus.UNAUTHORIZED)
        self.assertFalse(self.angemeldet())

    def test_auf_der_website(self):
        verwaltung = Client()
        verwaltung.force_login(fuehrungskraft(email="leitung@example.com"))

        def senden(**abweichend):
            antwort = verwaltung.post(
                reverse("user_edit", args=[self.zoe.pk]), formular(self.zoe, **abweichend)
            )
            self.assertEqual(antwort.status_code, HTTPStatus.FOUND)

        senden(is_active=False)
        self.pruefe_abgemeldet(lambda: senden(is_active=True))

    def test_in_der_verwaltung(self):
        verwaltung = Client()
        verwaltung.force_login(administrator(email="chef@example.com"))

        def senden(**abweichend):
            antwort = verwaltung.post(
                reverse("admin:accounts_customuser_change", args=[self.zoe.pk]),
                verwaltungsformular(self.zoe, **abweichend),
            )
            self.assertEqual(antwort.status_code, HTTPStatus.FOUND)

        senden(is_active=False)
        self.pruefe_abgemeldet(lambda: senden(is_active=True))

    def test_eine_andere_aenderung_meldet_niemanden_ab(self):
        verwaltung = Client()
        verwaltung.force_login(fuehrungskraft(email="leitung@example.com"))
        verwaltung.post(
            reverse("user_edit", args=[self.zoe.pk]), formular(self.zoe, last_name="Zaunkönig")
        )
        self.assertEqual(erneuern(self.handy).status_code, HTTPStatus.OK)
        self.assertTrue(self.angemeldet())
