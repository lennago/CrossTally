"""Filters, groupings and search fields of the lists.

Here stands what goes beyond the individual list: the multiple selections
for product family and product group (product, house article and the
selection tab of the counting list share them), the grouping including
remembered expanders and the search fields of the short lists.
"""

from decimal import Decimal
from unittest import skipUnless

from django.db import connection
from django.urls import reverse

from django_project.testing import AngemeldeteSeiten, skript
from organisation.models import Bereich, Haus, Ort

from .forms import HausartikelFilterForm
from .models import (
    Artikelstatus,
    Basiseinheit,
    Behaelter,
    Dimension,
    Hausartikel,
    Rezepturartikel,
    Rezepturkomponente,
    Ware,
    Warenfamilie,
    Warengruppe,
)


class Sortiment(AngemeldeteSeiten):
    """A house with three groups and two families: the basis of all cases here."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Filterhaus")
        cls.anderes = Haus.objects.create(name="Nebenan")
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.getraenke = Warengruppe.objects.create(name="Warengruppe G")
        cls.suesses = Warengruppe.objects.create(name="Warengruppe T")
        cls.merch = Warengruppe.objects.create(name="Warengruppe N")
        cls.limo = Warenfamilie.objects.create(name="Warenfamilie M", leiteinheit=cls.stueck)
        cls.riegel = Warenfamilie.objects.create(name="Warenfamilie R", leiteinheit=cls.stueck)
        cls.nummer = 200000

        cls.cola = cls.artikel("Artikel C", cls.getraenke, cls.limo)
        cls.fassbrause = cls.artikel("Artikel F", cls.getraenke, cls.limo)
        cls.schokoriegel = cls.artikel("Ware S", cls.suesses, cls.riegel)
        cls.becher = cls.artikel("Artikel B", cls.merch, None)
        cls.liste = reverse("hausartikel_list", args=[cls.haus.pk])

    @classmethod
    def artikel(cls, name, gruppe, familie, haus=None):
        cls.nummer += 1
        ware, _ = Ware.objects.get_or_create(
            name=name, defaults={"basiseinheit": cls.stueck, "warenfamilie": familie}
        )
        return Hausartikel.objects.create(
            haus=haus or cls.haus,
            ware=ware,
            warengruppe=gruppe,
            artikelnummer=str(cls.nummer),
            name=name,
        )

    def namen(self, adresse=None, **parameter):
        antwort = self.client.get(adresse or self.liste, parameter)
        return [artikel.name for artikel in antwort.context["hausartikel"]]


class HausartikelNachFamilieUndGruppe(Sortiment):
    def test_filter_suche_und_sortierung(self):
        alle = ["Artikel C", "Artikel F", "Ware S", "Artikel B"]
        faelle = (
            ("ohne Angabe stehen alle da", {}, alle),
            (
                "mehrere Warengruppen auf einmal",
                {"warengruppe": [self.getraenke.pk, self.merch.pk]},
                ["Artikel C", "Artikel F", "Artikel B"],
            ),
            # Old bookmarks carry exactly one number.
            (
                "eine einzelne Warengruppe geht weiter",
                {"warengruppe": self.suesses.pk},
                ["Ware S"],
            ),
            (
                "mehrere Familien und „ohne Familie“",
                {"familie": [self.riegel.pk, "ohne"]},
                ["Ware S", "Artikel B"],
            ),
            ("eine Familie", {"familie": [self.limo.pk]}, ["Artikel C", "Artikel F"]),
            (
                "Familie und Gruppe schneiden sich",
                {"familie": [self.limo.pk, self.riegel.pk], "warengruppe": [self.suesses.pk]},
                ["Ware S"],
            ),
            # A number that does not exist does not drag the other filters down.
            ("unbekannte Wahl wird übergangen", {"familie": ["99999"]}, alle),
            ("sortiert nach Familie, leere hinten", {"sortierung": "familie"}, alle),
            (
                "absteigend bleiben die leeren hinten",
                {"sortierung": "-familie"},
                ["Ware S", "Artikel C", "Artikel F", "Artikel B"],
            ),
            ("die Suche findet die Familie", {"q": "familie m"}, ["Artikel C", "Artikel F"]),
            ("die Suche findet die Warengruppe", {"q": "gruppe t"}, ["Ware S"]),
        )
        for fall, angaben, erwartet in faelle:
            with self.subTest(fall=fall):
                self.assertEqual(self.namen(**angaben), erwartet)

    def test_zur_wahl_steht_nur_was_das_haus_fuehrt(self):
        fremd = Warengruppe.objects.create(name="Nur nebenan")
        familie = Warenfamilie.objects.create(name="Warenfamilie E", leiteinheit=self.stueck)
        self.artikel("Ware M", fremd, familie, haus=self.anderes)
        form = HausartikelFilterForm({}, haus=self.haus)
        self.assertNotIn("Nur nebenan", [name for _, name in form.gruppenwahl.eintraege])
        familien = [name for _, name in form.familienwahl.eintraege]
        self.assertEqual(familien, ["Warenfamilie M", "Warenfamilie R", "Ohne Warenfamilie"])

    def test_nicht_zaehlbar_steht_nicht_zur_wahl(self):
        form = HausartikelFilterForm({}, haus=self.haus)
        self.assertNotIn("Nicht zählbar", [name for _, name in form.familienwahl.eintraege])

    def test_eingeschraenkt_erst_bei_abweichung_von_der_vorgabe(self):
        self.assertFalse(HausartikelFilterForm({}, haus=self.haus).ist_eingeschraenkt)
        alle = [str(g.pk) for g in (self.getraenke, self.suesses, self.merch)]
        self.assertFalse(
            HausartikelFilterForm({"warengruppe": alle}, haus=self.haus).ist_eingeschraenkt
        )
        self.assertTrue(
            HausartikelFilterForm({"warengruppe": alle[:1]}, haus=self.haus).ist_eingeschraenkt
        )

    def test_knopftext_der_auswahl(self):
        form = HausartikelFilterForm(
            {"warengruppe": [str(self.merch.pk)], "familie": [str(self.limo.pk), "ohne"]},
            haus=self.haus,
        )
        self.assertEqual(form.gruppenwahl.text, "Warengruppe N")
        self.assertEqual(form.familienwahl.text, "2 gewählt")
        self.assertEqual(
            HausartikelFilterForm({}, haus=self.haus).gruppenwahl.text, "Alle Warengruppen"
        )

    def test_seite_traegt_beide_auswahlen(self):
        html = self.client.get(self.liste).content.decode()
        self.assertEqual(html.count('<div class="dropdown" data-sammelwahl>'), 2)
        self.assertIn('name="familie"', html)
        self.assertIn('name="warengruppe"', html)
        # The default is on the box. With it the script leaves it out of the address.
        self.assertIn('data-vorgabe="1"', html)
        self.assertIn("js/live_filter.js", html)
        self.assertIn("wieVorgabe", skript("live_filter"))


class WareNachWarengruppen(Sortiment):
    def waren(self, **parameter):
        antwort = self.client.get(reverse("ware_list"), parameter)
        return [ware.name for ware in antwort.context["ware"]]

    def test_suche_ueber_familie_und_warengruppe(self):
        """The product itself carries neither. The search still finds it through them."""
        self.assertEqual(self.waren(q="familie m"), ["Artikel C", "Artikel F"])
        self.assertEqual(self.waren(q="gruppe t"), ["Ware S"])

    def test_suche_zaehlt_die_hausartikel_einmal(self):
        """The product group joins over the house articles, like the name."""
        self.artikel("Artikel C", self.getraenke, self.limo, haus=self.anderes)
        antwort = self.client.get(reverse("ware_list"), {"q": "gruppe g"})
        zahlen = {ware.name: ware.anzahl_aktiv for ware in antwort.context["ware"]}
        self.assertEqual(zahlen, {"Artikel C": 2, "Artikel F": 1})

    def test_mehrere_warengruppen(self):
        self.assertEqual(
            self.waren(warengruppe=[self.suesses.pk, self.merch.pk]), ["Artikel B", "Ware S"]
        )

    def test_ware_in_zwei_gewaehlten_gruppen_steht_einmal(self):
        self.artikel("Artikel C", self.merch, self.limo, haus=self.anderes)
        self.assertEqual(
            self.waren(warengruppe=[self.getraenke.pk, self.merch.pk]),
            ["Artikel B", "Artikel C", "Artikel F"],
        )


class SucheUeberDieFamilieDarueber(Sortiment):
    """Whoever types the family above means everything below it, as with a tick."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.bier = Warenfamilie.objects.create(
            name="Warenfamilie B", leiteinheit=cls.stueck, uebergeordnet=cls.limo
        )
        cls.artikel("Artikel P", cls.getraenke, cls.bier)

    def test_warenliste(self):
        antwort = self.client.get(reverse("ware_list"), {"q": "familie m"})
        self.assertEqual(
            [ware.name for ware in antwort.context["ware"]],
            ["Artikel C", "Artikel F", "Artikel P"],
        )

    def test_hausartikelliste(self):
        self.assertEqual(self.namen(q="familie m"), ["Artikel C", "Artikel F", "Artikel P"])

    def test_die_familie_darunter_holt_die_darueber_nicht(self):
        self.assertEqual(self.namen(q="familie b"), ["Artikel P"])


