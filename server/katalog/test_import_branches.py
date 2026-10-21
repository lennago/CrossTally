"""The checks of the import that keep integrity errors away, and the messages past the form.

The coverage of 2026-10-18 found them in no test: the plan's checks that
stand before the database's unique indexes, and the messages adopting gives
where a row comes past the form. Each case here is one of them.
"""

from decimal import Decimal

import pandas as pd
from django.test import TestCase

from django_project.testing import inventur_anlegen
from organisation.models import Bereich, Haus, Ort

from .forms import BaseImportFormSet, ImportFormSet
from .models import (
    GEBINDENAME_LAENGE,
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
from .services import (
    AKTIV,
    AKTUALISIEREN,
    ERSETZEN,
    IGNORIEREN,
    KONFLIKT,
    als_daten,
    ausfuehren,
    planen,
)

SPALTEN = ["artikelnummer", "name", "warengruppe", "gebinde", "anzahl", "einheit", "haus"]


def zeile(nummer: str, name: str, gebinde="Karton", anzahl=12.0, einheit="STUECK") -> dict:
    return {
        "artikelnummer": nummer,
        "name": name,
        "warengruppe": "Warengruppe G",
        "gebinde": gebinde,
        "anzahl": anzahl,
        "einheit": einheit,
        "haus": "Kino Nord",
    }


class Grundlage(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.ost = Haus.objects.create(name="Kino Ost")
        cls.stueck = Basiseinheit.objects.get(name="Stück(e)")
        cls.kg = Basiseinheit.objects.get(name="kg")
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")

    def plan(self, *zeilen: dict):
        return planen(pd.DataFrame(list(zeilen), columns=SPALTEN), self.haus)[0]

    def formset(self, plan, **abweichend) -> BaseImportFormSet:
        daten = {
            "plan-TOTAL_FORMS": str(len(plan)),
            "plan-INITIAL_FORMS": str(len(plan)),
            "plan-MIN_NUM_FORMS": "0",
            "plan-MAX_NUM_FORMS": "5000",
        }
        for stelle, satz in enumerate(plan):
            werte = {
                "artikelnummer": satz.artikelnummer,
                "name": satz.name,
                "warengruppe": satz.warengruppe,
                "einheit": satz.einheit,
                "art": satz.art,
                "vorhanden_pk": satz.vorhanden_pk or "",
                "ware": satz.ware_pk or "",
                "inventurart": satz.inventurart or "",
                "gebinde": satz.gebindetext,
                "gebinde_von": satz.gebinde_von or "",
                "vorgehen": satz.vorgehen,
                "status_neu": satz.status_neu,
                "hinweise": "",
                "abweichungen": ",".join(satz.abweichungen),
            }
            for feld, wert in werte.items():
                daten[f"plan-{stelle}-{feld}"] = wert
        formset = ImportFormSet(daten | abweichend, prefix="plan")
        formset.haus = self.haus
        return formset

    def fehler(self, formset: BaseImportFormSet, feld: str, zeile: int = 0) -> str:
        self.assertFalse(formset.is_valid())
        return " ".join(formset.forms[zeile].errors.get(feld, []))

    def ware(self, name: str, einheit=None, **felder) -> Ware:
        return Ware.objects.create(name=name, basiseinheit=einheit or self.stueck, **felder)

    def hausartikel(self, ware: Ware, nummer: str, name: str, haus=None, **felder) -> Hausartikel:
        return Hausartikel.objects.create(
            haus=haus or self.haus,
            ware=ware,
            warengruppe=self.gruppe,
            artikelnummer=nummer,
            name=name,
            **felder,
        )


class ImPlan(Grundlage):
    """The form's checks. Each stands before an index or a rule of the model."""

    def test_eine_ware_ausserhalb_des_vorrats(self):
        """Deleted between planning and submitting, or a tampered form."""
        formset = self.formset(self.plan(zeile("101001", "Ware N")), **{"plan-0-ware": "999999"})

        self.assertTrue(self.fehler(formset, "ware"))

    def test_ersetzen_will_wissen_womit_der_neue_entsteht(self):
        self.hausartikel(self.ware("Ware A"), "101001", "Ware A")
        formset = self.formset(
            self.plan(zeile("101001", "Ware B")),
            **{"plan-0-vorgehen": ERSETZEN, "plan-0-status_neu": ""},
        )

        self.assertIn(
            "Bitte wählen, womit der neue Artikel entsteht.", self.fehler(formset, "status_neu")
        )

    def test_eine_nummer_zweimal_im_plan(self):
        plan = self.plan(zeile("101001", "Ware N"), zeile("101002", "Ware M"))
        formset = self.formset(plan, **{"plan-1-artikelnummer": "101001"})

        self.assertIn("101001 steht mehrfach im Plan.", self.fehler(formset, "vorgehen", 1))

    def test_eine_nicht_zaehlbare_ware_wird_nicht_aktiv_angelegt(self):
        pfand = self.ware("Pfand", warenfamilie=Warenfamilie.nicht_zaehlbar())
        formset = self.formset(
            self.plan(zeile("101001", "Ware N")),
            **{"plan-0-ware": str(pfand.pk), "plan-0-vorgehen": AKTIV},
        )

        self.assertIn("„Pfand“ gehört zu „Nicht zählbar“.", self.fehler(formset, "ware"))

    def test_eine_gefuehrte_ware_wird_nicht_ignoriert(self):
        """Not countable is the product, in every house. Kino Ost carries it."""
        cola = self.ware("Cola")
        self.hausartikel(cola, "102001", "Cola", haus=self.ost)
        formset = self.formset(
            self.plan(zeile("101001", "Cola EK")),
            **{"plan-0-ware": str(cola.pk), "plan-0-vorgehen": IGNORIEREN},
        )

        self.assertIn("„Cola“ wird als Ware geführt.", self.fehler(formset, "ware"))

    def test_eine_ware_in_einer_rezeptur_wird_nicht_ignoriert(self):
        """The same rule as on the product's button: the recipe could no longer resolve."""
        sirup = self.ware("Sirup")
        mischung = Rezepturartikel.objects.create(name="Mischung", basiseinheit=self.stueck)
        Rezepturkomponente.objects.create(
            rezepturartikel=mischung, ware=sirup, menge_in_basiseinheit=Decimal("1")
        )
        formset = self.formset(
            self.plan(zeile("101001", "Sirup")),
            **{"plan-0-ware": str(sirup.pk), "plan-0-vorgehen": IGNORIEREN},
        )

        self.assertIn("steckt in der Rezeptur von „Mischung“", self.fehler(formset, "ware"))


class AmFormularVorbei(Grundlage):
    """Adopting reports instead of failing at the database where a row comes past the form."""

    def test_aendern_auf_eine_vorhandene_bezeichnung(self):
        """House, number and designation exist once, in every status."""
        ware = self.ware("Ware N")
        self.hausartikel(ware, "101001", "Ware N alt", status=Artikelstatus.EINGESTELLT)
        aktiv = self.hausartikel(ware, "101001", "Ware N")
        (satz,) = self.plan(zeile("101001", "Ware N", anzahl=6.0))
        self.assertEqual(satz.art, KONFLIKT)

        bericht = ausfuehren(
            [als_daten(satz) | {"vorgehen": AKTUALISIEREN, "name": "Ware N alt"}], self.haus
        )

        self.assertEqual(
            bericht.fehler,
            [
                "101001 „Ware N alt“ gibt es in Kino Nord schon (eingestellt). Der vorhandene "
                "Satz blieb, wie er war."
            ],
        )
        aktiv.refresh_from_db()
        self.assertEqual(aktiv.name, "Ware N")

    def test_aendern_ohne_umrechnung_zur_neuen_ware(self):
        """The unit stays, the product goes to kg: without the factor nothing changes."""
        alt = self.hausartikel(self.ware("Ware N"), "101001", "Ware N")
        neu = self.ware("Ware N lose", einheit=self.kg)
        (satz,) = self.plan(zeile("101001", "Ware N", anzahl=6.0))

        bericht = ausfuehren(
            [als_daten(satz) | {"vorgehen": AKTUALISIEREN, "ware": neu, "uebernehmen": []}],
            self.haus,
        )

        self.assertEqual(
            bericht.fehler,
            [
                "101001: Das Haus zählt in Stk., „Ware N lose“ wird in kg geführt. Ohne die "
                "Umrechnung blieb der Satz, wie er war."
            ],
        )
        alt.refresh_from_db()
        self.assertEqual(alt.ware.name, "Ware N")

    def test_ein_gezaehlter_artikel_kommt_nicht_unter_eine_nicht_zaehlbare_ware(self):
        """Saving refuses it under the rows' lock. Asked only there, it took the import back."""
        ware = self.ware("Ware N")
        artikel = self.hausartikel(ware, "101001", "Ware N")
        kasten = Gebinde.objects.create(
            ware=ware, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        ort = Ort.objects.create(
            name="Thekenfront", bereich=Bereich.objects.create(name="Theke", haus=self.haus)
        )
        ErwartungGebinde.objects.create(
            erwartung=Erwartung.objects.create(ort=ort, artikel=artikel), gebinde=kasten
        )
        inventur = inventur_anlegen(self.haus)
        inventur.zaehlpositionen_anlegen()
        inventur.zaehlung_starten()
        inventur.zaehlpositionen.update(eingabemenge=Decimal("3"))
        pfand = self.ware("Pfand", warenfamilie=Warenfamilie.nicht_zaehlbar())
        (satz,) = self.plan(zeile("101001", "Ware N"))

        bericht = ausfuehren(
            [als_daten(satz) | {"vorgehen": AKTUALISIEREN, "ware": pfand, "uebernehmen": []}],
            self.haus,
        )

        self.assertEqual(len(bericht.fehler), 1)
        self.assertIn("ist in einer laufenden Inventur schon gezählt", bericht.fehler[0])
        artikel.refresh_from_db()
        self.assertEqual((artikel.ware_id, artikel.status), (ware.pk, Artikelstatus.AKTIV))
        self.assertTrue(inventur.zaehlpositionen.filter(aktiv=True, eingabemenge=3).exists())

    def test_eine_gefuehrte_ware_wird_nicht_ignoriert(self):
        cola = self.ware("Cola")
        self.hausartikel(cola, "102001", "Cola", haus=self.ost)
        (satz,) = self.plan(zeile("101001", "Cola EK"))

        bericht = ausfuehren([als_daten(satz) | {"vorgehen": IGNORIEREN, "ware": cola}], self.haus)

        self.assertEqual(
            bericht.fehler,
            [
                "101001: „Cola“ wird als Ware geführt. Die Zeile wurde nicht auf nicht zählbar "
                "gesetzt. Dafür braucht sie eine eigene Bezeichnung."
            ],
        )
        self.assertFalse(Hausartikel.objects.filter(artikelnummer="101001").exists())

    def test_ein_zwilling_wird_nicht_angelegt(self):
        """Someone created the same row between planning and submitting."""
        (satz,) = self.plan(zeile("101001", "Ware N"))
        self.hausartikel(self.ware("Ware N"), "101001", "Ware N", status=Artikelstatus.INAKTIV)

        bericht = ausfuehren([als_daten(satz) | {"neuer_ware": "Ware N neu"}], self.haus)

        self.assertEqual(
            bericht.fehler,
            [
                "101001 „Ware N“ gibt es in Kino Nord schon (inaktiv) und wurde nicht noch "
                "einmal angelegt."
            ],
        )

    def test_ein_gebinde_unter_einem_gramm_wird_uebergangen(self):
        """In pieces of 0.00001 kg, converted into the product's kg: too small for the column."""
        ware = self.ware("Ware N", einheit=self.kg)
        (satz,) = self.plan(zeile("101001", "Ware N", gebinde="Beutel", anzahl=5.0))

        bericht = ausfuehren(
            [als_daten(satz) | {"ware": ware, "faktor_zur_ware": Decimal("0.00001")}], self.haus
        )

        self.assertIn(
            "101001: „Beutel“ fasste in kg weniger als 0,001. So klein lässt sich keine Menge "
            "führen. Das Gebinde wurde übergangen.",
            bericht.fehler,
        )
        self.assertFalse(ware.gebinde.filter(name="Beutel").exists())

    def test_ein_gebindename_der_schon_vergeben_ist(self):
        """Name and name with quantity both taken: the pack gets the next free name.

        Chosen by hand past the form, the field only adds to the product. Until
        10/2026 the pack was skipped here, and the quantity from the file was lost.
        """
        ware = self.ware("Ware N")
        Gebinde.objects.create(ware=ware, name="Karton", faktor_in_basiseinheit=Decimal("6"))
        Gebinde.objects.create(ware=ware, name="Karton 12", faktor_in_basiseinheit=Decimal("10"))
        (satz,) = self.plan(zeile("101001", "Artikel X", anzahl=12.0))
        self.assertIsNone(satz.gebinde_von)

        bericht = ausfuehren([als_daten(satz) | {"ware": ware}], self.haus)

        self.assertIn(
            "„Ware N“: „Karton“ gibt es schon mit einer anderen Menge. Angelegt wurde "
            "„Karton 12 (2)“.",
            bericht.fehler,
        )
        self.assertEqual(
            dict(ware.gebinde.values_list("name", "faktor_in_basiseinheit")),
            {
                "Karton": Decimal("6.000"),
                "Karton 12": Decimal("10.000"),
                "Karton 12 (2)": Decimal("12.000"),
                "Einzeln": Decimal("1.000"),
            },
        )

    def test_ein_langer_vergebener_gebindename_passt_in_die_spalte(self):
        """With the quantity attached it would be too long. PostgreSQL rejected it until 10/2026."""
        ware = self.ware("Ware N")
        lang = "L" * GEBINDENAME_LAENGE
        Gebinde.objects.create(ware=ware, name=lang, faktor_in_basiseinheit=Decimal("6"))
        (satz,) = self.plan(zeile("101001", "Artikel X", gebinde=lang, anzahl=12.0))

        ausfuehren([als_daten(satz) | {"ware": ware}], self.haus)

        self.assertEqual(
            ware.gebinde.get(faktor_in_basiseinheit=12).name,
            "L" * (GEBINDENAME_LAENGE - 3) + " 12",
        )


class Hinweise(Grundlage):
    def test_ein_inaktiver_satz_bleibt_es_beim_aendern(self):
        self.hausartikel(self.ware("Ware N"), "101001", "Ware N", status=Artikelstatus.INAKTIV)

        (satz,) = self.plan(zeile("101001", "Ware N", anzahl=6.0))

        self.assertEqual(satz.art, KONFLIKT)
        self.assertIn(
            "Der vorhandene Satz ist inaktiv. „Ändern“ lässt ihn das auch.", satz.hinweise
        )

    def test_eine_hinterlegte_umrechnung_wird_genannt(self):
        """The house counts the beans in bags of 1 kg. The plan says what is stored."""
        ware = self.ware("Ware N", einheit=self.kg)
        self.hausartikel(
            ware, "101001", "Ware N", basiseinheit=self.stueck, faktor_zur_ware=Decimal("1")
        )

        (satz,) = self.plan(zeile("101001", "Ware N", gebinde="Karton", anzahl=7.0))

        self.assertTrue(any("Hinterlegt ist 1" in hinweis for hinweis in satz.hinweise))
