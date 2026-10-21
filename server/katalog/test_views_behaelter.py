"""Maintain containers and the one that applies everywhere."""

from decimal import Decimal

from django.urls import reverse

from django_project.form_fields import wahlmenge
from django_project.testing import AngemeldeteSeiten
from organisation.models import Haus

from .forms import RezepturartikelForm, WareForm
from .models import (
    Basiseinheit,
    Behaelter,
    Dimension,
    Rezepturartikel,
    Ware,
    Warenfamilie,
)


class BehaelterPflege(AngemeldeteSeiten):
    """Vessels including their assignments, maintained from the container.

    The relations are on the pack or the recipe article. The form writes
    them backwards, which Django does not do by itself.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        gramm = Basiseinheit.objects.get(name="g")
        cls.nuesse = Ware.objects.create(name="Ware N", basiseinheit=gramm, abwiegbar=True)
        cls.mix = Rezepturartikel.objects.create(name="Mischung N", basiseinheit=gramm)

    def test_ohne_behaelter_heisst_auf_der_seite_wie_in_der_sprache(self):
        """The row belongs to the application. Header and breadcrumbs showed the stored name."""
        self.user.sprache = "en"
        self.user.save(update_fields=["sprache"])
        antwort = self.client.get(reverse("behaelter_edit", args=[Behaelter.ohne_behaelter().pk]))
        self.assertContains(antwort, "No container (scale tared)")
        self.assertNotContains(antwort, "Ohne Behälter")

    def test_die_zeile_ohne_loeschknopf_haelt_den_platz_frei(self):
        """Otherwise "Edit" for "Without container" would slip out of the others' column.

        The button would sit further right as the only one.
        """
        antwort = self.client.get(reverse("behaelter_list"))

        seite = antwort.content.decode()
        self.assertIn("btn-outline-danger invisible", seite)
        # And exactly once: "Without container" is the only one that stays.
        self.assertEqual(seite.count("btn-outline-danger invisible"), 1)

    def test_anlegen_setzt_beide_zuordnungen(self):
        antwort = self.client.post(
            reverse("behaelter_add"),
            {
                "name": "Eimer",
                "tara": "300",
                "ware": [self.nuesse.pk],
                "rezepturartikel": [self.mix.pk],
            },
        )
        self.assertRedirects(antwort, reverse("behaelter_list"))
        eimer = Behaelter.objects.get(name="Eimer")
        self.assertEqual(list(self.nuesse.behaelter.all()), [eimer])
        self.assertEqual(list(self.mix.behaelter.all()), [eimer])

    def test_bearbeiten_zeigt_die_bestehende_zuordnung(self):
        eimer = Behaelter.objects.create(name="Eimer", tara=Decimal("300"))
        eimer.waren.set([self.nuesse])
        form = self.client.get(reverse("behaelter_edit", args=[eimer.pk])).context["form"]
        self.assertEqual(list(form.fields["ware"].initial), [self.nuesse])

    def test_zuordnung_laesst_sich_loesen(self):
        eimer = Behaelter.objects.create(name="Eimer", tara=Decimal("300"))
        eimer.waren.set([self.nuesse])
        self.client.post(
            reverse("behaelter_edit", args=[eimer.pk]), {"name": "Eimer", "tara": "300"}
        )
        self.assertFalse(self.nuesse.behaelter.exists())

    def test_was_die_auswahl_nicht_anbietet_bleibt_zugeordnet(self):
        """Until 10/2026 changing the tare dropped it, and the way back came without container."""
        eimer = Behaelter.objects.create(name="Eimer", tara=Decimal("300"))
        stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        nicht_zaehlbar = Ware.objects.create(
            name="Ware Z",
            basiseinheit=self.nuesse.basiseinheit,
            abwiegbar=True,
            warenfamilie=Warenfamilie.nicht_zaehlbar(),
        )
        ohne_waage = Ware.objects.create(name="Ware O", basiseinheit=stueck)
        portion = Rezepturartikel.objects.create(name="Portion", basiseinheit=stueck)
        eimer.waren.set([self.nuesse, nicht_zaehlbar, ohne_waage])
        eimer.rezepturartikel.set([self.mix, portion])

        form = self.client.get(reverse("behaelter_edit", args=[eimer.pk])).context["form"]
        self.assertEqual(list(form.fields["ware"].queryset), [self.nuesse])
        antwort = self.client.post(
            reverse("behaelter_edit", args=[eimer.pk]), {"name": "Eimer", "tara": "350"}
        )

        self.assertRedirects(antwort, reverse("behaelter_list"))
        # Unticked in the selection: those go. The others were not offered and stay.
        self.assertEqual(sorted(eimer.waren.values_list("name", flat=True)), ["Ware O", "Ware Z"])
        self.assertEqual(list(eimer.rezepturartikel.all()), [portion])

    def test_nur_gewogene_rezepturartikel_stehen_zur_wahl(self):
        stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        Rezepturartikel.objects.create(name="Portion", basiseinheit=stueck)
        form = self.client.get(reverse("behaelter_add")).context["form"]
        self.assertEqual(list(form.fields["rezepturartikel"].queryset), [self.mix])

    def test_benutzter_behaelter_bleibt_stehen(self):
        eimer = Behaelter.objects.create(name="Eimer", tara=Decimal("300"))
        eimer.waren.set([self.nuesse])
        self.client.post(reverse("behaelter_delete", args=[eimer.pk]))
        self.assertTrue(Behaelter.objects.filter(pk=eimer.pk).exists())

    def test_unbenutzter_behaelter_laesst_sich_loeschen(self):
        eimer = Behaelter.objects.create(name="Eimer", tara=Decimal("300"))
        antwort = self.client.post(reverse("behaelter_delete", args=[eimer.pk]))
        self.assertRedirects(antwort, reverse("behaelter_list"))
        self.assertFalse(Behaelter.objects.filter(pk=eimer.pk).exists())

    def test_ohne_behaelter_ist_geschuetzt(self):
        """Tare 0 would otherwise shift every weighing that used it."""
        ohne = Behaelter.ohne_behaelter()
        form = self.client.get(reverse("behaelter_edit", args=[ohne.pk])).context["form"]
        self.assertTrue(form.fields["tara"].disabled)

        self.client.post(reverse("behaelter_delete", args=[ohne.pk]))
        self.assertTrue(Behaelter.objects.filter(pk=ohne.pk).exists())

    def test_liste_nennt_die_zuordnungen(self):
        eimer = Behaelter.objects.create(name="Eimer", tara=Decimal("300"))
        eimer.waren.set([self.nuesse])
        eimer.rezepturartikel.set([self.mix])
        antwort = self.client.get(reverse("behaelter_list"))
        self.assertContains(antwort, "Ware N")
        self.assertContains(antwort, "Mischung N")


class BehaelterDerHaeuser(AngemeldeteSeiten):
    """The bowl standing around in the house, unlike the can that only knows Posten S."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.kino_ost = Haus.objects.create(name="Kino Ost")
        cls.kino_sued = Haus.objects.create(name="Kino Süd")
        gramm = Basiseinheit.objects.get(name="g")
        cls.nuesse = Ware.objects.create(name="Ware N", basiseinheit=gramm, abwiegbar=True)
        cls.sahne = Ware.objects.create(name="Posten S", basiseinheit=gramm, abwiegbar=True)
        cls.mix = Rezepturartikel.objects.create(name="Mischung N", basiseinheit=gramm)
        cls.flasche = Behaelter.objects.create(name="Druckflasche", tara=Decimal("120"))
        cls.sahne.behaelter.add(cls.flasche)
        cls.schale = Behaelter.objects.create(name="Schale", tara=Decimal("250"))
        cls.schale.haeuser.add(cls.kino_sued)

    def test_das_formular_ordnet_haeuser_zu_und_die_liste_nennt_sie(self):
        antwort = self.client.post(
            reverse("behaelter_add"),
            {"name": "Eimer", "tara": "300", "haeuser": [self.kino_ost.pk, self.kino_sued.pk]},
        )
        self.assertRedirects(antwort, reverse("behaelter_list"))
        eimer = Behaelter.objects.get(name="Eimer")
        self.assertCountEqual(eimer.haeuser.all(), [self.kino_ost, self.kino_sued])

        liste = self.client.get(reverse("behaelter_list"))
        self.assertContains(liste, "In den Häusern")
        self.assertContains(liste, "nur an seinen Artikeln")
        # The search finds a container through its house too.
        gefunden = self.client.get(reverse("behaelter_list"), {"q": "kino ost"}).context[
            "behaelter"
        ]
        self.assertEqual([b.name for b in gefunden], ["Eimer"])

    def test_ein_haus_ist_keine_pflicht(self):
        antwort = self.client.post(reverse("behaelter_add"), {"name": "Dose", "tara": "80"})
        self.assertRedirects(antwort, reverse("behaelter_list"))
        self.assertFalse(Behaelter.objects.get(name="Dose").haeuser.exists())

    def test_was_fuer_einen_artikel_zur_wahl_steht(self):
        """Assigned, from any house, "Without container": the can only with its product."""
        ohne = Behaelter.ohne_behaelter()
        faelle = (
            (self.nuesse, [self.schale, ohne]),
            (self.sahne, [self.flasche, self.schale, ohne]),
            (self.mix, [self.schale, ohne]),
        )
        for artikel, erwartet in faelle:
            with self.subTest(artikel=artikel.name):
                self.assertCountEqual(Behaelter.zur_wahl_fuer(artikel), erwartet)

    def test_ein_behaelter_in_zwei_haeusern_steht_nur_einmal_zur_wahl(self):
        self.schale.haeuser.add(self.kino_ost)
        self.assertEqual(list(Behaelter.zur_wahl_fuer(self.nuesse)).count(self.schale), 1)

    def test_ohne_behaelter_bekommt_kein_haus(self):
        form = self.client.get(
            reverse("behaelter_edit", args=[Behaelter.ohne_behaelter().pk])
        ).context["form"]
        self.assertTrue(form.fields["haeuser"].disabled)