@skipUnless(connection.vendor == "postgresql", "Ähnliche Namen findet nur pg_trgm")
class TippfehlerInDerSuche(Sortiment):
    """Where nothing contains the term, PostgreSQL shows what sounds like it.

    Generic names on purpose: the placeholders "Artikel C" and "Artikel F" are
    alike to a similarity search, and the case wants names that are not.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.knabbereien = Warenfamilie.objects.create(name="Knabbereien", leiteinheit=cls.stueck)
        cls.nachos = cls.artikel("Nachos Käse", cls.suesses, cls.knabbereien)

    def waren(self, **parameter):
        antwort = self.client.get(reverse("ware_list"), parameter)
        return [ware.name for ware in antwort.context["ware"]]

    def test_tippfehler_im_namen(self):
        self.assertEqual(self.waren(q="nachoss"), ["Nachos Käse"])
        self.assertEqual(self.namen(q="nachs"), ["Nachos Käse"])

    def test_tippfehler_in_der_familie(self):
        self.assertEqual(self.waren(q="knabberei"), ["Nachos Käse"])
        self.assertEqual(self.namen(q="knabberei"), ["Nachos Käse"])

    def test_genaue_treffer_gehen_vor(self):
        """The names "Artikel C" and "Artikel F" are alike. Whoever types the C gets only the C."""
        self.assertEqual(self.waren(q="artikel c"), ["Artikel C"])
        self.assertEqual(self.namen(q="artikel c"), ["Artikel C"])

    def test_nummern_bleiben_genau(self):
        nummer = self.nachos.artikelnummer
        vertauscht = nummer[:-2] + nummer[-1] + nummer[-2]
        self.assertEqual(self.namen(q=nummer), ["Nachos Käse"])
        self.assertEqual(self.namen(q=vertauscht), [])

    def test_nichts_aehnliches_bleibt_leer(self):
        self.assertEqual(self.waren(q="gibtesnicht"), [])


class ArtikelwahlDerZaehlliste(Sortiment):
    """The tab "Select articles" filters with the same form."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        bereich = Bereich.objects.create(haus=cls.haus, name="Theke")
        cls.ort = Ort.objects.create(bereich=bereich, name="Front")
        cls.reiter = reverse("zaehlliste", args=[cls.ort.pk])

    def gewaehlt(self, **parameter):
        antwort = self.client.get(self.reiter, {"reiter": "auswahl"} | parameter)
        return [artikel.name for artikel in antwort.context["auswahl_hausartikel"]]

    def test_familien_und_gruppen_als_mehrfachauswahl(self):
        self.assertEqual(self.gewaehlt(familie=[self.limo.pk]), ["Artikel C", "Artikel F"])
        self.assertEqual(
            self.gewaehlt(warengruppe=[self.suesses.pk, self.merch.pk]), ["Ware S", "Artikel B"]
        )

    def test_sortieren_nach_familie(self):
        self.assertEqual(self.gewaehlt(sortierung="-familie")[0], "Ware S")

    def test_reiter_traegt_die_auswahlen(self):
        html = self.client.get(self.reiter, {"reiter": "auswahl"}).content.decode()
        self.assertEqual(html.count('<div class="dropdown" data-sammelwahl>'), 2)
        self.assertIn("data-uebernehmen", html)


