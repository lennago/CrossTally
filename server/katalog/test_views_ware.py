"""Products, their packs, weighing, product groups and product families through the pages."""

from decimal import Decimal

from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser
from django_project.form_fields import wahlmenge
from django_project.testing import (
    AngemeldeteSeiten,
    bereich,
    jahresinventur,
    monatsinventur,
    skript,
)
from organisation.models import Haus, Ort

from .forms import WareFilterForm, WareForm
from .models import (
    Artikelstatus,
    Basiseinheit,
    Behaelter,
    Dimension,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Rezepturartikel,
    Rezepturkomponente,
    Ware,
    Warenfamilie,
    Warengruppe,
)


class GebindeSortierung(AngemeldeteSeiten):
    """The order of the packs belongs to the product, not to the location."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.sammel = Ware.objects.create(name="Artikel C 0,5", basiseinheit=cls.stueck)

    def gebinde(self, *namen):
        """Per product every quantity occurs only once. Hence ascending."""
        return [
            Gebinde.objects.create(
                ware=self.sammel, name=name, faktor_in_basiseinheit=Decimal(nummer)
            )
            for nummer, name in enumerate(namen, start=1)
        ]

    def reihenfolge(self):
        return list(
            Gebinde.objects.filter(ware=self.sammel)
            .order_by("sortierung")
            .values_list("name", flat=True)
        )

    def test_position_wird_beim_anlegen_vergeben(self):
        self.gebinde("Kasten", "Flasche(n)", "Karton")
        self.assertEqual(
            [satz.sortierung for satz in Gebinde.objects.filter(ware=self.sammel)],
            [1, 2, 3],
        )

    def test_abwiegen_belegt_keine_position(self):
        """Since it is a tick on the product, it does not count in the order."""
        gramm = Basiseinheit.objects.get(name="g")
        nachos = Ware.objects.create(name="Artikel N lose", basiseinheit=gramm, abwiegbar=True)
        erstes = Gebinde.objects.create(
            ware=nachos, name="Karton", faktor_in_basiseinheit=Decimal("5")
        )
        self.assertEqual(erstes.sortierung, 1)

    def test_ziehen_schreibt_die_neue_reihenfolge(self):
        kasten, flasche, karton = self.gebinde("Kasten", "Flasche(n)", "Karton")
        antwort = self.client.post(
            reverse("gebinde_sortieren", args=[self.sammel.pk]),
            {"reihenfolge": [str(karton.pk), str(kasten.pk), str(flasche.pk)]},
        )
        self.assertRedirects(antwort, self.sammel.get_absolute_url())
        self.assertEqual(self.reihenfolge(), ["Karton", "Kasten", "Flasche(n)"])

    def test_speichern_im_hintergrund_antwortet_ohne_seite(self):
        """The button "Save order" stays on the page and reports on the button."""
        kasten, flasche = self.gebinde("Kasten", "Flasche(n)")
        antwort = self.client.post(
            reverse("gebinde_sortieren", args=[self.sammel.pk]),
            {"reihenfolge": [str(flasche.pk), str(kasten.pk)]},
            headers={"X-Requested-With": "fetch"},
        )
        self.assertEqual(antwort.status_code, 204)
        self.assertEqual(self.reihenfolge(), ["Flasche(n)", "Kasten"])

    def test_pfeil_im_hintergrund_bekommt_die_seite_zurueck(self):
        """The script puts the fresh list in place of the old one. A 204 would leave it nothing."""
        kasten, flasche = self.gebinde("Kasten", "Flasche(n)")
        antwort = self.client.post(
            reverse("gebinde_sortieren", args=[self.sammel.pk]),
            {"reihenfolge": [str(kasten.pk), str(flasche.pk)], "runter": str(kasten.pk)},
            headers={"X-Requested-With": "fetch"},
        )
        self.assertRedirects(antwort, self.sammel.get_absolute_url())
        self.assertEqual(self.reihenfolge(), ["Flasche(n)", "Kasten"])

    def test_gebindeknoepfe_wirken_ohne_seitenneuladen(self):
        self.gebinde("Kasten", "Flasche(n)")
        html = self.client.get(self.sammel.get_absolute_url()).content.decode()
        for stueck in ("data-liste", "data-sofort", "js/instant_actions.js"):
            self.assertIn(stueck, html)

    def test_der_fokus_findet_nach_einer_zeilenaktion_zurueck(self):
        """The pressed button is new after the swap. Until 10/2026 Tab then began at the top."""
        quelle = skript("instant_actions")
        for stueck in ("[data-fokus=", "tabIndex = -1", ":not([disabled])"):
            with self.subTest(stueck=stueck):
                self.assertIn(stueck, quelle)
        # Refreshing keeps the button: busy, not disabled.
        neu_laden = skript("soft_reload")
        self.assertIn('setAttribute("aria-busy", "true")', neu_laden)
        self.assertNotIn("knopf.disabled = true", neu_laden)
        # A closed selection menu hands the focus back to its button.
        self.assertIn("umschalter?.focus(", skript("multi_select"))

    def test_pfeil_hat_vorrang_vor_der_mitgeschickten_reihenfolge(self):
        kasten, flasche = self.gebinde("Kasten", "Flasche(n)")
        self.client.post(
            reverse("gebinde_sortieren", args=[self.sammel.pk]),
            {"reihenfolge": [str(kasten.pk), str(flasche.pk)], "runter": str(kasten.pk)},
        )
        self.assertEqual(self.reihenfolge(), ["Flasche(n)", "Kasten"])

    def test_unvollstaendige_reihenfolge_aendert_nichts(self):
        kasten, flasche = self.gebinde("Kasten", "Flasche(n)")
        self.client.post(
            reverse("gebinde_sortieren", args=[self.sammel.pk]),
            {"reihenfolge": [str(flasche.pk)]},
        )
        self.assertEqual(self.reihenfolge(), ["Kasten", "Flasche(n)"])

    def test_mehrere_neue_zeilen_bekommen_verschiedene_positionen(self):
        """A formset saves several rows. Validation assigns all the same number."""
        daten = {
            "name": "Artikel W 0,5",
            "basiseinheit": self.stueck.pk,
            "gebinde-TOTAL_FORMS": "2",
            "gebinde-INITIAL_FORMS": "0",
            "gebinde-MIN_NUM_FORMS": "0",
            "gebinde-MAX_NUM_FORMS": "1000",
            "gebinde-0-name": "Kasten",
            "gebinde-0-faktor_in_basiseinheit": "12",
            "gebinde-1-name": "Flasche(n)",
            "gebinde-1-faktor_in_basiseinheit": "1",
        }
        antwort = self.client.post(reverse("ware_add"), daten)
        self.assertEqual(antwort.status_code, 302)
        neu = Ware.objects.get(name="Artikel W 0,5")
        self.assertEqual(sorted(neu.gebinde.values_list("sortierung", flat=True)), [1, 2])

    def test_vorgaben_folgen_der_reihenfolge_des_gebindes(self):
        kasten, flasche = self.gebinde("Kasten", "Flasche(n)")
        haus = Haus.objects.create(name="Kino Süd")
        ort = Ort.objects.create(name="Theke", bereich=bereich(haus))
        artikel = Hausartikel.objects.create(
            haus=haus,
            ware=self.sammel,
            warengruppe=Warengruppe.objects.create(name="Warengruppe G2"),
            artikelnummer="301001",
            name="Artikel C 0,5",
        )
        erwartung = Erwartung.objects.create(ort=ort, artikel=artikel)
        ErwartungGebinde.objects.create(erwartung=erwartung, gebinde=flasche)
        ErwartungGebinde.objects.create(erwartung=erwartung, gebinde=kasten)

        self.assertEqual(
            [vorgabe.gebinde.name for vorgabe in erwartung.gebinde_vorgaben.all()],
            ["Kasten", "Flasche(n)"],
        )
        Gebinde.neu_sortieren(self.sammel.pk, [flasche.pk, kasten.pk])
        self.assertEqual(
            [vorgabe.gebinde.name for vorgabe in erwartung.gebinde_vorgaben.all()],
            ["Flasche(n)", "Kasten"],
        )


class AbgeleiteterAktivZustand(AngemeldeteSeiten):
    """Product group and product are active as long as a house article in them is active."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino West")
        cls.zweites = Haus.objects.create(name="Kino Nord")
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe U")
        cls.sammel = Ware.objects.create(
            name="Artikel F 0,33", basiseinheit=Basiseinheit.objects.get(dimension=Dimension.STUECK)
        )
        cls.hier = Hausartikel.objects.create(
            haus=cls.haus,
            ware=cls.sammel,
            warengruppe=cls.gruppe,
            artikelnummer="400001",
            name="Artikel F Kino West",
        )
        cls.dort = Hausartikel.objects.create(
            haus=cls.zweites,
            ware=cls.sammel,
            warengruppe=cls.gruppe,
            artikelnummer="400002",
            name="Artikel F Kino Nord",
        )

    def test_ohne_hausartikel_inaktiv(self):
        leer = Ware.objects.create(name="Nie geführt", basiseinheit=self.sammel.basiseinheit)
        self.assertFalse(leer.aktiv)

    def test_aktiv_solange_ein_haus_die_ware_fuehrt(self):
        self.assertTrue(self.sammel.aktiv)
        self.hier.deaktivieren()
        self.assertTrue(self.sammel.aktiv)
        self.dort.deaktivieren()
        self.assertFalse(self.sammel.aktiv)

    def test_inaktiv_setzt_alle_hausartikel_der_ware(self):
        antwort = self.client.post(
            reverse("ware_status", args=[self.sammel.pk]), {"status": "INAKTIV"}
        )
        self.assertRedirects(antwort, self.sammel.get_absolute_url())

        for artikel in (self.hier, self.dort):
            artikel.refresh_from_db()
            self.assertFalse(artikel.aktiv)
            self.assertIsNotNone(artikel.gueltig_bis)
        self.assertFalse(Ware.objects.get(pk=self.sammel.pk).aktiv)

    def test_einstellen_laesst_gebinde_und_rezeptur_stehen(self):
        Gebinde.objects.create(
            ware=self.sammel, name="Kasten", faktor_in_basiseinheit=Decimal("12")
        )
        zaehl = Rezepturartikel.objects.create(
            name="Menü", basiseinheit=Basiseinheit.objects.get(dimension=Dimension.STUECK)
        )
        Rezepturkomponente.objects.create(
            rezepturartikel=zaehl, ware=self.sammel, menge_in_basiseinheit=Decimal("1")
        )
        self.client.post(reverse("ware_status", args=[self.sammel.pk]), {"status": "EINGESTELLT"})
        self.assertEqual(self.sammel.gebinde.count(), 1)
        self.assertEqual(self.sammel.verwendet_in.count(), 1)

    def test_liste_zaehlt_die_aktiven_hausartikel(self):
        self.hier.deaktivieren()
        antwort = self.client.get(reverse("ware_list"))
        gefunden = {satz.name: satz.anzahl_aktiv for satz in antwort.context["ware"]}
        self.assertEqual(gefunden["Artikel F 0,33"], 1)

    def test_liste_zaehlt_auch_bei_gefilterter_suche_richtig(self):
        """The filters join over the house articles. Without 'distinct' that counted twice."""
        antwort = self.client.get(reverse("ware_list"), {"q": "artikel f"})
        gefunden = {satz.name: satz.anzahl_aktiv for satz in antwort.context["ware"]}
        self.assertEqual(gefunden["Artikel F 0,33"], 2)


