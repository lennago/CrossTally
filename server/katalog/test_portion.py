"""The portion: a unit only a family sums in, and the direction of a factor.

A portion holds pieces, a piece holds millilitres and grams, a litre weighs
grams. Against a higher dimension a factor is entered the other way round
("kg je Stk.", "g je Portion", "g je l"), stored is always the reference
quantity per unit of the product.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from django_project.form_fields import modellwahl
from django_project.testing import AngemeldeteSeiten
from organisation.models import Haus

from .forms import WareForm
from .forms.hausartikel import HausartikelForm
from .models import (
    Basiseinheit,
    Dimension,
    Hausartikel,
    Ware,
    Warenfamilie,
    Warengruppe,
    eingabe_zu_faktor,
    faktor_beschriftung,
    faktor_zu_eingabe,
    kehrwert,
)
from .warenfamilie_split_merge import aufteilen


class Richtung(SimpleTestCase):
    def test_gegen_eine_hoehere_dimension_dreht_sich_die_eingabe(self):
        self.assertFalse(kehrwert(Dimension.STUECK, Dimension.MASSE))
        self.assertFalse(kehrwert(Dimension.STUECK, Dimension.VOLUMEN))
        self.assertFalse(kehrwert(Dimension.VOLUMEN, Dimension.MASSE))
        self.assertTrue(kehrwert(Dimension.MASSE, Dimension.STUECK))
        self.assertTrue(kehrwert(Dimension.MASSE, Dimension.VOLUMEN))
        self.assertTrue(kehrwert(Dimension.VOLUMEN, Dimension.STUECK))
        for eigene in (Dimension.STUECK, Dimension.MASSE, Dimension.VOLUMEN):
            self.assertTrue(kehrwert(eigene, Dimension.PORTION))

    def test_beschriftung(self):
        self.assertEqual(
            faktor_beschriftung(Dimension.STUECK, Dimension.MASSE, "Stk."), "g je Stk."
        )
        self.assertEqual(faktor_beschriftung(Dimension.MASSE, Dimension.STUECK, "kg"), "kg je Stk.")
        self.assertEqual(faktor_beschriftung(Dimension.MASSE, Dimension.VOLUMEN, "g"), "g je l")
        self.assertEqual(faktor_beschriftung(Dimension.VOLUMEN, Dimension.MASSE, "l"), "g je l")
        self.assertEqual(
            faktor_beschriftung(Dimension.MASSE, Dimension.PORTION, "g"), "g je Portion"
        )
        self.assertEqual(
            faktor_beschriftung(Dimension.STUECK, Dimension.PORTION, "Stk."), "Stk. je Portion"
        )

    def test_hin_und_zurueck(self):
        """Entered "100 g je Portion", stored are the portions per gram."""
        self.assertEqual(
            eingabe_zu_faktor(Dimension.MASSE, Dimension.PORTION, Decimal("100")), Decimal("0.01")
        )
        self.assertEqual(
            faktor_zu_eingabe(Dimension.MASSE, Dimension.PORTION, Decimal("0.01")), Decimal("100")
        )
        # "0,125 kg je Stk.": eight pieces per kilogram.
        self.assertEqual(
            eingabe_zu_faktor(Dimension.MASSE, Dimension.STUECK, Decimal("0.125")), Decimal("8")
        )
        # "1030 g je l" for a product in grams: millilitres per gram.
        gespeichert = eingabe_zu_faktor(Dimension.MASSE, Dimension.VOLUMEN, Decimal("1030"))
        self.assertEqual(gespeichert.quantize(Decimal("0.000001")), Decimal("0.970874"))
        self.assertEqual(
            faktor_zu_eingabe(Dimension.MASSE, Dimension.VOLUMEN, Decimal("0.970874")),
            Decimal("1030"),
        )
        # The stored way stays untouched where nothing turns.
        self.assertEqual(
            eingabe_zu_faktor(Dimension.STUECK, Dimension.VOLUMEN, Decimal("500")), Decimal("500")
        )

    def test_zwoelf_stellen_bringen_die_eingabe_zurueck(self):
        """Entered "750 ml je Stk.": 0.001333333333 pieces per ml in the column, and 750 again."""
        gespeichert = eingabe_zu_faktor(
            Dimension.VOLUMEN, Dimension.STUECK, Decimal("750")
        ).quantize(Decimal("0.000000000001"))
        self.assertEqual(gespeichert, Decimal("0.001333333333"))
        self.assertEqual(
            str(faktor_zu_eingabe(Dimension.VOLUMEN, Dimension.STUECK, gespeichert)), "750"
        )
        # Where nothing turns, only the trailing zeros of the column go.
        for spalte, gezeigt in (("500.000000000000", "500"), ("0.125000000000", "0.125")):
            with self.subTest(spalte=spalte):
                self.assertEqual(
                    str(faktor_zu_eingabe(Dimension.STUECK, Dimension.MASSE, Decimal(spalte))),
                    gezeigt,
                )


class Portion(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.stueck = Basiseinheit.objects.get(name="Stück(e)")
        cls.gramm = Basiseinheit.objects.get(name="g")
        cls.kilo = Basiseinheit.objects.get(name="kg")
        cls.portion = Basiseinheit.objects.get(kennung="PORTION")

    def test_kommt_aus_der_migration_und_gehoert_den_familien(self):
        self.assertEqual(self.portion.dimension, Dimension.PORTION)
        self.assertTrue(self.portion.nur_fuer_familien)
        self.assertNotIn(self.portion, Basiseinheit.fuer_artikel())
        self.assertEqual(Basiseinheit.fuer_artikel().count(), 5)
        self.assertEqual(self.portion.kurz, "Portion")

    def test_keine_ware_und_kein_artikel_in_portionen(self):
        ware = Ware(name="Ware P", basiseinheit=self.portion)
        with self.assertRaises(ValidationError) as fehler:
            ware.full_clean()
        self.assertIn("basiseinheit", fehler.exception.message_dict)

        riegel = Ware.objects.create(name="Ware R", basiseinheit=self.stueck)
        artikel = Hausartikel(
            haus=Haus.objects.create(name="Kino Nord"),
            ware=riegel,
            warengruppe=Warengruppe.objects.create(name="Warengruppe S"),
            artikelnummer="101001",
            name="Ware R",
            basiseinheit=self.portion,
        )
        with self.assertRaises(ValidationError) as fehler:
            artikel.full_clean()
        self.assertIn("basiseinheit", fehler.exception.message_dict)

    def test_die_formulare_bieten_sie_nicht_an(self):
        for form in (WareForm(), HausartikelForm(haus=Haus.objects.create(name="Kino Ost"))):
            einheiten = modellwahl(form, "basiseinheit").queryset
            assert einheiten is not None
            self.assertNotIn(self.portion, einheiten)

    def test_familie_in_portionen_summiert_riegel_und_nuesse(self):
        """One bar is one portion, a hundred grams of nuts are one: the family adds them."""
        snacks = Warenfamilie.objects.create(name="Warenfamilie S", leiteinheit=self.portion)
        riegel = Ware.objects.create(name="Ware R", basiseinheit=self.stueck)
        riegel.umrechnungen_setzen({Dimension.PORTION: Decimal("1")})
        nuesse = Ware.objects.create(name="Ware N", basiseinheit=self.gramm)
        nuesse.umrechnungen_setzen(
            {
                Dimension.PORTION: eingabe_zu_faktor(
                    Dimension.MASSE, Dimension.PORTION, Decimal("100")
                )
            }
        )
        for ware in (riegel, nuesse):
            ware.warenfamilie = snacks
            ware.full_clean()
            ware.save()
        self.assertEqual(snacks.waren_ohne_faktor(), [])
        self.assertEqual(riegel.faktor_nach(Dimension.PORTION), Decimal("1"))
        self.assertEqual(nuesse.faktor_nach(Dimension.PORTION) * 300, Decimal("3"))
        self.assertEqual(nuesse.umrechnungen.get().anzeigetext, "1 Portion = 100 g")
        self.assertEqual(riegel.umrechnungen.get().anzeigetext, "1 Portion = 1 Stk.")

    def test_ohne_faktor_kommt_die_ware_nicht_in_die_familie(self):
        snacks = Warenfamilie.objects.create(name="Warenfamilie S", leiteinheit=self.portion)
        riegel = Ware(name="Ware R", basiseinheit=self.stueck, warenfamilie=snacks)
        with self.assertRaises(ValidationError) as fehler:
            riegel.full_clean()
        self.assertIn("Stk. je Portion", str(fehler.exception))

    def test_aufteilen_behaelt_die_portionen(self):
        snacks = Warenfamilie.objects.create(name="Warenfamilie S", leiteinheit=self.portion)
        riegel = Ware.objects.create(name="Ware R", basiseinheit=self.stueck, warenfamilie=snacks)
        riegel.umrechnungen_setzen({Dimension.PORTION: Decimal("1")})
        Ware.objects.create(name="Ware K", basiseinheit=self.stueck, warenfamilie=snacks)
        bericht = aufteilen(snacks, "Warenfamilie S", "Warenfamilie T", [riegel])
        self.assertEqual(bericht.bleibt.leiteinheit, self.portion)
        self.assertEqual(bericht.neu.leiteinheit, self.portion)


class Seiten(AngemeldeteSeiten):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.stueck = Basiseinheit.objects.get(name="Stück(e)")
        cls.kilo = Basiseinheit.objects.get(name="kg")
        cls.portion = Basiseinheit.objects.get(kennung="PORTION")

    def test_warenformular_zeigt_und_speichert_den_kehrwert(self):
        """Entered is "0,125 kg je Stk." and "0,05 kg je Portion", stored the other way round."""
        antwort = self.client.post(
            reverse("ware_add"),
            {
                "name": "Ware N",
                "basiseinheit": self.kilo.pk,
                "inventurart": "",
                "faktor_STUECK": "0,125",
                "faktor_PORTION": "0,05",
                "gebinde-TOTAL_FORMS": "0",
                "gebinde-INITIAL_FORMS": "0",
                "gebinde-MIN_NUM_FORMS": "0",
                "gebinde-MAX_NUM_FORMS": "1000",
            },
        )
        ware = Ware.objects.get(name="Ware N")
        self.assertRedirects(antwort, ware.get_absolute_url())
        self.assertEqual(
            ware.faktoren(), {Dimension.STUECK: Decimal("8"), Dimension.PORTION: Decimal("20")}
        )
        form = WareForm(instance=ware)
        self.assertEqual(form.fields["faktor_STUECK"].initial, Decimal("0.125"))
        self.assertEqual(form.fields["faktor_PORTION"].initial, Decimal("0.05"))
        self.assertContains(self.client.get(ware.get_absolute_url()), "1 Stk. = 0,125 kg")
        self.assertContains(self.client.get(ware.get_absolute_url()), "1 Portion = 0,05 kg")

    def test_familienformular_bietet_die_portion_immer_an(self):
        riegel = Ware.objects.create(name="Ware R", basiseinheit=self.stueck)
        antwort = self.client.post(
            reverse("warenfamilie_add"),
            {
                "name": "Warenfamilie S",
                "beschreibung": "",
                "leiteinheit": self.portion.pk,
                "uebergeordnet": "",
                "ware": [riegel.pk],
            },
        )
        # The bar lacks what one portion is. The form asks.
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, f'name="faktor_{riegel.pk}_PORTION"')
        self.assertContains(antwort, "„Ware R“: Stk. je Portion")

        antwort = self.client.post(
            reverse("warenfamilie_add"),
            {
                "name": "Warenfamilie S",
                "beschreibung": "",
                "leiteinheit": self.portion.pk,
                "uebergeordnet": "",
                "ware": [riegel.pk],
                f"faktor_{riegel.pk}_PORTION": "2",
            },
        )
        familie = Warenfamilie.objects.get(name="Warenfamilie S")
        self.assertRedirects(antwort, familie.get_absolute_url())
        self.assertEqual(familie.leiteinheit, self.portion)
        riegel.refresh_from_db()
        # Two pieces per portion: half a portion per piece.
        self.assertEqual(riegel.faktoren(), {Dimension.PORTION: Decimal("0.5")})