class GliederungUndAufklapper(Sortiment):
    def gruppen(self, **parameter):
        antwort = self.client.get(self.liste, parameter)
        return antwort, antwort.context["gruppen"]

    def test_nach_warengruppe(self):
        _, gruppen = self.gruppen(gliederung="warengruppe")
        self.assertEqual(
            [g["name"] for g in gruppen], ["Warengruppe G", "Warengruppe N", "Warengruppe T"]
        )

    def test_nach_warenfamilie_mit_ohne_zuletzt(self):
        antwort, gruppen = self.gruppen(gliederung="familie")
        self.assertEqual(
            [g.name for g in gruppen], ["Warenfamilie M", "Warenfamilie R", "Ohne Warenfamilie"]
        )
        self.assertEqual([a.name for a in gruppen[0].eintraege], ["Artikel C", "Artikel F"])
        # The column of the grouping drops away, the other stays.
        html = antwort.content.decode()
        self.assertIn("Warengruppe", html)
        self.assertIsNone(antwort.context.get("page_obj"))

    def test_ungegliedert_gibt_es_keine_gruppen(self):
        _, gruppen = self.gruppen()
        self.assertIsNone(gruppen)

    def test_gruppen_stehen_zugeklappt_und_tragen_ihre_kennung(self):
        antwort, _ = self.gruppen(gliederung="familie")
        html = antwort.content.decode()
        self.assertIn(f'data-gruppe="familie:{self.limo.pk}"', html)
        self.assertNotRegex(html, r"<details[^>]*\sopen")
        # The memory for it and the buttons for all at once.
        self.assertIn("js/expand.js", html)
        self.assertIn("sessionStorage", skript("expand"))
        self.assertIn('data-alle-gruppen="auf"', html)

    def test_dieselbe_gruppe_heisst_je_gliederung_anders(self):
        """A "Warengruppe G" as product group and as family must not share the state."""
        antwort, _ = self.gruppen(gliederung="warengruppe")
        self.assertIn('data-gruppe="warengruppe:Warengruppe G"', antwort.content.decode())

    def test_wareliste_ebenso(self):
        antwort = self.client.get(reverse("ware_list"), {"gliederung": "familie"})
        html = antwort.content.decode()
        self.assertIn(f'data-gruppe="familie:{self.limo.pk}"', html)
        self.assertNotRegex(html, r"<details[^>]*\sopen")


