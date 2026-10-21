"""What a file or the pack field brings has to fit its column, or it becomes a message.

Until 10/2026 "nan", "inf" and numbers beyond 28 places ended on the error
page as soon as the pack field was read. A pack beyond the column, a target
that grew past it by rounding and a product group longer than its name
passed every check and failed only at the database: under PostgreSQL with
the error page, under SQLite as a row nobody could read back.
"""

from decimal import Decimal

import pandas as pd
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from django_project.testing import AngemeldeteSeiten, inventur_anlegen
from organisation.models import Haus

from .formats.base import menge_lesen
from .models import Basiseinheit, Gebinde, Ware, Warengruppe
from .services import als_daten, ausfuehren, gebinde_lesen, planen

KOPF = "artikelnummer;name;warengruppe;gebinde;anzahl;einheit;haus"


class Gebindefeld(SimpleTestCase):
    def test_was_keine_zahl_ist_wird_gemeldet(self):
        for wert in ("nan", "NaN", "sNaN", "inf", "-Infinity", "Infinity"):
            with self.subTest(wert=wert):
                gebinde, fehler = gebinde_lesen(f"Kasten = {wert}")

                self.assertEqual(gebinde, {})
                self.assertEqual(
                    fehler, [f"„Kasten = {wert}“: „{wert}“ ist keine Menge größer null."]
                )

    def test_was_die_spalte_nicht_fasst_wird_gemeldet(self):
        """Also what only rounding carries over the limit, and what Decimal cannot round."""
        for wert in ("1e30", "240000000", "10000000", "9999999,9996"):
            with self.subTest(wert=wert):
                gebinde, fehler = gebinde_lesen(f"Karton = {wert}")

                self.assertEqual(gebinde, {})
                self.assertEqual(
                    fehler,
                    [
                        f"„Karton = {wert}“: „{wert}“ ist zu groß. Ein Gebinde fasst höchstens "
                        "9999999,999."
                    ],
                )

    def test_bis_an_die_grenze_geht_es(self):
        gebinde, fehler = gebinde_lesen("Karton = 9999999,999; Beutel = 0,0005")

        self.assertEqual(fehler, [])
        self.assertEqual(gebinde, {"Karton": Decimal("9999999.999"), "Beutel": Decimal("0.001")})

    def test_was_auf_null_rundet_ist_keine_menge(self):
        gebinde, fehler = gebinde_lesen("Beutel = 0,0004")

        self.assertEqual(gebinde, {})
        self.assertEqual(fehler, ["„Beutel = 0,0004“: „0,0004“ ist keine Menge größer null."])

    def test_ein_zu_langer_name_wird_gemeldet(self):
        lang = "K" * 101

        gebinde, fehler = gebinde_lesen(f"{lang} = 6\n{'K' * 100} = 12")

        self.assertEqual(gebinde, {"K" * 100: Decimal("12")})
        self.assertEqual(fehler, [f"„{lang}“ ist als Name zu lang. Erlaubt sind 100 Zeichen."])


class MengeAusDerDatei(SimpleTestCase):
    def test_was_erst_gerundet_zu_gross_wird_ist_keine_menge(self):
        """999999999.9995 rounds to 1000000000.000, and the column holds twelve digits."""
        self.assertIsNone(menge_lesen("999999999,9995"))
        self.assertEqual(menge_lesen("999999999,9994"), Decimal("999999999.999"))
        self.assertIsNone(menge_lesen("-999999999,9995"))

    def test_was_keine_zahl_ist(self):
        for wert in ("NaN", "Infinity", "-Infinity", "sNaN"):
            with self.subTest(wert=wert):
                self.assertIsNone(menge_lesen(wert))


