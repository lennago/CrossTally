"""A conflict that keeps the row's unit keeps its factor, and the frozen results their numbers.

The plan asks for the factor against the file's unit. Until 10/2026 a
conflict that adopted only the product group was refused until a factor was
typed for the file's unit (kg per ml), and that factor then stood on the kept
unit (kg per l). Every frozen result in that unit followed it: 10 kg became
0.01 kg in the evaluations and the PDFs of past inventories.
"""

from datetime import date
from decimal import Decimal

import pandas as pd
from django.test import TestCase

from django_project.testing import inventur_anlegen
from inventur.models import Sollbestand
from organisation.models import Bereich, Haus, Ort

from .forms import ImportFormSet
from .models import Basiseinheit, Gebinde, Hausartikel, Ware, Warengruppe
from .services import AKTUALISIEREN, WARENGRUPPE, als_daten, ausfuehren, planen
from .services.apply import faktor_behalten

SPALTEN = ["artikelnummer", "name", "warengruppe", "gebinde", "anzahl", "einheit", "haus"]


def einheit(kennung: str) -> Basiseinheit:
    return Basiseinheit.objects.get(kennung=kennung)


class Grundlage(TestCase):
    """Ware N in kg. Kino Nord counts it in litres, a litre weighs a kilo: 10 l at closing."""

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        cls.ort = Ort.objects.create(
            name="Lager", bereich=Bereich.objects.create(name="Lager", haus=cls.haus)
        )
        cls.ware = Ware.objects.create(name="Ware N", basiseinheit=einheit("KILOGRAMM"))
        cls.kanister = Gebinde.objects.create(
            ware=cls.ware, name="Kanister", faktor_in_basiseinheit=Decimal("10")
        )
        cls.artikel = Hausartikel.objects.create(
            haus=cls.haus,
            ware=cls.ware,
            warengruppe=cls.gruppe,
            artikelnummer="101001",
            name="Ware N Liter",
            basiseinheit=einheit("LITER"),
            faktor_zur_ware=Decimal("1"),
        )
        inventur = inventur_anlegen(cls.haus, stichtag=date(2026, 1, 31))
        inventur.zaehlpositionen.create(
            ort=cls.ort, artikel=cls.artikel, gebinde=cls.kanister, eingabemenge=Decimal("1")
        )
        Sollbestand.objects.create(
            inventur=inventur, hausartikel=cls.artikel, menge_basiseinheit=Decimal("10")
        )
        inventur.zaehlung_starten()
        inventur.abschliessen()
        cls.ergebnis = inventur.ergebnisse.get()

    def plan(self):
        """The file names the article in millilitres and in another product group."""
        tabelle = pd.DataFrame(
            [["101001", "Ware N Liter", "Warengruppe H", "", 0, "MILLILITER", "Kino Nord"]],
            columns=SPALTEN,
        )
        (satz,), _meldungen = planen(tabelle, self.haus)
        self.assertEqual(sorted(satz.abweichungen), ["einheit", "warengruppe"])
        return satz

    def assertBleibt(self):
        """The frozen result still says 10 l worth 10 kg, the row keeps litre and factor."""
        self.ergebnis.refresh_from_db()
        self.assertEqual(self.ergebnis.gesamtmenge_basiseinheit, Decimal("10"))
        self.assertEqual(
            (self.ergebnis.einheit, self.ergebnis.faktor_zur_ware), (einheit("LITER"), Decimal("1"))
        )
        artikel = Hausartikel.objects.get(pk=self.artikel.pk)
        self.assertEqual(artikel.warengruppe.name, "Warengruppe H")
        self.assertEqual((artikel.basiseinheit, artikel.faktor_zur_ware), (einheit("LITER"), 1))


class NurDieWarengruppe(Grundlage):
    def test_der_getippte_faktor_der_datei_landet_nicht_auf_dem_liter(self):
        """Typed as the old plan demanded it: 0,001 kg per ml, which is right for ml."""
        satz = self.plan()

        bericht = ausfuehren(
            [
                als_daten(satz)
                | {
                    "vorgehen": AKTUALISIEREN,
                    "uebernehmen": [WARENGRUPPE],
                    "faktor_zur_ware": Decimal("0.001"),
                }
            ],
            self.haus,
        )

        self.assertEqual(bericht.artikel_geaendert, ["101001 Ware N Liter"])
        self.assertBleibt()

    def test_der_plan_fragt_nicht_nach_dem_faktor(self):
        satz = self.plan()
        daten = {
            "plan-TOTAL_FORMS": "1",
            "plan-INITIAL_FORMS": "1",
            "plan-MIN_NUM_FORMS": "0",
            "plan-MAX_NUM_FORMS": "5000",
        }
        for feld, wert in als_daten(satz).items():
            if feld == "uebernehmen":
                continue
            if feld == "ware":
                eintrag = wert.pk
            elif feld in ("gebinde", "gebinde_datei"):
                eintrag = ""
            else:
                eintrag = "" if wert is None else wert
            daten[f"plan-0-{feld}"] = eintrag
        daten |= {
            "plan-0-vorgehen": AKTUALISIEREN,
            "plan-0-uebernehmen": [WARENGRUPPE],
            "plan-0-abweichungen": ",".join(satz.abweichungen),
        }
        formset = ImportFormSet(daten, prefix="plan")
        formset.haus = self.haus

        self.assertTrue(formset.is_valid(), formset.errors)
        ausfuehren([form.cleaned_data for form in formset.forms], self.haus)
        self.assertBleibt()


class FaktorBehalten(Grundlage):
    """The conversion itself, for the cases the plan does not reach as easily."""

    def test_eine_andere_ware_in_gramm_rechnet_den_eigenen_faktor_um(self):
        in_gramm = Ware.objects.create(name="Ware N in Gramm", basiseinheit=einheit("GRAMM"))
        self.assertEqual(
            faktor_behalten(
                self.artikel, einheit("KILOGRAMM"), in_gramm, einheit("MILLILITER"), None
            ),
            Decimal("1000"),
        )

    def test_ohne_eigenen_faktor_zaehlt_der_getippte_umgerechnet(self):
        ohne = Hausartikel(basiseinheit=einheit("LITER"), faktor_zur_ware=None)
        self.assertEqual(
            faktor_behalten(
                ohne, einheit("KILOGRAMM"), self.ware, einheit("MILLILITER"), Decimal("0.001")
            ),
            Decimal("1"),
        )

    def test_was_die_spalte_nicht_fasst_gilt_nicht(self):
        ohne = Hausartikel(basiseinheit=einheit("LITER"), faktor_zur_ware=None)
        self.assertIsNone(
            faktor_behalten(
                ohne, einheit("KILOGRAMM"), self.ware, einheit("MILLILITER"), Decimal("999999")
            )
        )

    def test_ueber_eine_andere_dimension_weiss_es_niemand(self):
        stueck = Hausartikel(basiseinheit=einheit("STUECK"), faktor_zur_ware=None)
        self.assertIsNone(
            faktor_behalten(
                stueck, einheit("KILOGRAMM"), self.ware, einheit("MILLILITER"), Decimal("0.001")
            )
        )
