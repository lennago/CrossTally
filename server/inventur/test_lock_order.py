"""One lock order for every path that creates count positions or changes what they hang on.

The inventory, then the locations, then the articles, then the rows
('inventur.models.orte_und_artikel_sperren'). Until 10/2026 five paths took
them in another order, and two transactions could wait for each other until
PostgreSQL broke one off: the closing changed articles after it had locked its
rows, a run locked house by house, merging locations locked the location that
stays only at the end, the clarification activated an article before it
locked, and a change of the types took rows out before it locked.
'ZuerstGesperrt' reads the order off the queries and runs everywhere. The
classes after it force each overlap on PostgreSQL with two connections
('test_merge_race.Nebeneinander').
"""

from decimal import Decimal
from unittest import skipUnless

from django.db import connection
from django.db.models import ProtectedError
from django.test import TestCase

from api.test_merge_split_race import abfragen_waehrend
from api.tests import STICHTAG
from django_project.testing import inventur_anlegen, monatsinventur
from inventur.closing import Entscheidungen, abschliessen
from inventur.closing import decisions as entscheidungen_modul
from inventur.closing.questions import klaeren
from inventur.models import Inventur, Inventurstatus, Sollgrund, Sollzeile, Zaehlposition
from inventur.services import inventuren_anlegen
from inventur.services.clarification import sollzeile_aktivieren
from inventur.test_merge_race import (
    ZUSAMMENFUEHREN_HAELT,
    MitBestand,
    Nebeneinander,
    erste_abfrage,
)
from katalog import ort_split_merge
from katalog.models import (
    Artikel,
    Artikelstatus,
    Basiseinheit,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Inventurart,
    Ware,
    Warengruppe,
)
from organisation.models import Bereich, Haus, Ort


class MehrBestand(MitBestand):
    """What the paths here need beside the product of 'MitBestand'."""

    def zweites_haus(self) -> tuple[Haus, Hausartikel]:
        """Kino West carries the product too, its article created later, with the higher number."""
        haus = Haus.objects.create(name="Kino West")
        ort = Ort.objects.create(
            name="Thekenfront", bereich=Bereich.objects.create(name="Theke", haus=haus)
        )
        artikel = Hausartikel.objects.create(
            haus=haus,
            ware=self.ware,
            warengruppe=Warengruppe.objects.get(name="Warengruppe G"),
            artikelnummer="101001",
            name="Artikel C",
        )
        ErwartungGebinde.objects.create(
            erwartung=Erwartung.objects.create(ort=ort, artikel=artikel), gebinde=self.kasten
        )
        return haus, artikel

    def ware_n(self, ort: Ort, **felder) -> Hausartikel:
        """A second product in cartons of 12, expected at 'ort'."""
        ware = Ware.objects.create(
            name="Ware N", basiseinheit=Basiseinheit.objects.get(name="Stück(e)")
        )
        karton = Gebinde.objects.create(
            ware=ware, name="Karton", faktor_in_basiseinheit=Decimal("12")
        )
        artikel = Hausartikel.objects.create(
            haus=self.haus,
            ware=ware,
            warengruppe=Warengruppe.objects.get(name="Warengruppe G"),
            artikelnummer="101002",
            name="Ware N",
            **felder,
        )
        ErwartungGebinde.objects.create(
            erwartung=Erwartung.objects.create(ort=ort, artikel=artikel), gebinde=karton
        )
        return artikel

    def gezaehlt(self) -> Inventur:
        """A running inventory with two crates at the counter."""
        inventur = inventur_anlegen(self.haus, stichtag=STICHTAG)
        inventur.zaehlpositionen_anlegen()
        inventur.zaehlung_starten()
        inventur.zaehlpositionen.update(eingabemenge=Decimal("2"))
        return inventur


