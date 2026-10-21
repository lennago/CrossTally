"""The pack field of the import plan: a free name for a new quantity, and inactive packs.

Until 10/2026 the field knew only the product's active packs. A proposed
"Kasten 20" that was taken as well dropped out of the field, while the hint
still announced it. A name that only an inactive pack held was proposed all
the same, and adopting created a name the field had not shown. The quantity
of an inactive pack stood in the field like a new one: adopting switched the
pack on again in every house, renamed if the file called it differently, and
nobody was told.
"""

from decimal import Decimal

import pandas as pd
from django.test import TestCase

from organisation.models import Haus

from .models import GEBINDENAME_LAENGE, Basiseinheit, Gebinde, Hausartikel, Ware, Warengruppe
from .services import NEU, als_daten, ausfuehren, gebinde_lesen, planen

SPALTEN = ["artikelnummer", "name", "warengruppe", "gebinde", "anzahl", "einheit", "haus"]


def zeile(nummer: str, name: str, gebinde: str, anzahl: float) -> dict:
    return {
        "artikelnummer": nummer,
        "name": name,
        "warengruppe": "Warengruppe G",
        "gebinde": gebinde,
        "anzahl": anzahl,
        "einheit": "STUECK",
        "haus": "Kino Nord",
    }


class Grundlage(TestCase):
    """'Ware N' is carried by Kino Ost. Kino Nord imports it for the first time.

    The row is new and finds the product by its name. So the field shows the
    product's packs, and adopting reconciles them in every house.
    """

    @classmethod
    def setUpTestData(cls):
        cls.nord = Haus.objects.create(name="Kino Nord")
        cls.ost = Haus.objects.create(name="Kino Ost")
        cls.stueck = Basiseinheit.objects.get(name="Stück(e)")
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        cls.ware = Ware.objects.create(name="Ware N", basiseinheit=cls.stueck)
        Hausartikel.objects.create(
            haus=cls.ost,
            ware=cls.ware,
            warengruppe=cls.gruppe,
            artikelnummer="102001",
            name="Ware N",
        )

    def gebinde(self, name: str, menge: int, aktiv: bool = True) -> Gebinde:
        return Gebinde.objects.create(
            ware=self.ware,
            name=name,
            faktor_in_basiseinheit=Decimal(menge),
            ist_einzeln=name == "Einzeln",
            aktiv=aktiv,
        )

    def planzeile(self, gebinde: str, anzahl: float):
        tabelle = pd.DataFrame([zeile("101001", "Ware N", gebinde, anzahl)], columns=SPALTEN)
        (satz,) = planen(tabelle, self.nord)[0]
        self.assertEqual((satz.art, satz.ware_pk), (NEU, self.ware.pk))
        return satz

    def bestand(self) -> dict[str, tuple[Decimal, bool]]:
        """Every pack of the product: its quantity and whether it is active."""
        return {
            name: (menge, aktiv)
            for name, menge, aktiv in self.ware.gebinde.values_list(
                "name", "faktor_in_basiseinheit", "aktiv"
            )
        }


class FreierName(Grundlage):
    """A new quantity under a name the product already gives another one."""

    def test_ist_auch_der_name_mit_menge_vergeben_zaehlt_er_weiter(self):
        """The name "Kasten 20" holds 18 already. Until 10/2026 the crate of 20 fell out."""
        self.gebinde("Kasten", 24)
        self.gebinde("Kasten 20", 18)

        satz = self.planzeile("Kasten", 20.0)

        self.assertEqual(
            satz.gebinde, {"Kasten": 24, "Kasten 20": 18, "Kasten 20 (2)": 20, "Einzeln": 1}
        )
        self.assertIn(
            "„Kasten“ gibt es bei der Ware schon mit einer anderen Menge. Vorgeschlagen ist "
            "deshalb „Kasten 20 (2)“ als eigenes Gebinde.",
            satz.hinweise,
        )
        ausfuehren([als_daten(satz)], self.nord)
        self.assertEqual(self.bestand()["Kasten 20 (2)"], (Decimal("20.000"), True))
        self.assertEqual(self.bestand()["Kasten 20"], (Decimal("18.000"), True))

    def test_ein_vorschlag_den_das_feld_schon_traegt_zaehlt_ebenso(self):
        """The file itself names a "Kasten 20" of 30, before the crate of 20 comes."""
        self.gebinde("Kasten", 24)
        tabelle = pd.DataFrame(
            [
                zeile("101001", "Ware N", "Kasten 20", 30.0),
                zeile("101001", "Ware N", "Kasten", 20.0),
            ],
            columns=SPALTEN,
        )

        (satz,) = planen(tabelle, self.nord)[0]

        self.assertEqual(
            satz.gebinde, {"Kasten": 24, "Kasten 20": 30, "Kasten 20 (2)": 20, "Einzeln": 1}
        )
        self.assertIn(
            "„Kasten“ gibt es bei der Ware schon mit einer anderen Menge. Vorgeschlagen ist "
            "deshalb „Kasten 20 (2)“ als eigenes Gebinde.",
            satz.hinweise,
        )
        ausfuehren([als_daten(satz)], self.nord)
        self.assertEqual(self.bestand()["Kasten 20"], (Decimal("30.000"), True))
        self.assertEqual(self.bestand()["Kasten 20 (2)"], (Decimal("20.000"), True))

    def test_auch_ein_inaktives_gebinde_haelt_seinen_namen(self):
        """The field does not show it, and yet the name is taken. The hint says by whom."""
        self.gebinde("Kasten", 20, aktiv=False)

        satz = self.planzeile("Kasten", 24.0)

        self.assertEqual(satz.gebinde, {"Kasten 24": 24, "Einzeln": 1})
        self.assertIn(
            "„Kasten“ gibt es bei der Ware schon, aber inaktiv und mit einer anderen Menge. "
            "Vorgeschlagen ist deshalb „Kasten 24“ als eigenes Gebinde.",
            satz.hinweise,
        )
        ausfuehren([als_daten(satz)], self.nord)
        self.assertEqual(
            self.bestand(),
            {
                "Kasten": (Decimal("20.000"), False),
                "Kasten 24": (Decimal("24.000"), True),
                "Einzeln": (Decimal("1.000"), True),
            },
        )

    def test_ein_inaktiver_name_mit_menge_zaehlt_ebenso(self):
        """Until 10/2026 the plan proposed "Kasten 20", and adopting created "Kasten 20 20"."""
        self.gebinde("Kasten", 24)
        self.gebinde("Kasten 20", 18, aktiv=False)

        satz = self.planzeile("Kasten", 20.0)

        self.assertEqual(satz.gebinde, {"Kasten": 24, "Kasten 20 (2)": 20, "Einzeln": 1})
        self.assertIn(
            "„Kasten“ gibt es bei der Ware schon mit einer anderen Menge. Vorgeschlagen ist "
            "deshalb „Kasten 20 (2)“ als eigenes Gebinde.",
            satz.hinweise,
        )
        ausfuehren([als_daten(satz)], self.nord)
        self.assertEqual(self.bestand()["Kasten 20 (2)"], (Decimal("20.000"), True))
        self.assertEqual(self.bestand()["Kasten 20"], (Decimal("18.000"), False))

    def test_der_vorschlag_passt_in_die_spalte(self):
        """The name gives way at the front. Until 10/2026 the form rejected the plan's proposal."""
        lang = "L" * GEBINDENAME_LAENGE
        self.gebinde(lang, 20)

        satz = self.planzeile(lang, 24.0)

        vorschlag = "L" * (GEBINDENAME_LAENGE - 3) + " 24"
        self.assertEqual(satz.gebinde, {lang: 20, vorschlag: 24, "Einzeln": 1})
        self.assertEqual(gebinde_lesen(satz.gebindetext)[1], [])


