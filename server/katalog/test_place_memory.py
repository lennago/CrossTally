"""The way back brings family and places along: a wrong click undone whole.

Discontinuing and ignoring take an article out of every counting list, and
ignoring takes the product's family away. Until 10/2026 the way back
restored the status alone: the article was active again but expected
nowhere, and the next inventory had no row for it. Now the article keeps
its places and the product its family, and the way back puts them where
they were, as far as location, packs and family still exist.
"""

from decimal import Decimal

import pandas as pd
from django.core.exceptions import ValidationError
from django.test import TestCase

from django_project.testing import administrator
from katalog import ort_split_merge, ware_merge, ware_split, warenfamilie_split_merge
from katalog.models import (
    Artikelstatus,
    Basiseinheit,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Ware,
    Warenfamilie,
    Warengruppe,
)
from katalog.services import (
    EINGESTELLT,
    ERSETZEN,
    UMBENANNT,
    ZURUECKHOLEN,
    als_daten,
    ausfuehren,
    planen,
)
from organisation.models import Bereich, Haus, Ort

SPALTEN = ["artikelnummer", "name", "warengruppe", "gebinde", "anzahl", "einheit", "haus"]

#: The places of Ware N as 'Aufbau' builds them.
PLAETZE = {
    "Thekenfront": ([("Beutel", None), ("Karton", Decimal("3.000"))], False),
    "Trockenlager": ([], True),
}


class Aufbau(TestCase):
    """Ware N in kg with box and bag, at the counter and on the scale in storage."""

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Nord")
        bereich = Bereich.objects.create(name="Theke", haus=cls.haus)
        cls.theke = Ort.objects.create(name="Thekenfront", bereich=bereich)
        cls.lager = Ort.objects.create(name="Trockenlager", bereich=bereich)
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        cls.kg = Basiseinheit.objects.get(name="kg")

    def setUp(self):
        self.familie = Warenfamilie.objects.create(name="Warenfamilie R")
        self.ware = Ware.objects.create(
            name="Ware N", basiseinheit=self.kg, abwiegbar=True, warenfamilie=self.familie
        )
        self.karton = Gebinde.objects.create(
            ware=self.ware, name="Karton", faktor_in_basiseinheit=Decimal("5")
        )
        self.beutel = Gebinde.objects.create(
            ware=self.ware, name="Beutel", faktor_in_basiseinheit=Decimal("1")
        )
        self.artikel = Hausartikel.objects.create(
            haus=self.haus,
            ware=self.ware,
            warengruppe=self.gruppe,
            artikelnummer="101001",
            name="Ware N",
        )
        # Another article first: the restored place comes back behind it.
        andere = Hausartikel.objects.create(
            haus=self.haus,
            ware=Ware.objects.create(name="Ware M", basiseinheit=self.kg),
            warengruppe=self.gruppe,
            artikelnummer="101002",
            name="Ware M",
        )
        Erwartung.objects.create(ort=self.theke, artikel=andere)
        theke = Erwartung.objects.create(ort=self.theke, artikel=self.artikel)
        ErwartungGebinde.objects.create(
            erwartung=theke, gebinde=self.karton, standard_menge=Decimal("3")
        )
        ErwartungGebinde.objects.create(erwartung=theke, gebinde=self.beutel)
        Erwartung.objects.create(ort=self.lager, artikel=self.artikel, wiegen_erwartet=True)

    def plaetze(self) -> dict[str, tuple[list[tuple[str, Decimal | None]], bool]]:
        return {
            erwartung.ort.name: (
                [
                    (vorgabe.gebinde.name, vorgabe.standard_menge)
                    for vorgabe in erwartung.gebinde_vorgaben.order_by("gebinde__name")
                ],
                erwartung.wiegen_erwartet,
            )
            for erwartung in self.artikel.erwartet_an.order_by("ort__name")
        }


