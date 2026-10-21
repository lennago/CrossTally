"""Sorting through the column headers: the rules, independent of a list."""

from dataclasses import dataclass
from decimal import Decimal

from django.test import SimpleTestCase

from .columns import Spaltensortierung, gliedern

SPALTEN = {
    "name": ("Name", ("name",)),
    "menge": ("Menge", ("menge",)),
}


@dataclass
class Zeile:
    name: str
    menge: Decimal | None


def sortierung(rohwert):
    return Spaltensortierung(rohwert, SPALTEN, "name", ("name",))


class Kopf(SimpleTestCase):
    def test_erster_klick_aufsteigend_zweiter_absteigend(self):
        self.assertEqual(sortierung("").kopf()["menge"].naechster, "menge")
        self.assertEqual(sortierung("menge").kopf()["menge"].naechster, "-menge")
        self.assertEqual(sortierung("-menge").kopf()["menge"].naechster, "menge")

    def test_nur_die_aktive_spalte_zeigt_ihre_richtung(self):
        koepfe = sortierung("-menge").kopf()
        self.assertEqual(koepfe["menge"].aria_sort, "descending")
        self.assertEqual(koepfe["name"].aria_sort, "none")

    def test_unbekannte_spalte_faellt_auf_die_vorgabe_zurueck(self):
        """A stale bookmark must not break the list."""
        self.assertEqual(sortierung("-gibtsnicht").rohwert, "name")


class PythonListe(SimpleTestCase):
    """The check list sorts computed rows, not a query."""

    zeilen = [
        Zeile("Artikel C", Decimal("3")),
        Zeile("artikel a", Decimal("3")),
        Zeile("Artikel B", None),
        Zeile("Dose", Decimal("0")),
    ]

    def namen(self, rohwert):
        return [zeile.name for zeile in sortierung(rohwert).sortieren(self.zeilen)]

    def test_leere_werte_stehen_in_beiden_richtungen_hinten(self):
        self.assertEqual(self.namen("menge"), ["Dose", "artikel a", "Artikel C", "Artikel B"])
        self.assertEqual(self.namen("-menge"), ["artikel a", "Artikel C", "Dose", "Artikel B"])

    def test_zweitschluessel_bleibt_unter_absteigender_spalte_aufsteigend(self):
        """Equal quantities are still alphabetical, regardless of case."""
        folge = self.namen("-menge")
        self.assertLess(folge.index("artikel a"), folge.index("Artikel C"))

    def test_null_ist_kein_leerer_wert(self):
        """A counted zero is information, not a missing value."""
        self.assertEqual(self.namen("menge")[0], "Dose")


class Gliedern(SimpleTestCase):
    def test_gleiche_namen_bilden_einen_block(self):
        bloecke = gliedern(["a1", "a2", "b1"], lambda wert: wert[0])
        self.assertEqual(
            [(b["name"], b["eintraege"]) for b in bloecke], [("a", ["a1", "a2"]), ("b", ["b1"])]
        )
