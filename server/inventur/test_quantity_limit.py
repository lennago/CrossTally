"""What a single row may carry: no more than a result holds.

The result keeps twelve digits, three of them after the point
('HOECHSTMENGE'). A count in packs was refused at input already. Weighings
and portions reached the result only through the closing, and that is where
a typo surfaced, with the manager instead of with whoever typed it (until
10/2026). Rows of the same article at other places add up only at closing,
and the closing still checks their sum ('test_closing.MehrAlsEinErgebnisFasst').
"""

from decimal import Decimal

from django.core.exceptions import ValidationError

from katalog.models import Behaelter, Hausartikel, Rezepturartikel, Rezepturkomponente

from .models import Wiegung, Zaehlposition
from .test_models import InventurGrundlagen, einheit

#: Ten million portions of 140 g each.
PORTIONEN_ZU_VIEL = (
    "Für „Ware N“ wären das 1400000000 g, mehr als ein Ergebnis fasst. Bitte die Zahl prüfen."
)


def zu_viel(menge: str) -> str:
    return f"Das wären {menge}, mehr als ein Ergebnis fasst. Bitte die Zahl prüfen."


class WasEinErgebnisFasst(InventurGrundlagen):
    def setUp(self):
        self.inv = self.inventur()
        self.nachos = self.ware("Ware N", "g", abwiegbar=True)
        self.artikel_n = self.artikel(self.nachos)
        self.schale = Behaelter.objects.create(name="Schale", tara=Decimal("250"))
        self.nachos.behaelter.add(self.schale)
        self.portion = Rezepturartikel.objects.create(
            name="Portion N", basiseinheit=einheit("Stück(e)")
        )
        Rezepturkomponente.objects.create(
            rezepturartikel=self.portion, ware=self.nachos, menge_in_basiseinheit=Decimal("140")
        )

    def fehler(self, objekt) -> dict:
        with self.assertRaises(ValidationError) as fehler:
            objekt.full_clean()
        return fehler.exception.message_dict

    def portionen(self, menge: str) -> Zaehlposition:
        return Zaehlposition(
            inventur=self.inv,
            ort=self.theke,
            artikel_id=self.portion.pk,
            eingabemenge=Decimal(menge),
        )

    def waage(self, artikel=None) -> Zaehlposition:
        return Zaehlposition.objects.create(
            inventur=self.inv, ort=self.theke, artikel=artikel or self.artikel_n
        )

    def schale_mit(self, position, brutto: str, behaelter=None) -> Wiegung:
        return Wiegung(
            zaehlposition=position, brutto_gramm=Decimal(brutto), behaelter=behaelter or self.schale
        )

    # -- Portions ----------------------------------------------------------

    def test_eine_portion_reicht_ueber_ihre_zutat_ins_ergebnis(self):
        self.assertEqual(
            self.fehler(self.portionen("10000000")), {"eingabemenge": [PORTIONEN_ZU_VIEL]}
        )
        self.portionen("7000000").full_clean()  # 980 million grams still fit

    def test_es_zaehlt_die_einheit_des_hauses(self):
        """1.4 billion grams are 1.4 million kilograms, and the house counts in kilograms."""
        Hausartikel.objects.filter(pk=self.artikel_n.pk).update(basiseinheit=einheit("kg"))
        self.portionen("10000000").full_clean()

    def test_eine_zutat_ohne_artikel_im_haus_verfaellt_auch_hier(self):
        """At closing its share lands nowhere, so there is nothing that would not fit."""
        Rezepturkomponente.objects.create(
            rezepturartikel=self.portion,
            ware=self.ware("Ware R", "g"),
            menge_in_basiseinheit=Decimal("1000"),
        )
        self.portionen("5000000").full_clean()  # five billion grams of a product nobody carries

    # -- Weighings ---------------------------------------------------------

    def test_die_schalen_einer_zeile_zaehlen_zusammen(self):
        """One bowl holds less than a billion grams, two of them may not."""
        position = self.waage()
        self.schale_mit(position, "600000250").save()
        zweite = self.schale_mit(position, "400000250")
        self.assertEqual(self.fehler(zweite), {"brutto_gramm": [zu_viel("1000000000 g")]})

        zweite.brutto_gramm = Decimal("400000249")
        zweite.full_clean()

    def test_eine_berichtigte_schale_zaehlt_mit_ihrem_neuen_gewicht(self):
        """Not twice: its old weight goes, the new one comes."""
        position = self.waage()
        erste = self.schale_mit(position, "600000250")
        erste.save()
        self.schale_mit(position, "300000250").save()

        erste.brutto_gramm = Decimal("700000250")
        self.assertEqual(self.fehler(erste), {"brutto_gramm": [zu_viel("1000000000 g")]})
        erste.brutto_gramm = Decimal("650000250")
        erste.full_clean()

    def test_eine_schale_in_einer_kleinen_einheit_des_hauses(self):
        """The house counts pieces of one milligram: a ton on the scale is a billion of them."""
        kaffee = self.ware("Ware K", "kg", abwiegbar=True)
        kaffee.behaelter.add(self.schale)
        bohnen = self.artikel(
            kaffee, basiseinheit=einheit("Stück(e)"), faktor_zur_ware=Decimal("0.000001")
        )
        tonne = self.schale_mit(self.waage(bohnen), "1000250")
        self.assertEqual(self.fehler(tonne), {"brutto_gramm": [zu_viel("1000000000 Stk.")]})

    def test_eine_gewogene_mischung_reicht_ueber_ihre_rezeptur_ins_ergebnis(self):
        """60 % of 1.8 billion grams are more than a result holds, 40 % are not."""
        mischung = Rezepturartikel.objects.create(
            name="Mischung N", basiseinheit=einheit("g"), anteilig=True
        )
        ware_a, ware_b = self.ware("Ware A", "g"), self.ware("Ware B", "g")
        for ware, anteil in ((ware_a, "60"), (ware_b, "40")):
            self.artikel(ware)
            Rezepturkomponente.objects.create(
                rezepturartikel=mischung, ware=ware, menge_in_basiseinheit=Decimal(anteil)
            )
        position = Zaehlposition.objects.create(
            inventur=self.inv, ort=self.theke, artikel_id=mischung.pk
        )
        ohne = Behaelter.ohne_behaelter()
        self.schale_mit(position, "900000000", ohne).save()
        zweite = self.schale_mit(position, "900000000", ohne)
        self.assertEqual(
            self.fehler(zweite),
            {
                "brutto_gramm": [
                    "Für „Ware A“ wären das 1080000000 g, mehr als ein Ergebnis fasst. Bitte die "
                    "Zahl prüfen."
                ]
            },
        )