class Gedaechtnis(Aufbau):
    def test_nicht_zaehlbar_und_zurueck_bringt_familie_und_plaetze(self):
        self.ware.ignorieren()
        self.artikel.refresh_from_db()
        self.ware.refresh_from_db()
        self.assertEqual(self.plaetze(), {})
        self.assertEqual(self.ware.familie_vorher, self.familie)
        self.assertEqual(len(self.artikel.erwartungen_vorher), 2)

        self.ware.wieder_aufnehmen()

        self.artikel.refresh_from_db()
        self.ware.refresh_from_db()
        self.assertEqual(self.artikel.status, Artikelstatus.AKTIV)
        self.assertEqual(self.ware.warenfamilie, self.familie)
        self.assertIsNone(self.ware.familie_vorher)
        self.assertEqual(
            self.plaetze(),
            {
                "Thekenfront": ([("Beutel", None), ("Karton", Decimal("3.000"))], False),
                "Trockenlager": ([], True),
            },
        )
        self.assertEqual(self.artikel.erwartungen_vorher, [])
        # At its old place, behind the other article.
        namen = [e.artikel.name for e in Erwartung.objects.filter(ort=self.theke)]
        self.assertEqual(namen, ["Ware M", "Ware N"])

    def test_die_erste_ware_bringt_der_familie_ihre_leiteinheit(self):
        """Warenfamilie R has no lead unit yet. The product coming back brings its own.

        As on every other way into a family ('Warenfamilie.aufnehmen'). Until
        10/2026 the way back skipped it, and the family had nothing to sum in.
        """
        self.assertIsNone(self.familie.leiteinheit)
        self.ware.ignorieren()

        Ware.objects.get(pk=self.ware.pk).wieder_aufnehmen()

        self.familie.refresh_from_db()
        self.assertEqual(self.familie.leiteinheit, self.kg)

    def test_eine_familie_die_die_ware_nicht_summieren_kann_nimmt_sie_nicht_zurueck(self):
        """Warenfamilie R went into one that sums in litres, and Ware N knows no litre.

        Until 10/2026 the way back took it there all the same, and the sum of
        the family went empty in the evaluation. Now it says what is missing
        and the product stays where it is.
        """
        liter = Basiseinheit.objects.get(name="Liter")
        familie_l = Warenfamilie.objects.create(name="Warenfamilie L", leiteinheit=liter)
        Ware.objects.create(name="Ware L", basiseinheit=liter, warenfamilie=familie_l)
        self.ware.ignorieren()
        warenfamilie_split_merge.zusammenfuehren(familie_l, self.familie, "Warenfamilie L")
        ware = Ware.objects.get(pk=self.ware.pk)
        self.assertEqual(ware.familie_vorher, familie_l)

        with self.assertRaisesMessage(ValidationError, "„Warenfamilie L“ rechnet in l. Ohne"):
            ware.wieder_aufnehmen()

        ware.refresh_from_db()
        self.assertTrue(ware.ist_nicht_zaehlbar)

    def test_einstellen_und_wieder_aktivieren_bringt_die_plaetze(self):
        self.artikel.einstellen()
        self.assertEqual(self.plaetze(), {})

        self.artikel.aktivieren()

        self.artikel.refresh_from_db()
        self.assertEqual(
            self.plaetze(),
            {
                "Thekenfront": ([("Beutel", None), ("Karton", Decimal("3.000"))], False),
                "Trockenlager": ([], True),
            },
        )
        self.assertEqual(self.artikel.erwartungen_vorher, [])

    def test_was_es_nicht_mehr_gibt_wird_uebergangen(self):
        self.ware.ignorieren()
        self.lager.delete()
        self.beutel.aktiv = False
        self.beutel.save()
        self.familie.delete()

        self.ware.refresh_from_db()
        self.ware.wieder_aufnehmen()

        self.ware.refresh_from_db()
        self.assertIsNone(self.ware.warenfamilie)
        self.assertEqual(self.plaetze(), {"Thekenfront": ([("Karton", Decimal("3.000"))], False)})

    def test_ein_platz_den_es_schon_wieder_gibt_bleibt_wie_er_ist(self):
        """Someone took the article in again by hand. That place is not doubled or changed."""
        self.artikel.einstellen()
        Erwartung.objects.create(ort=self.theke, artikel=self.artikel)

        self.artikel.aktivieren()

        self.assertEqual(self.plaetze(), {"Thekenfront": ([], False), "Trockenlager": ([], True)})

    def test_ein_platz_ohne_gebinde_und_ohne_waage_entsteht_nicht(self):
        self.artikel.einstellen()
        self.ware.abwiegbar = False
        self.ware.save()
        Gebinde.objects.filter(ware=self.ware).update(aktiv=False)

        self.artikel.aktivieren()

        self.assertEqual(self.plaetze(), {})

    def test_ohne_gedaechtnis_kommt_nichts_zurueck(self):
        """A row that left before 10/2026 has no memory and comes back as it did then."""
        self.artikel.einstellen()
        Hausartikel.objects.filter(pk=self.artikel.pk).update(erwartungen_vorher=[])
        self.artikel.refresh_from_db()

        self.artikel.aktivieren()

        self.assertEqual(self.plaetze(), {})

    def test_eingestellt_und_dann_ignoriert_vergisst_die_plaetze_nicht(self):
        self.artikel.einstellen()
        self.ware.ignorieren()
        self.artikel.refresh_from_db()
        self.assertEqual(len(self.artikel.erwartungen_vorher), 2)
        self.assertEqual(self.artikel.status_vorher, Artikelstatus.EINGESTELLT)

        self.ware.refresh_from_db()
        self.ware.wieder_aufnehmen()
        self.artikel.refresh_from_db()
        # Comes back discontinued, the memory waits for "Activate again".
        self.assertEqual(self.artikel.status, Artikelstatus.EINGESTELLT)
        self.assertEqual(len(self.artikel.erwartungen_vorher), 2)

        self.artikel.aktivieren()

        self.assertEqual(set(self.plaetze()), {"Thekenfront", "Trockenlager"})


