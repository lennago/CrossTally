"""House articles through the pages: partial indexes, status, packs and locations on creation."""

import re
from decimal import Decimal

from django.urls import reverse

from django_project.testing import AngemeldeteSeiten, monatsinventur, skript
from organisation.models import Bereich, Haus, Ort

from .models import (
    Artikelstatus,
    Basiseinheit,
    Behaelter,
    Dimension,
    Erwartung,
    Gebinde,
    Hausartikel,
    Rezepturartikel,
    Rezepturkomponente,
    Ware,
    Warenfamilie,
    Warengruppe,
)


class FormsetZeilen(AngemeldeteSeiten):
    """Adding rows, removing them and duplicates in the selection."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.cola = Ware.objects.create(name="Artikel C 1l", basiseinheit=cls.stueck)
        cls.becher = Ware.objects.create(name="Becher 0,4", basiseinheit=cls.stueck)
        cls.zaehl = Rezepturartikel.objects.create(name="Portion G", basiseinheit=cls.stueck)
        cls.komponente = Rezepturkomponente.objects.create(
            rezepturartikel=cls.zaehl, ware=cls.cola, menge_in_basiseinheit=Decimal("1")
        )

    def rezeptur(self, **zeilen):
        daten = {
            "name": self.zaehl.name,
            "basiseinheit": self.stueck.pk,
            "komponenten-TOTAL_FORMS": "2",
            "komponenten-INITIAL_FORMS": "1",
            "komponenten-MIN_NUM_FORMS": "0",
            "komponenten-MAX_NUM_FORMS": "1000",
            "komponenten-0-id": self.komponente.pk,
            "komponenten-0-ware": self.cola.pk,
            "komponenten-0-menge_in_basiseinheit": "1",
        }
        return daten | zeilen

    def test_entfernte_zeile_wird_geloescht(self):
        """The button ticks the delete box and empties the row."""
        antwort = self.client.post(
            reverse("rezepturartikel_edit", args=[self.zaehl.pk]),
            self.rezeptur(
                **{
                    "komponenten-0-ware": "",
                    "komponenten-0-menge_in_basiseinheit": "",
                    "komponenten-0-DELETE": "on",
                }
            ),
        )
        self.assertEqual(antwort.status_code, 302)
        self.assertFalse(self.zaehl.komponenten.exists())

    def test_leere_zusatzzeile_stoert_nicht(self):
        """A row added and removed right away is simply empty."""
        antwort = self.client.post(
            reverse("rezepturartikel_edit", args=[self.zaehl.pk]),
            self.rezeptur(
                **{
                    "komponenten-1-ware": "",
                    "komponenten-1-menge_in_basiseinheit": "",
                }
            ),
        )
        self.assertEqual(antwort.status_code, 302)
        self.assertEqual(self.zaehl.komponenten.count(), 1)

    def test_doppelter_ware_wird_abgewiesen(self):
        antwort = self.client.post(
            reverse("rezepturartikel_edit", args=[self.zaehl.pk]),
            self.rezeptur(
                **{
                    "komponenten-1-ware": self.cola.pk,
                    "komponenten-1-menge_in_basiseinheit": "2",
                }
            ),
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "nur einmal in der Rezeptur")
        self.assertEqual(self.zaehl.komponenten.count(), 1)

    def test_zweiter_ware_geht(self):
        antwort = self.client.post(
            reverse("rezepturartikel_edit", args=[self.zaehl.pk]),
            self.rezeptur(
                **{
                    "komponenten-1-ware": self.becher.pk,
                    "komponenten-1-menge_in_basiseinheit": "1",
                }
            ),
        )
        self.assertEqual(antwort.status_code, 302)
        self.assertEqual(self.zaehl.komponenten.count(), 2)


class StatusUeberKnoepfe(AngemeldeteSeiten):
    """Deactivating and reactivating go through the detail pages, not through ticks."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Ost2")
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G4")
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.sammel = Ware.objects.create(name="Artikel C 0,25", basiseinheit=cls.stueck)
        cls.artikel = Hausartikel.objects.create(
            haus=cls.haus,
            ware=cls.sammel,
            warengruppe=cls.gruppe,
            artikelnummer="601001",
            name="Artikel C klein",
        )
        cls.gebinde = Gebinde.objects.create(
            ware=cls.sammel, name="Kasten", faktor_in_basiseinheit=Decimal("24")
        )

    def status(self, status, **weiter):
        return self.client.post(
            reverse("hausartikel_status", args=[self.artikel.pk]), {"status": status, **weiter}
        )

    def test_hausartikel_inaktiv_setzen(self):
        antwort = self.status(Artikelstatus.INAKTIV)
        self.assertRedirects(antwort, self.artikel.get_absolute_url())
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.status, Artikelstatus.INAKTIV)
        self.assertIsNotNone(self.artikel.gueltig_bis)

    def test_hausartikel_einstellen_und_wieder_aktivieren(self):
        """Discontinued is not final. A slip can be taken back."""
        self.status(Artikelstatus.EINGESTELLT)
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.status, Artikelstatus.EINGESTELLT)
        self.status(Artikelstatus.AKTIV)
        self.artikel.refresh_from_db()
        self.assertTrue(self.artikel.aktiv)
        self.assertIsNone(self.artikel.gueltig_bis)

    def test_die_knoepfe_tragen_eine_marke_fuer_den_fokus(self):
        """The pressed one comes back disabled. The script moves the focus to its neighbour."""
        html = self.client.get(self.artikel.get_absolute_url()).content.decode()
        for status in (Artikelstatus.AKTIV, Artikelstatus.INAKTIV, Artikelstatus.EINGESTELLT):
            with self.subTest(status=status):
                self.assertIn(f'data-fokus="status-{self.artikel.pk}-{status}"', html)

    def test_reaktivieren_scheitert_am_nachfolger(self):
        """The number has been reassigned in the meantime. The partial index would not allow it."""
        self.artikel.einstellen()
        nachfolger = Hausartikel.objects.create(
            haus=self.haus,
            ware=Ware.objects.create(name="Artikel F 0,25", basiseinheit=self.stueck),
            warengruppe=self.gruppe,
            artikelnummer="601001",
            name="Artikel F klein",
        )
        antwort = self.client.post(
            reverse("hausartikel_status", args=[self.artikel.pk]),
            {"status": Artikelstatus.INAKTIV},
            follow=True,
        )
        self.assertContains(antwort, "führt schon 601001 „Artikel F klein“")
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.status, Artikelstatus.EINGESTELLT)
        self.assertTrue(Hausartikel.objects.get(pk=nachfolger.pk).aktiv)

    def test_nicht_zaehlbar_aus_der_zeile(self):
        self.status(Artikelstatus.IGNORIERT)
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.ware.warenfamilie, Warenfamilie.nicht_zaehlbar())
        self.assertEqual(self.artikel.status, Artikelstatus.IGNORIERT)

    def test_unbekannter_status_aendert_nichts(self):
        self.status("WEG")
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.status, Artikelstatus.AKTIV)

    def test_eingestellten_artikel_bearbeiten_kollidiert_nicht(self):
        """A discontinued row may carry the number of its successor."""
        self.artikel.einstellen()
        Hausartikel.objects.create(
            haus=self.haus,
            ware=Ware.objects.create(name="Artikel S 0,25", basiseinheit=self.stueck),
            warengruppe=self.gruppe,
            artikelnummer="601001",
            name="Artikel S klein",
        )
        antwort = self.client.post(
            reverse("hausartikel_edit", args=[self.artikel.pk]),
            {
                "artikelnummer": "601001",
                "name": "Artikel C klein, korrigiert",
                "ware": self.sammel.pk,
                "neuer_ware": "",
                "basiseinheit": "",
                "inventurart": monatsinventur().pk,
                "warengruppe": self.gruppe.pk,
                "neue_warengruppe": "",
                "ean": "",
                "gebinde-TOTAL_FORMS": "1",
                "gebinde-INITIAL_FORMS": "0",
                "gebinde-MIN_NUM_FORMS": "0",
                "gebinde-MAX_NUM_FORMS": "1000",
            },
        )
        self.assertEqual(antwort.status_code, 302)
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.name, "Artikel C klein, korrigiert")
        self.assertEqual(self.artikel.status, Artikelstatus.EINGESTELLT)

    def bearbeiten(self, **abweichend):
        daten = {
            "artikelnummer": self.artikel.artikelnummer,
            "name": self.artikel.name,
            "ware": self.sammel.pk,
            "neuer_ware": "",
            "basiseinheit": "",
            "inventurart": monatsinventur().pk,
            "warengruppe": self.gruppe.pk,
            "neue_warengruppe": "",
            "ean": "",
            "gebinde-TOTAL_FORMS": "0",
            "gebinde-INITIAL_FORMS": "0",
            "gebinde-MIN_NUM_FORMS": "0",
            "gebinde-MAX_NUM_FORMS": "1000",
        }
        return self.client.post(
            reverse("hausartikel_edit", args=[self.artikel.pk]), daten | abweichend
        )

    def test_nicht_zaehlbar_ueber_das_formular_und_zurueck(self):
        pfand = Ware.objects.create(
            name="Artikel P", basiseinheit=self.stueck, warenfamilie=Warenfamilie.nicht_zaehlbar()
        )
        antwort = self.bearbeiten(ware=pfand.pk)
        self.assertEqual(antwort.status_code, 302)
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.status, Artikelstatus.IGNORIERT)

        self.assertEqual(self.bearbeiten().status_code, 302)
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.status, Artikelstatus.AKTIV)

    def test_zurueckholen_scheitert_am_belegten_platz(self):
        """Taken up again means active, and that only works if the product is free."""
        self.artikel.ignorieren()
        fassbrause = Ware.objects.create(name="Artikel F 0,25", basiseinheit=self.stueck)
        Hausartikel.objects.create(
            haus=self.haus,
            ware=fassbrause,
            warengruppe=self.gruppe,
            artikelnummer="601002",
            name="Artikel F klein",
        )
        antwort = self.bearbeiten(ware=fassbrause.pk)
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "führt „Artikel F 0,25“ bereits")
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.status, Artikelstatus.IGNORIERT)

    def test_detailseite_eines_ignorierten_fuehrt_nicht_ins_leere(self):
        """No status buttons and no way to packs of a product that is none."""
        self.artikel.ignorieren()
        antwort = self.client.get(self.artikel.get_absolute_url())
        self.assertNotContains(antwort, reverse("hausartikel_status", args=[self.artikel.pk]))
        self.assertNotContains(antwort, reverse("ware_edit", args=[self.sammel.pk]))

    def test_familie_nicht_zaehlbar_laesst_sich_nicht_loeschen(self):
        kein = Warenfamilie.nicht_zaehlbar()
        adresse = reverse("warenfamilie_delete", args=[kein.pk])
        self.assertEqual(self.client.get(adresse).status_code, 404)
        self.assertEqual(self.client.post(adresse).status_code, 404)
        self.assertTrue(Warenfamilie.objects.filter(pk=kein.pk).exists())
        self.assertNotContains(self.client.get(kein.get_absolute_url()), adresse)

    def test_gebinde_umschalten(self):
        adresse = reverse("gebinde_umschalten", args=[self.gebinde.pk])
        antwort = self.client.post(adresse, {"aktiv": "0"})
        self.assertRedirects(antwort, self.sammel.get_absolute_url())
        self.gebinde.refresh_from_db()
        self.assertFalse(self.gebinde.aktiv)

        self.client.post(adresse, {"aktiv": "1"})
        self.gebinde.refresh_from_db()
        self.assertTrue(self.gebinde.aktiv)

    def test_ein_veralteter_klick_schaltet_nicht_zurueck(self):
        """Sent twice, from a stale tab: the pack stays inactive, and the message says so.

        Until 10/2026 the second click switched the pack on again.
        """
        adresse = reverse("gebinde_umschalten", args=[self.gebinde.pk])
        self.client.post(adresse, {"aktiv": "0"})

        antwort = self.client.post(adresse, {"aktiv": "0"}, follow=True)

        self.gebinde.refresh_from_db()
        self.assertFalse(self.gebinde.aktiv)
        self.assertContains(antwort, "ist schon inaktiv")

    def test_ohne_gewuenschten_zustand_aendert_sich_nichts(self):
        self.client.post(reverse("gebinde_umschalten", args=[self.gebinde.pk]))
        self.gebinde.refresh_from_db()
        self.assertTrue(self.gebinde.aktiv)

    def test_der_knopf_nennt_den_gewuenschten_zustand(self):
        seite = self.client.get(self.sammel.get_absolute_url()).content.decode()
        adresse = reverse("gebinde_umschalten", args=[self.gebinde.pk])
        self.assertRegex(seite, rf'formaction="{adresse}"\s+name="aktiv" value="0"')

    def test_gebinde_wird_beim_anlegen_aktiv(self):
        daten = {
            "name": "Artikel W 0,25",
            "basiseinheit": self.stueck.pk,
            "inventurart": monatsinventur().pk,
            "gebinde-TOTAL_FORMS": "1",
            "gebinde-INITIAL_FORMS": "0",
            "gebinde-MIN_NUM_FORMS": "0",
            "gebinde-MAX_NUM_FORMS": "1000",
            "gebinde-0-name": "Kasten",
            "gebinde-0-faktor_in_basiseinheit": "12",
        }
        self.client.post(reverse("ware_add"), daten)
        self.assertTrue(Ware.objects.get(name="Artikel W 0,25").gebinde.get().aktiv)


