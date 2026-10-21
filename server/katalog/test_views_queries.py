"""Lists must not query per row: equally many queries with two and with six rows."""

from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from django_project.testing import AngemeldeteSeiten
from organisation.models import Bereich, Haus, Ort

from .models import (
    Basiseinheit,
    Dimension,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Rezepturartikel,
    Rezepturkomponente,
    Ware,
    Warengruppe,
)


class AbfragenWachsenNichtMitDenZeilen(AngemeldeteSeiten):
    """Lists must not cost a query per row, measured with two and with six rows."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Ost")
        cls.kino_sued = Haus.objects.create(name="Kino Süd")
        cls.ort = Ort.objects.create(
            name="Theke", bereich=Bereich.objects.create(name="Theke", haus=cls.haus)
        )
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.nummer = 700000

    def ware(self, gruppe, name):
        """A product in both houses, with pack and counting list: everything a row reads."""
        ware = Ware.objects.create(name=name, basiseinheit=self.stueck)
        kasten = Gebinde.objects.create(
            ware=ware, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        for haus in (self.haus, self.kino_sued):
            type(self).nummer += 1
            artikel = Hausartikel.objects.create(
                haus=haus,
                ware=ware,
                warengruppe=gruppe,
                artikelnummer=str(self.nummer),
                name=name,
            )
            if haus == self.haus:
                erwartung = Erwartung.objects.create(ort=self.ort, artikel=artikel)
                ErwartungGebinde.objects.create(erwartung=erwartung, gebinde=kasten)
        return ware

    def rezepturartikel(self, gruppe, name):
        zutat = self.ware(gruppe, f"Zutat {name}")
        zaehl = Rezepturartikel.objects.create(name=name, basiseinheit=self.stueck)
        Rezepturkomponente.objects.create(
            rezepturartikel=zaehl, ware=zutat, menge_in_basiseinheit=Decimal("1")
        )
        Erwartung.objects.create(ort=self.ort, artikel=zaehl)
        return zaehl

    def abfragen(self, adresse) -> int:
        with CaptureQueriesContext(connection) as gezaehlt:
            self.assertEqual(self.client.get(adresse).status_code, 200)
        return len(gezaehlt)

    def test_warengruppe(self):
        gruppe = Warengruppe.objects.create(name="Warengruppe G")
        for i in range(2):
            self.ware(gruppe, f"Ware {i}")
        adresse = reverse("warengruppe_detail", args=[gruppe.pk])
        wenige = self.abfragen(adresse)
        for i in range(2, 6):
            self.ware(gruppe, f"Ware {i}")
        self.assertEqual(self.abfragen(adresse), wenige)

    def test_warengruppenliste(self):
        for i in range(2):
            self.ware(Warengruppe.objects.create(name=f"Gruppe {i}"), f"Ware {i}")
        wenige = self.abfragen(reverse("warengruppe_list"))
        for i in range(2, 6):
            self.ware(Warengruppe.objects.create(name=f"Gruppe {i}"), f"Ware {i}")
        self.assertEqual(self.abfragen(reverse("warengruppe_list")), wenige)

    def test_rezepturartikelliste(self):
        gruppe = Warengruppe.objects.create(name="Warengruppe S")
        for i in range(2):
            self.rezepturartikel(gruppe, f"Portion {i}")
        wenige = self.abfragen(reverse("rezepturartikel_list"))
        for i in range(2, 6):
            self.rezepturartikel(gruppe, f"Portion {i}")
        self.assertEqual(self.abfragen(reverse("rezepturartikel_list")), wenige)

    def test_zaehlliste(self):
        gruppe = Warengruppe.objects.create(name="Warengruppe G")
        for i in range(2):
            self.ware(gruppe, f"Ware {i}")
        adresse = reverse("zaehlliste", args=[self.ort.pk])
        wenige = self.abfragen(adresse)
        for i in range(2, 6):
            self.ware(gruppe, f"Ware {i}")
        self.assertEqual(self.abfragen(adresse), wenige)

    def test_zaehlliste_mit_vorgaben_an_inaktiven_gebinden(self):
        """They stand in their row as text, with their pack loaded along for all rows."""
        gruppe = Warengruppe.objects.create(name="Warengruppe G")
        for i in range(2):
            self.ware(gruppe, f"Ware {i}")
        Gebinde.objects.update(aktiv=False)
        adresse = reverse("zaehlliste", args=[self.ort.pk])
        wenige = self.abfragen(adresse)
        for i in range(2, 6):
            self.ware(gruppe, f"Ware {i}")
        Gebinde.objects.update(aktiv=False)
        self.assertEqual(self.abfragen(adresse), wenige)

    def test_ort_aufteilen(self):
        """Measured on 2026-09-18: 147 queries with 47 articles, three per row for the location."""
        gruppe = Warengruppe.objects.create(name="Warengruppe G")
        for i in range(2):
            self.ware(gruppe, f"Ware {i}")
        adresse = reverse("ort_aufteilen", args=[self.ort.pk])
        wenige = self.abfragen(adresse)
        for i in range(2, 6):
            self.ware(gruppe, f"Ware {i}")
        self.assertEqual(self.abfragen(adresse), wenige)

    def test_behaelterformular(self):
        """The selection of products names its unit per product, loaded in one go."""
        gruppe = Warengruppe.objects.create(name="Warengruppe S")
        for i in range(2):
            self.ware(gruppe, f"Ware {i}")
        Ware.objects.update(abwiegbar=True)
        wenige = self.abfragen(reverse("behaelter_add"))
        for i in range(2, 6):
            self.ware(gruppe, f"Ware {i}")
        Ware.objects.update(abwiegbar=True)
        self.assertEqual(self.abfragen(reverse("behaelter_add")), wenige)

    def test_hausartikel_in_der_verwaltung(self):
        """Measured: 107 queries per page, the unit behind every product separately."""
        gruppe = Warengruppe.objects.create(name="Warengruppe G")
        adresse = reverse("admin:katalog_hausartikel_changelist")
        for i in range(2):
            self.ware(gruppe, f"Ware {i}")
        self.client.get(adresse)  # fills Django's cache of the content types
        wenige = self.abfragen(adresse)
        for i in range(2, 6):
            self.ware(gruppe, f"Ware {i}")
        self.assertEqual(self.abfragen(adresse), wenige)

    def test_hausartikelliste(self):
        """The house's unit per row separately: 50 queries with 50 rows, until 09/2026."""
        gruppe = Warengruppe.objects.create(name="Warengruppe G")
        adresse = reverse("hausartikel_list", args=[self.haus.pk])
        for i in range(2):
            self.ware(gruppe, f"Ware {i}")
        wenige = self.abfragen(adresse)
        for i in range(2, 6):
            self.ware(gruppe, f"Ware {i}")
        self.assertEqual(self.abfragen(adresse), wenige)

    def test_hausseite(self):
        """The preview of the newest house articles shows the house's unit too."""
        gruppe = Warengruppe.objects.create(name="Warengruppe G")
        adresse = reverse("haus_detail", args=[self.haus.pk])
        for i in range(2):
            self.ware(gruppe, f"Ware {i}")
        wenige = self.abfragen(adresse)
        for i in range(2, 6):
            self.ware(gruppe, f"Ware {i}")
        self.assertEqual(self.abfragen(adresse), wenige)

    def test_erwartungen_in_der_verwaltung(self):
        """The filter "Location" named each with house and area: two queries per location."""
        gruppe = Warengruppe.objects.create(name="Warengruppe G")
        self.ware(gruppe, "Ware A")
        adresse = reverse("admin:katalog_erwartung_changelist")
        erwartung = Erwartung.objects.first()
        assert erwartung is not None
        aendern = reverse("admin:katalog_erwartung_change", args=[erwartung.pk])
        for seite in (adresse, aendern):
            self.client.get(seite)  # fills Django's cache of the content types
        wenige = {seite: self.abfragen(seite) for seite in (adresse, aendern)}
        bereich = Bereich.objects.create(name="Lager", haus=self.kino_sued)
        for i in range(5):
            Ort.objects.create(name=f"Regal {i}", bereich=bereich)
            self.ware(gruppe, f"Ware {i}")
        for seite, anzahl in wenige.items():
            with self.subTest(seite=seite):
                self.assertEqual(self.abfragen(seite), anzahl)

    def test_rezepturartikel_ueber_die_haeuser(self):
        """Whether it can be resolved the page asked twice per house."""
        gruppe = Warengruppe.objects.create(name="Warengruppe G")
        zaehl = self.rezepturartikel(gruppe, "Anteil N")
        adresse = reverse("rezepturartikel_detail", args=[zaehl.pk])
        wenige = self.abfragen(adresse)
        for name in ("Kino Nord", "Kino West", "Kino Mitte"):
            Haus.objects.create(name=name)
        self.assertEqual(self.abfragen(adresse), wenige)

    def test_rezepturformular(self):
        """The products to choose from once per form, not once per ingredient row."""
        gruppe = Warengruppe.objects.create(name="Warengruppe G")
        zaehl = self.rezepturartikel(gruppe, "Anteil N")
        adresse = reverse("rezepturartikel_edit", args=[zaehl.pk])
        wenige = self.abfragen(adresse)
        for i in range(4):
            Rezepturkomponente.objects.create(
                rezepturartikel=zaehl,
                ware=self.ware(gruppe, f"Zutat {i}"),
                menge_in_basiseinheit=Decimal("1"),
            )
        self.assertEqual(self.abfragen(adresse), wenige)
