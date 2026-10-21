"""The inventory on awkward data: where the faults of 10/2026 sat.

Most tests of this app count one product in pieces, in one family, with one
article per product and house. The faults found in 10/2026 sat right beside
that: a family below a family, a house that counts in another unit than the
product, two articles of one product in one house, half pieces from a
recipe, an inventory that already has its rows. Here all of it stands in one
stock, and each test below was red before its fix.
"""

from datetime import date
from decimal import Decimal

from django.urls import reverse

from django_project.testing import AngemeldeteSeiten, inventur_anlegen
from katalog.models import (
    Artikelstatus,
    Basiseinheit,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Rezepturartikel,
    Rezepturkomponente,
    Ware,
    Warenfamilie,
    Warengruppe,
)
from organisation.models import Bereich, Haus, Ort

from .closing import Entscheidungen, abschliessen
from .evaluation import (
    Warenzeile,
    familien_eines_laufs,
    familien_ueber_inventuren,
    warengruppen_einer_inventur,
)
from .models import Inventur, Sollbestand, Zaehlposition
from .services import ergebniszeilen

STICHTAG = date(2026, 1, 31)


def zahl(text: str) -> Decimal:
    """A number as a row shows it: "+15 Stk." is 15, "-0,5" is -0.5."""
    return Decimal(text.split(maxsplit=1)[0].replace(",", "."))


def zeile_der_ware(bloecke, ware: Ware) -> Warenzeile:
    """The row of a product, however deep its family stands."""
    return next(zeile for block in bloecke for zeile in block.alle_zeilen if zeile.ware == ware)


