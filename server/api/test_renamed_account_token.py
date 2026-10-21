"""A refresh token renews only for the account it was issued to, whatever name that account has now.

A token names its account by the user name ('USER_ID_FIELD'): a restored
backup renumbers the accounts. Renamed, an account's token named whoever
took the old name. The renewal compared the check value of the password,
and for two accounts with an empty password field that value is the same:
the token of the one renewed as the other, and its device was signed in
as a stranger (until 10/2026). The list of issued tokens knows the account
by its row, so the renewal asks there.
"""

from http import HTTPStatus

from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import CustomUser
from api.tests import adresse


def ohne_passwort(name: str) -> CustomUser:
    """An account whose password field is empty, as only code past the forms leaves it."""
    konto = CustomUser.objects.create_user(
        username=name,
        email=f"{name}@example.com",
        first_name=name.title(),
        last_name="Probe",
        password=None,
    )
    CustomUser.objects.filter(pk=konto.pk).update(password="")
    konto.refresh_from_db()
    return konto


def erneuern(refresh: str):
    return APIClient().post(adresse("token_erneuern"), {"refresh": refresh}, format="json")


class UmbenanntesKonto(TestCase):
    def setUp(self):
        self.anna = ohne_passwort("anna")
        self.berta = ohne_passwort("berta")
        self.handy = str(RefreshToken.for_user(self.anna))

    def namen_tauschen(self) -> None:
        CustomUser.objects.filter(pk=self.anna.pk).update(username="anna2")
        CustomUser.objects.filter(pk=self.berta.pk).update(username="anna")

    def test_das_token_erneuert_nicht_als_wer_den_namen_jetzt_traegt(self):
        self.namen_tauschen()

        antwort = erneuern(self.handy)

        self.assertEqual(antwort.status_code, HTTPStatus.UNAUTHORIZED)
        self.assertFalse(OutstandingToken.objects.filter(user=self.berta).exists())

    def test_ohne_umbenennen_erneuert_es_wie_bisher(self):
        antwort = erneuern(self.handy)

        self.assertEqual(antwort.status_code, HTTPStatus.OK)
        self.assertEqual(
            OutstandingToken.objects.get(jti=RefreshToken(antwort.json()["refresh"])["jti"]).user,
            self.anna,
        )
