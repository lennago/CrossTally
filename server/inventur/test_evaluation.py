"""Reports and PDF, grouped by product family.

Checked above all is the computing: sums over mixed units, the
differences over several inventories and the rule that stocks are only
added up when one inventory per house is in the selection.
"""

from datetime import date
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.db import connection
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import CustomUser
from django_project.testing import inventur_anlegen, oeffnet_neuen_tab
from katalog.models import (
    Basiseinheit,
    Dimension,
    Gebinde,
    Hausartikel,
    Ware,
    Warenfamilie,
    Warengruppe,
    portion_abgekuerzt,
)
from organisation.models import Bereich, Haus, Ort

from .evaluation import (
    OHNE_FAMILIE,
    Warenzeile,
    abgeschlossene,
    familien_einer_inventur,
    familien_ueber_inventuren,
    warengruppen_einer_inventur,
)
from .models import Inventur
from .services import FEHLT, MEHR, PASST


class Grundlagen(TestCase):
    """Two houses, two families, one of them with mixed units."""

    @classmethod
    def setUpTestData(cls):
        cls.user = CustomUser.objects.create_superuser(
            username="chef", password="geheim", email="chef@example.org"
        )
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.kg = Basiseinheit.objects.get(name="kg")
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        cls.kino_ost = Haus.objects.create(name="Kino Ost")
        cls.kino_sued = Haus.objects.create(name="Kino Süd")

        cls.limo = Warenfamilie.objects.create(name="Warenfamilie M", leiteinheit=cls.stueck)
        cls.eis = Warenfamilie.objects.create(name="Warenfamilie E", leiteinheit=cls.kg)

        cls.cola = cls.ware("Artikel C", cls.stueck, cls.limo)
        cls.eis_kg = cls.ware("Artikel V lose", cls.kg, cls.eis)
        # A portion weighs 125 g. Without this entry the family could not
        # sum the product.
        cls.eis_stueck = cls.ware("Ware am Stiel", cls.stueck, cls.eis)
        cls.eis_stueck.umrechnungen_setzen({Dimension.MASSE: Decimal("125")})
        cls.ohne = cls.ware("Posten V", cls.stueck, None)
        cls.lose = cls.ware("Ware N lose", cls.kg, None)

    @classmethod
    def ware(cls, name, einheit, familie) -> Ware:
        ware = Ware.objects.create(name=name, basiseinheit=einheit, warenfamilie=familie)
        Gebinde.objects.create(ware=ware, name="Stück", faktor_in_basiseinheit=1)
        return ware

    #: The number range per house. Not from 'haus.pk': under PostgreSQL the
    #: sequences keep running through the whole suite, and from the hundredth
    #: house number on the article number would be seven characters long.
    #: SQLite does not check the length, which is why that only showed up in
    #: the CI database.
    NUMMERNKREIS = {"Kino Ost": "101", "Kino Süd": "102"}

    def inventur_mit(self, haus, stichtag, mengen: dict) -> Inventur:
        """A closed inventory: {product: (actual, target)}."""
        inventur = inventur_anlegen(haus, stichtag=stichtag)
        # A house can carry several inventories. Area and location then
        # already exist.
        bereich, _ = Bereich.objects.get_or_create(name=f"Bereich {haus.name}", haus=haus)
        ort, _ = Ort.objects.get_or_create(name=f"Theke {haus.name}", bereich=bereich)
        for nummer, (ware, (ist, soll)) in enumerate(mengen.items(), start=1):
            # Per house and product there is exactly one house article. On the
            # second inventory of the same house it is already there.
            artikel, _ = Hausartikel.objects.get_or_create(
                haus=haus,
                ware=ware,
                defaults={
                    "warengruppe": self.gruppe,
                    "artikelnummer": f"{self.NUMMERNKREIS[haus.name]}{nummer:03d}",
                    "name": ware.name,
                },
            )
            inventur.zaehlpositionen.create(
                ort=ort,
                artikel=artikel,
                gebinde=ware.gebinde.first(),
                eingabemenge=Decimal(ist),
            )
            if soll is not None:
                inventur.sollbestaende.create(hausartikel=artikel, menge_basiseinheit=Decimal(soll))
        inventur.zaehlung_starten()
        inventur.abschliessen()
        return inventur


