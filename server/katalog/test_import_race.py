"""An import beside a closing of the same house, and beside a change of the stock it relies on.

The import writes its rows in the order of the file, and a renamed article
locks its rows ('django_project/renaming.py'). A closing locks its
inventory, the house's locations and articles by number, then the rows.
Until 10/2026 a file that named a higher number first could hold that
article while the closing held a lower one, each waited for the other, and
PostgreSQL broke one of them off. Now the import locks every article it may
write first, in one call and by number ('services.apply._artikel_zuerst').

Then it measures the plan against the stock once more, under those locks
('services.apply._bestand_unter_der_sperre'). The form measured it before
any lock. Until 10/2026 a row discontinued meanwhile was renamed as if it
were carried, one deleted meanwhile ended in a server error, and so did a
new row whose twin someone had created.
"""

from decimal import Decimal
from unittest import mock, skipUnless

import pandas as pd
from django.db import connection, transaction
from django.test import TestCase

from django_project.testing import inventur_anlegen
from inventur.closing import Entscheidungen, abschliessen
from inventur.models import Inventurstatus
from inventur.test_merge_race import Nebeneinander
from organisation.models import Bereich, Haus, Ort

from .models import (
    Basiseinheit,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Ware,
    Warengruppe,
)
from .services import AKTIV, AKTUALISIEREN, als_daten, ausfuehren, planen
from .services import apply as anwenden
from .test_import_kept_unit import SPALTEN


class Bestand:
    """Kino Nord counts Artikel A and Artikel B, each in crates, in a running inventory."""

    haus: Haus
    artikel_a: Hausartikel
    artikel_b: Hausartikel

    def bestand_anlegen(self) -> None:
        self.haus = Haus.objects.create(name="Kino Nord")
        ort = Ort.objects.create(
            name="Thekenfront", bereich=Bereich.objects.create(name="Theke", haus=self.haus)
        )
        stueck = Basiseinheit.objects.get(kennung="STUECK")
        gruppe = Warengruppe.objects.create(name="Warengruppe G")
        for nummer, name in (("101001", "Artikel A"), ("101002", "Artikel B")):
            ware = Ware.objects.create(name=name, basiseinheit=stueck)
            kasten = Gebinde.objects.create(
                ware=ware, name="Kasten", faktor_in_basiseinheit=Decimal("20")
            )
            artikel = Hausartikel.objects.create(
                haus=self.haus, ware=ware, warengruppe=gruppe, artikelnummer=nummer, name=name
            )
            ErwartungGebinde.objects.create(
                erwartung=Erwartung.objects.create(ort=ort, artikel=artikel), gebinde=kasten
            )
        self.artikel_a = Hausartikel.objects.get(artikelnummer="101001")
        self.artikel_b = Hausartikel.objects.get(artikelnummer="101002")
        self.inventur = inventur_anlegen(self.haus)
        self.inventur.zaehlpositionen_anlegen()
        self.inventur.zaehlung_starten()
        self.inventur.zaehlpositionen.update(eingabemenge=Decimal("1"))

    def umbenennen(self) -> list[dict]:
        """Both renamed, the higher number first in the file."""
        tabelle = pd.DataFrame(
            [
                ["101002", "Artikel B neu", "Warengruppe G", "", 0, "STUECK", "Kino Nord"],
                ["101001", "Artikel A neu", "Warengruppe G", "", 0, "STUECK", "Kino Nord"],
            ],
            columns=SPALTEN,
        )
        saetze, _meldungen = planen(tabelle, self.haus)
        return [
            als_daten(satz) | {"vorgehen": AKTUALISIEREN, "uebernehmen": list(satz.abweichungen)}
            for satz in saetze
        ]

    def namen(self) -> list[str]:
        return sorted(Hausartikel.objects.filter(haus=self.haus).values_list("name", flat=True))

    def plan(self, *zeilen: tuple[str, str], vorgehen: str = AKTUALISIEREN) -> list[dict]:
        """A plan for these numbers and names beside the two articles as they are."""
        tabelle = pd.DataFrame(
            [
                [nummer, name, "Warengruppe G", "", 0, "STUECK", "Kino Nord"]
                for nummer, name in (("101001", "Artikel A"), ("101002", "Artikel B"), *zeilen)
            ],
            columns=SPALTEN,
        )
        saetze, _meldungen = planen(tabelle, self.haus)
        return [als_daten(satz) | {"vorgehen": vorgehen} for satz in saetze]


