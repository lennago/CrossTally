"""Families within families: the chain, the tree and everything that reads it.

A family above another sums the products below it in its own unit. So a
product needs its factor into every dimension up the chain, the filters
and lists show the tree, and splitting and merging keep the place in it.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from django_project.form_fields import modellwahl
from django_project.testing import AngemeldeteSeiten

from .family_tree import OHNE_FAMILIE, familienbaum, flach
from .forms import WareForm, WarenfamilieForm
from .forms.filters import familienstruktur
from .models import Basiseinheit, Dimension, Ware, Warenfamilie
from .warenfamilie_split_merge import aufteilen, fehlende_faktoren, zusammenfuehren


class Getraenke(TestCase):
    """Warenfamilie G in litres, below it Warenfamilie B in pieces and Warenfamilie S in litres."""

    @classmethod
    def setUpTestData(cls):
        cls.stueck = Basiseinheit.objects.get(name="Stück(e)")
        cls.liter = Basiseinheit.objects.get(name="Liter")
        cls.kilo = Basiseinheit.objects.get(name="kg")
        cls.getraenke = Warenfamilie.objects.create(name="Warenfamilie G", leiteinheit=cls.liter)
        cls.biere = Warenfamilie.objects.create(
            name="Warenfamilie B", leiteinheit=cls.stueck, uebergeordnet=cls.getraenke
        )
        cls.saefte = Warenfamilie.objects.create(
            name="Warenfamilie S", leiteinheit=cls.liter, uebergeordnet=cls.getraenke
        )
        # A bottle of 500 ml: it knows its way into litres.
        cls.bier = cls.ware("Ware B 0,5", cls.stueck, cls.biere, {Dimension.VOLUMEN: "500"})
        cls.saft = cls.ware("Ware S", cls.liter, cls.saefte)
        # A product directly in the family above, next to the subfamilies.
        cls.wasser = cls.ware("Ware W", cls.liter, cls.getraenke)

    @staticmethod
    def ware(name, einheit, familie=None, umrechnungen=None) -> Ware:
        satz = Ware.objects.create(name=name, basiseinheit=einheit, warenfamilie=familie)
        if umrechnungen:
            satz.umrechnungen_setzen(
                {dimension: Decimal(wert) for dimension, wert in umrechnungen.items()}
            )
        return satz


class Kette(Getraenke):
    def test_vorfahren_und_nachkommen(self):
        self.assertEqual(self.biere.vorfahren(), [self.getraenke])
        self.assertEqual(self.getraenke.vorfahren(), [])
        self.assertEqual(self.getraenke.nachkommen_nummern(), {self.biere.pk, self.saefte.pk})
        self.assertEqual(self.biere.tiefe, 1)
        self.assertEqual(self.biere.anzeigepfad, "Warenfamilie G › Warenfamilie B")

    def test_alle_waren_ueber_den_baum(self):
        self.assertEqual(
            set(self.getraenke.alle_waren().values_list("name", flat=True)),
            {"Ware B 0,5", "Ware S", "Ware W"},
        )
        self.assertEqual(self.getraenke.anzahl_waren, 1)
        self.assertEqual(self.getraenke.anzahl_aller_waren, 3)

    def test_mit_nachkommen(self):
        self.assertEqual(
            Warenfamilie.mit_nachkommen([self.getraenke.pk]),
            {self.getraenke.pk, self.biere.pk, self.saefte.pk},
        )
        self.assertEqual(Warenfamilie.mit_nachkommen([self.biere.pk]), {self.biere.pk})

    def test_kein_kreis(self):
        self.getraenke.uebergeordnet = self.biere
        with self.assertRaises(ValidationError):
            self.getraenke.full_clean()
        self.biere.uebergeordnet = self.biere
        with self.assertRaises(ValidationError):
            self.biere.full_clean()

    def test_nicht_zaehlbar_steht_allein(self):
        kein = Warenfamilie.nicht_zaehlbar()
        self.biere.uebergeordnet = kein
        with self.assertRaises(ValidationError):
            self.biere.full_clean()
        kein.uebergeordnet = self.getraenke
        with self.assertRaises(ValidationError):
            kein.full_clean()

    def test_loeschen_rueckt_die_unterfamilien_auf(self):
        oben = Warenfamilie.objects.create(name="Warenfamilie O", leiteinheit=self.liter)
        self.getraenke.uebergeordnet = oben
        self.getraenke.save()
        self.getraenke.delete()
        self.biere.refresh_from_db()
        self.assertEqual(self.biere.uebergeordnet, oben)

    def test_die_dimensionen_bis_nach_oben(self):
        self.assertEqual(self.biere.summendimensionen(), [Dimension.VOLUMEN, Dimension.STUECK])
        # What the form is about to set counts, "no parent" included.
        self.assertEqual(self.biere.summendimensionen(uebergeordnet=None), [Dimension.STUECK])
        self.assertEqual(
            self.biere.summendimensionen(self.kilo, uebergeordnet=None), [Dimension.MASSE]
        )

    def test_eine_ware_braucht_den_faktor_in_jede_stufe(self):
        """The beer without millilitres cannot join the litres above."""
        stumm = self.ware("Ware B 0,33", self.stueck)
        stumm.warenfamilie = self.biere
        with self.assertRaises(ValidationError) as fehler:
            stumm.full_clean()
        self.assertIn("Warenfamilie G", str(fehler.exception))
        self.assertEqual(self.biere.fehlende_faktoren(waren=[stumm]), [(stumm, Dimension.VOLUMEN)])
        self.assertEqual(self.getraenke.waren_ohne_faktor(), [])

    def test_leiteinheit_der_familie_darueber_sieht_den_ganzen_baum(self):
        """Switching the top family to kilograms: the beer and the juice lack their grams."""
        fehlt = self.getraenke.fehlende_faktoren(self.kilo)
        self.assertEqual(
            {(ware.name, dimension) for ware, dimension in fehlt},
            {
                ("Ware B 0,5", Dimension.MASSE),
                ("Ware S", Dimension.MASSE),
                ("Ware W", Dimension.MASSE),
            },
        )


class Baum(Getraenke):
    def test_baum_mit_summen_und_ohne_zuletzt(self):
        lose = self.ware("Ware L", self.kilo)
        bloecke = familienbaum(
            [self.bier, self.saft, self.wasser, lose], lambda ware: ware.warenfamilie
        )
        self.assertEqual(
            [str(block.name) for block in bloecke], ["Warenfamilie G", str(OHNE_FAMILIE)]
        )
        oben = bloecke[0]
        self.assertEqual(
            [block.name for block in oben.unterbloecke], ["Warenfamilie B", "Warenfamilie S"]
        )
        self.assertEqual(oben.eintraege, [self.wasser])
        self.assertEqual(oben.anzahl, 3)
        self.assertEqual(oben.unterbloecke[0].tiefe, 1)
        self.assertEqual(oben.kennung, f"familie:{self.getraenke.pk}")
        self.assertEqual(bloecke[1].kennung, "familie:ohne")
        self.assertEqual(len(flach(bloecke)), 4)

    def test_familie_darueber_erscheint_auch_ohne_eigene_eintraege(self):
        bloecke = familienbaum([self.bier], lambda ware: ware.warenfamilie)
        self.assertEqual(bloecke[0].familie, self.getraenke)
        self.assertEqual(bloecke[0].eintraege, [])
        self.assertEqual(bloecke[0].unterbloecke[0].eintraege, [self.bier])

    def test_erlaubt_schneidet_den_baum(self):
        """Chosen only the beers: they stand at the top, the family above is skipped."""
        bloecke = familienbaum([self.bier], lambda ware: ware.warenfamilie, erlaubt={self.biere.pk})
        self.assertEqual([block.familie for block in bloecke], [self.biere])
        self.assertEqual(bloecke[0].tiefe, 0)

    def test_struktur_fuer_das_menue(self):
        struktur = familienstruktur([self.getraenke, self.biere])
        self.assertEqual(struktur[str(self.getraenke.pk)], (0, ""))
        self.assertEqual(struktur[str(self.biere.pk)], (1, str(self.getraenke.pk)))
        # Without the family above in the list the entry stands at the top.
        self.assertEqual(familienstruktur([self.biere])[str(self.biere.pk)], (0, ""))


class Listen(AngemeldeteSeiten, Getraenke):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Getraenke.setUpTestData()

    def test_warenliste_gliedert_als_baum(self):
        antwort = self.client.get(reverse("ware_list"), {"gliederung": "familie"})
        gruppen = antwort.context["gruppen"]
        self.assertEqual([g.name for g in gruppen], ["Warenfamilie G"])
        self.assertEqual(
            [g.name for g in gruppen[0].unterbloecke], ["Warenfamilie B", "Warenfamilie S"]
        )
        html = antwort.content.decode()
        self.assertIn(f'data-gruppe="familie:{self.biere.pk}"', html)
        self.assertNotRegex(html, r"<details[^>]*\sopen")

    def test_familienfilter_zaehlt_genau_die_haken(self):
        """The menu sets the families below a ticked one. The server takes what arrives."""
        antwort = self.client.get(reverse("ware_list"), {"familie": [self.getraenke.pk]})
        self.assertEqual([w.name for w in antwort.context["ware"]], ["Ware W"])
        antwort = self.client.get(
            reverse("ware_list"), {"familie": [self.getraenke.pk, self.biere.pk]}
        )
        self.assertEqual({w.name for w in antwort.context["ware"]}, {"Ware B 0,5", "Ware W"})

    def test_menue_zeigt_die_unterfamilien_eingerueckt(self):
        html = self.client.get(reverse("ware_list")).content.decode()
        self.assertIn(f'data-oben="{self.getraenke.pk}"', html)
        self.assertIn("wahl-ebene-1", html)

    def test_familienliste_zeigt_den_baum(self):
        """In the order of the tree, indented, with the total below. "Not countable" last."""
        antwort = self.client.get(reverse("warenfamilie_list"))
        familien = antwort.context["familien"]
        self.assertEqual(
            [f.name for f in familien],
            ["Warenfamilie G", "Warenfamilie B", "Warenfamilie S", "Nicht zählbar"],
        )
        self.assertEqual([f.tiefe for f in familien], [0, 1, 1, 0])
        self.assertEqual([f.gesamtzahl for f in familien], [3, 1, 1, 0])
        self.assertContains(antwort, 'class="baum-ebene-1"')
        self.assertContains(antwort, "(3 mit Unterfamilien)")
        self.assertNotContains(antwort, "Gehört zu")

    def test_familienliste_und_detailseite(self):
        antwort = self.client.get(self.biere.get_absolute_url())
        self.assertContains(antwort, "Warenfamilie G")
        antwort = self.client.get(self.getraenke.get_absolute_url())
        self.assertContains(antwort, "Unterfamilien")
        self.assertContains(antwort, "Warenfamilie B")


class Formular(AngemeldeteSeiten, Getraenke):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Getraenke.setUpTestData()

    def absenden(self, familie, **abweichend):
        daten = {
            "name": familie.name,
            "beschreibung": "",
            "leiteinheit": familie.leiteinheit_id,
            "uebergeordnet": familie.uebergeordnet_id or "",
            "ware": [ware.pk for ware in familie.waren.all()],
        }
        return self.client.post(reverse("warenfamilie_edit", args=[familie.pk]), daten | abweichend)

    def test_die_seite_nennt_die_familie_die_bearbeitet_wird(self):
        """Title, heading and path say "edit", like every other edit page.

        Until 10/2026 the template missed the record and headed the page "New
        product family", with "New" as the last step of the path.
        """
        antwort = self.client.get(reverse("warenfamilie_edit", args=[self.biere.pk]))
        self.assertContains(antwort, "<title>Warenfamilie B bearbeiten</title>", html=True)
        self.assertContains(
            antwort, '<h1 class="h3 mb-0">Warenfamilie B bearbeiten</h1>', html=True
        )
        for familie in (self.getraenke, self.biere):
            self.assertContains(
                antwort, f'<a href="{familie.get_absolute_url()}">{familie.name}</a>', html=True
            )
        self.assertContains(
            antwort,
            '<li class="breadcrumb-item active" aria-current="page">Bearbeiten</li>',
            html=True,
        )
        self.assertContains(
            antwort,
            f'<a href="{self.biere.get_absolute_url()}" class="btn btn-outline-secondary">'
            "Abbrechen</a>",
            html=True,
        )
        self.assertNotContains(antwort, "Neue Warenfamilie")
        # Creating stays as it was.
        neu = self.client.get(reverse("warenfamilie_add"))
        self.assertContains(neu, "<title>Neue Warenfamilie</title>", html=True)
        self.assertContains(
            neu, '<li class="breadcrumb-item active" aria-current="page">Neu</li>', html=True
        )

    def test_unter_eine_familie_haengen_fragt_nach_dem_faktor(self):
        """Warenfamilie K in kilograms takes the beers: every beer needs its grams."""
        kilo = Warenfamilie.objects.create(name="Warenfamilie K", leiteinheit=self.kilo)
        antwort = self.absenden(self.biere, uebergeordnet=kilo.pk)
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Fehlende Umrechnungen")
        self.assertContains(antwort, f'name="faktor_{self.bier.pk}_MASSE"')
        self.biere.refresh_from_db()
        self.assertEqual(self.biere.uebergeordnet, self.getraenke)

        antwort = self.absenden(
            self.biere, uebergeordnet=kilo.pk, **{f"faktor_{self.bier.pk}_MASSE": "520"}
        )
        self.assertRedirects(antwort, self.biere.get_absolute_url())
        self.biere.refresh_from_db()
        self.assertEqual(self.biere.uebergeordnet, kilo)
        self.bier.refresh_from_db()
        self.assertEqual(self.bier.faktoren()[Dimension.MASSE], Decimal("520"))

    def test_die_familie_darueber_darf_nicht_darunter_stehen(self):
        form = WarenfamilieForm(instance=self.getraenke)
        oben = modellwahl(form, "uebergeordnet").queryset
        assert oben is not None
        zur_wahl = set(oben.values_list("pk", flat=True))
        self.assertNotIn(self.getraenke.pk, zur_wahl)
        self.assertNotIn(self.biere.pk, zur_wahl)
        self.assertNotIn(Warenfamilie.nicht_zaehlbar().pk, zur_wahl)
        # The products below count for the sum, so the form knows them.
        self.assertEqual([ware.name for ware in form.waren_darunter], ["Ware B 0,5", "Ware S"])

    def test_die_auswahl_darueber_folgt_dem_baum(self):
        """In the order of the tree, a subfamily indented under its family."""
        andere = Warenfamilie.objects.create(name="Warenfamilie A", leiteinheit=self.liter)
        form = WarenfamilieForm(instance=andere)
        beschriftungen = [str(wahl.data["label"]) for wahl in form["uebergeordnet"].subwidgets]
        self.assertEqual(
            beschriftungen,
            [
                "Keine, steht für sich",
                "Warenfamilie G",
                "\u00a0\u00a0\u00a0\u00a0└ Warenfamilie B",
                "\u00a0\u00a0\u00a0\u00a0└ Warenfamilie S",
            ],
        )
        # The product's form shows the same tree, "Not countable" last.
        warenform = WareForm()
        self.assertEqual(
            [str(wahl.data["label"]) for wahl in warenform["warenfamilie"].subwidgets][1:],
            [
                "Warenfamilie A",
                "Warenfamilie G",
                "\u00a0\u00a0\u00a0\u00a0└ Warenfamilie B",
                "\u00a0\u00a0\u00a0\u00a0└ Warenfamilie S",
                "Nicht zählbar",
            ],
        )
        # The choice still validates against what is allowed.
        form = WarenfamilieForm(
            data={"name": "Warenfamilie A", "uebergeordnet": self.biere.pk, "ware": []},
            instance=andere,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["uebergeordnet"], self.biere)

    def test_leiteinheit_aus_den_unterfamilien(self):
        """The family above has no product of its own, but its subfamilies count in pieces."""
        oben = Warenfamilie.objects.create(name="Warenfamilie O")
        self.getraenke.uebergeordnet = oben
        self.getraenke.save()
        antwort = self.absenden(oben, leiteinheit=self.stueck.pk, ware=[])
        # The juice and the water in litres lack their pieces per litre.
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, f'name="faktor_{self.saft.pk}_STUECK"')

    def test_wechsel_der_leiteinheit_fragt_fuer_den_ganzen_baum(self):
        antwort = self.absenden(self.getraenke, leiteinheit=self.kilo.pk)
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, f'name="faktor_{self.saft.pk}_MASSE"')
        self.assertContains(antwort, f'name="faktor_{self.bier.pk}_MASSE"')

    def test_anlegen_ohne_waren_unter_einer_familie(self):
        antwort = self.client.post(
            reverse("warenfamilie_add"),
            {
                "name": "Warenfamilie N",
                "beschreibung": "",
                "leiteinheit": "",
                "uebergeordnet": self.getraenke.pk,
                "ware": [],
            },
        )
        neu = Warenfamilie.objects.get(name="Warenfamilie N")
        self.assertRedirects(antwort, neu.get_absolute_url())
        self.assertEqual(neu.uebergeordnet, self.getraenke)


class Unterfamilie(AngemeldeteSeiten, Getraenke):
    """A new family below another, from that family's page, taking its products along."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        Getraenke.setUpTestData()
        cls.frei = cls.ware("Ware F", cls.liter)
        cls.adresse = f"{reverse('warenfamilie_add')}?unter={cls.getraenke.pk}"

    def test_die_seite_bietet_den_knopf(self):
        antwort = self.client.get(self.getraenke.get_absolute_url())
        self.assertContains(antwort, "Neue Unterfamilie")
        self.assertContains(antwort, f"?unter={self.getraenke.pk}")
        kein = Warenfamilie.nicht_zaehlbar()
        self.assertNotContains(self.client.get(kein.get_absolute_url()), f"?unter={kein.pk}")

    def test_vorbelegt_und_die_waren_von_oben_zuerst(self):
        antwort = self.client.get(self.adresse)
        form = antwort.context["form"]
        self.assertEqual(form.oberfamilie, self.getraenke)
        self.assertEqual(form.fields["uebergeordnet"].initial, self.getraenke.pk)
        zur_wahl = modellwahl(form, "ware").queryset
        assert zur_wahl is not None
        # The water from the family above first, then the free product. The
        # beer stands in a family of its own and is not offered.
        self.assertEqual([ware.name for ware in zur_wahl], ["Ware W", "Ware F"])
        self.assertContains(antwort, "Neue Unterfamilie von Warenfamilie G")
        self.assertContains(antwort, f'name="unter" value="{self.getraenke.pk}"')

    def test_gewaehlte_waren_wechseln_hinunter(self):
        antwort = self.client.post(
            reverse("warenfamilie_add"),
            {
                "unter": self.getraenke.pk,
                "name": "Warenfamilie W",
                "beschreibung": "",
                "leiteinheit": self.liter.pk,
                "uebergeordnet": self.getraenke.pk,
                "ware": [self.wasser.pk, self.frei.pk],
            },
        )
        neu = Warenfamilie.objects.get(name="Warenfamilie W")
        self.assertRedirects(antwort, neu.get_absolute_url())
        self.assertEqual(neu.uebergeordnet, self.getraenke)
        self.assertEqual(set(neu.waren.values_list("name", flat=True)), {"Ware W", "Ware F"})
        self.assertEqual(self.getraenke.waren.count(), 0)
        self.assertEqual(self.getraenke.anzahl_aller_waren, 4)

    def test_ohne_unter_bleibt_es_das_alte_formular(self):
        form = self.client.get(reverse("warenfamilie_add")).context["form"]
        self.assertIsNone(form.oberfamilie)
        self.assertNotIn("unterfamilien", form.fields)
        zur_wahl = modellwahl(form, "ware").queryset
        assert zur_wahl is not None
        self.assertEqual([ware.name for ware in zur_wahl], ["Ware F"])

    def test_andere_unterfamilien_wandern_unter_die_neue(self):
        """Warenfamilie K takes the beers along: they stand below it afterwards, one level down."""
        form = self.client.get(self.adresse).context["form"]
        zur_wahl = modellwahl(form, "unterfamilien").queryset
        assert zur_wahl is not None
        self.assertEqual([f.name for f in zur_wahl], ["Warenfamilie B", "Warenfamilie S"])

        antwort = self.client.post(
            reverse("warenfamilie_add"),
            {
                "unter": self.getraenke.pk,
                "name": "Warenfamilie K",
                "beschreibung": "",
                "leiteinheit": self.liter.pk,
                "uebergeordnet": self.getraenke.pk,
                "ware": [],
                "unterfamilien": [self.biere.pk],
            },
        )
        neu = Warenfamilie.objects.get(name="Warenfamilie K")
        self.assertRedirects(antwort, neu.get_absolute_url())
        self.biere.refresh_from_db()
        self.assertEqual(self.biere.uebergeordnet, neu)
        self.assertEqual(self.biere.vorfahren(), [neu, self.getraenke])
        self.assertEqual(neu.anzahl_aller_waren, 1)

    def test_uebernommene_unterfamilien_brauchen_den_faktor(self):
        """Warenfamilie K in pieces takes the juices: the juice lacks its pieces per litre."""
        antwort = self.client.post(
            reverse("warenfamilie_add"),
            {
                "unter": self.getraenke.pk,
                "name": "Warenfamilie K",
                "beschreibung": "",
                "leiteinheit": self.stueck.pk,
                "uebergeordnet": self.getraenke.pk,
                "ware": [],
                "unterfamilien": [self.saefte.pk],
            },
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, f'name="faktor_{self.saft.pk}_STUECK"')
        self.assertFalse(Warenfamilie.objects.filter(name="Warenfamilie K").exists())


