"""The conversion to a product in another dimension applies to a row in every status.

'Hausartikel.pruefe_einheit' asks for it whether the row is active,
discontinued or not countable. Until 10/2026 the plan asked only where the
row ended up carried. A discontinued row without it passed the form and was
then dropped when adopting, while its product's packs were reconciled all
the same. "Replace" had discontinued the old row by then, and the number
stood without a carried article.
"""

from decimal import Decimal

import pandas as pd
from django.test import TestCase

from organisation.models import Haus

from .forms import BaseImportFormSet, ImportFormSet
from .models import Artikelstatus, Basiseinheit, Gebinde, Hausartikel, Ware, Warengruppe
from .services import (
    EINSTELLEN,
    ERSETZEN,
    INAKTIV,
    als_daten,
    ausfuehren,
    gebinde_lesen,
    gebinde_text,
    planen,
)

SPALTEN = ["artikelnummer", "name", "warengruppe", "gebinde", "anzahl", "einheit", "haus"]

#: What the plan says when the factor is missing.
FRAGE = "weiß nur, wer die Ware kennt"


def zeile(nummer: str, name: str) -> dict:
    return {
        "artikelnummer": nummer,
        "name": name,
        "warengruppe": "Warengruppe G",
        "gebinde": "Karton",
        "anzahl": 12.0,
        "einheit": "STUECK",
        "haus": "Kino Nord",
    }


class Grundlage(TestCase):
    """Ware N is carried in kg. Kino Nord counts what the file brings in pieces."""

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.stueck = Basiseinheit.objects.get(name="Stück(e)")
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")

    def setUp(self):
        self.ware = Ware.objects.create(
            name="Ware N", basiseinheit=Basiseinheit.objects.get(name="kg")
        )
        Gebinde.objects.create(ware=self.ware, name="Sack", faktor_in_basiseinheit=Decimal("25"))

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
                "vorgehen": satz.vorgehen,
                "status_neu": satz.status_neu,
                "hinweise": "",
                "abweichungen": ",".join(satz.abweichungen),
            }
            for feld, wert in werte.items():
                daten[f"plan-{stelle}-{feld}"] = wert
        return self.gebunden(daten | abweichend)

    def gebunden(self, daten) -> BaseImportFormSet:
        formset = ImportFormSet(daten, prefix="plan")
        formset.haus = self.haus
        return formset

    def alter_satz(self) -> Hausartikel:
        """101002 carried as Ware A. The file names the number for something else now."""
        return Hausartikel.objects.create(
            haus=self.haus,
            ware=Ware.objects.create(name="Ware A", basiseinheit=self.stueck),
            warengruppe=self.gruppe,
            artikelnummer="101002",
            name="Ware A",
        )


class ImPlan(Grundlage):
    def test_eingestellt_und_inaktiv_fragen_die_umrechnung(self):
        plan = self.plan(zeile("101001", "Artikel S"))
        for vorgehen in (EINSTELLEN, INAKTIV):
            with self.subTest(vorgehen=vorgehen):
                formset = self.formset(
                    plan, **{"plan-0-ware": str(self.ware.pk), "plan-0-vorgehen": vorgehen}
                )

                self.assertFalse(formset.is_valid())
                self.assertIn(FRAGE, " ".join(formset.forms[0].errors["faktor_zur_ware"]))

    def test_ersetzen_mit_einem_eingestellten_neuen_ebenso(self):
        self.alter_satz()
        plan = self.plan(zeile("101002", "Ware B neu"))
        formset = self.formset(
            plan,
            **{
                "plan-0-ware": str(self.ware.pk),
                "plan-0-vorgehen": ERSETZEN,
                "plan-0-status_neu": EINSTELLEN,
            },
        )

        self.assertFalse(formset.is_valid())
        self.assertIn(FRAGE, " ".join(formset.forms[0].errors["faktor_zur_ware"]))

    def test_mit_der_umrechnung_entsteht_der_eingestellte_satz(self):
        plan = self.plan(zeile("101001", "Artikel S"))
        formset = self.formset(
            plan,
            **{
                "plan-0-ware": str(self.ware.pk),
                "plan-0-vorgehen": EINSTELLEN,
                "plan-0-faktor_zur_ware": "0,5",
            },
        )
        # Chosen by hand, the row first comes back with the product's packs.
        self.assertFalse(formset.is_valid())
        assert formset.beschreibbar is not None
        formset = self.gebunden(formset.beschreibbar)
        self.assertTrue(formset.is_valid(), formset.errors)

        bericht = ausfuehren([form.cleaned_data for form in formset.forms], self.haus)

        self.assertEqual(bericht.fehler, [])
        neu = Hausartikel.objects.get(artikelnummer="101001")
        self.assertEqual(
            (neu.status, neu.ware, neu.faktor_zur_ware),
            (Artikelstatus.EINGESTELLT, self.ware, Decimal("0.5")),
        )