class SummenUeberEinheiten(Grundlagen):
    def test_familie_rechnet_ueber_dimensionen_um(self):
        """16 portions at 8 per kilo are two kilos, plus the loose 3 kg."""
        inventur = self.inventur_mit(
            self.kino_ost,
            date(2026, 1, 31),
            {self.eis_kg: ("3", None), self.eis_stueck: ("16", None)},
        )

        block = next(b for b in familien_einer_inventur(inventur) if b.name == "Warenfamilie E")

        self.assertEqual(block.ist_gesamt, "5 kg")

    def test_differenzen_tragen_ihr_vorzeichen(self):
        """The plus too, in the row as in the family's sum."""
        inventur = self.inventur_mit(
            self.kino_ost,
            date(2026, 1, 31),
            {self.eis_kg: ("5", "3"), self.eis_stueck: ("24", "8"), self.ohne: ("2", "5")},
        )

        bloecke = familien_einer_inventur(inventur)
        block = next(b for b in bloecke if b.name == "Warenfamilie E")
        zeilen = {z.ware: z for b in bloecke for z in b.zeilen}

        self.assertEqual(zeilen[self.eis_kg].differenz_text, "+2 kg")
        self.assertEqual(zeilen[self.eis_stueck].differenz_text, "+16 Stk.")
        self.assertEqual(zeilen[self.ohne].differenz_text, "-3 Stk.")
        # 16 portions at 8 per kilo are two kilos, plus the loose two.
        self.assertEqual(block.differenz_gesamt, "+4 kg")

    def test_differenzen_tragen_ihre_farbe(self):
        """Green means it fits. Yellow means there is more. Red means something is missing."""
        inventur = self.inventur_mit(
            self.kino_ost,
            date(2026, 1, 31),
            {self.eis_kg: ("5", "3"), self.eis_stueck: ("8", "8"), self.ohne: ("2", "5")},
        )

        bloecke = familien_einer_inventur(inventur)
        zeilen = {z.ware: z for b in bloecke for z in b.zeilen}

        self.assertEqual(zeilen[self.eis_kg].differenz_klasse, "differenz-mehr")
        self.assertEqual(zeilen[self.eis_stueck].differenz_klasse, "differenz-passt")
        self.assertEqual(zeilen[self.ohne].differenz_klasse, "differenz-fehlt")
        # The family's sum follows its lead unit: two kilos too many.
        self.assertEqual(bloecke[0].differenz_klasse, "differenz-mehr")

    def test_ohne_leiteinheit_faerbt_nur_ein_einiges_ergebnis(self):
        """The block "Without product family" sums per dimension. Disagreeing sums stay grey."""
        inventur = self.inventur_mit(
            self.kino_ost, date(2026, 1, 31), {self.ohne: ("2", "5"), self.lose: ("1", "4")}
        )
        block = next(b for b in familien_einer_inventur(inventur) if b.name == OHNE_FAMILIE)
        self.assertEqual(block.differenz_klasse, "differenz-fehlt")

        inventur = self.inventur_mit(
            self.kino_sued, date(2026, 1, 31), {self.ohne: ("9", "5"), self.lose: ("1", "4")}
        )
        block = next(b for b in familien_einer_inventur(inventur) if b.name == OHNE_FAMILIE)
        # Four pieces too many and three kilos too few make no statement.
        self.assertEqual(block.differenz_klasse, "")

    def test_ohne_soll_bleibt_die_differenz_auf_dem_blatt_leer(self):
        """A zero would look like "fine". Until 09/2026 it stood on the sheet that way."""
        inventur = self.inventur_mit(
            self.kino_ost, date(2026, 1, 31), {self.cola: ("12", None), self.ohne: ("2", "5")}
        )

        zeilen = {z.ware: z for b in familien_einer_inventur(inventur) for z in b.zeilen}

        self.assertEqual(zeilen[self.cola].differenz_text, "-")
        self.assertFalse(zeilen[self.cola].auffaellig)
        self.assertEqual(zeilen[self.ohne].differenz, Decimal("-3"))

    def test_ohne_zuordnung_steht_am_ende(self):
        inventur = self.inventur_mit(
            self.kino_ost, date(2026, 1, 31), {self.ohne: ("2", None), self.cola: ("1", None)}
        )

        namen = [block.name for block in familien_einer_inventur(inventur)]

        self.assertEqual(namen[-1], OHNE_FAMILIE)


class HausZaehltAnders(Grundlagen):
    """Kino Süd carries the loose goods in grams, the product itself is in kg."""

    def in_gramm(self):
        """A closed inventory whose article was frozen in grams: 2500 g, target 2000 g.

        The house counts in grams before closing. Until 10/2026 the unit was
        switched afterwards here, and the result followed it.
        """
        Hausartikel.objects.create(
            haus=self.kino_sued,
            ware=self.lose,
            warengruppe=self.gruppe,
            artikelnummer="101901",
            name=self.lose.name,
            basiseinheit=Basiseinheit.objects.get(name="g"),
        )
        inventur = self.inventur_mit(self.kino_sued, date(2026, 1, 31), {self.lose: (0, 0)})
        ergebnis = inventur.ergebnisse.get()
        ergebnis.gesamtmenge_basiseinheit = Decimal("2500")
        ergebnis.sollbestand = Decimal("2000")
        ergebnis.differenz = Decimal("500")
        ergebnis.save()
        return inventur

    def test_das_blatt_bleibt_in_der_einheit_des_hauses(self):
        """The sheet goes into the system that books in the house's unit, like the page.

        Until 10/2026 it showed "2,5 kg" where the page and the frozen result
        said "2500 g".
        """
        inventur = self.in_gramm()

        zeile = next(
            zeile
            for block in warengruppen_einer_inventur(inventur)
            for zeile in block.zeilen
            if zeile.ware == self.lose
        )

        self.assertEqual(zeile.ist_text, "2500 g")
        self.assertEqual(zeile.soll_text, "2000 g")
        self.assertEqual(zeile.differenz_text, "+500 g")

    def test_die_auswertung_rechnet_in_die_einheit_der_ware(self):
        inventur = self.in_gramm()

        zeile = next(
            zeile
            for block in familien_einer_inventur(inventur)
            for zeile in block.zeilen
            if zeile.ware == self.lose
        )
        self.assertEqual(zeile.ist_text, "2,5 kg")
        self.assertEqual(zeile.differenz_text, "+0,5 kg")

        bloecke, _ = familien_ueber_inventuren([inventur])
        summe = next(z for block in bloecke for z in block.zeilen if z.ware == self.lose)
        self.assertEqual(summe.differenz, Decimal("0.5"))


