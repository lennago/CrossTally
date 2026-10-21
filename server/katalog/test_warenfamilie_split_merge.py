"""Split and merge product families ('katalog/warenfamilie_split_merge.py').

The delicate part is not re-attaching the products but that the lead unit
afterwards fits the products each family still has, and that every product
can convert into it.
"""

from decimal import Decimal

from django.urls import reverse

from django_project.testing import AngemeldeteSeiten

from .models import Basiseinheit, Dimension, Ware, Warenfamilie
from .warenfamilie_split_merge import aufteilen, waren_ohne_faktor, zusammenfuehren


class Familien(AngemeldeteSeiten):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.stueck = Basiseinheit.objects.get(name="Stück(e)")
        cls.gramm = Basiseinheit.objects.get(name="g")
        cls.kilo = Basiseinheit.objects.get(name="kg")
        cls.liter = Basiseinheit.objects.get(name="Liter")

        # Warenfamilie N: portions in pieces, loose goods in kg. A kilogram
        # of the loose goods is seven portions.
        cls.nachos = Warenfamilie.objects.create(name="Warenfamilie N", leiteinheit=cls.stueck)
        cls.portion = cls.ware("Portion N", cls.stueck, cls.nachos)
        cls.karton = cls.ware("Posten N", cls.stueck, cls.nachos)
        cls.lose = cls.ware("Artikel N lose", cls.kilo, cls.nachos, {Dimension.STUECK: "7"})

    @staticmethod
    def ware(name, einheit, familie=None, umrechnungen=None):
        satz = Ware.objects.create(name=name, basiseinheit=einheit, warenfamilie=familie)
        if umrechnungen:
            satz.umrechnungen_setzen(
                {dimension: Decimal(wert) for dimension, wert in umrechnungen.items()}
            )
        return satz


class Aufteilen(Familien):
    def test_waren_wandern_und_beide_namen_gelten(self):
        bericht = aufteilen(self.nachos, "Warenfamilie N fertig", "Warenfamilie N roh", [self.lose])
        self.assertEqual(bericht.gewandert, 1)
        self.assertEqual(bericht.bleibt.name, "Warenfamilie N fertig")
        self.assertEqual(list(bericht.neu.waren.values_list("name", flat=True)), ["Artikel N lose"])
        self.assertEqual(bericht.bleibt.waren.count(), 2)

    def test_jede_haelfte_bekommt_die_einheit_die_sie_braucht(self):
        bericht = aufteilen(self.nachos, "Warenfamilie N fertig", "Warenfamilie N roh", [self.lose])
        # Left only piece goods: summed in pieces as before.
        self.assertEqual(bericht.bleibt.leiteinheit, self.stueck)
        # Right only kilograms and no piece goods any more: summed in kg. The
        # factor stays with the product, it belongs to it.
        self.assertEqual(bericht.neu.leiteinheit, self.kilo)
        self.assertEqual(bericht.neu.waren_ohne_faktor(), [])
        self.lose.refresh_from_db()
        self.assertEqual(self.lose.faktoren(), {Dimension.STUECK: Decimal("7")})

    def test_gemischte_haelfte_erbt_die_leiteinheit(self):
        bericht = aufteilen(
            self.nachos, "Warenfamilie N A", "Warenfamilie N B", [self.portion, self.lose]
        )
        self.assertEqual(bericht.neu.leiteinheit, self.stueck)
        self.assertEqual(bericht.neu.waren_ohne_faktor(), [])

    def test_leere_haelfte_hat_keine_einheit(self):
        bericht = aufteilen(self.nachos, "Warenfamilie N", "Noch leer", [])
        self.assertIsNone(bericht.neu.leiteinheit)
        self.assertEqual(bericht.bleibt.leiteinheit, self.stueck)

    def test_fremde_waren_werden_uebergangen(self):
        fremd = self.ware("Artikel C", self.stueck)
        bericht = aufteilen(self.nachos, "Warenfamilie N", "Neu", [fremd])
        self.assertEqual(bericht.gewandert, 0)
        fremd.refresh_from_db()
        self.assertIsNone(fremd.warenfamilie)

    def test_ueber_die_seite(self):
        adresse = reverse("warenfamilie_aufteilen", args=[self.nachos.pk])
        self.assertContains(self.client.get(adresse), "Artikel N lose")
        antwort = self.client.post(
            adresse,
            {"name": "Warenfamilie N", "neuer_name": "Lose Ware", "wandern": [self.lose.pk]},
        )
        self.assertRedirects(antwort, self.nachos.get_absolute_url())
        self.lose.refresh_from_db()
        self.assertEqual(self.lose.warenfamilie.name, "Lose Ware")

    def test_namen_muessen_frei_und_verschieden_sein(self):
        Warenfamilie.objects.create(name="Warenfamilie E")
        adresse = reverse("warenfamilie_aufteilen", args=[self.nachos.pk])
        for neuer_name in ("Warenfamilie N", "Warenfamilie E"):
            antwort = self.client.post(
                adresse, {"name": "Warenfamilie N", "neuer_name": neuer_name}
            )
            self.assertEqual(antwort.status_code, 200)
            self.assertTrue(antwort.context["form"].errors["neuer_name"])
        self.assertEqual(Warenfamilie.objects.filter(name="Warenfamilie N").count(), 1)

    def test_nicht_zaehlbar_laesst_sich_nicht_teilen(self):
        kein = Warenfamilie.nicht_zaehlbar()
        for name in ("warenfamilie_aufteilen", "warenfamilie_zusammenfuehren"):
            self.assertEqual(self.client.get(reverse(name, args=[kein.pk])).status_code, 404)
        self.assertNotContains(self.client.get(kein.get_absolute_url()), "Aufteilen")
        self.assertContains(self.client.get(self.nachos.get_absolute_url()), "Aufteilen")


