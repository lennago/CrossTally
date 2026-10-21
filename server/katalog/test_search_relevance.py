"""While someone searches, the hits come by how well they match.

First what begins with the term, then what contains it, then what only
matches through a number, and last what a product group or a family brings
along. The numbers of the articles here run **against** that order: sorted
by number, as the list does without a search, every case would come out
wrong.
"""

from django.urls import reverse

from django_project.testing import AngemeldeteSeiten
from organisation.models import Bereich, Haus, Ort

from .models import (
    Basiseinheit,
    Dimension,
    Hausartikel,
    Rezepturartikel,
    Rezepturkomponente,
    Ware,
    Warenfamilie,
    Warengruppe,
)


class Regal(AngemeldeteSeiten):
    """A shelf of lemonades, and next to it what only shares the group with them."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.ost = Haus.objects.create(name="Kino Ost")
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.limos = Warengruppe.objects.create(name="Warengruppe Limo")
        cls.anderes = Warengruppe.objects.create(name="Warengruppe W")

        cls.saft = cls.artikel("101001", "Apfelsaft", cls.limos)
        cls.zitrone = cls.artikel("101002", "Zitronenlimo", cls.limos)
        cls.orange = cls.artikel("101003", "Limo Orange", cls.limos)
        cls.wasser = cls.artikel("101004", "Wasser", cls.anderes)
        cls.liste = reverse("hausartikel_list", args=[cls.haus.pk])

    @classmethod
    def artikel(cls, nummer, name, gruppe, *, ware=None, haus=None, familie=None):
        ware_satz, _ = Ware.objects.get_or_create(
            name=ware or name, defaults={"basiseinheit": cls.stueck, "warenfamilie": familie}
        )
        return Hausartikel.objects.create(
            haus=haus or cls.haus,
            ware=ware_satz,
            warengruppe=gruppe,
            artikelnummer=nummer,
            name=name,
        )

    def namen(self, **parameter):
        antwort = self.client.get(self.liste, parameter)
        return [artikel.name for artikel in antwort.context["hausartikel"]]


class HausartikelNachRelevanz(Regal):
    def test_ohne_suche_bleibt_es_bei_der_nummer(self):
        self.assertEqual(self.namen(), ["Apfelsaft", "Zitronenlimo", "Limo Orange", "Wasser"])

    def test_erst_der_anfang_dann_der_rest_der_bezeichnung_dann_die_gruppe(self):
        self.assertEqual(self.namen(q="limo"), ["Limo Orange", "Zitronenlimo", "Apfelsaft"])

    def test_gross_und_klein_zaehlen_nicht(self):
        self.assertEqual(self.namen(q="LIMO"), ["Limo Orange", "Zitronenlimo", "Apfelsaft"])

    def test_der_name_der_ware_zaehlt_wie_die_bezeichnung(self):
        """Whoever searches knows the one or the other."""
        self.artikel("101000", "Eigenmarke gelb", self.anderes, ware="Limo Zitrone")
        self.assertEqual(
            self.namen(q="limo"),
            # Both begin with the term. Between them the number decides.
            ["Eigenmarke gelb", "Limo Orange", "Zitronenlimo", "Apfelsaft"],
        )

    def test_die_bezeichnung_kommt_vor_der_nummer(self):
        self.artikel("101077", "Becher", self.anderes)
        self.artikel("101099", "Tüte 77 g", self.anderes)
        self.assertEqual(self.namen(q="77"), ["Tüte 77 g", "Becher"])

    def test_die_nummer_kommt_vor_der_gruppe(self):
        gruppe = Warengruppe.objects.create(name="Warengruppe 1015")
        self.artikel("101900", "Deckel", gruppe)
        self.artikel("101500", "Strohhalm", self.anderes)
        self.assertEqual(self.namen(q="1015"), ["Strohhalm", "Deckel"])

    def test_die_familie_kommt_zuletzt(self):
        familie = Warenfamilie.objects.create(name="Warenfamilie Limo", leiteinheit=self.stueck)
        self.artikel("101000", "Eistee", self.anderes, familie=familie)
        self.assertEqual(
            self.namen(q="limo"), ["Limo Orange", "Zitronenlimo", "Eistee", "Apfelsaft"]
        )

    def test_innerhalb_einer_stufe_bleibt_die_reihenfolge_der_liste(self):
        self.artikel("101000", "Limo Kirsche", self.limos)
        self.assertEqual(self.namen(q="limo")[:2], ["Limo Kirsche", "Limo Orange"])

    # -- When the columns sort after all -----------------------------------

    def test_wer_eine_spalte_waehlt_sortiert_nach_ihr(self):
        """A click on a header is a decision. The search does not overrule it."""
        self.assertEqual(
            self.namen(q="limo", sortierung="nummer"),
            ["Apfelsaft", "Zitronenlimo", "Limo Orange"],
        )
        self.assertEqual(
            self.namen(q="limo", sortierung="-name"),
            ["Zitronenlimo", "Limo Orange", "Apfelsaft"],
        )

    def test_gegliedert_sortiert_die_gliederung(self):
        antwort = self.client.get(self.liste, {"q": "limo", "gliederung": "warengruppe"})
        self.assertEqual(
            [artikel.name for artikel in antwort.context["hausartikel"]],
            ["Apfelsaft", "Zitronenlimo", "Limo Orange"],
        )

    # -- What the headers say ------------------------------------------------

    def test_beim_suchen_traegt_kein_kopf_den_pfeil(self):
        """No column sorts then. A marked one would claim the opposite."""
        antwort = self.client.get(self.liste, {"q": "limo"})
        self.assertTrue(antwort.context["filterform"].nach_relevanz)
        self.assertNotContains(antwort, 'aria-sort="ascending"')
        self.assertNotContains(antwort, 'aria-sort="descending"')
        # The first click on a header sorts by it, ascending.
        self.assertContains(antwort, "sortierung=nummer")

    def test_ohne_suche_traegt_ihn_die_nummer(self):
        antwort = self.client.get(self.liste)
        self.assertFalse(antwort.context["filterform"].nach_relevanz)
        self.assertContains(antwort, 'aria-sort="ascending"', count=1)

    def test_mit_gewaehlter_spalte_traegt_sie_ihn_auch_beim_suchen(self):
        antwort = self.client.get(self.liste, {"q": "limo", "sortierung": "-name"})
        self.assertFalse(antwort.context["filterform"].nach_relevanz)
        self.assertContains(antwort, 'aria-sort="descending"', count=1)

    def test_ein_veraltetes_lesezeichen_waehlt_keine_spalte(self):
        """An unknown column falls back to the default, and with a search to the relevance."""
        self.assertEqual(
            self.namen(q="limo", sortierung="gibtesnicht"),
            ["Limo Orange", "Zitronenlimo", "Apfelsaft"],
        )


class AuswahlreiterNachRelevanz(Regal):
    """The selection tab of the counting list filters with the same form."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.ort = Ort.objects.create(
            name="Thekenfront", bereich=Bereich.objects.create(name="Theke", haus=cls.haus)
        )
        cls.auswahl = reverse("zaehlliste", args=[cls.ort.pk])

    def test_die_hausartikel_kommen_nach_relevanz(self):
        antwort = self.client.get(self.auswahl, {"reiter": "auswahl", "q": "limo"})
        self.assertEqual(
            [artikel.name for artikel in antwort.context["auswahl_hausartikel"]],
            ["Limo Orange", "Zitronenlimo", "Apfelsaft"],
        )

    def test_die_rezepturartikel_ebenso(self):
        for name in ("Hauslimo", "Limo im Becher"):
            portion = Rezepturartikel.objects.create(name=name, basiseinheit=self.stueck)
            Rezepturkomponente.objects.create(
                rezepturartikel=portion, ware=self.orange.ware, menge_in_basiseinheit=1
            )
        antwort = self.client.get(self.auswahl, {"reiter": "auswahl", "q": "limo"})
        self.assertEqual(
            [artikel.name for artikel in antwort.context["auswahl_rezepturartikel"]],
            ["Limo im Becher", "Hauslimo"],
        )


