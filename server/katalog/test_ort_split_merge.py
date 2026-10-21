"""Split and merge locations.

Checked above all is what has to be **preserved**: the order of the
counting list, the packs including default quantities, and when merging
the precedence of the location that stays.
"""

from datetime import date
from decimal import Decimal
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser
from django_project.testing import inventur_anlegen
from inventur.models import Zaehlposition
from organisation.models import Bereich, Haus, Ort

from .models import (
    Basiseinheit,
    Dimension,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Ware,
    Warengruppe,
)
from .ort_split_merge import aufteilen, vorschau, zusammenfuehren
from .views import zaehlliste as zaehlliste_views


class OrteGrundlagen(TestCase):
    """An area with two locations and four products, each with crate and bottle."""

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Ost")
        cls.bereich = Bereich.objects.create(name="Theke", haus=cls.haus)
        stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        gruppe = Warengruppe.objects.create(name="Warengruppe G")
        cls.artikel = {}
        cls.kasten = {}
        cls.flasche = {}
        for nummer, name in enumerate(
            ("Artikel C", "Artikel F", "Artikel S", "Artikel W"), start=1
        ):
            ware = Ware.objects.create(name=name, basiseinheit=stueck)
            cls.kasten[name] = Gebinde.objects.create(
                ware=ware, name="Kasten", faktor_in_basiseinheit=Decimal("24")
            )
            cls.flasche[name] = Gebinde.objects.create(
                ware=ware, name="Flasche", faktor_in_basiseinheit=Decimal("1")
            )
            cls.artikel[name] = Hausartikel.objects.create(
                haus=cls.haus,
                ware=ware,
                warengruppe=gruppe,
                artikelnummer=f"{101000 + nummer}",
                name=name,
            )

        cls.a = Ort.objects.create(name="Front", bereich=cls.bereich)
        cls.b = Ort.objects.create(name="Rückseite", bereich=cls.bereich)

    def erwarten(self, ort, name, *gebinde, menge=None):
        erwartung = Erwartung.objects.create(ort=ort, artikel=self.artikel[name])
        for satz in gebinde:
            ErwartungGebinde.objects.create(erwartung=erwartung, gebinde=satz, standard_menge=menge)
        return erwartung

    def liste(self, ort):
        return list(
            Erwartung.objects.filter(ort=ort)
            .order_by("sortierung")
            .values_list("artikel__name", "sortierung")
        )


class Aufteilen(OrteGrundlagen):
    def test_jede_haelfte_behaelt_ihre_reihenfolge_lueckenlos(self):
        cola = self.erwarten(self.a, "Artikel C")
        fassbrause = self.erwarten(self.a, "Artikel F")
        sprudel = self.erwarten(self.a, "Artikel S")
        wasser = self.erwarten(self.a, "Artikel W")

        bericht = aufteilen(self.a, "Front links", "Front rechts", [fassbrause, wasser])

        self.a.refresh_from_db()
        self.assertEqual(self.a.name, "Front links")
        self.assertEqual(self.liste(self.a), [("Artikel C", 1), ("Artikel S", 2)])
        self.assertEqual(self.liste(bericht.neu), [("Artikel F", 1), ("Artikel W", 2)])
        self.assertEqual(bericht.neu.bereich, self.bereich)
        self.assertEqual(bericht.gewandert, 2)
        # Unused but occupied: the expectation is the same, not copied.
        self.assertEqual(Erwartung.objects.get(pk=cola.pk).ort, self.a)
        self.assertEqual(Erwartung.objects.get(pk=sprudel.pk).ort, self.a)

    def test_gebinde_und_mengen_gehen_mit(self):
        fassbrause = self.erwarten(
            self.a, "Artikel F", self.kasten["Artikel F"], menge=Decimal("3")
        )
        bericht = aufteilen(self.a, "Front", "Neu", [fassbrause.pk])
        vorgabe = ErwartungGebinde.objects.get()
        self.assertEqual(vorgabe.erwartung.ort, bericht.neu)
        self.assertEqual(vorgabe.standard_menge, Decimal("3"))

    def test_fremde_erwartungen_bleiben_wo_sie_sind(self):
        self.erwarten(self.a, "Artikel C")
        fremd = self.erwarten(self.b, "Artikel F")
        bericht = aufteilen(self.a, "Front", "Neu", [fremd.pk])
        self.assertEqual(Erwartung.objects.get(pk=fremd.pk).ort, self.b)
        self.assertEqual(self.liste(bericht.neu), [])


