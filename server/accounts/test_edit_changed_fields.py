"""Editing an account writes what the form changed, and nothing else of the row.

Django's forms save the whole row of the account as the request read it when
it began, on the website ('UserUpdateView') and in the administration
('CustomUserAdmin.save_model'). Whatever was saved in between went back to
the state of before: a new password, a deactivation, a sign-in, another
language. An account deleted in between came back (until 10/2026). Each test
changes the account while the form checks its values: the request has read
the account by then, and nothing is saved yet. Or right after the saving
step has read the stored state, for an account deleted then.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from unittest import mock

from django.contrib.auth.hashers import make_password
from django.contrib.messages import get_messages
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounts.models import CustomUser
from django_project.testing import administrator, fuehrungskraft
from organisation.models import Haus

from .forms import RollenbeschriftungMixin
from .notification import Kontostand
from .test_notification import PASSWORT, formular, konto_anlegen, verwaltungsformular

NEU = "ganz-anderes-Passwort-47"


@contextmanager
def dazwischen(tun: Callable[[], object]) -> Iterator[None]:
    """Runs 'tun' once while the account form checks its values."""
    echt = RollenbeschriftungMixin.clean
    getan = []

    def pruefen(form):
        if not getan:
            getan.append(tun())
        return echt(form)

    with mock.patch.object(RollenbeschriftungMixin, "clean", pruefen):
        yield


@contextmanager
def nach_dem_lesen(tun: Callable[[], object]) -> Iterator[None]:
    """Runs 'tun' once right after the saving step has read the stored state of the account."""
    echt = Kontostand.aus_der_datenbank
    getan = []

    def lesen(konto):
        stand = echt(konto)
        if not getan:
            getan.append(tun())
        return stand

    with mock.patch.object(Kontostand, "aus_der_datenbank", lesen):
        yield


def meldungen(antwort) -> list[str]:
    return [str(meldung) for meldung in get_messages(antwort.wsgi_request)]


class Grundlage(TestCase):
    """Nele counts. Someone else edits her account."""

    #: Where the form of the account is sent.
    adresse_name = ""

    def setUp(self):
        self.nele = konto_anlegen()

    def nachher(self) -> CustomUser:
        return CustomUser.objects.get(pk=self.nele.pk)

    def daten(self, **abweichend) -> dict:
        raise NotImplementedError

    def bearbeiten(self, **abweichend):
        return self.client.post(
            reverse(self.adresse_name, args=[self.nele.pk]), self.daten(**abweichend)
        )

    def von_dazwischen(self, **felder) -> Callable[[], object]:
        return lambda: CustomUser.objects.filter(pk=self.nele.pk).update(**felder)

    def geloescht(self) -> Callable[[], object]:
        return lambda: CustomUser.objects.filter(pk=self.nele.pk).delete()

    def eingefuegt(self, abfragen: CaptureQueriesContext) -> list[str]:
        """The statements that wrote the account's row anew."""
        anfang = f'INSERT INTO "{CustomUser._meta.db_table}" '
        return [
            abfrage["sql"]
            for abfrage in abfragen.captured_queries
            if abfrage["sql"].startswith(anfang)
        ]

    def pruefe_neues_passwort_bleibt(self) -> None:
        with dazwischen(self.von_dazwischen(password=make_password(NEU))):
            antwort = self.bearbeiten(first_name="Nela")

        self.assertEqual(antwort.status_code, 302)
        konto = self.nachher()
        self.assertTrue(konto.check_password(NEU))
        self.assertEqual(konto.first_name, "Nela")

    def pruefe_abschaltung_bleibt(self) -> None:
        with dazwischen(self.von_dazwischen(is_active=False)):
            antwort = self.bearbeiten(last_name="Nachtfalter")

        self.assertEqual(antwort.status_code, 302)
        konto = self.nachher()
        self.assertFalse(konto.is_active)
        self.assertEqual(konto.last_name, "Nachtfalter")

    def pruefe_anderes_bleibt(self) -> None:
        """Language, appearance and the last sign-in are no fields of the form."""
        angemeldet = timezone.now()
        anders = self.von_dazwischen(sprache="en", darstellung="dark", last_login=angemeldet)
        with dazwischen(anders):
            self.bearbeiten(first_name="Nela")

        konto = self.nachher()
        self.assertEqual((konto.sprache, konto.darstellung), ("en", "dark"))
        self.assertEqual(konto.last_login, angemeldet)
        self.assertEqual(konto.first_name, "Nela")


