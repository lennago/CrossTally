"""Copy locations.

Checked above all is what has to **arrive**: the order of the counting
list, the packs including default quantities, and across houses the
article of the target house instead of the one of the source.
"""

import re
from decimal import Decimal

from django.urls import reverse

from accounts.models import CustomUser
from django_project.testing import skript
from organisation.models import Bereich, Haus, Ort

from .models import (
    Basiseinheit,
    Dimension,
    Erwartung,
    ErwartungGebinde,
    Hausartikel,
    Rezepturartikel,
    Rezepturkomponente,
    Warengruppe,
)
from .ort_copy import kopieren, vorschau
from .test_ort_split_merge import OrteGrundlagen


class KopierGrundlagen(OrteGrundlagen):
    """Plus a second house that carries two of the four products."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.lager = Bereich.objects.create(name="Lager", haus=cls.haus)
        cls.west = Haus.objects.create(name="Kino West")
        cls.theke_west = Bereich.objects.create(name="Theke", haus=cls.west)
        gruppe = Warengruppe.objects.get(name="Warengruppe G")
        cls.artikel_west = {
            name: Hausartikel.objects.create(
                haus=cls.west,
                ware=cls.artikel[name].ware,
                warengruppe=gruppe,
                artikelnummer=f"{101100 + nummer}",
                name=f"{name} West",
            )
            for nummer, name in enumerate(("Artikel C", "Artikel S"), start=1)
        }


class Kopieren(KopierGrundlagen):
    def test_reihenfolge_gebinde_und_mengen_kommen_an(self):
        self.erwarten(self.a, "Artikel W")
        self.erwarten(self.a, "Artikel C", self.kasten["Artikel C"], menge=Decimal("3"))
        gewogen = self.erwarten(self.a, "Artikel F")
        Erwartung.objects.filter(pk=gewogen.pk).update(wiegen_erwartet=False)

        bericht = kopieren(self.a, self.lager, "Front")

        self.assertEqual(bericht.neu.bereich, self.lager)
        self.assertEqual(bericht.kopiert, 3)
        self.assertEqual(bericht.ausgelassen, [])
        self.assertEqual(
            self.liste(bericht.neu), [("Artikel W", 1), ("Artikel C", 2), ("Artikel F", 3)]
        )
        vorgabe = ErwartungGebinde.objects.get(erwartung__ort=bericht.neu)
        self.assertEqual(vorgabe.gebinde, self.kasten["Artikel C"])
        self.assertEqual(vorgabe.standard_menge, Decimal("3"))
        self.assertFalse(
            Erwartung.objects.get(
                ort=bericht.neu, artikel=self.artikel["Artikel F"]
            ).wiegen_erwartet
        )

    def test_das_original_bleibt_unberuehrt(self):
        self.erwarten(self.a, "Artikel C", self.kasten["Artikel C"], menge=Decimal("3"))
        vorher = self.liste(self.a)

        kopieren(self.a, self.bereich, "Front zwei")

        self.a.refresh_from_db()
        self.assertEqual(self.a.name, "Front")
        self.assertEqual(self.liste(self.a), vorher)
        self.assertEqual(ErwartungGebinde.objects.filter(erwartung__ort=self.a).count(), 1)

    def test_im_anderen_haus_steht_dessen_artikel(self):
        self.erwarten(self.a, "Artikel C", self.kasten["Artikel C"], menge=Decimal("2"))
        self.erwarten(self.a, "Artikel F")
        self.erwarten(self.a, "Artikel S")

        bericht = kopieren(self.a, self.theke_west, "Front")

        self.assertEqual(self.liste(bericht.neu), [("Artikel C West", 1), ("Artikel S West", 2)])
        self.assertEqual(bericht.ausgelassen, ["Artikel F"])
        for erwartung in Erwartung.objects.filter(ort=bericht.neu):
            erwartung.full_clean()
        vorgabe = ErwartungGebinde.objects.get(erwartung__ort=bericht.neu)
        vorgabe.full_clean()
        self.assertEqual(vorgabe.erwartung.artikel_id, self.artikel_west["Artikel C"].pk)

    def test_eingestellter_artikel_des_zielhauses_zaehlt_nicht(self):
        self.erwarten(self.a, "Artikel C")
        self.artikel_west["Artikel C"].einstellen()

        bericht = kopieren(self.a, self.theke_west, "Front")

        self.assertEqual(self.liste(bericht.neu), [])
        self.assertEqual(bericht.ausgelassen, ["Artikel C"])

    def test_rezepturartikel_nur_wo_er_sich_aufloesen_laesst(self):
        stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        portion = Rezepturartikel.objects.create(name="Portion P", basiseinheit=stueck)
        Rezepturkomponente.objects.create(
            rezepturartikel=portion,
            ware=self.artikel["Artikel C"].ware,
            menge_in_basiseinheit=Decimal("1"),
        )
        schale = Rezepturartikel.objects.create(name="Schale S", basiseinheit=stueck)
        Rezepturkomponente.objects.create(
            rezepturartikel=schale,
            ware=self.artikel["Artikel F"].ware,
            menge_in_basiseinheit=Decimal("1"),
        )
        Erwartung.objects.create(ort=self.a, artikel=portion)
        Erwartung.objects.create(ort=self.a, artikel=schale)

        bericht = kopieren(self.a, self.theke_west, "Front")

        self.assertEqual(self.liste(bericht.neu), [("Portion P", 1)])
        self.assertEqual(bericht.ausgelassen, ["Schale S"])

    def test_vorschau_schreibt_nichts(self):
        self.erwarten(self.a, "Artikel F")
        zeilen = vorschau(self.a, self.theke_west)
        self.assertFalse(zeilen[0].wird_kopiert)
        self.assertEqual(Ort.objects.count(), 2)


class Seiten(KopierGrundlagen):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = CustomUser.objects.create_user(
            username="ortschef", password="geheim", is_staff=True
        )

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.adresse = reverse("ort_kopieren", args=[self.a.pk])

    def test_die_seite_oeffnet_mit_dem_eigenen_bereich(self):
        self.erwarten(self.a, "Artikel C")
        antwort = self.client.get(self.adresse)
        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(antwort.context["bereich"], self.bereich)
        self.assertEqual(antwort.context["anzahl_kopiert"], 1)
        # The own name is taken in the own area.
        self.assertEqual(antwort.context["form"]["name"].value(), "Front (Kopie)")

    def test_im_anderen_bereich_ist_der_eigene_name_der_vorschlag(self):
        self.erwarten(self.a, "Artikel C")
        self.erwarten(self.a, "Artikel F")
        antwort = self.client.get(self.adresse, {"bereich": self.theke_west.pk})
        self.assertEqual(antwort.context["form"]["name"].value(), "Front")
        self.assertEqual(antwort.context["anzahl_kopiert"], 1)
        self.assertEqual(antwort.context["anzahl_ausgelassen"], 1)
        self.assertContains(antwort, "wird dort nicht geführt")
        self.assertEqual(Ort.objects.count(), 2)

    def test_kopieren_ueber_die_seite(self):
        self.erwarten(self.a, "Artikel C")
        self.erwarten(self.a, "Artikel F")
        antwort = self.client.post(
            self.adresse,
            {"bereich": self.theke_west.pk, "gezeigt": self.theke_west.pk, "name": "Tresen"},
            follow=True,
        )
        neu = Ort.objects.get(name="Tresen")
        self.assertRedirects(antwort, neu.get_absolute_url())
        self.assertEqual(neu.bereich, self.theke_west)
        self.assertEqual(self.liste(neu), [("Artikel C West", 1)])
        meldungen = [str(meldung) for meldung in antwort.context["messages"]]
        self.assertTrue(any("Artikel F" in meldung for meldung in meldungen), meldungen)

    def test_ein_vergebener_name_wird_abgewiesen(self):
        antwort = self.client.post(
            self.adresse,
            {"bereich": self.bereich.pk, "gezeigt": self.bereich.pk, "name": "Rückseite"},
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertIn("name", antwort.context["form"].errors)
        self.assertEqual(Ort.objects.count(), 2)

    def test_derselbe_name_ist_im_anderen_bereich_frei(self):
        antwort = self.client.post(
            self.adresse, {"bereich": self.lager.pk, "gezeigt": self.lager.pk, "name": "Front"}
        )
        neu = Ort.objects.get(name="Front", bereich=self.lager)
        self.assertRedirects(antwort, neu.get_absolute_url())

    def test_die_wahl_steht_im_formular_das_kopiert(self):
        """Otherwise "Kopieren" sends the area of the last preview, not the selected one."""
        seite = self.client.get(self.adresse).content.decode()
        formular = next(
            formular
            for formular in re.findall(r"<form\b.*?</form>", seite, flags=re.DOTALL)
            if 'name="gezeigt"' in formular
        )
        self.assertIn('method="post"', formular)
        self.assertIn('<select name="bereich"', formular)
        self.assertIn(f'name="gezeigt" value="{self.bereich.pk}"', formular)
        self.assertIn("Kopieren", formular)
        self.assertNotIn('method="get"', seite)

    def test_eine_neue_wahl_zeigt_erst_ihre_vorschau(self):
        """The preview showed the own area, selected is another house."""
        self.erwarten(self.a, "Artikel C")
        self.erwarten(self.a, "Artikel F")
        antwort = self.client.post(
            self.adresse,
            {"bereich": self.theke_west.pk, "gezeigt": self.bereich.pk, "name": "Front (Kopie)"},
            follow=True,
        )
        self.assertEqual(Ort.objects.count(), 2)
        # The untouched suggestion stays behind, in the new area the own name is free.
        self.assertRedirects(antwort, f"{self.adresse}?bereich={self.theke_west.pk}")
        self.assertEqual(antwort.context["bereich"], self.theke_west)
        self.assertEqual(antwort.context["anzahl_ausgelassen"], 1)
        self.assertEqual(antwort.context["form"]["name"].value(), "Front")
        self.assertContains(antwort, "Noch ist nichts geschehen")

    def test_ein_getippter_name_kommt_mit_in_die_vorschau(self):
        antwort = self.client.post(
            self.adresse, {"bereich": self.lager.pk, "gezeigt": self.bereich.pk, "name": "Tresen"}
        )
        self.assertRedirects(antwort, f"{self.adresse}?bereich={self.lager.pk}&name=Tresen")
        self.assertFalse(Ort.objects.filter(name="Tresen").exists())

    def test_der_vorschauknopf_steht_nur_ohne_vorschau(self):
        self.assertNotContains(self.client.get(self.adresse), "data-vorschau-knopf")
        antwort = self.client.get(self.adresse, {"bereich": "999999"})
        self.assertNotIn("bereich", antwort.context)
        self.assertContains(antwort, "data-vorschau-knopf")
        # Pressed, it leads to the preview, without a message: no preview stood before.
        antwort = self.client.post(self.adresse, {"bereich": self.lager.pk}, follow=True)
        self.assertRedirects(antwort, f"{self.adresse}?bereich={self.lager.pk}")
        self.assertEqual(antwort.context["bereich"], self.lager)
        self.assertNotContains(antwort, "Noch ist nichts geschehen")

    def test_seite_und_skript_passen_zusammen(self):
        seite = self.client.get(self.adresse).content.decode()
        self.assertIn("js/live_preview.js", seite)
        quelle = skript("live_preview")
        for merkmal in ("data-vorschau-wahl", "data-vorschau", "data-mitnehmen", "gezeigt"):
            self.assertIn(merkmal, seite)
            self.assertIn(merkmal, quelle)
        self.assertIn("data-vorschau-knopf", quelle)

    def test_die_ortsseite_bietet_das_kopieren_an(self):
        antwort = self.client.get(self.a.get_absolute_url())
        self.assertContains(antwort, self.adresse)
