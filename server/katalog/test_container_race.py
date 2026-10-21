"""Deleting a container while the app weighs in it.

A weighing locks its container after its row
('api.serializers.counting.behaelter_sperren'). The website and the
administration lock it before PROTECT looks for its weighings
('GeschuetztesLoeschen.zuerst_sperren', 'VerwaltungSperrtZuerst'). A weighing
that comes first holds the container, and the page says so. One that comes
later finds it gone and hears 400. Until 10/2026 nothing was locked, and one
of the two commits ended in a server error. 'ErstGesperrt' reads the locks
off the queries on SQLite, 'Gleichzeitig' forces the overlap on PostgreSQL.
"""

from decimal import Decimal
from http import HTTPStatus
from unittest import skipUnless

from django.db import connection
from django.db.models.deletion import Collector
from django.test import Client, TestCase
from django.urls import reverse
from rest_framework.test import APIClient, APITransactionTestCase

from accounts.models import CustomUser
from api.serializers import counting
from api.test_merge_split_race import abfragen_waehrend
from api.tests import STICHTAG, adresse
from django_project.testing import inventur_anlegen
from inventur.models import Wiegung, Zaehlposition
from organisation.models import Bereich, Haus, Ort

from .models import Basiseinheit, Behaelter, Hausartikel, Ware, Warengruppe
from .test_admin_delete import Nebeneinander, geschuetzt, loeschseite


class MitSchale:
    """Loose goods weighed at the cold room, in a bowl of the house nothing else is assigned to."""

    haus: Haus
    konto: CustomUser
    chef: CustomUser
    schale: Behaelter
    waage: Zaehlposition

    def schale_anlegen(self) -> None:
        self.haus = Haus.objects.create(name="Kino Ost")
        kuehlraum = Ort.objects.create(
            name="Kühlraum", bereich=Bereich.objects.create(name="Lager", haus=self.haus)
        )
        ware = Ware.objects.create(
            name="Artikel N", basiseinheit=Basiseinheit.objects.get(name="g"), abwiegbar=True
        )
        artikel = Hausartikel.objects.create(
            haus=self.haus,
            ware=ware,
            warengruppe=Warengruppe.objects.create(name="Warengruppe G"),
            artikelnummer="101002",
            name="Artikel N lose",
        )
        # A bowl of the house: the app offers it, and it hangs on no product.
        self.schale = Behaelter.objects.create(name="Schale", tara=Decimal("250"))
        self.schale.haeuser.add(self.haus)
        inventur = inventur_anlegen(self.haus, stichtag=STICHTAG)
        self.waage = Zaehlposition.objects.create(inventur=inventur, ort=kuehlraum, artikel=artikel)
        self.konto = CustomUser.objects.create_user(
            username="erste", password="geheim", email="erste@example.org"
        )
        self.konto.haeuser.add(self.haus)
        inventur.zaehlende.add(self.konto)
        inventur.zaehlung_starten()
        self.chef = CustomUser.objects.create_superuser(
            username="chef", password="geheim", email="chef@example.org"
        )

    def wiegen(self):
        """1250 g in the bowl of 250 g, sent as the app sends it."""

        def senden():
            client = APIClient(raise_request_exception=False)
            client.force_authenticate(self.konto)
            return client.post(
                adresse("wiegung-list"),
                {
                    "zaehlposition": self.waage.pk,
                    "brutto_gramm": "1250",
                    "behaelter": self.schale.pk,
                },
                format="json",
            )

        return senden

    def auf_der_seite(self, client):
        return client.post(reverse("behaelter_delete", args=[self.schale.pk]))


def erste_sperre(test, abfragen, modell) -> int:
    """The place of the first query that locks 'modell'."""
    stelle = next(
        (
            stelle
            for stelle, (art, sperre, *_rest) in enumerate(abfragen)
            if art is modell and sperre
        ),
        None,
    )
    test.assertIsNotNone(stelle, f"{modell.__name__} nicht gesperrt")
    assert stelle is not None
    return stelle