class GedaechtnisUeberZusammenfuehren(Aufbau):
    """Merging and splitting renumber locations and packs. The memory follows them.

    Until 10/2026 it kept the old numbers: the way back found neither the
    location nor the pack, although both still existed under another number,
    and the article came back without a place.
    """

    def ware_der_zeile(self) -> str:
        return Hausartikel.objects.get(pk=self.artikel.pk).ware.name

    def test_aufteilen_der_ware_fuehrt_auf_die_kopien(self):
        self.artikel.einstellen()

        bericht = ware_split.aufteilen(self.ware, "Ware N", "Ware N neu", [self.artikel])
        self.artikel.refresh_from_db()
        self.artikel.aktivieren()

        self.assertEqual(self.ware_der_zeile(), "Ware N neu")
        self.assertEqual(
            self.plaetze(),
            {
                "Thekenfront": ([("Beutel", None), ("Karton", Decimal("3.000"))], False),
                "Trockenlager": ([], True),
            },
        )
        vorgaben = ErwartungGebinde.objects.filter(erwartung__artikel_id=self.artikel.pk)
        self.assertEqual({v.gebinde.ware_id for v in vorgaben}, {bericht.neu.pk})

    def test_aufteilen_einer_ignorierten_ware_nimmt_auch_die_familie_mit(self):
        self.ware.ignorieren()
        self.artikel.refresh_from_db()

        bericht = ware_split.aufteilen(
            Ware.objects.get(pk=self.ware.pk), "Ware N", "Ware N neu", [self.artikel]
        )
        neu = Ware.objects.get(pk=bericht.neu.pk)
        self.assertEqual(neu.familie_vorher, self.familie)
        neu.wieder_aufnehmen()

        neu.refresh_from_db()
        self.assertEqual(neu.warenfamilie, self.familie)
        self.assertEqual(set(self.plaetze()), {"Thekenfront", "Trockenlager"})

    def test_zusammenfuehren_der_waren_fuehrt_auf_das_verschmolzene_gebinde(self):
        """Ware N goes into Ware P, whose box holds 5 too: the box of P is the remembered one."""
        ziel = Ware.objects.create(name="Ware P", basiseinheit=self.kg, abwiegbar=True)
        karton_p = Gebinde.objects.create(
            ware=ziel, name="Karton", faktor_in_basiseinheit=Decimal("5")
        )
        self.artikel.einstellen()

        ware_merge.zusammenfuehren(self.ware, ziel)
        self.artikel.refresh_from_db()
        self.artikel.aktivieren()

        theke = Erwartung.objects.get(artikel_id=self.artikel.pk, ort=self.theke)
        self.assertEqual(
            {(v.gebinde_id, v.standard_menge) for v in theke.gebinde_vorgaben.all()},
            {(karton_p.pk, Decimal("3.000")), (self.beutel.pk, None)},
        )

    def test_zwei_nicht_zaehlbare_bringen_die_plaetze_der_aufgeloesten_mit(self):
        """Both products in "Not countable", each with a row in Kino Nord that remembers places.

        Merged, the absorbed row comes back discontinued and counts as the
        staying one. Its places come back with that one: at the counter its
        box default adds to the other's, the store brings its scale. Until
        10/2026 they were lost on the way back.
        """
        ziel = Ware.objects.create(
            name="Ware P", basiseinheit=self.kg, abwiegbar=True, warenfamilie=self.familie
        )
        karton_p = Gebinde.objects.create(
            ware=ziel, name="Karton", faktor_in_basiseinheit=Decimal("5")
        )
        bleibt = Hausartikel.objects.create(
            haus=self.haus,
            ware=ziel,
            warengruppe=self.gruppe,
            artikelnummer="101003",
            name="Ware P",
        )
        theke = Erwartung.objects.create(ort=self.theke, artikel=bleibt)
        ErwartungGebinde.objects.create(
            erwartung=theke, gebinde=karton_p, standard_menge=Decimal("2")
        )
        self.ware.ignorieren()
        ziel.ignorieren()

        ware_merge.zusammenfuehren(Ware.objects.get(pk=self.ware.pk), Ware.objects.get(pk=ziel.pk))
        Ware.objects.get(pk=ziel.pk).wieder_aufnehmen()

        bleibt.refresh_from_db()
        self.artikel.refresh_from_db()
        self.assertEqual(bleibt.status, Artikelstatus.AKTIV)
        self.assertEqual(
            (self.artikel.status, self.artikel.zaehlt_als_id),
            (Artikelstatus.EINGESTELLT, bleibt.pk),
        )
        plaetze = {
            erwartung.ort.name: (
                {(v.gebinde.name, v.standard_menge) for v in erwartung.gebinde_vorgaben.all()},
                erwartung.wiegen_erwartet,
            )
            for erwartung in bleibt.erwartet_an.all()
        }
        self.assertEqual(
            plaetze,
            {
                "Thekenfront": ({("Karton", Decimal("5")), ("Beutel", None)}, False),
                "Trockenlager": (set(), True),
            },
        )

    def test_zusammengefuehrte_orte_bringen_den_platz_an_den_bleibenden(self):
        self.artikel.einstellen()

        ort_split_merge.zusammenfuehren(self.theke, self.lager, "Thekenfront")
        self.artikel.refresh_from_db()
        self.artikel.aktivieren()

        # Both places fall onto the counter. It keeps its packs and gains the scale.
        self.assertEqual(
            self.plaetze(),
            {"Thekenfront": ([("Beutel", None), ("Karton", Decimal("3.000"))], True)},
        )

    def test_zusammengefuehrte_familien_bringen_die_ware_in_die_bleibende(self):
        bleibend = Warenfamilie.objects.create(name="Warenfamilie S", leiteinheit=self.kg)
        self.ware.ignorieren()

        warenfamilie_split_merge.zusammenfuehren(bleibend, self.familie, "Warenfamilie S")
        ware = Ware.objects.get(pk=self.ware.pk)
        ware.wieder_aufnehmen()

        ware.refresh_from_db()
        self.assertEqual(ware.warenfamilie, bleibend)


