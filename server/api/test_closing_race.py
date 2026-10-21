"""The closing and a write from the app at the same moment, in a forced order.

The closing locks the inventory and then every one of its rows, and holds
both until the result is frozen ('Inventur.zum_abschluss_sperren'). Every
write of the app that creates, revives or removes a row reads the inventory
under the same lock and checks it again there ('zaehlbar_unter_sperre'). A
quantity or a weighing locks only its row. Here the closing is slipped in
after the write's own checks and right before its transaction, so the test
needs no timing. The overlap itself is forced on PostgreSQL, for a new row
in 'tests.AbschlussUndAnlegenGleichzeitig' and for quantity and weighing in
'MengeUndWiegungGegenDenAbschluss' below. Until 10/2026 the new row got a
201 and was missing from the result, removing a row met a server error, and
a quantity or a weighing between reading and deleting got its 200 or 201
and was missing too.
"""

import threading
import time
from decimal import Decimal
from http import HTTPStatus
from unittest import mock, skipUnless

from django.db import connection
from django.db.models.sql.compiler import SQLCompiler
from rest_framework.test import APIClient, APITransactionTestCase

from accounts.models import CustomUser
from api import serializers as api_serializers
from api import views as api_views
from api.serializers import counting as counting_serializers
from api.tests import STICHTAG, ApiGrundlagen, adresse
from django_project.testing import inventur_anlegen
from inventur.closing import Entscheidungen, NeueWerte, abschliessen
from inventur.closing import decisions as abschluss_dienst
from inventur.models import Inventur, Inventurstatus, Wiegung, Zaehlposition
from inventur.services import zaehlstand
from katalog.models import (
    Basiseinheit,
    Behaelter,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Ware,
    Warengruppe,
)
from organisation.models import Bereich, Haus, Ort


class AbschlussWaehrendDesSchreibens(ApiGrundlagen):
    def setUp(self):
        super().setUp()
        self.anmelden()

    def abschliessen(self):
        if self.inventur.status == Inventurstatus.ZAEHLUNG:
            self.inventur.abschliessen()

    def abschluss_nach_der_pruefung(self):
        """'validate' has looked up the row, then the closing commits, then 'create' runs."""
        echt = api_serializers.vorhandene_zeile

        def dazwischen(daten):
            vorhanden = echt(daten)
            self.abschliessen()
            return vorhanden

        return mock.patch.object(counting_serializers, "vorhandene_zeile", side_effect=dazwischen)

    def abschluss_nach_dem_laden(self, viewset):
        """The view has loaded the row, then the closing commits, then it writes."""
        echt = viewset.get_object

        def dazwischen(view):
            objekt = echt(view)
            self.abschliessen()
            return objekt

        return mock.patch.object(viewset, "get_object", autospec=True, side_effect=dazwischen)

    def neue_zeile(self, menge="4"):
        return {
            "inventur": self.inventur.pk,
            "ort": self.lager.pk,
            "artikel": self.cola.pk,
            "gebinde": self.kasten.pk,
            "eingabemenge": menge,
        }

    def pruefe_abgewiesen(self, antwort):
        self.assertEqual(antwort.status_code, HTTPStatus.CONFLICT, antwort.data)
        self.assertEqual(antwort.data["konflikt"], "status")
        self.assertFalse(antwort.data["vorlaeufig"])
        self.inventur.refresh_from_db()
        self.assertEqual(self.inventur.status, Inventurstatus.ABGESCHLOSSEN)

    def test_eine_neue_zeile_bleibt_draussen(self):
        with self.abschluss_nach_der_pruefung():
            antwort = self.client.post(adresse("zaehlposition-list"), self.neue_zeile())

        self.pruefe_abgewiesen(antwort)
        self.assertFalse(Zaehlposition.objects.filter(inventur=self.inventur).exists())
        self.assertFalse(self.inventur.ergebnisse.filter(hausartikel=self.cola).exists())

    def test_eine_geloeschte_zeile_kommt_nicht_zurueck(self):
        weg = Zaehlposition.objects.create(
            inventur=self.inventur,
            ort=self.lager,
            artikel=self.cola,
            gebinde=self.kasten,
            aktiv=False,
        )

        with self.abschluss_nach_der_pruefung():
            antwort = self.client.post(adresse("zaehlposition-list"), self.neue_zeile("2"))

        self.pruefe_abgewiesen(antwort)
        self.assertFalse(Zaehlposition.objects.filter(pk=weg.pk).exists())

    def test_eine_zeile_entfernen_ist_kein_serverfehler(self):
        position = self.inventur.zaehlpositionen.get()

        with self.abschluss_nach_dem_laden(api_views.ZaehlpositionViewSet):
            antwort = self.client.delete(adresse("zaehlposition-detail", position.pk))

        self.pruefe_abgewiesen(antwort)
        self.assertFalse(Zaehlposition.objects.filter(pk=position.pk).exists())

    def test_eine_wiegung_entfernen_legt_die_zeile_nicht_neu_an(self):
        position = Zaehlposition.objects.create(
            inventur=self.inventur, ort=self.lager, artikel=self.nachos
        )
        wiegung = Wiegung.objects.create(
            zaehlposition=position, brutto_gramm="900", behaelter=self.schale
        )

        with self.abschluss_nach_dem_laden(api_views.WiegungViewSet):
            antwort = self.client.delete(adresse("wiegung-detail", wiegung.pk))

        self.pruefe_abgewiesen(antwort)
        self.assertFalse(Zaehlposition.objects.filter(pk=position.pk).exists())