class ErstGesperrt(MitSchale, TestCase):
    def setUp(self):
        self.schale_anlegen()

    def test_die_wiegung_sperrt_ihren_behaelter_nach_der_zeile(self):
        """Without the key, after the row: the last in the order every path locks in."""
        antworten = []
        abfragen = abfragen_waehrend(lambda: antworten.append(self.wiegen()()))

        self.assertEqual(antworten[0].status_code, HTTPStatus.CREATED, antworten[0].data)
        zeile = erste_sperre(self, abfragen, Zaehlposition)
        behaelter = erste_sperre(self, abfragen, Behaelter)
        self.assertLess(zeile, behaelter)
        _modell, _sperre, ohne_schluessel, reihenfolge, sql = abfragen[behaelter]
        self.assertTrue(ohne_schluessel, "FOR UPDATE statt FOR NO KEY UPDATE")
        self.assertEqual(reihenfolge, ("pk",))
        self.assertNotIn("JOIN", sql.upper())

    def test_das_loeschen_sperrt_den_behaelter_bevor_protect_nachsieht(self):
        """On the website and in the administration, 'FOR UPDATE': it is deleted."""
        for name, senden in (
            ("Website", self.auf_der_seite),
            ("Verwaltung", lambda client: loeschseite(client, self.schale)),
        ):
            with self.subTest(name):
                client = Client()
                client.force_login(self.chef)
                antworten = []
                abfragen = abfragen_waehrend(lambda: antworten.append(senden(client)))

                self.assertEqual(antworten[0].status_code, HTTPStatus.FOUND)
                self.assertFalse(Behaelter.objects.filter(pk=self.schale.pk).exists())
                gesperrt = erste_sperre(self, abfragen, Behaelter)
                _modell, _sperre, ohne_schluessel, reihenfolge, sql = abfragen[gesperrt]
                self.assertFalse(ohne_schluessel, "ohne Schlüssel, aber er wird gelöscht")
                self.assertEqual(reihenfolge, ("pk",))
                self.assertNotIn("JOIN", sql.upper())
                # PROTECT looks for the weighings after the lock.
                self.assertTrue(
                    any(modell is Wiegung for modell, *_rest in abfragen[gesperrt + 1 :]),
                    "die Wiegungen vor der Sperre angesehen",
                )
                # Back for the next case.
                self.schale = Behaelter.objects.create(name="Schale", tara=Decimal("250"))
                self.schale.haeuser.add(self.haus)


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class Gleichzeitig(MitSchale, Nebeneinander, APITransactionTestCase):
    """A weighing in the bowl and deleting the bowl, both orders. Runs in the CI as well."""

    serialized_rollback = True

    def setUp(self):
        self.schale_anlegen()
        # Signed in before the race: signing in deletes a session, and the
        # first 'Collector.delete' is to be the bowl's.
        self.seite = Client(raise_request_exception=False)
        self.seite.force_login(self.chef)

    def loeschen(self, senden):
        return lambda: senden(self.seite)

    def test_das_loeschen_wartet_auf_die_wiegung(self):
        """Until 10/2026 the bowl went first, and the weighing's commit failed on its key."""
        ergebnis = self.gleichzeitig(
            self.wiegen(),
            self.loeschen(self.auf_der_seite),
            (Collector, "delete"),
            app_zuerst=True,
        )

        antwort = ergebnis["app"]
        self.assertEqual(antwort.status_code, HTTPStatus.CREATED, getattr(antwort, "data", ""))
        # Back to the bowl's page, with the message what holds it.
        self.assertRedirects(
            ergebnis["verwaltung"], self.schale.get_absolute_url(), fetch_redirect_response=False
        )
        self.assertTrue(Behaelter.objects.filter(pk=self.schale.pk).exists())

    def test_die_wiegung_wartet_aufs_loeschen(self):
        """Until 10/2026 the weighing went first, and the delete's commit failed on its key."""
        ergebnis = self.gleichzeitig(
            self.wiegen(),
            self.loeschen(self.auf_der_seite),
            (Collector, "delete"),
            app_zuerst=False,
        )

        self.assertRedirects(
            ergebnis["verwaltung"], reverse("behaelter_list"), fetch_redirect_response=False
        )
        antwort = ergebnis["app"]
        self.assertEqual(antwort.status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(
            [str(text) for text in antwort.data["behaelter"]], [str(counting.BEHAELTER_WEG)]
        )
        self.assertFalse(Behaelter.objects.filter(pk=self.schale.pk).exists())

    def test_in_der_verwaltung_ebenso(self):
        """Until 10/2026 the administration deleted it past the weighing, and that one failed."""
        ergebnis = self.gleichzeitig(
            self.wiegen(),
            self.loeschen(lambda client: loeschseite(client, self.schale)),
            (Collector, "delete"),
            app_zuerst=True,
        )

        antwort = ergebnis["app"]
        self.assertEqual(antwort.status_code, HTTPStatus.CREATED, getattr(antwort, "data", ""))
        self.assertTrue(geschuetzt(ergebnis["verwaltung"]))
        self.assertTrue(Behaelter.objects.filter(pk=self.schale.pk).exists())