class StelleInDerZaehlliste(Aufbau):
    """The way back puts a place where it stood, not at the end of the list.

    Whoever counts walks the shelf in the order of the list. Until 10/2026 a
    returning article stood last, wherever it was before.
    """

    def setUp(self):
        super().setUp()
        hinten = Hausartikel.objects.create(
            haus=self.haus,
            ware=Ware.objects.create(name="Ware O", basiseinheit=self.kg),
            warengruppe=self.gruppe,
            artikelnummer="101003",
            name="Ware O",
        )
        Erwartung.objects.create(ort=self.theke, artikel=hinten)

    def an_der_theke(self) -> list[str]:
        return [e.artikel.name for e in Erwartung.objects.filter(ort=self.theke)]

    def test_wieder_aktivieren_bringt_die_alte_stelle(self):
        self.artikel.einstellen()
        self.assertEqual(self.an_der_theke(), ["Ware M", "Ware O"])

        self.artikel.aktivieren()

        self.assertEqual(self.an_der_theke(), ["Ware M", "Ware N", "Ware O"])
        sortierung = list(
            Erwartung.objects.filter(ort=self.theke).values_list("sortierung", flat=True)
        )
        self.assertEqual(sortierung, [1, 2, 3])

    def test_aus_nicht_zaehlbar_an_die_alte_stelle(self):
        self.ware.ignorieren()

        Ware.objects.get(pk=self.ware.pk).wieder_aufnehmen()

        self.assertEqual(self.an_der_theke(), ["Ware M", "Ware N", "Ware O"])

    def test_neu_nummeriert_und_hinten_ergaenzt_zaehlt_die_folge(self):
        """Dragging numbered the list anew and an article came in at the end meanwhile."""
        self.artikel.einstellen()
        folge = list(Erwartung.objects.filter(ort=self.theke).values_list("pk", flat=True))
        Erwartung.neu_sortieren(self.theke.pk, folge)
        neu = Hausartikel.objects.create(
            haus=self.haus,
            ware=Ware.objects.create(name="Ware P", basiseinheit=self.kg),
            warengruppe=self.gruppe,
            artikelnummer="101004",
            name="Ware P",
        )
        Erwartung.objects.create(ort=self.theke, artikel=neu)

        self.artikel.aktivieren()

        self.assertEqual(self.an_der_theke(), ["Ware M", "Ware N", "Ware O", "Ware P"])

    def test_ein_gedaechtnis_ohne_stelle_kommt_ans_ende(self):
        """A row that left before 2026-10-18 remembers no position. It comes back as before."""
        self.artikel.einstellen()
        self.artikel.refresh_from_db()
        ohne = [
            {schluessel: wert for schluessel, wert in platz.items() if schluessel != "stelle"}
            for platz in self.artikel.erwartungen_vorher
        ]
        Hausartikel.objects.filter(pk=self.artikel.pk).update(erwartungen_vorher=ohne)
        self.artikel.refresh_from_db()

        self.artikel.aktivieren()

        self.assertEqual(self.an_der_theke(), ["Ware M", "Ware O", "Ware N"])

    def test_ein_platz_am_aufgegangenen_ort_kommt_ans_ende_des_bleibenden(self):
        """Its position meant the storage list. In the cellar's list it means nothing."""
        keller = Ort.objects.create(name="Keller", bereich=self.theke.bereich)
        for nummer, name in (("101005", "Ware K"), ("101006", "Ware L")):
            Erwartung.objects.create(
                ort=keller,
                artikel=Hausartikel.objects.create(
                    haus=self.haus,
                    ware=Ware.objects.create(name=name, basiseinheit=self.kg),
                    warengruppe=self.gruppe,
                    artikelnummer=nummer,
                    name=name,
                ),
            )
        self.artikel.einstellen()

        ort_split_merge.zusammenfuehren(keller, self.lager, "Keller")
        self.artikel.refresh_from_db()
        self.artikel.aktivieren()

        im_keller = [e.artikel.name for e in Erwartung.objects.filter(ort=keller)]
        self.assertEqual(im_keller, ["Ware K", "Ware L", "Ware N"])
        self.assertEqual(self.an_der_theke(), ["Ware M", "Ware N", "Ware O"])