class BlattNachWarengruppen(Grundlagen):
    """The sheet of one inventory: by product group, within it by article number."""

    def test_gruppen_und_nummern_wie_in_der_warenwirtschaft(self):
        andere = Warengruppe.objects.create(name="Warengruppe A")
        inventur = self.inventur_mit(
            self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10"), self.ohne: ("2", "5")}
        )
        # The second article moves to another group. The numbers count 101001, 101002.
        Hausartikel.objects.filter(inventurergebnisse__inventur=inventur, ware=self.ohne).update(
            warengruppe=andere
        )
        bloecke = warengruppen_einer_inventur(inventur)
        self.assertEqual([block.name for block in bloecke], ["Warengruppe A", "Warengruppe G"])
        self.assertEqual([zeile.nummer for zeile in bloecke[0].zeilen], ["101002"])
        self.assertEqual([zeile.ware for zeile in bloecke[1].zeilen], [self.cola])

    def test_eigene_artikel_ohne_nummer_stehen_hinten(self):
        inventur = self.inventur_mit(
            self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10"), self.ohne: ("2", "5")}
        )
        Hausartikel.objects.filter(inventurergebnisse__inventur=inventur, ware=self.cola).update(
            artikelnummer=""
        )
        bloecke = warengruppen_einer_inventur(inventur)
        self.assertEqual([zeile.ware for zeile in bloecke[0].zeilen], [self.ohne, self.cola])


class AusgeschiedeneArtikel(Grundlagen):
    def test_eingestellter_artikel_steht_mit_vermerk_da(self):
        """The result is history. The article was relevant when it was counted."""
        inventur = self.inventur_mit(self.kino_ost, date(2026, 1, 31), {self.cola: ("3", None)})
        Hausartikel.objects.get(ware=self.cola).einstellen()

        zeile = next(z for b in familien_einer_inventur(inventur) for z in b.zeilen)

        self.assertEqual(zeile.vermerk, "eingestellt")

    def test_nachtraeglich_ignoriertes_faellt_heraus(self):
        inventur = self.inventur_mit(
            self.kino_ost, date(2026, 1, 31), {self.cola: ("3", None), self.ohne: ("2", None)}
        )
        Hausartikel.objects.get(ware=self.ohne).ignorieren()

        einzeln = {z.ware for b in familien_einer_inventur(inventur) for z in b.zeilen}
        bloecke, _ = familien_ueber_inventuren(abgeschlossene())
        gesamt = {z.ware for b in bloecke for z in b.zeilen}

        self.assertEqual(einzeln, {self.cola})
        self.assertEqual(gesamt, {self.cola})