class WarenNachRelevanz(Regal):
    def namen(self, **parameter):
        antwort = self.client.get(reverse("ware_list"), parameter)
        return [ware.name for ware in antwort.context["ware"]]

    def test_erst_der_name_dann_die_haeuser_dann_die_gruppe(self):
        # The product is called differently, one of its houses calls it by the term.
        self.artikel("102001", "Limonade gelb", self.anderes, ware="Brause", haus=self.ost)
        self.assertEqual(
            self.namen(q="limo"), ["Limo Orange", "Brause", "Zitronenlimo", "Apfelsaft"]
        )

    def test_eine_ware_in_zwei_haeusern_steht_einmal_da(self):
        """One house names it by the term, the other does not: two tiers, one row."""
        self.artikel("102001", "Limonade gelb", self.anderes, ware="Brause", haus=self.ost)
        self.artikel("101050", "Sprudel", self.anderes, ware="Brause")
        self.assertEqual(self.namen(q="limo").count("Brause"), 1)

    def test_die_nummer_eines_hauses_kommt_vor_der_gruppe(self):
        gruppe = Warengruppe.objects.create(name="Warengruppe 1015")
        self.artikel("101900", "Deckel", gruppe)
        self.artikel("101500", "Strohhalm", self.anderes)
        self.assertEqual(self.namen(q="1015"), ["Strohhalm", "Deckel"])

    def test_wer_eine_spalte_waehlt_sortiert_nach_ihr(self):
        self.assertEqual(
            self.namen(q="limo", sortierung="name"),
            ["Apfelsaft", "Limo Orange", "Zitronenlimo"],
        )


class KurzeListenNachRelevanz(Regal):
    def test_warengruppen_erst_was_so_beginnt(self):
        Warengruppe.objects.create(name="Limo und Saft")
        self.artikel("101010", "Schorle", Warengruppe.objects.get(name="Limo und Saft"))
        antwort = self.client.get(reverse("warengruppe_list"), {"q": "limo"})
        self.assertEqual(
            [gruppe.name for gruppe in antwort.context["warengruppen"]],
            ["Limo und Saft", "Warengruppe Limo"],
        )

    def test_rezepturartikel_erst_der_name_dann_die_zutat(self):
        for name, ware in (
            ("Becher gemischt", self.orange.ware),
            ("Hauslimo", self.wasser.ware),
            ("Limo im Becher", self.wasser.ware),
        ):
            portion = Rezepturartikel.objects.create(name=name, basiseinheit=self.stueck)
            Rezepturkomponente.objects.create(
                rezepturartikel=portion, ware=ware, menge_in_basiseinheit=1
            )
        antwort = self.client.get(reverse("rezepturartikel_list"), {"q": "limo"})
        self.assertEqual(
            [artikel.name for artikel in antwort.context["rezepturartikel"]],
            ["Limo im Becher", "Hauslimo", "Becher gemischt"],
        )
