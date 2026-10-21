"""The workflow of an inventory.

Checked is what the model itself promises: that the count positions fit
the expectations, that recipe articles resolve over their recipe at
closing, and that after freezing only the results remain.
"""

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from accounts.models import CustomUser
from django_project.testing import inventur_anlegen, jahresinventur, monatsinventur
from katalog.models import (
    Basiseinheit,
    Behaelter,
    Dimension,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Rezepturartikel,
    Rezepturkomponente,
    Ware,
    Warengruppe,
)
from organisation.models import Bereich, Haus, Ort

from .models import (
    Inventur,
    Inventurergebnis,
    Inventurnotiz,
    Inventurstatus,
    Sollbestand,
    Sollgrund,
    Sollzeile,
    Wiegung,
    Zaehlposition,
)

STICHTAG = date(2026, 1, 31)


def einheit(name: str) -> Basiseinheit:
    return Basiseinheit.objects.get(name=name)


class InventurGrundlagen(TestCase):
    """Shared setup: a house with counter and storage, a few products."""

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Ost")
        cls.theke = Ort.objects.create(
            name="Thekenfront", bereich=Bereich.objects.create(name="Theke", haus=cls.haus)
        )
        cls.lager = Ort.objects.create(
            name="Trockenlager", bereich=Bereich.objects.create(name="Lager", haus=cls.haus)
        )
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        cls.nummer = 0

    @classmethod
    def ware(cls, name, einheitenname="Stück(e)", **abweichend) -> Ware:
        return Ware.objects.create(name=name, basiseinheit=einheit(einheitenname), **abweichend)

    @classmethod
    def artikel(cls, ware, haus=None, **abweichend) -> Hausartikel:
        cls.nummer += 1
        return Hausartikel.objects.create(
            haus=haus or cls.haus,
            ware=ware,
            warengruppe=cls.gruppe,
            artikelnummer=f"{101000 + cls.nummer}",
            name=ware.name,
            **abweichend,
        )

    @classmethod
    def inventur(cls, jahr=False, haus=None) -> Inventur:
        """A monthly inventory, or with 'jahr' one that also counts the annual goods."""
        arten = [monatsinventur(), jahresinventur()] if jahr else [monatsinventur()]
        return inventur_anlegen(haus or cls.haus, *arten, stichtag=STICHTAG)


class InventurModellTests(InventurGrundlagen):
    def test_str_nennt_stichtag_haus_und_arten(self):
        self.assertEqual(str(self.inventur()), "31.01.2026 Kino Ost Monatsinventur")

    def test_str_bei_mehreren_arten(self):
        inventur = self.inventur(jahr=True)
        self.assertEqual(str(inventur), "31.01.2026 Kino Ost Monatsinventur + Jahresinventur")

    def test_beginnt_in_der_vorbereitung(self):
        self.assertEqual(self.inventur().status, Inventurstatus.VORBEREITUNG)

    def test_je_haus_und_tag_nur_eine(self):
        self.inventur()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.inventur()

    def test_auch_mit_anderen_arten_nur_eine(self):
        """What else is to be counted that day becomes a tab of the same inventory."""
        self.inventur()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.inventur(jahr=True)

    def test_abgeschlossen_am_haengt_am_status(self):
        """The timestamp is set exactly when the inventory is closed."""
        inventur = self.inventur()
        inventur.status = Inventurstatus.ABGESCHLOSSEN
        with self.assertRaises(IntegrityError), transaction.atomic():
            inventur.save()

    def test_zaehlung_startet_nur_aus_der_vorbereitung(self):
        inventur = self.inventur()
        inventur.zaehlung_starten()
        self.assertEqual(inventur.status, Inventurstatus.ZAEHLUNG)
        with self.assertRaises(ValidationError):
            inventur.zaehlung_starten()


class LaufUeberMehrereHaeuser(InventurGrundlagen):
    """The bracket is the cut-off date. There is no separate parent inventory."""

    def test_der_gleiche_tag_bildet_den_lauf(self):
        eigene = self.inventur()
        andere = self.inventur(haus=Haus.objects.create(name="Kino Süd"))
        self.assertCountEqual(eigene.lauf(), [eigene, andere])
        self.assertTrue(eigene.gehoert_zu_lauf)

    def test_einzelne_inventur_ist_kein_lauf(self):
        eigene = self.inventur()
        self.assertEqual(list(eigene.lauf()), [eigene])
        self.assertFalse(eigene.gehoert_zu_lauf)

    def test_andere_arten_am_selben_tag_gehoeren_dazu(self):
        eigene = self.inventur()
        andere = self.inventur(jahr=True, haus=Haus.objects.create(name="Kino Süd"))
        self.assertCountEqual(eigene.lauf(), [eigene, andere])


