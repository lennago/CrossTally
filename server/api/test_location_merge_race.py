"""Merging two locations while the app creates a row at the location that goes away.

The merge deletes the location that goes and locks it first
('katalog.ort_split_merge.zusammenfuehren'). A new row of the app locks its
location before it is written ('serializers.ort_sperren'). A row that comes
first stops the merge at PROTECT, and the page names the inventory. One
that comes later finds the location gone and hears 400 in the app's words.
Until 10/2026 the row was written onto the location being deleted, and its
commit ended in a server error. Forced here after 'validate', and on
PostgreSQL with two connections in 'OrteGleichzeitig'.
"""

import threading
from decimal import Decimal
from http import HTTPStatus
from unittest import mock, skipUnless

from django.db import connection
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient, APITransactionTestCase

from accounts.models import CustomUser
from api import serializers as api_serializers
from api.serializers import ORT_WEG
from api.serializers import counting as counting_serializers
from api.test_closing_race import _bis_jemand_wartet
from api.test_merge_split_race import abfragen_waehrend
from api.tests import STICHTAG, ApiGrundlagen, adresse
from django_project.testing import inventur_anlegen
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
from katalog.ort_split_merge import zusammenfuehren
from organisation.models import Bereich, Haus, Ort


class OrtZwischenPruefungUndSchreiben(ApiGrundlagen):
    """The merge slips in after 'validate' has looked and before the row is written."""

    def setUp(self):
        super().setUp()
        self.anmelden()
        # A second location in the area of the counter, nothing counted there.
        self.vitrine = Ort.objects.create(name="Vitrine", bereich=self.theke.bereich)

    def neue_zeile(self):
        return self.client.post(
            adresse("zaehlposition-list"),
            {
                "inventur": self.inventur.pk,
                "ort": self.vitrine.pk,
                "artikel": self.cola.pk,
                "gebinde": self.kasten.pk,
                "eingabemenge": "4",
            },
            format="json",
        )

    def zusammenfuehren(self):
        zusammenfuehren(self.theke, Ort.objects.get(pk=self.vitrine.pk), "Thekenfront")

    def pruefe_abgelehnt(self, rsp):
        self.assertEqual(rsp.status_code, HTTPStatus.BAD_REQUEST, rsp.data)
        self.assertEqual([str(text) for text in rsp.data["ort"]], [str(ORT_WEG)])
        self.assertFalse(Ort.objects.filter(pk=self.vitrine.pk).exists())
        self.assertFalse(Zaehlposition.objects.filter(ort_id=self.vitrine.pk).exists())

    def test_eine_neue_zeile_hoert_dass_ihr_ort_zusammengefuehrt_ist(self):
        echt = api_serializers.vorhandene_zeile
        gelaufen = []

        def nachsehen(daten):
            vorhanden = echt(daten)
            if not gelaufen:
                gelaufen.append(True)
                self.zusammenfuehren()
            return vorhanden

        with mock.patch.object(counting_serializers, "vorhandene_zeile", side_effect=nachsehen):
            rsp = self.neue_zeile()

        self.pruefe_abgelehnt(rsp)

    def test_dieselbe_antwort_wenn_der_ort_schon_vorher_zusammengefuehrt_war(self):
        """The job waited out a dead spot, the merge came before it: not DRF's "Invalid pk"."""
        self.zusammenfuehren()

        self.pruefe_abgelehnt(self.neue_zeile())

    def test_das_zusammenfuehren_sperrt_den_wegfallenden_ort_zuerst(self):
        """Before it reads anything, by number and without the area and house of the ordering."""
        quelle = Ort.objects.get(pk=self.vitrine.pk)

        abfragen = abfragen_waehrend(lambda: zusammenfuehren(self.theke, quelle, "Thekenfront"))

        modell, gesperrt, ohne_schluessel, reihenfolge, sql = abfragen[0]
        self.assertEqual((modell, gesperrt, ohne_schluessel), (Ort, True, False), sql)
        self.assertEqual(reihenfolge, ("pk",))
        self.assertNotIn("JOIN", sql.upper())


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class OrteGleichzeitig(APITransactionTestCase):
    """A new row of the app and the merge of its location on the website, in both orders.

    The side that holds the location first finishes. Each side waits until
    the other one waits for a lock ('test_closing_race._bis_jemand_wartet').
    Runs in the CI, which tests against PostgreSQL.
    """

    serialized_rollback = True

    def setUp(self):
        haus = Haus.objects.create(name="Kino Ost")
        bereich = Bereich.objects.create(name="Theke", haus=haus)
        self.theke = Ort.objects.create(name="Thekenfront", bereich=bereich)
        self.vitrine = Ort.objects.create(name="Vitrine", bereich=bereich)
        ware = Ware.objects.create(
            name="Artikel C", basiseinheit=Basiseinheit.objects.get(name="Stück(e)")
        )
        self.kasten = Gebinde.objects.create(
            ware=ware, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        self.artikel = Hausartikel.objects.create(
            haus=haus,
            ware=ware,
            warengruppe=Warengruppe.objects.create(name="Warengruppe G"),
            artikelnummer="101001",
            name="Artikel C",
        )
        ErwartungGebinde.objects.create(
            erwartung=Erwartung.objects.create(ort=self.theke, artikel=self.artikel),
            gebinde=self.kasten,
        )
        self.inventur = inventur_anlegen(haus, stichtag=STICHTAG)
        self.inventur.zaehlpositionen_anlegen()
        self.konto = CustomUser.objects.create_user(
            username="erste", password="geheim", email="erste@example.org"
        )
        self.konto.haeuser.add(haus)
        self.inventur.zaehlende.add(self.konto)
        self.inventur.zaehlung_starten()
        self.chef = CustomUser.objects.create_superuser(
            username="chef", password="geheim", email="chef@example.org"
        )

    def anlegen(self):
        """Four crates at the showcase, where nobody expected the article."""
        client = APIClient(raise_request_exception=False)
        client.force_authenticate(self.konto)
        return client.post(
            adresse("zaehlposition-list"),
            {
                "inventur": self.inventur.pk,
                "ort": self.vitrine.pk,
                "artikel": self.artikel.pk,
                "gebinde": self.kasten.pk,
                "eingabemenge": "4",
            },
            format="json",
        )

    def zusammenfuehren(self):
        """The showcase goes into the counter, through the page as a manager does it."""
        client = Client(raise_request_exception=False)
        client.force_login(self.chef)
        return client.post(
            reverse("ort_zusammenfuehren", args=[self.theke.pk]),
            {"quelle": self.vitrine.pk, "name": "Thekenfront", "gezeigt": self.vitrine.pk},
        )

    def gleichzeitig(self, *, app_zuerst: bool) -> dict:
        """Runs both in two threads, the first one held while it holds the location.

        The merge is held once it has deleted the showcase and renames the
        counter ('Ort.save'), the new row right before it is written
        ('Zaehlposition.save').
        """
        ziel, name = (Zaehlposition, "save") if app_zuerst else (Ort, "save")
        gehalten = threading.Event()
        fertig = {"app": threading.Event(), "seite": threading.Event()}
        ergebnis: dict = {}
        echt = getattr(ziel, name)
        warten_auf = fertig["seite"] if app_zuerst else fertig["app"]

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
                threading.Thread(
                    target=laufen, args=("seite", self.zusammenfuehren, not app_zuerst)
                ),
            ]
            for faden in faeden:
                faden.start()
            for faden in faeden:
                faden.join()
        return ergebnis

    def test_die_neue_zeile_wartet_aufs_zusammenfuehren_und_hoert_dass_ihr_ort_fehlt(self):
        ergebnis = self.gleichzeitig(app_zuerst=False)

        self.assertEqual(ergebnis["seite"].status_code, HTTPStatus.FOUND)
        antwort = ergebnis["app"]
        self.assertEqual(antwort.status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual([str(text) for text in antwort.data["ort"]], [str(ORT_WEG)])
        self.assertFalse(Ort.objects.filter(pk=self.vitrine.pk).exists())
        self.assertFalse(Zaehlposition.objects.filter(ort_id=self.vitrine.pk).exists())

    def test_das_zusammenfuehren_wartet_auf_die_neue_zeile_und_nennt_die_inventur(self):
        ergebnis = self.gleichzeitig(app_zuerst=True)

        antwort = ergebnis["app"]
        self.assertEqual(antwort.status_code, HTTPStatus.CREATED, antwort.data)
        seite = ergebnis["seite"]
        self.assertEqual(seite.status_code, HTTPStatus.OK)
        self.assertIn("Zählpositionen", str(seite.context["form"].errors["quelle"]))
        self.assertTrue(Ort.objects.filter(pk=self.vitrine.pk).exists())
        self.assertEqual(Zaehlposition.objects.get(ort=self.vitrine).eingabemenge, Decimal("4"))
