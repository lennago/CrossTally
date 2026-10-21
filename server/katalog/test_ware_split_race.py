"""Splitting while the app creates a row on an article of the product that stays and is renamed.

The app shows the product's name on every row ('api.master_data.ware_name').
Splitting locks the articles that stay along with those that migrate, then
their rows, and touches them at the end ('ware_split.aufteilen'). A new row
of the app locks its article first ('api.serializers.counting.gesperrter_artikel'):
one that comes later waits and reads the new name, one that came first is
touched with the others. Until 10/2026 neither happened, and the app kept
the old name until the next full sync. Forced on PostgreSQL with two
connections, in the CI as well.
"""

from decimal import Decimal
from http import HTTPStatus
from unittest import skipUnless

from django.db import connection
from django.utils.dateparse import parse_datetime
from rest_framework.test import APITransactionTestCase

from accounts.models import CustomUser
from api.tests import STICHTAG
from django_project.testing import inventur_anlegen
from inventur.models import Zaehlposition
from organisation.models import Bereich, Haus, Ort

from . import ware_split
from .models import Basiseinheit, Gebinde, Hausartikel, Ware, Warengruppe
from .test_admin_delete import Nebeneinander


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class BleibendeWareUmbenannt(Nebeneinander, APITransactionTestCase):
    """Kino Ost keeps the product as "Artikel C klein", Kino Nord's article goes to a new one."""

    serialized_rollback = True

    def setUp(self):
        self.haus = Haus.objects.create(name="Kino Ost")
        self.lager = Ort.objects.create(
            name="Trockenlager", bereich=Bereich.objects.create(name="Lager", haus=self.haus)
        )
        gruppe = Warengruppe.objects.create(name="Warengruppe G")
        self.ware = Ware.objects.create(
            name="Artikel C", basiseinheit=Basiseinheit.objects.get(name="Stück(e)")
        )
        self.kasten = Gebinde.objects.create(
            ware=self.ware, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        self.artikel = Hausartikel.objects.create(
            haus=self.haus, ware=self.ware, warengruppe=gruppe, artikelnummer="101001", name="C"
        )
        self.wandert = Hausartikel.objects.create(
            haus=Haus.objects.create(name="Kino Nord"),
            ware=self.ware,
            warengruppe=gruppe,
            artikelnummer="101002",
            name="C groß",
        )
        self.inventur = inventur_anlegen(self.haus, stichtag=STICHTAG)
        self.konto = CustomUser.objects.create_user(
            username="erste", password="geheim", email="erste@example.org"
        )
        self.konto.haeuser.add(self.haus)
        self.inventur.zaehlende.add(self.konto)
        self.inventur.zaehlung_starten()

    def aufteilen(self):
        return ware_split.aufteilen(
            Ware.objects.get(pk=self.ware.pk), "Artikel C klein", "Artikel C groß", [self.wandert]
        )

    def test_die_neue_zeile_wartet_und_liest_den_neuen_namen(self):
        """The split holds its locks, the row waits. Until 10/2026 it read "Artikel C"."""
        ergebnis = self.gleichzeitig(
            self.neue_zeile(self.lager, self.artikel, self.kasten),
            self.aufteilen,
            (ware_split, "_gebinde_abbilden"),
            app_zuerst=False,
        )

        antwort = ergebnis["app"]
        self.assertEqual(antwort.status_code, HTTPStatus.CREATED, getattr(antwort, "data", ""))
        self.assertEqual(antwort.data["artikel"], "Artikel C klein")

    def test_eine_zeile_davor_kommt_mit_dem_naechsten_abgleich(self):
        """The row holds its article first. The split waits, then touches it with the others."""
        ergebnis = self.gleichzeitig(
            self.neue_zeile(self.lager, self.artikel, self.kasten),
            self.aufteilen,
            (ware_split, "_gebinde_abbilden"),
            app_zuerst=True,
        )

        antwort = ergebnis["app"]
        self.assertEqual(antwort.status_code, HTTPStatus.CREATED, getattr(antwort, "data", ""))
        zeile = Zaehlposition.objects.get(pk=antwort.data["id"])
        self.assertGreater(
            zeile.zuletzt_geaendert, parse_datetime(antwort.data["zuletzt_geaendert"])
        )
        self.assertEqual(Ware.objects.get(pk=self.ware.pk).name, "Artikel C klein")
