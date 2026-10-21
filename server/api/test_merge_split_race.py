"""Merging or splitting a product while the app writes the same row, or creates a new one.

Both hang rows of running inventories onto another pack: merging onto the
pack of the product that stays (its own goes), splitting onto the copy on
the new product. A quantity or a weighing from the app locks the row and
reads it only then ('serializers.gesperrte_zeile'), and it writes nothing
but what it changes. Merging and splitting lock the articles and the rows
first, by number, and touch the rows last
('katalog.models.zaehlpositionen_sperren'). Until 10/2026 the app saved
the row it had loaded before, whole: after merging it wrote back a pack
that no longer existed (400 and the number lost), after splitting the pack
of the old product, which no longer fit the row's article. The order is
forced: here after the view has loaded the row, and in
'GleichzeitigMitDerApp' on PostgreSQL with two connections.

A new row has no row to lock. It locks its article instead and reads
article and pack only then ('serializers.gesperrter_artikel'). Until
10/2026 it was written onto a pack the merge was deleting, and its commit
ended in a server error. Forced the same two ways, here and in
'WaehrendDesUmhaengens'.
"""

import threading
from datetime import datetime
from decimal import Decimal
from http import HTTPStatus
from unittest import mock, skipUnless

from django.db import connection
from django.db.models.sql.compiler import SQLCompiler, SQLInsertCompiler, SQLUpdateCompiler
from django.utils import timezone
from rest_framework.test import APIClient, APITransactionTestCase

from accounts.models import CustomUser
from api import serializers as api_serializers
from api import views as api_views
from api.serializers import GEBINDE_WEG
from api.serializers import counting as counting_serializers
from api.test_closing_race import _bis_jemand_wartet
from api.tests import STICHTAG, ApiGrundlagen, adresse
from django_project.testing import inventur_anlegen
from inventur.models import Inventur, Inventurstatus, Wiegung, Zaehlposition
from katalog.models import (
    Artikel,
    Artikelstatus,
    Basiseinheit,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Ware,
    Warengruppe,
)
from katalog.ware_merge import zusammenfuehren
from katalog.ware_split import aufteilen
from organisation.models import Bereich, Haus, Ort