class UeberDieSeite(AngemeldeteSeiten):
    """The same through the plan, on both databases: a message instead of the error page."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Nord")

    @property
    def adresse(self) -> str:
        return reverse("hausartikel_import", args=[self.haus.pk])

    def hochladen(self, *zeilen: str):
        datei = SimpleUploadedFile(
            "export.csv", "\n".join([KOPF, *zeilen]).encode("utf-8"), "text/csv"
        )
        return self.client.post(self.adresse, {"datei": datei, "format": "winticket"})

    def absenden(self, antwort, **abweichend):
        formset = antwort.context["formset"]
        daten = {
            "schritt": "uebernehmen",
            "format": "winticket",
            "plan-TOTAL_FORMS": str(len(formset.forms)),
            "plan-INITIAL_FORMS": str(len(formset.forms)),
            "plan-MIN_NUM_FORMS": "0",
            "plan-MAX_NUM_FORMS": "5000",
        }
        for stelle, form in enumerate(formset.forms):
            for feld, wert in form.initial.items():
                daten[f"plan-{stelle}-{feld}"] = "" if wert is None else wert
        return self.client.post(self.adresse, daten | abweichend)

    def test_ein_zu_grosses_gebinde_aus_der_datei(self):
        plan = self.hochladen("101001;Ware N;Warengruppe G;Karton;99999999;Stück(e);Kino Nord")
        self.assertContains(plan, "Karton = 99999999")

        antwort = self.absenden(plan)

        self.assertContains(antwort, "„99999999“ ist zu groß.")
        self.assertFalse(Ware.objects.filter(name="Ware N").exists())

    def test_ein_riesiger_exponent_aus_der_datei(self):
        """The field shows the display limit, and adopting names the limit of the column.

        Until 10/2026 every plan row wrote "9e999999" out with a million
        digits, in the field, the hidden field and the hints, and
        "1e1000000" ended on the error page ('decimal.Overflow').
        """
        for wert in ("9e999999", "1e1000000"):
            with self.subTest(wert=wert):
                plan = self.hochladen(
                    f"101001;Ware N;Warengruppe G;Karton;{wert};Stück(e);Kino Nord"
                )
                self.assertEqual(plan.status_code, 200)
                self.assertLess(len(plan.content), 200_000)
                self.assertContains(plan, "Karton = 1000000000000000")

                antwort = self.absenden(plan)

                self.assertContains(
                    antwort,
                    "„1000000000000000“ ist zu groß. Ein Gebinde fasst höchstens 9999999,999.",
                )
                self.assertFalse(Ware.objects.filter(name="Ware N").exists())

    def test_ein_getippter_riesiger_exponent(self):
        """Reading the field names the value as it was typed."""
        plan = self.hochladen("101001;Ware N;Warengruppe G;Karton;6;Stück(e);Kino Nord")

        antwort = self.absenden(plan, **{"plan-0-gebinde": "Karton = 1e1000000"})

        self.assertContains(antwort, "„1e1000000“ ist zu groß.")
        self.assertFalse(Gebinde.objects.exists())

    def test_ein_getipptes_zu_grosses_gebinde(self):
        plan = self.hochladen("101001;Ware N;Warengruppe G;Karton;6;Stück(e);Kino Nord")

        antwort = self.absenden(plan, **{"plan-0-gebinde": "Karton = 240000000"})

        self.assertContains(antwort, "„240000000“ ist zu groß.")
        self.assertFalse(Gebinde.objects.exists())

    def test_ein_zu_langer_gebindename(self):
        plan = self.hochladen("101001;Ware N;Warengruppe G;Karton;6;Stück(e);Kino Nord")

        antwort = self.absenden(plan, **{"plan-0-gebinde": f"{'K' * 101} = 6"})

        self.assertContains(antwort, "ist als Name zu lang. Erlaubt sind 100 Zeichen.")
        self.assertFalse(Gebinde.objects.exists())

    def test_eine_zu_lange_artikelnummer_bleibt_draussen(self):
        """The column holds 20 characters. Until 10/2026 PostgreSQL refused with an error page."""
        lang = "1" + "0" * 24
        plan = self.hochladen(
            f"{lang};Ware N;Warengruppe G;Karton;6;Stück(e);Kino Nord",
            "101002;Ware M;Warengruppe G;Karton;6;Stück(e);Kino Nord",
        )

        self.assertContains(
            plan, f"„{lang}“: Die Artikelnummer ist länger als 20 Zeichen, übergangen."
        )
        self.assertEqual(
            [form.initial["artikelnummer"] for form in plan.context["formset"].forms], ["101002"]
        )
        self.assertEqual(self.absenden(plan).status_code, 302)
        self.assertEqual(list(Ware.objects.values_list("name", flat=True)), ["Ware M"])

    def test_eine_zu_lange_bezeichnung_bleibt_draussen(self):
        """Until 10/2026 it held up the whole plan, with an error in a hidden field."""
        plan = self.hochladen(
            f"101001;{'N' * 201};Warengruppe G;Karton;6;Stück(e);Kino Nord",
            "101002;Ware M;Warengruppe G;Karton;6;Stück(e);Kino Nord",
        )

        self.assertContains(plan, "101001: Die Bezeichnung ist länger als 200 Zeichen, übergangen.")
        self.assertEqual(self.absenden(plan).status_code, 302)
        self.assertEqual(list(Ware.objects.values_list("name", flat=True)), ["Ware M"])

    def test_ein_soll_mit_zu_langer_nummer_bleibt_draussen(self):
        """The target file the same, read on the inventory's page. The rest of the file counts."""
        inventur = inventur_anlegen(self.haus)
        lang = "1" + "0" * 24
        datei = SimpleUploadedFile(
            "soll.csv", f"{lang};4\n101002;3;{'N' * 201}\n101003;2\n".encode(), "text/csv"
        )

        antwort = self.client.post(
            inventur.get_absolute_url(), {"datei": datei, "format": "winticket"}, follow=True
        )

        self.assertEqual(antwort.status_code, 200)
        self.assertContains(
            antwort, f"Zeile 1: Die Artikelnummer „{lang}“ ist länger als 20 Zeichen."
        )
        self.assertContains(antwort, "Zeile 2: Die Bezeichnung ist länger als 200 Zeichen.")
        self.assertEqual(
            list(inventur.sollzeilen.values_list("artikelnummer", flat=True)), ["101003"]
        )

    def test_ein_zu_langer_gebindename_bleibt_draussen(self):
        """Adopting refused it anyway, and until 10/2026 the plan copied it into every field."""
        plan = self.hochladen(
            f"101001;Ware N;Warengruppe G;{'K' * 101};6;Stück(e);Kino Nord",
            "101002;Ware M;Warengruppe G;Karton;6;Stück(e);Kino Nord",
            f"101003;Ware L;Warengruppe G;{'K' * 100};6;Stück(e);Kino Nord",
        )

        self.assertContains(
            plan, "101001 „Ware N“: Der Name des Gebindes ist länger als 100 Zeichen, übergangen."
        )
        self.assertEqual(
            [form.initial["artikelnummer"] for form in plan.context["formset"].forms],
            ["101002", "101003"],
        )

    def test_eine_zu_lange_warengruppe_bleibt_draussen(self):
        """It rides along hidden: nobody could correct it in the plan."""
        plan = self.hochladen(
            f"101001;Ware N;{'W' * 101};Karton;6;Stück(e);Kino Nord",
            "101002;Ware M;Warengruppe G;Karton;6;Stück(e);Kino Nord",
        )

        self.assertContains(
            plan, "101001 „Ware N“: Die Warengruppe ist länger als 100 Zeichen, übergangen."
        )
        self.assertEqual(
            [form.initial["artikelnummer"] for form in plan.context["formset"].forms], ["101002"]
        )
        antwort = self.absenden(plan)
        self.assertEqual(antwort.status_code, 302)
        self.assertEqual(
            list(Warengruppe.objects.values_list("name", flat=True)), ["Warengruppe G"]
        )