class DerAbschlussSperrtSeineZeilen(ApiGrundlagen):
    """Before it compares or reads anything, the closing locks every row of the inventory.

    SQLite locks nothing, so the queries themselves are looked at, as in
    'tests.SperreOhneAeusserenJoin': the first query on the count positions
    is the one that locks them, and no locking query joins the nullable pack
    ('Zaehlposition.Meta.ordering'), which PostgreSQL refuses. The inventory
    is locked without its key: a merge that holds rows checks their keys
    against it at its commit, and under 'FOR UPDATE' the two waited for each
    other. The overlap itself is forced in 'MengeUndWiegungGegenDenAbschluss'
    and in 'test_merge_split_race.WaehrendDesUmhaengens'.
    """

    def abfragen(self, schliessen) -> list[tuple[type, bool, bool, str]]:
        """Model, lock, lock without key and SQL of every query compiled while 'schliessen' runs."""
        gesehen = []
        echt = SQLCompiler.as_sql

        def aufzeichnen(compiler, *args, **kwargs):
            sql, params = echt(compiler, *args, **kwargs)
            abfrage = compiler.query
            gesehen.append(
                (
                    abfrage.model,
                    bool(abfrage.select_for_update),
                    bool(abfrage.select_for_no_key_update),
                    sql,
                )
            )
            return sql, params

        with mock.patch.object(SQLCompiler, "as_sql", aufzeichnen):
            schliessen()
        return gesehen

    def pruefe_zuerst_gesperrt(self, abfragen):
        zeilen = [
            (gesperrt, sql) for modell, gesperrt, _ohne, sql in abfragen if modell is Zaehlposition
        ]
        self.assertTrue(zeilen, "ohne Abfrage auf die Zählpositionen prüft der Test nichts")
        self.assertTrue(zeilen[0][0], f"zuerst gelesen statt gesperrt: {zeilen[0][1]}")
        inventur = [
            ohne for modell, gesperrt, ohne, _sql in abfragen if modell is Inventur and gesperrt
        ]
        self.assertTrue(inventur, "ohne Sperre der Inventur prüft der Test nichts")
        self.assertTrue(all(inventur), "die Inventur mit FOR UPDATE statt FOR NO KEY UPDATE")
        for _modell, gesperrt, _ohne, sql in abfragen:
            if gesperrt:
                self.assertNotIn("OUTER JOIN", sql.upper())
        self.inventur.refresh_from_db()
        self.assertEqual(self.inventur.status, Inventurstatus.ABGESCHLOSSEN)

    def test_ueber_den_dienst(self):
        """The way of the check list, the clarification and "Close all"."""
        stand = zaehlstand(self.inventur)
        self.pruefe_zuerst_gesperrt(
            self.abfragen(lambda: abschliessen(self.inventur, Entscheidungen(), gesehen=stand))
        )

    def test_direkt_am_modell(self):
        self.pruefe_zuerst_gesperrt(self.abfragen(self.inventur.abschliessen))