class InaktiveMenge(Grundlage):
    """The file names the quantity of a pack the product carries inactive.

    If the file carries it, the import brings it back (decided on 2026-10-18).
    The field shows it under the pack's own name, the hint says what adopting
    does, and whoever does not want it takes the line out: the person decides,
    as on the house article form since 2026-10-19.
    """

    def setUp(self):
        self.kasten = self.gebinde("Kasten", 20, aktiv=False)
        self.gebinde("Einzeln", 1)

    def test_ein_neuer_artikel_holt_es_zurueck_und_sagt_es_vorher(self):
        """Proposed is "Aktiv". The hint says before adopting what adopting does."""
        satz = self.planzeile("Kasten", 20.0)

        self.assertEqual(satz.gebinde, {"Einzeln": 1, "Kasten": 20})
        self.assertIn(
            "„Kasten = 20“ führt die Ware nur inaktiv. Im Feld steht es als „Kasten = 20“, und "
            "Übernehmen holt es zurück, in allen Häusern. Wer das nicht will, nimmt die Zeile "
            "aus dem Feld.",
            satz.hinweise,
        )
        bericht = ausfuehren([als_daten(satz)], self.nord)
        self.assertEqual(self.bestand()["Kasten"], (Decimal("20.000"), True))
        self.assertEqual(bericht.gebinde_reaktiviert, 1)

    def test_unter_anderem_namen_kommt_es_unter_seinem_eigenen(self):
        """Until 10/2026 a "Kiste" of 20 renamed the crate and switched it on without a word."""
        satz = self.planzeile("Kiste", 20.0)

        self.assertEqual(satz.gebinde, {"Einzeln": 1, "Kasten": 20})
        self.assertIn(
            "„Kiste = 20“ hat die Menge des inaktiven Gebindes „Kasten“. Im Feld steht es als "
            "„Kasten = 20“, und Übernehmen holt es zurück, in allen Häusern. Wer das nicht will, "
            "nimmt die Zeile aus dem Feld.",
            satz.hinweise,
        )
        ausfuehren([als_daten(satz)], self.nord)
        self.assertEqual(
            self.bestand(),
            {"Kasten": (Decimal("20.000"), True), "Einzeln": (Decimal("1.000"), True)},
        )

    def test_wer_die_zeile_herausnimmt_laesst_es_inaktiv(self):
        """As the hint says. Nothing switches it on then, and the report counts nothing."""
        satz = self.planzeile("Kasten", 20.0)
        ohne = {name: menge for name, menge in satz.gebinde.items() if name != "Kasten"}

        bericht = ausfuehren([als_daten(satz) | {"gebinde": ohne}], self.nord)

        self.assertEqual(self.bestand()["Kasten"], (Decimal("20.000"), False))
        self.assertEqual(bericht.gebinde_reaktiviert, 0)


class InaktivesEinzelstueck(Grundlage):
    """The plan adds the loose single unit to piece goods by itself, see '_einzeln_ergaenzen'."""

    def test_ein_inaktives_bleibt_es(self):
        """Someone switched it off for this product. The import does not switch it on again."""
        self.gebinde("Kasten", 24)
        self.gebinde("Einzeln", 1, aktiv=False)

        satz = self.planzeile("Kasten", 24.0)

        self.assertEqual(satz.gebinde, {"Kasten": 24})
        self.assertIn("„Einzeln = 1“ führt die Ware nur inaktiv.", " ".join(satz.hinweise))
        ausfuehren([als_daten(satz)], self.nord)
        self.assertEqual(self.bestand()["Einzeln"], (Decimal("1.000"), False))
