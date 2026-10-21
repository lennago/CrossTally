"""Ignoring a product, or a house article changing its product, while the app counts.

Both mark the uncounted rows of running inventories as deleted
('hausartikel_status'). They lock the article and its rows first, by
number, and decide only then. A quantity or a weighing from the app holds
its row while it writes ('api.serializers.counting.gesperrte_zeile'). Until
10/2026 the rows were marked in one update without a lock: on PostgreSQL a
row the app had just counted ended marked as deleted with its count on it,
and a count that landed between the check and the update stayed active on
an ignored article.

SQLite locks nothing: 'ErstGesperrt' reads the order off the queries, and
a count written by hand between the check and the save stands in for the
app. 'NebenDerApp' forces the overlap on PostgreSQL with two connections,
in both orders.
"""

from decimal import Decimal
from http import HTTPStatus
from unittest import mock, skipUnless

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import CustomUser
from api.serializers import counting
from api.test_merge_split_race import abfragen_waehrend
from api.tests import STICHTAG, adresse
from django_project.testing import administrator, inventur_anlegen
from inventur.models import Inventur, Zaehlposition
from inventur.test_merge_race import MitBestand, Nebeneinander, erste_abfrage
from organisation.models import Haus

from . import hausartikel_status
from .models import (
    Artikel,
    Artikelstatus,
    Basiseinheit,
    Behaelter,
    Erwartung,
    Hausartikel,
    Ware,
    Warenfamilie,
)

#: What a refused ignore says about a counted article ('Hausartikel.pruefe_ignorierbar').
SCHON_GEZAEHLT = "ist in einer laufenden Inventur schon gezählt"


class MitZaehlung(MitBestand):
    """The crate of Artikel C and the scale of Ware N in a running count, one counter assigned."""

    inventur: Inventur
    konto: CustomUser
    lose: Ware
    schale: Behaelter
    lose_artikel: Hausartikel
    kastenzeile: Zaehlposition
    waage: Zaehlposition

    def zaehlung_anlegen(self) -> None:
        self.bestand_anlegen()
        self.lose = Ware.objects.create(
            name="Ware N", basiseinheit=Basiseinheit.objects.get(name="g"), abwiegbar=True
        )
        self.schale = Behaelter.objects.create(name="Schale", tara=Decimal("250"))
        self.lose.behaelter.add(self.schale)
        self.lose_artikel = Hausartikel.objects.create(
            haus=self.haus,
            ware=self.lose,
            warengruppe=self.artikel.warengruppe,
            artikelnummer="101002",
            name="Ware N",
        )
        Erwartung.objects.create(ort=self.ort, artikel=self.lose_artikel, wiegen_erwartet=True)
        self.inventur = inventur_anlegen(self.haus, stichtag=STICHTAG)
        self.inventur.zaehlpositionen_anlegen()
        self.konto = CustomUser.objects.create_user(
            username="erste", password="geheim", email="erste@example.org"
        )
        self.konto.haeuser.add(self.haus)
        self.inventur.zaehlende.add(self.konto)
        self.inventur.zaehlung_starten()
        self.kastenzeile = self.inventur.zaehlpositionen.get(gebinde=self.kasten)
        self.waage = self.inventur.zaehlpositionen.get(gebinde__isnull=True)

    def client_der_app(self) -> APIClient:
        client = APIClient(raise_request_exception=False)
        client.force_authenticate(self.konto)
        return client

    def menge(self):
        return self.client_der_app().patch(
            adresse("zaehlposition-detail", self.kastenzeile.pk),
            {"eingabemenge": "7"},
            format="json",
        )

    def wiegung(self):
        return self.client_der_app().post(
            adresse("wiegung-list"),
            {
                "zaehlposition": self.waage.pk,
                "brutto_gramm": "1250",
                "behaelter": self.schale.pk,
                "kennung": "7b0c2f8e-6a8e-4f62-9a3c-0b8f2f1d9a11",
            },
            format="json",
        )

    def ignorieren(self, ware: Ware):
        return lambda: Ware.objects.get(pk=ware.pk).ignorieren()

    def ware_wechseln(self) -> None:
        """Artikel C hangs on the other product now. Its crate is foreign to it."""
        artikel = Hausartikel.objects.get(pk=self.artikel.pk)
        artikel.ware = Ware.objects.get(pk=self.ziel.pk)
        artikel.save()

    def zeile(self, zeile: Zaehlposition) -> tuple[bool, Decimal | None, int]:
        """Whether the row counts, its number and how many weighings it holds."""
        frisch = Zaehlposition.objects.get(pk=zeile.pk)
        return frisch.aktiv, frisch.eingabemenge, frisch.wiegungen.count()

    def stand(self, ware: Ware, artikel: Hausartikel) -> tuple[bool, str]:
        """Whether the product lies in "Not countable", and the status of its article."""
        familie = Ware.objects.get(pk=ware.pk).warenfamilie_id
        status = Hausartikel.objects.get(pk=artikel.pk).status
        return familie == Warenfamilie.nicht_zaehlbar().pk, status