class WarengruppeArt(AngemeldeteSeiten):
    """The inventory type can be switched for all house articles of a product group."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Ost3")
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.suess = Warengruppe.objects.create(name="Warengruppe U")
        cls.nummer = 0

    def ware(self, name, warengruppe=None, haus=None):
        type(self).nummer += 1
        sammel = Ware.objects.create(name=name, basiseinheit=self.stueck)
        Hausartikel.objects.create(
            haus=haus or self.haus,
            ware=sammel,
            warengruppe=warengruppe or self.suess,
            artikelnummer=f"{800000 + self.nummer}",
            name=name,
        )
        return sammel

    def umstellen(self, art_pk):
        return self.client.post(
            reverse("warengruppe_inventurart", args=[self.suess.pk]), {"inventurart": art_pk}
        )

    @staticmethod
    def arten(ware) -> set[str]:
        return set(ware.hausartikel.values_list("inventurart__name", flat=True))

    def test_alle_artikel_der_gruppe_umstellen(self):
        eine, andere = self.ware("Ware G"), self.ware("Artikel L")
        antwort = self.umstellen(jahresinventur().pk)
        self.assertRedirects(antwort, self.suess.get_absolute_url())
        for sammel in (eine, andere):
            self.assertEqual(self.arten(sammel), {"Jahresinventur"})

    def test_andere_gruppen_bleiben_unberuehrt(self):
        self.ware("Ware G")
        fremd = self.ware(
            "Artikel B", warengruppe=Warengruppe.objects.create(name="Warengruppe G7")
        )
        self.umstellen(jahresinventur().pk)
        self.assertEqual(self.arten(fremd), {"Monatsinventur"})

    def test_dieselbe_ware_in_einer_anderen_gruppe_behaelt_dort_ihre_art(self):
        """The group hangs on the house article, as the type does."""
        geteilt = self.ware("Artikel N")
        Hausartikel.objects.create(
            haus=Haus.objects.create(name="Kino Süd4"),
            ware=geteilt,
            warengruppe=Warengruppe.objects.create(name="Warengruppe M"),
            artikelnummer="101810",
            name="Artikel N lokal",
        )
        antwort = self.umstellen(jahresinventur().pk)
        self.assertEqual(self.arten(geteilt), {"Monatsinventur", "Jahresinventur"})
        meldungen = [str(eintrag) for eintrag in get_messages(antwort.wsgi_request)]
        self.assertIn("Ein Artikel aus „Warengruppe U“ zählt jetzt", " ".join(meldungen))

    def test_ohne_aenderung_kommt_ein_hinweis(self):
        self.ware("Ware G")
        antwort = self.umstellen(monatsinventur().pk)
        meldungen = [str(eintrag) for eintrag in get_messages(antwort.wsgi_request)]
        self.assertIn("zählte schon alles", " ".join(meldungen))

    def test_eine_unbekannte_art_aendert_nichts(self):
        sammel = self.ware("Ware G")
        self.umstellen(0)
        self.assertEqual(self.arten(sammel), {"Monatsinventur"})

    def test_detailseite_zeigt_die_verteilung(self):
        self.ware("Ware G")
        nur_jahr = self.ware("Artikel L")
        nur_jahr.inventurart_setzen(jahresinventur())
        antwort = self.client.get(self.suess.get_absolute_url())
        self.assertEqual(
            antwort.context["arten_verteilung"],
            [(monatsinventur(), 1), (jahresinventur(), 1)],
        )
        self.assertIsNone(antwort.context["art_der_gruppe"])


class GebinderegelnImFormular(AngemeldeteSeiten):
    """The rules apply there too where several rows arrive at once."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.gramm = Basiseinheit.objects.get(name="g")

    def ware_daten(self, name, einheit, **abweichend):
        return {
            "name": name,
            "basiseinheit": einheit.pk,
            "gebinde-TOTAL_FORMS": "2",
            "gebinde-INITIAL_FORMS": "0",
            "gebinde-MIN_NUM_FORMS": "0",
            "gebinde-MAX_NUM_FORMS": "1000",
        } | abweichend

    def test_zwei_gleiche_mengen_in_einem_zug(self):
        antwort = self.client.post(
            reverse("ware_add"),
            self.ware_daten(
                "Artikel C 1l",
                self.stueck,
                **{
                    "gebinde-0-name": "Kasten",
                    "gebinde-0-faktor_in_basiseinheit": "20",
                    "gebinde-1-name": "Kiste",
                    "gebinde-1-faktor_in_basiseinheit": "20",
                },
            ),
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "dieselbe Menge")
        self.assertFalse(Ware.objects.filter(name="Artikel C 1l").exists())

    def test_abwiegen_wird_am_ware_gesetzt(self):
        schale = Behaelter.objects.create(name="Schale", tara=Decimal("250"))
        antwort = self.client.post(
            reverse("ware_add"),
            self.ware_daten("Artikel N lose", self.gramm, abwiegbar="on", behaelter=[schale.pk]),
        )
        self.assertEqual(antwort.status_code, 302)
        ware = Ware.objects.get(name="Artikel N lose")
        self.assertTrue(ware.abwiegbar)
        self.assertEqual(list(ware.behaelter.all()), [schale])

    def test_abwiegen_geht_auch_bei_stueckware(self):
        """Through the piece weight the scale converts into pieces."""
        antwort = self.client.post(
            reverse("ware_add"),
            self.ware_daten("Becher", self.stueck, abwiegbar="on", faktor_MASSE="4.5"),
        )
        self.assertEqual(antwort.status_code, 302)
        becher = Ware.objects.get(name="Becher")
        self.assertTrue(becher.abwiegbar)
        self.assertEqual(becher.in_basiseinheit(Decimal("45")), Decimal("10"))

    def test_eigene_dimension_hat_keinen_faktor(self):
        antwort = self.client.post(
            reverse("ware_add"),
            self.ware_daten("Artikel N", self.gramm, abwiegbar="on", faktor_MASSE="2"),
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "rechnet die Einheit selbst um")
        self.assertFalse(Ware.objects.filter(name="Artikel N").exists())

    def test_behaelter_ohne_haken_werden_abgewiesen(self):
        schale = Behaelter.objects.create(name="Schale", tara=Decimal("250"))
        antwort = self.client.post(
            reverse("ware_add"),
            self.ware_daten("Artikel N lose", self.gramm, behaelter=[schale.pk]),
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Behälter gehören zum Wiegen")

    def test_ohne_behaelter_steht_nicht_zur_wahl(self):
        """It applies at every weighing. A box for it would be a sham decision."""
        Behaelter.objects.create(name="Schale", tara=Decimal("250"))
        auswahl = wahlmenge(WareForm(), "behaelter")
        self.assertNotIn(Behaelter.ohne_behaelter(), auswahl)
        self.assertEqual([b.name for b in auswahl], ["Schale"])

    def test_die_ware_erlaubt_die_leere_waage_trotzdem(self):
        """Without assignment does not mean without possibility."""
        schale = Behaelter.objects.create(name="Schale", tara=Decimal("250"))
        ware = Ware.objects.create(name="Artikel N lose", basiseinheit=self.gramm, abwiegbar=True)
        ware.behaelter.set([schale])
        self.assertCountEqual(ware.waehlbare_behaelter(), [schale, Behaelter.ohne_behaelter()])

    def test_verschiedene_mengen_gehen_durch(self):
        antwort = self.client.post(
            reverse("ware_add"),
            self.ware_daten(
                "Artikel C 1l",
                self.stueck,
                **{
                    "gebinde-0-name": "Kasten",
                    "gebinde-0-faktor_in_basiseinheit": "20",
                    "gebinde-1-name": "Sixpack",
                    "gebinde-1-faktor_in_basiseinheit": "6",
                },
            ),
        )
        self.assertEqual(antwort.status_code, 302)
        self.assertEqual(Ware.objects.get(name="Artikel C 1l").gebinde.count(), 2)

    def test_hausartikelformular_weist_doppelte_menge_ab(self):
        haus = Haus.objects.create(name="Kino Ost4")
        antwort = self.client.post(
            reverse("hausartikel_add", args=[haus.pk]),
            {
                "artikelnummer": "910001",
                "name": "Artikel C",
                "ware": "",
                "neuer_ware": "",
                "basiseinheit": self.stueck.pk,
                "warengruppe": "",
                "neue_warengruppe": "Warengruppe G8",
                "ean": "",
                "gebinde-TOTAL_FORMS": "2",
                "gebinde-INITIAL_FORMS": "0",
                "gebinde-MIN_NUM_FORMS": "0",
                "gebinde-MAX_NUM_FORMS": "1000",
                "gebinde-0-name": "Kasten",
                "gebinde-0-faktor_in_basiseinheit": "20",
                "gebinde-1-name": "Kiste",
                "gebinde-1-faktor_in_basiseinheit": "20",
                "orte-TOTAL_FORMS": "0",
                "orte-INITIAL_FORMS": "0",
                "orte-MIN_NUM_FORMS": "0",
                "orte-MAX_NUM_FORMS": "1000",
            },
        )
        self.assertContains(antwort, "nur einmal vorkommen")
        self.assertFalse(Hausartikel.objects.filter(artikelnummer="910001").exists())

    def test_importplan_weist_doppelte_menge_ab(self):
        haus = Haus.objects.create(name="Kino Süd5")
        inhalt = (
            "artikelnummer;name;warengruppe;gebinde;anzahl;einheit;haus\n"
            "101001;Artikel C 0,33;Warengruppe G;Kasten;24;Stück(e);Kino Süd5"
        )
        antwort = self.client.post(
            reverse("hausartikel_import", args=[haus.pk]),
            {
                "datei": SimpleUploadedFile("e.csv", inhalt.encode(), "text/csv"),
                "format": "winticket",
            },
        )
        formset = antwort.context["formset"]
        daten = {
            "schritt": "uebernehmen",
            "plan-TOTAL_FORMS": "1",
            "plan-INITIAL_FORMS": "1",
            "plan-MIN_NUM_FORMS": "0",
            "plan-MAX_NUM_FORMS": "5000",
        }
        for feld, wert in formset.forms[0].initial.items():
            daten[f"plan-0-{feld}"] = "" if wert is None else wert
        daten["plan-0-gebinde"] = "Kasten = 24; Kiste = 24"

        antwort = self.client.post(reverse("hausartikel_import", args=[haus.pk]), daten)
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "nur einmal vorkommen")
        self.assertFalse(Hausartikel.objects.exists())


