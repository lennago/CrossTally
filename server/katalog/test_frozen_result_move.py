"""A frozen result keeps its way into the product when its article moves to another dimension.

Frozen against a product of its own dimension, a result carries no factor:
within one dimension the units compute. When its article moves to a product
of another dimension, only the article connects the two products. Until
10/2026 the result kept no factor, and the report left it out of every sum
with a note. A result with a factor of its own kept that one, although it
led into the old product, and the report read a wrong number from it.
"""

from datetime import date
from decimal import Decimal

import pandas as pd
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from django_project.testing import AngemeldeteSeiten, inventur_anlegen, monatsinventur
from inventur.evaluation import Warenzeile, familien_eines_laufs, familien_ueber_inventuren
from organisation.models import Bereich, Haus, Ort

from .models import FAKTOR_MINDESTENS, Basiseinheit, Gebinde, Hausartikel, Ware, Warengruppe
from .services import AKTUALISIEREN, WARENGRUPPE, als_daten, ausfuehren, planen
from .test_import_kept_unit import SPALTEN
from .ware_merge import zusammenfuehren
from .ware_unit_change import einheit_wechseln


def einheit(kennung: str) -> Basiseinheit:
    return Basiseinheit.objects.get(kennung=kennung)


class ErgebnisNimmtDenWegUeberDenArtikel(AngemeldeteSeiten):
    """Kino Nord closed an inventory. Its article then moves to a product of another dimension."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        bereich = Bereich.objects.create(name="Lager", haus=cls.haus)
        cls.ort = Ort.objects.create(name="Trockenlager", bereich=bereich)

    def setUp(self):
        super().setUp()
        self.lose = Ware.objects.create(name="Ware G", basiseinheit=einheit("GRAMM"))
        self.beutel = Gebinde.objects.create(
            ware=self.lose, name="Beutel", faktor_in_basiseinheit=Decimal("500")
        )
        self.stueckware = Ware.objects.create(name="Ware S", basiseinheit=einheit("STUECK"))
        self.karton = Gebinde.objects.create(
            ware=self.stueckware, name="Karton", faktor_in_basiseinheit=Decimal("4")
        )
        self.fluessig = Ware.objects.create(name="Ware L", basiseinheit=einheit("LITER"))

    def abschliessen(self, ware: Ware, gebinde: Gebinde, **artikel) -> None:
        """One pack counted and the inventory closed. The article counts as 'artikel' says."""
        self.artikel = Hausartikel.objects.create(
            haus=self.haus,
            ware=ware,
            warengruppe=self.gruppe,
            artikelnummer="101201",
            name="Artikel C",
            **artikel,
        )
        self.inventur = inventur_anlegen(self.haus, stichtag=date(2026, 1, 31))
        self.inventur.zaehlpositionen.create(
            ort=self.ort, artikel=self.artikel, gebinde=gebinde, eingabemenge=Decimal("1")
        )
        self.inventur.zaehlung_starten()
        self.inventur.abschliessen()

    def aendern(self, **felder) -> None:
        for name, wert in felder.items():
            setattr(self.artikel, name, wert)
        self.artikel.full_clean()
        self.artikel.save()

    def zeilen(self, ware: Ware) -> dict[str, Warenzeile]:
        """The product's row in the report over inventories and in the run."""
        bloecke, _ = familien_ueber_inventuren([self.inventur])
        (auswertung,) = [z for b in bloecke for z in b.alle_zeilen if z.ware == ware]
        bloecke = familien_eines_laufs([self.inventur])
        (lauf,) = [z for b in bloecke for z in b.alle_zeilen if z.ware == ware]
        return {"Auswertung": auswertung, "Lauf": lauf}

    def ergebnis(self) -> tuple:
        ergebnis = self.inventur.ergebnisse.get()
        return (ergebnis.einheit, ergebnis.gesamtmenge_basiseinheit, ergebnis.faktor_zur_ware)

    def test_500_g_in_stueck_zu_250_g_sind_2_stueck(self):
        """Counted in pieces of 250 g since closing, the article moves to a product in pieces."""
        self.abschliessen(self.lose, self.beutel)
        self.aendern(basiseinheit=einheit("STUECK"), faktor_zur_ware=Decimal("250"))

        self.aendern(ware=self.stueckware, faktor_zur_ware=None)

        # Unit and quantity stay as frozen. Only the way into the product is new.
        self.assertEqual(self.ergebnis(), (einheit("GRAMM"), Decimal("500"), Decimal("0.004")))
        for bericht, zeile in self.zeilen(self.stueckware).items():
            with self.subTest(bericht):
                self.assertEqual((zeile.ist, zeile.ohne_umrechnung), (Decimal("2"), 0))
                self.assertEqual((zeile.ist_text, zeile.ohne_umrechnung_text), ("2 Stk.", ""))
        self.assertNotContains(
            self.client.get(reverse("auswertung")), "lässt sich nicht in Stk. umrechnen"
        )

    def test_die_einheit_des_artikels_wechselt_im_selben_schritt_in_ihrer_dimension(self):
        """Counted in kg at 4 pieces per kg since, the article moves to litres and counts in g.

        A kilogram of the article is a thousand of its grams. The 4 pieces
        were 1 kg, and at 1.2 l per kg that is 1.2 l.
        """
        self.abschliessen(self.stueckware, self.karton)
        self.aendern(basiseinheit=einheit("KILOGRAMM"), faktor_zur_ware=Decimal("4"))

        self.aendern(
            ware=self.fluessig, basiseinheit=einheit("GRAMM"), faktor_zur_ware=Decimal("0.0012")
        )

        self.assertEqual(self.ergebnis(), (einheit("STUECK"), Decimal("4"), Decimal("0.3")))
        for bericht, zeile in self.zeilen(self.fluessig).items():
            with self.subTest(bericht):
                self.assertEqual((zeile.ist, zeile.ohne_umrechnung), (Decimal("1.2"), 0))

    def test_ein_ergebnis_ohne_eigenen_weg_in_die_alte_ware_bleibt_wie_es_ist(self):
        """500 g reach a product in pieces only through the article's kg. In litres, too.

        Such a result had no factor of its own into the old product, so
        there is none to carry on. The unit of the article keeps leading it.
        """
        self.abschliessen(self.lose, self.beutel)
        self.aendern(basiseinheit=einheit("KILOGRAMM"), faktor_zur_ware=None)
        self.aendern(ware=self.stueckware, faktor_zur_ware=Decimal("4"))

        self.aendern(ware=self.fluessig, faktor_zur_ware=Decimal("1.2"))

        self.assertEqual(self.ergebnis(), (einheit("GRAMM"), Decimal("500"), None))
        for bericht, zeile in self.zeilen(self.fluessig).items():
            with self.subTest(bericht):
                self.assertEqual((zeile.ist, zeile.ohne_umrechnung), (Decimal("0.6"), 0))

    def test_1_kg_zu_4_stueck_je_kg_sind_1_2_l(self):
        """Frozen at 4 pieces per kg, the article then counts in g at 1.2 l per kg: 1.2 l, not 4.

        The result's own factor led into the old product in pieces. Until
        10/2026 it stayed as it was and read as 4 l per kg.
        """
        self.abschliessen(
            self.stueckware,
            self.karton,
            basiseinheit=einheit("KILOGRAMM"),
            faktor_zur_ware=Decimal("4"),
        )

        self.aendern(
            ware=self.fluessig, basiseinheit=einheit("GRAMM"), faktor_zur_ware=Decimal("0.0012")
        )

        self.assertEqual(self.ergebnis(), (einheit("KILOGRAMM"), Decimal("1"), Decimal("1.2")))
        for bericht, zeile in self.zeilen(self.fluessig).items():
            with self.subTest(bericht):
                self.assertEqual((zeile.ist, zeile.ohne_umrechnung), (Decimal("1.2"), 0))

    def test_ein_ergebnis_aus_einer_frueheren_einheit_zieht_mit(self):
        """2 pieces at 250 g, the article in kg since, then in litres at 1.2 l per kg: 0.6 l.

        The article keeps its unit. Until 10/2026 the pieces kept their 250 g
        and read as 500 l.
        """
        self.abschliessen(
            self.lose,
            self.beutel,
            basiseinheit=einheit("STUECK"),
            faktor_zur_ware=Decimal("250"),
        )
        self.aendern(basiseinheit=einheit("KILOGRAMM"), faktor_zur_ware=None)

        self.aendern(ware=self.fluessig, faktor_zur_ware=Decimal("1.2"))

        self.assertEqual(self.ergebnis(), (einheit("STUECK"), Decimal("2"), Decimal("0.3")))
        for bericht, zeile in self.zeilen(self.fluessig).items():
            with self.subTest(bericht):
                self.assertEqual((zeile.ist, zeile.ohne_umrechnung), (Decimal("0.6"), 0))

    def test_ohne_bruecke_geht_der_eigene_faktor_und_die_zeile_sagt_es(self):
        """The article leaves its dimension in the same step: rather no way than 4 l per kg.

        Counted in kg at 4 pieces per kg, the article moves to litres and
        counts in pieces at 0.3 l each. Nothing connects a kilogram of the old
        product with the new one. Until 10/2026 the frozen factor stayed and
        read as 4 l per kg.
        """
        self.abschliessen(
            self.stueckware,
            self.karton,
            basiseinheit=einheit("KILOGRAMM"),
            faktor_zur_ware=Decimal("4"),
        )

        self.aendern(
            ware=self.fluessig, basiseinheit=einheit("STUECK"), faktor_zur_ware=Decimal("0.3")
        )

        self.assertEqual(self.ergebnis(), (einheit("KILOGRAMM"), Decimal("1"), None))
        hinweis = "Ein Ergebnis lässt sich nicht in l umrechnen und fehlt hier."
        for bericht, zeile in self.zeilen(self.fluessig).items():
            with self.subTest(bericht):
                self.assertEqual((zeile.ist, zeile.ohne_umrechnung), (None, 1))
                self.assertEqual(zeile.ohne_umrechnung_text, hinweis)