class ErstGesperrt(MitZaehlung, TestCase):
    """The article and its rows are locked before a row is marked, read off the queries."""

    def setUp(self):
        self.zaehlung_anlegen()

    def pruefe_zuerst_gesperrt(self, abfragen) -> None:
        artikel = erste_abfrage(abfragen, Artikel, gesperrt=True)
        zeilen = erste_abfrage(abfragen, Zaehlposition, gesperrt=True)
        markiert = erste_abfrage(abfragen, Zaehlposition, beginnt="UPDATE")
        self.assertLess(artikel, zeilen, "die Zeilen vor ihrem Artikel")
        self.assertLess(zeilen, markiert, "markiert, bevor die Zeilen gesperrt sind")
        # By number and without an outer join, as the closing locks them.
        _modell, _sperre, _ohne_schluessel, reihenfolge, sql = abfragen[zeilen]
        self.assertEqual(reihenfolge, ("pk",))
        self.assertNotIn("JOIN", sql.upper().split(" WHERE ", 1)[0], sql)

    def test_das_ignorieren_sperrt_die_zeilen_bevor_es_sie_markiert(self):
        abfragen = abfragen_waehrend(self.ignorieren(self.ware))

        self.pruefe_zuerst_gesperrt(abfragen)
        self.assertFalse(self.zeile(self.kastenzeile)[0])

    def test_alle_artikel_der_ware_und_ihre_zeilen_vor_dem_ersten_speichern(self):
        """Two houses carry the product: everything is locked before the first article is written.

        Article by article, a location renamed in the same moment, which locks
        its rows by number, could hold a row of the second article while
        waiting for one of the first.
        """
        Hausartikel.objects.create(
            haus=Haus.objects.create(name="Kino Süd"),
            ware=self.ware,
            warengruppe=self.artikel.warengruppe,
            artikelnummer="102001",
            name="Artikel C",
        )

        abfragen = abfragen_waehrend(self.ignorieren(self.ware))

        zeilen = erste_abfrage(abfragen, Zaehlposition, gesperrt=True)
        self.assertLess(erste_abfrage(abfragen, Artikel, gesperrt=True), zeilen)
        self.assertLess(zeilen, erste_abfrage(abfragen, Artikel, beginnt="UPDATE"))
        self.assertFalse(self.zeile(self.kastenzeile)[0])

    def test_der_warenwechsel_sperrt_die_zeilen_bevor_er_sie_markiert(self):
        abfragen = abfragen_waehrend(self.ware_wechseln)

        self.pruefe_zuerst_gesperrt(abfragen)
        self.assertFalse(self.zeile(self.kastenzeile)[0])

    def test_was_nach_der_pruefung_gezaehlt_wird_haelt_das_ignorieren_auf(self):
        """The check before saving came a moment too early: the app counted right after it."""
        echt = Ware.pruefe_familienwechsel

        def pruefen_und_zaehlen(ware, familie):
            echt(ware, familie)
            Zaehlposition.objects.filter(pk=self.kastenzeile.pk).update(eingabemenge=Decimal("7"))

        with (
            mock.patch.object(
                Ware, "pruefe_familienwechsel", autospec=True, side_effect=pruefen_und_zaehlen
            ),
            self.assertRaises(ValidationError) as fehler,
        ):
            self.ignorieren(self.ware)()

        self.assertIn(SCHON_GEZAEHLT, " ".join(fehler.exception.messages))
        self.assertEqual(self.stand(self.ware, self.artikel), (False, Artikelstatus.AKTIV))
        self.assertEqual(self.zeile(self.kastenzeile), (True, Decimal("7"), 0))

    def test_die_seite_der_ware_nennt_die_absage_am_formular(self):
        """The same moment through the product's page: a message, not the error page."""
        echt = Ware.pruefe_familienwechsel

        def pruefen_und_zaehlen(ware, familie):
            echt(ware, familie)
            Zaehlposition.objects.filter(pk=self.kastenzeile.pk).update(eingabemenge=Decimal("7"))

        self.client.force_login(administrator())
        daten = {
            "name": self.ware.name,
            "basiseinheit": self.ware.basiseinheit_id,
            "warenfamilie": Warenfamilie.nicht_zaehlbar().pk,
            "umrechnung": "",
            "gebinde-TOTAL_FORMS": "0",
            "gebinde-INITIAL_FORMS": "0",
            "gebinde-MIN_NUM_FORMS": "0",
            "gebinde-MAX_NUM_FORMS": "1000",
        }
        with mock.patch.object(
            Ware, "pruefe_familienwechsel", autospec=True, side_effect=pruefen_und_zaehlen
        ):
            antwort = self.client.post(reverse("ware_edit", args=[self.ware.pk]), daten)

        self.assertEqual(antwort.status_code, HTTPStatus.OK)
        self.assertContains(antwort, SCHON_GEZAEHLT)
        self.assertEqual(self.stand(self.ware, self.artikel), (False, Artikelstatus.AKTIV))
        self.assertEqual(self.zeile(self.kastenzeile), (True, Decimal("7"), 0))


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class NebenDerApp(MitZaehlung, Nebeneinander):
    """A quantity or a weighing from the app beside an ignore or a change of product.

    Whoever holds the rows first finishes. The other one waits and then reads
    what the first one left: an ignore refuses a row counted meanwhile, a
    change of product leaves it standing, and a count after the ignore hears
    that its row was removed.
    """

    def setUp(self):
        self.zaehlung_anlegen()

    def pruefe_abgelehnt(self, ergebnis: dict) -> None:
        fehler = ergebnis.get("danach_fehler")
        self.assertIsInstance(fehler, ValidationError, ergebnis)
        assert isinstance(fehler, ValidationError)
        self.assertIn(SCHON_GEZAEHLT, " ".join(fehler.messages))

    def test_das_ignorieren_wartet_auf_die_menge(self):
        """The quantity holds its row while it writes. The ignore sees it and is refused."""
        ergebnis = self.gleichzeitig(
            self.menge,
            self.ignorieren(self.ware),
            (counting, "pruefen"),
            fehler_erlaubt=("danach",),
        )

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.OK)
        self.pruefe_abgelehnt(ergebnis)
        self.assertEqual(self.zeile(self.kastenzeile), (True, Decimal("7"), 0))
        self.assertEqual(self.stand(self.ware, self.artikel), (False, Artikelstatus.AKTIV))

    def test_das_ignorieren_wartet_auf_die_wiegung(self):
        """The weighing holds its row up to its container. The ignore sees the bowl."""
        ergebnis = self.gleichzeitig(
            self.wiegung,
            self.ignorieren(self.lose),
            (counting, "behaelter_sperren"),
            fehler_erlaubt=("danach",),
        )

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.CREATED)
        self.pruefe_abgelehnt(ergebnis)
        self.assertEqual(self.zeile(self.waage), (True, None, 1))
        self.assertEqual(self.stand(self.lose, self.lose_artikel), (False, Artikelstatus.AKTIV))

    def test_die_menge_wartet_auf_das_ignorieren(self):
        """Held after its locks: the quantity waits for the row and hears that it was removed."""
        ergebnis = self.gleichzeitig(
            self.ignorieren(self.ware), self.menge, (hausartikel_status, "plaetze_merken")
        )

        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.NOT_FOUND)
        self.assertEqual(ergebnis["danach"].data["detail"], "Die Zeile wurde inzwischen entfernt.")
        self.assertEqual(self.zeile(self.kastenzeile), (False, None, 0))
        self.assertEqual(self.stand(self.ware, self.artikel), (True, Artikelstatus.IGNORIERT))

    def test_der_warenwechsel_wartet_auf_die_menge(self):
        """Counted is counted: the crate of the old product stays, with its number."""
        ergebnis = self.gleichzeitig(self.menge, self.ware_wechseln, (counting, "pruefen"))

        self.assertEqual(ergebnis["zuerst"].status_code, HTTPStatus.OK)
        self.assertEqual(self.zeile(self.kastenzeile), (True, Decimal("7"), 0))
        self.assertEqual(Hausartikel.objects.get(pk=self.artikel.pk).ware_id, self.ziel.pk)

    def test_eine_wiegung_an_der_entfernten_zeile_wartet_auf_das_ignorieren(self):
        """Removed before, held by the ignore: the bowl waits and does not bring the row back."""
        Zaehlposition.objects.filter(pk=self.waage.pk).update(aktiv=False)

        ergebnis = self.gleichzeitig(
            self.ignorieren(self.lose), self.wiegung, (hausartikel_status, "plaetze_merken")
        )

        self.assertEqual(ergebnis["danach"].status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(self.zeile(self.waage), (False, None, 0))
        self.assertEqual(self.stand(self.lose, self.lose_artikel), (True, Artikelstatus.IGNORIERT))
