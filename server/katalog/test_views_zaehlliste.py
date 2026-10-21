"""The counting list of a location in its two tabs and the location choice on the recipe article."""

from datetime import date
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from django_project.testing import AngemeldeteSeiten, inventur_anlegen, skript
from organisation.models import Bereich, Haus, Ort

from .forms import ZaehllisteFormSet
from .models import (
    Artikelstatus,
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
    waehlbare_artikel,
)


class ArtikelwahlAmOrt(AngemeldeteSeiten):
    """What is available at a location and what is not.

    A recipe article belongs only where it can be resolved: every recipe
    component needs an active house article in this house. Otherwise it
    would be available at every location and 'Erwartung.clean()' would
    reject it when saving.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.gruppe = Warengruppe.objects.create(name="Kino")
        cls.haus = Haus.objects.create(name="Kino Nord2")
        cls.fremd = Haus.objects.create(name="Kino Süd2")
        cls.ort = Ort.objects.create(
            name="Theke", bereich=Bereich.objects.create(name="Theke", haus=cls.haus)
        )

        cls.karton = cls.ware("Posten N", cls.haus, "930001")
        cls.nachos = cls.ware("Artikel N lose", cls.haus, "930002")
        # Carried only in the foreign house, not resolvable in Kino Nord2.
        cls.becher = cls.ware("Artikel M", cls.fremd, "930003")

        cls.portion = cls.rezeptur("Portion N", cls.karton, cls.nachos)
        cls.menue = cls.rezeptur("Menü M", cls.karton, cls.becher)
        cls.ohne = cls.rezeptur("Ohne Rezeptur")

    @classmethod
    def ware(cls, name, haus, nummer):
        sammel = Ware.objects.create(name=name, basiseinheit=cls.stueck)
        Hausartikel.objects.create(
            haus=haus,
            ware=sammel,
            warengruppe=cls.gruppe,
            artikelnummer=nummer,
            name=name,
        )
        return sammel

    @classmethod
    def rezeptur(cls, name, *waren):
        zaehl = Rezepturartikel.objects.create(name=name, basiseinheit=cls.stueck)
        for sammel in waren:
            Rezepturkomponente.objects.create(
                rezepturartikel=zaehl, ware=sammel, menge_in_basiseinheit=Decimal("1")
            )
        return zaehl

    def wahl(self):
        return set(waehlbare_artikel(self.ort).values_list("name", flat=True))

    def test_aufloesbarer_rezepturartikel_steht_zur_wahl(self):
        self.assertIn("Portion N", self.wahl())
        self.assertTrue(self.portion.verfuegbar_in(self.haus.pk))

    def test_rezepturartikel_mit_fehlender_zutat_steht_nicht_zur_wahl(self):
        self.assertNotIn("Menü M", self.wahl())
        self.assertFalse(self.menue.verfuegbar_in(self.haus.pk))

    def test_rezepturartikel_ohne_rezeptur_steht_nicht_zur_wahl(self):
        self.assertNotIn("Ohne Rezeptur", self.wahl())

    def test_wahl_deckt_sich_mit_aufloesbar_in(self):
        """The query and the model method must not drift apart."""
        gewaehlt = self.wahl()
        for zaehl in Rezepturartikel.objects.all():
            with self.subTest(rezepturartikel=zaehl.name):
                self.assertEqual(zaehl.name in gewaehlt, zaehl.aufloesbar_in(self.haus.pk))

    def test_aktive_ware_in_einem_anderen_haus_zaehlt_hier_nicht(self):
        """An 'exclude' over two conditions let that through: active there, discontinued here."""
        dip = self.ware("Artikel D", self.fremd, "930010")
        Hausartikel.objects.create(
            haus=self.haus,
            ware=dip,
            warengruppe=self.gruppe,
            artikelnummer="930011",
            name="Artikel D",
            status=Artikelstatus.EINGESTELLT,
        )
        self.rezeptur("Portion D", dip)
        self.assertNotIn("Portion D", self.wahl())

    def test_rezepturartikel_mit_inaktiver_zutat_steht_zur_wahl(self):
        """Like an inactive house article: seasonal goods have their place in the counting list."""
        Hausartikel.objects.get(artikelnummer="930002").deaktivieren()
        self.assertIn("Portion N", self.wahl())

    def test_fremder_hausartikel_steht_nicht_zur_wahl(self):
        self.assertNotIn("Artikel M", self.wahl())

    def test_inaktiver_hausartikel_steht_zur_wahl_eingestellter_nicht(self):
        """Seasonal goods have their place in the counting list, what is over does not."""
        artikel = Hausartikel.objects.get(artikelnummer="930002")
        artikel.deaktivieren()
        self.assertIn("Artikel N lose", self.wahl())
        artikel.einstellen()
        self.assertNotIn("Artikel N lose", self.wahl())

    def test_der_auswahlreiter_nennt_den_status(self):
        """A discontinued article is marked as such, not as "inactive" (until 10/2026)."""
        Hausartikel.objects.get(artikelnummer="930002").einstellen()
        antwort = self.client.get(
            reverse("zaehlliste", args=[self.ort.pk]),
            {"reiter": "auswahl", "status": "eingestellt"},
        )
        abzeichen = '<span class="badge bg-body-secondary text-body border ms-1">{}</span>'
        self.assertContains(antwort, abzeichen.format("eingestellt"), html=True)
        self.assertNotContains(antwort, abzeichen.format("inaktiv"), html=True)

    def test_was_schon_erwartet_wird_bleibt_sichtbar(self):
        """Otherwise the entry would silently vanish at the next save."""
        Erwartung.objects.create(ort=self.ort, artikel=self.portion)
        Hausartikel.objects.get(artikelnummer="930002").einstellen()

        self.assertFalse(self.portion.aufloesbar_in(self.haus.pk))
        self.assertIn("Portion N", self.wahl())

    def test_der_auswahlreiter_zeigt_nur_die_waehlbaren(self):
        antwort = self.client.get(reverse("zaehlliste", args=[self.ort.pk]), {"reiter": "auswahl"})
        waehlbar = antwort.context["waehlbar"]
        self.assertIn(self.portion.pk, waehlbar)
        self.assertNotIn(self.menue.pk, waehlbar)


class ZaehllisteInZweiReitern(AngemeldeteSeiten):
    """The counting list carries both: what is expected here and what can be added."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Ost6")
        cls.ort = Ort.objects.create(
            name="Theke", bereich=Bereich.objects.create(name="Theke", haus=cls.haus)
        )
        stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.getraenke = Warengruppe.objects.create(name="Warengruppe G6")
        cls.suesses = Warengruppe.objects.create(name="Warengruppe T6")

        cls.artikel = []
        for nummer, name, gruppe in (
            ("950001", "Artikel C 0,33", cls.getraenke),
            ("950002", "Artikel F 0,33", cls.getraenke),
            ("950003", "Ware S", cls.suesses),
        ):
            ware = Ware.objects.create(name=name, basiseinheit=stueck)
            cls.artikel.append(
                Hausartikel.objects.create(
                    haus=cls.haus,
                    ware=ware,
                    warengruppe=gruppe,
                    artikelnummer=nummer,
                    name=name,
                )
            )
        cls.portion = Rezepturartikel.objects.create(name="Portion N", basiseinheit=stueck)
        Rezepturkomponente.objects.create(
            rezepturartikel=cls.portion,
            ware=cls.artikel[0].ware,
            menge_in_basiseinheit=Decimal("1"),
        )
        cls.liste = reverse("zaehlliste", args=[cls.ort.pk])

    def erwartete_namen(self):
        return sorted(
            Erwartung.objects.filter(ort=self.ort).values_list("artikel__name", flat=True)
        )

    # -- The location itself ------------------------------------------------

    def test_ort_bearbeiten_hat_keine_artikelauswahl_mehr(self):
        antwort = self.client.get(reverse("ort_edit", args=[self.ort.pk]))
        self.assertNotIn("artikel_form", antwort.context)

    # -- Tab "Select articles" ----------------------------------------------

    def test_auswahlreiter_zeigt_die_artikel_des_hauses(self):
        antwort = self.client.get(self.liste, {"reiter": "auswahl"})
        self.assertEqual(antwort.context["reiter"], "auswahl")
        self.assertEqual(
            sorted(a.name for a in antwort.context["auswahl_hausartikel"]),
            ["Artikel C 0,33", "Artikel F 0,33", "Ware S"],
        )
        self.assertEqual(
            [a.name for a in antwort.context["auswahl_rezepturartikel"]], ["Portion N"]
        )

    def test_die_filter_greifen(self):
        antwort = self.client.get(self.liste, {"reiter": "auswahl", "warengruppe": self.suesses.pk})
        self.assertEqual([a.name for a in antwort.context["auswahl_hausartikel"]], ["Ware S"])

    def test_aufnehmen_und_wieder_herausnehmen(self):
        ziel = f"{self.liste}?reiter=auswahl"
        antwort = self.client.post(
            reverse("erwartung_umschalten", args=[self.ort.pk]),
            {"artikel": self.artikel[0].pk, "aktion": "aufnehmen", "weiter": ziel},
        )
        self.assertRedirects(antwort, ziel)
        self.assertEqual(self.erwartete_namen(), ["Artikel C 0,33"])

        self.client.post(
            reverse("erwartung_umschalten", args=[self.ort.pk]),
            {"artikel": self.artikel[0].pk, "aktion": "entfernen", "weiter": ziel},
        )
        self.assertEqual(self.erwartete_namen(), [])

    def umschalten(self, aktion, artikel=None):
        return self.client.post(
            reverse("erwartung_umschalten", args=[self.ort.pk]),
            {"artikel": (artikel or self.artikel[0]).pk, "aktion": aktion},
            follow=True,
        )

    def test_zweimal_hinzufuegen_nimmt_nichts_wieder_heraus(self):
        """A second tab, or a send repeated after the network failed. Until 10/2026 it toggled."""
        karton = Gebinde.objects.create(
            ware=self.artikel[0].ware, name="Karton", faktor_in_basiseinheit=Decimal("24")
        )
        self.umschalten("aufnehmen")
        ErwartungGebinde.objects.filter(gebinde=karton).update(standard_menge=Decimal("4"))

        antwort = self.umschalten("aufnehmen")

        self.assertContains(antwort, "„Artikel C 0,33“ steht schon in der Zählliste von Theke.")
        vorgabe = ErwartungGebinde.objects.get(erwartung__ort=self.ort, gebinde=karton)
        self.assertEqual(vorgabe.standard_menge, Decimal("4"))

    def test_zweimal_entfernen_legt_nichts_wieder_an(self):
        Erwartung.objects.create(ort=self.ort, artikel=self.artikel[0])
        self.umschalten("entfernen")

        antwort = self.umschalten("entfernen")

        self.assertContains(
            antwort, "„Artikel C 0,33“ steht nicht mehr in der Zählliste von Theke."
        )
        self.assertEqual(self.erwartete_namen(), [])

    def test_ohne_aktion_geschieht_nichts(self):
        """A page from before the buttons said what they want. Guessing could undo a click."""
        Erwartung.objects.create(ort=self.ort, artikel=self.artikel[0])
        for aktion in ("", "umschalten"):
            with self.subTest(aktion=aktion):
                antwort = self.umschalten(aktion)
                self.assertContains(antwort, "Unbekannte Aktion.")
                self.assertEqual(self.erwartete_namen(), ["Artikel C 0,33"])

    def test_die_knoepfe_sagen_was_sie_wollen(self):
        Erwartung.objects.create(ort=self.ort, artikel=self.artikel[0])
        antwort = self.client.get(self.liste, {"reiter": "auswahl"})
        self.assertContains(antwort, 'name="aktion" value="entfernen"', count=1)
        # The other two house articles and the recipe article.
        self.assertContains(antwort, 'name="aktion" value="aufnehmen"', count=3)

    def test_neue_erwartungen_haengen_sich_hinten_an(self):
        for artikel in self.artikel[:2]:
            self.umschalten("aufnehmen", artikel)
        self.assertEqual(
            list(
                Erwartung.objects.filter(ort=self.ort)
                .order_by("sortierung")
                .values_list("artikel__name", flat=True)
            ),
            ["Artikel C 0,33", "Artikel F 0,33"],
        )

    def test_nicht_aufloesbarer_rezepturartikel_wird_abgewiesen(self):
        fremd = Rezepturartikel.objects.create(
            name="Fremdmenü", basiseinheit=Basiseinheit.objects.get(dimension=Dimension.STUECK)
        )
        andere = Ware.objects.create(
            name="Nur woanders", basiseinheit=Basiseinheit.objects.get(name="kg")
        )
        Rezepturkomponente.objects.create(
            rezepturartikel=fremd, ware=andere, menge_in_basiseinheit=Decimal("1")
        )
        self.umschalten("aufnehmen", fremd)
        self.assertEqual(self.erwartete_namen(), [])

    def test_unsinnige_nummer_legt_nichts_an(self):
        self.client.post(
            reverse("erwartung_umschalten", args=[self.ort.pk]),
            {"artikel": "xyz", "aktion": "aufnehmen"},
        )
        self.assertEqual(self.erwartete_namen(), [])

    def test_fremdes_ziel_wird_verworfen(self):
        antwort = self.client.post(
            reverse("erwartung_umschalten", args=[self.ort.pk]),
            {
                "artikel": self.artikel[0].pk,
                "aktion": "aufnehmen",
                "weiter": "https://example.com/phish",
            },
        )
        self.assertRedirects(antwort, self.liste)

    # -- Tab "Counting list" ------------------------------------------------

    def _zeilen(self, **abweichend):
        erwartungen = list(Erwartung.objects.filter(ort=self.ort).order_by("sortierung"))
        daten = {
            "zeilen-TOTAL_FORMS": str(len(erwartungen)),
            "zeilen-INITIAL_FORMS": str(len(erwartungen)),
            "zeilen-MIN_NUM_FORMS": "0",
            "zeilen-MAX_NUM_FORMS": "1000",
            "reihenfolge": [str(e.pk) for e in erwartungen],
        }
        for stelle, erwartung in enumerate(erwartungen):
            daten[f"zeilen-{stelle}-erwartung"] = erwartung.pk
        return daten | abweichend

    def test_aktualisieren_sagt_was_weggefallen_ist(self):
        """Rows removed elsewhere took their typed values with them, silently (until 10/2026)."""
        quelle = skript("soft_reload")
        self.assertIn("TEXT.weggefallen", quelle)
        self.assertIn('sessionStorage.setItem("abgemeldet"', quelle)
        antwort = self.client.get(self.liste)
        self.assertContains(antwort, "data-weggefallen=")
        self.assertContains(antwort, "{anzahl} Zeilen sind inzwischen weg")

    def test_eine_inzwischen_entfernte_zeile_wird_gemeldet_statt_leer_gerendert(self):
        """A second window removed the expectation. Until 10/2026 the page showed an empty row."""
        for artikel in self.artikel[:2]:
            Erwartung.objects.create(ort=self.ort, artikel=artikel)
        daten = self._zeilen()
        Erwartung.objects.filter(ort=self.ort, artikel=self.artikel[1]).delete()

        antwort = self.client.post(self.liste, daten)

        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "inzwischen entfernt")
        self.assertNotContains(antwort, 'data-kennung=""')

    def test_das_kreuz_nimmt_eine_zeile_heraus(self):
        for artikel in self.artikel[:2]:
            Erwartung.objects.create(ort=self.ort, artikel=artikel)
        raus = Erwartung.objects.get(ort=self.ort, artikel=self.artikel[1])

        antwort = self.client.post(self.liste, self._zeilen(entfernen=str(raus.pk)))
        self.assertRedirects(antwort, self.liste)
        self.assertEqual(self.erwartete_namen(), ["Artikel C 0,33"])

    def test_das_kreuf_laesst_den_artikel_selbst_stehen(self):
        Erwartung.objects.create(ort=self.ort, artikel=self.artikel[0])
        raus = Erwartung.objects.get(ort=self.ort)
        self.client.post(self.liste, self._zeilen(entfernen=str(raus.pk)))
        self.assertTrue(Hausartikel.objects.filter(pk=self.artikel[0].pk).exists())

    def test_fremde_erwartung_wird_nicht_entfernt(self):
        """The number comes from the form. It has to belong to this location."""
        anderer = Ort.objects.create(
            name="Lager", bereich=Bereich.objects.create(name="Lager", haus=self.haus)
        )
        fremd = Erwartung.objects.create(ort=anderer, artikel=self.artikel[0])
        self.client.post(self.liste, self._zeilen(entfernen=str(fremd.pk)))
        self.assertTrue(Erwartung.objects.filter(pk=fremd.pk).exists())

    def test_beide_reiter_rendern(self):
        Erwartung.objects.create(ort=self.ort, artikel=self.artikel[0])
        for parameter in ({}, {"reiter": "auswahl"}):
            with self.subTest(parameter=parameter):
                self.assertEqual(self.client.get(self.liste, parameter).status_code, 200)

    # -- Order: dragging, buttons, removing ---------------------------------

    def drei_erwartete(self):
        return [Erwartung.objects.create(ort=self.ort, artikel=a) for a in self.artikel]

    def folge(self):
        return list(
            Erwartung.objects.filter(ort=self.ort)
            .order_by("sortierung")
            .values_list("artikel__name", flat=True)
        )

    def test_entfernen_behaelt_die_gezogene_reihenfolge_der_uebrigen(self):
        """The ✕ used to only delete and threw away everything dragged before."""
        cola, fassbrause, schokoriegel = self.drei_erwartete()
        gezogen = [str(schokoriegel.pk), str(cola.pk), str(fassbrause.pk)]
        self.client.post(self.liste, self._zeilen(reihenfolge=gezogen, entfernen=str(cola.pk)))
        self.assertEqual(self.folge(), ["Ware S", "Artikel F 0,33"])

    def test_pfeil_wirkt_auf_die_gezogene_reihenfolge(self):
        """Whoever first drags and then presses an arrow means both."""
        cola, fassbrause, schokoriegel = self.drei_erwartete()
        gezogen = [str(schokoriegel.pk), str(cola.pk), str(fassbrause.pk)]
        antwort = self.client.post(
            self.liste, self._zeilen(reihenfolge=gezogen, runter=str(schokoriegel.pk))
        )
        self.assertRedirects(antwort, self.liste)
        self.assertEqual(self.folge(), ["Artikel C 0,33", "Ware S", "Artikel F 0,33"])

    def test_ganz_nach_oben_und_unten(self):
        cola, fassbrause, schokoriegel = self.drei_erwartete()
        self.client.post(self.liste, self._zeilen(ganz_hoch=str(schokoriegel.pk)))
        self.assertEqual(self.folge(), ["Ware S", "Artikel C 0,33", "Artikel F 0,33"])
        self.client.post(self.liste, self._zeilen(ganz_runter=str(schokoriegel.pk)))
        self.assertEqual(self.folge(), ["Artikel C 0,33", "Artikel F 0,33", "Ware S"])

    def test_sprung_bleibt_in_der_liste(self):
        """Even if it moves nothing: the first one all the way up is already at the top."""
        cola, _, _ = self.drei_erwartete()
        antwort = self.client.post(self.liste, self._zeilen(ganz_hoch=str(cola.pk)))
        self.assertRedirects(antwort, self.liste)

    def test_nach_dem_loslassen_wird_im_hintergrund_gesichert(self):
        """A crashing tab should cost nothing more of what was already dragged."""
        cola, fassbrause, schokoriegel = self.drei_erwartete()
        gezogen = [str(fassbrause.pk), str(schokoriegel.pk), str(cola.pk)]
        antwort = self.client.post(
            self.liste, self._zeilen(reihenfolge=gezogen), headers={"X-Requested-With": "fetch"}
        )
        self.assertEqual(antwort.status_code, 204)
        self.assertEqual(self.folge(), ["Artikel F 0,33", "Ware S", "Artikel C 0,33"])

    def test_zeilenknopf_im_hintergrund_bekommt_die_liste_zurueck(self):
        """The script puts the fresh list in place of the old one. A 204 would leave it nothing."""
        cola, fassbrause, schokoriegel = self.drei_erwartete()
        kopf = {"X-Requested-With": "fetch"}

        antwort = self.client.post(
            self.liste, self._zeilen(runter=str(cola.pk)), headers=kopf, follow=True
        )
        self.assertRedirects(antwort, self.liste)
        self.assertEqual(self.folge(), ["Artikel F 0,33", "Artikel C 0,33", "Ware S"])

        antwort = self.client.post(
            self.liste, self._zeilen(entfernen=str(cola.pk)), headers=kopf, follow=True
        )
        self.assertRedirects(antwort, self.liste)
        self.assertEqual(self.folge(), ["Artikel F 0,33", "Ware S"])
        self.assertContains(antwort, "Aus der Zählliste genommen")
        self.assertContains(antwort, 'data-ersetzen="anzahl">2<')

    def test_zeilenknoepfe_wirken_ohne_seitenneuladen(self):
        """Without the marks the script would leave the form alone, and the page would jump."""
        self.drei_erwartete()
        html = self.client.get(self.liste).content.decode()
        for stueck in ("data-liste", "data-sofort", "data-seitenwechsel", "js/instant_actions.js"):
            self.assertIn(stueck, html)

    def test_halb_getippte_menge_haelt_das_sichern_der_reihenfolge_nicht_auf(self):
        """The message for it comes on the actual save, the order not only then."""
        cola, fassbrause, schokoriegel = self.drei_erwartete()
        kasten = Gebinde.objects.create(
            ware=self.artikel[0].ware,
            name="Kasten",
            faktor_in_basiseinheit=Decimal("24"),
        )
        daten = self._zeilen(reihenfolge=[str(fassbrause.pk), str(cola.pk), str(schokoriegel.pk)])
        # Row 0 is Artikel C. Its quantity is no number, so the formset is invalid.
        daten |= {f"zeilen-0-gebinde_{kasten.pk}": "on", f"zeilen-0-menge_{kasten.pk}": "abc"}
        antwort = self.client.post(self.liste, daten, headers={"X-Requested-With": "fetch"})
        self.assertEqual(antwort.status_code, 204)
        self.assertEqual(self.folge(), ["Artikel F 0,33", "Artikel C 0,33", "Ware S"])
        self.assertFalse(ErwartungGebinde.objects.exists())

    # -- All rows at once ----------------------------------------------------

    def test_ein_menue_wirkt_auf_alle_zeilen(self):
        """Six entries: every pack, the loose single unit and the scale, to tick and to untick."""
        self.drei_erwartete()
        Gebinde.objects.create(
            ware=self.artikel[0].ware, name="Kasten", faktor_in_basiseinheit=Decimal("24")
        )
        html = self.client.get(self.liste).content.decode()
        for stueck in (
            "data-alle-menue",
            'data-alle="gebinde" data-richtung="an"',
            'data-alle="gebinde" data-richtung="ab"',
            'data-alle="einzeln" data-richtung="an"',
            'data-alle="einzeln" data-richtung="ab"',
            'data-alle="wiegen" data-richtung="an"',
            'data-alle="wiegen" data-richtung="ab"',
            "Alle Gebinde anwählen",
            "Alle Gebinde abwählen",
            "Überall „Einzeln“ anwählen",
            "Überall Wiegen abwählen",
            "js/zaehlliste.js",
        ):
            with self.subTest(stueck=stueck):
                self.assertIn(stueck, html)
        for stueck in ("[data-alle]", "[data-einzeln]", "[data-wiegen]", "[data-alle-menue]"):
            with self.subTest(stueck=stueck):
                self.assertIn(stueck, skript("zaehlliste"))

    def test_das_einzelstueck_und_die_waage_sind_markiert(self):
        """The script finds them by their marks, not by their names: those carry the row number."""
        erwartung, karton = self._abwiegbar_erwartet()
        Gebinde.objects.create(
            ware=karton.ware, name="Einzeln", faktor_in_basiseinheit=Decimal("1"), ist_einzeln=True
        )
        html = self.client.get(self.liste).content.decode()
        self.assertEqual(html.count(" data-einzeln>"), 1)
        self.assertIn('name="zeilen-0-wiegen"', html)
        self.assertIn("data-wiegen", html)

    def test_abwaehlen_fragt_nur_wo_mengen_verloren_gingen(self):
        """The question stands ready in the page. The script arms it when a quantity stands."""
        self.drei_erwartete()
        html = self.client.get(self.liste).content.decode()
        self.assertIn("Die eingetragenen Standardmengen gehen dabei verloren.", html)
        self.assertNotIn("data-rueckfrage", html)
        self.assertIn("eintrag.dataset.rueckfrage = eintrag.dataset.frage", skript("zaehlliste"))

    def test_das_menue_gehoert_zur_liste_und_kommt_mit_ihr_zurueck(self):
        """A row button replaces the list. What stands outside of it would go stale."""
        self.drei_erwartete()
        html = self.client.get(self.liste).content.decode()
        self.assertLess(html.index("data-liste"), html.index("data-alle-menue"))

    # -- Row buttons despite a row that does not hold up ---------------------

    def test_das_kreuz_wirkt_auch_wenn_eine_zeile_nicht_haelt(self):
        """Freshly taken in, a weighable product stands without pack and scale.

        Until 10/2026 that row blocked the ✕ of every other row: the page came
        back with the error, and nobody saw it in a long list.
        """
        erwartung, karton = self._abwiegbar_erwartet()
        erwartung.wiegen_erwartet = False
        erwartung.save(update_fields=["wiegen_erwartet"])
        raus = Erwartung.objects.create(ort=self.ort, artikel=self.artikel[1])

        antwort = self.client.post(self.liste, self._zeilen(entfernen=str(raus.pk)), follow=True)
        self.assertRedirects(antwort, self.liste)
        self.assertEqual(self.erwartete_namen(), ["Artikel C 0,33"])
        # The untouched row is not reported: nothing of the person's was lost.
        self.assertNotContains(antwort, "wurde nicht gespeichert")

    def test_der_pfeil_wirkt_auch_wenn_eine_zeile_nicht_haelt(self):
        erwartung, karton = self._abwiegbar_erwartet()
        erwartung.wiegen_erwartet = False
        erwartung.save(update_fields=["wiegen_erwartet"])
        zweite = Erwartung.objects.create(ort=self.ort, artikel=self.artikel[1])

        self.client.post(self.liste, self._zeilen(hoch=str(zweite.pk)))
        self.assertEqual(self.folge(), ["Artikel F 0,33", "Artikel C 0,33"])

    def test_die_gueltigen_zeilen_werden_dabei_gespeichert(self):
        erwartung, karton = self._abwiegbar_erwartet()
        erwartung.wiegen_erwartet = False
        erwartung.save(update_fields=["wiegen_erwartet"])
        zweite = Erwartung.objects.create(ort=self.ort, artikel=self.artikel[1])
        kasten = Gebinde.objects.create(
            ware=self.artikel[1].ware, name="Kasten", faktor_in_basiseinheit=Decimal("6")
        )

        daten = self._zeilen(ganz_hoch=str(zweite.pk))
        daten |= {f"zeilen-1-gebinde_{kasten.pk}": "on", f"zeilen-1-menge_{kasten.pk}": "3"}
        self.client.post(self.liste, daten)
        vorgabe = ErwartungGebinde.objects.get(erwartung=zweite)
        self.assertEqual((vorgabe.gebinde_id, vorgabe.standard_menge), (kasten.pk, Decimal("3")))

    def test_eine_geaenderte_zeile_die_nicht_haelt_wird_gemeldet(self):
        """The ✕ does its work, and the person hears what did not arrive."""
        erwartung, karton = self._abwiegbar_erwartet()
        raus = Erwartung.objects.create(ort=self.ort, artikel=self.artikel[1])

        # Row 0 is the weighable article, stored with the scale. Unticking it
        # without a pack does not hold up.
        daten = self._zeilen(entfernen=str(raus.pk))
        antwort = self.client.post(self.liste, daten, follow=True)
        self.assertEqual(self.erwartete_namen(), ["Artikel C 0,33"])
        self.assertContains(antwort, "„Artikel C 0,33“ wurde nicht gespeichert")
        self.assertContains(antwort, "Ohne Gebinde und ohne Wiegen")
        erwartung.refresh_from_db()
        self.assertTrue(erwartung.wiegen_erwartet)

    def test_speichern_selbst_besteht_auf_einer_gueltigen_liste(self):
        """Only the row buttons go past an invalid row. "Save" shows the errors."""
        erwartung, _ = self._abwiegbar_erwartet()
        antwort = self.client.post(self.liste, self._zeilen())
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Ohne Gebinde und ohne Wiegen")

    # -- Saving before the other tab opens ----------------------------------

    def test_speichern_folgt_weiter(self):
        """The script sends the other tab along when ticks are unsaved."""
        self.drei_erwartete()
        auswahl = f"{self.liste}?reiter=auswahl"
        antwort = self.client.post(self.liste, self._zeilen(weiter=auswahl))
        self.assertRedirects(antwort, auswahl)

    def test_der_reiterwechsel_kommt_an_einer_zeile_vorbei_die_nicht_haelt(self):
        """Like a row button: an untouched invalid row must not block the other tab."""
        erwartung, karton = self._abwiegbar_erwartet()
        erwartung.wiegen_erwartet = False
        erwartung.save(update_fields=["wiegen_erwartet"])
        zweite = Erwartung.objects.create(ort=self.ort, artikel=self.artikel[1])
        kasten = Gebinde.objects.create(
            ware=self.artikel[1].ware, name="Kasten", faktor_in_basiseinheit=Decimal("6")
        )
        auswahl = f"{self.liste}?reiter=auswahl"
        daten = self._zeilen(weiter=auswahl) | {f"zeilen-1-gebinde_{kasten.pk}": "on"}
        antwort = self.client.post(self.liste, daten, follow=True)
        self.assertRedirects(antwort, auswahl)
        self.assertTrue(ErwartungGebinde.objects.filter(erwartung=zweite, gebinde=kasten).exists())
        self.assertContains(antwort, "gespeichert")
        self.assertNotContains(antwort, "wurde nicht gespeichert")

    def test_ein_fremdes_weiter_fuehrt_zum_ort(self):
        self.drei_erwartete()
        antwort = self.client.post(self.liste, self._zeilen(weiter="https://example.org/"))
        self.assertRedirects(antwort, self.ort.get_absolute_url())

    def test_der_reiter_traegt_die_marke_und_das_formular_das_feld(self):
        self.drei_erwartete()
        html = self.client.get(self.liste).content.decode()
        self.assertIn('?reiter=auswahl" data-vorher-speichern', html)
        self.assertIn('name="weiter" value=""', html)
        for stueck in ("a[data-vorher-speichern]", "input[name=weiter]", "requestSubmit"):
            with self.subTest(stueck=stueck):
                self.assertIn(stueck, skript("zaehlliste"))

    # -- Refresh without losing what is typed --------------------------------

    def test_aktualisieren_holt_die_liste_und_behaelt_die_eingaben(self):
        """The button, the script and the row identity the script keeps a tick with."""
        cola, _, _ = self.drei_erwartete()
        html = self.client.get(self.liste).content.decode()
        for stueck in ("data-neu-laden", "js/soft_reload.js", f'data-kennung="{cola.pk}"'):
            with self.subTest(stueck=stueck):
                self.assertIn(stueck, html)
        for stueck in ("[data-neu-laden]", "[data-kennung]", "liste:ersetzt"):
            with self.subTest(stueck=stueck):
                self.assertIn(stueck, skript("soft_reload"))

    def test_der_fokus_kehrt_nach_dem_umschalten_zurueck(self):
        """Keyboard users press the same button again. Until 10/2026 it was gone after the swap."""
        html = self.client.get(self.liste, {"reiter": "auswahl"}).content.decode()
        self.assertIn(f'data-fokus="erwartung-{self.artikel[0].pk}"', html)
        quelle = skript("zaehlliste")
        # After an entry of the "all rows" menu the button that opened it takes the focus.
        self.assertIn('querySelector("[data-bs-toggle]")', quelle)
        self.assertIn("focus({ preventScroll: true })", quelle)

    def test_der_weg_zu_den_gebinden_springt_zum_abschnitt(self):
        """The note "no active packs" leads to the pack section of the edit page, not to the top."""
        Erwartung.objects.create(ort=self.ort, artikel=self.artikel[0])
        html = self.client.get(self.liste).content.decode()
        self.assertIn(reverse("hausartikel_edit", args=[self.artikel[0].pk]) + "#gebinde", html)
        bearbeiten = self.client.get(reverse("hausartikel_edit", args=[self.artikel[0].pk]))
        self.assertContains(bearbeiten, 'id="gebinde"')

    # -- Weighing per location ----------------------------------------------

    def _abwiegbar_erwartet(self):
        ware = self.artikel[0].ware
        ware.abwiegbar = True
        ware.save(update_fields=["abwiegbar"])
        karton = Gebinde.objects.create(
            ware=ware, name="Karton", faktor_in_basiseinheit=Decimal("24")
        )
        erwartung = Erwartung.objects.create(
            ort=self.ort, artikel=self.artikel[0], wiegen_erwartet=True
        )
        return erwartung, karton

    def test_der_haken_wiegen_steht_nur_bei_abwiegbarer_ware(self):
        self._abwiegbar_erwartet()
        Erwartung.objects.create(ort=self.ort, artikel=self.artikel[1])
        antwort = self.client.get(self.liste)
        self.assertContains(antwort, 'name="zeilen-0-wiegen"')
        self.assertNotContains(antwort, 'name="zeilen-1-wiegen"')

    def test_wiegen_laesst_sich_je_ort_abwaehlen(self):
        erwartung, karton = self._abwiegbar_erwartet()
        self.assertTrue(erwartung.wiegen_erwartet)

        daten = self._zeilen(**{f"zeilen-0-gebinde_{karton.pk}": "on"})
        self.assertEqual(self.client.post(self.liste, daten).status_code, 302)
        erwartung.refresh_from_db()
        self.assertFalse(erwartung.wiegen_erwartet)

        daten |= {"zeilen-0-wiegen": "on"}
        self.assertEqual(self.client.post(self.liste, daten).status_code, 302)
        erwartung.refresh_from_db()
        self.assertTrue(erwartung.wiegen_erwartet)

    def test_weder_gebinde_noch_wiegen_wird_abgewiesen(self):
        """The article would be in the counting list and get no row in the inventory."""
        erwartung, _ = self._abwiegbar_erwartet()
        antwort = self.client.post(self.liste, self._zeilen())
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Ohne Gebinde und ohne Wiegen")
        erwartung.refresh_from_db()
        self.assertTrue(erwartung.wiegen_erwartet)

    def _aufnehmen(self, artikel) -> Erwartung:
        self.umschalten("aufnehmen", artikel)
        return Erwartung.objects.get(ort=self.ort, artikel=artikel)

    def test_neu_aufgenommen_wird_nicht_gewogen(self):
        """Most locations hold boxes. The tick for the scale is set by whoever means it."""
        ware = self.artikel[0].ware
        ware.abwiegbar = True
        ware.save(update_fields=["abwiegbar"])
        Gebinde.objects.create(ware=ware, name="Karton", faktor_in_basiseinheit=Decimal("24"))

        self.assertFalse(self._aufnehmen(self.artikel[0]).wiegen_erwartet)
        antwort = self.client.get(self.liste)
        self.assertContains(antwort, 'name="zeilen-0-wiegen"')
        self.assertFalse(antwort.context["formset"].forms[0].wiegen_haken.value())

    def test_wo_es_nur_die_waage_gibt_bleibt_sie_vorbelegt(self):
        """Without a pack and without the scale the article would get no row at all."""
        ware = self.artikel[0].ware
        ware.abwiegbar = True
        ware.save(update_fields=["abwiegbar"])
        # An inactive pack is not available when counting. It does not count.
        Gebinde.objects.create(
            ware=ware, name="Karton", faktor_in_basiseinheit=Decimal("24"), aktiv=False
        )

        self.assertTrue(self._aufnehmen(self.artikel[0]).wiegen_erwartet)

    def test_nicht_abwiegbare_ware_wird_ohne_waage_aufgenommen(self):
        self.assertFalse(self._aufnehmen(self.artikel[1]).wiegen_erwartet)

    def test_hinzufuegen_hakt_die_aktiven_gebinde_an(self):
        """Without one the article would get no row here. Until 10/2026 it came without."""
        ware = self.artikel[1].ware
        for stelle, (name, menge, aktiv) in enumerate(
            (("Karton", "24", True), ("Kiste", "12", True), ("Tray", "6", False)), start=1
        ):
            Gebinde.objects.create(
                ware=ware,
                name=name,
                faktor_in_basiseinheit=Decimal(menge),
                sortierung=stelle,
                aktiv=aktiv,
            )

        erwartung = self._aufnehmen(self.artikel[1])

        vorgaben = erwartung.gebinde_vorgaben.order_by("gebinde__sortierung")
        self.assertEqual(
            list(vorgaben.values_list("gebinde__name", "standard_menge")),
            [("Karton", None), ("Kiste", None)],
        )
        # Untouched, the list holds up: "Save" goes through as it stands.
        daten = self._zeilen(
            **{f"zeilen-0-gebinde_{vorgabe.gebinde_id}": "on" for vorgabe in vorgaben}
        )
        self.assertEqual(self.client.post(self.liste, daten).status_code, 302)

    def test_ohne_gebinde_wird_auch_nicht_abwiegbare_ware_abgewiesen(self):
        """The article would be in the counting list and get no row. Until 10/2026 it was taken."""
        karton = Gebinde.objects.create(
            ware=self.artikel[1].ware, name="Karton", faktor_in_basiseinheit=Decimal("24")
        )
        erwartung = Erwartung.objects.create(ort=self.ort, artikel=self.artikel[1])
        ErwartungGebinde.objects.create(erwartung=erwartung, gebinde=karton)

        # Every pack unticked, as the menu "Untick all packs" leaves it.
        antwort = self.client.post(self.liste, self._zeilen())

        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(
            antwort.context["formset"].forms[0].non_field_errors(),
            ["Ohne Gebinde bekäme der Artikel hier keine Zeile."],
        )
        self.assertContains(antwort, "Ohne Gebinde bekäme der Artikel hier keine Zeile.")
        self.assertTrue(ErwartungGebinde.objects.filter(erwartung=erwartung).exists())

    def test_inaktives_gebinde_steht_als_text_da_und_bekommt_keine_zeile(self):
        """Decided 2026-10-18. The default stays for the case the pack comes back."""
        ware = self.artikel[1].ware
        karton, kiste = (
            Gebinde.objects.create(
                ware=ware, name=name, faktor_in_basiseinheit=Decimal(menge), sortierung=stelle
            )
            for stelle, (name, menge) in enumerate((("Karton", "24"), ("Kiste", "12")), start=1)
        )
        erwartung = Erwartung.objects.create(ort=self.ort, artikel=self.artikel[1])
        ErwartungGebinde.objects.create(erwartung=erwartung, gebinde=karton)
        ErwartungGebinde.objects.create(
            erwartung=erwartung, gebinde=kiste, standard_menge=Decimal("4")
        )

        self.client.post(reverse("gebinde_umschalten", args=[kiste.pk]), {"aktiv": "0"})

        antwort = self.client.get(self.liste)
        zeile = antwort.context["formset"].forms[0]
        self.assertEqual([vorgabe.gebinde for vorgabe in zeile.inaktive_vorgaben], [kiste])
        self.assertNotIn(f"gebinde_{kiste.pk}", zeile.fields)
        self.assertContains(antwort, "Kiste · 4")
        # Saving the list leaves it alone.
        daten = self._zeilen(**{f"zeilen-0-gebinde_{karton.pk}": "on"})
        self.assertEqual(self.client.post(self.liste, daten).status_code, 302)
        vorgabe = ErwartungGebinde.objects.get(erwartung=erwartung, gebinde=kiste)
        self.assertEqual(vorgabe.standard_menge, Decimal("4"))
        # The next inventory gives it no row.
        inventur = inventur_anlegen(self.haus, stichtag=date(2026, 1, 31))
        inventur.zaehlpositionen_anlegen()
        self.assertEqual(
            list(inventur.zaehlpositionen.values_list("gebinde__name", flat=True)), ["Karton"]
        )

    def test_ohne_gebinde_zur_wahl_bleibt_die_zeile_unbeanstandet(self):
        """A product without an active pack has nothing to tick. Its note says what is missing."""
        Gebinde.objects.create(
            ware=self.artikel[1].ware,
            name="Karton",
            faktor_in_basiseinheit=Decimal("24"),
            aktiv=False,
        )
        Erwartung.objects.create(ort=self.ort, artikel=self.artikel[1])
        Erwartung.objects.create(ort=self.ort, artikel=self.portion)

        self.assertEqual(self.client.post(self.liste, self._zeilen()).status_code, 302)

    def test_rezepturartikel_wird_ohne_waage_aufgenommen(self):
        """For it the field says nothing, whether it is weighed or not."""
        self.assertFalse(self._aufnehmen(self.portion).wiegen_erwartet)

    def test_nicht_abwiegbare_ware_behaelt_ihren_wert(self):
        """There the field says nothing. Saving the list should not flip it."""
        erwartung = Erwartung.objects.create(
            ort=self.ort, artikel=self.artikel[1], wiegen_erwartet=True
        )
        self.assertEqual(self.client.post(self.liste, self._zeilen()).status_code, 302)
        erwartung.refresh_from_db()
        self.assertTrue(erwartung.wiegen_erwartet)

    def test_zeilen_finden_ihre_erwartung_auch_nach_dem_umsortieren(self):
        """The form dates from the time before saving in the background.

        Its rows are numbered by the old order, the database already knows the
        new one. The packs still have to land on the right product: matching
        goes by the field sent along, not by the position.
        """
        cola, fassbrause, schokoriegel = self.drei_erwartete()
        kasten = Gebinde.objects.create(
            ware=self.artikel[0].ware,
            name="Kasten",
            faktor_in_basiseinheit=Decimal("24"),
        )
        daten_vorher = self._zeilen()  # Row 0 = Artikel C, that is how the page was loaded

        Erwartung.neu_sortieren(self.ort.pk, [schokoriegel.pk, fassbrause.pk, cola.pk])

        daten_vorher |= {f"zeilen-0-gebinde_{kasten.pk}": "on"}
        antwort = self.client.post(self.liste, daten_vorher)
        self.assertEqual(antwort.status_code, 302)
        self.assertEqual(
            list(ErwartungGebinde.objects.values_list("erwartung__artikel__name", flat=True)),
            ["Artikel C 0,33"],
        )