class UmhaengenZwischenLadenUndSchreiben(ApiGrundlagen):
    """The other side slips in after the view has loaded the row and before it writes."""

    def setUp(self):
        super().setUp()
        self.anmelden()
        self.position = self.inventur.zaehlpositionen.get()  # a crate at the counter

    def nach_dem_laden(self, dazwischen):
        """The view loads the row, then 'dazwischen' runs once, then the view writes."""
        echt = api_views.ZaehlpositionViewSet.get_object
        gelaufen = []

        def laden(view):
            objekt = echt(view)
            if not gelaufen:
                gelaufen.append(True)
                dazwischen()
            return objekt

        return mock.patch.object(
            api_views.ZaehlpositionViewSet, "get_object", autospec=True, side_effect=laden
        )

    def nach_der_pruefung(self, dazwischen):
        """A weighing is checked against its row ('behaelter_erlaubt'), then 'dazwischen' runs."""
        echt = counting_serializers.behaelter_erlaubt

        def pruefen(position, behaelter):
            erlaubt = echt(position, behaelter)
            dazwischen()
            return erlaubt

        return mock.patch.object(counting_serializers, "behaelter_erlaubt", side_effect=pruefen)

    def zaehlen(self, menge="3", vorher=None):
        return self.client.patch(
            adresse("zaehlposition-detail", self.position.pk),
            {"eingabemenge": menge, "eingabemenge_vorher": vorher},
            format="json",
        )

    def entfernen(self, position):
        """What another device's delete writes ('ZaehlpositionViewSet.perform_destroy')."""
        Zaehlposition.objects.filter(pk=position.pk).update(
            aktiv=False, zuletzt_geaendert=timezone.now()
        )

    def waage_mit_schale(self) -> tuple[Zaehlposition, Wiegung]:
        position = Zaehlposition.objects.create(
            inventur=self.inventur, ort=self.lager, artikel=self.nachos
        )
        schale = Wiegung.objects.create(
            zaehlposition=position, brutto_gramm=Decimal("900"), behaelter=self.schale
        )
        return position, schale

    # -- Quantities ----------------------------------------------------------

    def test_nach_dem_aufteilen_steht_die_menge_am_abbild(self):
        bericht = {}

        def aufteilen_jetzt():
            bericht["neu"] = aufteilen(
                self.ware, "Artikel C 0,33", "Artikel C 0,33 Ost", [self.cola]
            ).neu

        with self.nach_dem_laden(aufteilen_jetzt):
            rsp = self.zaehlen()

        self.assertEqual(rsp.status_code, HTTPStatus.OK, rsp.data)
        abbild = Gebinde.objects.get(ware=bericht["neu"], name="Kasten")
        self.position.refresh_from_db()
        self.assertEqual(self.position.gebinde_id, abbild.pk)
        self.assertEqual(self.position.eingabemenge, Decimal("3"))
        # The row fits its article, and the next quantity goes through as well.
        self.position.full_clean()
        self.assertEqual(rsp.data["gebinde"]["id"], abbild.pk)
        self.assertEqual(self.zaehlen("4", vorher="3").status_code, HTTPStatus.OK)

    def test_nach_dem_zusammenfuehren_steht_die_menge_am_bleibenden_gebinde(self):
        ziel = Ware.objects.create(name="Artikel C", basiseinheit=self.stueck)
        kasten = Gebinde.objects.create(
            ware=ziel, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        Gebinde.objects.create(ware=ziel, name="Flasche", faktor_in_basiseinheit=Decimal("1"))

        with self.nach_dem_laden(lambda: zusammenfuehren(self.ware, ziel)):
            rsp = self.zaehlen()

        self.assertEqual(rsp.status_code, HTTPStatus.OK, rsp.data)
        self.position.refresh_from_db()
        self.assertEqual(self.position.gebinde_id, kasten.pk)
        self.assertEqual(self.position.eingabemenge, Decimal("3"))
        self.assertEqual(rsp.data["gebinde"]["id"], kasten.pk)

    def test_eine_inzwischen_entfernte_zeile_bleibt_entfernt(self):
        """The answer of a row removed before: 404, and the app names the reason."""
        with self.nach_dem_laden(lambda: self.entfernen(self.position)):
            rsp = self.zaehlen()

        self.assertEqual(rsp.status_code, HTTPStatus.NOT_FOUND, rsp.data)
        self.assertEqual(str(rsp.data["detail"]), "Die Zeile wurde inzwischen entfernt.")
        self.position.refresh_from_db()
        self.assertFalse(self.position.aktiv)
        self.assertIsNone(self.position.eingabemenge)

    def test_eine_wiederbelebte_zeile_haengt_am_abbild(self):
        """Created anew after a removal: the row as it stands after splitting comes back."""
        self.entfernen(self.position)
        echt = api_serializers.vorhandene_zeile
        bericht = {}

        def dazwischen(daten):
            vorhanden = echt(daten)
            if not bericht:
                bericht["neu"] = aufteilen(
                    self.ware, "Artikel C 0,33", "Artikel C 0,33 Ost", [self.cola]
                ).neu
            return vorhanden

        with mock.patch.object(counting_serializers, "vorhandene_zeile", side_effect=dazwischen):
            rsp = self.client.post(
                adresse("zaehlposition-list"),
                {
                    "inventur": self.inventur.pk,
                    "ort": self.theke.pk,
                    "artikel": self.cola.pk,
                    "gebinde": self.kasten.pk,
                    "eingabemenge": "4",
                },
            )

        self.assertEqual(rsp.status_code, HTTPStatus.CREATED, rsp.data)
        self.position.refresh_from_db()
        self.assertTrue(self.position.aktiv)
        self.assertEqual(
            self.position.gebinde, Gebinde.objects.get(ware=bericht["neu"], name="Kasten")
        )
        self.position.full_clean()

    # -- Weighings -----------------------------------------------------------

    def test_eine_neue_schale_nimmt_die_alten_einer_inzwischen_entfernten_zeile_mit(self):
        """Removed on another device a moment ago: the row comes back without its old bowl."""
        position, alte = self.waage_mit_schale()

        with self.nach_der_pruefung(lambda: self.entfernen(position)):
            rsp = self.client.post(
                adresse("wiegung-list"),
                {"zaehlposition": position.pk, "brutto_gramm": "1300", "behaelter": self.schale.pk},
            )

        self.assertEqual(rsp.status_code, HTTPStatus.CREATED, rsp.data)
        position.refresh_from_db()
        self.assertTrue(position.aktiv)
        self.assertEqual(
            list(position.wiegungen.values_list("brutto_gramm", flat=True)), [Decimal("1300")]
        )
        self.assertFalse(Wiegung.objects.filter(pk=alte.pk).exists())

    def test_eine_berichtigte_schale_holt_eine_inzwischen_entfernte_zeile_nicht_zurueck(self):
        """As for a quantity: 404 with the reason, the row stays removed, the bowl as it was.

        Until 10/2026 the answer was 200, and the corrected bowl stayed on the
        removed row, where nobody saw it again.
        """
        position, schale = self.waage_mit_schale()

        with self.nach_der_pruefung(lambda: self.entfernen(position)):
            rsp = self.client.patch(adresse("wiegung-detail", schale.pk), {"brutto_gramm": "950"})

        self.assertEqual(rsp.status_code, HTTPStatus.NOT_FOUND, rsp.data)
        self.assertEqual(str(rsp.data["detail"]), "Die Zeile wurde inzwischen entfernt.")
        position.refresh_from_db()
        schale.refresh_from_db()
        self.assertFalse(position.aktiv)
        self.assertEqual(schale.brutto_gramm, Decimal("900"))

    # -- New rows ------------------------------------------------------------

    def neue_zeile(self):
        """A crate the app found in the store, where nobody expected the article."""
        return self.client.post(
            adresse("zaehlposition-list"),
            {
                "inventur": self.inventur.pk,
                "ort": self.lager.pk,
                "artikel": self.cola.pk,
                "gebinde": self.kasten.pk,
                "eingabemenge": "4",
            },
            format="json",
        )

    def neue_zeile_nach_der_pruefung(self, dazwischen):
        """'validate' has looked for the row, then 'dazwischen' runs once, then 'create'."""
        echt = api_serializers.vorhandene_zeile
        gelaufen = []

        def nachsehen(daten):
            vorhanden = echt(daten)
            if not gelaufen:
                gelaufen.append(True)
                dazwischen()
            return vorhanden

        with mock.patch.object(counting_serializers, "vorhandene_zeile", side_effect=nachsehen):
            return self.neue_zeile()

    def ziel_mit_kasten(self) -> Ware:
        """A product that stays and has a crate of 20 too: the two crates merge."""
        ziel = Ware.objects.create(name="Artikel C", basiseinheit=self.stueck)
        Gebinde.objects.create(ware=ziel, name="Kasten", faktor_in_basiseinheit=Decimal("20"))
        return ziel

    def pruefe_abgelehnt(self, rsp, feld, meldung):
        self.assertEqual(rsp.status_code, HTTPStatus.BAD_REQUEST, rsp.data)
        self.assertEqual([str(text) for text in rsp.data[feld]], [str(meldung)])
        self.assertFalse(Zaehlposition.objects.filter(ort=self.lager).exists())

    def test_eine_neue_zeile_hoert_dass_ihr_gebinde_verschmolzen_ist(self):
        """Merged after the check: the pack is gone, and the answer says so in the app's words."""
        ziel = self.ziel_mit_kasten()

        rsp = self.neue_zeile_nach_der_pruefung(lambda: zusammenfuehren(self.ware, ziel))

        self.pruefe_abgelehnt(rsp, "gebinde", GEBINDE_WEG)

    def test_dieselbe_antwort_wenn_das_gebinde_schon_vorher_verschmolzen_war(self):
        """The job waited out a dead spot, the merge came before it: not DRF's "Invalid pk"."""
        zusammenfuehren(self.ware, self.ziel_mit_kasten())

        self.pruefe_abgelehnt(self.neue_zeile(), "gebinde", GEBINDE_WEG)

    def test_eine_neue_zeile_landet_am_umgezogenen_gebinde(self):
        """A pack the product that stays lacked moves over with its number, and the row fits it."""
        ziel = Ware.objects.create(name="Artikel C", basiseinheit=self.stueck)

        rsp = self.neue_zeile_nach_der_pruefung(lambda: zusammenfuehren(self.ware, ziel))

        self.assertEqual(rsp.status_code, HTTPStatus.CREATED, rsp.data)
        neu = Zaehlposition.objects.get(ort=self.lager)
        self.assertEqual((neu.gebinde_id, neu.gebinde.ware), (self.kasten.pk, ziel))
        self.assertEqual(neu.eingabemenge, Decimal("4"))
        neu.full_clean()

    def test_eine_neue_zeile_nach_dem_aufteilen(self):
        """Split after the check: the article went to the new product, the pack stayed behind."""
        rsp = self.neue_zeile_nach_der_pruefung(
            lambda: aufteilen(self.ware, "Artikel C 0,33", "Artikel C 0,33 Ost", [self.cola])
        )

        self.pruefe_abgelehnt(rsp, "gebinde", "Das Gebinde gehört nicht zu diesem Artikel.")

    def test_eine_neue_zeile_fuer_eine_eingestellte_dublette(self):
        """The house carried both products: merging discontinued this row, the answer says so."""
        ziel = Ware.objects.create(name="Artikel C", basiseinheit=self.stueck)
        Hausartikel.objects.create(
            haus=self.haus,
            ware=ziel,
            warengruppe=self.gruppe,
            artikelnummer="101009",
            name="Artikel C Kasten",
        )

        rsp = self.neue_zeile_nach_der_pruefung(lambda: zusammenfuehren(self.ware, ziel))

        self.assertEqual(Hausartikel.objects.get(pk=self.cola.pk).status, Artikelstatus.EINGESTELLT)
        self.assertEqual(rsp.status_code, HTTPStatus.BAD_REQUEST, rsp.data)
        self.assertIn("101009", str(rsp.data["artikel"][0]))
        self.assertFalse(Zaehlposition.objects.filter(ort=self.lager).exists())

    def test_eine_entfernte_zeile_einer_eingestellten_dublette_bleibt_entfernt(self):
        """Revived after the check: refused like a new row, and the row stays removed.

        Until 10/2026 reviving came before the check under the lock, and the
        row came back on the article the merge had just discontinued.
        """
        entfernt = Zaehlposition.objects.create(
            inventur=self.inventur,
            ort=self.lager,
            artikel=self.cola,
            gebinde=self.kasten,
            unerwartet=True,
            aktiv=False,
        )
        ziel = Ware.objects.create(name="Artikel C", basiseinheit=self.stueck)
        Hausartikel.objects.create(
            haus=self.haus,
            ware=ziel,
            warengruppe=self.gruppe,
            artikelnummer="101009",
            name="Artikel C Kasten",
        )

        rsp = self.neue_zeile_nach_der_pruefung(lambda: zusammenfuehren(self.ware, ziel))

        self.assertEqual(Hausartikel.objects.get(pk=self.cola.pk).status, Artikelstatus.EINGESTELLT)
        self.assertEqual(rsp.status_code, HTTPStatus.BAD_REQUEST, rsp.data)
        self.assertIn("101009", str(rsp.data["artikel"][0]))
        entfernt.refresh_from_db()
        self.assertEqual((entfernt.aktiv, entfernt.eingabemenge), (False, None))


def abfragen_waehrend(schreiben) -> list[tuple[type, bool, bool, tuple, str]]:
    """Model, lock, lock without key, order and SQL of every query compiled while 'schreiben' runs.

    Selects, updates and inserts, in their order. SQLite locks nothing, so
    the lock is read off the query: 'select_for_no_key_update' is the
    'FOR NO KEY UPDATE' PostgreSQL gets.
    """
    gesehen = []
    lesen, aendern, einfuegen = (
        SQLCompiler.as_sql,
        SQLUpdateCompiler.as_sql,
        SQLInsertCompiler.as_sql,
    )

    def aufzeichnen(echt):
        def compiliert(compiler, *args, **kwargs):
            ergebnis = echt(compiler, *args, **kwargs)
            abfrage = compiler.query
            sql = ergebnis[0][0] if isinstance(compiler, SQLInsertCompiler) else ergebnis[0]
            gesehen.append(
                (
                    abfrage.model,
                    bool(getattr(abfrage, "select_for_update", False)),
                    bool(getattr(abfrage, "select_for_no_key_update", False)),
                    tuple(getattr(abfrage, "order_by", ())),
                    sql,
                )
            )
            return ergebnis

        return compiliert

    with (
        mock.patch.object(SQLCompiler, "as_sql", aufzeichnen(lesen)),
        mock.patch.object(SQLUpdateCompiler, "as_sql", aufzeichnen(aendern)),
        mock.patch.object(SQLInsertCompiler, "as_sql", aufzeichnen(einfuegen)),
    ):
        schreiben()
    return gesehen


class ZusammenfuehrenUndAufteilenSperrenZuerst(ApiGrundlagen):
    """Before they change anything, merging and splitting lock the articles and rows they re-hang.

    SQLite locks nothing, so the queries themselves are looked at, as in
    'test_closing_race.DerAbschlussSperrtSeineZeilen': the first query on the
    count positions is the one that locks them, by number and without an
    outer join, the order the closing locks in. Before it the articles are
    locked, where a new row of the app waits ('katalog.models.artikel_sperren').
    The overlap itself is forced in 'GleichzeitigMitDerApp' and
    'WaehrendDesUmhaengens'.
    """

    def erste(self, abfragen, modell, *, gesperrt: bool = False) -> int:
        """The place of the first query on 'modell', or of the first one that locks it."""
        for stelle, (art, sperre, *_rest) in enumerate(abfragen):
            if art is modell and (sperre or not gesperrt):
                return stelle
        self.fail(f"keine {'sperrende ' if gesperrt else ''}Abfrage auf {modell.__name__}")

    def pruefe_zuerst_gesperrt(self, abfragen):
        zeilen = [eintrag[1:] for eintrag in abfragen if eintrag[0] is Zaehlposition]
        self.assertTrue(zeilen, "ohne Abfrage auf die Zählpositionen prüft der Test nichts")
        gesperrt, _ohne_schluessel, reihenfolge, sql = zeilen[0]
        self.assertTrue(gesperrt, f"zuerst geschrieben oder gelesen statt gesperrt: {sql}")
        self.assertEqual(reihenfolge, ("pk",))
        artikel = self.erste(abfragen, Artikel, gesperrt=True)
        self.assertLess(artikel, self.erste(abfragen, Zaehlposition), "die Artikel nach den Zeilen")
        _modell, _gesperrt, ohne_schluessel, reihenfolge, _sql = abfragen[artikel]
        self.assertTrue(ohne_schluessel, "die Artikel mit FOR UPDATE statt FOR NO KEY UPDATE")
        self.assertEqual(reihenfolge, ("pk",))
        for _modell, gesperrt, _ohne_schluessel, _reihenfolge, sql in abfragen:
            if gesperrt:
                self.assertNotIn("OUTER JOIN", sql.upper())

    def test_aufteilen(self):
        self.pruefe_zuerst_gesperrt(
            abfragen_waehrend(
                lambda: aufteilen(self.ware, "Artikel C 0,33", "Artikel C Ost", [self.cola])
            )
        )

    def test_zusammenfuehren(self):
        ziel = Ware.objects.create(name="Artikel C", basiseinheit=self.stueck)
        Gebinde.objects.create(ware=ziel, name="Kasten", faktor_in_basiseinheit=Decimal("20"))
        self.pruefe_zuerst_gesperrt(abfragen_waehrend(lambda: zusammenfuehren(self.ware, ziel)))

    def test_eine_neue_zeile_sperrt_inventur_ort_und_artikel_bevor_sie_liest(self):
        """Inventory, location and article, all without the key, then the pack, then the row.

        Without the key: a merge that touched rows twice checks their keys at
        its commit, the inventory's too ('checks.zaehlbar_unter_sperre'). The
        location for a merge of locations ('serializers.ort_sperren').
        """
        self.anmelden()
        antworten = []
        abfragen = abfragen_waehrend(
            lambda: antworten.append(
                self.client.post(
                    adresse("zaehlposition-list"),
                    {
                        "inventur": self.inventur.pk,
                        "ort": self.lager.pk,
                        "artikel": self.cola.pk,
                        "gebinde": self.kasten.pk,
                    },
                    format="json",
                )
            )
        )
        self.assertEqual(antworten[0].status_code, HTTPStatus.CREATED, antworten[0].data)
        inventur = self.erste(abfragen, Inventur, gesperrt=True)
        ort = self.erste(abfragen, Ort, gesperrt=True)
        artikel = self.erste(abfragen, Artikel, gesperrt=True)
        neu = next(
            stelle
            for stelle, (modell, _gesperrt, _ohne, _reihe, sql) in enumerate(abfragen)
            if modell is Zaehlposition and sql.upper().startswith("INSERT")
        )
        self.assertLess(inventur, ort)
        self.assertLess(ort, artikel)
        # The pack read once more after the lock ('validate' read it before any).
        self.assertTrue(
            any(abfragen[stelle][0] is Gebinde for stelle in range(artikel + 1, neu)),
            "das Gebinde nicht nach der Sperre gelesen",
        )
        for stelle in (inventur, ort, artikel):
            self.assertTrue(abfragen[stelle][2], f"FOR UPDATE statt FOR NO KEY UPDATE: {stelle}")
        for _modell, gesperrt, _ohne_schluessel, _reihenfolge, sql in abfragen:
            if gesperrt:
                self.assertNotIn("OUTER JOIN", sql.upper())

    def test_die_zeilen_kommen_mit_dem_naechsten_abgleich(self):
        """Every row of the article, also the scale, whose product now has another name."""
        waage = Zaehlposition.objects.create(
            inventur=self.inventur, ort=self.lager, artikel=self.nachos
        )
        vorher = timezone.now()
        ziel = Ware.objects.create(name="Ware N", basiseinheit=self.gramm, abwiegbar=True)
        zusammenfuehren(self.nachos_ware, ziel)

        self.anmelden()
        rsp = self.client.get(adresse("zaehlposition-list"), {"geaendert_seit": vorher.isoformat()})
        self.assertEqual([zeile["id"] for zeile in rsp.data], [waage.pk])
        self.assertEqual(rsp.data[0]["artikel"], "Ware N")


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class GleichzeitigMitDerApp(APITransactionTestCase):
    """A quantity from the app and a merge or a split at the same moment, in both orders.

    The side that holds the row first finishes, the other waits for it and
    then reads what the first one wrote. Each side waits until the other one
    waits for a lock ('test_closing_race._bis_jemand_wartet'). Runs in the
    CI, which tests against PostgreSQL.
    """

    serialized_rollback = True

    def setUp(self):
        haus = Haus.objects.create(name="Kino Ost")
        ort = Ort.objects.create(
            name="Thekenfront", bereich=Bereich.objects.create(name="Theke", haus=haus)
        )
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
            erwartung=Erwartung.objects.create(ort=ort, artikel=self.artikel), gebinde=self.kasten
        )
        self.inventur = inventur_anlegen(haus, stichtag=STICHTAG)
        self.inventur.zaehlpositionen_anlegen()
        self.konto = CustomUser.objects.create_user(
            username="erste", password="geheim", email="erste@example.org"
        )
        self.konto.haeuser.add(haus)
        self.inventur.zaehlende.add(self.konto)
        self.inventur.zaehlung_starten()
        self.position = self.inventur.zaehlpositionen.get()
        self.position.eingabemenge = Decimal("2")
        self.position.save()

    # -- What the two sides do ------------------------------------------

    def menge(self, client):
        """From 2 to 7 crates."""
        return client.patch(
            adresse("zaehlposition-detail", self.position.pk),
            {"eingabemenge": "7", "eingabemenge_vorher": "2"},
            format="json",
        )

    def aufteilen(self):
        aufteilen(Ware.objects.get(pk=self.ware.pk), "Artikel C", "Artikel C Ost", [self.artikel])

    def zusammenfuehren(self):
        ziel = Ware.objects.create(
            name="Artikel C neu", basiseinheit=Basiseinheit.objects.get(name="Stück(e)")
        )
        Gebinde.objects.create(ware=ziel, name="Kasten", faktor_in_basiseinheit=Decimal("20"))
        zusammenfuehren(Ware.objects.get(pk=self.ware.pk), ziel)

    def gleichzeitig(self, halt, umhaengen, *, app_zuerst: bool):
        """Runs the quantity and the merge or split in two threads, one held at 'halt'.

        'halt' is (object, name) of a function the held side calls while it
        holds the row. It goes on once the other side waits for a lock, or
        is done because nothing made it wait.
        """
        ziel, name = halt
        gehalten = threading.Event()
        geschrieben = threading.Event()
        umgehaengt = threading.Event()
        ergebnis: dict = {}
        echt = getattr(ziel, name)
        anderer = umgehaengt if app_zuerst else geschrieben

        def halten(*args, **kwargs):
            if not gehalten.is_set():
                gehalten.set()
                _bis_jemand_wartet(anderer)
            return echt(*args, **kwargs)

        def senden():
            client = APIClient()
            client.force_authenticate(self.konto)
            if not app_zuerst:
                gehalten.wait(timeout=10)
            try:
                ergebnis["antwort"] = self.menge(client)
            finally:
                geschrieben.set()
                connection.close()

        def katalog():
            if app_zuerst:
                gehalten.wait(timeout=10)
            try:
                umhaengen()
            finally:
                umgehaengt.set()
                connection.close()

        with mock.patch.object(ziel, name, side_effect=halten):
            faeden = [threading.Thread(target=senden), threading.Thread(target=katalog)]
            for faden in faeden:
                faden.start()
            for faden in faeden:
                faden.join()
        self.position.refresh_from_db()
        return ergebnis["antwort"]

    def pruefe_am_richtigen_gebinde(self, antwort):
        self.assertEqual(antwort.status_code, HTTPStatus.OK, antwort.data)
        artikel = Hausartikel.objects.get(pk=self.artikel.pk)
        self.assertEqual(self.position.gebinde.ware_id, artikel.ware_id)
        self.assertEqual(self.position.eingabemenge, Decimal("7"))
        self.position.full_clean()

    # -- The merge or split holds the row ------------------------------------

    def test_die_menge_wartet_aufs_aufteilen_und_landet_am_abbild(self):
        antwort = self.gleichzeitig(
            (Hausartikel, "plaetze_umschreiben"), self.aufteilen, app_zuerst=False
        )
        self.pruefe_am_richtigen_gebinde(antwort)
        self.assertEqual(antwort.data["gebinde"]["id"], self.position.gebinde_id)

    def test_die_menge_wartet_aufs_zusammenfuehren_und_landet_am_bleibenden_gebinde(self):
        antwort = self.gleichzeitig(
            (Hausartikel, "plaetze_umschreiben"), self.zusammenfuehren, app_zuerst=False
        )
        self.pruefe_am_richtigen_gebinde(antwort)
        self.assertEqual(antwort.data["gebinde"]["id"], self.position.gebinde_id)

    # -- The app holds the row ---------------------------------------------

    def test_das_aufteilen_wartet_auf_die_menge_und_ist_juenger(self):
        """The split's timestamp comes after the app's, so the next sync brings the copy.

        Until 10/2026 the split took its time before it waited for the row,
        and the row ended up older than the quantity the app had just
        written. Only the margin of a few seconds the app sets its sync mark
        back by covered that ('MARGIN' in 'app/src/services/sync.js').
        """
        antwort = self.gleichzeitig(
            (counting_serializers, "gleiche_menge"), self.aufteilen, app_zuerst=True
        )
        self.pruefe_am_richtigen_gebinde(antwort)
        self.assertGreater(
            self.position.zuletzt_geaendert,
            datetime.fromisoformat(antwort.data["zuletzt_geaendert"]),
        )

    def test_das_zusammenfuehren_wartet_auf_die_menge_und_ist_juenger(self):
        antwort = self.gleichzeitig(
            (counting_serializers, "gleiche_menge"), self.zusammenfuehren, app_zuerst=True
        )
        self.pruefe_am_richtigen_gebinde(antwort)
        self.assertGreater(
            self.position.zuletzt_geaendert,
            datetime.fromisoformat(antwort.data["zuletzt_geaendert"]),
        )


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class WaehrendDesUmhaengens(APITransactionTestCase):
    """A new row from the app, or the closing, while a merge or a split changes the product.

    The new row locks its article, merging and splitting lock every article
    they move before anything else ('katalog.models.artikel_sperren'). The
    side that holds it first finishes. A merge that waits for a new row
    re-hangs it onto the crate that stays, a split onto the copy. A new row
    that waits for a merge finds its crate gone and hears 400 in the app's
    words, one that waits for a split hears that the crate is the other
    product's. Until 10/2026 the new row was written onto the crate the merge
    was deleting: it held the inventory, the merge needed it for the check of
    its keys at the commit, and PostgreSQL broke one of the two off. The
    closing ran into the same while it waited for the rows a merge held.
    Each side waits until the other one waits for a lock, as in
    'GleichzeitigMitDerApp'. Runs in the CI, which tests against PostgreSQL.
    """

    serialized_rollback = True

    def setUp(self):
        haus = Haus.objects.create(name="Kino Ost")
        bereich = Bereich.objects.create(name="Theke", haus=haus)
        ort = Ort.objects.create(name="Thekenfront", bereich=bereich)
        self.lager = Ort.objects.create(name="Trockenlager", bereich=bereich)
        self.stueck = Basiseinheit.objects.get(name="Stück(e)")
        self.ware = Ware.objects.create(name="Artikel C", basiseinheit=self.stueck)
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
            erwartung=Erwartung.objects.create(ort=ort, artikel=self.artikel), gebinde=self.kasten
        )
        self.inventur = inventur_anlegen(haus, stichtag=STICHTAG)
        self.inventur.zaehlpositionen_anlegen()
        self.konto = CustomUser.objects.create_user(
            username="erste", password="geheim", email="erste@example.org"
        )
        self.konto.haeuser.add(haus)
        self.inventur.zaehlende.add(self.konto)
        self.inventur.zaehlung_starten()
        # Two crates at the counter: the row the merge or the split holds.
        self.inventur.zaehlpositionen.update(eingabemenge=Decimal("2"))

    # -- What the sides do ---------------------------------------------------

    def anlegen(self):
        """Four crates in the store, where nobody expected the article."""
        client = APIClient(raise_request_exception=False)
        client.force_authenticate(self.konto)
        return client.post(
            adresse("zaehlposition-list"),
            {
                "inventur": self.inventur.pk,
                "ort": self.lager.pk,
                "artikel": self.artikel.pk,
                "gebinde": self.kasten.pk,
                "eingabemenge": "4",
            },
            format="json",
        )

    def zusammenfuehren(self):
        """Into a product with a crate of 20 of its own: the two crates merge, this one goes."""
        ziel = Ware.objects.create(name="Artikel C neu", basiseinheit=self.stueck)
        Gebinde.objects.create(ware=ziel, name="Kasten", faktor_in_basiseinheit=Decimal("20"))
        zusammenfuehren(Ware.objects.get(pk=self.ware.pk), ziel)

    def aufteilen(self):
        aufteilen(Ware.objects.get(pk=self.ware.pk), "Artikel C", "Artikel C Ost", [self.artikel])

    def schliessen(self):
        Inventur.objects.get(pk=self.inventur.pk).abschliessen()

    def gleichzeitig(self, umhaengen, andere, *, umhaengen_zuerst: bool) -> dict:
        """Runs the merge or split and the other side in two threads, the first one held.

        The merge or the split is held once it has re-hung the rows
        ('plaetze_umschreiben'), the new row right before it is written
        ('Zaehlposition.save'), each while it holds its locks. It goes on
        once the other side waits for a lock, or is done because nothing
        made it wait. What either side raises is kept, not lost in its thread.
        """
        ziel, name = (
            (Hausartikel, "plaetze_umschreiben") if umhaengen_zuerst else (Zaehlposition, "save")
        )
        gehalten = threading.Event()
        fertig = {"umhaengen": threading.Event(), "andere": threading.Event()}
        ergebnis: dict = {}
        echt = getattr(ziel, name)
        warten_auf = fertig["andere"] if umhaengen_zuerst else fertig["umhaengen"]

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
            except Exception as fehler:  # The test names it, the thread would hide it.
                ergebnis[f"{seite}_fehler"] = fehler
            finally:
                fertig[seite].set()
                connection.close()

        with mock.patch.object(ziel, name, autospec=True, side_effect=halten):
            faeden = [
                threading.Thread(target=laufen, args=("umhaengen", umhaengen, umhaengen_zuerst)),
                threading.Thread(target=laufen, args=("andere", andere, not umhaengen_zuerst)),
            ]
            for faden in faeden:
                faden.start()
            for faden in faeden:
                faden.join()
        self.assertNotIn("umhaengen_fehler", ergebnis)
        self.assertNotIn("andere_fehler", ergebnis)
        return ergebnis

    def neue_zeile(self) -> Zaehlposition | None:
        return Zaehlposition.objects.filter(ort=self.lager).select_related("gebinde").first()

    # -- The merge or the split holds the article ----------------------------

    def test_die_neue_zeile_wartet_aufs_zusammenfuehren_und_hoert_dass_ihr_kasten_fehlt(self):
        antwort = self.gleichzeitig(self.zusammenfuehren, self.anlegen, umhaengen_zuerst=True)[
            "andere"
        ]

        self.assertEqual(antwort.status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual([str(text) for text in antwort.data["gebinde"]], [str(GEBINDE_WEG)])
        self.assertIsNone(self.neue_zeile())
        self.assertFalse(Gebinde.objects.filter(pk=self.kasten.pk).exists())

    def test_die_neue_zeile_wartet_aufs_aufteilen_und_passt_nicht_mehr(self):
        antwort = self.gleichzeitig(self.aufteilen, self.anlegen, umhaengen_zuerst=True)["andere"]

        self.assertEqual(antwort.status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(
            [str(text) for text in antwort.data["gebinde"]],
            ["Das Gebinde gehört nicht zu diesem Artikel."],
        )
        self.assertIsNone(self.neue_zeile())

    def test_eine_entfernte_zeile_wartet_aufs_zusammenfuehren_und_bleibt_entfernt(self):
        """The house carries both products: the merge discontinues the article, nothing revives.

        Until 10/2026 reviving came before the check under the lock, and the
        row came back on the discontinued article.
        """
        entfernt = Zaehlposition.objects.create(
            inventur=self.inventur,
            ort=self.lager,
            artikel=self.artikel,
            gebinde=self.kasten,
            unerwartet=True,
            aktiv=False,
        )
        ziel = Ware.objects.create(name="Artikel C neu", basiseinheit=self.stueck)
        Hausartikel.objects.create(
            haus=self.artikel.haus,
            ware=ziel,
            warengruppe=self.artikel.warengruppe,
            artikelnummer="101009",
            name="Artikel C neu",
        )

        antwort = self.gleichzeitig(
            lambda: zusammenfuehren(Ware.objects.get(pk=self.ware.pk), ziel),
            self.anlegen,
            umhaengen_zuerst=True,
        )["andere"]

        self.assertEqual(antwort.status_code, HTTPStatus.BAD_REQUEST, antwort.data)
        self.assertIn("101009", str(antwort.data["artikel"][0]))
        entfernt.refresh_from_db()
        self.assertEqual((entfernt.aktiv, entfernt.eingabemenge), (False, None))

    def test_der_abschluss_wartet_aufs_zusammenfuehren(self):
        """The closing waits for the rows the merge holds and freezes them as they end up."""
        self.gleichzeitig(self.zusammenfuehren, self.schliessen, umhaengen_zuerst=True)

        inventur = Inventur.objects.get(pk=self.inventur.pk)
        self.assertEqual(inventur.status, Inventurstatus.ABGESCHLOSSEN)
        self.assertEqual(
            list(inventur.ergebnisse.values_list("hausartikel", "gesamtmenge_basiseinheit")),
            [(self.artikel.pk, Decimal("40.000"))],
        )

    # -- The new row holds the article -----------------------------------------

    def pruefe_umgehaengt(self, antwort, ware: str):
        """Created, then re-hung by the side that waited, and fitting its article."""
        self.assertEqual(antwort.status_code, HTTPStatus.CREATED, antwort.data)
        neu = self.neue_zeile()
        assert neu is not None
        self.assertEqual(neu.pk, antwort.data["id"])
        self.assertEqual(neu.gebinde.ware.name, ware)
        self.assertEqual(neu.gebinde.ware_id, Hausartikel.objects.get(pk=self.artikel.pk).ware_id)
        self.assertEqual(neu.eingabemenge, Decimal("4"))
        neu.full_clean()
        # Touched after the answer: the app's next sync brings the new crate.
        self.assertGreater(
            neu.zuletzt_geaendert, datetime.fromisoformat(antwort.data["zuletzt_geaendert"])
        )

    def test_das_zusammenfuehren_wartet_auf_die_neue_zeile_und_nimmt_sie_mit(self):
        antwort = self.gleichzeitig(self.zusammenfuehren, self.anlegen, umhaengen_zuerst=False)[
            "andere"
        ]

        self.pruefe_umgehaengt(antwort, "Artikel C neu")

    def test_das_aufteilen_wartet_auf_die_neue_zeile_und_nimmt_sie_mit(self):
        antwort = self.gleichzeitig(self.aufteilen, self.anlegen, umhaengen_zuerst=False)["andere"]

        self.pruefe_umgehaengt(antwort, "Artikel C Ost")