class ZaehlpositionenAnlegen(InventurGrundlagen):
    """One position per expected pack, plus one for weighing."""

    def setUp(self):
        self.inv = self.inventur()

    def erwartung(self, ort, artikel, *gebinde, wiegen=False):
        erwartung = Erwartung.objects.create(ort=ort, artikel=artikel, wiegen_erwartet=wiegen)
        for satz in gebinde:
            ErwartungGebinde.objects.create(erwartung=erwartung, gebinde=satz)
        return erwartung

    def positionen(self):
        return [
            (p.ort.name, p.artikel.name, p.gebinde.name if p.gebinde_id else None)
            for p in self.inv.zaehlpositionen.all()
        ]

    def test_je_gebinde_eine_position(self):
        cola = self.ware("Artikel C 0,33")
        kasten = Gebinde.objects.create(
            ware=cola, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        einzeln = Gebinde.objects.create(
            ware=cola, name="Einzeln", faktor_in_basiseinheit=Decimal("1")
        )
        self.erwartung(self.theke, self.artikel(cola), kasten, einzeln)

        self.assertEqual(self.inv.zaehlpositionen_anlegen(), 2)
        self.assertCountEqual(
            self.positionen(),
            [
                ("Thekenfront", "Artikel C 0,33", "Kasten"),
                ("Thekenfront", "Artikel C 0,33", "Einzeln"),
            ],
        )

    def test_abwiegbare_ware_bekommt_eine_zusaetzliche_position(self):
        nachos = self.ware("Artikel N", "kg", abwiegbar=True)
        karton = Gebinde.objects.create(
            ware=nachos, name="Karton", faktor_in_basiseinheit=Decimal("5")
        )
        self.erwartung(self.theke, self.artikel(nachos), karton, wiegen=True)

        self.inv.zaehlpositionen_anlegen()
        self.assertCountEqual(
            self.positionen(),
            [("Thekenfront", "Artikel N", "Karton"), ("Thekenfront", "Artikel N", None)],
        )

    def test_ohne_wiegen_am_ort_entsteht_keine_wiegezeile(self):
        """In storage stands the box. A weighing row would stay open there at every inventory."""
        nachos = self.ware("Artikel N", "kg", abwiegbar=True)
        karton = Gebinde.objects.create(
            ware=nachos, name="Karton", faktor_in_basiseinheit=Decimal("5")
        )
        # Nobody has said anything about the scale: then it is not expected.
        erwartung = Erwartung.objects.create(ort=self.theke, artikel=self.artikel(nachos))
        ErwartungGebinde.objects.create(erwartung=erwartung, gebinde=karton)
        self.assertFalse(erwartung.wiegen_erwartet)

        self.inv.zaehlpositionen_anlegen()
        self.assertEqual(self.positionen(), [("Thekenfront", "Artikel N", "Karton")])

    def test_rezepturartikel_bekommt_genau_eine(self):
        cola = self.ware("Artikel C 0,33")
        self.artikel(cola)
        portion = Rezepturartikel.objects.create(name="Menü", basiseinheit=einheit("Stück(e)"))
        Rezepturkomponente.objects.create(
            rezepturartikel=portion, ware=cola, menge_in_basiseinheit=Decimal("1")
        )
        self.erwartung(self.theke, portion)

        self.inv.zaehlpositionen_anlegen()
        self.assertEqual(self.positionen(), [("Thekenfront", "Menü", None)])

    def test_inaktives_gebinde_bekommt_keine_die_vorgabe_bleibt(self):
        """No longer carried in it (decided 2026-10-18). Until 10/2026 it had a row."""
        cola = self.ware("Artikel C 0,33")
        kasten = Gebinde.objects.create(
            ware=cola, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        kiste = Gebinde.objects.create(
            ware=cola, name="Kiste", faktor_in_basiseinheit=Decimal("12"), aktiv=False
        )
        erwartung = self.erwartung(self.theke, self.artikel(cola), kasten, kiste)

        self.assertEqual(self.inv.zaehlpositionen_anlegen(), 1)
        self.assertEqual(self.positionen(), [("Thekenfront", "Artikel C 0,33", "Kasten")])
        # It counts again once the pack is active again.
        self.assertEqual(erwartung.gebinde_vorgaben.count(), 2)

    def test_ware_ohne_gebinde_und_ohne_waage_bekommt_keine(self):
        self.erwartung(self.theke, self.artikel(self.ware("Artikel C 0,33")))
        self.assertEqual(self.inv.zaehlpositionen_anlegen(), 0)

    def test_orte_werden_getrennt_gefuehrt(self):
        cola = self.ware("Artikel C 0,33")
        kasten = Gebinde.objects.create(
            ware=cola, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        artikel = self.artikel(cola)
        self.erwartung(self.theke, artikel, kasten)
        self.erwartung(self.lager, artikel, kasten)

        self.inv.zaehlpositionen_anlegen()
        self.assertCountEqual(
            self.positionen(),
            [
                ("Thekenfront", "Artikel C 0,33", "Kasten"),
                ("Trockenlager", "Artikel C 0,33", "Kasten"),
            ],
        )

    def test_zweiter_aufruf_verdoppelt_nichts(self):
        cola = self.ware("Artikel C 0,33")
        kasten = Gebinde.objects.create(
            ware=cola, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        self.erwartung(self.theke, self.artikel(cola), kasten)

        self.inv.zaehlpositionen_anlegen()
        self.inv.zaehlpositionen_anlegen()
        self.assertEqual(self.inv.zaehlpositionen.count(), 1)

    def test_fremdes_haus_bleibt_aussen_vor(self):
        fremd = Ort.objects.create(
            name="Theke",
            bereich=Bereich.objects.create(name="Theke", haus=Haus.objects.create(name="Kino Süd")),
        )
        cola = self.ware("Artikel C 0,33")
        kasten = Gebinde.objects.create(
            ware=cola, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        self.erwartung(fremd, self.artikel(cola, haus=fremd.bereich.haus), kasten)

        self.assertEqual(self.inv.zaehlpositionen_anlegen(), 0)

    def test_inaktiver_artikel_bekommt_keine_bleibt_aber_erwartet(self):
        """Seasonal goods out of season. If they are there anyway, the app counts them as new."""
        cola = self.ware("Artikel C 0,33")
        kasten = Gebinde.objects.create(
            ware=cola, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        artikel = self.artikel(cola)
        erwartung = self.erwartung(self.theke, artikel, kasten)
        artikel.deaktivieren()

        self.assertEqual(self.inv.zaehlpositionen_anlegen(), 0)
        self.assertTrue(Erwartung.objects.filter(pk=erwartung.pk).exists())


class JahreswareBeiDerMonatsinventur(InventurGrundlagen):
    """What is only counted annually gets no row in the month.

    A row that stays empty month after month would be in the way in the app
    and would reappear in the clarification at closing every time. The product
    can still be added, then as an unexpected row.
    """

    def setUp(self):
        self.wein = self.ware("Ware W")
        self.karton = Gebinde.objects.create(
            ware=self.wein, name="Karton", faktor_in_basiseinheit=Decimal("12")
        )
        self.cola = self.ware("Artikel C 0,33")
        self.kasten = Gebinde.objects.create(
            ware=self.cola, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        for ware, gebinde, art in (
            (self.wein, self.karton, jahresinventur()),
            (self.cola, self.kasten, monatsinventur()),
        ):
            erwartung = Erwartung.objects.create(
                ort=self.theke, artikel=self.artikel(ware, inventurart=art)
            )
            ErwartungGebinde.objects.create(erwartung=erwartung, gebinde=gebinde)

    def waren_der_positionen(self, inventur) -> list[str]:
        return [
            position.artikel.hausartikel.ware.name for position in inventur.zaehlpositionen.all()
        ]

    def test_monatsinventur_laesst_sie_aus(self):
        inventur = self.inventur()
        self.assertEqual(inventur.zaehlpositionen_anlegen(), 1)
        self.assertEqual(self.waren_der_positionen(inventur), ["Artikel C 0,33"])

    def test_mit_der_jahresinventur_beide(self):
        inventur = self.inventur(jahr=True)
        self.assertEqual(inventur.zaehlpositionen_anlegen(), 2)
        self.assertCountEqual(self.waren_der_positionen(inventur), ["Artikel C 0,33", "Ware W"])

    def test_die_erwartung_bleibt_stehen(self):
        """It stays expected. Only this inventory does not mean it."""
        self.inventur().zaehlpositionen_anlegen()
        self.assertEqual(Erwartung.objects.filter(ort=self.theke).count(), 2)

    def test_ihr_sollbestand_wird_trotzdem_gespeichert(self):
        """So it has a say in case someone counts them after all."""
        inventur = self.inventur()
        inventur.zaehlpositionen_anlegen()
        artikel = Hausartikel.objects.get(ware=self.wein)
        Sollbestand.objects.create(
            inventur=inventur, hausartikel=artikel, menge_basiseinheit=Decimal("24")
        )
        self.assertNotIn(artikel.pk, inventur.wirksame_sollbestaende())

        Zaehlposition.objects.create(
            inventur=inventur,
            ort=self.theke,
            artikel_id=artikel.pk,
            gebinde=self.karton,
            eingabemenge=Decimal("2"),
            unerwartet=True,
        )
        self.assertEqual(inventur.wirksame_sollbestaende()[artikel.pk], Decimal("24"))


class RezepturartikelNurAusJahresware(InventurGrundlagen):
    """A recipe article is due monthly as soon as **one** ingredient is."""

    def setUp(self):
        self.jahresware = self.ware("Ware W")
        self.artikel(self.jahresware, inventurart=jahresinventur())
        self.portion = Rezepturartikel.objects.create(
            name="Portion W", basiseinheit=einheit("Stück(e)")
        )
        Rezepturkomponente.objects.create(
            rezepturartikel=self.portion,
            ware=self.jahresware,
            menge_in_basiseinheit=Decimal("1"),
        )
        Erwartung.objects.create(ort=self.theke, artikel=self.portion)

    def test_nur_jahreszutaten_heisst_keine_zeile_im_monat(self):
        self.assertEqual(self.inventur().zaehlpositionen_anlegen(), 0)

    def test_bei_der_jahresinventur_bekommt_er_seine_zeile(self):
        self.assertEqual(self.inventur(jahr=True).zaehlpositionen_anlegen(), 1)

    def test_eine_monatliche_zutat_genuegt(self):
        becher = self.ware("Ware B")
        self.artikel(becher)
        Rezepturkomponente.objects.create(
            rezepturartikel=self.portion, ware=becher, menge_in_basiseinheit=Decimal("1")
        )
        self.assertEqual(self.inventur().zaehlpositionen_anlegen(), 1)


class RezepturartikelMitInaktiverZutat(InventurGrundlagen):
    """A recipe article hangs on its ingredients. If one is inactive, so is it."""

    def setUp(self):
        self.karton = self.ware("Posten N")
        self.artikel_karton = self.artikel(self.karton)
        self.portion = Rezepturartikel.objects.create(
            name="Portion N", basiseinheit=einheit("Stück(e)")
        )
        Rezepturkomponente.objects.create(
            rezepturartikel=self.portion, ware=self.karton, menge_in_basiseinheit=Decimal("1")
        )
        Erwartung.objects.create(ort=self.theke, artikel=self.portion)

    def test_bekommt_keine_zeile_bleibt_aber_erwartet(self):
        """Like an inactive house article: no row by itself, addable in the app."""
        self.artikel_karton.deaktivieren()
        inventur = self.inventur()
        self.assertEqual(inventur.zaehlpositionen_anlegen(), 0)
        self.assertTrue(Erwartung.objects.filter(artikel=self.portion).exists())

    def test_mit_aktiver_zutat_bekommt_er_seine_zeile(self):
        inventur = self.inventur()
        self.assertEqual(inventur.zaehlpositionen_anlegen(), 1)

    def test_erwartung_geht_mit_inaktiver_zutat_nicht_aber_mit_eingestellter(self):
        self.artikel_karton.deaktivieren()
        Erwartung(ort=self.lager, artikel=self.portion).full_clean()
        self.artikel_karton.einstellen()
        with self.assertRaises(ValidationError):
            Erwartung(ort=self.lager, artikel=self.portion).full_clean()


class ZaehlpositionUmrechnung(InventurGrundlagen):
    """What the input means in the product's base unit."""

    def setUp(self):
        self.inv = self.inventur()

    def position(self, artikel, gebinde=None, menge=None):
        return Zaehlposition.objects.create(
            inventur=self.inv,
            ort=self.theke,
            artikel=artikel,
            gebinde=gebinde,
            eingabemenge=menge,
        )

    def wiegen(self, position, brutto, behaelter):
        return Wiegung.objects.create(
            zaehlposition=position, brutto_gramm=Decimal(brutto), behaelter=behaelter
        )

    def test_gebinde_mal_faktor(self):
        cola = self.ware("Artikel C 0,33")
        kasten = Gebinde.objects.create(
            ware=cola, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        position = self.position(self.artikel(cola), kasten, Decimal("3"))
        self.assertEqual(position.menge_in_basiseinheit(), Decimal("60"))
        self.assertFalse(position.wird_gewogen)

    def test_abwiegen_zieht_die_tara_ab(self):
        nachos = self.ware("Artikel N", "kg", abwiegbar=True)
        schale = Behaelter.objects.create(name="Schale", tara=Decimal("250"))
        position = self.position(self.artikel(nachos))
        self.wiegen(position, "2750", schale)

        self.assertEqual(position.menge_in_basiseinheit(), Decimal("2.5"))
        self.assertTrue(position.wird_gewogen)

    def test_jede_wiegung_bringt_ihre_eigene_tara_mit(self):
        """Three bowls subtract tare three times: the whole reason for rows of their own."""
        nachos = self.ware("Artikel N", "kg", abwiegbar=True)
        schale = Behaelter.objects.create(name="Schale", tara=Decimal("250"))
        eimer = Behaelter.objects.create(name="Eimer", tara=Decimal("300"))
        position = self.position(self.artikel(nachos))

        self.wiegen(position, "1250", schale)  # 1000 g
        self.wiegen(position, "900", eimer)  # 600 g
        self.wiegen(position, "1100", schale)  # 850 g

        self.assertEqual(position.menge_in_basiseinheit(), Decimal("2.45"))

    def test_ohne_wiegung_ist_nichts_gezaehlt(self):
        """A zero would be the claim that the shelf is empty."""
        nachos = self.ware("Artikel N", "kg", abwiegbar=True)
        position = self.position(self.artikel(nachos))
        self.assertIsNone(position.menge_in_basiseinheit())
        self.assertFalse(position.ist_gezaehlt)

    def test_wiegung_nur_wo_gewogen_wird(self):
        cola = self.ware("Artikel C 0,33")
        kasten = Gebinde.objects.create(
            ware=cola, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        position = self.position(self.artikel(cola), kasten)
        wiegung = Wiegung(
            zaehlposition=position,
            brutto_gramm=Decimal("500"),
            behaelter=Behaelter.ohne_behaelter(),
        )
        with self.assertRaises(ValidationError):
            wiegung.full_clean()

    def test_ohne_eingabe_keine_menge(self):
        cola = self.ware("Artikel C 0,33")
        kasten = Gebinde.objects.create(
            ware=cola, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        self.assertIsNone(self.position(self.artikel(cola), kasten).menge_in_basiseinheit())

    def test_ort_muss_zum_haus_der_inventur_passen(self):
        fremd = Ort.objects.create(
            name="Theke",
            bereich=Bereich.objects.create(name="Theke", haus=Haus.objects.create(name="Kino Süd")),
        )
        cola = self.ware("Artikel C 0,33")
        position = Zaehlposition(inventur=self.inv, ort=fremd, artikel=self.artikel(cola))
        with self.assertRaises(ValidationError):
            position.full_clean()

    def test_artikel_muss_zum_haus_der_inventur_passen(self):
        """Otherwise closing would freeze a quantity onto a foreign house article."""
        cola = self.ware("Artikel C 0,33")
        fremd = self.artikel(cola, haus=Haus.objects.create(name="Kino Süd"))
        kasten = Gebinde.objects.create(
            ware=cola, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        position = Zaehlposition(
            inventur=self.inv, ort=self.theke, artikel_id=fremd.pk, gebinde=kasten
        )
        with self.assertRaises(ValidationError) as fehler:
            position.full_clean()
        self.assertIn("artikel", fehler.exception.message_dict)

    def test_rezepturartikel_gehoert_keinem_haus(self):
        """It has none. Only what a house carries is checked."""
        nachos = self.ware("Artikel N", "kg")
        self.artikel(nachos)
        portion = Rezepturartikel.objects.create(name="Portion N", basiseinheit=einheit("Stück(e)"))
        Rezepturkomponente.objects.create(
            rezepturartikel=portion,
            ware=nachos,
            menge_in_basiseinheit=Decimal("0.140"),
        )
        Zaehlposition(inventur=self.inv, ort=self.theke, artikel_id=portion.pk).full_clean()

    def test_ohne_gebinde_nur_was_gewogen_wird(self):
        position = Zaehlposition(
            inventur=self.inv, ort=self.theke, artikel=self.artikel(self.ware("Artikel C 0,33"))
        )
        with self.assertRaises(ValidationError) as fehler:
            position.full_clean()
        self.assertEqual(
            fehler.exception.message_dict,
            {
                "gebinde": [
                    "„Artikel C 0,33“ wird nicht gewogen. Ohne Gebinde lässt sich hier nichts "
                    "zählen."
                ]
            },
        )

    def test_ein_rezepturartikel_zaehlt_nicht_in_gebinden(self):
        cola = self.ware("Artikel C 0,33")
        kasten = Gebinde.objects.create(
            ware=cola, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        portion = Rezepturartikel.objects.create(name="Portion N", basiseinheit=einheit("Stück(e)"))
        position = Zaehlposition(
            inventur=self.inv, ort=self.theke, artikel_id=portion.pk, gebinde=kasten
        )
        with self.assertRaises(ValidationError) as fehler:
            position.full_clean()
        self.assertEqual(
            fehler.exception.message_dict,
            {"gebinde": ["Rezepturartikel werden nicht in Gebinden gezählt."]},
        )

    def test_die_menge_muss_in_ein_ergebnis_passen(self):
        """99,999,999 crates of 20 are two billion bottles: the result keeps nine digits."""
        cola = self.ware("Artikel C 0,33")
        kasten = Gebinde.objects.create(
            ware=cola, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        artikel = self.artikel(cola)
        position = Zaehlposition(
            inventur=self.inv,
            ort=self.theke,
            artikel=artikel,
            gebinde=kasten,
            eingabemenge=Decimal("99999999"),
        )
        with self.assertRaises(ValidationError) as fehler:
            position.full_clean()
        self.assertEqual(
            fehler.exception.message_dict,
            {
                "eingabemenge": [
                    "Das wären 1999999980 Stk., mehr als ein Ergebnis fasst. Bitte die Zahl prüfen."
                ]
            },
        )

        position.eingabemenge = Decimal("49999999")  # 999,999,980 still fit
        position.full_clean()

    def test_gebinde_muss_zum_artikel_gehoeren(self):
        cola = self.ware("Artikel C 0,33")
        fremdes = Gebinde.objects.create(
            ware=self.ware("Artikel F 0,33"),
            name="Kasten",
            faktor_in_basiseinheit=Decimal("20"),
        )
        position = Zaehlposition(
            inventur=self.inv, ort=self.theke, artikel=self.artikel(cola), gebinde=fremdes
        )
        with self.assertRaises(ValidationError):
            position.full_clean()

    def test_zweite_zeile_ohne_gebinde_wird_abgewiesen(self):
        """NULL counts as different from everything in the unique index. Hence the partial index."""
        nachos = self.ware("Artikel N", "kg", abwiegbar=True)
        artikel = self.artikel(nachos)
        self.position(artikel)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.position(artikel)


class HausZaehltAnders(InventurGrundlagen):
    """The product is carried in kg, the house counts in pieces of 1 kg each."""

    def setUp(self):
        self.inv = self.inventur()
        self.kaffee = self.ware("Ware K", "kg", abwiegbar=True)
        self.karton = Gebinde.objects.create(
            ware=self.kaffee, name="Karton", faktor_in_basiseinheit=Decimal("6")
        )
        self.bohnen = self.artikel(
            self.kaffee, basiseinheit=einheit("Stück(e)"), faktor_zur_ware=Decimal("1")
        )

    def position(self, **abweichend) -> Zaehlposition:
        return Zaehlposition.objects.create(
            inventur=self.inv, ort=self.theke, artikel=self.bohnen, **abweichend
        )

    def test_das_gebinde_rechnet_in_die_einheit_des_hauses(self):
        """Two boxes of 6 kg are 12 pieces for the house, not 12 kg."""
        position = self.position(gebinde=self.karton, eingabemenge=Decimal("2"))
        self.assertEqual(position.menge_in_basiseinheit(), Decimal("12"))

    def test_die_waage_rechnet_in_die_einheit_des_hauses(self):
        position = self.position()
        Wiegung.objects.create(
            zaehlposition=position,
            brutto_gramm=Decimal("2500"),
            behaelter=Behaelter.objects.create(name="Schale", tara=Decimal("500")),
        )
        self.assertEqual(position.menge_in_basiseinheit(), Decimal("2"))

    def test_die_rezeptur_loest_in_die_einheit_des_hauses_auf(self):
        """A portion needs 0.5 kg. Four portions are 2 pieces for the house."""
        portion = Rezepturartikel.objects.create(name="Portion K", basiseinheit=einheit("Stück(e)"))
        Rezepturkomponente.objects.create(
            rezepturartikel=portion, ware=self.kaffee, menge_in_basiseinheit=Decimal("0.5")
        )
        Zaehlposition.objects.create(
            inventur=self.inv, ort=self.theke, artikel=portion, eingabemenge=Decimal("4")
        )
        self.assertEqual(self.inv.mengen_je_hausartikel()[self.bohnen.pk], Decimal("2"))

    def test_das_ergebnis_steht_in_der_einheit_des_hauses(self):
        """That is how the target comes from WinTICKET, that is how the result goes back there."""
        self.position(gebinde=self.karton, eingabemenge=Decimal("2"))
        Sollbestand.objects.create(
            inventur=self.inv, hausartikel=self.bohnen, menge_basiseinheit=Decimal("10")
        )
        self.inv.zaehlung_starten()
        self.inv.abschliessen()
        ergebnis = self.inv.ergebnisse.get()
        self.assertEqual(ergebnis.gesamtmenge_basiseinheit, Decimal("12"))
        self.assertEqual(ergebnis.differenz, Decimal("2"))


class InventurAbschluss(InventurGrundlagen):
    """Freezing: results stay, working data vanishes."""

    def setUp(self):
        self.inv = self.inventur()
        self.cola = self.ware("Artikel C 0,33")
        self.kasten = Gebinde.objects.create(
            ware=self.cola, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        self.einzeln = Gebinde.objects.create(
            ware=self.cola, name="Einzeln", faktor_in_basiseinheit=Decimal("1")
        )
        self.artikel_cola = self.artikel(self.cola)

    def zaehlen(self, ort, gebinde, menge):
        return Zaehlposition.objects.create(
            inventur=self.inv,
            ort=ort,
            artikel=self.artikel_cola,
            gebinde=gebinde,
            eingabemenge=Decimal(menge),
        )

    def test_eingefroren_wird_halb_auf_gerundet(self):
        """Half up as on the check list, and the same on SQLite and PostgreSQL.

        Left to the database, SQLite rounds 1.2345 half to even and
        PostgreSQL half away from zero. Until 10/2026 a weighing of 1234.5 g
        froze as 1.234 kg locally and as 1.235 kg in production.
        """
        artikel = self.artikel(self.ware("Ware N", "kg"))
        zeilen = self.inv._ergebnisse(
            {artikel.pk: Decimal("1.2345")}, {artikel.pk: Decimal("1.0005")}, timezone.now()
        )
        Inventurergebnis.objects.bulk_create(zeilen)

        ergebnis = self.inv.ergebnisse.get()
        self.assertEqual(ergebnis.gesamtmenge_basiseinheit, Decimal("1.235"))
        self.assertEqual(ergebnis.sollbestand, Decimal("1.001"))
        self.assertEqual(ergebnis.differenz, Decimal("0.234"))

    def test_mengen_werden_ueber_orte_und_gebinde_summiert(self):
        """The reference example: 9 crates of 20 + 9 single, 20 at the counter."""
        self.zaehlen(self.lager, self.kasten, "9")
        self.zaehlen(self.lager, self.einzeln, "9")
        self.zaehlen(self.theke, self.einzeln, "20")

        self.assertEqual(self.inv.mengen_je_hausartikel(), {self.artikel_cola.pk: Decimal("209")})

    def test_abschluss_friert_ein_und_raeumt_auf(self):
        self.zaehlen(self.lager, self.kasten, "2")
        Sollbestand.objects.create(
            inventur=self.inv, hausartikel=self.artikel_cola, menge_basiseinheit=Decimal("38")
        )

        # An open question about the target file. After closing nobody answers it any more.
        Sollzeile.objects.create(
            inventur=self.inv,
            artikelnummer="999999",
            menge_basiseinheit=Decimal("1"),
            grund=Sollgrund.UNBEKANNT,
        )

        self.assertEqual(self.inv.abschliessen(), 1)
        self.inv.refresh_from_db()

        ergebnis = self.inv.ergebnisse.get()
        self.assertEqual(ergebnis.gesamtmenge_basiseinheit, Decimal("40"))
        self.assertEqual(ergebnis.sollbestand, Decimal("38"))
        self.assertEqual(ergebnis.differenz, Decimal("2"))
        self.assertEqual(self.inv.status, Inventurstatus.ABGESCHLOSSEN)
        self.assertIsNotNone(self.inv.abgeschlossen_am)
        self.assertFalse(self.inv.zaehlpositionen.exists())
        self.assertFalse(self.inv.sollbestaende.exists())
        self.assertFalse(self.inv.sollzeilen.exists(), "Die offenen Sollzeilen stehen noch da.")

    def test_artikel_mit_soll_aber_ohne_zaehlung_wird_zu_null(self):
        """The interesting case: the product is missing entirely."""
        Sollbestand.objects.create(
            inventur=self.inv, hausartikel=self.artikel_cola, menge_basiseinheit=Decimal("20")
        )
        self.inv.abschliessen()

        ergebnis = self.inv.ergebnisse.get()
        self.assertEqual(ergebnis.gesamtmenge_basiseinheit, Decimal("0"))
        self.assertEqual(ergebnis.differenz, Decimal("-20"))

    def test_ohne_soll_bleibt_die_differenz_offen(self):
        self.zaehlen(self.lager, self.kasten, "1")
        self.inv.abschliessen()

        ergebnis = self.inv.ergebnisse.get()
        self.assertEqual(ergebnis.gesamtmenge_basiseinheit, Decimal("20"))
        self.assertIsNone(ergebnis.sollbestand)
        self.assertIsNone(ergebnis.differenz)

    def test_ungezaehlte_positionen_zaehlen_nicht_mit(self):
        self.zaehlen(self.lager, self.kasten, "1")
        Zaehlposition.objects.create(
            inventur=self.inv, ort=self.theke, artikel=self.artikel_cola, gebinde=self.einzeln
        )
        self.inv.abschliessen()
        self.assertEqual(self.inv.ergebnisse.get().gesamtmenge_basiseinheit, Decimal("20"))

    def test_veralteter_stand_bricht_sauber_ab(self):
        """Two requests with the same open state: the second gets an answer instead of a 500."""
        veraltet = Inventur.objects.get(pk=self.inv.pk)
        self.inv.abschliessen()
        with self.assertRaises(ValidationError):
            veraltet.abschliessen()

    def test_zweimal_abschliessen_geht_nicht(self):
        self.inv.abschliessen()
        with self.assertRaises(ValidationError):
            self.inv.abschliessen()

    def test_wer_gezaehlt_hat_bleibt_nicht_gespeichert(self):
        """Nobody counts any more, and the assignment would only say who once could have."""
        zaehlerin = CustomUser.objects.create_user(
            username="zaehlerin", password="geheim", email="z@example.org"
        )
        self.inv.zaehlende.add(zaehlerin)

        self.inv.abschliessen()

        self.assertFalse(self.inv.zaehlende.exists())
        self.assertFalse(self.inv.darf_zaehlen(zaehlerin))


class RezepturartikelAufloesen(InventurGrundlagen):
    """A recipe article is distributed over its recipe at closing."""

    def setUp(self):
        self.inv = self.inventur()
        self.karton = self.ware("Posten N")
        self.nachos = self.ware("Artikel N", "kg")
        self.artikel_karton = self.artikel(self.karton)
        self.artikel_nachos = self.artikel(self.nachos)

        self.portion = Rezepturartikel.objects.create(
            name="Portion N", basiseinheit=einheit("Stück(e)")
        )
        Rezepturkomponente.objects.create(
            rezepturartikel=self.portion,
            ware=self.karton,
            menge_in_basiseinheit=Decimal("1"),
        )
        Rezepturkomponente.objects.create(
            rezepturartikel=self.portion,
            ware=self.nachos,
            menge_in_basiseinheit=Decimal("0.14"),
        )

    def test_portionen_landen_auf_beiden_hausartikeln(self):
        Zaehlposition.objects.create(
            inventur=self.inv, ort=self.theke, artikel=self.portion, eingabemenge=Decimal("10")
        )
        self.assertEqual(
            self.inv.mengen_je_hausartikel(),
            {self.artikel_karton.pk: Decimal("10"), self.artikel_nachos.pk: Decimal("1.4")},
        )

    def test_gezaehlte_ware_und_portionen_addieren_sich(self):
        karton_gebinde = Gebinde.objects.create(
            ware=self.karton, name="Stück(e)", faktor_in_basiseinheit=Decimal("1")
        )
        Zaehlposition.objects.create(
            inventur=self.inv,
            ort=self.lager,
            artikel=self.artikel_karton,
            gebinde=karton_gebinde,
            eingabemenge=Decimal("5"),
        )
        Zaehlposition.objects.create(
            inventur=self.inv, ort=self.theke, artikel=self.portion, eingabemenge=Decimal("10")
        )
        self.assertEqual(self.inv.mengen_je_hausartikel()[self.artikel_karton.pk], Decimal("15"))

    def test_anteile_in_prozent_landen_in_litern_auf_dem_hausartikel(self):
        """Syrups in litres, weighed as a mix: 60 % of 1000 g at 1250 g/l are 0.48 l."""
        sirup_a, sirup_b = self.ware("Sirup A", "Liter"), self.ware("Sirup B", "Liter")
        sirup_a.umrechnungen_setzen({Dimension.MASSE: Decimal("1250")})
        sirup_b.umrechnungen_setzen({Dimension.MASSE: Decimal("1000")})
        a_a, a_b = self.artikel(sirup_a), self.artikel(sirup_b)
        mix = Rezepturartikel.objects.create(
            name="Sirupmischung", basiseinheit=einheit("g"), anteilig=True
        )
        for ware, anteil in ((sirup_a, "60"), (sirup_b, "40")):
            Rezepturkomponente.objects.create(
                rezepturartikel=mix, ware=ware, menge_in_basiseinheit=Decimal(anteil)
            )
        kanne = Behaelter.objects.create(name="Kanne", tara=Decimal("500"))
        position = Zaehlposition.objects.create(inventur=self.inv, ort=self.theke, artikel=mix)
        Wiegung.objects.create(
            zaehlposition=position, brutto_gramm=Decimal("1500"), behaelter=kanne
        )

        self.assertEqual(
            self.inv.mengen_je_hausartikel(),
            {a_a.pk: Decimal("0.48"), a_b.pk: Decimal("0.4")},
        )

    def test_gewogener_rezepturartikel_verteilt_das_nettogewicht(self):
        """A mix of two products, weighed once."""
        gramm = einheit("g")
        erdnuesse, cashews = self.ware("Ware E", "g"), self.ware("Posten C", "g")
        a_erd, a_cash = self.artikel(erdnuesse), self.artikel(cashews)
        mix = Rezepturartikel.objects.create(name="Mischung N", basiseinheit=gramm)
        eimer = Behaelter.objects.create(name="Eimer", tara=Decimal("300"))
        mix.behaelter.set([eimer])
        for ware, anteil in ((erdnuesse, "0.6"), (cashews, "0.4")):
            Rezepturkomponente.objects.create(
                rezepturartikel=mix, ware=ware, menge_in_basiseinheit=Decimal(anteil)
            )

        position = Zaehlposition.objects.create(inventur=self.inv, ort=self.theke, artikel=mix)
        Wiegung.objects.create(
            zaehlposition=position, brutto_gramm=Decimal("1300"), behaelter=eimer
        )
        mengen = self.inv.mengen_je_hausartikel()
        self.assertEqual(mengen[a_erd.pk], Decimal("600.0"))
        self.assertEqual(mengen[a_cash.pk], Decimal("400.0"))

    def test_zwei_eimer_nussmix_zaehlen_zweimal_tara_ab(self):
        """The same case as with raw goods: the tare belongs to the weighing."""
        gramm = einheit("g")
        erdnuesse, cashews = self.ware("Ware E", "g"), self.ware("Posten C", "g")
        a_erd, a_cash = self.artikel(erdnuesse), self.artikel(cashews)
        mix = Rezepturartikel.objects.create(name="Mischung N", basiseinheit=gramm)
        eimer = Behaelter.objects.create(name="Eimer", tara=Decimal("300"))
        mix.behaelter.set([eimer])
        for ware, anteil in ((erdnuesse, "0.6"), (cashews, "0.4")):
            Rezepturkomponente.objects.create(
                rezepturartikel=mix, ware=ware, menge_in_basiseinheit=Decimal(anteil)
            )

        position = Zaehlposition.objects.create(inventur=self.inv, ort=self.theke, artikel=mix)
        Wiegung.objects.create(
            zaehlposition=position, brutto_gramm=Decimal("1300"), behaelter=eimer
        )
        Wiegung.objects.create(zaehlposition=position, brutto_gramm=Decimal("800"), behaelter=eimer)

        # 1000 g + 500 g net, distributed 60/40.
        mengen = self.inv.mengen_je_hausartikel()
        self.assertEqual(mengen[a_erd.pk], Decimal("900.0"))
        self.assertEqual(mengen[a_cash.pk], Decimal("600.0"))

    def test_inaktiver_hausartikel_nimmt_den_anteil_auf(self):
        """Inactive holds number and product. The share has a place, and the clarification asks."""
        self.artikel_nachos.deaktivieren()
        Zaehlposition.objects.create(
            inventur=self.inv, ort=self.theke, artikel=self.portion, eingabemenge=Decimal("10")
        )
        self.assertEqual(
            self.inv.mengen_je_hausartikel(),
            {self.artikel_karton.pk: Decimal("10"), self.artikel_nachos.pk: Decimal("1.4")},
        )

    def test_eingestellter_hausartikel_laesst_den_anteil_verfallen(self):
        """Discontinued means: the product is no longer carried in the house and takes nothing."""
        self.artikel_nachos.einstellen()
        Zaehlposition.objects.create(
            inventur=self.inv, ort=self.theke, artikel=self.portion, eingabemenge=Decimal("10")
        )
        self.assertEqual(self.inv.mengen_je_hausartikel(), {self.artikel_karton.pk: Decimal("10")})


class WareWechselt(InventurGrundlagen):
    """If a house article gets another product, the packs of the old one no longer belong to it."""

    def setUp(self):
        self.cola = self.ware("Artikel C 0,33")
        self.zero = self.ware("Artikel C Zero")
        self.kasten = Gebinde.objects.create(
            ware=self.cola, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        self.kiste = Gebinde.objects.create(
            ware=self.zero, name="Kiste", faktor_in_basiseinheit=Decimal("24")
        )
        self.artikel_cola = self.artikel(self.cola)
        erwartung = Erwartung.objects.create(ort=self.theke, artikel=self.artikel_cola)
        self.vorgabe = ErwartungGebinde.objects.create(erwartung=erwartung, gebinde=self.kasten)
        self.inv = self.inventur()
        self.inv.zaehlpositionen_anlegen()

    def wechseln(self, **kwargs):
        self.artikel_cola.ware = self.zero
        self.artikel_cola.save(**kwargs)

    def test_vorgabe_der_alten_ware_faellt_weg(self):
        """Otherwise the next inventory would create a row with a foreign factor."""
        self.wechseln()
        self.assertFalse(ErwartungGebinde.objects.filter(pk=self.vorgabe.pk).exists())
        self.assertTrue(Erwartung.objects.filter(artikel=self.artikel_cola).exists())

    def test_ungezaehlte_zeile_der_alten_ware_wird_als_geloescht_markiert(self):
        position = self.inv.zaehlpositionen.get()
        vorher = position.zuletzt_geaendert
        self.wechseln()
        position.refresh_from_db()
        self.assertFalse(position.aktiv)
        self.assertGreater(position.zuletzt_geaendert, vorher)

    def test_gezaehlte_zeile_bleibt(self):
        """Counted is counted. The check list shows them."""
        self.inv.zaehlpositionen.update(eingabemenge=Decimal("2"))
        self.wechseln()
        self.assertTrue(self.inv.zaehlpositionen.get().aktiv)

    def test_gebinde_der_neuen_ware_bleiben_unberuehrt(self):
        erwartung = Erwartung.objects.get(artikel=self.artikel_cola)
        # A default of the new product, as the form could create it.
        Hausartikel.objects.filter(pk=self.artikel_cola.pk).update(ware=self.zero)
        neu = ErwartungGebinde.objects.create(erwartung=erwartung, gebinde=self.kiste)
        self.artikel_cola.refresh_from_db()
        self.artikel_cola.save()
        self.assertTrue(ErwartungGebinde.objects.filter(pk=neu.pk).exists())

    def test_ohne_geschriebene_ware_passiert_nichts(self):
        """'update_fields' without 'ware' does not write the change. Then everything stays."""
        self.wechseln(update_fields=["name"])
        self.assertTrue(ErwartungGebinde.objects.filter(pk=self.vorgabe.pk).exists())


class SollbestandTests(InventurGrundlagen):
    def setUp(self):
        self.inv = self.inventur()
        self.artikel_cola = self.artikel(self.ware("Artikel C 0,33"))

    def test_je_inventur_und_artikel_nur_einer(self):
        Sollbestand.objects.create(
            inventur=self.inv, hausartikel=self.artikel_cola, menge_basiseinheit=Decimal("5")
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Sollbestand.objects.create(
                inventur=self.inv, hausartikel=self.artikel_cola, menge_basiseinheit=Decimal("6")
            )

    def test_artikel_muss_zum_haus_gehoeren(self):
        fremd = self.artikel(self.ware("Artikel F 0,33"), haus=Haus.objects.create(name="Kino Süd"))
        satz = Sollbestand(inventur=self.inv, hausartikel=fremd, menge_basiseinheit=Decimal("5"))
        with self.assertRaises(ValidationError):
            satz.full_clean()


class NotizTests(InventurGrundlagen):
    """The note per article: the same house rule as on the target stock."""

    def setUp(self):
        self.inv = self.inventur()

    def test_artikel_muss_zum_haus_gehoeren(self):
        """Checked is the rule on the model, not only the path through the form.

        Through the check page only the own house comes in anyway. But the rule
        also has to hold where someone creates past it.
        """
        fremd = self.artikel(self.ware("Artikel F 0,33"), haus=Haus.objects.create(name="Kino Süd"))
        notiz = Inventurnotiz(inventur=self.inv, hausartikel=fremd, text="geht nicht")
        with self.assertRaises(ValidationError) as fehler:
            notiz.full_clean()
        self.assertIn("hausartikel", fehler.exception.message_dict)

    def test_ignorieren_raeumt_die_notiz_ab(self):
        """Otherwise closing would freeze a row for something that is not a product."""
        artikel = self.artikel(self.ware("Artikel P"))
        Inventurnotiz.objects.create(inventur=self.inv, hausartikel=artikel, text="Was ist das?")
        artikel.ignorieren()
        self.assertFalse(Inventurnotiz.objects.filter(hausartikel=artikel).exists())

    def test_je_inventur_und_artikel_nur_eine(self):
        artikel = self.artikel(self.ware("Artikel C 0,33"))
        Inventurnotiz.objects.create(inventur=self.inv, hausartikel=artikel, text="erste")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Inventurnotiz.objects.create(inventur=self.inv, hausartikel=artikel, text="zweite")


class ErgebnisTests(InventurGrundlagen):
    def test_je_inventur_und_artikel_nur_eines(self):
        inv = self.inventur()
        artikel = self.artikel(self.ware("Artikel C 0,33"))

        Inventurergebnis.objects.create(
            inventur=inv,
            hausartikel=artikel,
            gesamtmenge_basiseinheit=Decimal("1"),
            eingefroren_am=timezone.now(),
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Inventurergebnis.objects.create(
                inventur=inv,
                hausartikel=artikel,
                gesamtmenge_basiseinheit=Decimal("2"),
                eingefroren_am=timezone.now(),
            )