class InDerEinheitDerWare(TestCase):
    """The field holds its quantities in the row's unit, the product in its own.

    Converted, a quantity can grow past the column or two can round to one.
    Until 10/2026 the first ended under PostgreSQL in a numeric overflow and
    the second at the unique index, both on the error page, and nothing of
    the import was written. Now the pack is left out and the report says why.
    """

    SPALTEN = ["artikelnummer", "name", "warengruppe", "gebinde", "anzahl", "einheit", "haus"]

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Nord")

    def uebernehmen(self, einheit_der_ware: str, einheit_der_zeile: str, gebinde: dict):
        ware = Ware.objects.create(
            name="Ware N", basiseinheit=Basiseinheit.objects.get(kennung=einheit_der_ware)
        )
        tabelle = pd.DataFrame(
            [["101001", "Ware N", "Warengruppe G", "", 0, einheit_der_zeile, "Kino Nord"]],
            columns=self.SPALTEN,
        )
        (satz,), _meldungen = planen(tabelle, self.haus)
        daten = als_daten(satz) | {"ware": ware, "gebinde": gebinde, "gebinde_von": ware.pk}
        return ware, ausfuehren([daten], self.haus)

    def test_ein_sack_in_kg_ist_in_gramm_zu_gross(self):
        ware, bericht = self.uebernehmen(
            "GRAMM", "KILOGRAMM", {"Sack": Decimal("20000"), "Beutel": Decimal("1")}
        )

        self.assertEqual(
            bericht.fehler,
            [
                "101001: „Sack“ fasste in g 20000000. Ein Gebinde fasst höchstens 9999999,999. "
                "Das Gebinde wurde übergangen."
            ],
        )
        self.assertEqual(
            list(ware.gebinde.values_list("name", "faktor_in_basiseinheit")),
            [("Beutel", Decimal("1000"))],
        )
        self.assertEqual(bericht.artikel_neu, ["101001 Ware N"])

    def test_zwei_mengen_die_gerundet_zusammenfallen(self):
        """1 g and 1,4 g are both 0,001 kg."""
        ware, bericht = self.uebernehmen(
            "KILOGRAMM", "GRAMM", {"Beutel": Decimal("1"), "Tüte": Decimal("1.4")}
        )

        self.assertEqual(
            bericht.fehler,
            [
                "101001: „Tüte“ fasste in kg dasselbe wie „Beutel“ (0,001). Das Gebinde wurde "
                "übergangen."
            ],
        )
        self.assertEqual(list(ware.gebinde.values_list("name", flat=True)), ["Beutel"])
