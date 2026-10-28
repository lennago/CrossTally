"""What the lockout keeps, and how the administration reads and clears it.

Deleting an account takes its failed logins and decoy attempts along on
every path, the "locked" column reads the cool-off, and the caller's address
comes from the headers the lockout trusts, not from any header a caller
sends along (audit of 2026-10-13).
"""

from datetime import timedelta
from http import HTTPStatus
from unittest import mock

from axes.models import AccessAttempt
from django.contrib.admin.sites import site as admin_site
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from django_project.testing import administrator

from .admin import FehlversuchAdmin
from .models import Anmeldeversuch, CustomUser, Fehlversuch, Passwortanfrage


def konto_anlegen(**abweichend) -> CustomUser:
    felder = {
        "username": "nele",
        "password": "geheim",
        "email": "nele@example.com",
        "first_name": "Nele",
        "last_name": "Neu",
    }
    return CustomUser.objects.create_user(**(felder | abweichend))


class SpurenGehenMitDemKonto(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = administrator(email="chef@example.com")

    def setUp(self):
        self.client.force_login(self.admin)
        self.nele = konto_anlegen()
        AccessAttempt.objects.create(
            username="nele",
            ip_address="10.0.0.1",
            failures_since_start=3,
            attempt_time=timezone.now(),
        )
        Anmeldeversuch.objects.create(username="nele", ip_address="10.0.0.1")

    def spuren(self) -> tuple[int, int]:
        return (
            AccessAttempt.objects.filter(username="nele").count(),
            Anmeldeversuch.objects.filter(username="nele").count(),
        )

    def test_einzeln_in_der_verwaltung(self):
        antwort = self.client.post(
            reverse("admin:accounts_customuser_delete", args=[self.nele.pk]), {"post": "yes"}
        )
        self.assertEqual(antwort.status_code, HTTPStatus.FOUND)
        self.assertFalse(CustomUser.objects.filter(username="nele").exists())
        self.assertEqual(self.spuren(), (0, 0))

    def test_im_sammelloeschen(self):
        self.client.post(
            reverse("admin:accounts_customuser_changelist"),
            {"action": "delete_selected", "_selected_action": [self.nele.pk], "post": "yes"},
        )
        self.assertFalse(CustomUser.objects.filter(username="nele").exists())
        self.assertEqual(self.spuren(), (0, 0))


class SpalteGesperrt(TestCase):
    def versuch(self, alter: timedelta) -> Fehlversuch:
        versuch = Fehlversuch.objects.create(
            username=f"nele{alter.total_seconds():.0f}",
            ip_address="10.0.0.7",
            failures_since_start=5,
            attempt_time=timezone.now(),
            user_agent="probe",
        )
        Fehlversuch.objects.filter(pk=versuch.pk).update(attempt_time=timezone.now() - alter)
        versuch.refresh_from_db()
        return versuch

    def test_frisch_gesperrt_und_abgelaufen(self):
        spalte = FehlversuchAdmin(Fehlversuch, admin_site).gesperrt
        self.assertTrue(spalte(self.versuch(timedelta(minutes=5))))
        self.assertFalse(spalte(self.versuch(timedelta(hours=2))))


class OhneMailImHintergrund(TestCase):
    """Asking for a new password sends its mail from a thread of its own ('im_hintergrund').

    That thread outlived its test. It read the accounts while the next test of
    the suite was writing them, and now and then the output showed "database
    table is locked". These tests look at the caller's address, not at the mail.
    """

    def setUp(self):
        super().setUp()
        hintergrund = mock.patch("accounts.password_reset.im_hintergrund")
        self.hintergrund = hintergrund.start()
        self.addCleanup(hintergrund.stop)


@override_settings(AXES_IPWARE_META_PRECEDENCE_ORDER=("REMOTE_ADDR",), AXES_IPWARE_PROXY_COUNT=None)
class AdresseDesAbsendersOhneProxy(OhneMailImHintergrund):
    """Only the headers the lockout trusts count. Without a proxy that is 'REMOTE_ADDR'."""

    def test_eine_mitgeschickte_kopfzeile_zaehlt_nicht(self):
        self.client.post(
            reverse("password_reset"),
            {"email": "niemand@example.com"},
            HTTP_X_FORWARDED_FOR="192.168.1.5",
            HTTP_X_REAL_IP="93.184.216.34",
        )
        self.assertEqual(Passwortanfrage.objects.get().ip_address, "127.0.0.1")
        self.hintergrund.assert_called_once()

    def test_der_koeder_merkt_sich_die_echte_adresse(self):
        self.client.post(
            reverse("honeypot:login"),
            {"username": "x", "password": "y"},
            HTTP_CLIENT_IP="9.9.9.9",
        )
        self.assertEqual(Anmeldeversuch.objects.get().ip_address, "127.0.0.1")


@override_settings(
    AXES_IPWARE_META_PRECEDENCE_ORDER=("HTTP_X_FORWARDED_FOR", "REMOTE_ADDR"),
    AXES_IPWARE_PROXY_COUNT=0,
)
class AdresseDesAbsendersHinterDemProxy(OhneMailImHintergrund):
    """Behind the proxy 'X-Forwarded-For' counts, which Caddy writes itself. Nothing else."""

    def test_x_real_ip_zaehlt_nicht(self):
        for nummer in range(3):
            self.client.post(
                reverse("password_reset"),
                {"email": f"konto{nummer}@example.com"},
                HTTP_X_FORWARDED_FOR="192.168.1.5",
                HTTP_X_REAL_IP=f"93.184.216.{nummer + 1}",
            )
        adressen = set(Passwortanfrage.objects.values_list("ip_address", flat=True))
        self.assertEqual(adressen, {"192.168.1.5"})
        self.assertEqual(self.hintergrund.call_count, 3)