class WarengruppenSuchenUndFiltern(Sortiment):
    def gruppen(self, **parameter):
        antwort = self.client.get(reverse("warengruppe_list"), parameter)
        return [gruppe.name for gruppe in antwort.context["warengruppen"]]

    def test_textsuche(self):
        self.assertEqual(self.gruppen(q="gruppe g"), ["Warengruppe G"])

    def test_ohne_angabe_die_gefuehrten(self):
        self.becher.einstellen()
        self.assertEqual(self.gruppen(), ["Warengruppe G", "Warengruppe T"])
        self.assertEqual(self.gruppen(status="eingestellt"), ["Warengruppe N"])
        self.assertEqual(
            self.gruppen(status="alle"), ["Warengruppe G", "Warengruppe N", "Warengruppe T"]
        )

    def test_inaktiv_heisst_keiner_aktiv_aber_einer_inaktiv(self):
        self.cola.deaktivieren()
        self.assertEqual(self.gruppen(status="inaktiv"), [])
        self.fassbrause.deaktivieren()
        self.assertEqual(self.gruppen(status="inaktiv"), ["Warengruppe G"])
        self.assertEqual(self.gruppen(status="aktiv"), ["Warengruppe N", "Warengruppe T"])

    def test_leere_gruppe_gilt_als_aktiv(self):
        Warengruppe.objects.create(name="Ganz neu")
        self.assertIn("Ganz neu", self.gruppen(status="aktiv"))

    def test_sortierung_bleibt(self):
        self.assertEqual(
            self.gruppen(sortierung="-name"), ["Warengruppe T", "Warengruppe N", "Warengruppe G"]
        )
        self.assertEqual(self.gruppen(sortierung="-artikel")[0], "Warengruppe G")

    def test_seite_hat_suchfeld_und_sofortfilter(self):
        html = self.client.get(reverse("warengruppe_list")).content.decode()
        self.assertIn('type="search"', html)
        self.assertIn("data-filter", html)


