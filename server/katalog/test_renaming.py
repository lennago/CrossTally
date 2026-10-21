"""A new name of a product, an article, a pack, a location or an area reaches the app.

The app fetches only the rows that changed since its last sync
('zuletzt_geaendert'), and the rows carry these names. Whoever saves one of
the models with a new name locks the rows that show it and touches them at
the end ('django_project.renaming'). Until 10/2026 the new name reached the
app only with a full sync, and a renamed area stood twice in it. Here the
app's sync is asked afterwards, as the app asks it. 'Gleichzeitig' forces a
new row of the app into the middle of a rename on PostgreSQL.
"""

import sys
from datetime import datetime
from decimal import Decimal
from http import HTTPStatus
from unittest import skipUnless

from django.db import connection, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.test import APITransactionTestCase

from accounts.models import CustomUser
from api.test_merge_split_race import abfragen_waehrend
from api.tests import STICHTAG, ApiGrundlagen, adresse
from django_project.testing import inventur_anlegen
from inventur.models import Zaehlposition
from organisation.models import Bereich, Haus, Ort

from .models import (
    Artikel,
    Basiseinheit,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Ware,
    Warengruppe,
)
from .test_admin_delete import Nebeneinander


class DieAppErfaehrtEs(ApiGrundlagen):
    """Each name a row shows, changed, and what the app's next sync brings."""

    def setUp(self):
        super().setUp()
        self.anmelden()
        # A row of the prepared item too: its own name is what the app shows.
        self.portionszeile = Zaehlposition.objects.create(
            inventur=self.inventur, ort=self.lager, artikel=self.portion
        )
        self.vorher = timezone.now()

    def abgleich(self) -> list[dict]:
        antwort = self.client.get(
            adresse("zaehlposition-list"),
            {"inventur": self.inventur.pk, "geaendert_seit": self.vorher.isoformat()},
        )
        self.assertEqual(antwort.status_code, HTTPStatus.OK, antwort.data)
        return antwort.data

    def umbenennen(self, satz, name: str) -> None:
        satz.name = name
        satz.save()

    def test_die_ware(self):
        self.umbenennen(self.ware, "Artikel C 0,5")
        zeilen = self.abgleich()
        self.assertTrue(zeilen)
        self.assertEqual({zeile["artikel"] for zeile in zeilen}, {"Artikel C 0,5"})

    def test_der_hausartikel(self):
        self.umbenennen(self.cola, "Artikel C Flasche")
        self.assertEqual(
            {zeile["artikel_im_haus"] for zeile in self.abgleich()}, {"Artikel C Flasche"}
        )

    def test_der_rezepturartikel(self):
        self.umbenennen(self.portion, "Anteil N klein")
        self.assertEqual(
            [(zeile["id"], zeile["artikel"]) for zeile in self.abgleich()],
            [(self.portionszeile.pk, "Anteil N klein")],
        )

    def test_das_gebinde(self):
        self.umbenennen(self.kasten, "Kiste")
        zeilen = self.abgleich()
        self.assertTrue(zeilen)
        self.assertEqual({zeile["gebinde"]["name"] for zeile in zeilen}, {"Kiste"})

    def test_der_ort(self):
        self.umbenennen(self.lager, "Kühlraum")
        self.assertEqual(
            [(zeile["id"], zeile["ort"]["name"]) for zeile in self.abgleich()],
            [(self.portionszeile.pk, "Kühlraum")],
        )

    def test_der_bereich(self):
        """The app groups its areas by the name: one touched and one not would stand twice."""
        self.umbenennen(self.theke.bereich, "Bar")
        zeilen = self.abgleich()
        self.assertTrue(zeilen)
        self.assertEqual({zeile["ort"]["bereich"] for zeile in zeilen}, {"Bar"})
        self.assertEqual(
            {zeile["id"] for zeile in zeilen},
            set(self.inventur.zaehlpositionen.filter(ort=self.theke).values_list("pk", flat=True)),
        )

    def test_ueber_die_seite_des_orts(self):
        """The location's page, as an example of every page that saves the model."""
        website = Client()
        website.force_login(
            CustomUser.objects.create_superuser(
                username="chef", password="geheim", email="chef@example.org"
            )
        )
        antwort = website.post(
            reverse("ort_edit", args=[self.lager.pk]),
            {"name": "Kühlraum", "bereich": self.lager.bereich_id},
        )
        self.assertEqual(antwort.status_code, HTTPStatus.FOUND)
        self.assertEqual([zeile["ort"]["name"] for zeile in self.abgleich()], ["Kühlraum"])

    def test_derselbe_name_ruehrt_nichts_an(self):
        """A sync would fetch the rows for nothing."""
        for satz in (self.ware, self.cola, self.kasten, self.lager, self.theke.bereich):
            with self.subTest(type(satz).__name__):
                satz.save()
                self.assertEqual(self.abgleich(), [])

    def test_ohne_den_namen_wird_nicht_einmal_nachgesehen(self):
        """'update_fields' without the name: no query for it."""
        with CaptureQueriesContext(connection) as abfragen:
            self.kasten.save(update_fields=["aktiv"])
        self.assertEqual(len(abfragen.captured_queries), 1, abfragen.captured_queries)