#: What adopting says first where the plan no longer fits the stock.
NICHTS_UEBERNOMMEN = "Übernommen wurde nichts. Seit dem Einlesen hat sich der Bestand von"


class DieSperreKommtZuerst(Bestand, TestCase):
    """SQLite locks nothing: the order of the calls says what PostgreSQL would lock when."""

    def setUp(self):
        self.bestand_anlegen()

    def test_jeder_artikel_des_plans_ist_gesperrt_bevor_einer_gespeichert_wird(self):
        folge = []
        echt_sperren, echt_speichern = anwenden.artikel_sperren, Hausartikel.save

        def sperren(nummern):
            folge.append(("sperren", set(nummern)))
            return echt_sperren(nummern)

        def speichern(artikel, *args, **kwargs):
            folge.append(("speichern", artikel.pk))
            return echt_speichern(artikel, *args, **kwargs)

        zeilen = self.umbenennen()
        with (
            mock.patch.object(anwenden, "artikel_sperren", side_effect=sperren),
            mock.patch.object(Hausartikel, "save", autospec=True, side_effect=speichern),
        ):
            ausfuehren(zeilen, self.haus)

        self.assertEqual(folge[0], ("sperren", {self.artikel_a.pk, self.artikel_b.pk}))
        self.assertEqual(
            [schritt for schritt in folge if schritt[0] == "speichern"],
            [("speichern", self.artikel_b.pk), ("speichern", self.artikel_a.pk)],
        )
        self.assertEqual(self.namen(), ["Artikel A neu", "Artikel B neu"])


