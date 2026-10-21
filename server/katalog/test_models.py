"""The rules of the catalog model.

Two levels: the **product** applies across all houses and carries base
unit, inventory scope and packs. The **house article** is what a single
house carries of it in WinTICKET.

Tested here is what the model itself promises: conversions, uniqueness,
validity and the mechanics behind the derived properties. The interface
for it is in 'test_views'.
"""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone, translation

from django_project.testing import bereich, inventur_anlegen, jahresinventur, monatsinventur
from organisation.models import Haus, Ort
from scripts import normalize

from .models import (
    EINZELN_NAME,
    NUR_FUER_FAMILIEN,
    OHNE_WARENGRUPPE,
    Artikel,
    Artikelstatus,
    Basiseinheit,
    Behaelter,
    Dimension,
    Einheitenkennung,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Rezepturartikel,
    Rezepturkomponente,
    Ware,
    Warenfamilie,
    Warengruppe,
    WareUmrechnung,
    Zaehlumfang,
    menge_kurz,
    menge_mit_vorzeichen,
    portion_abgekuerzt,
)


def einheit(name: str) -> Basiseinheit:
    return Basiseinheit.objects.get(name=name)


def ware(name: str, einheitenname: str = "Stück(e)", umrechnungen=None, **abweichend) -> Ware:
    """A product, with 'umrechnungen' as its factors per foreign dimension."""
    satz = Ware.objects.create(name=name, basiseinheit=einheit(einheitenname), **abweichend)
    if umrechnungen:
        satz.umrechnungen_setzen(umrechnungen)
    return satz


def hausartikel(haus: Haus, ware: Ware, nummer="101001", **abweichend):
    vorgaben = {
        "name": ware.name,
        "warengruppe": Warengruppe.objects.get_or_create(name="Warengruppe G")[0],
    }
    return Hausartikel.objects.create(
        haus=haus, ware=ware, artikelnummer=nummer, **(vorgaben | abweichend)
    )


class MengeKurzTests(SimpleTestCase):
    """Quantities are in the database as Decimal with three decimal places."""

    def test_ohne_ueberfluessige_nullen_und_lesbar(self):
        """'normalize' alone would write 1000 as 1E+3."""
        for menge, kurz in (("1.000", "1"), ("0.140", "0,14"), ("1000.000", "1000")):
            with self.subTest(menge=menge):
                self.assertEqual(menge_kurz(Decimal(menge)), kurz)