class WarengruppeBeimAnlegen(AngemeldeteSeiten):
    """The product group can be created along with the house article."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino West2")
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G5")
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)

    def daten(self, **abweichend):
        basis = {
            "artikelnummer": "101700",
            "name": "Ware P süß",
            "ware": "",
            "neuer_ware": "",
            "basiseinheit": self.stueck.pk,
            "inventurart": monatsinventur().pk,
            "warengruppe": "",
            "neue_warengruppe": "",
            "ean": "",
            "gebinde-TOTAL_FORMS": "1",
            "gebinde-INITIAL_FORMS": "0",
            "gebinde-MIN_NUM_FORMS": "0",
            "gebinde-MAX_NUM_FORMS": "1000",
            "orte-TOTAL_FORMS": "0",
            "orte-INITIAL_FORMS": "0",
            "orte-MIN_NUM_FORMS": "0",
            "orte-MAX_NUM_FORMS": "1000",
        }
        return basis | abweichend

    def anlegen(self, **abweichend):
        return self.client.post(
            reverse("hausartikel_add", args=[self.haus.pk]), self.daten(**abweichend)
        )

    def test_neue_warengruppe_entsteht_mit(self):
        antwort = self.anlegen(neue_warengruppe="Warengruppe S süß")
        self.assertEqual(antwort.status_code, 302)
        artikel = Hausartikel.objects.get(artikelnummer="101700")
        self.assertEqual(artikel.warengruppe.name, "Warengruppe S süß")

    def test_vorhandene_warengruppe_bleibt_moeglich(self):
        self.anlegen(warengruppe=self.gruppe.pk)
        self.assertEqual(Hausartikel.objects.get(artikelnummer="101700").warengruppe, self.gruppe)

    def test_beides_wird_abgewiesen(self):
        antwort = self.anlegen(warengruppe=self.gruppe.pk, neue_warengruppe="Warengruppe S süß")
        self.assertContains(antwort, "nicht beides")
        self.assertFalse(Hausartikel.objects.filter(artikelnummer="101700").exists())

    def test_keines_von_beiden_wird_abgewiesen(self):
        antwort = self.anlegen()
        self.assertContains(antwort, "braucht eine Bezeichnung")

    def test_vorhandener_name_verweist_auf_die_auswahl(self):
        antwort = self.anlegen(neue_warengruppe=self.gruppe.name)
        self.assertContains(antwort, "gibt es bereits")
        self.assertFalse(Warengruppe.objects.filter(name=self.gruppe.name).count() > 1)


class GebindeBeimHausartikel(AngemeldeteSeiten):
    """If a pack is missing, it arises when editing the house article."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Süd3")
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G6")
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.sammel = Ware.objects.create(name="Artikel B 0,33", basiseinheit=cls.stueck)
        cls.artikel = Hausartikel.objects.create(
            haus=cls.haus,
            ware=cls.sammel,
            warengruppe=cls.gruppe,
            artikelnummer="800001",
            name="Artikel B klein",
        )

    def bearbeiten(self, **abweichend):
        daten = {
            "artikelnummer": self.artikel.artikelnummer,
            "name": self.artikel.name,
            "ware": self.sammel.pk,
            "neuer_ware": "",
            "basiseinheit": "",
            "inventurart": monatsinventur().pk,
            "warengruppe": self.gruppe.pk,
            "neue_warengruppe": "",
            "ean": "",
            "gebinde-TOTAL_FORMS": "1",
            "gebinde-INITIAL_FORMS": "0",
            "gebinde-MIN_NUM_FORMS": "0",
            "gebinde-MAX_NUM_FORMS": "1000",
        }
        return self.client.post(
            reverse("hausartikel_edit", args=[self.artikel.pk]), daten | abweichend
        )

    def test_seite_zeigt_die_gebinde_des_wares(self):
        Gebinde.objects.create(
            ware=self.sammel, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        antwort = self.client.get(reverse("hausartikel_edit", args=[self.artikel.pk]))
        self.assertContains(antwort, "Kasten")

    def test_neues_gebinde_entsteht_am_ware(self):
        antwort = self.bearbeiten(
            **{"gebinde-0-name": "Sixpack", "gebinde-0-faktor_in_basiseinheit": "6"}
        )
        self.assertEqual(antwort.status_code, 302)
        gebinde = self.sammel.gebinde.get()
        self.assertEqual((gebinde.name, gebinde.faktor_in_basiseinheit), ("Sixpack", 6))

    def test_gebinde_ohne_menge_wird_abgewiesen(self):
        antwort = self.bearbeiten(**{"gebinde-0-name": "Sixpack"})
        self.assertContains(antwort, "Ohne Menge je Gebinde")
        self.assertFalse(self.sammel.gebinde.exists())

    def test_ein_vergebener_name_mit_anderer_menge_wird_gemeldet(self):
        """Until 10/2026 the second pack vanished without a word."""
        sixpack = Gebinde.objects.create(
            ware=self.sammel, name="Sixpack", faktor_in_basiseinheit=Decimal("6")
        )
        antwort = self.bearbeiten(
            **{"gebinde-0-name": "Sixpack", "gebinde-0-faktor_in_basiseinheit": "8"}
        )
        self.assertEqual(antwort.status_code, 302)
        self.assertContains(
            self.client.get(antwort["Location"]),
            f"„Sixpack“ steht bei „Artikel B 0,33“ schon für 6 {self.stueck.kurz}.",
        )
        self.assertEqual(list(self.sammel.gebinde.all()), [sixpack])


class BehaelterAmHausartikel(AngemeldeteSeiten):
    """The detail page of a house article shows and maintains the weighing of its product."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        kg = Basiseinheit.objects.get(name="kg")
        stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        haus = Haus.objects.create(name="Kino Ost8")
        gruppe = Warengruppe.objects.create(name="Kino8")
        cls.schale = Behaelter.objects.create(name="Schale", tara=Decimal("250"))
        cls.eimer = Behaelter.objects.create(name="Eimer", tara=Decimal("900"))

        cls.nachos = Ware.objects.create(name="Artikel N lose", basiseinheit=kg, abwiegbar=True)
        cls.cola = Ware.objects.create(name="Artikel C 0,33", basiseinheit=stueck)
        cls.gewogen = Hausartikel.objects.create(
            haus=haus,
            ware=cls.nachos,
            warengruppe=gruppe,
            artikelnummer="960001",
            name="Artikel N lose",
        )
        cls.gezaehlt = Hausartikel.objects.create(
            haus=haus,
            ware=cls.cola,
            warengruppe=gruppe,
            artikelnummer="960002",
            name="Artikel C 0,33",
        )

    # -- What the page shows -----------------------------------------------

    def test_abwiegen_steht_auf_der_seite(self):
        antwort = self.client.get(self.gewogen.get_absolute_url())
        self.assertContains(antwort, "ja, brutto in Gramm")

    def test_bei_gezaehlter_ware_steht_es_auch_da(self):
        antwort = self.client.get(self.gezaehlt.get_absolute_url())
        self.assertContains(antwort, "nur in Gebinden")

    def test_behaelterkarte_nur_beim_abwiegen(self):
        self.assertContains(self.client.get(self.gewogen.get_absolute_url()), "Übliche Behälter")
        self.assertNotContains(
            self.client.get(self.gezaehlt.get_absolute_url()), "Übliche Behälter"
        )

    def test_die_jetzige_wahl_ist_angehakt(self):
        self.nachos.behaelter.set([self.schale])
        wahl = dict(self.client.get(self.gewogen.get_absolute_url()).context["behaelterwahl"])
        self.assertTrue(wahl[self.schale])
        self.assertFalse(wahl[self.eimer])

    def test_ohne_behaelter_steht_nicht_zur_wahl(self):
        wahl = dict(self.client.get(self.gewogen.get_absolute_url()).context["behaelterwahl"])
        self.assertNotIn(Behaelter.ohne_behaelter(), wahl)

    # -- What saving causes -------------------------------------------------

    def test_speichern_setzt_die_behaelter(self):
        seite = self.gewogen.get_absolute_url()
        antwort = self.client.post(
            reverse("ware_behaelter", args=[self.nachos.pk]),
            {
                "behaelter": [self.schale.pk, self.eimer.pk],
                "weiter": seite,
            },
        )
        self.assertRedirects(antwort, seite)
        self.assertCountEqual(self.nachos.behaelter.all(), [self.schale, self.eimer])

    def test_leeres_formular_loest_alle(self):
        self.nachos.behaelter.set([self.schale])
        self.client.post(reverse("ware_behaelter", args=[self.nachos.pk]), {})
        self.assertEqual(self.nachos.behaelter.count(), 0)

    def test_nicht_abgewogene_ware_wird_abgewiesen(self):
        self.client.post(
            reverse("ware_behaelter", args=[self.cola.pk]),
            {"behaelter": [self.schale.pk]},
        )
        self.assertEqual(self.cola.behaelter.count(), 0)

    def test_ohne_behaelter_kommt_nicht_durch(self):
        """It applies everywhere. An assignment would be a sham decision."""
        self.client.post(
            reverse("ware_behaelter", args=[self.nachos.pk]),
            {"behaelter": [Behaelter.ohne_behaelter().pk]},
        )
        self.assertEqual(self.nachos.behaelter.count(), 0)

    def test_unsinnige_nummer_stoert_nicht(self):
        self.client.post(
            reverse("ware_behaelter", args=[self.nachos.pk]),
            {"behaelter": ["xyz", self.schale.pk]},
        )
        self.assertCountEqual(self.nachos.behaelter.all(), [self.schale])

    def test_fremdes_ziel_wird_verworfen(self):
        antwort = self.client.post(
            reverse("ware_behaelter", args=[self.nachos.pk]),
            {"weiter": "https://example.com/phish"},
        )
        self.assertRedirects(antwort, self.nachos.get_absolute_url())


class OrtswahlAmHausartikel(AngemeldeteSeiten):
    """The other side of the selection tab of the counting list.

    There one chooses the articles for a location, here the locations for an
    article. Switched is the same expectation through the same endpoint,
    only from the other direction and with another return target.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Ost9")
        cls.theke = Bereich.objects.create(name="Theke", haus=cls.haus)
        cls.lager = Bereich.objects.create(name="Lager", haus=cls.haus)
        cls.vorne = Ort.objects.create(name="Thekenfront", bereich=cls.theke)
        cls.fach = Ort.objects.create(name="Gefrierfach", bereich=cls.theke)
        cls.trocken = Ort.objects.create(name="Trockenlager", bereich=cls.lager)

        stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.artikel = Hausartikel.objects.create(
            haus=cls.haus,
            ware=Ware.objects.create(name="Artikel C 0,9", basiseinheit=stueck),
            warengruppe=Warengruppe.objects.create(name="Warengruppe H"),
            artikelnummer="940001",
            name="Artikel C 0,9",
        )

    def orte(self):
        return sorted(
            erwartung.ort.name
            for erwartung in Erwartung.objects.filter(artikel=self.artikel.artikel_ptr)
        )

    def test_seite_zeigt_alle_orte_des_hauses_nach_bereich(self):
        antwort = self.client.get(self.artikel.get_absolute_url())
        gliederung = {
            bereich.name: [ort.name for ort, _ in orte]
            for bereich, orte in antwort.context["ortswahl"]
        }
        self.assertEqual(
            gliederung,
            {"Lager": ["Trockenlager"], "Theke": ["Gefrierfach", "Thekenfront"]},
        )

    def test_seite_kennt_die_stellung_in_der_zaehlliste(self):
        Erwartung.objects.create(ort=self.vorne, artikel=self.artikel)
        antwort = self.client.get(self.artikel.get_absolute_url())
        stellung = {
            ort.name: (erwartung.sortierung if erwartung else None)
            for _, orte in antwort.context["ortswahl"]
            for ort, erwartung in orte
        }
        self.assertEqual(stellung, {"Thekenfront": 1, "Gefrierfach": None, "Trockenlager": None})

    def test_ort_hinzufuegen(self):
        antwort = self.client.post(
            reverse("erwartung_umschalten", args=[self.trocken.pk]),
            {
                "artikel": self.artikel.pk,
                "aktion": "aufnehmen",
                "weiter": self.artikel.get_absolute_url(),
            },
        )
        self.assertRedirects(antwort, self.artikel.get_absolute_url())
        self.assertEqual(self.orte(), ["Trockenlager"])

    def test_ort_wieder_entfernen(self):
        Erwartung.objects.create(ort=self.vorne, artikel=self.artikel)
        self.client.post(
            reverse("erwartung_umschalten", args=[self.vorne.pk]),
            {
                "artikel": self.artikel.pk,
                "aktion": "entfernen",
                "weiter": self.artikel.get_absolute_url(),
            },
        )
        self.assertEqual(self.orte(), [])

    def test_die_knoepfe_sagen_was_sie_wollen(self):
        """A stale click changes nothing (until 10/2026 the button only toggled)."""
        Erwartung.objects.create(ort=self.vorne, artikel=self.artikel)
        html = self.client.get(self.artikel.get_absolute_url()).content.decode()
        # One button per location of the house: Thekenfront, Gefrierfach, Trockenlager.
        self.assertEqual(
            sorted(re.findall(r'name="aktion"\s+value="(\w+)"', html)),
            ["aufnehmen", "aufnehmen", "entfernen"],
        )

    def test_fremdes_haus_steht_nicht_zur_wahl(self):
        fremd = Ort.objects.create(
            name="Theke",
            bereich=Bereich.objects.create(
                name="Theke", haus=Haus.objects.create(name="Kino Süd9")
            ),
        )
        antwort = self.client.get(self.artikel.get_absolute_url())
        alle = [ort for _, orte in antwort.context["ortswahl"] for ort, _ in orte]
        self.assertNotIn(fremd, alle)

    def test_eingestellter_artikel_kommt_nirgends_neu_hinzu(self):
        """'Erwartung.clean()' would reject it. The button must not create it at all."""
        self.artikel.einstellen()
        self.client.post(
            reverse("erwartung_umschalten", args=[self.fach.pk]),
            {
                "artikel": self.artikel.pk,
                "aktion": "aufnehmen",
                "weiter": self.artikel.get_absolute_url(),
            },
        )
        self.assertEqual(self.orte(), [])

    def test_inaktiver_artikel_laesst_sich_noch_entfernen(self):
        Erwartung.objects.create(ort=self.vorne, artikel=self.artikel)
        self.artikel.deaktivieren()
        self.client.post(
            reverse("erwartung_umschalten", args=[self.vorne.pk]),
            {
                "artikel": self.artikel.pk,
                "aktion": "entfernen",
                "weiter": self.artikel.get_absolute_url(),
            },
        )
        self.assertEqual(self.orte(), [])

    def test_knoepfe_wirken_ohne_seitenneuladen(self):
        html = self.client.get(self.artikel.get_absolute_url()).content.decode()
        self.assertIn("data-liste", html)
        self.assertIn("data-sofort", html)
        self.assertIn(reverse("erwartung_umschalten", args=[self.vorne.pk]), html)


class StatusFilter(AngemeldeteSeiten):
    """Lists show by default what the house carries **today**.

    Discontinued is history: it hangs on old inventory results and should
    stay findable, but not clog every list.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Ost")
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)

        cls.laufend = Ware.objects.create(name="Artikel C", basiseinheit=cls.stueck)
        cls.inaktiv = Ware.objects.create(name="Artikel B", basiseinheit=cls.stueck)
        cls.ohne = Ware.objects.create(name="Frisch angelegt", basiseinheit=cls.stueck)

        Hausartikel.objects.create(
            haus=cls.haus,
            ware=cls.laufend,
            warengruppe=cls.gruppe,
            artikelnummer="101001",
            name="Artikel C 0,33",
        )
        alt = Hausartikel.objects.create(
            haus=cls.haus,
            ware=cls.inaktiv,
            warengruppe=cls.gruppe,
            artikelnummer="101002",
            name="Artikel B alt",
        )
        alt.deaktivieren()

    def namen(self, **filter):
        antwort = self.client.get(reverse("ware_list"), filter)
        return sorted(ware.name for ware in antwort.context["ware"])

    def test_ware_ohne_angabe_die_gefuehrten(self):
        self.assertEqual(self.namen(), ["Artikel B", "Artikel C", "Frisch angelegt"])
        self.assertEqual(self.namen(status="aktiv"), ["Artikel C", "Frisch angelegt"])

    def test_ware_eingestellt_erst_wenn_alle_es_sind(self):
        self.assertEqual(self.namen(status="eingestellt"), [])
        Hausartikel.objects.get(artikelnummer="101002").einstellen()
        self.assertEqual(self.namen(status="eingestellt"), ["Artikel B"])
        self.assertEqual(self.namen(), ["Artikel C", "Frisch angelegt"])

    def test_ware_nur_inaktive(self):
        self.assertEqual(self.namen(status="inaktiv"), ["Artikel B"])

    def test_ware_alle(self):
        self.assertEqual(self.namen(status="alle"), ["Artikel B", "Artikel C", "Frisch angelegt"])

    def test_ware_ohne_hausartikel_bleibt_sichtbar(self):
        """Otherwise it would vanish at the very moment someone creates it."""
        self.assertIn("Frisch angelegt", self.namen())
        self.assertNotIn("Frisch angelegt", self.namen(status="inaktiv"))

    def test_hausartikelliste_ohne_angabe_die_gefuehrten(self):
        antwort = self.client.get(reverse("hausartikel_list", args=[self.haus.pk]))
        self.assertEqual(
            sorted(artikel.artikelnummer for artikel in antwort.context["hausartikel"]),
            ["101001", "101002"],
        )


class GebindereihenfolgeAmHausartikel(AngemeldeteSeiten):
    """The order of the packs belongs to the product and is sorted from the house too."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Nord")
        gruppe = Warengruppe.objects.create(name="Warengruppe G")
        stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.ware = Ware.objects.create(name="Artikel C 0,33", basiseinheit=stueck)
        cls.artikel = Hausartikel.objects.create(
            haus=cls.haus,
            ware=cls.ware,
            warengruppe=gruppe,
            artikelnummer="101001",
            name="Artikel C klein",
        )
        cls.kasten = Gebinde.objects.create(
            ware=cls.ware, name="Kasten", faktor_in_basiseinheit=Decimal("24")
        )
        cls.flasche = Gebinde.objects.create(
            ware=cls.ware, name="Flasche", faktor_in_basiseinheit=Decimal("1")
        )

    def reihenfolge(self):
        return list(self.ware.gebinde.order_by("sortierung").values_list("name", flat=True))

    def test_die_seite_traegt_die_liste_zum_ziehen(self):
        html = self.client.get(self.artikel.get_absolute_url()).content.decode()
        for stueck in (
            reverse("gebinde_sortieren", args=[self.ware.pk]),
            "data-sortierbar",
            "data-griff",
            "data-reihenfolge-speichern",
            "js/sortable.js",
            "Die Reihenfolge gehört der Ware. Sie gilt in jedem Haus",
        ):
            with self.subTest(stueck=stueck):
                self.assertIn(stueck, html)

    def test_der_knopf_sagt_nur_bei_einer_aenderung_speichern(self):
        """Two states in the script: changed is green, in step with the server is outlined."""
        quelle = skript("sortable")
        self.assertIn('knopf.classList.toggle("btn-success", geaendert)', quelle)
        self.assertIn('knopf.classList.toggle("btn-outline-success", !geaendert)', quelle)
        self.assertIn("knopf.textContent = geaendert ? SPEICHERN : GESPEICHERT", quelle)

    def test_aendern_springt_zu_den_gebinden_der_ware(self):
        html = self.client.get(self.artikel.get_absolute_url()).content.decode()
        self.assertIn(reverse("ware_edit", args=[self.ware.pk]) + "#gebinde", html)
        bearbeiten = self.client.get(reverse("ware_edit", args=[self.ware.pk]))
        self.assertContains(bearbeiten, 'id="gebinde"')

    def test_ein_pfeil_fuehrt_zurueck_zum_hausartikel(self):
        """Without that the answer would be the product's page, and the list would be gone."""
        seite = self.artikel.get_absolute_url()
        antwort = self.client.post(
            reverse("gebinde_sortieren", args=[self.ware.pk]),
            {"runter": str(self.kasten.pk), "weiter": seite},
            headers={"X-Requested-With": "fetch"},
        )
        self.assertRedirects(antwort, seite)
        self.assertEqual(self.reihenfolge(), ["Flasche", "Kasten"])

    def test_die_gezogene_reihenfolge_wird_im_hintergrund_gespeichert(self):
        antwort = self.client.post(
            reverse("gebinde_sortieren", args=[self.ware.pk]),
            {
                "reihenfolge": [str(self.flasche.pk), str(self.kasten.pk)],
                "weiter": self.artikel.get_absolute_url(),
            },
            headers={"X-Requested-With": "fetch"},
        )
        self.assertEqual(antwort.status_code, 204)
        self.assertEqual(self.reihenfolge(), ["Flasche", "Kasten"])

    def test_inaktiv_setzen_bleibt_auf_der_seite(self):
        seite = self.artikel.get_absolute_url()
        antwort = self.client.post(
            reverse("gebinde_umschalten", args=[self.kasten.pk]), {"weiter": seite, "aktiv": "0"}
        )
        self.assertRedirects(antwort, seite)
        self.kasten.refresh_from_db()
        self.assertFalse(self.kasten.aktiv)

    def test_fremdes_ziel_wird_verworfen(self):
        antwort = self.client.post(
            reverse("gebinde_sortieren", args=[self.ware.pk]),
            {"runter": str(self.kasten.pk), "weiter": "https://example.org/"},
        )
        self.assertRedirects(antwort, self.ware.get_absolute_url())

    def test_die_seite_der_ware_bleibt_wie_sie_war(self):
        html = self.client.get(self.ware.get_absolute_url()).content.decode()
        self.assertIn("data-sortierbar", html)
        self.assertIn("Diese Reihenfolge gilt beim Zählen an jedem Ort.", html)
        self.assertNotIn("Die Reihenfolge gehört der Ware.", html)

    def test_was_keine_ware_ist_hat_nichts_zu_sortieren(self):
        self.artikel.ignorieren()
        html = self.client.get(self.artikel.get_absolute_url()).content.decode()
        self.assertNotIn(reverse("gebinde_sortieren", args=[self.ware.pk]), html)
        self.assertNotIn("data-sortierbar", html)