class Zusammenfuehren(OrteGrundlagen):
    def test_neue_artikel_hinten_in_ihrer_reihenfolge(self):
        self.erwarten(self.a, "Artikel C")
        self.erwarten(self.a, "Artikel F")
        self.erwarten(self.b, "Artikel W")
        self.erwarten(self.b, "Artikel S")

        bericht = zusammenfuehren(self.a, self.b, "Front")

        self.assertEqual(
            self.liste(self.a),
            [("Artikel C", 1), ("Artikel F", 2), ("Artikel W", 3), ("Artikel S", 4)],
        )
        self.assertEqual(bericht.neue_artikel, 2)
        self.assertFalse(Ort.objects.filter(pk=self.b.pk).exists())

    def test_vorhandener_artikel_bekommt_nur_neue_gebinde(self):
        """The remaining location wins, for the default quantity too."""
        self.erwarten(self.a, "Artikel C", self.kasten["Artikel C"], menge=Decimal("2"))
        self.erwarten(
            self.b,
            "Artikel C",
            self.kasten["Artikel C"],
            self.flasche["Artikel C"],
            menge=Decimal("9"),
        )

        bericht = zusammenfuehren(self.a, self.b, "Front")

        erwartung = Erwartung.objects.get(ort=self.a)
        vorgaben = {v.gebinde.name: v.standard_menge for v in erwartung.gebinde_vorgaben.all()}
        self.assertEqual(vorgaben, {"Kasten": Decimal("2"), "Flasche": Decimal("9")})
        self.assertEqual(bericht.neue_artikel, 0)
        self.assertEqual(bericht.neue_gebinde, 1)

    def test_wo_an_einem_der_orte_gewogen_wurde_wird_weiter_gewogen(self):
        """As with the packs: what either location counted, the joint one keeps counting."""
        bleibt = self.erwarten(self.a, "Artikel C", self.kasten["Artikel C"])
        Erwartung.objects.filter(pk=bleibt.pk).update(wiegen_erwartet=False)
        gewogen = self.erwarten(self.b, "Artikel C", self.kasten["Artikel C"])
        Erwartung.objects.filter(pk=gewogen.pk).update(wiegen_erwartet=True)

        zusammenfuehren(self.a, self.b, "Front")

        self.assertTrue(Erwartung.objects.get(ort=self.a).wiegen_erwartet)

    def test_der_bleibende_ort_darf_den_namen_des_anderen_annehmen(self):
        zusammenfuehren(self.a, self.b, "Rückseite")
        self.a.refresh_from_db()
        self.assertEqual(self.a.name, "Rückseite")

    def test_luecken_in_den_positionen_stoeren_nicht(self):
        cola = self.erwarten(self.a, "Artikel C")
        Erwartung.objects.filter(pk=cola.pk).update(sortierung=7)
        self.erwarten(self.b, "Artikel F")
        zusammenfuehren(self.a, self.b, "Front")
        self.assertEqual(self.liste(self.a), [("Artikel C", 1), ("Artikel F", 2)])

    def test_vorschau_schreibt_nichts(self):
        self.erwarten(self.b, "Artikel F")
        zeilen = vorschau(self.a, self.b)
        self.assertTrue(zeilen[0].ist_neu)
        self.assertEqual(self.liste(self.a), [])
        self.assertTrue(Ort.objects.filter(pk=self.b.pk).exists())


