"""The counting list in one section per inventory type, and where an article goes on a change.

Wish of 2026-10-09: the list is divided by type like the tabs of the app,
and an article that changes its type goes to the end of the new one at
every location that expects it. Changed is the type on many paths. Each
of them is checked here once.
"""

import re
from decimal import Decimal

from django.urls import reverse

from django_project.testing import AngemeldeteSeiten, jahresinventur, monatsinventur
from organisation.models import Bereich, Haus, Ort

from .forms import HausartikelForm, ZaehllisteFormSet
from .models import (
    Basiseinheit,
    Dimension,
    Erwartung,
    Hausartikel,
    Inventurart,
    Rezepturartikel,
    Rezepturkomponente,
    Ware,
    Warengruppe,
    neue_art_ans_ende,
)
from .ware_merge import zusammenfuehren


class Grundlage(AngemeldeteSeiten):
    """Kino Nord with a counter and a store, Kino Ost with a counter. All monthly at first.

    The counter of Kino Nord expects Artikel C, Ware D, the portion, Ware E
    and Ware F, in this order. The portion is made of E and F. The store
    expects D and Artikel C. Kino Ost carries Artikel C too and expects it
    before Ware G.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.monat = monatsinventur()
        cls.jahr = jahresinventur()
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        cls.nord = Haus.objects.create(name="Kino Nord")
        cls.ost = Haus.objects.create(name="Kino Ost")
        cls.theke = cls.ort(cls.nord, "Theke")
        cls.lager = cls.ort(cls.nord, "Lager")
        cls.ost_theke = cls.ort(cls.ost, "Theke")

        cls.cola = Ware.objects.create(name="Artikel C", basiseinheit=cls.stueck)
        cls.cola_nord = cls.artikel(cls.cola, cls.nord, "101001")
        cls.cola_ost = cls.artikel(cls.cola, cls.ost, "101001")
        cls.d = cls.artikel(cls.ware("Ware D"), cls.nord, "101002")
        cls.e = cls.artikel(cls.ware("Ware E"), cls.nord, "101003")
        cls.f = cls.artikel(cls.ware("Ware F"), cls.nord, "101004")
        cls.g = cls.artikel(cls.ware("Ware G"), cls.ost, "101005")
        cls.portion = Rezepturartikel.objects.create(name="Portion N", basiseinheit=cls.stueck)
        for zutat in (cls.e, cls.f):
            Rezepturkomponente.objects.create(
                rezepturartikel=cls.portion, ware=zutat.ware, menge_in_basiseinheit=Decimal("1")
            )

        for artikel in (cls.cola_nord, cls.d, cls.portion, cls.e, cls.f):
            Erwartung.objects.create(ort=cls.theke, artikel_id=artikel.pk)
        for artikel in (cls.d, cls.cola_nord):
            Erwartung.objects.create(ort=cls.lager, artikel_id=artikel.pk)
        for artikel in (cls.cola_ost, cls.g):
            Erwartung.objects.create(ort=cls.ost_theke, artikel_id=artikel.pk)

    @classmethod
    def ort(cls, haus, name) -> Ort:
        return Ort.objects.create(name=name, bereich=Bereich.objects.create(name=name, haus=haus))

    @classmethod
    def ware(cls, name) -> Ware:
        return Ware.objects.create(name=name, basiseinheit=cls.stueck)

    @classmethod
    def artikel(cls, ware, haus, nummer) -> Hausartikel:
        return Hausartikel.objects.create(
            haus=haus, ware=ware, warengruppe=cls.gruppe, artikelnummer=nummer, name=ware.name
        )

    def abschnitte(self, ort) -> list[tuple[str, list[str]]]:
        """The sections as the page shows them: per type the names of its rows."""
        formset = ZaehllisteFormSet(prefix="zeilen", ort=ort)
        return [
            (art.name, [zeile.erwartung_objekt.artikel.name for zeile in zeilen])
            for art, zeilen in formset.abschnitte()
        ]

    def folge(self, ort) -> list[str]:
        """The stored order of the location, across all sections."""
        return list(
            Erwartung.objects.filter(ort=ort)
            .order_by("sortierung")
            .values_list("artikel__name", flat=True)
        )

    def erwartung(self, ort, artikel) -> Erwartung:
        return Erwartung.objects.get(ort=ort, artikel_id=artikel.pk)


class Abschnitte(Grundlage):
    def test_je_art_ein_abschnitt_in_der_folge_der_arten(self):
        self.d.inventurart_setzen(self.jahr)
        self.assertEqual(
            self.abschnitte(self.theke),
            [
                ("Monatsinventur", ["Artikel C", "Portion N", "Ware E", "Ware F"]),
                ("Jahresinventur", ["Ware D"]),
            ],
        )

    def test_die_folge_der_arten_bestimmt_die_der_abschnitte(self):
        self.d.inventurart_setzen(self.jahr)
        self.jahr.verschieben(-1)
        self.assertEqual(
            [name for name, _ in self.abschnitte(self.theke)], ["Jahresinventur", "Monatsinventur"]
        )

    def test_nur_arten_mit_zeilen(self):
        self.assertEqual([name for name, _ in self.abschnitte(self.theke)], ["Monatsinventur"])

    def test_rezepturartikel_steht_unter_der_ersten_art_seiner_zutaten(self):
        self.e.inventurart_setzen(self.jahr)
        self.assertIn("Portion N", dict(self.abschnitte(self.theke))["Monatsinventur"])
        self.f.inventurart_setzen(self.jahr)
        self.assertIn("Portion N", dict(self.abschnitte(self.theke))["Jahresinventur"])

    def test_die_seite_zeigt_ueberschriften_und_zieht_je_abschnitt(self):
        """A row cannot be dragged into another type: every section is a list of its own."""
        self.d.inventurart_setzen(self.jahr)
        html = self.client.get(reverse("zaehlliste", args=[self.theke.pk])).content.decode()
        ueberschriften = re.findall(r'scope="rowgroup"[^>]*>\s*([^<]+?)\s*<', html)
        self.assertEqual(ueberschriften, ["Monatsinventur", "Jahresinventur"])
        self.assertEqual(html.count("data-sortierbar"), 2)

    def test_mit_nur_einer_art_keine_ueberschrift(self):
        """With one type there is nothing to divide."""
        self.jahr.delete()
        html = self.client.get(reverse("zaehlliste", args=[self.theke.pk])).content.decode()
        self.assertNotIn('scope="rowgroup"', html)
        self.assertEqual(html.count("data-sortierbar"), 1)

    def test_die_pfeile_enden_am_rand_des_abschnitts(self):
        self.d.inventurart_setzen(self.jahr)
        html = self.client.get(reverse("zaehlliste", args=[self.theke.pk])).content.decode()

        def gesperrt(knopf):
            muster = rf'name="{knopf}" value="(\d+)"[^>]*disabled'
            return {int(pk) for pk in re.findall(muster, html)}

        cola, d, f = (self.erwartung(self.theke, a).pk for a in (self.cola_nord, self.d, self.f))
        self.assertEqual(gesperrt("hoch"), {cola, d})
        self.assertEqual(gesperrt("runter"), {f, d})


class KnoepfeImAbschnitt(Grundlage):
    """The buttons of a row move within its type, the other sections keep their places."""

    def setUp(self):
        super().setUp()
        # Monthly: Artikel C, Portion N, Ware E, Ware F. Annually: Ware D.
        self.d.inventurart_setzen(self.jahr)
        self.liste = reverse("zaehlliste", args=[self.theke.pk])

    def senden(self, **knopf):
        formset = ZaehllisteFormSet(prefix="zeilen", ort=self.theke)
        daten = {
            "zeilen-TOTAL_FORMS": str(len(formset.folge)),
            "zeilen-INITIAL_FORMS": str(len(formset.folge)),
            "zeilen-MIN_NUM_FORMS": "0",
            "zeilen-MAX_NUM_FORMS": "1000",
            "reihenfolge": [str(pk) for pk in formset.folge],
        }
        for stelle, pk in enumerate(formset.folge):
            daten[f"zeilen-{stelle}-erwartung"] = str(pk)
        antwort = self.client.post(
            self.liste, daten | {name: str(pk) for name, pk in knopf.items()}
        )
        self.assertRedirects(antwort, self.liste)

    def test_nach_unten_am_ende_des_abschnitts_aendert_nichts(self):
        """Before, the step swapped with the first row of the next type."""
        vorher = self.folge(self.theke)
        self.senden(runter=self.erwartung(self.theke, self.f).pk)
        self.assertEqual(self.folge(self.theke), vorher)

    def test_nach_oben_am_anfang_des_abschnitts_aendert_nichts(self):
        vorher = self.folge(self.theke)
        self.senden(hoch=self.erwartung(self.theke, self.d).pk)
        self.assertEqual(self.folge(self.theke), vorher)

    def test_ganz_nach_unten_geht_ans_ende_des_abschnitts(self):
        self.senden(ganz_runter=self.erwartung(self.theke, self.cola_nord).pk)
        self.assertEqual(
            self.folge(self.theke), ["Portion N", "Ware E", "Ware F", "Artikel C", "Ware D"]
        )

    def test_ganz_nach_oben_geht_an_den_anfang_des_abschnitts(self):
        self.senden(ganz_hoch=self.erwartung(self.theke, self.f).pk)
        self.assertEqual(
            self.folge(self.theke), ["Ware F", "Artikel C", "Portion N", "Ware E", "Ware D"]
        )

    def test_ein_schritt_im_abschnitt(self):
        self.senden(runter=self.erwartung(self.theke, self.cola_nord).pk)
        self.assertEqual(
            self.abschnitte(self.theke)[0][1], ["Portion N", "Artikel C", "Ware E", "Ware F"]
        )


class AnsEndeDerNeuenArt(Grundlage):
    """An article that changes its type stands behind the rows the new type already has."""

    def test_an_jedem_ort(self):
        self.d.inventurart_setzen(self.jahr)
        self.cola_nord.inventurart_setzen(self.jahr)
        self.assertEqual(
            dict(self.abschnitte(self.theke))["Jahresinventur"], ["Ware D", "Artikel C"]
        )
        self.assertEqual(self.abschnitte(self.lager), [("Jahresinventur", ["Ware D", "Artikel C"])])

    def test_nur_dieser_artikel_laesst_die_anderen_haeuser_stehen(self):
        self.cola_nord.inventurart_setzen(self.jahr)
        self.assertEqual(self.folge(self.ost_theke), ["Artikel C", "Ware G"])

    def test_fuer_die_ganze_ware_in_jedem_haus(self):
        self.cola_nord.inventurart_setzen(self.jahr, ganze_ware=True)
        self.assertEqual(self.folge(self.ost_theke), ["Ware G", "Artikel C"])

    def test_an_der_ware(self):
        self.cola.inventurart_setzen(self.jahr)
        self.assertEqual(self.folge(self.ost_theke), ["Ware G", "Artikel C"])
        self.assertEqual(
            self.folge(self.theke), ["Ware D", "Portion N", "Ware E", "Ware F", "Artikel C"]
        )

    def test_an_der_warengruppe_behalten_mehrere_ihre_folge(self):
        self.d.inventurart_setzen(self.jahr)
        self.gruppe.inventurart_setzen(self.jahr)
        self.assertEqual(
            self.abschnitte(self.theke),
            [("Jahresinventur", ["Ware D", "Artikel C", "Portion N", "Ware E", "Ware F"])],
        )

    def test_was_seine_art_behaelt_bleibt_wo_es_ist(self):
        vorher = list(Erwartung.objects.order_by("pk").values_list("pk", "sortierung"))
        self.d.inventurart_setzen(self.monat)
        self.cola.inventurart_setzen(self.monat)
        nachher = list(Erwartung.objects.order_by("pk").values_list("pk", "sortierung"))
        self.assertEqual(nachher, vorher)

    def test_die_uebrigen_behalten_ihre_stelle(self):
        stellen = dict(Erwartung.objects.values_list("pk", "sortierung"))
        self.d.inventurart_setzen(self.jahr)
        geaendert = {
            pk
            for pk, stelle in Erwartung.objects.values_list("pk", "sortierung")
            if stellen[pk] != stelle
        }
        self.assertEqual(
            geaendert,
            {self.erwartung(self.theke, self.d).pk, self.erwartung(self.lager, self.d).pk},
        )

    def test_der_rezepturartikel_wechselt_mit_seiner_zutat(self):
        self.e.inventurart_setzen(self.jahr)
        # F is still monthly, so the portion stays where it was.
        self.assertEqual(
            self.abschnitte(self.theke),
            [
                ("Monatsinventur", ["Artikel C", "Ware D", "Portion N", "Ware F"]),
                ("Jahresinventur", ["Ware E"]),
            ],
        )
        self.f.inventurart_setzen(self.jahr)
        self.assertEqual(
            dict(self.abschnitte(self.theke))["Jahresinventur"], ["Ware E", "Portion N", "Ware F"]
        )

    def test_beim_tausch_zweier_arten_wechselt_der_rezepturartikel(self):
        """The portion stands under the first type of its ingredients. Swapped, that is another."""
        self.d.inventurart_setzen(self.jahr)
        self.f.inventurart_setzen(self.jahr)
        self.jahr.verschieben(-1)
        self.assertEqual(
            self.abschnitte(self.theke),
            [
                ("Jahresinventur", ["Ware D", "Ware F", "Portion N"]),
                ("Monatsinventur", ["Artikel C", "Ware E"]),
            ],
        )

    def test_beim_loeschen_einer_art_hinter_die_der_nachfolgerin(self):
        becher = Inventurart.objects.create(name="Becherinventur")
        self.d.inventurart_setzen(becher)
        self.f.inventurart_setzen(self.jahr)
        becher.loeschen(self.jahr)
        self.assertEqual(dict(self.abschnitte(self.theke))["Jahresinventur"], ["Ware F", "Ware D"])

    def test_im_formular_des_hausartikels(self):
        self.f.inventurart_setzen(self.jahr)
        daten = {
            "quelle": self.d.quelle,
            "artikelnummer": self.d.artikelnummer,
            "name": self.d.name,
            "ware": self.d.ware_id,
            "neuer_ware": "",
            "basiseinheit": "",
            "warengruppe": self.d.warengruppe_id,
            "neue_warengruppe": "",
            "ean": "",
            "inventurart": self.jahr.pk,
        }
        form = HausartikelForm(daten, instance=self.d)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(dict(self.abschnitte(self.theke))["Jahresinventur"], ["Ware F", "Ware D"])

    def test_beim_zusammenfuehren_von_waren(self):
        """The articles of the dissolved product take the type of the remaining one."""
        self.f.inventurart_setzen(self.jahr)
        zweite = self.artikel(self.ware("Ware F2"), self.ost, "101006")
        Erwartung.objects.create(ort=self.ost_theke, artikel_id=zweite.pk)
        self.cola.inventurart_setzen(self.jahr)
        zusammenfuehren(zweite.ware, self.f.ware)
        self.assertEqual(self.folge(self.ost_theke), ["Ware G", "Artikel C", "Ware F2"])

    def test_verschachtelt_wirkt_nur_der_aeussere(self):
        """The form wraps the article's own method. Together they move once."""
        self.f.inventurart_setzen(self.jahr)
        with neue_art_ans_ende():
            self.d.inventurart_setzen(self.jahr)
            self.cola_nord.inventurart_setzen(self.jahr)
        # In their old order, not in the order of the calls.
        self.assertEqual(
            dict(self.abschnitte(self.theke))["Jahresinventur"], ["Ware F", "Artikel C", "Ware D"]
        )