class RezepturartikelSuchenUndFiltern(Sortiment):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.portion = cls.rezepturartikel("Mischung L", cls.cola, cls.fassbrause)
        cls.riegelbox = cls.rezepturartikel("Portion R", cls.schokoriegel)
        cls.leer = Rezepturartikel.objects.create(
            name="Noch ohne Rezeptur", basiseinheit=cls.stueck
        )

    @classmethod
    def rezepturartikel(cls, name, *zutaten):
        artikel = Rezepturartikel.objects.create(name=name, basiseinheit=cls.stueck)
        for zutat in zutaten:
            Rezepturkomponente.objects.create(
                rezepturartikel=artikel,
                ware=zutat.ware,
                menge_in_basiseinheit=Decimal("1"),
            )
        return artikel

    def artikelnamen(self, **parameter):
        antwort = self.client.get(reverse("rezepturartikel_list"), parameter)
        return [artikel.name for artikel in antwort.context["rezepturartikel"]]

    def test_suche_ueber_name_und_zutat(self):
        self.assertEqual(self.artikelnamen(q="portion r"), ["Portion R"])
        self.assertEqual(self.artikelnamen(q="artikel f"), ["Mischung L"])

    def test_ohne_angabe_alle(self):
        self.assertEqual(self.artikelnamen(), ["Mischung L", "Noch ohne Rezeptur", "Portion R"])

    def test_status_aus_den_zutaten(self):
        self.assertEqual(self.artikelnamen(status="aktiv"), ["Mischung L", "Portion R"])
        self.assertEqual(self.artikelnamen(status="eingestellt"), ["Noch ohne Rezeptur"])

        self.fassbrause.deaktivieren()
        self.assertEqual(self.artikelnamen(status="inaktiv"), ["Mischung L"])

        self.schokoriegel.einstellen()
        self.assertEqual(
            self.artikelnamen(status="eingestellt"), ["Noch ohne Rezeptur", "Portion R"]
        )

    def test_ein_haus_in_dem_alles_aktiv_ist_genuegt(self):
        self.fassbrause.deaktivieren()
        self.artikel("Artikel C", self.getraenke, self.limo, haus=self.anderes)
        self.artikel("Artikel F", self.getraenke, self.limo, haus=self.anderes)
        status = Rezepturartikel.status_je_artikel()
        self.assertEqual(status[self.portion.pk], Artikelstatus.AKTIV)

    def test_zutaten_aus_zwei_haeusern_ergeben_keinen_aktiven(self):
        """Every house on its own: Artikel C here and Artikel F next door resolve nowhere."""
        self.fassbrause.einstellen()
        self.artikel("Artikel F", self.getraenke, self.limo, haus=self.anderes)
        status = Rezepturartikel.status_je_artikel()
        self.assertEqual(status[self.portion.pk], Artikelstatus.EINGESTELLT)

    def test_zeile_nennt_den_status(self):
        self.fassbrause.deaktivieren()
        html = self.client.get(reverse("rezepturartikel_list")).content.decode()
        self.assertIn("nirgends auflösbar", html)
        self.assertIn(">inaktiv<", html)


class BehaelterSuchen(Sortiment):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.schale = Behaelter.objects.create(name="Schale", tara=Decimal("250"))
        cls.eimer = Behaelter.objects.create(name="Eimer", tara=Decimal("300"))
        ware = cls.schokoriegel.ware
        ware.abwiegbar = True
        ware.save()
        ware.behaelter.add(cls.eimer)

    def behaelter(self, **parameter):
        antwort = self.client.get(reverse("behaelter_list"), parameter)
        return [eintrag.name for eintrag in antwort.context["behaelter"]]

    def test_suche_ueber_den_namen(self):
        self.assertEqual(self.behaelter(q="scha"), ["Schale"])

    def test_suche_ueber_die_zugeordnete_ware(self):
        self.assertEqual(self.behaelter(q="ware s"), ["Eimer"])

    def test_sortierung_nach_tara(self):
        namen = self.behaelter(sortierung="-tara")
        self.assertLess(namen.index("Eimer"), namen.index("Schale"))