class DifferenzenUeberHaeuser(Grundlagen):
    def test_zaehlt_die_differenzen_je_ware_zusammen(self):
        """Twenty too many here, twenty too few there. That is a movement."""
        self.inventur_mit(self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10")})
        self.inventur_mit(self.kino_sued, date(2026, 1, 31), {self.cola: ("5", "25")})

        bloecke, bestaende = familien_ueber_inventuren(abgeschlossene())
        zeile = next(z for b in bloecke for z in b.zeilen if z.ware == self.cola)

        self.assertEqual(zeile.differenz, Decimal("0"))
        self.assertEqual(zeile.haeuser, 2)
        self.assertTrue(bestaende)

    def test_ohne_soll_in_allen_inventuren_bleibt_die_differenz_leer(self):
        """A zero would mean "fine", but nothing was ever compared."""
        self.inventur_mit(self.kino_ost, date(2026, 1, 31), {self.cola: ("12", None)})
        self.inventur_mit(self.kino_sued, date(2026, 1, 31), {self.cola: ("4", None)})

        bloecke, _ = familien_ueber_inventuren(abgeschlossene())
        zeile = next(z for b in bloecke for z in b.zeilen if z.ware == self.cola)

        self.assertIsNone(zeile.differenz)
        self.assertEqual(zeile.differenz_text, "-")
        self.assertEqual(zeile.teilsumme_text, "")
        self.assertFalse(zeile.auffaellig)
        self.assertEqual(bloecke[0].differenz_gesamt, "")

    def test_teilsumme_sagt_aus_wie_vielen_inventuren(self):
        """Where only part had a target, only that counts, and it says so."""
        self.inventur_mit(self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10")})
        self.inventur_mit(self.kino_sued, date(2026, 1, 31), {self.cola: ("4", None)})

        bloecke, _ = familien_ueber_inventuren(abgeschlossene())
        zeile = next(z for b in bloecke for z in b.zeilen if z.ware == self.cola)

        self.assertEqual(zeile.differenz, Decimal("20"))
        self.assertEqual(zeile.teilsumme_text, "aus 1 von 2 Inventuren")

    def test_volle_summe_braucht_keinen_hinweis(self):
        self.inventur_mit(self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10")})
        self.inventur_mit(self.kino_sued, date(2026, 1, 31), {self.cola: ("5", "25")})

        bloecke, _ = familien_ueber_inventuren(abgeschlossene())
        zeile = next(z for b in bloecke for z in b.zeilen if z.ware == self.cola)

        self.assertEqual(zeile.teilsumme_text, "")

    def test_mehrere_inventuren_je_haus_lassen_die_bestaende_weg(self):
        """Adding stocks over time would be no result but nonsense."""
        self.inventur_mit(self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10")})
        self.inventur_mit(self.kino_ost, date(2026, 2, 28), {self.cola: ("40", "10")})

        bloecke, bestaende = familien_ueber_inventuren(abgeschlossene())
        zeile = next(z for b in bloecke for z in b.zeilen if z.ware == self.cola)

        self.assertFalse(bestaende)
        self.assertIsNone(zeile.ist)
        self.assertEqual(zeile.differenz, Decimal("50"))

    def test_nur_mehrere_haeuser_laesst_die_einzelnen_weg(self):
        self.inventur_mit(self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10")})
        self.inventur_mit(
            self.kino_sued, date(2026, 1, 31), {self.cola: ("5", "25"), self.ohne: ("1", None)}
        )

        bloecke, _ = familien_ueber_inventuren(abgeschlossene(), nur_mehrere_haeuser=True)
        waren = {z.ware for b in bloecke for z in b.zeilen}

        self.assertEqual(waren, {self.cola})

    def test_zeitraum_und_haus_grenzen_ein(self):
        self.inventur_mit(self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10")})
        self.inventur_mit(self.kino_sued, date(2026, 6, 30), {self.cola: ("5", "25")})

        im_januar = abgeschlossene(von=date(2026, 1, 1), bis=date(2026, 3, 31))
        nur_kino_sued = abgeschlossene(haeuser=[self.kino_sued])

        self.assertEqual([i.haus for i in im_januar], [self.kino_ost])
        self.assertEqual([i.haus for i in nur_kino_sued], [self.kino_sued])


class AbfragenWachsenNichtMitDenFamilien(Grundlagen):
    """The factors came individually per family (until 09/2026), and per product they must not."""

    def familie(self, name) -> Ware:
        """A family in kilos with a product in pieces: summing only works through the factor."""
        familie = Warenfamilie.objects.create(name=name, leiteinheit=self.kg)
        ware = self.ware(f"Ware aus {name}", self.stueck, familie)
        ware.umrechnungen_setzen({Dimension.MASSE: Decimal("125")})
        return ware

    def abfragen(self, auswerten) -> int:
        """Including the sums under every family. Only the page reads the factors."""
        with CaptureQueriesContext(connection) as gezaehlt:
            summen = [
                (block.ist_gesamt, block.differenz_gesamt, block.differenz_klasse)
                for block in auswerten()
            ]
        self.assertTrue(summen)
        return len(gezaehlt)

    def test_eine_und_viele_inventuren(self):
        waren = [self.familie(f"Warenfamilie {buchstabe}") for buchstabe in "RSTUV"]
        eine = self.inventur_mit(self.kino_ost, date(2026, 1, 31), {waren[0]: ("16", "8")})
        fuenf = self.inventur_mit(
            self.kino_sued, date(2026, 1, 31), {ware: ("16", "8") for ware in waren}
        )
        self.assertEqual(
            self.abfragen(lambda: familien_einer_inventur(fuenf)),
            self.abfragen(lambda: familien_einer_inventur(eine)),
        )
        self.assertEqual(
            self.abfragen(lambda: familien_ueber_inventuren([fuenf])[0]),
            self.abfragen(lambda: familien_ueber_inventuren([eine])[0]),
        )


class UeberMehrereInventurenGeteilt(Grundlagen):
    """Over several inventories every product is built once, not once per result.

    The same articles come again every month. Joined to each row, a year
    built eight objects per result, and the report spent two thirds of its
    time on that (until 09/2026).
    """

    def gebaut(self, modell, auswerten) -> int:
        """How many rows of 'modell' became objects while evaluating."""
        entstanden = []
        urspruenglich = modell.from_db.__func__

        def mitgezaehlt(cls, db, field_names, values):
            entstanden.append(cls)
            return urspruenglich(cls, db, field_names, values)

        with mock.patch.object(modell, "from_db", classmethod(mitgezaehlt)):
            bloecke, _ = auswerten()
        self.assertTrue(bloecke)
        return len(entstanden)

    def drei_monate(self):
        for monat in (1, 2, 3):
            for haus in (self.kino_ost, self.kino_sued):
                self.inventur_mit(
                    haus,
                    date(2026, monat, 28),
                    {self.cola: ("30", "10"), self.eis_kg: ("4", "5")},
                )

    def test_jede_ware_und_jeder_artikel_entsteht_einmal(self):
        self.drei_monate()
        auswerten = lambda: familien_ueber_inventuren(abgeschlossene())  # noqa: E731
        # Twelve results name two products, carried by two houses each.
        self.assertEqual(self.gebaut(Ware, auswerten), 2)
        self.assertEqual(self.gebaut(Hausartikel, auswerten), 4)

    def test_die_abfragen_wachsen_nicht_mit_den_inventuren(self):
        self.inventur_mit(self.kino_ost, date(2025, 12, 28), {self.cola: ("30", "10")})
        with CaptureQueriesContext(connection) as wenige:
            familien_ueber_inventuren(abgeschlossene())
        self.drei_monate()
        with CaptureQueriesContext(connection) as viele:
            familien_ueber_inventuren(abgeschlossene())
        self.assertEqual(len(viele), len(wenige))

    def test_die_summen_bleiben_dieselben(self):
        """Shared objects must not add up: each result counts with its own quantity."""
        self.drei_monate()
        bloecke, sinnvoll = familien_ueber_inventuren(abgeschlossene())
        je_ware = {zeile.ware: zeile for block in bloecke for zeile in block.zeilen}
        self.assertFalse(sinnvoll)
        self.assertEqual(je_ware[self.cola].differenz, Decimal("120"))
        self.assertEqual(je_ware[self.cola].ergebnisse, 6)
        self.assertEqual(je_ware[self.eis_kg].differenz, Decimal("-6"))
        self.assertEqual(je_ware[self.cola].haeuser, 2)


class Seiten(Grundlagen):
    def setUp(self):
        self.client.force_login(self.user)

    def test_auswertungsseite_zeigt_die_familien(self):
        self.inventur_mit(self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10")})

        antwort = self.client.get(reverse("auswertung"))

        self.assertContains(antwort, "Warenfamilie M")
        self.assertContains(antwort, "Differenzen über Inventuren")

    def test_familien_kommen_zugeklappt(self):
        """A family opens only on click. The header already carries the sum."""
        self.inventur_mit(self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10")})

        antwort = self.client.get(reverse("auswertung"))

        self.assertContains(antwort, '<details class="border rounded mb-2" data-gruppe="familie:')
        self.assertNotContains(antwort, "<details open")
        self.assertContains(antwort, "data-alle-gruppen")

    def test_seite_faerbt_jede_differenz(self):
        """The colour stands *in* the cell: on the cell itself Bootstrap would beat it."""
        self.inventur_mit(
            self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10"), self.ohne: ("2", "5")}
        )

        antwort = self.client.get(reverse("auswertung"))

        self.assertContains(antwort, '<span class="differenz-mehr">+20 Stk.</span>')
        self.assertContains(antwort, '<span class="differenz-fehlt">-3 Stk.</span>')
        # The family's sum carries the same colour, only quieter.
        self.assertContains(antwort, '<span class="differenz-mehr differenz-leise">')

    def test_seite_nennt_die_teilsumme(self):
        self.inventur_mit(self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10")})
        self.inventur_mit(self.kino_sued, date(2026, 1, 31), {self.cola: ("4", None)})

        antwort = self.client.get(reverse("auswertung"))

        self.assertContains(antwort, "aus 1 von 2 Inventuren")

    def test_pdf_der_auswertung(self):
        self.inventur_mit(self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10")})

        antwort = self.client.get(reverse("auswertung"), {"pdf": "1"})

        self.assertEqual(antwort["Content-Type"], "application/pdf")
        self.assertTrue(antwort.content.startswith(b"%PDF"))

    def test_das_pdf_oeffnet_sich_in_einem_neuen_tab(self):
        """The page with its filters stays open. 'inline' keeps the browser from downloading."""
        inventur = self.inventur_mit(self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10")})
        pdf_der_inventur = reverse("inventur_pdf", args=[inventur.pk])

        self.assertTrue(oeffnet_neuen_tab(self.client.get(reverse("auswertung")), "?pdf=1"))
        self.assertTrue(
            oeffnet_neuen_tab(self.client.get(inventur.get_absolute_url()), pdf_der_inventur)
        )
        for antwort in (
            self.client.get(reverse("auswertung"), {"pdf": "1"}),
            self.client.get(pdf_der_inventur),
        ):
            self.assertTrue(antwort["Content-Disposition"].startswith("inline;"))

    def blatt_der_auswertung(self, **auswahl) -> str:
        """The template before WeasyPrint, because no text can be searched in the finished PDF."""
        antwort = self.client.get(reverse("auswertung"), auswahl)
        return render_to_string("pdf/evaluation.html", antwort.context_data)

    def test_blatt_nennt_zeitraum_und_haeuser_statt_jeder_inventur(self):
        """One inventory per house and month would give a page-long header over a year."""
        for stichtag in (date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)):
            self.inventur_mit(self.kino_ost, stichtag, {self.cola: ("30", "10")})
        self.inventur_mit(self.kino_sued, date(2026, 2, 28), {self.cola: ("4", "4")})

        blatt = self.blatt_der_auswertung()

        self.assertIn("Stichtage vom 31.01.2026 bis 31.03.2026", blatt)
        self.assertIn("Häuser: Kino Ost, Kino Süd", blatt)
        self.assertEqual(blatt.count("Kino Ost"), 1)
        self.assertNotIn("28.02.2026", blatt)

    def test_die_fusszeile_traegt_kein_html(self):
        """The footer is a CSS "content" string. An entity would be printed as text."""
        self.inventur_mit(self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10")})

        blatt = self.blatt_der_auswertung()

        self.assertIn('content: "Auswertung · 1 Inventur"', blatt)
        self.assertNotIn("&middot;", blatt.split("</style>")[0])

    def test_blatt_nennt_nur_was_in_der_auswahl_liegt(self):
        """The filter reaches further than the inventories. Printed is what is reported."""
        self.inventur_mit(self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10")})
        self.inventur_mit(self.kino_sued, date(2026, 6, 30), {self.cola: ("4", "4")})

        blatt = self.blatt_der_auswertung(von="2026-01-01", bis="2026-03-31")

        self.assertIn("Stichtag 31.01.2026", blatt)
        self.assertNotIn("Stichtage vom", blatt)
        self.assertIn("Haus: Kino Ost", blatt)
        self.assertNotIn("Kino Süd", blatt)

    def test_blatt_sagt_wenn_nur_waren_aus_mehreren_haeusern_gelistet_sind(self):
        self.inventur_mit(self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10")})
        self.inventur_mit(self.kino_sued, date(2026, 1, 31), {self.cola: ("4", "4")})
        hinweis = "Nur Waren, die in mehreren Häusern gezählt wurden"

        self.assertIn(hinweis, self.blatt_der_auswertung(nur_mehrere_haeuser="on"))
        self.assertNotIn(hinweis, self.blatt_der_auswertung())

    def test_das_blatt_traegt_den_eigenen_titel(self):
        """Whoever prints the drinks of one summer files the sheet under that name."""
        self.inventur_mit(self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10")})

        blatt = self.blatt_der_auswertung(titel="Getränke im Sommer")

        self.assertIn("<title>Getränke im Sommer</title>", blatt)
        self.assertIn("<h1>Getränke im Sommer</h1>", blatt)
        self.assertNotIn("Differenzen über Inventuren", blatt)

    def test_ohne_titel_bleibt_es_bei_der_vorgabe(self):
        self.inventur_mit(self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10")})

        for auswahl in ({}, {"titel": ""}, {"titel": "   "}):
            with self.subTest(auswahl=auswahl):
                blatt = self.blatt_der_auswertung(**auswahl)
                self.assertIn("<title>Differenzen über Inventuren</title>", blatt)
                self.assertIn("<h1>Differenzen über Inventuren</h1>", blatt)

    def test_die_datei_heisst_wie_das_blatt(self):
        """Two reports saved one after the other must not both land as the same file."""
        self.inventur_mit(self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10")})

        def dateiname(**auswahl) -> str:
            antwort = self.client.get(reverse("auswertung"), {"pdf": "1", **auswahl})
            return antwort["Content-Disposition"]

        self.assertIn('filename="getranke-im-sommer.pdf"', dateiname(titel="Getränke im Sommer"))
        self.assertIn('filename="auswertung-differenzen.pdf"', dateiname())
        # Nothing but punctuation leaves no slug: the generic name stays instead of ".pdf".
        self.assertIn('filename="auswertung-differenzen.pdf"', dateiname(titel="???"))

    def test_die_seite_behaelt_ihre_ueberschrift_und_reicht_den_titel_weiter(self):
        """Only the sheet is renamed. The page passes the title on with the other filters."""
        self.inventur_mit(self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10")})

        antwort = self.client.get(reverse("auswertung"), {"titel": "Getränke im Sommer"})

        self.assertContains(antwort, '<h1 class="h3 mb-0">Differenzen über Inventuren</h1>')
        self.assertContains(antwort, 'name="titel"')
        self.assertContains(antwort, 'placeholder="Differenzen über Inventuren"')
        self.assertContains(antwort, "titel=Getr%C3%A4nke+im+Sommer&pdf=1")

    def test_ein_zu_langer_titel_nimmt_die_anderen_filter_nicht_mit(self):
        """The error stays at the field. The period still applies, the sheet keeps the default."""
        self.inventur_mit(self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10")})
        self.inventur_mit(self.kino_sued, date(2026, 6, 30), {self.cola: ("4", "4")})

        antwort = self.client.get(
            reverse("auswertung"), {"titel": "x" * 101, "bis": "2026-03-31", "pdf": "0"}
        )

        self.assertContains(antwort, "höchstens 100 Zeichen")
        kontext = antwort.context_data
        assert kontext is not None
        self.assertEqual(kontext["titel"], "Differenzen über Inventuren")
        self.assertEqual(len(kontext["inventuren"]), 1)

    def test_pdf_einer_abgeschlossenen_inventur(self):
        inventur = self.inventur_mit(self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10")})

        antwort = self.client.get(reverse("inventur_pdf", args=[inventur.pk]))

        self.assertEqual(antwort["Content-Type"], "application/pdf")
        self.assertTrue(antwort.content.startswith(b"%PDF"))

    def test_das_blatt_traegt_dieselben_farben(self):
        """Checked is the template before WeasyPrint. The finished PDF contains only colour."""
        inventur = self.inventur_mit(
            self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10"), self.ohne: ("2", "5")}
        )
        bloecke = warengruppen_einer_inventur(inventur)

        blatt = render_to_string(
            "pdf/inventur.html",
            {"inventur": inventur, "bloecke": bloecke, "anzahl_waren": len(bloecke)},
        )

        self.assertIn(f'class="zahl {MEHR}"', blatt)
        self.assertIn(f'class="zahl {FEHLT}"', blatt)

    def test_das_blatt_zeigt_die_notiz(self):
        """Whoever transfers the numbers reads why one of them looks the way it does."""
        inventur = self.inventur_mit(self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10")})
        inventur.ergebnisse.update(notiz="zwei Kästen\nhinten im Lager")
        bloecke = warengruppen_einer_inventur(inventur)

        blatt = render_to_string(
            "pdf/inventur.html",
            {"inventur": inventur, "bloecke": bloecke, "anzahl_waren": len(bloecke)},
        )

        self.assertInHTML('<div class="notiz">zwei Kästen<br>hinten im Lager</div>', blatt)

    def test_kein_pdf_fuer_eine_laufende_inventur(self):
        """Printed is the frozen state. Everything else still moves."""
        inventur = inventur_anlegen(self.kino_ost, stichtag=date(2026, 3, 31))

        antwort = self.client.get(reverse("inventur_pdf", args=[inventur.pk]), follow=True)

        self.assertContains(antwort, "noch nicht abgeschlossen")


class ZeileSummiert(SimpleTestCase):
    """What a result lacks contributes nothing, and a sum without any stays empty."""

    def test_hinzufuegen(self):
        zeile = Warenzeile(ware=Ware(name="Ware N"))
        zeile.hinzufuegen(ist=Decimal("2"), soll=None, differenz=None)
        zeile.hinzufuegen(ist=Decimal("3"), soll=Decimal("4"), differenz=Decimal("-1"))
        zeile.hinzufuegen(differenz=Decimal("0.5"))
        self.assertEqual(
            (zeile.ist, zeile.soll, zeile.differenz), (Decimal("5"), Decimal("4"), Decimal("-0.5"))
        )
        leer = Warenzeile(ware=Ware(name="Ware M"))
        leer.hinzufuegen(ist=Decimal("0"))
        self.assertEqual((leer.ist, leer.soll, leer.differenz), (Decimal("0"), None, None))


class Farben(SimpleTestCase):
    """The colour of the difference has to have a rule everywhere, on screen as on paper.

    A class without a rule goes unnoticed. The number then simply stands in
    black, and nobody misses a colour they have never seen. The sheet has its
    own stylesheet, because WeasyPrint loads no Bootstrap.
    """

    def test_jede_klasse_hat_ihre_regel(self):
        stylesheets = {
            "site.css": settings.BASE_DIR / "static" / "site.css",
            "pdf/base.html": settings.BASE_DIR / "templates" / "pdf" / "base.html",
        }
        for name, pfad in stylesheets.items():
            text = pfad.read_text(encoding="utf-8")
            for klasse in (PASST, FEHLT, MEHR):
                with self.subTest(stylesheet=name, klasse=klasse):
                    self.assertIn(f".{klasse} {{", text)


class FamilienInFamilien(Grundlagen):
    """A family above sums everything below it, in its own unit, from the products."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.liter = Basiseinheit.objects.get(name="Liter")
        # Warenfamilie G in litres above the lemonades in pieces and the juices in litres.
        cls.getraenke = Warenfamilie.objects.create(name="Warenfamilie G", leiteinheit=cls.liter)
        cls.limo.uebergeordnet = cls.getraenke
        cls.limo.save()
        cls.saefte = Warenfamilie.objects.create(
            name="Warenfamilie S", leiteinheit=cls.liter, uebergeordnet=cls.getraenke
        )
        cls.saft = cls.ware("Artikel S", cls.liter, cls.saefte)
        # A bottle holds 500 ml.
        cls.cola.umrechnungen_setzen({Dimension.VOLUMEN: Decimal("500")})

    def test_die_summe_oben_kommt_aus_den_waren(self):
        """Twenty bottles too many and two litres missing: nine litres, not "20 + -2"."""
        self.inventur_mit(
            self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10"), self.saft: ("3", "5")}
        )
        bloecke, _ = familien_ueber_inventuren(abgeschlossene())

        self.assertEqual([block.name for block in bloecke], ["Warenfamilie G"])
        oben = bloecke[0]
        self.assertEqual(oben.name, "Warenfamilie G")
        self.assertEqual(
            [block.name for block in oben.unterbloecke], ["Warenfamilie M", "Warenfamilie S"]
        )
        self.assertEqual(oben.zeilen, [])
        self.assertEqual(oben.anzahl, 2)
        self.assertEqual(oben.differenz_gesamt, "+8 l")
        self.assertEqual(oben.unterbloecke[0].differenz_gesamt, "+20 Stk.")
        self.assertEqual(oben.unterbloecke[1].differenz_gesamt, "-2 l")

    def test_das_blatt_einer_inventur_steht_als_baum(self):
        inventur = self.inventur_mit(
            self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10"), self.saft: ("3", "5")}
        )
        bloecke = familien_einer_inventur(inventur)
        self.assertEqual(bloecke[0].name, "Warenfamilie G")
        self.assertEqual(bloecke[0].ist_gesamt, "18 l")

    def test_familienfilter_und_mehrere_haeuser_wirken_zusammen(self):
        """Only the family and within it only what both houses count."""
        self.inventur_mit(
            self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10"), self.saft: ("3", "5")}
        )
        self.inventur_mit(self.kino_sued, date(2026, 1, 31), {self.cola: ("12", "10")})

        bloecke, _ = familien_ueber_inventuren(abgeschlossene(), familien={self.limo.pk})
        self.assertEqual([block.familie for block in bloecke], [self.limo])
        self.assertEqual(bloecke[0].tiefe, 0)
        self.assertEqual({zeile.ware.name for zeile in bloecke[0].zeilen}, {"Artikel C"})

        bloecke, _ = familien_ueber_inventuren(
            abgeschlossene(), familien={self.getraenke.pk, self.limo.pk, self.saefte.pk}
        )
        self.assertEqual(bloecke[0].anzahl, 2)
        bloecke, _ = familien_ueber_inventuren(
            abgeschlossene(),
            familien={self.getraenke.pk, self.limo.pk, self.saefte.pk},
            nur_mehrere_haeuser=True,
        )
        self.assertEqual(bloecke[0].anzahl, 1)
        self.assertEqual([block.name for block in bloecke[0].unterbloecke], ["Warenfamilie M"])

    def test_ohne_familie_laesst_sich_mitwaehlen(self):
        self.inventur_mit(
            self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10"), self.ohne: ("2", "5")}
        )
        bloecke, _ = familien_ueber_inventuren(abgeschlossene(), familien={None})
        self.assertEqual([str(block.name) for block in bloecke], [str(OHNE_FAMILIE)])


class FamilienSeiten(Grundlagen):
    def setUp(self):
        self.client.force_login(self.user)
        self.liter = Basiseinheit.objects.get(name="Liter")
        self.getraenke = Warenfamilie.objects.create(name="Warenfamilie G", leiteinheit=self.liter)
        self.limo.uebergeordnet = self.getraenke
        self.limo.save()
        self.cola.umrechnungen_setzen({Dimension.VOLUMEN: Decimal("500")})
        self.inventur = self.inventur_mit(
            self.kino_ost, date(2026, 1, 31), {self.cola: ("30", "10")}
        )

    def test_auswertung_zeigt_den_baum_und_die_wahl(self):
        antwort = self.client.get(reverse("auswertung"))
        html = antwort.content.decode()
        self.assertIn(f'data-gruppe="familie:{self.getraenke.pk}"', html)
        self.assertIn(f'data-gruppe="familie:{self.limo.pk}"', html)
        self.assertIn(f'data-oben="{self.getraenke.pk}"', html)
        self.assertIn("+10 l", html)
        self.assertIn("multi_select.js", html)

    def test_auswertung_nach_familie_eingeschraenkt(self):
        antwort = self.client.get(reverse("auswertung"), {"familie": [self.limo.pk]})
        self.assertContains(antwort, "Warenfamilie M")
        self.assertNotContains(antwort, f'data-gruppe="familie:{self.getraenke.pk}"')
        self.assertEqual(antwort.context["familien_text"], "Warenfamilie G › Warenfamilie M")

    def test_pdf_der_auswertung_traegt_die_familien(self):
        antwort = self.client.get(reverse("auswertung"), {"familie": [self.limo.pk], "pdf": "1"})
        self.assertEqual(antwort["Content-Type"], "application/pdf")
        html = render_to_string(
            "pdf/evaluation.html",
            {
                "inventuren": [self.inventur],
                "bloecke": [],
                "familien_text": "Warenfamilie G › Warenfamilie M",
                "hausnamen": ["Kino Ost"],
                "farben": "alle",
            },
        )
        self.assertIn("Warenfamilien: Warenfamilie G › Warenfamilie M", html)

    def test_ohne_warenfamilie_laesst_sich_waehlen(self):
        self.inventur_mit(
            self.kino_sued, date(2026, 1, 31), {self.cola: ("5", "5"), self.ohne: ("2", "3")}
        )

        antwort = self.client.get(reverse("auswertung"), {"familie": ["ohne"]})

        self.assertEqual(antwort.context["familien_text"], "Ohne Warenfamilie")
        self.assertEqual(
            [zeile.ware for block in antwort.context["bloecke"] for zeile in block.alle_zeilen],
            [self.ohne],
        )

    def test_das_ende_vor_dem_anfang(self):
        antwort = self.client.get(reverse("auswertung"), {"von": "2026-02-01", "bis": "2026-01-01"})
        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(antwort.context["form"].errors["bis"], ["Das Ende liegt vor dem Anfang."])

    def test_das_blatt_einer_inventur_kennt_keine_familien(self):
        """By group and number, as the system lists them. Families stay in the report."""
        html = render_to_string(
            "pdf/inventur.html",
            {
                "inventur": self.inventur,
                "bloecke": warengruppen_einer_inventur(self.inventur),
                "anzahl_waren": 1,
                "farben": "alle",
            },
        )
        self.assertIn("<h2>Warengruppe G</h2>", html)
        self.assertNotIn("Warenfamilie", html)
        self.assertNotIn("Summe", html)


class Portionen(Grundlagen):
    """A family sums in portions, the unit only a family carries."""

    def test_riegel_und_nuesse_werden_portionen(self):
        portion = Basiseinheit.objects.get(kennung="PORTION")
        gramm = Basiseinheit.objects.get(name="g")
        snacks = Warenfamilie.objects.create(name="Warenfamilie S", leiteinheit=portion)
        riegel = self.ware("Ware R", self.stueck, snacks)
        riegel.umrechnungen_setzen({Dimension.PORTION: Decimal("1")})
        nuesse = self.ware("Ware N", gramm, snacks)
        # A hundred grams are one portion.
        nuesse.umrechnungen_setzen({Dimension.PORTION: Decimal("0.01")})
        self.inventur_mit(
            self.kino_ost, date(2026, 1, 31), {riegel: ("12", "10"), nuesse: ("500", "800")}
        )

        bloecke, _ = familien_ueber_inventuren(abgeschlossene())
        block = next(block for block in bloecke if block.familie == snacks)
        # Two bars too many, three portions of nuts missing. One portion
        # stays singular, seventeen are plural.
        self.assertEqual(block.differenz_gesamt, "-1 Portion")
        self.assertEqual(block.ist_gesamt, "17 Portionen")
        # On paper short, one portion like seventeen.
        with portion_abgekuerzt():
            self.assertEqual(block.differenz_gesamt, "-1 Port.")
            self.assertEqual(block.ist_gesamt, "17 Port.")