class Zusammenfuehren(Familien):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Warenfamilie D sums in litres. Its sauce converts nowhere else, its
        # dip of 90 ml knows its way into litres, but not into pieces.
        cls.dips = Warenfamilie.objects.create(name="Warenfamilie D", leiteinheit=cls.liter)
        cls.dip = cls.ware("Ware K", cls.stueck, cls.dips, {Dimension.VOLUMEN: "90"})
        cls.sauce = cls.ware("Artikel K", cls.liter, cls.dips)
        cls.adresse = reverse("warenfamilie_zusammenfuehren", args=[cls.nachos.pk])

    def test_es_fehlt_was_die_bleibende_einheit_nicht_erreicht(self):
        """The dip counts in pieces already. The sauce needs pieces per litre."""
        self.assertEqual(waren_ohne_faktor(self.nachos, self.dips), [self.sauce])

    def test_nichts_fehlt_wenn_die_dimensionen_passen(self):
        mehr = Warenfamilie.objects.create(name="Warenfamilie Q", leiteinheit=self.kilo)
        self.ware("Artikel N XXL", self.kilo, mehr, {Dimension.STUECK: "5"})
        self.assertEqual(waren_ohne_faktor(self.nachos, mehr), [])

    def test_bleibende_familie_gewinnt(self):
        bericht = zusammenfuehren(
            self.nachos,
            self.dips,
            "Warenfamilie O",
            {(self.sauce.pk, Dimension.STUECK): Decimal("10")},
        )
        familie = bericht.familie
        self.assertEqual(bericht.neue_waren, 2)
        self.assertEqual(familie.name, "Warenfamilie O")
        self.assertEqual(familie.leiteinheit, self.stueck)
        self.assertEqual(familie.waren_ohne_faktor(), [])
        self.assertFalse(Warenfamilie.objects.filter(name="Warenfamilie D").exists())
        self.assertEqual(familie.waren.count(), 5)
        self.sauce.refresh_from_db()
        self.assertEqual(self.sauce.faktoren(), {Dimension.STUECK: Decimal("10")})

    def test_die_bleibende_rueckt_an_die_stelle_der_aufgehenden(self):
        oben = Warenfamilie.objects.create(name="Warenfamilie A")
        unten = Warenfamilie.objects.create(name="Warenfamilie Z", uebergeordnet=oben)
        geschwister = Warenfamilie.objects.create(name="Warenfamilie G", uebergeordnet=oben)

        zusammenfuehren(unten, oben, "Warenfamilie Z")

        unten.refresh_from_db()
        geschwister.refresh_from_db()
        self.assertIsNone(unten.uebergeordnet)
        self.assertEqual(geschwister.uebergeordnet, unten)

    def test_zwei_ebenen_hoeher_entsteht_kein_kreis(self):
        """A grandparent absorbed into its grandchild. Until 10/2026: X below Z below X."""
        oben = Warenfamilie.objects.create(name="Warenfamilie A")
        mitte = Warenfamilie.objects.create(name="Warenfamilie X", uebergeordnet=oben)
        unten = Warenfamilie.objects.create(name="Warenfamilie Z", uebergeordnet=mitte)

        zusammenfuehren(unten, oben, "Warenfamilie Z")

        mitte.refresh_from_db()
        unten.refresh_from_db()
        self.assertIsNone(unten.uebergeordnet, "takes the place of the absorbed one at the top")
        self.assertEqual(mitte.uebergeordnet, unten)
        self.assertEqual([familie.pk for familie in mitte.vorfahren()], [unten.pk])
        self.assertEqual(unten.nachkommen_nummern(), {mitte.pk})

    def test_name_der_aufgehenden_darf_uebernommen_werden(self):
        bericht = zusammenfuehren(
            self.nachos,
            self.dips,
            "Warenfamilie D",
            {(self.sauce.pk, Dimension.STUECK): Decimal("10")},
        )
        self.assertEqual(bericht.familie.name, "Warenfamilie D")

    def test_leere_bleibende_uebernimmt_die_einheit(self):
        leer = Warenfamilie.objects.create(name="Neu und leer")
        self.assertEqual(waren_ohne_faktor(leer, self.dips), [])
        bericht = zusammenfuehren(leer, self.dips, "Neu und leer")
        self.assertEqual(bericht.familie.leiteinheit, self.liter)
        self.assertEqual(bericht.familie.waren_ohne_faktor(), [])

    def test_leere_bleibende_unter_einer_oberfamilie_fragt_nach_deren_faktor(self):
        """The family above sums in litres. Until 10/2026 the merge asked nothing."""
        oben = Warenfamilie.objects.create(name="Warenfamilie F", leiteinheit=self.liter)
        leer = Warenfamilie.objects.create(name="Warenfamilie L", uebergeordnet=oben)
        # None of the three knows its way into litres.
        drei = [self.lose, self.portion, self.karton]
        self.assertEqual(waren_ohne_faktor(leer, self.nachos), drei)

        adresse = reverse("warenfamilie_zusammenfuehren", args=[leer.pk])
        daten = {"quelle": self.nachos.pk, "name": "Warenfamilie L", "gezeigt": self.nachos.pk}
        antwort = self.client.post(adresse, daten)
        self.assertEqual(antwort.status_code, 200)
        for ware in drei:
            with self.subTest(ware=ware.name):
                self.assertContains(antwort, f'name="faktor_{ware.pk}_VOLUMEN"')
        self.assertContains(antwort, "Ohne diesen Faktor")
        self.assertTrue(Warenfamilie.objects.filter(pk=self.nachos.pk).exists())

        antwort = self.client.post(
            adresse, daten | {f"faktor_{ware.pk}_VOLUMEN": "2" for ware in drei}
        )
        self.assertRedirects(antwort, leer.get_absolute_url())
        leer.refresh_from_db()
        oben.refresh_from_db()
        self.assertEqual(leer.leiteinheit, self.stueck)
        self.assertEqual(oben.waren_ohne_faktor(), [])

    def test_vorschau_schreibt_nichts_und_fragt_nach_dem_faktor(self):
        antwort = self.client.get(self.adresse, {"quelle": self.dips.pk})
        self.assertContains(antwort, "Artikel K")
        self.assertContains(antwort, "Fehlende Umrechnung")
        # A piece ranks above a litre: entered is what a piece holds.
        self.assertContains(antwort, "„Artikel K“: l je Stk.")
        self.assertContains(antwort, f'name="faktor_{self.sauce.pk}_STUECK"')
        # In the preview an empty field is not an error yet.
        self.assertNotContains(antwort, "is-invalid")
        self.assertTrue(Warenfamilie.objects.filter(pk=self.dips.pk).exists())

    def test_ohne_bestaetigung_geschieht_nichts(self):
        self.client.post(
            self.adresse,
            {
                "quelle": self.dips.pk,
                "name": "Warenfamilie N",
                f"faktor_{self.sauce.pk}_STUECK": "10",
            },
        )
        self.assertTrue(Warenfamilie.objects.filter(pk=self.dips.pk).exists())

    def test_fehlender_faktor_haelt_das_zusammenfuehren_auf(self):
        antwort = self.client.post(
            self.adresse,
            {"quelle": self.dips.pk, "name": "Warenfamilie N", "gezeigt": self.dips.pk},
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Ohne diesen Faktor")
        self.assertTrue(Warenfamilie.objects.filter(pk=self.dips.pk).exists())

    def test_zusammenfuehren_ueber_die_seite_mit_komma(self):
        antwort = self.client.post(
            self.adresse,
            {
                "quelle": self.dips.pk,
                "name": "Warenfamilie N",
                "gezeigt": self.dips.pk,
                f"faktor_{self.sauce.pk}_STUECK": "0,08",
            },
        )
        self.assertRedirects(antwort, self.nachos.get_absolute_url())
        self.sauce.refresh_from_db()
        self.assertEqual(self.sauce.warenfamilie, self.nachos)
        # Entered "0,08 l je Stk.", stored are the pieces per litre.
        self.assertEqual(self.sauce.faktoren(), {Dimension.STUECK: Decimal("12.5")})

    def test_nicht_zaehlbar_steht_nicht_zur_wahl(self):
        antwort = self.client.get(self.adresse)
        namen = [familie.name for familie in antwort.context["form"].fields["quelle"].queryset]
        self.assertEqual(namen, ["Warenfamilie D"])


class BewusstFremdeLeiteinheit(Familien):
    """A lead unit no product of the family carries was chosen on purpose. It stays.

    The family's form allows litres over nothing but bottles in pieces, kg
    over products in g. Until 10/2026 splitting and merging turned such a
    family into the unit of its first product, without a word, and every
    report summed it in that unit from then on.
    """

    def flaschen(self, familie, *namen):
        return [self.ware(name, self.stueck, familie, {Dimension.VOLUMEN: "500"}) for name in namen]

    def test_aufteilen_behaelt_liter_ueber_flaschen(self):
        getraenke = Warenfamilie.objects.create(name="Warenfamilie F", leiteinheit=self.liter)
        *_, letzte = self.flaschen(getraenke, "Ware A", "Ware B", "Ware C")

        bericht = aufteilen(getraenke, "Warenfamilie F", "Warenfamilie G", [letzte])

        bericht.bleibt.refresh_from_db()
        self.assertEqual(bericht.bleibt.leiteinheit, self.liter)
        self.assertEqual(bericht.neu.leiteinheit, self.liter)

    def test_aufteilen_behaelt_kilogramm_ueber_gramm(self):
        snacks = Warenfamilie.objects.create(name="Warenfamilie S", leiteinheit=self.kilo)
        erste = self.ware("Ware G", self.gramm, snacks)
        self.ware("Ware H", self.gramm, snacks)

        bericht = aufteilen(snacks, "Warenfamilie S", "Warenfamilie T", [erste])

        bericht.bleibt.refresh_from_db()
        self.assertEqual(bericht.bleibt.leiteinheit, self.kilo)
        self.assertEqual(bericht.neu.leiteinheit, self.kilo)

    def test_eine_haelfte_die_sie_nicht_erreicht_nimmt_die_ihrer_ersten_ware(self):
        """A bottle without its millilitres cannot be summed in litres. Then pieces after all."""
        getraenke = Warenfamilie.objects.create(name="Warenfamilie F", leiteinheit=self.liter)
        self.flaschen(getraenke, "Ware A")
        ohne = self.ware("Ware D", self.stueck, getraenke)

        bericht = aufteilen(getraenke, "Warenfamilie F", "Warenfamilie G", [ohne])

        self.assertEqual(bericht.neu.leiteinheit, self.stueck)
        bericht.bleibt.refresh_from_db()
        self.assertEqual(bericht.bleibt.leiteinheit, self.liter)

    def test_zusammenfuehren_behaelt_die_einheit_der_bleibenden(self):
        getraenke = Warenfamilie.objects.create(name="Warenfamilie F", leiteinheit=self.liter)
        weitere = Warenfamilie.objects.create(name="Warenfamilie G", leiteinheit=self.stueck)
        self.flaschen(getraenke, "Ware A")
        self.flaschen(weitere, "Ware C")

        bericht = zusammenfuehren(getraenke, weitere, "Warenfamilie F")

        bericht.familie.refresh_from_db()
        self.assertEqual(bericht.familie.leiteinheit, self.liter)
        self.assertEqual(bericht.familie.waren_ohne_faktor(), [])