class FaktorUeberDerSpalte(AngemeldeteSeiten):
    """A pallet of 1000 kg was counted as one piece. Carried in g, that is a million g.

    The column holds six places before the point. Until 10/2026 PostgreSQL
    refused such a change with a numeric overflow, an error page, and SQLite
    stored a number it could not read back. Now the change is refused with a
    message that names the result, and nothing is written.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        bereich = Bereich.objects.create(name="Lager", haus=cls.haus)
        cls.ort = Ort.objects.create(name="Trockenlager", bereich=bereich)

    def setUp(self):
        super().setUp()
        self.kilo = Ware.objects.create(name="Ware K", basiseinheit=einheit("KILOGRAMM"))
        palette = Gebinde.objects.create(
            ware=self.kilo, name="Palette", faktor_in_basiseinheit=Decimal("1000")
        )
        self.artikel = Hausartikel.objects.create(
            haus=self.haus,
            ware=self.kilo,
            warengruppe=self.gruppe,
            artikelnummer="101301",
            name="Ware K",
            basiseinheit=einheit("STUECK"),
            faktor_zur_ware=Decimal("1000"),
        )
        self.inventur = inventur_anlegen(self.haus, stichtag=date(2026, 1, 31))
        self.inventur.zaehlpositionen.create(
            ort=self.ort, artikel=self.artikel, gebinde=palette, eingabemenge=Decimal("1")
        )
        self.inventur.zaehlung_starten()
        self.inventur.abschliessen()
        # The house counts in kg since. The pallet keeps its factor.
        self.artikel.basiseinheit, self.artikel.faktor_zur_ware = einheit("KILOGRAMM"), None
        self.artikel.save()
        self.gramm = Ware.objects.create(name="Ware G", basiseinheit=einheit("GRAMM"))

    def meldung(self, ware: str) -> str:
        return (
            f"Im Ergebnis von „Ware K“ der Inventur {self.inventur} wäre 1 Stk. danach "
            f"1000000 {ware}. Eine Umrechnung fasst höchstens 999999,999999999999."
        )

    def unveraendert(self) -> None:
        """Nothing written: the article on its product, the pallet with its factor."""
        self.assertEqual(Hausartikel.objects.get(pk=self.artikel.pk).ware_id, self.kilo.pk)
        ergebnis = self.inventur.ergebnisse.get()
        self.assertEqual(
            (ergebnis.einheit, ergebnis.gesamtmenge_basiseinheit, ergebnis.faktor_zur_ware),
            (einheit("STUECK"), Decimal("1"), Decimal("1000")),
        )

    def test_das_formular_weist_den_wechsel_der_ware_ab(self):
        antwort = self.client.post(
            reverse("hausartikel_edit", args=[self.artikel.pk]),
            {
                "artikelnummer": "101301",
                "name": "Ware K",
                "ean": "",
                "ware": self.gramm.pk,
                "neuer_ware": "",
                "basiseinheit": einheit("KILOGRAMM").pk,
                "faktor_zur_ware": "",
                "inventurart": monatsinventur().pk,
                "warengruppe": self.gruppe.pk,
                "neue_warengruppe": "",
                "gebinde-TOTAL_FORMS": "0",
                "gebinde-INITIAL_FORMS": "0",
                "gebinde-MIN_NUM_FORMS": "0",
                "gebinde-MAX_NUM_FORMS": "1000",
                "orte-TOTAL_FORMS": "0",
                "orte-INITIAL_FORMS": "0",
                "orte-MIN_NUM_FORMS": "0",
                "orte-MAX_NUM_FORMS": "1000",
            },
        )

        self.assertEqual(antwort.status_code, 200)
        self.assertFormError(antwort.context["form"], "ware", self.meldung("g"))
        self.unveraendert()

    def test_eine_neue_ware_im_formular_ebenso(self):
        """The new product arises only when saving: the form's check cannot see it.

        Created in g with the article's new unit, the pallet would be a million
        g. Until 10/2026 the refusal of 'save()' ended on the error page.
        """
        antwort = self.client.post(
            reverse("hausartikel_edit", args=[self.artikel.pk]),
            {
                "artikelnummer": "101301",
                "name": "Ware K",
                "ean": "",
                "ware": "",
                "neuer_ware": "Ware K in Gramm",
                "basiseinheit": einheit("GRAMM").pk,
                "faktor_zur_ware": "",
                "inventurart": monatsinventur().pk,
                "warengruppe": self.gruppe.pk,
                "neue_warengruppe": "",
                "gebinde-TOTAL_FORMS": "0",
                "gebinde-INITIAL_FORMS": "0",
                "gebinde-MIN_NUM_FORMS": "0",
                "gebinde-MAX_NUM_FORMS": "1000",
                "orte-TOTAL_FORMS": "0",
                "orte-INITIAL_FORMS": "0",
                "orte-MIN_NUM_FORMS": "0",
                "orte-MAX_NUM_FORMS": "1000",
            },
        )

        self.assertEqual(antwort.status_code, 200)
        self.assertFormError(antwort.context["form"], "ware", self.meldung("g"))
        self.assertFalse(Ware.objects.filter(name="Ware K in Gramm").exists())
        self.unveraendert()

    def test_am_formular_vorbei_ebenso(self):
        self.artikel.ware = self.gramm

        with self.assertRaises(ValidationError) as fang:
            self.artikel.save()

        self.assertEqual(fang.exception.message_dict, {"ware": [self.meldung("g")]})
        self.unveraendert()

    def test_ueber_die_bruecke_des_artikels_ebenso(self):
        """The house counts in g, the product moves to ml at 1 ml per g: a million ml."""
        self.artikel.basiseinheit = einheit("GRAMM")
        self.artikel.save()
        milli = Ware.objects.create(name="Ware M", basiseinheit=einheit("MILLILITER"))
        self.artikel.ware, self.artikel.faktor_zur_ware = milli, Decimal("1")

        with self.assertRaises(ValidationError) as fang:
            self.artikel.full_clean()
        self.assertEqual(fang.exception.message_dict["ware"], [self.meldung("ml")])
        with self.assertRaises(ValidationError):
            self.artikel.save()

        self.unveraendert()

    def test_der_import_meldet_es_an_seiner_zeile(self):
        """An import that moves the article to the product in g: a message for its row.

        Until 10/2026 the check of the model stopped the whole import with an
        error page, the other rows taken back with it.
        """
        tabelle = pd.DataFrame(
            [["101301", "Ware K", "Warengruppe H", "", 0, "KILOGRAMM", "Kino Nord"]],
            columns=SPALTEN,
        )
        (satz,), _meldungen = planen(tabelle, self.haus)

        bericht = ausfuehren(
            [
                als_daten(satz)
                | {"vorgehen": AKTUALISIEREN, "uebernehmen": [WARENGRUPPE], "ware": self.gramm}
            ],
            self.haus,
        )

        self.assertEqual(bericht.artikel_geaendert, [])
        self.assertEqual(bericht.fehler, [f"101301: {self.meldung('g')}"])
        self.unveraendert()

    def test_der_wechsel_der_einheit_und_das_zusammenfuehren_der_ware_ebenso(self):
        """Both convert the results with the product's factor, here a thousand g per kg."""
        for name, wechsel in (
            ("Einheit", lambda: einheit_wechseln(self.kilo, einheit("GRAMM"))),
            (
                "Zusammenführen",
                lambda: zusammenfuehren(self.kilo, self.gramm, faktor=Decimal("1000")),
            ),
        ):
            with self.subTest(name):
                with self.assertRaises(ValidationError) as fang:
                    wechsel()

                self.assertEqual(fang.exception.messages, [self.meldung("g")])
                self.unveraendert()
                self.kilo.refresh_from_db()
                self.assertEqual(self.kilo.basiseinheit, einheit("KILOGRAMM"))