class UeberDenImport(Aufbau):
    """The import's ways to a discontinued row. Until 10/2026 only 'aktivieren' was tested."""

    def plan(self, name: str):
        tabelle = pd.DataFrame(
            [["101001", name, "Warengruppe G", "Karton", 5.0, "KILOGRAMM", "Kino Nord"]],
            columns=SPALTEN,
        )
        # Ware M of the same product group comes along as missing from the file.
        (satz,) = [satz for satz in planen(tabelle, self.haus)[0] if satz.artikelnummer == "101001"]
        return satz

    def test_zurueckholen_bringt_die_plaetze(self):
        self.artikel.einstellen()
        satz = self.plan("Ware N")
        self.assertEqual(satz.art, EINGESTELLT)

        bericht = ausfuehren([als_daten(satz) | {"vorgehen": ZURUECKHOLEN}], self.haus)

        self.assertEqual(bericht.artikel_zurueckgeholt, ["101001 Ware N"])
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.status, Artikelstatus.AKTIV)
        self.assertEqual(self.plaetze(), PLAETZE)
        self.assertEqual(self.artikel.erwartungen_vorher, [])

    def test_ersetzen_merkt_sich_die_plaetze_des_alten(self):
        """The new row starts without places. The old one keeps them for the way back."""
        satz = self.plan("Ware P")
        self.assertEqual(satz.art, UMBENANNT)

        ausfuehren(
            [als_daten(satz) | {"vorgehen": ERSETZEN, "ware": None, "neuer_ware": "Ware P"}],
            self.haus,
        )

        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.status, Artikelstatus.EINGESTELLT)
        self.assertEqual(self.plaetze(), {})
        self.assertEqual(len(self.artikel.erwartungen_vorher), 2)
        neu = Hausartikel.objects.get(artikelnummer="101001", name="Ware P")
        self.assertFalse(neu.erwartet_an.exists())


