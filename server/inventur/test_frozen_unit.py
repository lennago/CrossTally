"""A frozen result keeps the unit the house counted in, and the unit waits for closing.

Until 10/2026 a result held only numbers and was read in the article's unit
of the day. A house that changed its unit afterwards found "10 kg" on the
sheet where it had counted 10 pieces, and the report counted double. The
target of an inventory still running read the same way in the new unit.
"""

import io
import json
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

import pandas as pd
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from django_project.backup_upgrade import umschreiben
from django_project.testing import AngemeldeteSeiten, inventur_anlegen, monatsinventur
from katalog.models import Basiseinheit, Gebinde, Hausartikel, Ware, Warengruppe
from katalog.services import AKTUALISIEREN, als_daten, ausfuehren, planen
from katalog.test_dumpdata import alles_loeschen
from katalog.ware_merge import zusammenfuehren
from katalog.ware_unit_change import einheit_wechseln
from organisation.models import Bereich, Haus, Ort

from .evaluation import (
    _zeilen_einer_inventur,
    familien_eines_laufs,
    familien_ueber_inventuren,
    warengruppen_einer_inventur,
)
from .models import Inventur, Inventurergebnis, Inventurnotiz, Sollbestand, Sollgrund, Sollzeile
from .pdf import pdf_html
from .services import ergebniszeilen

SPALTEN = ["artikelnummer", "name", "warengruppe", "gebinde", "anzahl", "einheit", "haus"]


def einheit(name: str) -> Basiseinheit:
    return Basiseinheit.objects.get(name=name)


class Grundlage:
    """Ware N in kg. Kino Nord counts it in pieces of half a kilo, 10 pieces at closing."""

    haus: Haus
    gruppe: Warengruppe
    ort: Ort

    @classmethod
    def grundlage(cls) -> None:
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        bereich = Bereich.objects.create(name="Lager", haus=cls.haus)
        cls.ort = Ort.objects.create(name="Trockenlager", bereich=bereich)

    def aufbauen(self) -> None:
        self.ware = Ware.objects.create(name="Ware N", basiseinheit=einheit("kg"))
        self.karton = Gebinde.objects.create(
            ware=self.ware, name="Karton", faktor_in_basiseinheit=Decimal("5")
        )
        self.artikel = Hausartikel.objects.create(
            haus=self.haus,
            ware=self.ware,
            warengruppe=self.gruppe,
            artikelnummer="101001",
            name="Ware N Stück",
            basiseinheit=einheit("Stück(e)"),
            faktor_zur_ware=Decimal("0.5"),
        )
        self.abgeschlossen = self.abschliessen(date(2026, 1, 31))

    def abschliessen(self, stichtag: date):
        """One box counted (5 kg, so 10 pieces), target 10 pieces."""
        inventur = inventur_anlegen(self.haus, stichtag=stichtag)
        inventur.zaehlpositionen.create(
            ort=self.ort, artikel=self.artikel, gebinde=self.karton, eingabemenge=Decimal("1")
        )
        Sollbestand.objects.create(
            inventur=inventur, hausartikel=self.artikel, menge_basiseinheit=Decimal("10")
        )
        inventur.zaehlung_starten()
        inventur.abschliessen()
        return inventur

    def blatt(self):
        """The sheet of the closed inventory, in the house's unit and in the product's."""
        im_haus = _zeilen_einer_inventur(self.abgeschlossen, im_haus=True)[0]
        in_ware = _zeilen_einer_inventur(self.abgeschlossen)[0]
        return (
            f"{im_haus.ist_text}, Soll {im_haus.soll_text}",
            f"{in_ware.ist_text}, Soll {in_ware.soll_text}",
        )


