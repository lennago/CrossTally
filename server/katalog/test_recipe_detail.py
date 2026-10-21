"""The page of a prepared item answers per house the way the count list does.

An inactive ingredient makes the item an inactive one there: it can be put
into the count list and added in the app. Until 10/2026 the page said
"component missing" for that house and offered no button, while the
selection tab of the count list offered the item.
"""

from django.urls import reverse

from django_project.testing import AngemeldeteSeiten
from katalog.models import (
    Basiseinheit,
    Hausartikel,
    Rezepturartikel,
    Rezepturkomponente,
    Ware,
    Warengruppe,
)
from organisation.models import Bereich, Haus, Ort

# The badges, not the words: the help text names every case too.
FEHLT = '<span class="badge bg-body-secondary text-body border">Komponente fehlt</span>'
INAKTIV = "Zutat inaktiv</span>"


class ZutatInaktiv(AngemeldeteSeiten):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.ort = Ort.objects.create(
            name="Theke", bereich=Bereich.objects.create(name="Foyer", haus=cls.haus)
        )
        stueck = Basiseinheit.objects.get(name="Stück(e)")
        ware = Ware.objects.create(name="Ware N", basiseinheit=stueck)
        cls.zutat = Hausartikel.objects.create(
            haus=cls.haus,
            ware=ware,
            warengruppe=Warengruppe.objects.create(name="Warengruppe G"),
            artikelnummer="101001",
            name="Ware N",
        )
        cls.portion = Rezepturartikel.objects.create(name="Portion P", basiseinheit=stueck)
        Rezepturkomponente.objects.create(
            rezepturartikel=cls.portion, ware=ware, menge_in_basiseinheit=1
        )

    def seite(self):
        return self.client.get(reverse("rezepturartikel_detail", args=[self.portion.pk]))

    def test_mit_aktiver_zutat_steht_ja_und_hinzufuegen_da(self):
        antwort = self.seite()
        self.assertContains(antwort, "Hinzufügen")
        self.assertNotContains(antwort, FEHLT)
        self.assertNotContains(antwort, INAKTIV)

    def test_mit_inaktiver_zutat_bleibt_der_knopf_und_die_seite_sagt_warum(self):
        self.zutat.deaktivieren()

        antwort = self.seite()

        self.assertContains(antwort, INAKTIV)
        self.assertContains(antwort, "Hinzufügen")
        self.assertNotContains(antwort, FEHLT)

    def test_mit_eingestellter_zutat_fehlt_die_komponente(self):
        self.zutat.einstellen()

        antwort = self.seite()

        self.assertContains(antwort, FEHLT)
        self.assertNotContains(antwort, "Hinzufügen")
