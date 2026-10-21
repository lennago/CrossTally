"""Deleting an area while the app creates a row at one of its locations.

The area takes its locations along. A new row of the app locks its location
('api.serializers.counting.ort_sperren'), so the area's page locks its locations
before PROTECT looks ('BereichDeleteView.form_valid'). A row created first is
seen and becomes the page's message, one that comes later finds its location
gone and hears 400. Until 10/2026 nothing was locked: a row created in the
same moment landed on a location the area took along, and one of the two
commits ended in a server error. SQLite locks nothing: 'ErstGesperrt' reads
the lock off the queries, 'Gleichzeitig' forces the overlap on PostgreSQL.
"""

import threading
from decimal import Decimal
from http import HTTPStatus
from unittest import mock, skipUnless

from django.db import connection
from django.test import Client, TestCase
from django.urls import reverse
from rest_framework.test import APIClient, APITransactionTestCase

from accounts.models import CustomUser
from api.serializers import ORT_WEG
from api.test_closing_race import _bis_jemand_wartet
from api.test_merge_split_race import abfragen_waehrend
from api.tests import STICHTAG, adresse
from django_project.testing import fuehrungskraft, inventur_anlegen
from inventur.models import Zaehlposition
from katalog.models import (
    Basiseinheit,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Ware,
    Warengruppe,
)
from organisation.models import Bereich, Haus, Ort


class MitLager:
    """A counter expected at the counter, and a store area with a cold room nobody expects it in."""

    haus: Haus
    lager: Bereich
    kuehlraum: Ort
    artikel: Hausartikel
    kasten: Gebinde

    def lager_anlegen(self) -> None:
        self.haus = Haus.objects.create(name="Kino Ost")
        theke = Ort.objects.create(
            name="Thekenfront", bereich=Bereich.objects.create(name="Theke", haus=self.haus)
        )
        self.lager = Bereich.objects.create(name="Lager", haus=self.haus)
        self.kuehlraum = Ort.objects.create(name="Kühlraum", bereich=self.lager)
        ware = Ware.objects.create(
            name="Artikel C", basiseinheit=Basiseinheit.objects.get(name="Stück(e)")
        )
        self.kasten = Gebinde.objects.create(
            ware=ware, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        self.artikel = Hausartikel.objects.create(
            haus=self.haus,
            ware=ware,
            warengruppe=Warengruppe.objects.create(name="Warengruppe G"),
            artikelnummer="101001",
            name="Artikel C",
        )
        ErwartungGebinde.objects.create(
            erwartung=Erwartung.objects.create(ort=theke, artikel=self.artikel),
            gebinde=self.kasten,
        )


class ErstGesperrt(MitLager, TestCase):
    def setUp(self):
        self.lager_anlegen()

    def test_das_loeschen_sperrt_die_orte_des_bereichs_zuerst(self):
        client = Client()
        client.force_login(fuehrungskraft())
        antworten = []
        abfragen = abfragen_waehrend(
            lambda: antworten.append(client.post(reverse("bereich_delete", args=[self.lager.pk])))
        )

        self.assertEqual(antworten[0].status_code, HTTPStatus.FOUND)
        self.assertFalse(Bereich.objects.filter(pk=self.lager.pk).exists())
        gesperrt = next(
            stelle
            for stelle, (modell, sperre, *_rest) in enumerate(abfragen)
            if modell is Ort and sperre
        )
        _modell, _sperre, ohne_schluessel, reihenfolge, _sql = abfragen[gesperrt]
        self.assertFalse(ohne_schluessel, "ohne Schlüssel, aber die Orte werden gelöscht")
        self.assertEqual(reihenfolge, ("pk",))
        # PROTECT looks at the count positions of the locations: only after the lock.
        geschaut = next(
            stelle for stelle, (modell, *_rest) in enumerate(abfragen) if modell is Zaehlposition
        )
        self.assertLess(gesperrt, geschaut)


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class Gleichzeitig(MitLager, APITransactionTestCase):
    """Both orders, the first side held while it holds its lock ('_bis_jemand_wartet')."""

    serialized_rollback = True

    def setUp(self):
        self.lager_anlegen()
        self.inventur = inventur_anlegen(self.haus, stichtag=STICHTAG)
        self.inventur.zaehlpositionen_anlegen()
        self.konto = CustomUser.objects.create_user(
            username="erste", password="geheim", email="erste@example.org"
        )
        self.konto.haeuser.add(self.haus)
        self.inventur.zaehlende.add(self.konto)
        self.inventur.zaehlung_starten()
        self.chef = fuehrungskraft()

    def anlegen(self):
        """Two crates in the cold room, where nobody expected the article."""
        client = APIClient(raise_request_exception=False)
        client.force_authenticate(self.konto)
        return client.post(
            adresse("zaehlposition-list"),
            {
                "inventur": self.inventur.pk,
                "ort": self.kuehlraum.pk,
                "artikel": self.artikel.pk,
                "gebinde": self.kasten.pk,
                "eingabemenge": "2",
            },
            format="json",
        )

    def loeschen(self):
        client = Client(raise_request_exception=False)
        client.force_login(self.chef)
        return client.post(reverse("bereich_delete", args=[self.lager.pk]))

    def gleichzeitig(self, *, app_zuerst: bool) -> dict:
        """The new row is held right before it is written, the area right before it is deleted."""
        ziel, name = (Zaehlposition, "save") if app_zuerst else (Bereich, "delete")
        echt = getattr(ziel, name)
        gehalten = threading.Event()
        fertig = {"app": threading.Event(), "website": threading.Event()}
        ergebnis: dict = {}
        warten_auf = fertig["website"] if app_zuerst else fertig["app"]

        def halten(*args, **kwargs):
            if not gehalten.is_set():
                gehalten.set()
                _bis_jemand_wartet(warten_auf)
            return echt(*args, **kwargs)

        def laufen(seite, tun, zuerst):
            if not zuerst:
                gehalten.wait(timeout=10)
            try:
                ergebnis[seite] = tun()
            finally:
                fertig[seite].set()
                connection.close()

        with mock.patch.object(ziel, name, autospec=True, side_effect=halten):
            faeden = [
                threading.Thread(target=laufen, args=("app", self.anlegen, app_zuerst)),
                threading.Thread(target=laufen, args=("website", self.loeschen, not app_zuerst)),
            ]
            for faden in faeden:
                faden.start()
            for faden in faeden:
                faden.join()
        return ergebnis

    def test_das_loeschen_wartet_auf_die_neue_zeile(self):
        """The row came first: the area stays, and the page says what holds it."""
        ergebnis = self.gleichzeitig(app_zuerst=True)

        self.assertEqual(ergebnis["app"].status_code, HTTPStatus.CREATED, ergebnis["app"].data)
        self.assertEqual(ergebnis["website"].status_code, HTTPStatus.FOUND)
        self.assertTrue(Bereich.objects.filter(pk=self.lager.pk).exists())
        self.assertTrue(Zaehlposition.objects.filter(ort=self.kuehlraum).exists())

    def test_die_neue_zeile_wartet_aufs_loeschen(self):
        ergebnis = self.gleichzeitig(app_zuerst=False)

        self.assertEqual(ergebnis["website"].status_code, HTTPStatus.FOUND)
        self.assertFalse(Bereich.objects.filter(pk=self.lager.pk).exists())
        self.assertEqual(ergebnis["app"].status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual([str(text) for text in ergebnis["app"].data["ort"]], [str(ORT_WEG)])