class AufteilenUndZusammenfuehren(Getraenke):
    def test_aufteilen_bleibt_unter_derselben_familie(self):
        bericht = aufteilen(self.biere, "Warenfamilie B", "Warenfamilie B2", [self.bier])
        self.assertEqual(bericht.neu.uebergeordnet, self.getraenke)
        self.assertEqual(bericht.neu.leiteinheit, self.stueck)

    def test_zusammenfuehren_nimmt_die_unterfamilien_mit(self):
        andere = Warenfamilie.objects.create(name="Warenfamilie A", leiteinheit=self.liter)
        bericht = zusammenfuehren(andere, self.getraenke, "Warenfamilie A")
        self.biere.refresh_from_db()
        self.assertEqual(self.biere.uebergeordnet, bericht.familie)
        self.assertEqual(bericht.neue_waren, 1)

    def test_die_familie_darueber_geht_in_der_darunter_auf(self):
        """Warenfamilie G goes into Warenfamilie B: B takes its place, S moves under B."""
        bericht = zusammenfuehren(
            self.biere,
            self.getraenke,
            "Warenfamilie B",
            {(self.wasser.pk, Dimension.STUECK): Decimal("2")},
        )
        self.biere.refresh_from_db()
        self.saefte.refresh_from_db()
        self.assertIsNone(self.biere.uebergeordnet)
        self.assertEqual(self.saefte.uebergeordnet, self.biere)
        self.assertEqual(bericht.familie.leiteinheit, self.stueck)

    def test_fehlende_faktoren_sehen_die_familie_darueber(self):
        """Into Warenfamilie B (pieces, under litres): the kilo product lacks both."""
        kilos = Warenfamilie.objects.create(name="Warenfamilie K", leiteinheit=self.kilo)
        lose = self.ware("Ware L", self.kilo, kilos)
        self.assertEqual(
            fehlende_faktoren(self.biere, kilos),
            [(lose, Dimension.VOLUMEN), (lose, Dimension.STUECK)],
        )
        # A subfamily of the absorbed family counts too.
        unter = Warenfamilie.objects.create(
            name="Warenfamilie U", leiteinheit=self.kilo, uebergeordnet=kilos
        )
        tief = self.ware("Ware T", self.kilo, unter, {Dimension.STUECK: "4"})
        self.assertIn((tief, Dimension.VOLUMEN), fehlende_faktoren(self.biere, kilos))