class OhneBehaelterIstUeberall(AngemeldeteSeiten):
    """The row "Without container" is not assigned. It applies anyway.

    The rule stands once on the model ('Behaelter.zuweisbar') and has to
    apply on every interface that offers containers for choice.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.schale = Behaelter.objects.create(name="Schale", tara=Decimal("250"))
        cls.ohne = Behaelter.ohne_behaelter()

    def test_zuweisbar_laesst_ihn_aus(self):
        self.assertNotIn(self.ohne, Behaelter.zuweisbar())
        self.assertIn(self.schale, Behaelter.zuweisbar())

    def test_gebindeformular_bietet_ihn_nicht_an(self):
        self.assertNotIn(self.ohne, wahlmenge(WareForm(), "behaelter"))

    def test_rezepturartikelformular_bietet_ihn_nicht_an(self):
        self.assertNotIn(self.ohne, wahlmenge(RezepturartikelForm(), "behaelter"))

    def test_admin_bietet_ihn_nicht_an(self):
        """The second path to the same model must not circumvent the rule."""
        # Not '/admin/', because the honeypot sits there, the real admin lies
        # under ADMIN_URL.
        antwort = self.client.get(reverse("admin:katalog_ware_add"))
        self.assertEqual(antwort.status_code, 200)
        feld = antwort.context["adminform"].form.fields["behaelter"]
        self.assertNotIn(self.ohne, feld.queryset)
        self.assertIn(self.schale, feld.queryset)

    def test_seine_eigene_seite_sperrt_die_zuordnung(self):
        antwort = self.client.get(reverse("behaelter_edit", args=[self.ohne.pk]))
        form = antwort.context["form"]
        self.assertTrue(form.fields["ware"].disabled)
        self.assertTrue(form.fields["rezepturartikel"].disabled)
        self.assertTrue(form.fields["tara"].disabled)

    def test_andere_behaelter_bleiben_zuweisbar(self):
        antwort = self.client.get(reverse("behaelter_edit", args=[self.schale.pk]))
        form = antwort.context["form"]
        self.assertFalse(form.fields["ware"].disabled)
        self.assertFalse(form.fields["tara"].disabled)