class AmFormularVorbei(Grundlage):
    def test_ersetzen_ohne_umrechnung_laesst_den_alten_satz_stehen(self):
        alt = self.alter_satz()
        (satz,) = self.plan(zeile("101002", "Ware B neu"))
        daten = als_daten(satz) | {
            "ware": self.ware,
            "vorgehen": ERSETZEN,
            "status_neu": EINSTELLEN,
        }

        bericht = ausfuehren([daten], self.haus)

        alt.refresh_from_db()
        self.assertEqual(alt.status, Artikelstatus.AKTIV)
        self.assertEqual(
            list(Hausartikel.objects.filter(artikelnummer="101002").values_list("name", flat=True)),
            ["Ware A"],
        )
        self.assertEqual(
            bericht.fehler,
            [
                "101002: Das Haus zählt in Stk., „Ware N“ wird in kg geführt. Ohne die "
                "Umrechnung wurde die Zeile nicht übernommen."
            ],
        )
        self.assertEqual(bericht.artikel_ersetzt, [])

    def test_eine_nicht_uebernommene_zeile_laesst_die_gebinde_der_ware(self):
        (satz,) = self.plan(zeile("101001", "Artikel S"))
        daten = als_daten(satz) | {"ware": self.ware, "vorgehen": EINSTELLEN}

        bericht = ausfuehren([daten], self.haus)

        self.assertFalse(Hausartikel.objects.filter(artikelnummer="101001").exists())
        self.assertEqual(len(bericht.fehler), 1)
        self.assertEqual(
            list(self.ware.gebinde.values_list("name", "faktor_in_basiseinheit", "aktiv")),
            [("Sack", Decimal("25.000"), True)],
        )


class OhneUmrechnungImPlan(Grundlage):
    """The plan cannot convert: Kino Nord counts Ware N in pieces, the product is carried in kg.

    The field then holds only the file's quantities. Adopting with the factor
    typed later leaves the product's packs as they are and adds what is
    missing. Until 10/2026 a converted quantity that happened to match a pack
    renamed it to the file's name in every house, and switched it on if it
    was inactive, though nobody had seen it in the field.
    """

    def setUp(self):
        super().setUp()
        ost = Haus.objects.create(name="Kino Ost")
        Hausartikel.objects.create(
            haus=ost, ware=self.ware, warengruppe=self.gruppe, artikelnummer="102001", name="Ware N"
        )
        self.eimer = Gebinde.objects.create(
            ware=self.ware, name="Eimer", faktor_in_basiseinheit=Decimal("30"), aktiv=False
        )

    def neue_zeile(self, gebinde: str, anzahl: float):
        (satz,) = self.plan(zeile("101001", "Ware N") | {"gebinde": gebinde, "anzahl": anzahl})
        self.assertEqual(satz.ware_pk, self.ware.pk)
        return satz

    def bestand(self) -> dict[str, tuple[Decimal, bool]]:
        return {
            name: (menge, aktiv)
            for name, menge, aktiv in self.ware.gebinde.values_list(
                "name", "faktor_in_basiseinheit", "aktiv"
            )
        }

    def test_der_plan_merkt_sich_keine_gebinde_der_ware(self):
        satz = self.neue_zeile("Karton", 12.0)

        self.assertIsNone(satz.gebinde_von)
        self.assertNotIn("Eimer", satz.gebinde)
        self.assertNotIn("Sack", satz.gebinde)

    def test_ein_inaktives_gebinde_bleibt_inaktiv_und_heisst_weiter_so(self):
        """12 pieces of 2.5 kg are the 30 kg of the inactive bucket."""
        satz = self.neue_zeile("Karton", 12.0)

        bericht = ausfuehren([als_daten(satz) | {"faktor_zur_ware": Decimal("2.5")}], self.haus)

        self.assertEqual(self.bestand()["Eimer"], (Decimal("30.000"), False))
        self.assertNotIn("Karton", self.bestand())
        self.assertEqual(bericht.gebinde_reaktiviert, 0)
        self.assertIn("Das Feld zeigte nicht die Gebinde von „Ware N“", " ".join(bericht.fehler))

    def test_ein_aktives_gebinde_wird_nicht_umbenannt(self):
        """10 pieces of 2.5 kg are the 25 kg of the sack. It keeps its name in every house."""
        satz = self.neue_zeile("Kiste", 10.0)

        bericht = ausfuehren([als_daten(satz) | {"faktor_zur_ware": Decimal("2.5")}], self.haus)

        self.assertEqual(self.bestand()["Sack"], (Decimal("25.000"), True))
        self.assertNotIn("Kiste", self.bestand())
        self.assertEqual(bericht.gebinde_umbenannt, 0)

    def test_im_formular_kommt_die_zeile_mit_den_gebinden_der_ware_zurueck(self):
        """Whoever types the factor sees the product's packs before anything is written.

        In pieces of 2.5 kg the sack is 10 and the inactive bucket 12. The file's
        carton of 12 is the bucket, so it stands under its own name, with its hint.
        """
        satz = self.neue_zeile("Karton", 12.0)
        formset = self.formset(
            [satz],
            **{
                # What the page carries from the plan ('views/imports.py').
                "plan-0-gebinde_von": satz.gebinde_von or "",
                "plan-0-gebinde_datei": gebinde_text(satz.gebinde_datei),
                "plan-0-faktor_zur_ware": "2,5",
            },
        )

        self.assertFalse(formset.is_valid())
        zurueck = formset.beschreibbar or {}
        self.assertEqual(
            gebinde_lesen(zurueck["plan-0-gebinde"])[0],
            {"Sack": Decimal("10"), "Eimer": Decimal("12"), "Einzeln": Decimal("1")},
        )
        self.assertEqual(zurueck["plan-0-gebinde_von"], str(self.ware.pk))
        fehler = " ".join(formset.forms[0].errors["gebinde"])
        self.assertIn("„Karton = 12“ hat die Menge des inaktiven Gebindes „Eimer“.", fehler)