class AbwiegenAusDerListe(AngemeldeteSeiten):
    """Weighing can be switched row by row, because it belongs to the product."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.kg = Basiseinheit.objects.get(name="kg")
        cls.haus = Haus.objects.create(name="Kino Ost9")
        cls.gruppe = Warengruppe.objects.create(name="Kino9")
        cls.schale = Behaelter.objects.create(name="Schale", tara=Decimal("250"))

        cls.nachos = Ware.objects.create(name="Artikel N lose", basiseinheit=cls.kg)
        cls.cola = Ware.objects.create(name="Artikel C 0,33", basiseinheit=cls.stueck)
        for nummer, ware in (("990001", cls.nachos), ("990002", cls.cola)):
            Hausartikel.objects.create(
                haus=cls.haus,
                ware=ware,
                warengruppe=cls.gruppe,
                artikelnummer=nummer,
                name=ware.name,
            )

    def schalten(self, ware, wert, weiter=None):
        return self.client.post(
            reverse("ware_abwiegen", args=[ware.pk]),
            {"abwiegbar": wert, "weiter": weiter or reverse("ware_list")},
        )

    # -- What the lists offer -----------------------------------------------

    def test_alle_drei_listen_zeigen_die_knoepfe(self):
        adressen = [
            reverse("ware_list"),
            reverse("hausartikel_list", args=[self.haus.pk]),
            self.gruppe.get_absolute_url(),
        ]
        ziel = reverse("ware_abwiegen", args=[self.nachos.pk])
        for adresse in adressen:
            with self.subTest(adresse=adresse):
                antwort = self.client.get(adresse)
                self.assertContains(antwort, "Abwiegen")
                self.assertContains(antwort, ziel)

    def test_auch_stueckware_bekommt_den_knopf(self):
        """The scale delivers grams. The rest is done by the conversion factor."""
        antwort = self.client.get(reverse("ware_list"))
        self.assertContains(antwort, reverse("ware_abwiegen", args=[self.cola.pk]))

    # -- What the buttons cause ---------------------------------------------

    def test_einschalten(self):
        liste = reverse("ware_list") + "?q=artikel+n"
        antwort = self.schalten(self.nachos, "ja", liste)
        self.assertRedirects(antwort, liste)
        self.nachos.refresh_from_db()
        self.assertTrue(self.nachos.abwiegbar)

    def test_ausschalten_loest_die_behaelter(self):
        """Without the tick the form rejects containers. They must not remain."""
        self.nachos.abwiegbar = True
        self.nachos.save(update_fields=["abwiegbar"])
        self.nachos.behaelter.set([self.schale])

        self.schalten(self.nachos, "nein")
        self.nachos.refresh_from_db()
        self.assertFalse(self.nachos.abwiegbar)
        self.assertEqual(self.nachos.behaelter.count(), 0)

    def test_danach_bleibt_die_ware_speicherbar(self):
        """The actual reason for detaching: otherwise the form would fail."""
        self.nachos.abwiegbar = True
        self.nachos.save(update_fields=["abwiegbar"])
        self.nachos.behaelter.set([self.schale])
        self.schalten(self.nachos, "nein")

        form = WareForm(
            {
                "name": self.nachos.name,
                "basiseinheit": self.kg.pk,
            },
            instance=Ware.objects.get(pk=self.nachos.pk),
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_stueckware_laesst_sich_abwiegen(self):
        antwort = self.schalten(self.cola, "ja")
        self.cola.refresh_from_db()
        self.assertTrue(self.cola.abwiegbar)
        self.assertEqual(antwort.status_code, 302)

    def test_fehlender_faktor_wird_angemahnt(self):
        """Without piece weight the scale computed 1:1. That must not happen silently."""
        antwort = self.schalten(self.cola, "ja")
        meldungen = " ".join(str(m) for m in get_messages(antwort.wsgi_request))
        self.assertIn("„g je Stk.“", meldungen)

    def test_bei_masse_kommt_keine_mahnung(self):
        antwort = self.schalten(self.nachos, "ja")
        meldungen = " ".join(str(m) for m in get_messages(antwort.wsgi_request))
        self.assertNotIn("fehlt noch", meldungen)

    def test_derselbe_zustand_meldet_nichts(self):
        antwort = self.schalten(self.nachos, "nein")
        self.assertEqual(list(get_messages(antwort.wsgi_request)), [])

    def test_fremdes_ziel_wird_verworfen(self):
        antwort = self.schalten(self.nachos, "ja", "https://example.com/phish")
        self.assertRedirects(antwort, self.nachos.get_absolute_url())


class UmrechnungenImFormular(AngemeldeteSeiten):
    """One field per foreign dimension. Containers belong to weighing, the grams do too."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.kg = Basiseinheit.objects.get(name="kg")
        cls.liter = Basiseinheit.objects.get(name="Liter")
        cls.ml = Basiseinheit.objects.get(name="Milliliter")
        cls.schale = Behaelter.objects.create(name="Schale", tara=Decimal("250"))

    def daten(self, einheit, **abweichend):
        return {
            "name": "Probe",
            "basiseinheit": einheit.pk,
        } | abweichend

    # -- What the validation allows -----------------------------------------

    def test_faktor_ohne_haken_geht(self):
        """The family may need the grams even if nobody weighs the product."""
        form = WareForm(self.daten(self.stueck, faktor_MASSE="4,5"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.faktoren, {Dimension.MASSE: Decimal("4.5")})

    def test_gewicht_bleibt_solange_eine_anteilige_rezeptur_es_braucht(self):
        """A share in percent converts through the grams per litre. Emptying the field fails."""
        sirup = Ware.objects.create(name="Sirup S", basiseinheit=self.liter)
        sirup.umrechnungen_setzen({Dimension.MASSE: Decimal("1250")})
        mischung = Rezepturartikel.objects.create(
            name="Mischung S", basiseinheit=Basiseinheit.objects.get(name="g"), anteilig=True
        )
        Rezepturkomponente.objects.create(
            rezepturartikel=mischung, ware=sirup, menge_in_basiseinheit=Decimal("100")
        )

        form = WareForm(self.daten(self.liter, name="Sirup S"), instance=sirup)
        self.assertFalse(form.is_valid())
        self.assertIn("„Mischung S“ rechnet Anteile in Prozent", str(form.errors["faktor_MASSE"]))

        # With the grams in place it goes through, and so does a switch to a mass unit.
        form = WareForm(self.daten(self.liter, name="Sirup S", faktor_MASSE="1250"), instance=sirup)
        self.assertTrue(form.is_valid(), form.errors)

    def test_die_warenseite_zeigt_den_anteil_einer_gewogenen_rezeptur_in_prozent(self):
        """Until 10/2026 it said "50 kg je g" where the recipe page said "50 %"."""
        sirup = Ware.objects.create(name="Sirup S", basiseinheit=self.liter)
        sirup.umrechnungen_setzen({Dimension.MASSE: Decimal("1250")})
        mischung = Rezepturartikel.objects.create(
            name="Mischung S", basiseinheit=Basiseinheit.objects.get(name="g"), anteilig=True
        )
        Rezepturkomponente.objects.create(
            rezepturartikel=mischung, ware=sirup, menge_in_basiseinheit=Decimal("50")
        )

        antwort = self.client.get(reverse("ware_detail", args=[sirup.pk]))

        self.assertContains(antwort, "50 %")
        self.assertNotContains(antwort, "je g")

    def test_faktor_der_eigenen_dimension_wird_abgewiesen(self):
        form = WareForm(self.daten(self.kg, abwiegbar="on", faktor_MASSE="2"))
        self.assertFalse(form.is_valid())
        self.assertIn("rechnet die Einheit selbst um", str(form.errors["faktor_MASSE"]))

    def test_wiegen_braucht_die_gramm(self):
        for einheit in (self.stueck, self.liter):
            with self.subTest(einheit=einheit.name):
                form = WareForm(self.daten(einheit, abwiegbar="on"))
                self.assertFalse(form.is_valid())
                self.assertIn("Zum Wiegen braucht die Ware", str(form.errors["faktor_MASSE"]))
                form = WareForm(self.daten(einheit, abwiegbar="on", faktor_MASSE="1,25"))
                self.assertTrue(form.is_valid(), form.errors)

    def test_masse_mit_haken_geht_ohne_faktor(self):
        form = WareForm(self.daten(self.kg, abwiegbar="on"))
        self.assertTrue(form.is_valid(), form.errors)

    def test_gespeicherte_faktoren_stehen_im_formular_und_ein_leeres_feld_loescht(self):
        flasche = Ware.objects.create(name="Flasche 0,5", basiseinheit=self.stueck)
        flasche.umrechnungen_setzen(
            {Dimension.VOLUMEN: Decimal("500"), Dimension.MASSE: Decimal("520")}
        )
        form = WareForm(instance=flasche)
        self.assertEqual(form.fields["faktor_VOLUMEN"].initial, Decimal("500"))
        self.assertEqual(form.fields["faktor_MASSE"].initial, Decimal("520"))

        form = WareForm(
            self.daten(self.stueck, name="Flasche 0,5", faktor_VOLUMEN="330"), instance=flasche
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(flasche.faktoren(), {Dimension.VOLUMEN: Decimal("330")})

    def test_750_ml_je_stueck_bleiben_750(self):
        """Stored is the reciprocal, to twelve places. With six it came back as 750.188."""
        for name, einheit, eingabe, gespeichert in (
            ("Ware N", self.ml, "750", "0.001333333333"),
            ("Ware L", self.liter, "0,75", "1.333333333333"),
        ):
            with self.subTest(einheit=einheit.name):
                form = WareForm(self.daten(einheit, name=name, faktor_STUECK=eingabe))
                self.assertTrue(form.is_valid(), form.errors)
                ware = form.save()
                zeile = ware.umrechnungen.get()
                self.assertEqual(zeile.referenzmenge_je_einheit, Decimal(gespeichert))
                self.assertEqual(zeile.anzeigetext, f"1 Stk. = {eingabe} {einheit.kurz}")
                form = WareForm(instance=ware)
                self.assertIn(f'value="{eingabe}"', str(form["faktor_STUECK"]))

    def test_die_nullen_der_spalte_stehen_nicht_im_formular(self):
        """The column hands a stored 500 back as 500.000000000000. The field says 500."""
        flasche = Ware.objects.create(name="Flasche 0,5", basiseinheit=self.stueck)
        flasche.umrechnungen_setzen({Dimension.VOLUMEN: Decimal("500")})
        self.assertIn('value="500"', str(WareForm(instance=flasche)["faktor_VOLUMEN"]))

    def test_ein_faktor_mit_zwoelf_stellen_laesst_sich_wieder_speichern(self):
        """After a change of unit a factor may carry twelve places. Saving the form keeps it."""
        dose = Ware.objects.create(name="Ware D", basiseinheit=self.stueck)
        dose.umrechnungen_setzen({Dimension.MASSE: Decimal("333.333333333333")})
        gezeigt = WareForm(instance=dose)["faktor_MASSE"]
        self.assertIn('value="333,333333333333"', str(gezeigt))
        form = WareForm(
            self.daten(self.stueck, name="Ware D", faktor_MASSE="333,333333333333"), instance=dose
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(dose.faktoren(), {Dimension.MASSE: Decimal("333.333333333333")})

    def test_null_ist_keine_umrechnung(self):
        """Django would name the twelve-place limit, and that reads "1E-12"."""
        form = WareForm(self.daten(self.stueck, faktor_VOLUMEN="0"))
        self.assertFalse(form.is_valid())
        meldung = str(form.errors["faktor_VOLUMEN"])
        self.assertIn("Eine Umrechnung muss größer als null sein.", meldung)
        self.assertNotIn("1E-12", meldung)

    def test_behaelter_ohne_haken_wird_abgewiesen(self):
        form = WareForm(self.daten(self.kg, behaelter=[self.schale.pk]))
        self.assertFalse(form.is_valid())
        self.assertIn("Ohne den Haken bleiben sie leer", str(form.errors["behaelter"]))

    # -- What the script for showing and hiding needs ---------------------------

    def test_die_seite_bringt_die_angriffspunkte_mit(self):
        antwort = self.client.get(reverse("ware_add"))
        for marke in (
            'id="id_abwiegbar"',
            'id="id_basiseinheit"',
            'id="div_id_behaelter"',
            'id="div_id_faktor_MASSE"',
            'id="div_id_faktor_VOLUMEN"',
            'id="div_id_faktor_STUECK"',
            "data-dimension",
        ):
            with self.subTest(marke=marke):
                self.assertContains(antwort, marke)

    def test_das_feld_ist_nicht_abgeschaltet(self):
        """Disabled, its value would not come along. Showing and hiding happens in the browser."""
        self.assertFalse(WareForm().fields["faktor_MASSE"].disabled)


class AbwiegenFiltern(AngemeldeteSeiten):
    """Both article lists can be restricted to "is weighed"."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        kg = Basiseinheit.objects.get(name="kg")
        stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.haus = Haus.objects.create(name="Kino Ost7")
        gruppe = Warengruppe.objects.create(name="Kino7")

        cls.nachos = Ware.objects.create(name="Artikel N lose", basiseinheit=kg, abwiegbar=True)
        cls.cola = Ware.objects.create(name="Artikel C 0,33", basiseinheit=stueck)
        for nummer, ware in (("970001", cls.nachos), ("970002", cls.cola)):
            Hausartikel.objects.create(
                haus=cls.haus,
                ware=ware,
                warengruppe=gruppe,
                artikelnummer=nummer,
                name=ware.name,
            )

    def namen(self, adresse, schluessel, **parameter):
        antwort = self.client.get(adresse, parameter)
        return sorted(satz.name for satz in antwort.context[schluessel])

    def test_wareliste(self):
        adresse = reverse("ware_list")
        self.assertEqual(self.namen(adresse, "ware"), ["Artikel C 0,33", "Artikel N lose"])
        self.assertEqual(self.namen(adresse, "ware", abwiegbar="ja"), ["Artikel N lose"])
        self.assertEqual(self.namen(adresse, "ware", abwiegbar="nein"), ["Artikel C 0,33"])

    def test_hausartikelliste_filtert_ueber_die_ware(self):
        """Weighing hangs on the product, not on the house row."""
        adresse = reverse("hausartikel_list", args=[self.haus.pk])
        self.assertEqual(self.namen(adresse, "hausartikel", abwiegbar="ja"), ["Artikel N lose"])
        self.assertEqual(self.namen(adresse, "hausartikel", abwiegbar="nein"), ["Artikel C 0,33"])

    def test_unsinniger_wert_laesst_die_liste_stehen(self):
        """A faulty parameter in the address must not filter everything away."""
        adresse = reverse("ware_list")
        self.assertEqual(
            self.namen(adresse, "ware", abwiegbar="vielleicht"),
            ["Artikel C 0,33", "Artikel N lose"],
        )

    def test_der_filter_gilt_als_einschraenkung(self):
        """Otherwise the hint "Reset filter" would be missing."""
        for (adresse,) in ((reverse("ware_list"),),):
            antwort = self.client.get(adresse, {"abwiegbar": "ja"})
            self.assertTrue(antwort.context["filterform"].ist_eingeschraenkt)

    def test_das_feld_steht_in_beiden_formularen(self):
        for adresse in (
            reverse("ware_list"),
            reverse("hausartikel_list", args=[self.haus.pk]),
        ):
            with self.subTest(adresse=adresse):
                self.assertContains(self.client.get(adresse), 'name="abwiegbar"')


class Warenfamilien(TestCase):
    """Creating a family: choose products, unit from them, factor only on conflict."""

    @classmethod
    def setUpTestData(cls):
        cls.user = CustomUser.objects.create_superuser(
            username="chefin", password="geheim", email="chefin@example.org"
        )
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.kg = Basiseinheit.objects.get(name="kg")
        cls.eis_lose = Ware.objects.create(name="Artikel V lose", basiseinheit=cls.kg)
        cls.eis_stiel = Ware.objects.create(name="Ware am Stiel", basiseinheit=cls.stueck)
        cls.cola = Ware.objects.create(name="Artikel C", basiseinheit=cls.stueck)

    def setUp(self):
        self.client.force_login(self.user)

    def absenden(self, **abweichend):
        daten = {
            "name": "Warenfamilie E",
            "beschreibung": "",
            "leiteinheit": "",
            "ware": [],
        }
        return self.client.post(reverse("warenfamilie_add"), daten | abweichend)

    def test_ohne_waren_genuegen_name_und_beschreibung(self):
        """The first product later determines the unit. Before that there is nothing to choose."""
        antwort = self.absenden(name="Warenfamilie G", beschreibung="Beispielbeschreibung")

        familie = Warenfamilie.objects.get(name="Warenfamilie G")
        self.assertRedirects(antwort, familie.get_absolute_url())
        self.assertIsNone(familie.leiteinheit)
        self.assertEqual(familie.anzahl_waren, 0)

    def test_waren_kommen_mit_und_bestimmen_die_einheit(self):
        self.absenden(name="Warenfamilie M", ware=[self.cola.pk], leiteinheit=self.stueck.pk)

        familie = Warenfamilie.objects.get(name="Warenfamilie M")
        self.cola.refresh_from_db()
        self.assertEqual(self.cola.warenfamilie, familie)
        self.assertEqual(familie.leiteinheit, self.stueck)

    def test_fremde_einheit_als_leiteinheit_fragt_nach_dem_faktor(self):
        """A sum in kilos over piece goods works, once every piece says what it weighs."""
        antwort = self.absenden(ware=[self.cola.pk], leiteinheit=self.kg.pk)

        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, f'name="faktor_{self.cola.pk}_MASSE"')
        self.assertFalse(Warenfamilie.objects.filter(sammelt_ignorierte=False).exists())

    def test_zwei_dimensionen_verlangen_den_faktor_der_ware(self):
        """The form asks for the missing factor itself, one field per product and dimension."""
        antwort = self.absenden(ware=[self.eis_lose.pk, self.eis_stiel.pk], leiteinheit=self.kg.pk)

        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Fehlende Umrechnungen")
        self.assertContains(antwort, "„Ware am Stiel“: g je Stk.")
        self.assertContains(antwort, f'name="faktor_{self.eis_stiel.pk}_MASSE"')
        self.assertContains(antwort, "Ohne diesen Faktor")
        self.assertFalse(Warenfamilie.objects.filter(sammelt_ignorierte=False).exists())

    def test_der_faktor_aus_dem_formular_landet_an_der_ware(self):
        self.absenden(
            ware=[self.eis_lose.pk, self.eis_stiel.pk],
            leiteinheit=self.kg.pk,
            **{f"faktor_{self.eis_stiel.pk}_MASSE": "125"},
        )

        familie = Warenfamilie.objects.get(name="Warenfamilie E")
        self.assertEqual(familie.anzahl_waren, 2)
        self.eis_stiel.refresh_from_db()
        self.assertEqual(self.eis_stiel.faktoren(), {Dimension.MASSE: Decimal("125")})
        self.assertEqual(familie.waren_ohne_faktor(), [])

    def test_mit_faktor_geht_es_durch(self):
        self.eis_stiel.umrechnungen_setzen({Dimension.MASSE: Decimal("125")})
        self.absenden(ware=[self.eis_lose.pk, self.eis_stiel.pk], leiteinheit=self.kg.pk)

        familie = Warenfamilie.objects.get(name="Warenfamilie E")
        self.assertEqual(familie.anzahl_waren, 2)
        self.assertEqual(familie.waren_ohne_faktor(), [])

    def test_detailseite_nennt_waren_ohne_faktor(self):
        """Past every check (an old backup, say) the page still says what is missing."""
        familie = Warenfamilie.objects.create(name="Warenfamilie E", leiteinheit=self.kg)
        Ware.objects.filter(pk__in=[self.eis_lose.pk, self.eis_stiel.pk]).update(
            warenfamilie=familie
        )

        antwort = self.client.get(familie.get_absolute_url())

        self.assertContains(antwort, "führen keine Umrechnung in die Leiteinheit")
        self.assertContains(antwort, "Ware am Stiel")
        self.assertEqual(familie.waren_ohne_faktor(), [self.eis_stiel])

    def test_zur_wahl_stehen_nur_waren_ohne_familie(self):
        andere = Warenfamilie.objects.create(name="Belegt", leiteinheit=self.stueck)
        self.cola.warenfamilie = andere
        self.cola.save()

        antwort = self.client.get(reverse("warenfamilie_add"))
        waehlbar = antwort.context["form"].fields["ware"].queryset

        self.assertNotIn(self.cola, waehlbar)
        self.assertIn(self.eis_lose, waehlbar)

    def test_ware_ohne_passenden_faktor_kommt_nicht_hinein(self):
        """Not past the product form either: the sum could not be formed."""
        familie = Warenfamilie.objects.create(name="Warenfamilie E", leiteinheit=self.kg)
        self.eis_lose.warenfamilie = familie
        self.eis_lose.save()
        ware = Ware(name="Neu am Stiel", basiseinheit=self.stueck, warenfamilie=familie)

        with self.assertRaises(ValidationError) as fehler:
            ware.full_clean()

        self.assertIn("Ohne „g je Stk.“", str(fehler.exception))

    def test_die_ware_behaelt_ihren_faktor_solange_die_familie_ihn_braucht(self):
        """Clearing the field in the product's form would leave a sum nobody can form."""
        familie = Warenfamilie.objects.create(name="Warenfamilie E", leiteinheit=self.kg)
        self.eis_stiel.umrechnungen_setzen({Dimension.MASSE: Decimal("125")})
        self.eis_stiel.warenfamilie = familie
        self.eis_stiel.save()

        form = WareForm(
            {
                "name": self.eis_stiel.name,
                "basiseinheit": self.stueck.pk,
                "warenfamilie": familie.pk,
            },
            instance=self.eis_stiel,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("nicht summieren", str(form.errors["warenfamilie"]))

    def test_die_erste_ware_bestimmt_die_einheit_einer_leeren_familie(self):
        familie = Warenfamilie.objects.create(name="Neu")

        familie.aufnehmen(self.eis_lose)

        familie.refresh_from_db()
        self.eis_lose.refresh_from_db()
        self.assertEqual(familie.leiteinheit, self.kg)
        self.assertEqual(self.eis_lose.warenfamilie, familie)

    def test_liste_zaehlt_die_waren(self):
        """The number comes from the database. It must not get in the property's way."""
        familie = Warenfamilie.objects.create(name="Warenfamilie E", leiteinheit=self.kg)
        self.eis_lose.warenfamilie = familie
        self.eis_lose.save()

        antwort = self.client.get(reverse("warenfamilie_list"))

        self.assertEqual(antwort.status_code, 200)
        # A list in the order of the tree, no queryset any more.
        gezaehlt = next(f for f in antwort.context["familien"] if f.pk == familie.pk)
        self.assertEqual(gezaehlt.warenzahl, 1)

    def test_familie_ohne_einheit_laesst_sich_ansehen(self):
        familie = Warenfamilie.objects.create(name="Warenfamilie G", beschreibung="Warenfamilie A")

        antwort = self.client.get(familie.get_absolute_url())

        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Einheit noch offen")

    def test_die_waren_stehen_in_zwei_spalten(self):
        """Left available, right included, and the whole row is the switch.

        The box sits in the label and is only invisible: that way a click on
        the row hits it too, and without JavaScript the form stays usable.
        """
        antwort = self.client.get(reverse("warenfamilie_add"))

        self.assertContains(antwort, 'data-warenliste="frei"')
        self.assertContains(antwort, 'data-warenliste="gewaehlt"')
        self.assertContains(antwort, 'class="wahlzeile"')
        # Every row carries its position. By it it finds its way back when deselected.
        self.assertContains(antwort, 'data-reihe="0"')

    def test_das_formular_stoesst_die_auswahlliste_an(self):
        """Tom Select shows its own copy of the choice.

        The script presets the unit of the first product in the <select>.
        Without this nudge nothing of that would be visible. The form does not
        notice (it checks the sent value), the person does.
        """
        antwort = self.client.get(reverse("warenfamilie_add"))

        self.assertContains(antwort, "js/warenfamilie_form.js")
        self.assertIn("tomselect", skript("warenfamilie_form"))
        self.assertIn("setValue", skript("warenfamilie_form"))

    def test_die_ware_nimmt_ihren_faktor_mit_hinaus(self):
        """The factor belongs to the product. Leaving the family does not touch it."""
        familie = Warenfamilie.objects.create(name="Warenfamilie E", leiteinheit=self.kg)
        Ware.objects.filter(pk__in=[self.eis_lose.pk, self.eis_stiel.pk]).update(
            warenfamilie=familie
        )
        self.eis_stiel.umrechnungen_setzen({Dimension.MASSE: Decimal("125")})

        antwort = self.client.post(
            reverse("warenfamilie_edit", args=[familie.pk]),
            {
                "name": "Warenfamilie E",
                "beschreibung": "",
                "leiteinheit": self.kg.pk,
                "ware": [self.eis_lose.pk],
            },
        )

        self.assertRedirects(antwort, familie.get_absolute_url())
        self.eis_stiel.refresh_from_db()
        self.assertIsNone(self.eis_stiel.warenfamilie)
        self.assertEqual(self.eis_stiel.faktoren(), {Dimension.MASSE: Decimal("125")})


class FilterDerWarenliste(AngemeldeteSeiten):
    """Product families and houses as multiple selection, applied only with "Apply"."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe A")
        cls.a, cls.b, cls.c = (Haus.objects.create(name=name) for name in "ABC")
        cls.limo = Warenfamilie.objects.create(name="Warenfamilie L")
        cls.eis = Warenfamilie.objects.create(name="Warenfamilie E")
        cls.kein = Warenfamilie.nicht_zaehlbar()

        nummern = iter(range(101001, 101100))

        def ware(name, familie, *haeuser):
            sammel = Ware.objects.create(name=name, basiseinheit=cls.stueck, warenfamilie=familie)
            for haus in haeuser:
                Hausartikel.objects.create(
                    haus=haus,
                    ware=sammel,
                    warengruppe=cls.gruppe,
                    artikelnummer=str(next(nummern)),
                    name=name,
                )
            return sammel

        cls.ab = ware("In A und B", cls.limo, cls.a, cls.b)
        cls.abc = ware("In A, B und C", cls.eis, cls.a, cls.b, cls.c)
        cls.nur_c = ware("Nur in C", None, cls.c)
        cls.pfand = ware("Artikel P", cls.kein, cls.a)

    def namen(self, **filter):
        antwort = self.client.get(reverse("ware_list"), filter)
        return sorted(ware.name for ware in antwort.context["ware"])

    def test_vorgabe_alle_familien_ausser_nicht_zaehlbar(self):
        self.assertEqual(self.namen(), ["In A und B", "In A, B und C", "Nur in C"])

    def test_familien_einzeln_mehrere_und_ohne(self):
        self.assertEqual(self.namen(familie=[self.limo.pk]), ["In A und B"])
        self.assertEqual(self.namen(familie=[self.eis.pk, "ohne"]), ["In A, B und C", "Nur in C"])
        self.assertEqual(self.namen(familie=[self.kein.pk]), ["Artikel P"])

    def test_mindestens_in_a_und_b_vielleicht_c(self):
        self.assertEqual(
            self.namen(**{f"haus_{self.a.pk}": "mit", f"haus_{self.b.pk}": "mit"}),
            ["In A und B", "In A, B und C"],
        )

    def test_genau_in_a_und_b_nicht_c(self):
        self.assertEqual(
            self.namen(
                **{
                    f"haus_{self.a.pk}": "mit",
                    f"haus_{self.b.pk}": "mit",
                    f"haus_{self.c.pk}": "ohne",
                }
            ),
            ["In A und B"],
        )

    def test_a_oder_b_nicht_c(self):
        """Nothing on "must": one of the other houses is enough."""
        self.assertEqual(self.namen(**{f"haus_{self.c.pk}": "ohne"}), ["In A und B"])

    def test_haus_im_sinne_des_statusfilters(self):
        Hausartikel.objects.get(ware=self.ab, haus=self.b).einstellen()
        mit_b = {f"haus_{self.b.pk}": "mit"}
        self.assertEqual(self.namen(**mit_b), ["In A, B und C"])
        self.assertEqual(self.namen(status="alle", **mit_b), ["In A und B", "In A, B und C"])

    def test_auswahl_wirkt_erst_mit_uebernehmen(self):
        """The multiple selections are exempt from the instant filter and have their own button."""
        html = self.client.get(reverse("ware_list")).content.decode()
        # Product family, product group and "Carried in".
        self.assertEqual(html.count('<div class="dropdown" data-sammelwahl>'), 3)
        self.assertEqual(html.count("data-uebernehmen>"), 3)
        self.assertIn("js/live_filter.js", html)
        self.assertIn('closest("[data-sammelwahl]")', skript("live_filter"))
        self.assertIn("Alle außer Nicht zählbar", html)

    def test_sortierpfeil_ist_in_jedem_kopf_reserviert(self):
        """Otherwise the sorted column would grow, and the table would jump."""
        html = self.client.get(reverse("ware_list")).content.decode()
        self.assertEqual(html.count("&#9650;") + html.count("&#9660;"), 5)
        # Invisible, not gone: the arrow of the unsorted headers keeps its place.
        self.assertEqual(html.count('class="small invisible"'), 4)
        self.assertIn("tabelle-fest", html)


class NichtZaehlbarInDerWarenfamilie(AngemeldeteSeiten):
    """Through the family's form in and out. The house articles follow."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.haus = Haus.objects.create(name="Kino West")
        cls.pfand = Ware.objects.create(name="Artikel P", basiseinheit=cls.stueck)
        cls.kg = Ware.objects.create(
            name="Posten G", basiseinheit=Basiseinheit.objects.get(name="kg")
        )
        cls.artikel = Hausartikel.objects.create(
            haus=cls.haus,
            ware=cls.pfand,
            warengruppe=Warengruppe.objects.create(name="Warengruppe J"),
            artikelnummer="901001",
            name="Artikel P",
        )

    def setUp(self):
        super().setUp()
        self.kein = Warenfamilie.nicht_zaehlbar()

    def absenden(self, waren):
        return self.client.post(
            reverse("warenfamilie_edit", args=[self.kein.pk]),
            {
                "name": self.kein.name,
                "beschreibung": "",
                "ware": [ware.pk for ware in waren],
                "umrechnungen-TOTAL_FORMS": "0",
                "umrechnungen-INITIAL_FORMS": "0",
                "umrechnungen-MIN_NUM_FORMS": "0",
                "umrechnungen-MAX_NUM_FORMS": "1000",
            },
        )

    def test_hinein_ohne_leiteinheit_und_hinaus(self):
        antwort = self.absenden([self.pfand, self.kg])
        self.assertRedirects(antwort, self.kein.get_absolute_url())
        self.artikel.refresh_from_db()
        self.kein.refresh_from_db()
        self.assertEqual(self.artikel.status, Artikelstatus.IGNORIERT)
        self.assertIsNone(self.kein.leiteinheit)

        self.absenden([self.kg])
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.status, Artikelstatus.AKTIV)


class WareFilterZaehlt(TestCase):
    def test_der_statusfilter_schraenkt_ein(self):
        """Otherwise with "discontinued only" the hint that filtering is on would be missing."""
        self.assertFalse(WareFilterForm({}).ist_eingeschraenkt)
        self.assertTrue(WareFilterForm({"status": "eingestellt"}).ist_eingeschraenkt)