class DerBestandUnterDerSperre(Bestand, TestCase):
    """What changed between the form's look and the locks, done by hand right before them."""

    def setUp(self):
        self.bestand_anlegen()

    @staticmethod
    def vor_der_sperre(tun):
        echt = anwenden._artikel_zuerst

        def erst_tun(zeilen, haus):
            tun()
            return echt(zeilen, haus)

        return mock.patch.object(anwenden, "_artikel_zuerst", side_effect=erst_tun)

    def pruefe_nichts_uebernommen(self, bericht, nummer: str) -> None:
        self.assertTrue(bericht.fehler[0].startswith(NICHTS_UEBERNOMMEN), bericht.fehler)
        self.assertIn(
            f"Diese Zeile passt nicht mehr zu dem, was Kino Nord unter {nummer} hat.",
            " ".join(bericht.fehler),
        )
        self.assertEqual((bericht.artikel_neu, bericht.artikel_geaendert), ([], []))
        self.assertEqual(bericht.uebergangen, bericht.zeilen)

    def test_ein_inzwischen_eingestellter_wird_nicht_umbenannt(self):
        zeilen = self.umbenennen()

        with self.vor_der_sperre(
            lambda: Hausartikel.objects.get(pk=self.artikel_b.pk).einstellen()
        ):
            bericht = ausfuehren(zeilen, self.haus)

        self.pruefe_nichts_uebernommen(bericht, "101002")
        self.assertEqual(self.namen(), ["Artikel A", "Artikel B"])

    def test_ein_inzwischen_geloeschter_ist_keine_fehlerseite(self):
        Hausartikel.objects.create(
            haus=self.haus,
            ware=Ware.objects.create(name="Artikel D", basiseinheit=self.artikel_a.basiseinheit),
            warengruppe=self.artikel_a.warengruppe,
            artikelnummer="101004",
            name="Artikel D",
        )
        zeilen = self.plan(("101004", "Artikel D neu"))

        with self.vor_der_sperre(
            lambda: Hausartikel.objects.filter(artikelnummer="101004").delete()
        ):
            bericht = ausfuehren(zeilen, self.haus)

        self.pruefe_nichts_uebernommen(bericht, "101004")
        self.assertEqual(self.namen(), ["Artikel A", "Artikel B"])

    def test_ein_inzwischen_angelegter_zwilling_wird_gemeldet(self):
        """Seen under the lock as for every path past the form: reported, not created."""
        zeilen = self.plan(("101005", "Artikel E"), vorgehen=AKTIV)

        def zwilling():
            Hausartikel.objects.create(
                haus=self.haus,
                ware=Ware.objects.create(
                    name="Artikel E", basiseinheit=self.artikel_a.basiseinheit
                ),
                warengruppe=self.artikel_a.warengruppe,
                artikelnummer="101005",
                name="Artikel E",
            )

        with self.vor_der_sperre(zwilling):
            bericht = ausfuehren(zeilen, self.haus)

        self.assertEqual(
            bericht.fehler,
            [
                "101005 „Artikel E“ gibt es in Kino Nord schon (aktiv) und wurde nicht noch "
                "einmal angelegt."
            ],
        )
        self.assertEqual(Hausartikel.objects.filter(artikelnummer="101005").count(), 1)

    def test_eine_inzwischen_vergebene_nummer(self):
        """The house carries a number once: a new row would end at the index."""
        zeilen = self.plan(("101005", "Artikel E"), vorgehen=AKTIV)
        stueck = self.artikel_a.basiseinheit

        def vergeben():
            Hausartikel.objects.create(
                haus=self.haus,
                ware=Ware.objects.create(name="Ware X", basiseinheit=stueck),
                warengruppe=self.artikel_a.warengruppe,
                artikelnummer="101005",
                name="Ware X",
            )

        with self.vor_der_sperre(vergeben):
            bericht = ausfuehren(zeilen, self.haus)

        self.pruefe_nichts_uebernommen(bericht, "101005")
        self.assertEqual(
            list(Hausartikel.objects.filter(artikelnummer="101005").values_list("name", flat=True)),
            ["Ware X"],
        )

    def test_eine_inzwischen_geloeschte_ware(self):
        gewaehlt = Ware.objects.create(name="Ware W", basiseinheit=self.artikel_a.basiseinheit)
        zeilen = [
            zeile | {"ware": gewaehlt}
            for zeile in self.plan(("101005", "Artikel E"), vorgehen=AKTIV)
        ]

        with self.vor_der_sperre(lambda: Ware.objects.filter(pk=gewaehlt.pk).delete()):
            bericht = ausfuehren(zeilen, self.haus)

        self.assertTrue(bericht.fehler[0].startswith(NICHTS_UEBERNOMMEN), bericht.fehler)
        self.assertIn("101005: Die gewählte Ware „Ware W“ gibt es nicht mehr.", bericht.fehler[1])
        self.assertFalse(Hausartikel.objects.filter(artikelnummer="101005").exists())

    def test_ohne_aenderung_dazwischen_wird_uebernommen(self):
        bericht = ausfuehren(self.umbenennen(), self.haus)

        self.assertEqual(bericht.fehler, [])
        self.assertEqual(self.namen(), ["Artikel A neu", "Artikel B neu"])


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class NebenDerSeiteDesArtikels(Bestand, Nebeneinander):
    """Artikel B is discontinued on its page while an import that renames it is on its way.

    The page holds the article's row, the import waits for its lock
    ('_artikel_zuerst') and measures the plan only then.
    """

    def setUp(self):
        self.bestand_anlegen()

    def test_der_import_wartet_und_uebernimmt_nichts(self):
        zeilen = self.umbenennen()

        def einstellen():
            with transaction.atomic():
                Hausartikel.objects.get(pk=self.artikel_b.pk).einstellen()

        ergebnis = self.gleichzeitig(
            einstellen,
            lambda: ausfuehren(zeilen, self.haus),
            (Hausartikel, "save"),
            nach_dem_aufruf=True,
        )

        bericht = ergebnis["danach"]
        self.assertTrue(bericht.fehler[0].startswith(NICHTS_UEBERNOMMEN), bericht.fehler)
        self.assertEqual(bericht.artikel_geaendert, [])
        self.assertEqual(self.namen(), ["Artikel A", "Artikel B"])


@skipUnless(connection.vendor == "postgresql", "SQLite kennt keine Sperre einzelner Zeilen")
class NebenEinemAbschluss(Bestand, Nebeneinander):
    """The import holds after its first article, the closing of the same house comes beside it."""

    def setUp(self):
        self.bestand_anlegen()

    def test_der_abschluss_wartet_bis_der_import_durch_ist(self):
        zeilen = self.umbenennen()

        ergebnis = self.gleichzeitig(
            lambda: ausfuehren(zeilen, self.haus),
            lambda: abschliessen(self.inventur, Entscheidungen()),
            (Hausartikel, "save"),
            nach_dem_aufruf=True,
        )

        self.assertEqual(len(ergebnis["zuerst"].artikel_geaendert), 2)
        self.assertEqual(self.namen(), ["Artikel A neu", "Artikel B neu"])
        self.inventur.refresh_from_db()
        self.assertEqual(self.inventur.status, Inventurstatus.ABGESCHLOSSEN)