class ZuerstGesperrt(MehrBestand, TestCase):
    """Every path locks the locations and articles before it writes, read off the queries."""

    def setUp(self):
        self.bestand_anlegen()

    def vor(self, abfragen, gesperrt: type, geschrieben: type, beginnt: str) -> None:
        """The first lock of 'gesperrt' comes before the first such write of 'geschrieben'."""
        self.assertLess(
            erste_abfrage(abfragen, gesperrt, gesperrt=True),
            erste_abfrage(abfragen, geschrieben, beginnt=beginnt),
            f"{gesperrt.__name__} erst nach {beginnt} auf {geschrieben.__name__} gesperrt",
        )

    def test_ein_lauf_sperrt_alle_haeuser_bevor_er_die_erste_inventur_anlegt(self):
        west, _artikel = self.zweites_haus()
        abfragen = abfragen_waehrend(
            lambda: inventuren_anlegen([west, self.haus], [monatsinventur()], STICHTAG)
        )

        for modell in (Ort, Artikel):
            self.vor(abfragen, modell, Inventur, "INSERT")
        self.assertEqual(Zaehlposition.objects.count(), 2)

    def test_die_klaerung_sperrt_bevor_sie_den_artikel_aktiviert(self):
        inventur = inventur_anlegen(self.haus, stichtag=STICHTAG)
        self.artikel.deaktivieren()
        zeile = Sollzeile.objects.create(
            inventur=inventur,
            artikelnummer="101001",
            name="Artikel C",
            menge_basiseinheit=Decimal("3"),
            grund=Sollgrund.INAKTIV,
            hausartikel=self.artikel,
        )
        abfragen = abfragen_waehrend(lambda: sollzeile_aktivieren(zeile))

        for modell in (Ort, Artikel):
            self.vor(abfragen, modell, Artikel, "UPDATE")
        self.assertEqual(Hausartikel.objects.get(pk=self.artikel.pk).status, Artikelstatus.AKTIV)

    def test_der_wechsel_der_arten_sperrt_bevor_er_zeilen_herausnimmt(self):
        becher = Inventurart.objects.create(name="Becherinventur")
        self.ware_n(self.ort, inventurart=becher)
        inventur = inventur_anlegen(self.haus, stichtag=STICHTAG)
        inventur.zaehlpositionen_anlegen()
        abfragen = abfragen_waehrend(lambda: inventur.arten_aendern([becher]))

        for modell in (Ort, Artikel):
            self.vor(abfragen, modell, Zaehlposition, "UPDATE")
        self.assertEqual(inventur.zaehlpositionen.filter(aktiv=True).get().gebinde.name, "Karton")

    def test_der_abschluss_sperrt_die_artikel_vor_den_zeilen(self):
        inventur = self.gezaehlt()
        abfragen = abfragen_waehrend(lambda: abschliessen(inventur, Entscheidungen()))

        bestand = erste_abfrage(abfragen, Inventur, gesperrt=True)
        artikel = erste_abfrage(abfragen, Artikel, gesperrt=True)
        zeilen = erste_abfrage(abfragen, Zaehlposition, gesperrt=True)
        self.assertLess(bestand, artikel)
        self.assertLess(artikel, zeilen, "die Zeilen vor den Artikeln, anders als beim Umhängen")
        self.assertEqual(Inventur.objects.get().status, Inventurstatus.ABGESCHLOSSEN)

    def test_orte_zusammenfuehren_sperrt_beide_orte_zuerst(self):
        lager = Ort.objects.create(name="Lager", bereich=self.ort.bereich)
        self.ware_n(lager)
        abfragen = abfragen_waehrend(
            lambda: ort_split_merge.zusammenfuehren(self.ort, lager, "Thekenfront")
        )

        stelle = erste_abfrage(abfragen, Ort, gesperrt=True)
        _modell, _gesperrt, ohne_schluessel, reihenfolge, sql = abfragen[stelle]
        self.assertIn(" IN (", sql.upper(), "nur einer der beiden Orte")
        self.assertFalse(ohne_schluessel, "ohne Schlüssel, aber einer der beiden wird gelöscht")
        self.assertEqual(reihenfolge, ("pk",))
        self.assertLess(stelle, erste_abfrage(abfragen, Erwartung))
        self.assertFalse(Ort.objects.filter(pk=lager.pk).exists())


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class AbschlussWaehrendDesZusammenfuehrens(MehrBestand, Nebeneinander):
    """The closing brings an inactive counted article back while its product merges."""

    def setUp(self):
        super().setUp()
        self.inventur = self.gezaehlt()
        # Set inactive after counting: the closing asks whether it comes back.
        Hausartikel.objects.get(pk=self.artikel.pk).deaktivieren()

    def schliessen(self):
        inventur = Inventur.objects.get(pk=self.inventur.pk)
        wieder = {eintrag.schluessel for eintrag in klaeren(inventur).wiederaufnahmen}
        return abschliessen(inventur, Entscheidungen(wiederaufnehmen=wieder))

    def pruefe_beides(self) -> None:
        self.assertEqual(
            Inventur.objects.get(pk=self.inventur.pk).status, Inventurstatus.ABGESCHLOSSEN
        )
        self.pruefe_zusammengefuehrt()

    def test_das_zusammenfuehren_wartet_auf_den_abschluss(self):
        """Held once the questions are read, while the closing holds articles and rows."""
        self.gleichzeitig(
            self.schliessen,
            self.zusammenfuehren,
            (entscheidungen_modul, "klaeren"),
            nach_dem_aufruf=True,
        )

        self.pruefe_beides()

    def test_der_abschluss_wartet_aufs_zusammenfuehren(self):
        self.gleichzeitig(self.zusammenfuehren, self.schliessen, ZUSAMMENFUEHREN_HAELT)

        self.pruefe_beides()


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class LaufWaehrendDesZusammenfuehrens(MehrBestand, Nebeneinander):
    """A run over two houses that both carry the product, while it merges.

    Kino West comes first in the run, and its article has the higher number:
    house by house the run held it while the merge, in the order of the
    numbers, held the one of Kino Ost the run needed next.
    """

    def setUp(self):
        super().setUp()
        self.west, self.artikel_west = self.zweites_haus()

    def anlegen(self):
        return inventuren_anlegen(
            [Haus.objects.get(pk=self.west.pk), Haus.objects.get(pk=self.haus.pk)],
            [monatsinventur()],
            STICHTAG,
        )

    def pruefe_angelegt(self) -> None:
        """Both houses have their row, on the crate that stays."""
        self.assertEqual(
            sorted(Zaehlposition.objects.values_list("artikel_id", "gebinde_id")),
            sorted([(self.artikel.pk, self.bleibt.pk), (self.artikel_west.pk, self.bleibt.pk)]),
        )

    def test_das_zusammenfuehren_wartet_auf_den_lauf(self):
        """Held once the first house has its rows, while the run holds every article."""
        self.gleichzeitig(
            self.anlegen,
            self.zusammenfuehren,
            (Inventur, "_zaehlpositionen_anlegen"),
            nach_dem_aufruf=True,
        )

        self.pruefe_zusammengefuehrt()
        self.pruefe_angelegt()

    def test_der_lauf_wartet_aufs_zusammenfuehren(self):
        self.gleichzeitig(self.zusammenfuehren, self.anlegen, ZUSAMMENFUEHREN_HAELT)

        self.pruefe_zusammengefuehrt()
        self.pruefe_angelegt()


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class OrteZusammenfuehrenWaehrendDesAnlegens(MehrBestand, Nebeneinander):
    """Merging the store into the counter while an inventory of the house arises.

    The counter has the lower number. Creating locks every location of the
    house in the order of the numbers, so it held the counter while the
    merge held the store and wanted the counter for its new name.
    """

    def setUp(self):
        super().setUp()
        self.lager = Ort.objects.create(name="Lager", bereich=self.ort.bereich)
        self.artikel_n = self.ware_n(self.lager)

    def orte_zusammenfuehren(self):
        return ort_split_merge.zusammenfuehren(
            Ort.objects.get(pk=self.ort.pk), Ort.objects.get(pk=self.lager.pk), "Theke vorn"
        )

    def anlegen(self):
        return inventuren_anlegen([Haus.objects.get(pk=self.haus.pk)], [monatsinventur()], STICHTAG)

    def test_das_anlegen_wartet_auf_beide_orte(self):
        """Held right after the merge locked, before it reads the lists."""
        self.gleichzeitig(self.orte_zusammenfuehren, self.anlegen, (ort_split_merge, "vorschau"))

        self.assertFalse(Ort.objects.filter(pk=self.lager.pk).exists())
        self.assertEqual(Ort.objects.get(pk=self.ort.pk).name, "Theke vorn")
        # Both rows stand at the location that stays.
        self.assertEqual(
            sorted(Zaehlposition.objects.values_list("ort_id", "artikel_id")),
            sorted([(self.ort.pk, self.artikel.pk), (self.ort.pk, self.artikel_n.pk)]),
        )

    def test_das_zusammenfuehren_wartet_aufs_anlegen(self):
        """Then a row hangs on the store, and the merge stops at it."""
        ergebnis = self.gleichzeitig(
            self.anlegen,
            self.orte_zusammenfuehren,
            (Inventur, "_erwartungen_im_umfang"),
            nach_dem_aufruf=True,
            fehler_erlaubt=("danach",),
        )

        self.assertIsInstance(ergebnis.get("danach_fehler"), ProtectedError)
        self.assertTrue(Ort.objects.filter(pk=self.lager.pk).exists())
        self.assertEqual(Zaehlposition.objects.count(), 2)


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class ZweiAktivierungen(MehrBestand, Nebeneinander):
    """Two inactive articles of one house brought back from the clarification at once."""

    def setUp(self):
        super().setUp()
        self.artikel_n = self.ware_n(self.ort)
        self.inventur = inventur_anlegen(self.haus, stichtag=STICHTAG)
        self.zeilen = []
        for artikel in (self.artikel, self.artikel_n):
            artikel.deaktivieren()
            self.zeilen.append(
                Sollzeile.objects.create(
                    inventur=self.inventur,
                    artikelnummer=artikel.artikelnummer,
                    name=artikel.name,
                    menge_basiseinheit=Decimal("3"),
                    grund=Sollgrund.INAKTIV,
                    hausartikel=artikel,
                )
            )

    def aktivieren(self, stelle: int):
        return lambda: sollzeile_aktivieren(Sollzeile.objects.get(pk=self.zeilen[stelle].pk))

    def test_die_zweite_wartet_auf_die_erste(self):
        """Held right after the first article is active again."""
        self.gleichzeitig(
            self.aktivieren(0),
            self.aktivieren(1),
            (Hausartikel, "aktivieren"),
            nach_dem_aufruf=True,
        )

        for artikel in (self.artikel, self.artikel_n):
            self.assertEqual(Hausartikel.objects.get(pk=artikel.pk).status, Artikelstatus.AKTIV)
        self.assertEqual(self.inventur.zaehlpositionen.count(), 2)
        self.assertFalse(Sollzeile.objects.exists())


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class ArtenWaehrendDesZusammenfuehrens(MehrBestand, Nebeneinander):
    """The monthly inventory in preparation becomes a cup inventory while its product merges."""

    def setUp(self):
        super().setUp()
        self.becher = Inventurart.objects.create(name="Becherinventur")
        self.artikel_n = self.ware_n(self.ort, inventurart=self.becher)
        self.inventur = inventur_anlegen(self.haus, stichtag=STICHTAG)
        self.inventur.zaehlpositionen_anlegen()

    def arten(self):
        Inventur.objects.get(pk=self.inventur.pk).arten_aendern([self.becher])

    def test_das_zusammenfuehren_wartet_auf_den_wechsel(self):
        """Held once the monthly rows are taken out, while the change holds them."""
        self.gleichzeitig(
            self.arten,
            self.zusammenfuehren,
            (Inventur, "_nicht_mehr_dran_entfernen"),
            nach_dem_aufruf=True,
        )

        self.pruefe_zusammengefuehrt()
        aktiv = self.inventur.zaehlpositionen.filter(aktiv=True)
        self.assertEqual(list(aktiv.values_list("artikel_id", flat=True)), [self.artikel_n.pk])