class AufDerWebsite(Grundlage):
    adresse_name = "user_edit"

    def setUp(self):
        super().setUp()
        self.client.force_login(fuehrungskraft(email="leitung@example.com"))

    def daten(self, **abweichend) -> dict:
        return formular(self.nele, **abweichend)

    def test_ein_neues_passwort_von_dazwischen_bleibt(self):
        self.pruefe_neues_passwort_bleibt()

    def test_eine_abschaltung_von_dazwischen_bleibt(self):
        self.pruefe_abschaltung_bleibt()

    def test_was_sonst_dazwischen_kam_bleibt(self):
        self.pruefe_anderes_bleibt()

    def test_was_das_formular_aendert_wird_geschrieben(self):
        haus = Haus.objects.create(name="Kino Nord")

        antwort = self.bearbeiten(
            username="nela",
            first_name="Nela",
            last_name="Nachtfalter",
            is_staff=True,
            is_active=False,
            haeuser=[haus.pk],
        )

        self.assertRedirects(antwort, self.nele.get_absolute_url())
        konto = self.nachher()
        self.assertEqual(
            (konto.username, konto.first_name, konto.last_name, konto.is_staff, konto.is_active),
            ("nela", "Nela", "Nachtfalter", True, False),
        )
        self.assertEqual(list(konto.haeuser.all()), [haus])
        self.assertTrue(konto.check_password(PASSWORT))

    def pruefe_geloescht(self, antwort) -> None:
        """The list says it, instead of the error page or the account back with its password."""
        self.assertRedirects(antwort, reverse("user_list"), fetch_redirect_response=False)
        self.assertEqual(
            meldungen(antwort),
            [
                "Das Konto „Nele Nachtigall“ gibt es nicht mehr. Deine Änderung ist nicht "
                "gespeichert."
            ],
        )
        self.assertFalse(CustomUser.objects.filter(pk=self.nele.pk).exists())

    def test_ein_geloeschtes_konto_kommt_nicht_wieder(self):
        with dazwischen(self.geloescht()):
            antwort = self.bearbeiten(first_name="Nela")

        self.pruefe_geloescht(antwort)

    def test_auch_nicht_wenn_es_nach_dem_lesen_geht(self):
        """Saved whole, the row would be inserted anew: Django's 'save' falls back to that."""
        with nach_dem_lesen(self.geloescht()), CaptureQueriesContext(connection) as abfragen:
            antwort = self.bearbeiten(first_name="Nela")

        self.pruefe_geloescht(antwort)
        self.assertEqual(self.eingefuegt(abfragen), [])


class InDerVerwaltung(Grundlage):
    adresse_name = "admin:accounts_customuser_change"

    def setUp(self):
        super().setUp()
        self.client.force_login(administrator(email="chef@example.com"))

    def daten(self, **abweichend) -> dict:
        return verwaltungsformular(self.nele, **abweichend)

    def test_ein_neues_passwort_von_dazwischen_bleibt(self):
        self.pruefe_neues_passwort_bleibt()

    def test_eine_abschaltung_von_dazwischen_bleibt(self):
        self.pruefe_abschaltung_bleibt()

    def test_was_sonst_dazwischen_kam_bleibt(self):
        self.pruefe_anderes_bleibt()

    def test_der_administrator_bringt_die_fuehrungskraft_mit(self):
        """Derived in the form ('RollenbeschriftungMixin'): written though the box stayed empty."""
        antwort = self.bearbeiten(is_superuser=True, is_staff=False)

        self.assertEqual(antwort.status_code, 302)
        konto = self.nachher()
        self.assertTrue(konto.is_superuser)
        self.assertTrue(konto.is_staff)

    def pruefe_geloescht(self, antwort, abfragen: CaptureQueriesContext) -> None:
        """The list says it, as on the site.

        The deletion here runs in the request's own transaction, and refusing
        the change rolls it back with it. In operation another request deletes
        and commits. What counts is that the row was not written anew.
        """
        self.assertRedirects(
            antwort,
            reverse("admin:accounts_customuser_changelist"),
            fetch_redirect_response=False,
        )
        self.assertEqual(
            meldungen(antwort),
            [
                "Das Konto „Nele Nachtigall“ gibt es nicht mehr. Deine Änderung ist nicht "
                "gespeichert."
            ],
        )
        self.assertEqual(self.eingefuegt(abfragen), [])
        self.assertFalse(CustomUser.objects.filter(first_name="Nela").exists())

    def test_ein_geloeschtes_konto_kommt_nicht_wieder(self):
        with dazwischen(self.geloescht()), CaptureQueriesContext(connection) as abfragen:
            antwort = self.bearbeiten(first_name="Nela")

        self.pruefe_geloescht(antwort, abfragen)

    def test_auch_nicht_wenn_es_nach_dem_lesen_geht(self):
        with nach_dem_lesen(self.geloescht()), CaptureQueriesContext(connection) as abfragen:
            antwort = self.bearbeiten(first_name="Nela")

        self.pruefe_geloescht(antwort, abfragen)
