"""Merging while the app creates a row on a pack of the target that takes the source's name.

The rows on such a pack are locked first and touched at the end
('ware_merge.zusammenfuehren'). A new row of the app locks its article
before it reads the pack ('api.serializers.counting.gesperrter_artikel'), one of the
target's, and the merge locks those before its rows. A row that comes later
waits and reads the new name, one that came first is touched with the
others. Until 10/2026 the target's articles were not locked, and a row
created meanwhile kept the old name until the next full sync. Forced on
PostgreSQL with two connections, in the CI as well.
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

from . import ware_merge
from .models import (
    Basiseinheit,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Ware,
    Warengruppe,
)
from .test_admin_delete import Nebeneinander


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class UmbenanntesGebindeDesZiels(Nebeneinander, APITransactionTestCase):
    """The target's crate takes the source's name "Kasten", the app counts on it at the store."""

    serialized_rollback = True

    def setUp(self):
        self.haus = Haus.objects.create(name="Kino Ost")
        bereich = Bereich.objects.create(name="Theke", haus=self.haus)
        theke = Ort.objects.create(name="Thekenfront", bereich=bereich)
        self.lager = Ort.objects.create(name="Trockenlager", bereich=bereich)
        stueck = Basiseinheit.objects.get(name="Stück(e)")
        self.ziel = Ware.objects.create(name="Artikel C", basiseinheit=stueck)
        self.kiste = Gebinde.objects.create(
            ware=self.ziel, name="Kiste", faktor_in_basiseinheit=Decimal("24")
        )
        self.artikel = Hausartikel.objects.create(
            haus=self.haus,
            ware=self.ziel,
            warengruppe=Warengruppe.objects.create(name="Warengruppe G"),
            artikelnummer="101001",
            name="Artikel C",
        )
        ErwartungGebinde.objects.create(
            erwartung=Erwartung.objects.create(ort=theke, artikel=self.artikel),
            gebinde=self.kiste,
        )
        self.quelle = Ware.objects.create(name="Artikel C alt", basiseinheit=stueck)
        self.kasten = Gebinde.objects.create(
            ware=self.quelle, name="Kasten", faktor_in_basiseinheit=Decimal("24")
        )
        self.inventur = inventur_anlegen(self.haus, stichtag=STICHTAG)
        self.inventur.zaehlpositionen_anlegen()
        self.konto = CustomUser.objects.create_user(
            username="erste", password="geheim", email="erste@example.org"
        )
        self.konto.haeuser.add(self.haus)
        self.inventur.zaehlende.add(self.konto)
        self.inventur.zaehlung_starten()

    def zusammenfuehren(self):
        """Same quantity, another name: the preview's choice keeps the source's name."""
        return ware_merge.zusammenfuehren(
            Ware.objects.get(pk=self.quelle.pk),
            Ware.objects.get(pk=self.ziel.pk),
            entscheidungen={str(self.kasten.pk): "quelle"},
        )

    def pruefe_zusammengefuehrt(self):
        self.assertFalse(Ware.objects.filter(pk=self.quelle.pk).exists())
        self.kiste.refresh_from_db()
        self.assertEqual(self.kiste.name, "Kasten")

    def test_die_neue_zeile_wartet_und_liest_den_neuen_namen(self):
        """The merge holds its locks, the row waits for it. Until 10/2026 it read "Kiste"."""
        ergebnis = self.gleichzeitig(
            self.neue_zeile(self.lager, self.artikel, self.kiste),
            self.zusammenfuehren,
            (ware_merge, "_dubletten_aufloesen"),
            app_zuerst=False,
        )

        antwort = ergebnis["app"]
        self.assertEqual(antwort.status_code, HTTPStatus.CREATED, getattr(antwort, "data", ""))
        self.assertEqual(antwort.data["gebinde"]["name"], "Kasten")
        self.pruefe_zusammengefuehrt()

    def test_eine_zeile_davor_kommt_mit_dem_naechsten_abgleich(self):
        """The row holds its article first. The merge waits, then touches it with the others.

        Until 10/2026 it kept the timestamp of its creation, before the new
        name, and the app's next sync did not fetch it again.
        """
        ergebnis = self.gleichzeitig(
            self.neue_zeile(self.lager, self.artikel, self.kiste),
            self.zusammenfuehren,
            (ware_merge, "_dubletten_aufloesen"),
            app_zuerst=True,
        )

        antwort = ergebnis["app"]
        self.assertEqual(antwort.status_code, HTTPStatus.CREATED, getattr(antwort, "data", ""))
        zeile = Zaehlposition.objects.get(pk=antwort.data["id"])
        self.assertGreater(
            zeile.zuletzt_geaendert, parse_datetime(antwort.data["zuletzt_geaendert"])
        )
        self.pruefe_zusammengefuehrt()