class FaktorJenseitsDerRechengenauigkeit(TestCase):
    """Factors at both ends of what the column takes: a piece of 1e-12 g, then of 999999 ml.

    The way of the result into the new product becomes about 1e21 ml per kg.
    Rounded to twelve places that needs 34 digits, and Decimal computes with
    28. Until 10/2026 it raised 'InvalidOperation', an error page, before the
    check could say that the column does not hold the factor.
    """

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        bereich = Bereich.objects.create(name="Lager", haus=cls.haus)
        cls.ort = Ort.objects.create(name="Trockenlager", bereich=bereich)

    def setUp(self):
        self.lose = Ware.objects.create(name="Ware G", basiseinheit=einheit("GRAMM"))
        beutel = Gebinde.objects.create(
            ware=self.lose, name="Beutel", faktor_in_basiseinheit=Decimal("500")
        )
        self.artikel = Hausartikel.objects.create(
            haus=self.haus,
            ware=self.lose,
            warengruppe=self.gruppe,
            artikelnummer="101401",
            name="Artikel C",
            basiseinheit=einheit("KILOGRAMM"),
        )
        self.inventur = inventur_anlegen(self.haus, stichtag=date(2026, 1, 31))
        self.inventur.zaehlpositionen.create(
            ort=self.ort, artikel=self.artikel, gebinde=beutel, eingabemenge=Decimal("1")
        )
        self.inventur.zaehlung_starten()
        self.inventur.abschliessen()
        # Counted in pieces since, each the smallest amount the column takes.
        self.artikel.basiseinheit = einheit("STUECK")
        self.artikel.faktor_zur_ware = FAKTOR_MINDESTENS
        self.artikel.full_clean()
        self.artikel.save()
        self.milli = Ware.objects.create(name="Ware M", basiseinheit=einheit("MILLILITER"))

    def meldung(self, menge: str, einheit: str) -> str:
        return (
            f"Im Ergebnis von „Artikel C“ der Inventur {self.inventur} wäre 1 kg danach "
            f"{menge} {einheit}. Eine Umrechnung fasst höchstens 999999,999999999999."
        )

    def unveraendert(self) -> None:
        artikel = Hausartikel.objects.get(pk=self.artikel.pk)
        self.assertEqual(
            (artikel.ware_id, artikel.faktor_zur_ware), (self.lose.pk, FAKTOR_MINDESTENS)
        )
        ergebnis = self.inventur.ergebnisse.get()
        self.assertEqual((ergebnis.einheit, ergebnis.faktor_zur_ware), (einheit("KILOGRAMM"), None))

    def test_die_pruefung_nennt_das_ergebnis(self):
        self.artikel.ware, self.artikel.faktor_zur_ware = self.milli, Decimal("999999")

        with self.assertRaises(ValidationError) as fang:
            self.artikel.full_clean()

        self.assertEqual(
            fang.exception.message_dict["ware"], [self.meldung("999999000000000000000", "ml")]
        )
        self.unveraendert()

    def test_am_formular_vorbei_ebenso(self):
        self.artikel.ware, self.artikel.faktor_zur_ware = self.milli, Decimal("999999")

        with self.assertRaises(ValidationError) as fang:
            self.artikel.save()

        self.assertEqual(
            fang.exception.message_dict, {"ware": [self.meldung("999999000000000000000", "ml")]}
        )
        self.unveraendert()

    def test_der_faktor_aus_dem_wechsel_der_ware_ebenso(self):
        """The product carries litres now, and its factor reaches every result.

        No caller hands over such a factor today: 1e15 l per g. The column
        refuses it all the same, with the message and before anything is written.
        """
        Ware.objects.filter(pk=self.lose.pk).update(basiseinheit=einheit("LITER"))
        self.artikel.refresh_from_db()

        with self.assertRaises(ValidationError) as fang:
            self.artikel.ergebnisse_nachziehen(einheit("GRAMM"), Decimal("1e15"))

        self.assertEqual(
            fang.exception.message_dict, {"ware": [self.meldung("1000000000000000000", "l")]}
        )
        ergebnis = self.inventur.ergebnisse.get()
        self.assertEqual((ergebnis.einheit, ergebnis.faktor_zur_ware), (einheit("KILOGRAMM"), None))