class UnbequemerBestand(AngemeldeteSeiten):
    """Kino Nord and Kino Süd count Artikel C and Ware N on the same day.

    Artikel C is in pieces, in "Warenfamilie S" below "Warenfamilie R". Kino
    Nord carries it twice: the current article and a discontinued one that
    still has a counted row. A portion holds half a piece of it. Ware N is in
    kg, directly in "Warenfamilie R". Kino Nord counts it in pieces of 0.5 kg,
    Kino Süd in kg.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        stueck = Basiseinheit.objects.get(name="Stück(e)")
        oben = Warenfamilie.objects.create(name="Warenfamilie R", leiteinheit=stueck)
        unten = Warenfamilie.objects.create(
            name="Warenfamilie S", leiteinheit=stueck, uebergeordnet=oben
        )
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        cls.cola = Ware.objects.create(
            name="Artikel C 0,33", basiseinheit=stueck, warenfamilie=unten
        )
        cls.ware_n = Ware.objects.create(
            name="Ware N", basiseinheit=Basiseinheit.objects.get(name="kg"), warenfamilie=oben
        )
        cls.kasten = Gebinde.objects.create(
            ware=cls.cola, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        cls.beutel = Gebinde.objects.create(
            ware=cls.ware_n, name="Beutel", faktor_in_basiseinheit=Decimal("0.5")
        )
        cls.portion = Rezepturartikel.objects.create(name="Portion R", basiseinheit=stueck)
        Rezepturkomponente.objects.create(
            rezepturartikel=cls.portion, ware=cls.cola, menge_in_basiseinheit=Decimal("0.5")
        )

        cls.nord = Haus.objects.create(name="Kino Nord")
        cls.sued = Haus.objects.create(name="Kino Süd")
        cls.orte = {
            haus.pk: Ort.objects.create(
                name="Thekenfront", bereich=Bereich.objects.create(name="Theke", haus=haus)
            )
            for haus in (cls.nord, cls.sued)
        }
        cls.nord_cola = cls.artikel(cls.nord, "101001", cls.cola, cls.kasten)
        cls.nord_cola_alt = cls.artikel(
            cls.nord, "101009", cls.cola, name="Artikel C alt", status=Artikelstatus.EINGESTELLT
        )
        cls.nord_n = cls.artikel(
            cls.nord,
            "101002",
            cls.ware_n,
            cls.beutel,
            basiseinheit=stueck,
            faktor_zur_ware=Decimal("0.5"),
        )
        cls.sued_cola = cls.artikel(cls.sued, "101101", cls.cola, cls.kasten)
        cls.sued_n = cls.artikel(cls.sued, "101102", cls.ware_n, cls.beutel)

    @classmethod
    def artikel(cls, haus, nummer, ware, gebinde=None, **felder) -> Hausartikel:
        """A house article, expected at the counter in 'gebinde' if one is given."""
        felder.setdefault("name", ware.name)
        artikel = Hausartikel.objects.create(
            haus=haus, ware=ware, warengruppe=cls.gruppe, artikelnummer=nummer, **felder
        )
        if gebinde is not None:
            erwartung = Erwartung.objects.create(ort=cls.orte[haus.pk], artikel=artikel)
            ErwartungGebinde.objects.create(erwartung=erwartung, gebinde=gebinde)
        return artikel

    @staticmethod
    def menge(inventur: Inventur, artikel: Hausartikel, anzahl: str) -> None:
        inventur.zaehlpositionen.filter(artikel=artikel).update(eingabemenge=Decimal(anzahl))

    @staticmethod
    def soll(inventur: Inventur, mengen: dict[Hausartikel, str]) -> None:
        for artikel, menge in mengen.items():
            Sollbestand.objects.create(
                inventur=inventur, hausartikel=artikel, menge_basiseinheit=Decimal(menge)
            )

    def lauf(self) -> tuple[Inventur, Inventur]:
        """Both houses counted, with target, in the count."""
        nord = inventur_anlegen(self.nord, stichtag=STICHTAG)
        sued = inventur_anlegen(self.sued, stichtag=STICHTAG)
        for inventur in (nord, sued):
            inventur.zaehlpositionen_anlegen()
            inventur.zaehlung_starten()
        theke = self.orte[self.nord.pk]
        # Kino Nord: 2 crates and 5 portions of half a piece are 42.5 pieces
        # against 53. The discontinued article: 1 crate against 25. Ware N:
        # 3 bags of 0.5 kg are 3 of the house's pieces against 4.
        self.menge(nord, self.nord_cola, "2")
        Zaehlposition.objects.create(
            inventur=nord, ort=theke, artikel=self.portion, eingabemenge=Decimal("5")
        )
        Zaehlposition.objects.create(
            inventur=nord,
            ort=theke,
            artikel=self.nord_cola_alt,
            gebinde=self.kasten,
            eingabemenge=Decimal("1"),
            unerwartet=True,
        )
        self.menge(nord, self.nord_n, "3")
        self.soll(nord, {self.nord_cola: "53", self.nord_cola_alt: "25", self.nord_n: "4"})
        # Kino Süd: 3 crates against 45, 2 bags (1 kg) against 0.5 kg.
        self.menge(sued, self.sued_cola, "3")
        self.menge(sued, self.sued_n, "2")
        self.soll(sued, {self.sued_cola: "45", self.sued_n: "0.5"})
        return nord, sued

    def abgeschlossen(self) -> tuple[Inventur, Inventur]:
        """Both closed, read fresh: the frozen rows hang on the status."""
        for inventur in self.lauf():
            abschliessen(inventur, Entscheidungen())
        nord, sued = Inventur.objects.order_by("haus__name")
        return nord, sued

    def test_jede_gezeigte_zeile_geht_auf(self):
        """Actual minus target is the difference, in the check list and on the sheet.

        42.5 pieces read "43" against "53", and the difference said "-11".
        """
        for inventur in self.lauf():
            with self.subTest(haus=inventur.haus.name):
                zeilen = ergebniszeilen(inventur)
                self.assertTrue(zeilen)
                for zeile in zeilen:
                    self.assertEqual(
                        zahl(zeile.ist_text) - zahl(zeile.soll_text),
                        zahl(zeile.differenz_text),
                        zeile.hausartikel.name,
                    )
                abschliessen(inventur, Entscheidungen())
                inventur.refresh_from_db()
                blatt = [z for block in warengruppen_einer_inventur(inventur) for z in block.zeilen]
                self.assertEqual(len(blatt), len(zeilen))
                for zeile in blatt:
                    self.assertEqual(
                        zahl(zeile.ist_text) - zahl(zeile.soll_text),
                        zahl(zeile.differenz_text),
                        zeile.ware.name,
                    )

    def test_der_reiter_gesamt_zaehlt_jede_stufe_und_jeden_artikel(self):
        """Two products, both opposite. Artikel C stands one family down.

        Kino Nord's column of Artikel C is the difference of both its
        articles: -10 and -5.
        """
        self.lauf()

        antwort = self.client.get(reverse("inventur_lauf", args=[STICHTAG.isoformat()]))

        self.assertEqual(
            (antwort.context["anzahl_waren"], antwort.context["anzahl_gegenlaeufig"]), (2, 2)
        )
        cola = zeile_der_ware(antwort.context["bloecke"], self.cola)
        self.assertEqual(cola.je_haus, [Decimal("-15"), Decimal("15")])
        self.assertEqual(cola.ist, Decimal("123"))

    def test_die_auswertung_zaehlt_inventuren_und_nicht_artikel(self):
        """Artikel C comes from two inventories, not from three results."""
        bloecke, _ = familien_ueber_inventuren(self.abgeschlossen())

        cola = zeile_der_ware(bloecke, self.cola)
        self.assertEqual((cola.ergebnisse, cola.mit_differenz, cola.haeuser), (2, 2, 2))
        self.assertEqual(cola.differenz, Decimal("0"))

    def test_die_einheit_des_hauses_bleibt_am_ergebnis(self):
        """Kino Nord counts Ware N in kg from now on. Its closed day stays 3 pieces of 0.5 kg."""
        nord, sued = self.abgeschlossen()
        vorher = zeile_der_ware(familien_eines_laufs([nord, sued]), self.ware_n)
        self.assertEqual(
            (vorher.ist, vorher.je_haus), (Decimal("2.5"), [Decimal("-0.5"), Decimal("0.5")])
        )

        self.nord_n.basiseinheit = Basiseinheit.objects.get(name="kg")
        self.nord_n.faktor_zur_ware = None
        self.nord_n.save()

        nachher = zeile_der_ware(familien_eines_laufs([nord, sued]), self.ware_n)
        self.assertEqual((nachher.ist, nachher.je_haus), (vorher.ist, vorher.je_haus))
        blatt = [
            zeile.ist_text
            for block in warengruppen_einer_inventur(nord)
            for zeile in block.zeilen
            if zeile.ware == self.ware_n
        ]
        self.assertEqual(blatt, ["3 Stk."])

    def test_vorhandene_zeilen_kommen_nicht_noch_einmal_dazu(self):
        """Laid out again, an inventory with its rows gets none: the answer is 0."""
        nord, _ = self.lauf()
        anzahl = nord.zaehlpositionen.count()

        self.assertEqual(nord.zaehlpositionen_anlegen(), 0)
        self.assertEqual(nord.zaehlpositionen.count(), anzahl)