class OrtswahlAmRezepturartikel(AngemeldeteSeiten):
    """As with the house article, only across all houses, because a recipe article belongs to none.

    Where a recipe component is missing it cannot be resolved and therefore
    not entered either. An already entered location stays removable.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        gruppe = Warengruppe.objects.create(name="Kino16")
        cls.kino_nord = Haus.objects.create(name="Kino Nord16")
        cls.kino_sued = Haus.objects.create(name="Kino Süd16")
        cls.vorne = Ort.objects.create(
            name="Thekenfront", bereich=Bereich.objects.create(name="Theke", haus=cls.kino_nord)
        )
        cls.drueben = Ort.objects.create(
            name="Thekenfront", bereich=Bereich.objects.create(name="Theke", haus=cls.kino_sued)
        )

        karton = Ware.objects.create(name="Posten N16", basiseinheit=stueck)
        becher = Ware.objects.create(name="Artikel M16", basiseinheit=stueck)
        for nummer, (haus, ware) in enumerate(
            ((cls.kino_nord, karton), (cls.kino_nord, becher), (cls.kino_sued, karton)), start=1
        ):
            Hausartikel.objects.create(
                haus=haus,
                ware=ware,
                warengruppe=gruppe,
                artikelnummer=f"96000{nummer}",
                name=ware.name,
            )

        cls.menue = Rezepturartikel.objects.create(name="Menü M16", basiseinheit=stueck)
        for ware in (karton, becher):
            Rezepturkomponente.objects.create(
                rezepturartikel=cls.menue, ware=ware, menge_in_basiseinheit=Decimal("1")
            )

    def orte(self):
        return sorted(
            f"{erwartung.ort.haus.name}/{erwartung.ort.name}"
            for erwartung in Erwartung.objects.filter(artikel=self.menue.artikel_ptr)
        )

    def test_seite_zeigt_alle_haeuser_mit_aufloesbarkeit_und_orten(self):
        antwort = self.client.get(self.menue.get_absolute_url())
        ortswahl = antwort.context["ortswahl"]
        self.assertEqual(
            {haus.name: stufe for haus, stufe, _ in ortswahl},
            {"Kino Nord16": "ja", "Kino Süd16": "fehlt"},
            "Auflösbar ist das Menü nur, wo jede Zutat geführt wird.",
        )
        self.assertEqual(
            {
                haus.name: [ort.name for _, orte in bereiche for ort, _ in orte]
                for haus, _, bereiche in ortswahl
            },
            {"Kino Nord16": ["Thekenfront"], "Kino Süd16": ["Thekenfront"]},
            "Die Orte stehen je Haus da, auch dort, wo sich nichts eintragen lässt.",
        )

    def test_im_aufloesbaren_haus_hinzufuegen(self):
        antwort = self.client.post(
            reverse("erwartung_umschalten", args=[self.vorne.pk]),
            {
                "artikel": self.menue.pk,
                "aktion": "aufnehmen",
                "weiter": self.menue.get_absolute_url(),
            },
        )
        self.assertRedirects(antwort, self.menue.get_absolute_url())
        self.assertEqual(self.orte(), ["Kino Nord16/Thekenfront"])

    def test_wo_eine_komponente_fehlt_geht_es_nicht(self):
        self.client.post(
            reverse("erwartung_umschalten", args=[self.drueben.pk]),
            {
                "artikel": self.menue.pk,
                "aktion": "aufnehmen",
                "weiter": self.menue.get_absolute_url(),
            },
        )
        self.assertEqual(self.orte(), [])

    def test_eingetragener_ort_bleibt_entfernbar(self):
        """Even if the ingredient has been discontinued in the meantime."""
        Erwartung.objects.create(ort=self.vorne, artikel=self.menue)
        Hausartikel.objects.get(artikelnummer="960002").deaktivieren()
        self.assertFalse(self.menue.verfuegbar_in(self.kino_nord.pk))

        self.client.post(
            reverse("erwartung_umschalten", args=[self.vorne.pk]),
            {
                "artikel": self.menue.pk,
                "aktion": "entfernen",
                "weiter": self.menue.get_absolute_url(),
            },
        )
        self.assertEqual(self.orte(), [])

    def test_knoepfe_wirken_ohne_seitenneuladen(self):
        html = self.client.get(self.menue.get_absolute_url()).content.decode()
        self.assertIn("data-liste", html)
        self.assertIn(reverse("erwartung_umschalten", args=[self.vorne.pk]), html)

    def test_ohne_aufloesung_kein_hinzufuegen_knopf(self):
        """Kino Süd cannot carry it. There is no button there, only a dash."""
        html = self.client.get(self.menue.get_absolute_url()).content.decode()
        self.assertNotIn(reverse("erwartung_umschalten", args=[self.drueben.pk]), html)


class ZeilenSpeichern(TestCase):
    """What 'speichern' writes per row, and the rows it leaves alone.

    On the formset itself: the page saves also when the list does not hold
    up as a whole (a row button), and two of these ways cannot be reached
    with what the page sends. Ware N goes onto the scale and is expected
    with it and with two boxes. Ware M is expected without a default.
    """

    @classmethod
    def setUpTestData(cls):
        haus = Haus.objects.create(name="Kino West")
        cls.ort = Ort.objects.create(
            name="Theke", bereich=Bereich.objects.create(name="Theke", haus=haus)
        )
        stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        gruppe = Warengruppe.objects.create(name="Warengruppe W")
        artikel = {}
        for nummer, name, abwiegbar in (("101001", "Ware N", True), ("101002", "Ware M", False)):
            ware = Ware.objects.create(name=name, basiseinheit=stueck, abwiegbar=abwiegbar)
            artikel[name] = Hausartikel.objects.create(
                haus=haus, ware=ware, warengruppe=gruppe, artikelnummer=nummer, name=name
            )
        cls.n, cls.m = artikel["Ware N"], artikel["Ware M"]
        cls.karton = Gebinde.objects.create(
            ware=cls.n.ware, name="Karton", faktor_in_basiseinheit=Decimal("24")
        )
        cls.kasten = Gebinde.objects.create(
            ware=cls.m.ware, name="Kasten", faktor_in_basiseinheit=Decimal("6")
        )

    def setUp(self):
        self.erwartung_n = Erwartung.anlegen(
            self.ort, self.n, wiegen=True, vorgaben=[(self.karton.pk, Decimal("2"))]
        )
        self.erwartung_m = Erwartung.anlegen(self.ort, self.m)

    def zeile(self, erwartung: Erwartung, gebinde: Gebinde | None = None, **felder) -> dict:
        """A row as the page sends it: its expectation, a ticked pack and further fields."""
        if gebinde is not None:
            felder[f"gebinde_{gebinde.pk}"] = "on"
            felder[f"menge_{gebinde.pk}"] = felder.pop("menge", "")
        return {"erwartung": str(erwartung.pk)} | felder

    def formset(self, *zeilen: dict, initial: int | None = None):
        """The formset over these rows, cleaned as the page does it before saving."""
        daten = {
            "zeilen-TOTAL_FORMS": str(len(zeilen)),
            "zeilen-INITIAL_FORMS": str(len(zeilen) if initial is None else initial),
            "zeilen-MIN_NUM_FORMS": "0",
            "zeilen-MAX_NUM_FORMS": "1000",
        }
        for stelle, zeile in enumerate(zeilen):
            daten |= {f"zeilen-{stelle}-{feld}": wert for feld, wert in zeile.items()}
        formset = ZaehllisteFormSet(daten, prefix="zeilen", ort=self.ort)
        formset.is_valid()
        return formset

    def vorgaben(self) -> list[tuple]:
        return sorted(
            ErwartungGebinde.objects.values_list(
                "erwartung__artikel__name", "gebinde__name", "standard_menge"
            )
        )

    def test_ein_fehler_der_liste_haelt_auch_die_gueltigen_zeilen_auf(self):
        """A row sent twice, or one whose expectation is gone: no row of the list is saved.

        The last of two rows would otherwise silently win.
        """
        # A number no expectation carries (any longer), as after a second window removed it.
        weg = Erwartung.objects.order_by("-pk").values_list("pk", flat=True)[0] + 1
        stand = self.vorgaben()
        for fall, zweite in (
            ("zweimal", self.zeile(self.erwartung_m, self.kasten, menge="5")),
            ("entfernt", {"erwartung": str(weg)}),
        ):
            with self.subTest(fall):
                formset = self.formset(self.zeile(self.erwartung_m, self.kasten, menge="4"), zweite)
                self.assertTrue(formset.non_form_errors())

                formset.speichern()

                self.assertEqual(self.vorgaben(), stand)

    def test_eine_unveraenderte_zusatzzeile_nennt_keine_erwartung_und_wird_uebergangen(self):
        """A row the management form counts as extra and that sends what it showed.

        Django cleans nothing in such a row, so it names no expectation. The
        page never sends one (it counts every row as initial). Skipped, the
        row keeps what is stored, and saving it costs no query.
        """
        formset = self.formset(
            self.zeile(self.erwartung_n, self.karton, menge="2", wiegen="on"), initial=0
        )
        self.assertFalse(formset.non_form_errors())
        self.assertNotIn("erwartung", formset.forms[0].cleaned_data)

        with self.assertNumQueries(0):
            formset.speichern()

        self.assertEqual(self.vorgaben(), [("Ware N", "Karton", Decimal("2"))])

    def test_die_vorgaben_landen_an_der_erwartung_des_feldes(self):
        """The row's field decides, not the expectation the formset loaded for the row.

        The formset hands every row the expectation it loaded with its
        defaults, and 'speichern' takes that one only where it is the same.
        A row that carries another one or none still saves onto its own.
        """
        for geladen in (None, self.erwartung_n):
            with self.subTest(geladen=geladen):
                ErwartungGebinde.objects.filter(erwartung=self.erwartung_m).delete()
                formset = self.formset(self.zeile(self.erwartung_m, self.kasten, menge="4"))
                formset.forms[0].erwartung_objekt = geladen

                formset.speichern()

                self.assertEqual(
                    self.vorgaben(),
                    [("Ware M", "Kasten", Decimal("4")), ("Ware N", "Karton", Decimal("2"))],
                )

    def test_eine_zeile_wie_gespeichert_schreibt_nichts(self):
        """Ticks, quantities and the scale as they are stored: saving the row writes nothing."""
        ErwartungGebinde.objects.create(erwartung=self.erwartung_m, gebinde=self.kasten)
        formset = self.formset(
            self.zeile(self.erwartung_n, self.karton, menge="2", wiegen="on"),
            self.zeile(self.erwartung_m, self.kasten),
        )

        with CaptureQueriesContext(connection) as abfragen:
            formset.speichern()

        geschrieben = [
            abfrage["sql"]
            for abfrage in abfragen.captured_queries
            if not abfrage["sql"].lstrip().upper().startswith("SELECT")
        ]
        self.assertEqual(geschrieben, [])
        self.erwartung_n.refresh_from_db()
        self.assertTrue(self.erwartung_n.wiegen_erwartet)