class Seiten(OrteGrundlagen):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = CustomUser.objects.create_user(
            username="ortschef", password="geheim", is_staff=True
        )

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_aufteilen_ueber_die_seite(self):
        self.erwarten(self.a, "Artikel C")
        fassbrause = self.erwarten(self.a, "Artikel F")
        adresse = reverse("ort_aufteilen", args=[self.a.pk])
        self.assertEqual(self.client.get(adresse).status_code, 200)

        antwort = self.client.post(
            adresse, {"name": "Links", "neuer_name": "Rechts", "wandern": [fassbrause.pk]}
        )
        self.assertRedirects(antwort, self.a.get_absolute_url())
        neu = Ort.objects.get(name="Rechts")
        self.assertEqual(self.liste(neu), [("Artikel F", 1)])

    def test_aufteilen_auf_einen_vergebenen_namen_wird_abgewiesen(self):
        antwort = self.client.post(
            reverse("ort_aufteilen", args=[self.a.pk]),
            {"name": "Front", "neuer_name": "Rückseite"},
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertIn("neuer_name", antwort.context["form"].errors)
        self.assertEqual(Ort.objects.count(), 2)

    def test_zusammenfuehren_zeigt_erst_die_vorschau(self):
        self.erwarten(self.b, "Artikel F")
        adresse = reverse("ort_zusammenfuehren", args=[self.a.pk])
        antwort = self.client.get(adresse, {"quelle": self.b.pk})
        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(antwort.context["anzahl_neu"], 1)

        # Without a preview on screen nothing is written. The button leads to it.
        antwort = self.client.post(adresse, {"quelle": self.b.pk, "name": "Front"})
        self.assertRedirects(antwort, f"{adresse}?quelle={self.b.pk}&name=Front")
        self.assertTrue(Ort.objects.filter(pk=self.b.pk).exists())

        antwort = self.client.post(
            adresse, {"quelle": self.b.pk, "name": "Front", "gezeigt": self.b.pk}
        )
        self.assertRedirects(antwort, self.a.get_absolute_url())
        self.assertFalse(Ort.objects.filter(pk=self.b.pk).exists())

    def test_eine_andere_wahl_als_die_gezeigte_fuehrt_nichts_zusammen(self):
        """The preview showed one location, selected is another. Neither may go."""
        lager = Ort.objects.create(name="Lager", bereich=self.bereich)
        adresse = reverse("ort_zusammenfuehren", args=[self.a.pk])
        antwort = self.client.post(
            adresse, {"quelle": lager.pk, "name": "Theke", "gezeigt": self.b.pk}, follow=True
        )
        self.assertRedirects(antwort, f"{adresse}?quelle={lager.pk}&name=Theke")
        self.assertEqual(Ort.objects.filter(pk__in=[self.b.pk, lager.pk]).count(), 2)
        self.assertEqual(antwort.context["quelle"], lager)
        self.assertEqual(antwort.context["form"]["name"].value(), "Theke")
        self.assertContains(antwort, "Noch ist nichts geschehen")
        self.assertContains(antwort, f'name="gezeigt" value="{lager.pk}"')

    def test_die_vorschau_folgt_der_wahl_mit_dem_skript(self):
        antwort = self.client.get(reverse("ort_zusammenfuehren", args=[self.a.pk]))
        self.assertContains(antwort, "js/live_preview.js")
        self.assertContains(antwort, "data-vorschau-wahl")
        # Before a choice only the button can send it.
        self.assertContains(antwort, "data-vorschau-knopf")
        antwort = self.client.get(
            reverse("ort_zusammenfuehren", args=[self.a.pk]), {"quelle": self.b.pk}
        )
        self.assertNotContains(antwort, "data-vorschau-knopf")

    def test_der_name_des_aufgehenden_orts_ist_frei_der_eines_dritten_nicht(self):
        Ort.objects.create(name="Kasse", bereich=self.bereich)
        adresse = reverse("ort_zusammenfuehren", args=[self.a.pk])
        antwort = self.client.post(
            adresse, {"quelle": self.b.pk, "name": "Kasse", "gezeigt": self.b.pk}
        )
        self.assertIn("name", antwort.context["form"].errors)
        self.assertTrue(Ort.objects.filter(pk=self.b.pk).exists())

        antwort = self.client.post(
            adresse, {"quelle": self.b.pk, "name": "Rückseite", "gezeigt": self.b.pk}
        )
        self.assertRedirects(antwort, self.a.get_absolute_url())
        self.a.refresh_from_db()
        self.assertEqual(self.a.name, "Rückseite")

    def test_nur_orte_desselben_bereichs(self):
        anderer = Ort.objects.create(
            name="Lager", bereich=Bereich.objects.create(name="Lager", haus=self.haus)
        )
        antwort = self.client.post(
            reverse("ort_zusammenfuehren", args=[self.a.pk]),
            {"quelle": anderer.pk, "name": "Front", "gezeigt": anderer.pk},
        )
        self.assertIn("quelle", antwort.context["form"].errors)
        self.assertTrue(Ort.objects.filter(pk=anderer.pk).exists())

    def test_laufende_zaehlung_am_wegfallenden_ort_sperrt(self):
        """The count positions point to the location via PROTECT.

        And they belong to an inventory that is still running.
        """
        inventur = inventur_anlegen(self.haus, stichtag=date(2026, 1, 31))
        Zaehlposition.objects.create(
            inventur=inventur,
            ort=self.b,
            artikel=self.artikel["Artikel C"],
            gebinde=self.kasten["Artikel C"],
        )
        antwort = self.client.post(
            reverse("ort_zusammenfuehren", args=[self.a.pk]),
            {"quelle": self.b.pk, "name": "Front", "gezeigt": self.b.pk},
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertIn("Zählpositionen", str(antwort.context["form"].errors["quelle"]))
        self.assertTrue(Ort.objects.filter(pk=self.b.pk).exists())

    def test_zwei_inventuren_am_wegfallenden_ort_heissen_zwei(self):
        """Until 10/2026 the message named two inventories and spoke of "this inventory"."""
        for stichtag in (date(2026, 1, 31), date(2026, 2, 28)):
            Zaehlposition.objects.create(
                inventur=inventur_anlegen(self.haus, stichtag=stichtag),
                ort=self.b,
                artikel=self.artikel["Artikel C"],
                gebinde=self.kasten["Artikel C"],
            )
        antwort = self.client.post(
            reverse("ort_zusammenfuehren", args=[self.a.pk]),
            {"quelle": self.b.pk, "name": "Front", "gezeigt": self.b.pk},
        )
        fehler = str(antwort.context["form"].errors["quelle"])
        self.assertIn("wenn diese Inventuren abgeschlossen oder gelöscht sind", fehler)

    def test_eine_zaehlung_zwischen_pruefung_und_loeschen_wird_gemeldet(self):
        """The app counts at the location just after the check. Until 10/2026 an error page."""
        self.erwarten(self.b, "Artikel F", self.kasten["Artikel F"])
        inventur = inventur_anlegen(self.haus, stichtag=date(2026, 1, 31))
        echt = zaehlliste_views.zusammenfuehren_orte

        def die_app_zaehlt_dazwischen(ziel, quelle, name):
            Zaehlposition.objects.create(
                inventur=inventur,
                ort=quelle,
                artikel=self.artikel["Artikel C"],
                gebinde=self.kasten["Artikel C"],
            )
            return echt(ziel, quelle, name)

        with mock.patch.object(zaehlliste_views, "zusammenfuehren_orte", die_app_zaehlt_dazwischen):
            antwort = self.client.post(
                reverse("ort_zusammenfuehren", args=[self.a.pk]),
                {"quelle": self.b.pk, "name": "Front", "gezeigt": self.b.pk},
            )

        self.assertEqual(antwort.status_code, 200)
        self.assertIn("Zählpositionen", str(antwort.context["form"].errors["quelle"]))
        self.assertTrue(Ort.objects.filter(pk=self.b.pk).exists())
        # Nothing of the merge stays: the transaction took it back.
        self.assertEqual(self.liste(self.a), [])
        self.assertEqual(self.liste(self.b), [("Artikel F", 1)])