class BasiseinheitTests(TestCase):
    """The four units come from the data migration."""

    def test_alle_basiseinheiten_sind_da(self):
        self.assertEqual(
            sorted(Basiseinheit.objects.values_list("name", flat=True)),
            ["Liter", "Milliliter", "Portion", "Stück(e)", "g", "kg"],
        )

    def test_jede_einheit_kennt_dimension_referenz_und_rundung(self):
        """Computed in grams or millilitres, rounded per unit."""
        faelle = (
            # Name, dimension, factor to reference, rounded from 2.4567
            ("Stück(e)", Dimension.STUECK, "1", "2"),
            ("g", Dimension.MASSE, "1", "2"),
            ("kg", Dimension.MASSE, "1000", "2.457"),
            ("Milliliter", Dimension.VOLUMEN, "1", "2"),
            ("Liter", Dimension.VOLUMEN, "1000", "2.457"),
        )
        for name, dimension, faktor, gerundet in faelle:
            with self.subTest(einheit=name):
                satz = einheit(name)
                self.assertEqual(satz.dimension, dimension)
                self.assertEqual(satz.faktor_zur_referenz, Decimal(faktor))
                self.assertEqual(satz.runden(Decimal("2.4567")), Decimal(gerundet))

    def test_name_ist_eindeutig(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Basiseinheit.objects.create(name="kg")

    def test_natuerlicher_schluessel(self):
        """So 'dumpdata --natural-primary' finds them again by name.

        The name stays the key. It is no longer displayed, but every existing
        backup addresses the unit by it.
        """
        self.assertEqual(einheit("kg").natural_key(), ("kg",))
        self.assertEqual(Basiseinheit.objects.get_by_natural_key("kg"), einheit("kg"))

    def test_jede_einheit_hat_ihre_kennung(self):
        """The id is language-independent. The import matches against it."""
        faelle = (
            ("Stück(e)", Einheitenkennung.STUECK),
            ("g", Einheitenkennung.GRAMM),
            ("kg", Einheitenkennung.KILOGRAMM),
            ("Milliliter", Einheitenkennung.MILLILITER),
            ("Liter", Einheitenkennung.LITER),
        )
        for name, kennung in faelle:
            with self.subTest(einheit=name):
                self.assertEqual(einheit(name).kennung, kennung)

    def test_kennung_ist_eindeutig(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Basiseinheit.objects.create(name="Pfund", kennung=Einheitenkennung.KILOGRAMM)

    def test_die_kurzform_folgt_der_sprache(self):
        """Shown is the short form, not the stored name."""
        faelle = (
            ("Stück(e)", "Stk.", "pcs"),
            ("g", "g", "g"),
            ("kg", "kg", "kg"),
            ("Milliliter", "ml", "ml"),
            ("Liter", "l", "l"),
        )
        for name, deutsch, englisch in faelle:
            with self.subTest(einheit=name):
                satz = einheit(name)
                with translation.override("de"):
                    self.assertEqual(satz.kurz, deutsch)
                    self.assertEqual(str(satz), deutsch)
                with translation.override("en"):
                    self.assertEqual(satz.kurz, englisch)

    def test_hinter_einer_zahl_steht_die_portion_in_der_mehrzahl(self):
        """Only exactly one portion is singular. The short forms fit behind every number."""
        portion = einheit("Portion")
        faelle = (
            # Quantity, German, English
            ("130", "130 Portionen", "130 portions"),
            ("1", "1 Portion", "1 portion"),
            ("-1", "-1 Portion", "-1 portion"),
            ("0", "0 Portionen", "0 portions"),
            ("1.5", "1,5 Portionen", "1.5 portions"),
        )
        for menge, deutsch, englisch in faelle:
            with self.subTest(menge=menge):
                with translation.override("de"):
                    self.assertEqual(portion.mit_menge(Decimal(menge)), deutsch)
                with translation.override("en"):
                    self.assertEqual(portion.mit_menge(Decimal(menge)), englisch)
        with translation.override("de"):
            self.assertEqual(portion.mit_menge(Decimal("3"), menge_mit_vorzeichen), "+3 Portionen")
            self.assertEqual(einheit("Stück(e)").mit_menge(Decimal("130")), "130 Stk.")
            self.assertEqual(einheit("kg").mit_menge(Decimal("1")), "1 kg")

    def test_auf_dem_papier_ist_die_portion_kurz(self):
        """In a PDF it stands like "Stk." behind every number, one portion or a hundred."""
        portion = einheit("Portion")
        faelle = (
            # Quantity, German, English
            ("130", "130 Port.", "130 port."),
            ("1", "1 Port.", "1 port."),
            ("1.5", "1,5 Port.", "1.5 port."),
        )
        with portion_abgekuerzt():
            for menge, deutsch, englisch in faelle:
                with self.subTest(menge=menge):
                    with translation.override("de"):
                        self.assertEqual(portion.mit_menge(Decimal(menge)), deutsch)
                    with translation.override("en"):
                        self.assertEqual(portion.mit_menge(Decimal(menge)), englisch)
            with translation.override("de"):
                self.assertEqual(portion.mit_menge(Decimal("3"), menge_mit_vorzeichen), "+3 Port.")
                self.assertEqual(einheit("Stück(e)").mit_menge(Decimal("130")), "130 Stk.")
        # After the block the screen has the whole word again.
        with translation.override("de"):
            self.assertEqual(portion.mit_menge(Decimal("130")), "130 Portionen")


class FestesGebindeTests(TestCase):
    """Count × quantity per pack."""

    @classmethod
    def setUpTestData(cls):
        cls.cola = ware("Artikel C 0,33")
        cls.kasten = Gebinde.objects.create(
            ware=cls.cola, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        cls.flasche = Gebinde.objects.create(
            ware=cls.cola, name="Flasche(n)", faktor_in_basiseinheit=Decimal("1")
        )

    def test_anzahl_mal_faktor(self):
        faelle = (
            (self.kasten, "3", "60"),
            (self.flasche, "7", "7"),
            (self.kasten, "0", "0"),
            # Freshly loaded from the database it computes the same.
            (Gebinde.objects.get(pk=self.kasten.pk), "2", "40"),
        )
        for gebinde, anzahl, erwartet in faelle:
            with self.subTest(gebinde=gebinde.name, anzahl=anzahl):
                self.assertEqual(gebinde.in_basiseinheit(Decimal(anzahl)), Decimal(erwartet))


class AbwiegenTests(TestCase):
    """Weighed gross in grams. The tare comes off.

    Weighing hangs on the product, no longer on a pack of its own.
    """

    @classmethod
    def setUpTestData(cls):
        cls.schale = Behaelter.objects.create(name="Schale", tara=Decimal("250"))
        cls.nachos_kg = ware("Artikel N kg", "kg", abwiegbar=True)
        cls.nachos_g = ware("Artikel N g", "g", abwiegbar=True)
        cls.sirup = ware("Artikel S", "Liter")

    def test_umrechnung_aus_gramm(self):
        """Grams into the product's unit, through what one unit weighs."""
        dichter_sirup = ware(
            "Artikel S dicht",
            "Liter",
            abwiegbar=True,
            umrechnungen={Dimension.MASSE: Decimal("1250")},
        )
        saft = ware(
            "Posten F",
            "Milliliter",
            abwiegbar=True,
            umrechnungen={Dimension.MASSE: Decimal("1.03")},
        )
        becher = ware(
            "Becher", "Stück(e)", abwiegbar=True, umrechnungen={Dimension.MASSE: Decimal("4.5")}
        )
        faelle = (
            # Case, product, gross in g, container, expected in the product's unit
            ("Gramm auf Kilogramm", self.nachos_kg, "2500", None, "2.5"),
            ("Gramm auf Gramm", self.nachos_g, "2500", None, "2500"),
            ("Milliliter auf Liter", self.sirup, "1500", None, "1.5"),
            ("die Tara geht ab", self.nachos_kg, "2750", self.schale, "2.5"),
            ("Waage genullt: das volle Gewicht zählt", self.nachos_kg, "2750", None, "2.75"),
            ("brutto unter Tara ergibt null", self.nachos_kg, "100", self.schale, "0"),
            ("Volumen über die Dichte: 1500 g bei 1250 g/l", dichter_sirup, "1500", None, "1.2"),
            ("Volumen in Millilitern", saft, "206", None, "200"),
            ("Stück über das Stückgewicht: 45 g bei 4,5 g", becher, "45", None, "10"),
            ("Stück mit Tara", becher, "295", self.schale, "10"),
        )
        for fall, artikel, brutto, behaelter, erwartet in faelle:
            with self.subTest(fall=fall):
                self.assertEqual(
                    artikel.in_basiseinheit(Decimal(brutto), behaelter), Decimal(erwartet)
                )

    def test_wiegefaktor_haengt_an_der_dimension(self):
        """Mass weighs itself. Volume and piece goods need the entry, named after the unit."""
        faelle = (
            (self.nachos_kg, "g je kg", False, Decimal("1000")),
            (ware("Posten F 2", "Liter"), "g je l", True, None),
            (ware("Becher 2"), "g je Stk.", True, None),
        )
        for artikel, beschriftung, braucht_faktor, gramm in faelle:
            with self.subTest(ware=artikel.name):
                self.assertEqual(artikel.faktor_beschriftung(Dimension.MASSE), beschriftung)
                self.assertEqual(artikel.braucht_wiegefaktor, braucht_faktor)
                self.assertEqual(artikel.gramm_je_einheit, gramm)

    def test_ohne_eintrag_rechnet_die_waage_eins_zu_eins(self):
        """As long as the default was 1. The toggle warns, the form insists."""
        becher = ware("Becher 3", abwiegbar=True)
        self.assertEqual(becher.in_basiseinheit(Decimal("45")), Decimal("45"))

    def test_faktor_je_dimension(self):
        """Own dimension from the unit, foreign ones from the rows, unknown ones None."""
        flasche = ware(
            "Flasche 0,5",
            umrechnungen={Dimension.VOLUMEN: Decimal("500"), Dimension.MASSE: Decimal("520")},
        )
        self.assertEqual(flasche.faktor_nach(Dimension.STUECK), Decimal("1"))
        self.assertEqual(flasche.faktor_nach(Dimension.VOLUMEN), Decimal("500"))
        self.assertEqual(flasche.gramm_je_einheit, Decimal("520"))
        flasche.umrechnungen_setzen({Dimension.VOLUMEN: Decimal("330")})
        self.assertEqual(flasche.faktoren(), {Dimension.VOLUMEN: Decimal("330")})
        self.assertIsNone(flasche.faktor_nach(Dimension.MASSE))

    def test_angesagte_faktoren_zaehlen_vor_den_gespeicherten(self):
        """A form announces what it writes. Before saving, the rows still show the old state."""
        flasche = ware("Flasche 0,3", umrechnungen={Dimension.MASSE: Decimal("330")})
        flasche.geplante_faktoren = {Dimension.VOLUMEN: Decimal("300")}
        self.assertEqual(flasche.faktoren(), {Dimension.VOLUMEN: Decimal("300")})
        self.assertIsNone(flasche.gramm_je_einheit)

    def test_waehlbare_behaelter_enthalten_immer_die_leere_waage(self):
        self.nachos_kg.behaelter.set([self.schale])
        self.assertCountEqual(
            self.nachos_kg.waehlbare_behaelter(), [self.schale, Behaelter.ohne_behaelter()]
        )


class BehaelterTests(TestCase):
    def test_ohne_behaelter_kommt_aus_der_migration(self):
        ohne = Behaelter.ohne_behaelter()
        self.assertTrue(ohne.immer_erlaubt)
        self.assertEqual(ohne.tara, Decimal("0"))

    def test_nur_einer_gilt_ueberall(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Behaelter.objects.create(name="Zweiter", tara=0, immer_erlaubt=True)

    def test_ohne_behaelter_laesst_sich_nicht_loeschen(self):
        """The backstop on the model, for every path past the delete page."""
        with self.assertRaises(ValidationError):
            Behaelter.ohne_behaelter().delete()
        self.assertTrue(Behaelter.objects.filter(immer_erlaubt=True).exists())

    def test_gesucht_wird_ueber_das_kennzeichen(self):
        """A rename must not let the search run into nothing."""
        ohne = Behaelter.ohne_behaelter()
        ohne.name = "Waage auf null"
        ohne.save()
        self.assertEqual(Behaelter.ohne_behaelter(), ohne)


class GebinderegelnTests(TestCase):
    """Per product a name once, a quantity once, one weighing pack."""

    @classmethod
    def setUpTestData(cls):
        cls.cola = ware("Artikel C 0,33")
        cls.nachos = ware("Artikel N", "kg")

    def test_name_je_ware_eindeutig(self):
        Gebinde.objects.create(ware=self.cola, name="Kasten", faktor_in_basiseinheit=Decimal("20"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Gebinde.objects.create(
                ware=self.cola, name="Kasten", faktor_in_basiseinheit=Decimal("6")
            )

    def test_gleicher_name_bei_anderer_ware_geht(self):
        Gebinde.objects.create(ware=self.cola, name="Kasten", faktor_in_basiseinheit=Decimal("20"))
        Gebinde.objects.create(
            ware=ware("Artikel F 0,33"),
            name="Kasten",
            faktor_in_basiseinheit=Decimal("20"),
        )

    def test_menge_je_ware_nur_einmal(self):
        """A "Kasten" and a "Kiste" of 20 each could not be told apart when counting."""
        Gebinde.objects.create(ware=self.cola, name="Kasten", faktor_in_basiseinheit=Decimal("20"))
        doppelt = Gebinde(ware=self.cola, name="Kiste", faktor_in_basiseinheit=Decimal("20"))
        with self.assertRaises(ValidationError) as fehler:
            doppelt.full_clean()
        self.assertIn("faktor_in_basiseinheit", fehler.exception.message_dict)

    def test_abwiegen_geht_in_jeder_einheit(self):
        """The scale delivers grams. The rest is done by the conversion into grams."""
        self.cola.umrechnungen_setzen({Dimension.MASSE: Decimal("4.5")})
        for ware in (self.cola, self.nachos):
            with self.subTest(ware=ware.name):
                ware.abwiegbar = True
                ware.full_clean()

    def test_eigene_dimension_hat_keinen_faktor(self):
        """A gram weighs a gram, anything else would be a calculation error."""
        with self.assertRaises(ValidationError) as fehler:
            WareUmrechnung(
                ware=self.nachos, dimension=Dimension.MASSE, referenzmenge_je_einheit=Decimal("2")
            ).full_clean()
        self.assertIn("dimension", fehler.exception.message_dict)
        # The writer for every path drops it silently as well.
        self.nachos.umrechnungen_setzen({Dimension.MASSE: Decimal("2")})
        self.assertEqual(self.nachos.faktoren(), {})

    def test_eine_umrechnung_je_dimension(self):
        self.cola.umrechnungen_setzen({Dimension.MASSE: Decimal("4.5")})
        with self.assertRaises(ValidationError):
            WareUmrechnung(
                ware=self.cola, dimension=Dimension.MASSE, referenzmenge_je_einheit=Decimal("5")
            ).full_clean()

    def test_ohne_ware_wird_nicht_geprueft(self):
        """In the formset the foreign key is only fixed when saving."""
        Gebinde(name="Kasten", faktor_in_basiseinheit=Decimal("20")).clean()


class KonkretTests(TestCase):
    """The descent from the base class to the concrete form."""

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.cola = ware("Artikel C 0,33")
        cls.artikel = hausartikel(cls.haus, cls.cola)
        cls.rezepturartikel = Rezepturartikel.objects.create(
            name="Portion N", basiseinheit=einheit("Stück(e)")
        )
        cls.gebinde = Gebinde.objects.create(
            ware=cls.cola, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )

    def test_artikel_findet_seine_auspraegung(self):
        kern = Artikel.objects.get(pk=self.artikel.pk)
        self.assertIsInstance(kern.konkret(), Hausartikel)
        self.assertIsInstance(
            Artikel.objects.get(pk=self.rezepturartikel.pk).konkret(), Rezepturartikel
        )

    def test_mti_teilt_sich_den_primaerschluessel(self):
        self.assertEqual(self.artikel.pk, self.artikel.artikel_ptr_id)

    def test_ist_hausartikel(self):
        self.assertTrue(Artikel.objects.get(pk=self.artikel.pk).ist_hausartikel)
        self.assertFalse(Artikel.objects.get(pk=self.rezepturartikel.pk).ist_hausartikel)

    def test_basis_ohne_auspraegung_meldet_sich(self):
        kern = Artikel.objects.create(name="Verwaister Kern")
        with self.assertRaises(NotImplementedError):
            kern.zaehlpflichtig_bei(Zaehlumfang((monatsinventur().pk,)), self.haus.pk)

    def test_abwiegbare_ware(self):
        """Only a house article whose product goes onto the scale, seen from the core too."""
        self.assertFalse(self.artikel.abwiegbare_ware)
        self.cola.abwiegbar = True
        self.cola.save()
        self.assertTrue(Hausartikel.objects.get(pk=self.artikel.pk).abwiegbare_ware)
        self.assertTrue(Artikel.objects.get(pk=self.artikel.pk).abwiegbare_ware)
        gewogen = Rezepturartikel.objects.create(name="Portion M", basiseinheit=einheit("g"))
        self.assertTrue(gewogen.wird_gewogen)
        self.assertFalse(Artikel.objects.get(pk=gewogen.pk).abwiegbare_ware)
        self.assertFalse(Artikel.objects.create(name="Verwaister Kern").abwiegbare_ware)


class HausartikelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.cola = ware("Artikel C 0,33")

    def hausartikel_mit_nummer(self, nummer: str) -> Hausartikel:
        return Hausartikel(
            haus=self.haus,
            ware=self.cola,
            warengruppe=Warengruppe.objects.get_or_create(name="Warengruppe G")[0],
            artikelnummer=nummer,
            name="Artikel C",
        )

    def test_artikelnummer_beginnt_mit_einer_ziffer(self):
        """WinTICKET has no scheme. Only a sum row must not become a number."""
        for nummer in ("", "a12345", "12 34"):
            with self.subTest(nummer=nummer), self.assertRaises(ValidationError):
                self.hausartikel_mit_nummer(nummer).full_clean()

    def test_die_nummer_bleibt_wie_sie_eingegeben_wurde(self):
        """Short, long, with suffix: that is the article's name in WinTICKET, so it is here."""
        for nummer in ("12345", "1234567", "101014-1", "12345a"):
            with self.subTest(nummer=nummer):
                satz = self.hausartikel_mit_nummer(f" {nummer} ")
                satz.full_clean()
                self.assertEqual(satz.artikelnummer, nummer)

    def test_fuehrende_nullen_bleiben(self):
        artikel = hausartikel(self.haus, self.cola, nummer="001234")
        artikel.refresh_from_db()
        self.assertEqual(artikel.artikelnummer, "001234")

    def test_ean_nimmt_nur_ziffern(self):
        artikel = hausartikel(self.haus, self.cola)
        artikel.ean = "400-123"
        with self.assertRaises(ValidationError):
            artikel.full_clean()

    def test_ein_gefuehrter_je_haus_und_nummer(self):
        hausartikel(self.haus, self.cola)
        with self.assertRaises(IntegrityError), transaction.atomic():
            hausartikel(self.haus, ware("Artikel F 0,33"))

    def test_ein_gefuehrter_je_haus_und_ware(self):
        hausartikel(self.haus, self.cola)
        with self.assertRaises(IntegrityError), transaction.atomic():
            hausartikel(self.haus, self.cola, nummer="101002")

    def test_dieselbe_nummer_in_zwei_haeusern(self):
        hausartikel(self.haus, self.cola)
        hausartikel(Haus.objects.create(name="Kino Süd"), self.cola)
        self.assertEqual(Hausartikel.objects.filter(artikelnummer="101001").count(), 2)

    def test_das_datum_folgt_dem_status(self):
        artikel = hausartikel(self.haus, self.cola)
        artikel.status = Artikelstatus.INAKTIV
        artikel.full_clean()
        self.assertIsNotNone(artikel.gueltig_bis)
        artikel.status = Artikelstatus.AKTIV
        artikel.full_clean()
        self.assertIsNone(artikel.gueltig_bis)

    def test_die_datenbank_haelt_datum_und_status_zusammen(self):
        """Past the model, because 'update()' does not know '_status_angleichen'."""
        artikel = hausartikel(self.haus, self.cola)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Hausartikel.objects.filter(pk=artikel.pk).update(status=Artikelstatus.INAKTIV)

    def test_aktiv_ist_aus_dem_status_abgeleitet(self):
        artikel = hausartikel(self.haus, self.cola)
        for status, aktiv in ((Artikelstatus.INAKTIV, False), (Artikelstatus.AKTIV, True)):
            artikel.status_setzen(status)
            self.assertEqual(artikel.aktiv, aktiv)
            self.assertEqual(Hausartikel.objects.filter(aktiv=aktiv).get(), artikel)

    def test_deaktivieren_setzt_beides(self):
        artikel = hausartikel(self.haus, self.cola)
        artikel.deaktivieren()
        artikel.refresh_from_db()
        self.assertEqual(artikel.status, Artikelstatus.INAKTIV)
        self.assertIsNotNone(artikel.gueltig_bis)

    def test_inaktiv_haelt_nummer_und_ware(self):
        """Seasonal goods come back. Nobody else gets their number meanwhile."""
        hausartikel(self.haus, self.cola).deaktivieren()
        with self.assertRaises(IntegrityError), transaction.atomic():
            hausartikel(self.haus, ware("Artikel F 0,33"))
        with self.assertRaises(IntegrityError), transaction.atomic():
            hausartikel(self.haus, self.cola, nummer="101002")

    def test_eingestellt_gibt_nummer_und_ware_frei(self):
        hausartikel(self.haus, self.cola).einstellen()
        self.assertTrue(hausartikel(self.haus, ware("Artikel F 0,33")).aktiv)
        self.assertTrue(hausartikel(self.haus, self.cola, nummer="101002").aktiv)

    def test_eingestellt_behaelt_das_erste_datum(self):
        artikel = hausartikel(self.haus, self.cola)
        artikel.deaktivieren()
        seit = artikel.gueltig_bis
        artikel.einstellen()
        artikel.refresh_from_db()
        self.assertEqual(artikel.gueltig_bis, seit)

    def test_aktivieren_kehrt_das_um(self):
        artikel = hausartikel(self.haus, self.cola)
        artikel.einstellen()
        artikel.aktivieren()
        artikel.refresh_from_db()
        self.assertTrue(artikel.aktiv)
        self.assertIsNone(artikel.gueltig_bis)

    def test_reaktivieren_nennt_den_nachfolger(self):
        alt = hausartikel(self.haus, self.cola)
        alt.einstellen()
        neu = hausartikel(self.haus, ware("Artikel F 0,33"))
        self.assertEqual(alt.blockiert_durch(), neu)
        with self.assertRaisesMessage(ValidationError, "Artikel F 0,33"):
            alt.aktivieren()
        alt.refresh_from_db()
        self.assertEqual(alt.status, Artikelstatus.EINGESTELLT)

    def test_ohne_nachfolger_ist_der_platz_frei(self):
        artikel = hausartikel(self.haus, self.cola)
        artikel.einstellen()
        self.assertIsNone(artikel.blockiert_durch())

    def test_ignoriert_geht_nicht_ueber_den_status(self):
        artikel = hausartikel(self.haus, self.cola)
        with self.assertRaises(ValidationError):
            artikel.status_setzen(Artikelstatus.IGNORIERT)


class AusscheidenTests(TestCase):
    """What happens with expectations and running counts when an article retires."""

    @classmethod
    def setUpTestData(cls):

        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.ort = Ort.objects.create(name="Theke", bereich=bereich(cls.haus))
        cls.cola = ware("Artikel C 0,33")
        cls.kasten = Gebinde.objects.create(
            ware=cls.cola, name="Kasten", faktor_in_basiseinheit=Decimal("24")
        )
        cls.inventur = inventur_anlegen(cls.haus, stichtag="2026-09-30")

    def setUp(self):
        self.artikel = hausartikel(self.haus, self.cola)
        Erwartung.objects.create(ort=self.ort, artikel=self.artikel)

    def position(self, **werte):
        from inventur.models import Zaehlposition  # noqa: PLC0415

        return Zaehlposition.objects.create(
            inventur=self.inventur, ort=self.ort, artikel=self.artikel, gebinde=self.kasten, **werte
        )

    def test_inaktiv_bleibt_erwartet(self):
        self.artikel.deaktivieren()
        self.assertTrue(Erwartung.objects.filter(artikel=self.artikel).exists())

    def test_eingestellt_wird_nirgends_mehr_erwartet(self):
        self.artikel.einstellen()
        self.assertFalse(Erwartung.objects.filter(artikel=self.artikel).exists())

    def test_eingestellt_bleibt_in_der_laufenden_zaehlung(self):
        position = self.position()
        self.artikel.einstellen()
        position.refresh_from_db()
        self.assertTrue(position.aktiv)

    def test_ignorieren_legt_die_ware_in_nicht_zaehlbar(self):
        self.artikel.ignorieren()
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.ware, self.cola)
        self.assertEqual(self.artikel.ware.warenfamilie, Warenfamilie.nicht_zaehlbar())
        self.assertEqual(self.artikel.status, Artikelstatus.IGNORIERT)
        self.assertIsNotNone(self.artikel.gueltig_bis)
        self.assertFalse(Erwartung.objects.filter(artikel=self.artikel).exists())

    def test_ignorieren_nimmt_ungezaehlte_zeilen_aus_der_zaehlung(self):
        position = self.position()
        self.artikel.ignorieren()
        position.refresh_from_db()
        self.assertFalse(position.aktiv)

    def test_gezaehltes_laesst_sich_nicht_ignorieren(self):
        self.position(eingabemenge=Decimal("2"))
        with self.assertRaises(ValidationError):
            self.artikel.ignorieren()
        self.cola.refresh_from_db()
        self.assertIsNone(self.cola.warenfamilie)
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.status, Artikelstatus.AKTIV)

    def test_die_familie_der_ware_bestimmt_ignoriert_in_beide_richtungen(self):
        """Applies in every house that carries the product. Artikel P is nowhere a product."""
        anderes = hausartikel(Haus.objects.create(name="Kino Süd"), self.cola)
        self.cola.warenfamilie = Warenfamilie.nicht_zaehlbar()
        self.cola.full_clean()
        self.cola.save()
        for artikel in (self.artikel, anderes):
            artikel.refresh_from_db()
            self.assertEqual(artikel.status, Artikelstatus.IGNORIERT)

        self.cola.warenfamilie = None
        self.cola.full_clean()
        self.cola.save()
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.status, Artikelstatus.AKTIV)
        self.assertIsNone(self.artikel.gueltig_bis)

    def test_ein_hausartikel_an_einer_ignorierten_ware_ist_ignoriert(self):
        pfand = ware("Artikel P", warenfamilie=Warenfamilie.nicht_zaehlbar())
        artikel = hausartikel(self.haus, pfand, nummer="901001")
        self.assertEqual(artikel.status, Artikelstatus.IGNORIERT)

    def test_zurueck_nur_wo_der_platz_frei_ist(self):
        self.artikel.ignorieren()
        hausartikel(self.haus, ware("Artikel F 0,33"), nummer=self.artikel.artikelnummer)
        self.cola.refresh_from_db()
        self.cola.warenfamilie = None
        with self.assertRaises(ValidationError):
            self.cola.full_clean()

    def test_ignorierte_belegen_keine_nummer(self):
        """Artikel P under a number Compeso later reassigns is not in the way."""
        self.artikel.ignorieren()
        self.assertTrue(hausartikel(self.haus, ware("Artikel F 0,33")).aktiv)

    def test_ausgeschiedene_lassen_sich_nicht_erwarten(self):
        self.artikel.einstellen()
        with self.assertRaises(ValidationError):
            Erwartung(ort=self.ort, artikel=self.artikel).full_clean()


class NichtZaehlbarTests(TestCase):
    def test_kommt_aus_der_datenmigration(self):
        kein = Warenfamilie.nicht_zaehlbar()
        self.assertTrue(kein.sammelt_ignorierte)
        pfand = ware("Artikel P", warenfamilie=kein)
        self.assertNotIn(pfand, Ware.objects.echte())
        self.assertTrue(pfand.ist_nicht_zaehlbar)

    def test_gibt_es_nur_einmal(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Warenfamilie.objects.create(name="Zweite", sammelt_ignorierte=True)

    def test_laesst_sich_nicht_loeschen(self):
        """Its products would otherwise be products again, and their house articles stay ignored."""
        with self.assertRaises(ValidationError):
            Warenfamilie.nicht_zaehlbar().delete()
        self.assertTrue(Warenfamilie.objects.filter(sammelt_ignorierte=True).exists())

    def test_findet_sich_ueber_den_namen(self):
        """So a backup finds them again in a fresh database."""
        kein = Warenfamilie.nicht_zaehlbar()
        self.assertEqual(Warenfamilie.objects.get_by_natural_key(*kein.natural_key()), kein)

    def test_summiert_nichts_und_nimmt_jede_einheit(self):
        kein = Warenfamilie.nicht_zaehlbar()
        kein.aufnehmen(ware("Artikel P"))
        kein.aufnehmen(ware("Posten G lose", "kg"))
        kein.refresh_from_db()
        self.assertIsNone(kein.leiteinheit)
        self.assertEqual(kein.waren_ohne_faktor(), [])
        self.assertIsNone(ware("Posten S", "Liter").nicht_summierbar_in(kein))

    def test_gehoert_in_keine_rezeptur(self):
        portion = Rezepturartikel.objects.create(name="Portion", basiseinheit=einheit("Stück(e)"))
        with self.assertRaises(ValidationError):
            Rezepturkomponente(
                rezepturartikel=portion,
                ware=ware("Artikel P", warenfamilie=Warenfamilie.nicht_zaehlbar()),
                menge_in_basiseinheit=Decimal("1"),
            ).full_clean()


class WarenstatusTests(TestCase):
    """A product stands like its liveliest house article and can be set as a whole."""

    @classmethod
    def setUpTestData(cls):
        cls.kino_nord = Haus.objects.create(name="Kino Nord")
        cls.kino_sued = Haus.objects.create(name="Kino Süd")

    def setUp(self):
        self.cola = ware("Artikel C 0,33")
        self.hier = hausartikel(self.kino_nord, self.cola)
        self.dort = hausartikel(self.kino_sued, self.cola)

    def status(self):
        return Ware.objects.mit_statuszahlen().get(pk=self.cola.pk).warenstatus

    def test_aktiv_solange_ein_haus_sie_aktiv_fuehrt(self):
        self.hier.deaktivieren()
        self.assertEqual(self.status(), Artikelstatus.AKTIV)
        self.assertEqual(self.cola.warenstatus, Artikelstatus.AKTIV)

    def test_eingestellt_erst_wenn_alle_eingestellt_sind(self):
        self.hier.einstellen()
        self.dort.deaktivieren()
        self.assertEqual(self.status(), Artikelstatus.INAKTIV)
        self.dort.einstellen()
        self.assertEqual(self.status(), Artikelstatus.EINGESTELLT)

    def test_ohne_hausartikel_aktiv(self):
        self.assertEqual(ware("Neu").warenstatus, Artikelstatus.AKTIV)

    def test_einstellen_trifft_alle_haeuser(self):
        Erwartung.objects.create(
            ort=Ort.objects.create(name="Theke", bereich=bereich(self.kino_sued)), artikel=self.dort
        )
        self.cola.status_setzen(Artikelstatus.EINGESTELLT)
        for artikel in (self.hier, self.dort):
            artikel.refresh_from_db()
            self.assertEqual(artikel.status, Artikelstatus.EINGESTELLT)
        self.assertFalse(Erwartung.objects.exists())

    def test_aktiv_holt_den_zuletzt_eingestellten_zurueck(self):
        """Two discontinued rows of the same number: the slot goes to whoever had it last."""
        tee = ware("Ware W")
        jetzt = timezone.now()
        frueher = hausartikel(self.kino_sued, tee, "101700", name="Ware W 2019")
        frueher.einstellen()
        zuletzt = hausartikel(self.kino_sued, tee, "101700", name="Ware W 2024")
        zuletzt.einstellen()
        Hausartikel.objects.filter(pk=frueher.pk).update(gueltig_bis=jetzt - timedelta(days=900))

        nicht_zurueck = tee.status_setzen(Artikelstatus.AKTIV)

        self.assertEqual(Hausartikel.objects.get(pk=zuletzt.pk).status, Artikelstatus.AKTIV)
        self.assertEqual(Hausartikel.objects.get(pk=frueher.pk).status, Artikelstatus.EINGESTELLT)
        self.assertEqual(len(nicht_zurueck), 1)

    def test_aktiv_holt_zurueck_was_zurueckkann(self):
        self.cola.status_setzen(Artikelstatus.EINGESTELLT)
        hausartikel(self.kino_sued, ware("Artikel F 0,33"), nummer=self.dort.artikelnummer)
        offen = self.cola.status_setzen(Artikelstatus.AKTIV)
        self.hier.refresh_from_db()
        self.dort.refresh_from_db()
        self.assertEqual(self.hier.status, Artikelstatus.AKTIV)
        self.assertEqual(self.dort.status, Artikelstatus.EINGESTELLT)
        self.assertEqual(len(offen), 1)
        self.assertIn("Artikel F 0,33", offen[0])


class ZaehlpflichtTests(TestCase):
    """When an article has to be counted along in an inventory."""

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.monatlich = hausartikel(cls.haus, ware("Artikel C 0,33"))
        cls.jaehrlich = hausartikel(
            cls.haus, ware("Ware D"), nummer="101002", inventurart=jahresinventur()
        )
        cls.monat = Zaehlumfang((monatsinventur().pk,))
        cls.alle = Zaehlumfang((monatsinventur().pk, jahresinventur().pk))

    def test_art_und_haus(self):
        """An article is due where its type is counted, and only in its own house."""
        fremd = Haus.objects.create(name="Kino Süd")
        faelle = (
            ("Monatsware im Monat", self.monatlich, self.monat, self.haus.pk, True),
            ("Monatsware mit allen Arten", self.monatlich, self.alle, self.haus.pk, True),
            ("Jahresware im Monat", self.jaehrlich, self.monat, self.haus.pk, False),
            ("Jahresware mit allen Arten", self.jaehrlich, self.alle, self.haus.pk, True),
            ("fremdes Haus", self.monatlich, self.alle, fremd.pk, False),
        )
        for fall, artikel, umfang, haus_id, erwartet in faelle:
            with self.subTest(fall):
                self.assertIs(artikel.zaehlpflichtig_bei(umfang, haus_id), erwartet)

    def test_inaktiv_schlaegt_alles(self):
        self.monatlich.deaktivieren()
        self.assertFalse(self.monatlich.zaehlpflichtig_bei(self.alle, self.haus.pk))

    def test_ueber_die_basis_gerufen(self):
        kern = Artikel.objects.get(pk=self.jaehrlich.pk)
        self.assertFalse(kern.zaehlpflichtig_bei(self.monat, self.haus.pk))


class RezepturartikelTests(TestCase):
    """Prepared goods: they resolve into products through their recipe."""

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.karton = ware("Posten N")
        cls.nachos = ware("Artikel N", "kg")
        cls.portion = Rezepturartikel.objects.create(
            name="Portion N", basiseinheit=einheit("Stück(e)")
        )
        Rezepturkomponente.objects.create(
            rezepturartikel=cls.portion, ware=cls.karton, menge_in_basiseinheit=Decimal("1")
        )
        Rezepturkomponente.objects.create(
            rezepturartikel=cls.portion,
            ware=cls.nachos,
            menge_in_basiseinheit=Decimal("0.14"),
        )

    def test_wird_in_stueck_gezaehlt(self):
        self.assertEqual(self.portion.basiseinheit, einheit("Stück(e)"))

    def test_verfuegbar_nur_wenn_jede_komponente_da_ist(self):
        self.assertFalse(self.portion.verfuegbar_in(self.haus.pk))
        hausartikel(self.haus, self.karton)
        self.assertFalse(self.portion.verfuegbar_in(self.haus.pk))
        hausartikel(self.haus, self.nachos, nummer="101002")
        self.assertTrue(self.portion.verfuegbar_in(self.haus.pk))

    def test_alle_haeuser_auf_einmal_wie_je_haus(self):
        zweites = Haus.objects.create(name="Kino Süd")
        hausartikel(self.haus, self.karton)
        hausartikel(self.haus, self.nachos, nummer="101002")
        hausartikel(zweites, self.karton, nummer="101003")
        self.assertEqual(self.portion.verfuegbar_in_haeusern(), {self.haus.pk})
        hausartikel(zweites, self.nachos, nummer="101004").deaktivieren()
        self.assertEqual(self.portion.verfuegbar_in_haeusern(), {self.haus.pk})
        leer = Rezepturartikel.objects.create(name="Leer", basiseinheit=einheit("Stück(e)"))
        self.assertEqual(leer.verfuegbar_in_haeusern(), set())

    def test_inaktive_komponente_zaehlt_nicht(self):
        hausartikel(self.haus, self.karton)
        zweiter = hausartikel(self.haus, self.nachos, nummer="101002")
        zweiter.deaktivieren()
        self.assertFalse(self.portion.verfuegbar_in(self.haus.pk))

    def test_ohne_rezeptur_nicht_verfuegbar(self):
        leer = Rezepturartikel.objects.create(name="Leer", basiseinheit=einheit("Stück(e)"))
        self.assertFalse(leer.verfuegbar_in(self.haus.pk))
        self.assertFalse(leer.aufloesbar_in(self.haus.pk))

    def test_aufloesbar_auch_ueber_eine_inaktive_komponente(self):
        """Inactive holds the slot. The share lands there, discontinued has none any more."""
        hausartikel(self.haus, self.karton)
        zweiter = hausartikel(self.haus, self.nachos, nummer="101002")
        zweiter.deaktivieren()
        self.assertFalse(self.portion.verfuegbar_in(self.haus.pk))
        self.assertTrue(self.portion.aufloesbar_in(self.haus.pk))
        zweiter.einstellen()
        self.assertFalse(self.portion.aufloesbar_in(self.haus.pk))

    def test_umfang_passt_sobald_eine_zutat_dran_ist(self):
        """The ingredient's type is that of the article the house carries for it."""
        monat = Zaehlumfang((monatsinventur().pk,))
        jahr = Zaehlumfang((jahresinventur().pk,))
        karton = hausartikel(self.haus, self.karton)
        nachos = hausartikel(self.haus, self.nachos, nummer="101002")
        nachos.inventurart_setzen(jahresinventur())
        self.assertTrue(self.portion.umfang_passt(monat, self.haus.pk))
        karton.inventurart_setzen(jahresinventur())
        self.assertFalse(self.portion.umfang_passt(monat, self.haus.pk))
        self.assertTrue(self.portion.umfang_passt(jahr, self.haus.pk))
        # A house that carries none of them has nothing due.
        self.assertFalse(self.portion.umfang_passt(jahr, Haus.objects.create(name="Kino Süd").pk))

    def test_zaehlpflicht_folgt_den_komponenten(self):
        """Due as soon as one ingredient is, and only where every one is available."""
        monat = Zaehlumfang((monatsinventur().pk,))
        alle = Zaehlumfang((monatsinventur().pk, jahresinventur().pk))
        karton = hausartikel(self.haus, self.karton)
        nachos = hausartikel(self.haus, self.nachos, nummer="101002")
        nachos.inventurart_setzen(jahresinventur())
        self.assertTrue(
            self.portion.zaehlpflichtig_bei(monat, self.haus.pk), "eine Zutat ist monatlich"
        )
        karton.inventurart_setzen(jahresinventur())
        self.assertFalse(
            self.portion.zaehlpflichtig_bei(monat, self.haus.pk), "alle Zutaten nur jährlich"
        )
        self.assertTrue(self.portion.zaehlpflichtig_bei(alle, self.haus.pk), "mit allen Arten")

    def test_name_nur_einmal(self):
        with self.assertRaises(ValidationError):
            Rezepturartikel(name="Portion N", basiseinheit=einheit("Stück(e)")).full_clean()

    def test_komponente_je_ware_nur_einmal(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Rezepturkomponente.objects.create(
                rezepturartikel=self.portion,
                ware=self.karton,
                menge_in_basiseinheit=Decimal("2"),
            )

    def test_menge_muss_positiv_sein(self):
        with self.assertRaises(ValidationError):
            Rezepturkomponente(
                rezepturartikel=self.portion,
                ware=ware("Posten Z"),
                menge_in_basiseinheit=Decimal("0"),
            ).full_clean()

    def test_str_nennt_stueckzahlen_ohne_einheit(self):
        """For pieces the unit contributes nothing, for mass it does."""
        nachos, karton = self.portion.komponenten.order_by("ware__name")
        self.assertEqual(str(karton), "1 × Posten N")
        self.assertEqual(str(nachos), "0,14 kg Artikel N")


class GewogenerRezepturartikelTests(TestCase):
    """A container goes on the scale as a whole and is distributed through the recipe.

    The case: a mix that exists in WinTICKET only as two article numbers. It
    is counted once (in grams, gross), it is booked proportionally onto both
    house articles.
    """

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.erdnuesse = ware("Ware E", "g")
        cls.cashews = ware("Posten C", "g")
        cls.eimer = Behaelter.objects.create(name="Eimer", tara=Decimal("300"))

        cls.mischung = Rezepturartikel.objects.create(name="Mischung N", basiseinheit=einheit("g"))
        cls.mischung.behaelter.set([cls.eimer])
        Rezepturkomponente.objects.create(
            rezepturartikel=cls.mischung,
            ware=cls.erdnuesse,
            menge_in_basiseinheit=Decimal("0.6"),
        )
        Rezepturkomponente.objects.create(
            rezepturartikel=cls.mischung,
            ware=cls.cashews,
            menge_in_basiseinheit=Decimal("0.4"),
        )

    def test_masse_heisst_wiegen(self):
        self.assertTrue(self.mischung.wird_gewogen)

    def test_stueck_heisst_abzaehlen(self):
        portion = Rezepturartikel.objects.create(name="Portion", basiseinheit=einheit("Stück(e)"))
        self.assertFalse(portion.wird_gewogen)
        self.assertEqual(portion.in_basiseinheit(Decimal("7")), Decimal("7"))

    def test_tara_geht_ab_und_ohne_behaelter_zaehlt_das_volle_gewicht(self):
        for behaelter, erwartet in ((self.eimer, "1000"), (None, "1300")):
            with self.subTest(behaelter=behaelter):
                self.assertEqual(
                    self.mischung.in_basiseinheit(Decimal("1300"), behaelter), Decimal(erwartet)
                )

    def test_waehlbare_behaelter_enthalten_die_leere_waage(self):
        self.assertCountEqual(
            self.mischung.waehlbare_behaelter(), [self.eimer, Behaelter.ohne_behaelter()]
        )

    def test_ein_wiegen_landet_auf_beiden_waren(self):
        verteilt = self.mischung.mengen_je_ware(Decimal("1300"), self.eimer)
        self.assertEqual(verteilt[self.erdnuesse], Decimal("600.0"))
        self.assertEqual(verteilt[self.cashews], Decimal("400.0"))

    def test_anteile_bleiben_in_der_einheit_der_ware(self):
        """A component carried in kg gets kg, not grams."""
        dip = ware("Ware Q", "kg")
        Rezepturkomponente.objects.create(
            rezepturartikel=self.mischung,
            ware=dip,
            menge_in_basiseinheit=Decimal("0.0006"),
        )
        self.assertEqual(self.mischung.mengen_je_ware(Decimal("1000"))[dip], Decimal("0.6000"))

    def test_behaelter_nur_beim_wiegen(self):
        self.mischung.basiseinheit = einheit("Stück(e)")
        with self.assertRaises(ValidationError):
            self.mischung.full_clean()

    def test_nur_stueck_und_gramm_stehen_zur_wahl(self):
        """Weighing happens in grams. A second mass unit only brings conversion."""
        self.assertCountEqual(
            Rezepturartikel.waehlbare_einheiten().values_list("name", flat=True),
            ["Stück(e)", "g"],
            "die Wahl",
        )
        for name in ("kg", "Liter", "Milliliter"):
            with self.subTest(einheit=name), self.assertRaises(ValidationError):
                Rezepturartikel(name=f"In {name}", basiseinheit=einheit(name)).full_clean()


class AbgeleiteterAktivZustandTests(TestCase):
    """Product group and product carry no switch of their own."""

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        cls.cola = ware("Artikel C 0,33")
        cls.artikel = hausartikel(cls.haus, cls.cola, warengruppe=cls.gruppe)

    def test_aktiv_solange_ware_aktiv_ist(self):
        self.assertTrue(self.gruppe.aktiv)
        self.assertTrue(self.cola.aktiv)

        self.artikel.deaktivieren()
        self.assertFalse(Warengruppe.objects.get(pk=self.gruppe.pk).aktiv)
        self.assertFalse(Ware.objects.get(pk=self.cola.pk).aktiv)

    def test_ohne_artikel_inaktiv(self):
        self.assertFalse(Warengruppe.objects.create(name="Leer").aktiv)
        self.assertFalse(ware("Nie geführt").aktiv)

    def test_deaktivieren_setzt_alle_artikel(self):
        self.assertEqual(self.gruppe.deaktivieren(), 1)
        self.artikel.refresh_from_db()
        self.assertFalse(self.artikel.aktiv)
        self.assertIsNotNone(self.artikel.gueltig_bis)

    def test_deaktivieren_zaehlt_nur_die_aktiven(self):
        self.artikel.deaktivieren()
        self.assertEqual(self.gruppe.deaktivieren(), 0)

    def test_haeuser_der_warengruppe(self):
        kino_sued = Haus.objects.create(name="Kino Süd")
        hausartikel(kino_sued, self.cola, warengruppe=self.gruppe)
        self.assertEqual([haus.name for haus in self.gruppe.haeuser], ["Kino Nord", "Kino Süd"])


class WarengruppeArtTests(TestCase):
    """The inventory type hangs on the house article but can be switched per group."""

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe U")
        cls.eine = ware("Ware G")
        cls.andere = ware("Artikel L")
        hausartikel(cls.haus, cls.eine, warengruppe=cls.gruppe)
        hausartikel(cls.haus, cls.andere, nummer="101002", warengruppe=cls.gruppe)

    def test_setzen_erfasst_alle_artikel(self):
        self.assertEqual(self.gruppe.inventurart_setzen(jahresinventur()), 2)
        self.assertEqual(
            set(self.gruppe.hausartikel.values_list("inventurart", flat=True)),
            {jahresinventur().pk},
        )

    def test_schon_gesetzte_zaehlen_nicht_mit(self):
        self.gruppe.inventurart_setzen(jahresinventur())
        self.assertEqual(self.gruppe.inventurart_setzen(jahresinventur()), 0)

    def test_verteilung_zaehlt_artikel_je_art(self):
        """The same type is one entry, in the order of the types.

        The house article's Meta ordering would otherwise be in the GROUP BY
        and group per row.
        """
        self.assertEqual(self.gruppe.arten_verteilung(), [(monatsinventur(), 2)], "beide gleich")
        Hausartikel.objects.get(ware=self.eine).inventurart_setzen(jahresinventur())
        self.assertEqual(
            self.gruppe.arten_verteilung(),
            [(monatsinventur(), 1), (jahresinventur(), 1)],
            "je Art eine",
        )


class SortierungTests(TestCase):
    """Positions are unique per group and assigned automatically."""

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.ort = Ort.objects.create(name="Theke", bereich=bereich(cls.haus))
        cls.cola = ware("Artikel C 0,33")

    def gebinde(self, *namen):
        return [
            Gebinde.objects.create(
                ware=self.cola, name=name, faktor_in_basiseinheit=Decimal(stelle + 1)
            )
            for stelle, name in enumerate(namen)
        ]

    def erwartungen(self, anzahl: int):
        saetze = []
        for stelle in range(anzahl):
            artikel = hausartikel(self.haus, ware(f"Ware {stelle}"), nummer=f"10000{stelle}")
            saetze.append(Erwartung.objects.create(ort=self.ort, artikel=artikel))
        return saetze

    def test_position_wird_hinten_angehaengt(self):
        self.assertEqual([satz.sortierung for satz in self.gebinde("A", "B", "C")], [1, 2, 3])

    def test_position_je_gruppe_eindeutig(self):
        erste, zweite = self.gebinde("A", "B")
        zweite.sortierung = erste.sortierung
        with self.assertRaises(IntegrityError), transaction.atomic():
            zweite.save()

    def test_neu_sortieren_dreht_die_reihenfolge(self):
        a, b, c = self.gebinde("A", "B", "C")
        Gebinde.neu_sortieren(self.cola.pk, [c.pk, a.pk, b.pk])
        self.assertEqual(
            list(
                Gebinde.objects.filter(ware=self.cola)
                .order_by("sortierung")
                .values_list("name", flat=True)
            ),
            ["C", "A", "B"],
        )

    def test_neu_sortieren_verlangt_die_ganze_gruppe(self):
        a, _b = self.gebinde("A", "B")
        with self.assertRaises(ValueError):
            Gebinde.neu_sortieren(self.cola.pk, [a.pk])

    def test_erwartungen_werden_ebenso_gezaehlt(self):
        saetze = self.erwartungen(3)
        self.assertEqual([satz.sortierung for satz in saetze], [1, 2, 3])

        Erwartung.neu_sortieren(self.ort.pk, [satz.pk for satz in reversed(saetze)])
        self.assertEqual(
            list(
                Erwartung.objects.filter(ort=self.ort)
                .order_by("sortierung")
                .values_list("pk", flat=True)
            ),
            [satz.pk for satz in reversed(saetze)],
        )

    def test_belegte_position_weicht_aus(self):
        """A formset assigns the same number to several new rows when validating."""
        erste = self.gebinde("A")[0]
        zweites = Gebinde(
            ware=self.cola,
            name="B",
            faktor_in_basiseinheit=Decimal("9"),
            sortierung=erste.sortierung,
        )
        zweites.save()
        self.assertEqual(zweites.sortierung, 2)


class ErwartungTests(TestCase):
    """Which article is to be found at which location."""

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.fremd = Haus.objects.create(name="Kino Süd")
        cls.ort = Ort.objects.create(name="Theke", bereich=bereich(cls.haus))
        cls.cola = ware("Artikel C 0,33")
        cls.artikel = hausartikel(cls.haus, cls.cola)
        cls.gebinde = Gebinde.objects.create(
            ware=cls.cola, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )

    def test_artikel_je_ort_nur_einmal(self):
        Erwartung.objects.create(ort=self.ort, artikel=self.artikel)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Erwartung.objects.create(ort=self.ort, artikel=self.artikel)

    def test_artikel_muss_zum_haus_passen(self):
        fremder_ort = Ort.objects.create(name="Theke", bereich=bereich(self.fremd))
        with self.assertRaises(ValidationError):
            Erwartung(ort=fremder_ort, artikel=self.artikel).full_clean()

    def test_rezepturartikel_muss_sich_aufloesen_lassen(self):
        portion = Rezepturartikel.objects.create(name="Portion", basiseinheit=einheit("Stück(e)"))
        Rezepturkomponente.objects.create(
            rezepturartikel=portion,
            ware=ware("Artikel N", "kg"),
            menge_in_basiseinheit=Decimal("0.14"),
        )
        with self.assertRaises(ValidationError):
            Erwartung(ort=self.ort, artikel=portion).full_clean()

    def test_vorgabe_muss_zum_artikel_gehoeren(self):
        erwartung = Erwartung.objects.create(ort=self.ort, artikel=self.artikel)
        fremdes = Gebinde.objects.create(
            ware=ware("Artikel F 0,33"),
            name="Kasten",
            faktor_in_basiseinheit=Decimal("20"),
        )
        with self.assertRaises(ValidationError):
            ErwartungGebinde(erwartung=erwartung, gebinde=fremdes).full_clean()

    def test_rezepturartikel_haben_keine_vorgaben(self):
        portion = Rezepturartikel.objects.create(name="Portion", basiseinheit=einheit("Stück(e)"))
        Rezepturkomponente.objects.create(
            rezepturartikel=portion, ware=self.cola, menge_in_basiseinheit=Decimal("1")
        )
        erwartung = Erwartung.objects.create(ort=self.ort, artikel=portion)
        with self.assertRaises(ValidationError):
            ErwartungGebinde(erwartung=erwartung, gebinde=self.gebinde).full_clean()

    def test_vorgaben_folgen_der_reihenfolge_der_gebinde(self):
        zweites = Gebinde.objects.create(
            ware=self.cola, name="Flasche(n)", faktor_in_basiseinheit=Decimal("1")
        )
        erwartung = Erwartung.objects.create(ort=self.ort, artikel=self.artikel)
        ErwartungGebinde.objects.create(erwartung=erwartung, gebinde=zweites)
        ErwartungGebinde.objects.create(erwartung=erwartung, gebinde=self.gebinde)

        self.assertEqual(
            [vorgabe.gebinde.name for vorgabe in erwartung.gebinde_vorgaben.all()],
            ["Kasten", "Flasche(n)"],
        )


class SchutzUndKaskadenTests(TestCase):
    """What goes along when deleting and what resists."""

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.ort = Ort.objects.create(name="Theke", bereich=bereich(cls.haus))
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        cls.cola = ware("Artikel C 0,33")
        cls.artikel = hausartikel(cls.haus, cls.cola, warengruppe=cls.gruppe)

    def test_haus_mit_artikeln_ist_geschuetzt(self):
        with self.assertRaises(ProtectedError), transaction.atomic():
            self.haus.delete()

    def test_warengruppe_mit_artikeln_ist_geschuetzt(self):
        with self.assertRaises(ProtectedError), transaction.atomic():
            self.gruppe.delete()

    def test_ware_mit_artikeln_ist_geschuetzt(self):
        with self.assertRaises(ProtectedError), transaction.atomic():
            self.cola.delete()

    def test_ware_loeschen_nimmt_ihre_gebinde_mit(self):
        frei = ware("Ungenutzt")
        Gebinde.objects.create(ware=frei, name="Kasten", faktor_in_basiseinheit=Decimal("20"))
        frei.delete()
        self.assertEqual(Gebinde.objects.filter(ware_id=frei.pk).count(), 0)

    def test_ort_loeschen_nimmt_die_erwartungen_mit(self):
        Erwartung.objects.create(ort=self.ort, artikel=self.artikel)
        self.ort.delete()
        self.assertEqual(Erwartung.objects.count(), 0)

    def test_artikel_loeschen_nimmt_die_erwartung_mit(self):
        Erwartung.objects.create(ort=self.ort, artikel=self.artikel)
        self.artikel.delete()
        self.assertEqual(Erwartung.objects.count(), 0)

    def test_rezepturartikel_loeschen_nimmt_die_rezeptur_mit(self):
        portion = Rezepturartikel.objects.create(name="Portion", basiseinheit=einheit("Stück(e)"))
        Rezepturkomponente.objects.create(
            rezepturartikel=portion, ware=self.cola, menge_in_basiseinheit=Decimal("1")
        )
        portion.delete()
        self.assertEqual(Rezepturkomponente.objects.count(), 0)


class StammdatenSprechenDieSpracheDesKontosTests(TestCase):
    """Three rows belong to the application, not to a house.

    They arise in a migration and are called the same everywhere. Their name
    is thus language and not data: stored it stays as it is, shown it is
    translated. Nothing is renamed, not even when the server changes
    language. The choice hangs on the account here as everywhere.
    """

    def test_nicht_zaehlbar(self):
        familie = Warenfamilie.nicht_zaehlbar()
        self.assertEqual(familie.name, "Nicht zählbar")
        self.assertEqual(familie.anzeigename, "Nicht zählbar")
        with translation.override("en"):
            self.assertEqual(str(familie.anzeigename), "Not countable")
            self.assertIn("deposits", str(familie.anzeigebeschreibung).lower())

    def test_ohne_behaelter(self):
        behaelter = Behaelter.ohne_behaelter()
        self.assertEqual(behaelter.name, "Ohne Behälter (Waage genullt)")
        with translation.override("en"):
            self.assertEqual(str(behaelter.anzeigename), "No container (scale tared)")

    def test_ohne_warengruppe(self):
        gruppe = Warengruppe.objects.create(name=OHNE_WARENGRUPPE)
        with translation.override("en"):
            self.assertEqual(str(gruppe.anzeigename), "No article group")

    def test_jede_andere_bleibt_wie_sie_eingetragen_ist(self):
        """Product groups come from the inventory management system, families are maintained."""
        gruppe = Warengruppe.objects.create(name="201 VK - Getränke")
        familie = Warenfamilie.objects.create(name="Limonaden", beschreibung="Sprudelndes.")
        behaelter = Behaelter.objects.create(name="Schale", tara=Decimal("250"))
        with translation.override("en"):
            self.assertEqual(gruppe.anzeigename, "201 VK - Getränke")
            self.assertEqual(familie.anzeigename, "Limonaden")
            self.assertEqual(familie.anzeigebeschreibung, "Sprudelndes.")
            self.assertEqual(behaelter.anzeigename, "Schale")

    def test_der_gespeicherte_name_bleibt_der_natuerliche_schluessel(self):
        """Otherwise every backup would be unreadable after a language switch."""
        with translation.override("en"):
            self.assertEqual(Warenfamilie.nicht_zaehlbar().natural_key(), ("Nicht zählbar",))
            self.assertEqual(
                Behaelter.ohne_behaelter().natural_key(), ("Ohne Behälter (Waage genullt)",)
            )


class NormalisierungPasstZumKatalogTests(SimpleTestCase):
    """'scripts.normalize' gets by without Django and carries the names a second time.

    The transformation run ('python -m scripts.transform') starts without
    'django.setup()'. The ids and the name of the single unit therefore stand
    there once more. They must not drift apart.
    """

    def test_dieselben_kennungen(self):
        """Without the units only a family carries: an export never brings a portion."""
        aus_katalog = {kennung.value for kennung in Einheitenkennung} - set(NUR_FUER_FAMILIEN)
        aus_normalisierung = set(normalize.EINHEITEN.values())
        self.assertEqual(aus_normalisierung, aus_katalog)

    def test_derselbe_name_fuer_das_einzelstueck(self):
        self.assertEqual(normalize.EINZELN, EINZELN_NAME)

    def test_der_rueckfall_ist_stueck(self):
        self.assertEqual(normalize.RUECKFALL, Einheitenkennung.STUECK.value)


class EinzelstueckTests(TestCase):
    """The loose single unit carries a switch instead of a name."""

    @classmethod
    def setUpTestData(cls):
        cls.cola = ware("Artikel C 0,33")
        cls.sirup = ware("Artikel S", "Liter")

    def test_anzeigename_kommt_aus_der_sprache(self):
        einzeln = Gebinde.objects.create(
            ware=self.cola,
            name="Einzeln",
            ist_einzeln=True,
            faktor_in_basiseinheit=Decimal("1"),
        )
        self.assertEqual(einzeln.anzeigename, "Einzeln")
        with translation.override("en"):
            self.assertEqual(str(einzeln.anzeigename), "Single")

    def test_jeder_andere_name_bleibt_wie_er_eingetragen_ist(self):
        kasten = Gebinde.objects.create(
            ware=self.cola, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        with translation.override("en"):
            self.assertEqual(kasten.anzeigename, "Kasten")

    def test_die_datenbank_laesst_nur_genau_eines_zu(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Gebinde.objects.create(
                ware=self.cola,
                name="Sixpack",
                ist_einzeln=True,
                faktor_in_basiseinheit=Decimal("6"),
            )

    def test_es_gibt_es_nur_bei_stueckware(self):
        """A litre in a bottle is a pack and not a single piece."""
        flasche = Gebinde(
            ware=self.sirup,
            name="Einzeln",
            ist_einzeln=True,
            faktor_in_basiseinheit=Decimal("1"),
        )
        with self.assertRaises(ValidationError) as fehler:
            flasche.full_clean()
        self.assertIn("ist_einzeln", fehler.exception.message_dict)


class GebindemengeEindeutigTests(TestCase):
    """Per product every quantity occurs only once, now in the database too.

    "Kasten" and "Pack" of 18 each are the same pack under two names and
    cannot be told apart when counting.
    """

    def setUp(self):
        self.cola = ware("Artikel C 0,33")
        Gebinde.objects.create(ware=self.cola, name="Kasten", faktor_in_basiseinheit=Decimal("20"))

    def test_gleiche_menge_wird_von_der_datenbank_abgewiesen(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Gebinde.objects.create(
                ware=self.cola, name="Kiste", faktor_in_basiseinheit=Decimal("20")
            )

    def test_clean_nennt_den_namen_der_die_menge_belegt(self):
        doppelt = Gebinde(ware=self.cola, name="Kiste", faktor_in_basiseinheit=Decimal("20"))
        with self.assertRaises(ValidationError) as fehler:
            doppelt.full_clean()
        self.assertIn("„Kasten“", str(fehler.exception))

    def test_andere_menge_geht(self):
        Gebinde.objects.create(ware=self.cola, name="Einzeln", faktor_in_basiseinheit=Decimal("1"))
        self.assertEqual(self.cola.gebinde.count(), 2)

    def test_dieselbe_menge_bei_anderer_ware_geht(self):
        Gebinde.objects.create(
            ware=ware("Artikel F 0,33"),
            name="Kasten",
            faktor_in_basiseinheit=Decimal("20"),
        )
        self.assertEqual(Gebinde.objects.filter(faktor_in_basiseinheit=20).count(), 2)

    def test_das_eigene_bleibt_bearbeitbar(self):
        kasten = self.cola.gebinde.get()
        kasten.name = "Kiste"
        kasten.full_clean()
        kasten.save()
        self.assertEqual(self.cola.gebinde.get().name, "Kiste")


class KonkreteAuspraegung(TestCase):
    """'konkret()' is the path from the core table to the concrete form, and it is cheap."""

    def test_gibt_die_auspraegung_zurueck(self):
        haus = Haus.objects.create(name="Kino Ost")
        ware = Ware.objects.create(
            name="Artikel C", basiseinheit=Basiseinheit.objects.get(dimension=Dimension.STUECK)
        )
        artikel = Hausartikel.objects.create(
            haus=haus,
            ware=ware,
            warengruppe=Warengruppe.objects.create(name="Warengruppe G"),
            artikelnummer="1",
            name="Artikel C Haussatz",
        )
        kern = Artikel.objects.get(pk=artikel.pk)

        self.assertEqual(kern.konkret(), artikel)
        self.assertEqual(kern.warenname, "Artikel C")
        self.assertEqual(kern.name_im_haus, "Artikel C Haussatz")

    def test_auf_einer_auspraegung_kostet_es_keine_abfrage(self):
        """Otherwise it would be a hundred queries in a list with a hundred rows."""
        haus = Haus.objects.create(name="Kino Süd")
        ware = Ware.objects.create(
            name="Artikel F", basiseinheit=Basiseinheit.objects.get(dimension=Dimension.STUECK)
        )
        artikel = Hausartikel.objects.select_related("ware").get(
            pk=Hausartikel.objects.create(
                haus=haus,
                ware=ware,
                warengruppe=Warengruppe.objects.create(name="Warengruppe L"),
                artikelnummer="2",
                name="Artikel F",
            ).pk
        )

        with self.assertNumQueries(0):
            self.assertIs(artikel.konkret(), artikel)
            self.assertEqual(artikel.warenname, "Artikel F")
            self.assertEqual(artikel.name_im_haus, "")


class AnteiligeRezepturTests(TestCase):
    """A weighed mix states its recipe as shares of the weight in percent.

    The case: a house lemonade from three syrups in litres. Whoever knows the
    recipe knows the percentages, not the litres per gram. The way from the
    gram into the litre goes through the syrup's weight per litre, the same
    entry the scale uses.
    """

    @classmethod
    def setUpTestData(cls):
        cls.sirup_a = ware("Sirup A", "Liter", {Dimension.MASSE: Decimal("1250")})
        cls.sirup_b = ware("Sirup B", "Liter", {Dimension.MASSE: Decimal("1000")})
        cls.mischung = Rezepturartikel.objects.create(
            name="Sirupmischung", basiseinheit=einheit("g"), anteilig=True
        )
        for satz, anteil in ((cls.sirup_a, "60"), (cls.sirup_b, "40")):
            Rezepturkomponente.objects.create(
                rezepturartikel=cls.mischung, ware=satz, menge_in_basiseinheit=Decimal(anteil)
            )

    def test_anteile_gehen_ueber_das_gewicht_je_liter_in_die_ware(self):
        """1000 g of mix: 600 g of A at 1250 g/l are 0.48 l, 400 g of B at 1000 g/l 0.4 l."""
        verteilt = self.mischung.verteilen(Decimal("1000"))
        self.assertEqual(verteilt[self.sirup_a], Decimal("0.48"))
        self.assertEqual(verteilt[self.sirup_b], Decimal("0.4"))

    def test_ware_in_masse_rechnet_ueber_ihre_einheit(self):
        """A share of a product in kg: 30 % of 1000 g are 0.3 kg, no entry needed."""
        dip = ware("Ware Q", "kg")
        komponente = Rezepturkomponente.objects.create(
            rezepturartikel=self.mischung, ware=dip, menge_in_basiseinheit=Decimal("30")
        )
        komponente.full_clean()
        self.assertEqual(self.mischung.verteilen(Decimal("1000"))[dip], Decimal("0.3"))

    def test_ohne_gewicht_je_einheit_wird_die_zeile_abgewiesen(self):
        """A litre product without its grams: the share could not be converted."""
        ohne = ware("Sirup C", "Liter")
        komponente = Rezepturkomponente(
            rezepturartikel=self.mischung, ware=ohne, menge_in_basiseinheit=Decimal("10")
        )
        with self.assertRaises(ValidationError) as fehler:
            komponente.full_clean()
        self.assertIn(
            "wie viel Gramm eine Einheit wiegt", str(fehler.exception.message_dict["ware"])
        )
        # Without the flag the number is litres per gram, and nothing is converted.
        self.mischung.anteilig = False
        Rezepturkomponente(
            rezepturartikel=self.mischung, ware=ohne, menge_in_basiseinheit=Decimal("10")
        ).full_clean()

    def test_ein_anteil_ueber_hundert_wird_abgewiesen(self):
        komponente = Rezepturkomponente(
            rezepturartikel=self.mischung, ware=self.sirup_a, menge_in_basiseinheit=Decimal("120")
        )
        with self.assertRaises(ValidationError) as fehler:
            komponente.full_clean()
        self.assertIn("menge_in_basiseinheit", fehler.exception.message_dict)

    def test_anteile_muessen_das_ganze_ergeben(self):
        Rezepturartikel.anteile_pruefen([Decimal("60"), Decimal("40")])
        with self.assertRaises(ValidationError) as fehler:
            Rezepturartikel.anteile_pruefen([Decimal("60"), Decimal("38.5")])
        self.assertIn("98,5 %, nicht 100 %", str(fehler.exception))

    def test_anteile_gibt_es_nur_beim_wiegen(self):
        portion = Rezepturartikel(name="Portion P", basiseinheit=einheit("Stück(e)"), anteilig=True)
        with self.assertRaises(ValidationError) as fehler:
            portion.full_clean()
        self.assertIn("anteilig", fehler.exception.message_dict)

    def test_str_nennt_den_anteil_in_prozent(self):
        komponente = self.mischung.komponenten.get(ware=self.sirup_a)
        self.assertEqual(str(komponente), "60 % Sirup A")


class KurzformDerEinheit(TestCase):
    """The short form follows the language and comes from a small cache.

    Django's 'get_kennung_display' builds the dict of all choices on every
    call. The import plan asked it half a million times (12 s, 2026-10-09).
    """

    def test_folgt_der_sprache(self):
        stueck = Basiseinheit.objects.get(kennung=Einheitenkennung.STUECK)
        with translation.override("de"):
            self.assertEqual(stueck.kurz, "Stk.")
        with translation.override("en"):
            self.assertEqual(stueck.kurz, "pcs")

    def test_fragt_django_nicht_je_aufruf(self):
        stueck = Basiseinheit.objects.get(kennung=Einheitenkennung.STUECK)
        with mock.patch.object(Basiseinheit, "get_kennung_display") as django:
            self.assertEqual(stueck.kurz, stueck.kurz)
        django.assert_not_called()