def _jemand_wartet_auf_eine_sperre() -> bool:
    """Whether another connection to this database waits for a lock right now.

    Only this database: the CI runs the tests in parallel, each process with
    a database of its own. PostgreSQL reads 'pg_stat_activity' once per
    transaction, and the side that asks holds one open, hence the cleared
    snapshot before every look.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_stat_clear_snapshot()")
        cursor.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
            "AND wait_event_type = 'Lock' AND pid <> pg_backend_pid()"
        )
        zeile = cursor.fetchone()
    return bool(zeile and zeile[0])


def _bis_jemand_wartet(fertig: threading.Event, frist: float = 10.0) -> None:
    """Holds until the other side waits for a lock or is already done, at most 'frist' seconds.

    On the lock and not on a fixed time: on a slow runner a fixed pause
    lets one side go on before the other has reached the lock.
    """
    ende = time.monotonic() + frist
    while time.monotonic() < ende and not fertig.is_set():
        if _jemand_wartet_auf_eine_sperre():
            return
        time.sleep(0.02)


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class MengeUndWiegungGegenDenAbschluss(APITransactionTestCase):
    """A quantity or a weighing from the app and the closing at the same moment, in both orders.

    Both writes lock only their row. The closing locks every row of the
    inventory before it compares and reads ('Inventur.zum_abschluss_sperren').
    A write that holds its row first is waited for and lands in the result.
    One that comes later waits until the result is frozen, finds its row gone
    and hears 409, so the app keeps the number under "not accepted". Until
    10/2026 the closing locked only the inventory: a write between reading and
    deleting got its 200 or 201 and was missing from the result, and one after
    the comparison was frozen without anyone having seen it. Each side waits
    until the other one waits for a lock. Runs in the CI, which tests against
    PostgreSQL. The quantity closes through the service of the pages, the
    weighing directly through the model, the way the demo does.
    """

    serialized_rollback = True

    def setUp(self):
        haus = Haus.objects.create(name="Kino Ost")
        ort = Ort.objects.create(
            name="Thekenfront", bereich=Bereich.objects.create(name="Theke", haus=haus)
        )
        gruppe = Warengruppe.objects.create(name="Warengruppe G")
        ware = Ware.objects.create(
            name="Artikel C", basiseinheit=Basiseinheit.objects.get(name="Stück(e)")
        )
        kasten = Gebinde.objects.create(
            ware=ware, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        artikel = Hausartikel.objects.create(
            haus=haus, ware=ware, warengruppe=gruppe, artikelnummer="101001", name="Artikel C"
        )
        ErwartungGebinde.objects.create(
            erwartung=Erwartung.objects.create(ort=ort, artikel=artikel), gebinde=kasten
        )
        lose = Ware.objects.create(
            name="Ware N", basiseinheit=Basiseinheit.objects.get(name="g"), abwiegbar=True
        )
        self.schale = Behaelter.objects.create(name="Schale", tara=Decimal("250"))
        lose.behaelter.add(self.schale)
        Erwartung.objects.create(
            ort=ort,
            artikel=Hausartikel.objects.create(
                haus=haus, ware=lose, warengruppe=gruppe, artikelnummer="101002", name="Ware N"
            ),
            wiegen_erwartet=True,
        )
        self.inventur = inventur_anlegen(haus, stichtag=STICHTAG)
        self.inventur.zaehlpositionen_anlegen()
        self.konto = CustomUser.objects.create_user(
            username="erste", password="geheim", email="erste@example.org"
        )
        self.konto.haeuser.add(haus)
        self.inventur.zaehlende.add(self.konto)
        self.inventur.zaehlung_starten()
        self.kastenzeile = self.inventur.zaehlpositionen.get(gebinde=kasten)
        self.kastenzeile.eingabemenge = Decimal("2")
        self.kastenzeile.save()
        self.waage = self.inventur.zaehlpositionen.get(gebinde__isnull=True)

    # -- What the two sides do ------------------------------------------

    def menge(self, client):
        """From 2 to 7 crates, i.e. 140 pieces instead of 40."""
        return client.patch(
            adresse("zaehlposition-detail", self.kastenzeile.pk),
            {"eingabemenge": "7", "eingabemenge_vorher": "2"},
            format="json",
        )

    def wiegung(self, client):
        """1250 g in the 250 g bowl, i.e. 1000 g net."""
        return client.post(
            adresse("wiegung-list"),
            {
                "zaehlposition": self.waage.pk,
                "brutto_gramm": "1250",
                "behaelter": self.schale.pk,
                "kennung": "7b0c2f8e-6a8e-4f62-9a3c-0b8f2f1d9a11",
            },
            format="json",
        )

    def ueber_den_dienst(self, gesehen: str | None = None):
        return lambda: abschliessen(
            Inventur.objects.get(pk=self.inventur.pk), Entscheidungen(), gesehen=gesehen
        )

    def am_modell(self):
        return lambda: Inventur.objects.get(pk=self.inventur.pk).abschliessen()

    # -- The two orders ---------------------------------------------------

    def gleichzeitig(self, halt, schreiben, schliessen, *, schreiben_zuerst: bool) -> dict:
        """Runs the write and the closing in two threads, one held at 'halt'.

        'halt' is (object, name) of a function the held side calls while it
        holds its lock. It goes on once the other side waits for a lock, or
        is done because nothing made it wait.
        """
        ziel, name = halt
        gehalten = threading.Event()
        geschrieben = threading.Event()
        geschlossen = threading.Event()
        ergebnis: dict = {}
        echt = getattr(ziel, name)
        anderer = geschlossen if schreiben_zuerst else geschrieben

        def halten(*args, **kwargs):
            if not gehalten.is_set():
                gehalten.set()
                _bis_jemand_wartet(anderer)
            return echt(*args, **kwargs)

        def senden():
            client = APIClient()
            client.force_authenticate(self.konto)
            if not schreiben_zuerst:
                gehalten.wait(timeout=10)
            try:
                ergebnis["antwort"] = schreiben(client)
            finally:
                geschrieben.set()
                connection.close()

        def abschluss():
            if schreiben_zuerst:
                gehalten.wait(timeout=10)
            try:
                schliessen()
            except NeueWerte as fehler:
                ergebnis["neue_werte"] = fehler
            finally:
                geschlossen.set()
                connection.close()

        with mock.patch.object(ziel, name, autospec=True, side_effect=halten):
            faeden = [threading.Thread(target=senden), threading.Thread(target=abschluss)]
            for faden in faeden:
                faden.start()
            for faden in faeden:
                faden.join()
        self.inventur.refresh_from_db()
        return ergebnis

    def eingefroren(self) -> dict[str, Decimal]:
        return dict(
            self.inventur.ergebnisse.values_list(
                "hausartikel__artikelnummer", "gesamtmenge_basiseinheit"
            )
        )

    def pruefe_abgewiesen(self, antwort):
        """The 409 of a closed inventory: final, the app lists the number as not accepted."""
        self.assertEqual(antwort.status_code, HTTPStatus.CONFLICT, antwort.data)
        self.assertEqual(
            (antwort.data["konflikt"], antwort.data["status"], antwort.data["vorlaeufig"]),
            ("status", Inventurstatus.ABGESCHLOSSEN, False),
        )
        self.assertEqual(self.inventur.status, Inventurstatus.ABGESCHLOSSEN)
        self.assertFalse(Zaehlposition.objects.filter(inventur=self.inventur).exists())

    # -- While the result is frozen -----------------------------------------

    def test_eine_menge_beim_einfrieren_wartet_und_hoert_409(self):
        ergebnis = self.gleichzeitig(
            (Inventur, "_ergebnisse"), self.menge, self.ueber_den_dienst(), schreiben_zuerst=False
        )
        self.pruefe_abgewiesen(ergebnis["antwort"])
        self.assertEqual(self.eingefroren(), {"101001": Decimal("40.000")})

    def test_eine_wiegung_beim_einfrieren_wartet_und_hoert_409(self):
        ergebnis = self.gleichzeitig(
            (Inventur, "_ergebnisse"), self.wiegung, self.am_modell(), schreiben_zuerst=False
        )
        self.pruefe_abgewiesen(ergebnis["antwort"])
        self.assertEqual(self.eingefroren(), {"101001": Decimal("40.000")})
        self.assertFalse(Wiegung.objects.exists())

    # -- A write that holds its row first -----------------------------------

    def test_eine_menge_vor_dem_abschluss_wird_eingefroren(self):
        ergebnis = self.gleichzeitig(
            (counting_serializers, "gleiche_menge"),
            self.menge,
            self.ueber_den_dienst(),
            schreiben_zuerst=True,
        )
        self.assertEqual(ergebnis["antwort"].status_code, HTTPStatus.OK, ergebnis["antwort"].data)
        self.assertEqual(self.inventur.status, Inventurstatus.ABGESCHLOSSEN)
        self.assertEqual(self.eingefroren(), {"101001": Decimal("140.000")})

    def test_eine_wiegung_vor_dem_abschluss_wird_eingefroren(self):
        ergebnis = self.gleichzeitig(
            (api_serializers.WiegungSerializer, "create"),
            self.wiegung,
            self.am_modell(),
            schreiben_zuerst=True,
        )
        antwort = ergebnis["antwort"]
        self.assertEqual(antwort.status_code, HTTPStatus.CREATED, antwort.data)
        self.assertEqual(self.inventur.status, Inventurstatus.ABGESCHLOSSEN)
        self.assertEqual(
            self.eingefroren(), {"101001": Decimal("40.000"), "101002": Decimal("1000.000")}
        )

    # -- Around the comparison of the count state -------------------------

    def test_eine_menge_nach_dem_vergleich_wartet_und_hoert_409(self):
        """Compared and not yet read: what the check list showed is what freezes."""
        gesehen = zaehlstand(self.inventur)
        ergebnis = self.gleichzeitig(
            (abschluss_dienst, "klaeren"),
            self.menge,
            self.ueber_den_dienst(gesehen),
            schreiben_zuerst=False,
        )
        self.pruefe_abgewiesen(ergebnis["antwort"])
        self.assertEqual(self.eingefroren(), {"101001": Decimal("40.000")})

    def test_eine_menge_vor_dem_vergleich_haelt_den_abschluss_auf(self):
        """It lands before the comparison, so the comparison sees it: new values, not closed."""
        gesehen = zaehlstand(self.inventur)
        ergebnis = self.gleichzeitig(
            (counting_serializers, "gleiche_menge"),
            self.menge,
            self.ueber_den_dienst(gesehen),
            schreiben_zuerst=True,
        )
        self.assertEqual(ergebnis["antwort"].status_code, HTTPStatus.OK, ergebnis["antwort"].data)
        self.assertIn("neue_werte", ergebnis)
        self.assertEqual(self.inventur.status, Inventurstatus.ZAEHLUNG)
        self.assertFalse(self.inventur.ergebnisse.exists())
        self.kastenzeile.refresh_from_db()
        self.assertEqual(self.kastenzeile.eingabemenge, Decimal("7"))