class Einfrieren(Grundlage, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.grundlage()

    def setUp(self):
        self.aufbauen()

    def test_der_abschluss_haelt_einheit_und_faktor_fest(self):
        ergebnis = self.abgeschlossen.ergebnisse.get()
        self.assertEqual(ergebnis.gesamtmenge_basiseinheit, Decimal("10"))
        self.assertEqual(
            (ergebnis.einheit, ergebnis.faktor_zur_ware), (einheit("Stück(e)"), Decimal("0.5"))
        )
        self.assertEqual(self.blatt(), ("10 Stk., Soll 10 Stk.", "5 kg, Soll 5 kg"))

    def test_ein_spaeterer_wechsel_der_einheit_laesst_das_ergebnis_stehen(self):
        """The house counts in kg from now on. Its January stays 10 pieces, worth 5 kg."""
        self.artikel.basiseinheit = einheit("kg")
        self.artikel.faktor_zur_ware = None
        self.artikel.full_clean()
        self.artikel.save()

        self.assertEqual(self.blatt(), ("10 Stk., Soll 10 Stk.", "5 kg, Soll 5 kg"))
        zeile = ergebniszeilen(self.abgeschlossen)[0]
        self.assertEqual((zeile.ist_text, zeile.einheit), ("10", einheit("Stück(e)")))
        bloecke, _ = familien_ueber_inventuren([self.abgeschlossen])
        (summe,) = [z for block in bloecke for z in block.alle_zeilen if z.ware == self.ware]
        self.assertEqual(summe.ist, Decimal("5"))
        bloecke = familien_eines_laufs([self.abgeschlossen])
        (lauf,) = [z for block in bloecke for z in block.alle_zeilen if z.ware == self.ware]
        self.assertEqual((lauf.ist, lauf.soll), (Decimal("5"), Decimal("5")))
        # The printed sheet, as the inventory management system gets it.
        bloecke = warengruppen_einer_inventur(self.abgeschlossen)
        html = pdf_html(
            "pdf/inventur.html",
            {"inventur": self.abgeschlossen, "bloecke": bloecke, "anzahl_waren": 1},
        )
        self.assertIn(">10 Stk.<", html)
        self.assertNotIn("10 kg", html)

    def test_auch_ueber_den_import(self):
        """The file now says kg. "Change" adopts the unit, the result stays as it was frozen."""
        plan, _ = planen(
            pd.DataFrame(
                [["101001", "Ware N Stück", "Warengruppe G", "Karton", 5.0, "KILOGRAMM", ""]],
                columns=SPALTEN,
            ),
            self.haus,
        )
        bericht = ausfuehren([als_daten(plan[0]) | {"vorgehen": AKTUALISIEREN}], self.haus)

        self.assertEqual(bericht.artikel_geaendert, ["101001 Ware N Stück"])
        self.assertEqual(Hausartikel.objects.get(pk=self.artikel.pk).basiseinheit, einheit("kg"))
        self.assertEqual(self.blatt(), ("10 Stk., Soll 10 Stk.", "5 kg, Soll 5 kg"))


class DieWareLerntDazu(Grundlage, TestCase):
    """The factor of a frozen result follows what the product says later, as the article's does."""

    @classmethod
    def setUpTestData(cls):
        cls.grundlage()

    def setUp(self):
        self.aufbauen()

    def test_die_ware_wechselt_nach_gramm(self):
        einheit_wechseln(self.ware, einheit("g"))

        ergebnis = self.abgeschlossen.ergebnisse.get()
        self.assertEqual(ergebnis.faktor_zur_ware, Decimal("500"))
        self.assertEqual(self.blatt(), ("10 Stk., Soll 10 Stk.", "5000 g, Soll 5000 g"))

    def test_auch_ein_ergebnis_in_einer_frueheren_einheit_des_hauses(self):
        """The house counts in kg now, the result stays in pieces and converts along."""
        self.artikel.basiseinheit, self.artikel.faktor_zur_ware = einheit("kg"), None
        self.artikel.save()

        einheit_wechseln(self.ware, einheit("g"))

        self.assertEqual(self.abgeschlossen.ergebnisse.get().faktor_zur_ware, Decimal("500"))
        self.assertEqual(self.blatt(), ("10 Stk., Soll 10 Stk.", "5000 g, Soll 5000 g"))

    def test_zusammengefuehrt_in_eine_ware_in_gramm(self):
        ziel = Ware.objects.create(name="Ware M", basiseinheit=einheit("g"))

        zusammenfuehren(self.ware, ziel, faktor=Decimal("1000"))

        ergebnis = Inventurergebnis.objects.get(inventur=self.abgeschlossen)
        self.assertEqual(ergebnis.faktor_zur_ware, Decimal("500"))
        self.assertEqual(ergebnis.in_wareneinheit(ergebnis.gesamtmenge_basiseinheit), 5000)

    def test_ein_neuer_faktor_am_artikel_gilt_fuer_seine_ergebnisse(self):
        """What a piece weighs is knowledge about the product. A correction applies backwards."""
        self.artikel.faktor_zur_ware = Decimal("0.6")
        self.artikel.save()

        self.assertEqual(self.blatt(), ("10 Stk., Soll 10 Stk.", "6 kg, Soll 6 kg"))


class EinheitWartetAufDenAbschluss(Grundlage, AngemeldeteSeiten):
    """Target, open target stock line and difference by hand are numbers in the house's unit."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.grundlage()

    def setUp(self):
        super().setUp()
        self.aufbauen()
        self.laufend = inventur_anlegen(self.haus, stichtag=date(2026, 2, 28))

    def wechseln(self):
        self.artikel.basiseinheit = einheit("kg")
        self.artikel.faktor_zur_ware = None
        self.artikel.full_clean()

    def test_ein_soll_haelt_die_einheit_fest(self):
        Sollbestand.objects.create(
            inventur=self.laufend, hausartikel=self.artikel, menge_basiseinheit=Decimal("24")
        )
        with self.assertRaises(ValidationError) as fang:
            self.wechseln()
        meldung = " ".join(fang.exception.messages)
        self.assertIn(f"„{self.laufend}“", meldung)
        self.assertIn(
            "Diese Zahlen stehen in Stk., und bis zum Abschluss bleibt das Haus bei dieser "
            "Einheit.",
            meldung,
        )

    def test_auch_eine_offene_sollzeile_und_eine_differenz_von_hand(self):
        for anlegen in (
            lambda: Sollzeile.objects.create(
                inventur=self.laufend,
                artikelnummer="101001",
                menge_basiseinheit=Decimal("24"),
                grund=Sollgrund.INAKTIV,
                hausartikel=self.artikel,
            ),
            lambda: Inventurnotiz.objects.create(
                inventur=self.laufend, hausartikel=self.artikel, differenz=Decimal("-2")
            ),
        ):
            with self.subTest(anlegen=anlegen):
                eintrag = anlegen()
                with self.assertRaises(ValidationError):
                    self.wechseln()
                eintrag.delete()

    def test_eine_notiz_ohne_differenz_und_ein_neuer_faktor_stoeren_nicht(self):
        Inventurnotiz.objects.create(inventur=self.laufend, hausartikel=self.artikel, text="Regal")
        Sollbestand.objects.create(
            inventur=self.laufend, hausartikel=self.artikel, menge_basiseinheit=Decimal("24")
        )
        self.artikel.faktor_zur_ware = Decimal("0.6")
        self.artikel.full_clean()
        Sollbestand.objects.filter(inventur=self.laufend).delete()
        self.wechseln()

    def test_nach_dem_abschluss_geht_es(self):
        Sollbestand.objects.create(
            inventur=self.laufend, hausartikel=self.artikel, menge_basiseinheit=Decimal("24")
        )
        self.laufend.zaehlung_starten()
        self.laufend.abschliessen()
        self.wechseln()

    def test_das_formular_des_artikels_sagt_es(self):
        Sollbestand.objects.create(
            inventur=self.laufend, hausartikel=self.artikel, menge_basiseinheit=Decimal("24")
        )
        antwort = self.client.post(
            reverse("hausartikel_edit", args=[self.artikel.pk]),
            {
                "artikelnummer": "101001",
                "name": "Ware N Stück",
                "ean": "",
                "ware": self.ware.pk,
                "neuer_ware": "",
                "basiseinheit": einheit("kg").pk,
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
        self.assertContains(antwort, "bis zum Abschluss bleibt das Haus bei dieser Einheit.")
        self.assertEqual(
            Hausartikel.objects.get(pk=self.artikel.pk).basiseinheit, einheit("Stück(e)")
        )

    def test_der_importplan_sagt_es(self):
        Sollbestand.objects.create(
            inventur=self.laufend, hausartikel=self.artikel, menge_basiseinheit=Decimal("24")
        )
        kopf = "artikelnummer;name;warengruppe;gebinde;anzahl;einheit;haus"
        datei = "\n".join([kopf, "101001;Ware N Stück;Warengruppe G;Karton;5;kg;Kino Nord"])
        adresse = reverse("hausartikel_import", args=[self.haus.pk])
        plan = self.client.post(
            adresse,
            {
                "datei": SimpleUploadedFile("export.csv", datei.encode(), "text/csv"),
                "format": "winticket",
            },
        )
        formset = plan.context["formset"]
        daten = {
            "schritt": "uebernehmen",
            "format": "winticket",
            "plan-TOTAL_FORMS": "1",
            "plan-INITIAL_FORMS": "1",
            "plan-MIN_NUM_FORMS": "0",
            "plan-MAX_NUM_FORMS": "5000",
        }
        for feld, wert in formset.forms[0].initial.items():
            daten[f"plan-0-{feld}"] = "" if wert is None else wert
        daten["plan-0-vorgehen"] = AKTUALISIEREN

        antwort = self.client.post(adresse, daten)

        self.assertContains(antwort, "bis zum Abschluss bleibt das Haus bei dieser Einheit.")
        self.assertEqual(
            Hausartikel.objects.get(pk=self.artikel.pk).basiseinheit, einheit("Stück(e)")
        )

    def test_am_formular_vorbei_bleibt_der_satz(self):
        Sollbestand.objects.create(
            inventur=self.laufend, hausartikel=self.artikel, menge_basiseinheit=Decimal("24")
        )
        plan, _ = planen(
            pd.DataFrame(
                [["101001", "Ware N Stück", "Warengruppe G", "Karton", 5.0, "KILOGRAMM", ""]],
                columns=SPALTEN,
            ),
            self.haus,
        )
        bericht = ausfuehren([als_daten(plan[0]) | {"vorgehen": AKTUALISIEREN}], self.haus)

        self.assertIn("bis zum Abschluss bleibt das Haus", " ".join(bericht.fehler))
        self.assertEqual(
            Hausartikel.objects.get(pk=self.artikel.pk).basiseinheit, einheit("Stück(e)")
        )


class OhneFaktorInEinerAnderenDimension(AngemeldeteSeiten):
    """A result frozen in g against a product in g, whose article moved to a product in pieces.

    It keeps no factor: within its old product the units computed, and
    'ergebnisse_nachziehen' only gives the article's factor to results in
    the article's unit of today. Until 10/2026 the report applied that
    factor anyway, although it belongs to another unit. A factor of its own
    comes only where the article bridges the two products, and it does not
    in either case here ('Hausartikel._ergebnisse_ueber_den_artikel').
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
        lose = Ware.objects.create(name="Ware G", basiseinheit=einheit("g"))
        beutel = Gebinde.objects.create(
            ware=lose, name="Beutel", faktor_in_basiseinheit=Decimal("500")
        )
        self.artikel = Hausartikel.objects.create(
            haus=self.haus,
            ware=lose,
            warengruppe=self.gruppe,
            artikelnummer="101201",
            name="Ware G lose",
        )
        self.stichtag = date(2026, 1, 31)
        self.inventur = inventur_anlegen(self.haus, stichtag=self.stichtag)
        self.inventur.zaehlpositionen.create(
            ort=self.ort, artikel=self.artikel, gebinde=beutel, eingabemenge=Decimal("1")
        )
        self.inventur.zaehlung_starten()
        self.inventur.abschliessen()
        # A run is two houses or more. Alone, its page leads to the inventory.
        nebenan = inventur_anlegen(Haus.objects.create(name="Kino Ost"), stichtag=self.stichtag)
        nebenan.zaehlung_starten()
        nebenan.abschliessen()
        self.stueckware = Ware.objects.create(name="Ware S", basiseinheit=einheit("Stück(e)"))

    def aendern(self, **felder):
        for name, wert in felder.items():
            setattr(self.artikel, name, wert)
        self.artikel.full_clean()
        self.artikel.save()

    def zeilen(self):
        """The product's row in the report over inventories and in the run."""
        bloecke, _ = familien_ueber_inventuren([self.inventur])
        (auswertung,) = [z for b in bloecke for z in b.alle_zeilen if z.ware == self.stueckware]
        bloecke = familien_eines_laufs([self.inventur])
        (lauf,) = [z for b in bloecke for z in b.alle_zeilen if z.ware == self.stueckware]
        return {"Auswertung": auswertung, "Lauf": lauf}

    def test_ueber_die_einheit_des_artikels(self):
        """The house counts in kg now, at 4 pieces per kg: 500 g are 2 pieces, not 2000."""
        self.aendern(basiseinheit=einheit("kg"), faktor_zur_ware=None)
        self.aendern(ware=self.stueckware, faktor_zur_ware=Decimal("4"))

        ergebnis = self.inventur.ergebnisse.get()
        self.assertEqual((ergebnis.einheit, ergebnis.faktor_zur_ware), (einheit("g"), None))
        self.assertEqual(ergebnis.faktor_zur_ware_wirksam, Decimal("0.004"))
        for bericht, zeile in self.zeilen().items():
            with self.subTest(bericht):
                self.assertEqual((zeile.ist, zeile.ohne_umrechnung), (Decimal("2"), 0))
                self.assertEqual(zeile.ist_text, "2 Stk.")

    def test_ohne_weg_in_die_ware_fehlt_das_ergebnis_und_die_zeile_sagt_es(self):
        """Into pieces and onto a product in pieces in one step: nothing knows the way.

        Nobody said what a piece weighs, neither before nor after. Until
        10/2026 the report said 500 pieces.
        """
        self.aendern(ware=self.stueckware, basiseinheit=einheit("Stück(e)"), faktor_zur_ware=None)

        ergebnis = self.inventur.ergebnisse.get()
        self.assertIsNone(ergebnis.faktor_zur_ware_wirksam)
        self.assertIsNone(ergebnis.in_wareneinheit(ergebnis.gesamtmenge_basiseinheit))
        hinweis = "Ein Ergebnis lässt sich nicht in Stk. umrechnen und fehlt hier."
        for bericht, zeile in self.zeilen().items():
            with self.subTest(bericht):
                self.assertEqual((zeile.ist, zeile.ohne_umrechnung), (None, 1))
                self.assertEqual(zeile.ohne_umrechnung_text, hinweis)

        # The sheet of the inventory itself stays as it was frozen: 500 g.
        (zeile,) = _zeilen_einer_inventur(self.inventur, im_haus=True)
        self.assertEqual((zeile.ist_text, zeile.ohne_umrechnung), ("500 g", 0))
        # The pages and the sheets say it where the row stands.
        stichtag = self.stichtag.isoformat()
        self.assertContains(self.client.get(reverse("auswertung")), hinweis)
        self.assertContains(self.client.get(reverse("inventur_lauf", args=[stichtag])), hinweis)
        self.assertIn(hinweis, self.blatt(reverse("auswertung"), pdf="1"))
        self.assertIn(hinweis, self.blatt(reverse("inventur_lauf_pdf", args=[stichtag])))

    def blatt(self, adresse: str, **abfrage) -> str:
        """The HTML the view hands to WeasyPrint: no text can be searched in the finished PDF."""
        with mock.patch("weasyprint.HTML") as html:
            html.return_value.write_pdf.return_value = b"%PDF-1.7"
            self.client.get(adresse, abfrage)
        return html.call_args.kwargs["string"]


class AltesBackup(Grundlage, TestCase):
    """A backup from before 10/2026 brings results without unit. Restoring takes the article's."""

    @classmethod
    def setUpTestData(cls):
        cls.grundlage()

    def setUp(self):
        self.aufbauen()

    def test_der_empfaenger_traegt_einheit_und_faktor_nach(self):
        satz = {
            "model": "inventur.inventurergebnis",
            "pk": 9001,
            "fields": {
                "inventur": self.abgeschlossen.pk,
                "hausartikel": self.artikel.pk,
                "gesamtmenge_basiseinheit": "7.000",
                "sollbestand": None,
                "differenz": None,
                "notiz": "",
                "eingefroren_am": timezone.now().isoformat(),
            },
        }
        Inventurergebnis.objects.filter(inventur=self.abgeschlossen).delete()
        with tempfile.TemporaryDirectory() as ordner:
            pfad = Path(ordner) / "stand.json"
            pfad.write_text(json.dumps([satz]), encoding="utf-8")
            call_command("loaddata", str(pfad), verbosity=0)

        ergebnis = Inventurergebnis.objects.get(pk=9001)
        self.assertEqual(
            (ergebnis.einheit, ergebnis.faktor_zur_ware), (einheit("Stück(e)"), Decimal("0.5"))
        )

    def test_ein_ganzes_backup_mit_den_ergebnissen_vor_den_artikeln(self):
        """So 'dumpdata' writes it, and the receiver would not find the article yet.

        Reading fills the unit in beforehand ('backup_upgrade'), as restoring
        in the administration and 'manage.py backup read' do.
        """
        puffer = io.StringIO()
        call_command(
            "dumpdata",
            "katalog",
            "organisation",
            "inventur",
            natural_foreign=True,
            natural_primary=True,
            stdout=puffer,
        )
        saetze = json.loads(puffer.getvalue())
        ergebnisse = [satz for satz in saetze if satz["model"] == "inventur.inventurergebnis"]
        for satz in ergebnisse:
            del satz["fields"]["einheit"], satz["fields"]["faktor_zur_ware"]
        # In front, whatever order 'dumpdata' chose here: that is the case at stake.
        saetze = ergebnisse + [satz for satz in saetze if satz not in ergebnisse]
        Inventur.objects.all().delete()
        alles_loeschen()

        with tempfile.TemporaryDirectory() as ordner:
            pfad = Path(ordner) / "stand.json"
            pfad.write_bytes(umschreiben(json.dumps(saetze).encode()))
            call_command("loaddata", str(pfad), verbosity=0)

        ergebnis = Inventurergebnis.objects.get()
        self.assertEqual(ergebnis.einheit, einheit("Stück(e)"))
        self.assertEqual(ergebnis.faktor_zur_ware, Decimal("0.5"))
