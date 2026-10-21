"""Mails go out after the commit, not under the locks of the change they tell about.

A mail server that does not answer keeps the sender waiting for its timeout,
ten seconds per mail ('MAILERS'). Sent inside the transaction, the removal
of passkeys in the administration held the rows of every administrator
meanwhile ('administratoren_sperren'), and a confirmed address the row of
its account. Every renewal and sign-in of those accounts waited too. With six
keys the request ran past the worker's minute and rolled back, after the
mails had gone out (until 10/2026).

Every mail notes whether a transaction was still open when it left. Hence a
'TransactionTestCase': in a 'TestCase' the test's own one is always open.
"""

from smtplib import SMTPException
from unittest import mock

from django.core import mail
from django.db import connection
from django.test import Client, TransactionTestCase
from django.urls import reverse

from accounts import invitation, notification
from accounts.models import CustomUser, Passkey
from django_project.testing import administrator

from .email_change import token
from .test_notification import konto_anlegen, verwaltungsformular


class Mitschreiben(TransactionTestCase):
    """An administrator signed in to the administration, and every mail noted."""

    serialized_rollback = True

    def setUp(self):
        self.chef = administrator(email="chef@example.com", first_name="Conrad", last_name="Chef")
        self.nele = konto_anlegen()
        self.verwaltung = Client()
        self.verwaltung.force_login(self.chef)
        #: For every mail sent: whether a transaction was open at that moment.
        self.offen: list[bool] = []

    def mitschreiben(self, *args, **kwargs):
        self.offen.append(connection.in_atomic_block)
        return mail.send_mail(*args, **kwargs)

    def schluessel(self, konto: CustomUser, kennung: str = "abc") -> Passkey:
        return Passkey.objects.create(
            benutzer=konto, kennung=kennung, oeffentlicher_schluessel=b"x", name="Handy"
        )


class NachDemCommit(Mitschreiben):
    def test_ein_entfernter_passkey(self):
        passkey = self.schluessel(self.nele)

        with mock.patch.object(notification, "send_mail", side_effect=self.mitschreiben):
            self.verwaltung.post(
                reverse("admin:accounts_passkey_delete", args=[passkey.pk]), {"post": "yes"}
            )

        self.assertFalse(Passkey.objects.filter(pk=passkey.pk).exists())
        self.assertEqual(self.offen, [False])
        self.assertEqual(mail.outbox[0].to, ["nele@example.com"])

    def test_eine_auswahl_von_passkeys(self):
        zora = konto_anlegen(username="zora", email="zora@example.com", first_name="Zora")
        auswahl = [self.schluessel(self.nele).pk, self.schluessel(zora, "def").pk]

        with mock.patch.object(notification, "send_mail", side_effect=self.mitschreiben):
            self.verwaltung.post(
                reverse("admin:accounts_passkey_changelist"),
                {"action": "delete_selected", "_selected_action": auswahl, "post": "yes"},
            )

        self.assertFalse(Passkey.objects.filter(pk__in=auswahl).exists())
        self.assertEqual(self.offen, [False, False])
        empfaenger = sorted(nachricht.to[0] for nachricht in mail.outbox)
        self.assertEqual(empfaenger, ["nele@example.com", "zora@example.com"])

    def test_die_warnung_kommt_nach_dem_commit_noch_an(self):
        """The message goes into the request that is still under way."""
        passkey = self.schluessel(self.nele)

        with mock.patch.object(notification, "send_mail", side_effect=SMTPException):
            seite = self.verwaltung.post(
                reverse("admin:accounts_passkey_delete", args=[passkey.pk]),
                {"post": "yes"},
                follow=True,
            )

        self.assertFalse(Passkey.objects.filter(pk=passkey.pk).exists())
        self.assertContains(
            seite,
            "Die Benachrichtigung an nele@example.com ging nicht hinaus, der E-Mail-Server "
            "antwortet nicht.",
        )

    def test_eine_bestaetigte_adresse(self):
        link = reverse("email_bestaetigen", kwargs={"token": token(self.nele, "neu@example.org")})

        with mock.patch.object(notification, "send_mail", side_effect=self.mitschreiben):
            Client().post(link)

        self.nele.refresh_from_db()
        self.assertEqual(self.nele.email, "neu@example.org")
        self.assertEqual(self.offen, [False])


class KontenInDerVerwaltung(Mitschreiben):
    """The account pages of the administration save inside Django's transaction.

    Deleting or demoting an administrator holds the rows of every
    administrator there ('letzter_administrator_geht'). The mails about the
    change wait for the commit, as those about passkeys do.
    """

    def test_eine_aenderung(self):
        with mock.patch.object(notification, "send_mail", side_effect=self.mitschreiben):
            self.verwaltung.post(
                reverse("admin:accounts_customuser_change", args=[self.nele.pk]),
                verwaltungsformular(self.nele, last_name="Nachtfalter"),
            )

        self.nele.refresh_from_db()
        self.assertEqual(self.nele.last_name, "Nachtfalter")
        self.assertEqual(self.offen, [False])

    def test_eine_neue_adresse(self):
        with mock.patch.object(notification, "send_mail", side_effect=self.mitschreiben):
            seite = self.verwaltung.post(
                reverse("admin:accounts_customuser_change", args=[self.nele.pk]),
                verwaltungsformular(self.nele, email="nele@example.org"),
                follow=True,
            )

        empfaenger = sorted(nachricht.to[0] for nachricht in mail.outbox)
        self.assertIn("nele@example.org", empfaenger)
        self.assertTrue(self.offen)
        self.assertNotIn(True, self.offen)
        # The notice goes into the request that is still under way.
        self.assertContains(seite, "ist eine Mail mit einem Link unterwegs")

    def test_eine_einladung(self):
        with mock.patch.object(invitation, "send_mail", side_effect=self.mitschreiben):
            self.verwaltung.post(
                reverse("admin:accounts_customuser_add"),
                {
                    "username": "zora",
                    "email": "zora@example.com",
                    "first_name": "Zora",
                    "last_name": "Zeisig",
                    "password1": "",
                    "password2": "",
                },
            )

        self.assertTrue(CustomUser.objects.get(username="zora").ist_eingeladen)
        self.assertEqual(self.offen, [False])

    def test_ein_geloeschtes_konto(self):
        with mock.patch.object(notification, "send_mail", side_effect=self.mitschreiben):
            self.verwaltung.post(
                reverse("admin:accounts_customuser_delete", args=[self.nele.pk]), {"post": "yes"}
            )

        self.assertFalse(CustomUser.objects.filter(pk=self.nele.pk).exists())
        self.assertEqual(self.offen, [False])

    def test_eine_auswahl_von_konten(self):
        zora = konto_anlegen(username="zora", email="zora@example.com", first_name="Zora")

        with mock.patch.object(notification, "send_mail", side_effect=self.mitschreiben):
            self.verwaltung.post(
                reverse("admin:accounts_customuser_changelist"),
                {
                    "action": "delete_selected",
                    "_selected_action": [self.nele.pk, zora.pk],
                    "post": "yes",
                },
            )

        self.assertFalse(CustomUser.objects.filter(pk__in=[self.nele.pk, zora.pk]).exists())
        self.assertEqual(self.offen, [False, False])