class InDerFolgeGesperrt(ApiGrundlagen):
    """The locks of a rename, read off the queries: what a new row locks first, then the rows."""

    def gesperrt(self, abfragen, modell) -> int:
        stelle = next(
            (
                stelle
                for stelle, (art, sperre, *_rest) in enumerate(abfragen)
                if art is modell and sperre
            ),
            None,
        )
        self.assertIsNotNone(stelle, f"{modell.__name__} nicht gesperrt")
        assert stelle is not None
        return stelle

    def pruefe(self, satz, name: str, zuerst, ohne_schluessel: bool = True):
        satz.name = name
        abfragen = abfragen_waehrend(satz.save)
        vorne = self.gesperrt(abfragen, zuerst)
        zeilen = self.gesperrt(abfragen, Zaehlposition)
        self.assertLess(vorne, zeilen)
        self.assertEqual(abfragen[vorne][2], ohne_schluessel)
        for _modell, sperre, _ohne, reihenfolge, sql in abfragen:
            if sperre:
                self.assertEqual(reihenfolge, ("pk",))
                self.assertNotIn("JOIN", sql.upper())

    def test_ware_gebinde_und_artikel_sperren_die_artikel_zuerst(self):
        for satz, name in ((self.ware, "Artikel C 0,5"), (self.kasten, "Kiste"), (self.cola, "C")):
            with self.subTest(type(satz).__name__):
                self.pruefe(satz, name, Artikel)

    def test_ort_und_bereich_sperren_die_orte_zuerst(self):
        for satz, name in ((self.lager, "Kühlraum"), (self.theke.bereich, "Bar")):
            with self.subTest(type(satz).__name__):
                self.pruefe(satz, name, Ort)


def _halt() -> None:
    """Where a rename waits in 'Gleichzeitig', its locks held and not committed yet."""


#: This module, where 'Gleichzeitig' holds a rename ('_halt').
HIER = sys.modules[__name__]


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class Gleichzeitig(Nebeneinander, APITransactionTestCase):
    """A new row of the app while its product or its location gets a new name, both orders.

    The rename runs as a page saves the model, in one transaction. Runs in
    the CI as well.
    """

    serialized_rollback = True

    def setUp(self):
        haus = Haus.objects.create(name="Kino Ost")
        bereich = Bereich.objects.create(name="Theke", haus=haus)
        theke = Ort.objects.create(name="Thekenfront", bereich=bereich)
        self.lager = Ort.objects.create(name="Trockenlager", bereich=bereich)
        self.ware = Ware.objects.create(
            name="Artikel C", basiseinheit=Basiseinheit.objects.get(name="Stück(e)")
        )
        self.kasten = Gebinde.objects.create(
            ware=self.ware, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        self.artikel = Hausartikel.objects.create(
            haus=haus,
            ware=self.ware,
            warengruppe=Warengruppe.objects.create(name="Warengruppe G"),
            artikelnummer="101001",
            name="Artikel C",
        )
        ErwartungGebinde.objects.create(
            erwartung=Erwartung.objects.create(ort=theke, artikel=self.artikel),
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

    def umbenennen(self, modell, pk: int, name: str):
        def tun():
            with transaction.atomic():
                satz = modell.objects.get(pk=pk)
                satz.name = name
                satz.save()
                _halt()

        return tun

    def zeile(self):
        return self.neue_zeile(self.lager, self.artikel, self.kasten)

    def pruefe_beruehrt(self, antwort) -> None:
        """The row came first, and the rename touched it after: the app's next sync fetches it."""
        self.assertEqual(antwort.status_code, HTTPStatus.CREATED, getattr(antwort, "data", ""))
        zeile = Zaehlposition.objects.get(pk=antwort.data["id"])
        geschickt = parse_datetime(antwort.data["zuletzt_geaendert"])
        assert isinstance(geschickt, datetime)
        self.assertGreater(zeile.zuletzt_geaendert, geschickt)

    def test_die_neue_zeile_liest_den_neuen_namen_der_ware(self):
        """Until 10/2026 it read "Artikel C", the rename was not committed yet."""
        ergebnis = self.gleichzeitig(
            self.zeile(),
            self.umbenennen(Ware, self.ware.pk, "Artikel C 0,5"),
            (HIER, "_halt"),
            app_zuerst=False,
        )

        antwort = ergebnis["app"]
        self.assertEqual(antwort.status_code, HTTPStatus.CREATED, getattr(antwort, "data", ""))
        self.assertEqual(antwort.data["artikel"], "Artikel C 0,5")

    def test_eine_zeile_davor_bekommt_den_neuen_namen_der_ware(self):
        self.pruefe_beruehrt(
            self.gleichzeitig(
                self.zeile(),
                self.umbenennen(Ware, self.ware.pk, "Artikel C 0,5"),
                (HIER, "_halt"),
                app_zuerst=True,
            )["app"]
        )

    def test_die_neue_zeile_liest_den_neuen_namen_des_orts(self):
        """Until 10/2026 it read "Trockenlager": the location as the request had loaded it."""
        ergebnis = self.gleichzeitig(
            self.zeile(),
            self.umbenennen(Ort, self.lager.pk, "Kühlraum"),
            (HIER, "_halt"),
            app_zuerst=False,
        )

        antwort = ergebnis["app"]
        self.assertEqual(antwort.status_code, HTTPStatus.CREATED, getattr(antwort, "data", ""))
        self.assertEqual(antwort.data["ort"]["name"], "Kühlraum")

    def test_eine_zeile_davor_bekommt_den_neuen_namen_des_orts(self):
        self.pruefe_beruehrt(
            self.gleichzeitig(
                self.zeile(),
                self.umbenennen(Ort, self.lager.pk, "Kühlraum"),
                (HIER, "_halt"),
                app_zuerst=True,
            )["app"]
        )
