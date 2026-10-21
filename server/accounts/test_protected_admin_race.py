"""An administrator is changed only by an administrator, also when the role comes in between.

'AdminsSindGeschuetztMixin' asks when the request begins. An administrator
who promoted the account after that, while a manager's form was on its way,
left the step that writes unasked: the manager's edit went to an
administrator, deactivated or renamed it, and a deletion deleted one (until
10/2026). The step that writes asks once more, under the account's lock.
Here the promotion comes right before that lock, and under PostgreSQL from
the administration while the manager's step waits for it.
"""

from http import HTTPStatus
from unittest import mock, skipUnless

from django.db import connection
from django.test import Client, TestCase
from django.urls import reverse

from accounts import views as konto_ansichten
from accounts.admin import CustomUserAdmin
from accounts.forms import NurGeaendertesMixin
from accounts.models import CustomUser
from accounts.test_notification import formular, verwaltungsformular
from accounts.test_sign_out_race import Nebeneinander
from django_project.testing import administrator, fuehrungskraft


def befoerdern(konto: CustomUser) -> None:
    """What an administrator writes who makes the account one, in the administration."""
    CustomUser.objects.filter(pk=konto.pk).update(is_staff=True, is_superuser=True)


class DieRolleKommtDazwischen(TestCase):
    def setUp(self):
        administrator(email="chef@example.com", first_name="Conrad", last_name="Chef")
        self.zoe = CustomUser.objects.create_user(
            username="zoe",
            email="zoe@example.com",
            password="ziemlich-geheim-2026",
            first_name="Zoe",
            last_name="Zähler",
        )
        self.client.force_login(fuehrungskraft(email="leitung@example.com"))

    def test_bearbeiten_schreibt_nichts(self):
        echt = NurGeaendertesMixin.gesperrt_gelesen

        def dazwischen(form):
            befoerdern(self.zoe)
            return echt(form)

        with mock.patch.object(NurGeaendertesMixin, "gesperrt_gelesen", dazwischen):
            antwort = self.client.post(
                reverse("user_edit", args=[self.zoe.pk]),
                formular(self.zoe, last_name="Zaunkönig", is_active=False),
            )

        self.assertEqual(antwort.status_code, HTTPStatus.FORBIDDEN)
        zoe = CustomUser.objects.get(pk=self.zoe.pk)
        self.assertEqual((zoe.last_name, zoe.is_active, zoe.is_superuser), ("Zähler", True, True))

    def test_loeschen_loescht_nichts(self):
        echt = konto_ansichten.letzter_administrator_geht

        def dazwischen(konten):
            befoerdern(self.zoe)
            return echt(konten)

        with mock.patch.object(konto_ansichten, "letzter_administrator_geht", dazwischen):
            antwort = self.client.post(reverse("user_delete", args=[self.zoe.pk]))

        self.assertEqual(antwort.status_code, HTTPStatus.FORBIDDEN)
        self.assertTrue(CustomUser.objects.filter(pk=self.zoe.pk).exists())

    def test_ohne_befoerderung_geht_beides_wie_bisher(self):
        antwort = self.client.post(
            reverse("user_edit", args=[self.zoe.pk]), formular(self.zoe, last_name="Zaunkönig")
        )
        self.assertEqual(antwort.status_code, HTTPStatus.FOUND)
        self.assertEqual(CustomUser.objects.get(pk=self.zoe.pk).last_name, "Zaunkönig")

        antwort = self.client.post(reverse("user_delete", args=[self.zoe.pk]))
        self.assertEqual(antwort.status_code, HTTPStatus.FOUND)
        self.assertFalse(CustomUser.objects.filter(pk=self.zoe.pk).exists())


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class BefoerdertWaehrendEineFuehrungskraftSchreibt(Nebeneinander):
    """The promotion in the administration holds the account: the manager's step waits for it."""

    def setUp(self):
        super().setUp()
        self.leitung = fuehrungskraft(email="leitung@example.com")

    def befoerdern(self):
        verwaltung = Client(raise_request_exception=False)
        verwaltung.force_login(self.chef)
        return verwaltung.post(
            reverse("admin:accounts_customuser_change", args=[self.zoe.pk]),
            verwaltungsformular(self.zoe, is_staff=True, is_superuser=True),
        )

    def bearbeiten(self):
        browser = Client(raise_request_exception=False)
        browser.force_login(self.leitung)
        return browser.post(
            reverse("user_edit", args=[self.zoe.pk]),
            formular(self.zoe, last_name="Zaunkönig", is_active=False),
        )

    def loeschen(self):
        browser = Client(raise_request_exception=False)
        browser.force_login(self.leitung)
        return browser.post(reverse("user_delete", args=[self.zoe.pk]))

    def test_das_bearbeiten_wartet_und_wird_abgewiesen(self):
        ergebnis = self.gleichzeitig(
            self.befoerdern,
            self.bearbeiten,
            (CustomUserAdmin, "save_related"),
            nach_dem_aufruf=True,
        )

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.FOUND)
        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.FORBIDDEN)
        zoe = CustomUser.objects.get(pk=self.zoe.pk)
        self.assertEqual((zoe.last_name, zoe.is_active, zoe.is_superuser), ("Z", True, True))

    def test_das_loeschen_wartet_und_wird_abgewiesen(self):
        ergebnis = self.gleichzeitig(
            self.befoerdern,
            self.loeschen,
            (CustomUserAdmin, "save_related"),
            nach_dem_aufruf=True,
        )

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.FOUND)
        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.FORBIDDEN)
        self.assertTrue(CustomUser.objects.get(pk=self.zoe.pk).is_superuser)
