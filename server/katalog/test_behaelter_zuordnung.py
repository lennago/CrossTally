"""The assignment of containers to product and recipe article, without preselection.

Until 09/2026 there was a default container for that, which the counting
app preselected. The app preselects nothing any more since 2026-09-19 (a
guessed tare goes unnoticed by anyone when counting), and on 2026-09-21
the default dropped out of model, forms and API. What remains is the
assignment itself.
"""

from decimal import Decimal

from django.urls import reverse

from django_project.testing import AngemeldeteSeiten
from organisation.models import Haus

from .forms import RezepturartikelForm, WareForm
from .models import Basiseinheit, Behaelter, Hausartikel, Ware, Warengruppe


class Zuordnung(AngemeldeteSeiten):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.gramm = Basiseinheit.objects.get(name="g")
        cls.schale = Behaelter.objects.create(name="Schale", tara=Decimal("250"))
        cls.eimer = Behaelter.objects.create(name="Eimer", tara=Decimal("300"))
        cls.nachos = Ware.objects.create(
            name="Artikel N lose", basiseinheit=cls.gramm, abwiegbar=True
        )
        cls.artikel = Hausartikel.objects.create(
            haus=Haus.objects.create(name="Kino Ost"),
            ware=cls.nachos,
            warengruppe=Warengruppe.objects.create(name="Warengruppe S"),
            artikelnummer="101002",
            name="Artikel N lose",
        )

    def ware_form(self, **abweichend):
        daten = {
            "name": "Artikel N lose",
            "basiseinheit": self.gramm.pk,
            "inventurart": "",
            "abwiegbar": "on",
            "behaelter": [self.schale.pk, self.eimer.pk],
        }
        return WareForm(daten | abweichend, instance=self.nachos)

    def test_mehrere_behaelter_ohne_weitere_wahl(self):
        form = self.ware_form()
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().behaelter.count(), 2)

    def test_wer_nicht_wiegt_hat_keine_behaelter(self):
        form = self.ware_form(abwiegbar="", behaelter=[])
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().behaelter.count(), 0)

    def test_rezepturartikel_ebenso(self):
        daten = {
            "name": "Mischung N",
            "basiseinheit": self.gramm.pk,
            "behaelter": [self.schale.pk, self.eimer.pk],
        }
        form = RezepturartikelForm(daten)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().behaelter.count(), 2)

    def test_die_seite_kennt_keine_vorbelegung_mehr(self):
        for adresse in (
            reverse("ware_edit", args=[self.nachos.pk]),
            self.artikel.get_absolute_url(),
            self.nachos.get_absolute_url(),
        ):
            with self.subTest(adresse=adresse):
                antwort = self.client.get(adresse)
                self.assertEqual(antwort.status_code, 200)
                self.assertNotContains(antwort, "standardbehaelter")
                self.assertNotContains(antwort, "vorbelegt")

    def test_schnellzuordnung_am_hausartikel(self):
        """Maintained from the house, stored on the product."""
        antwort = self.client.post(
            reverse("ware_behaelter", args=[self.nachos.pk]),
            {"behaelter": [self.schale.pk, self.eimer.pk]},
            follow=True,
        )
        self.assertContains(antwort, "Behälter für „Artikel N lose“ gespeichert")
        self.assertEqual(self.nachos.behaelter.count(), 2)

    def test_schnellzuordnung_leert_auch(self):
        self.nachos.behaelter.add(self.schale)
        self.client.post(reverse("ware_behaelter", args=[self.nachos.pk]), {})
        self.assertEqual(self.nachos.behaelter.count(), 0)