class SeiteDesArtikels(Aufbau):
    """The article's page says whether its places come back. Only a memory knows them.

    A row that left before the memory existed comes back without places, and
    until 10/2026 every text promised them all the same.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = administrator()

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def seite(self) -> str:
        return self.client.get(self.artikel.get_absolute_url()).content.decode()

    def vergessen(self):
        """As a row that left before the memory: nothing remembered."""
        Hausartikel.objects.filter(pk=self.artikel.pk).update(erwartungen_vorher=[])

    def test_eingestellt_mit_und_ohne_gemerkte_plaetze(self):
        self.artikel.einstellen()
        gemerkt = "Seine Plätze in den Zähllisten kommen dabei an ihre alte Stelle zurück"
        self.assertIn(gemerkt, self.seite())
        self.vergessen()
        seite = self.seite()
        self.assertIn("Plätze in den Zähllisten hat er sich keine gemerkt.", seite)
        self.assertNotIn(gemerkt, seite)

    def test_nicht_zaehlbar_mit_und_ohne_gemerkte_plaetze(self):
        self.ware.ignorieren()
        gemerkt = "Mit „Wieder aufnehmen“ kommen seine Plätze"
        self.assertIn(gemerkt, self.seite())
        self.vergessen()
        seite = self.seite()
        self.assertIn("Plätze in den Zähllisten hat er sich keine gemerkt.", seite)
        self.assertNotIn(gemerkt, seite)
