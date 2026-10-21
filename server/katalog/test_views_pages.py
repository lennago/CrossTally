"""Open every page of the catalog once, and what all lists share.

'SeitenRendern' catches typos in templates and missing context names that
would otherwise only show up in the browser. Plus paging, breadcrumbs,
instant filters, the column headers and the buttons in the rows.
"""

import re
from decimal import Decimal

from django.urls import reverse

from django_project.form_fields import wahlmenge
from django_project.testing import (
    AngemeldeteSeiten,
    bereich,
    jahresinventur,
    monatsinventur,
    ware_zusammenfuehren,
)
from organisation.models import Haus, Ort

from .forms import RezepturartikelForm, WareForm
from .models import (
    QUELLE_EIGEN,
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


class SeitenRendern(AngemeldeteSeiten):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Ost")
        cls.ort = Ort.objects.create(name="Theke", bereich=bereich(cls.haus))
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.gramm = Basiseinheit.objects.get(name="g")
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        cls.sammel = Ware.objects.create(name="Artikel C 0,33", basiseinheit=cls.stueck)
        cls.sammel2 = Ware.objects.create(name="Artikel C 033", basiseinheit=cls.stueck)
        Gebinde.objects.create(ware=cls.sammel, name="Kasten", faktor_in_basiseinheit=Decimal("20"))
        cls.nachos = Ware.objects.create(name="Artikel N", basiseinheit=cls.gramm, abwiegbar=True)
        cls.grund = Hausartikel.objects.create(
            haus=cls.haus,
            ware=cls.sammel,
            warengruppe=cls.gruppe,
            artikelnummer="101001",
            name="Artikel C 0,33 Flasche",
        )
        cls.zaehl = Rezepturartikel.objects.create(name="Portion N", basiseinheit=cls.stueck)
        Rezepturkomponente.objects.create(
            rezepturartikel=cls.zaehl,
            ware=cls.sammel,
            menge_in_basiseinheit=Decimal("1"),
        )
        cls.erwartung = Erwartung.objects.create(ort=cls.ort, artikel=cls.grund)

    def test_die_hilfe_erklaert_nur_was_die_seite_hat(self):
        """A help section for a button or box the page lacks is left out (until 10/2026)."""
        kein = Warenfamilie.nicht_zaehlbar()
        familie = Warenfamilie.objects.create(name="Warenfamilie R")
        abschnitt = "<h3>Neue Unterfamilie</h3>"
        self.assertNotContains(self.client.get(kein.get_absolute_url()), abschnitt, html=True)
        self.assertContains(self.client.get(familie.get_absolute_url()), abschnitt, html=True)
        # Counted in pieces, not weighed: neither scale nor containers.
        seite = self.client.get(self.zaehl.get_absolute_url())
        self.assertNotContains(seite, "<h3>Wiegen</h3>", html=True)
        seite = self.client.get(self.grund.get_absolute_url())
        self.assertNotContains(seite, "<h3>Behälter beim Wiegen</h3>", html=True)
        # Deleting a type asks for a successor, and its help says what follows.
        art = monatsinventur()
        seite = self.client.get(reverse("inventurart_delete", args=[art.pk]))
        self.assertContains(seite, "<h3>Eine Inventurart löschen</h3>", html=True)

    def test_die_ean_verspricht_kein_scannen(self):
        """There is no scanning in the app, and it does not get the EAN (until 10/2026).

        The text stands on the model, so the administration says the same.
        """
        for adresse in (
            reverse("hausartikel_edit", args=[self.grund.pk]),
            reverse("admin:katalog_hausartikel_change", args=[self.grund.pk]),
        ):
            with self.subTest(adresse=adresse):
                seite = self.client.get(adresse)
                self.assertContains(
                    seite, "Die Suche in den Listen findet den Artikel auch darüber."
                )
                self.assertNotContains(seite, "Scannen")

    def anzahl_im_kopf(self, adresse) -> str:
        """The count next to the heading of a list."""
        html = self.client.get(adresse).content.decode()
        treffer = re.search(r'data-ersetzen="anzahl">\s*([^<]*?)\s*</span>', html)
        assert treffer is not None
        return re.sub(r"\s+", " ", treffer.group(1))

    def test_die_koepfe_zaehlen_was_die_liste_zeigt(self):
        """Products as products, prepared items as prepared items, in both languages.

        Until 10/2026 the product list counted "Artikel", and English read "73
        article(s)", "1 Article" and "7 item(s)".
        """
        waren = reverse("ware_list")
        artikel = reverse("hausartikel_list", args=[self.haus.pk])
        rezepturartikel = reverse("rezepturartikel_list")
        self.assertRegex(self.anzahl_im_kopf(waren), r"^\d+ Waren$")
        self.assertEqual(self.anzahl_im_kopf(artikel), "1 Artikel")
        self.assertEqual(self.anzahl_im_kopf(rezepturartikel), "1 Artikel")
        self.user.sprache = "en"
        self.user.save(update_fields=["sprache"])
        self.assertRegex(self.anzahl_im_kopf(waren), r"^\d+ products$")
        self.assertEqual(self.anzahl_im_kopf(artikel), "1 article")
        self.assertEqual(self.anzahl_im_kopf(rezepturartikel), "1 prepared item")

    def test_alle_seiten(self):
        adressen = [
            reverse("haus_list"),
            reverse("haus_add"),
            reverse("haus_detail", args=[self.haus.pk]),
            reverse("haus_edit", args=[self.haus.pk]),
            reverse("haus_delete", args=[self.haus.pk]),
            reverse("ort_detail", args=[self.ort.pk]),
            reverse("ort_edit", args=[self.ort.pk]),
            reverse("ort_delete", args=[self.ort.pk]),
            reverse("ware_list"),
            reverse("ware_list") + f"?q=artikel+c&haus={self.haus.pk}&sortierung=einheit",
            reverse("ware_add"),
            reverse("ware_detail", args=[self.sammel.pk]),
            reverse("ware_edit", args=[self.sammel.pk]),
            reverse("ware_delete", args=[self.sammel.pk]),
            reverse("ware_merge", args=[self.sammel.pk]),
            reverse("hausartikel_list", args=[self.haus.pk]),
            reverse("hausartikel_list", args=[self.haus.pk]) + "?q=artikel+c&sortierung=geaendert",
            reverse("hausartikel_add", args=[self.haus.pk]),
            reverse("hausartikel_detail", args=[self.grund.pk]),
            reverse("hausartikel_edit", args=[self.grund.pk]),
            reverse("hausartikel_delete", args=[self.grund.pk]),
            reverse("hausartikel_import", args=[self.haus.pk]),
            reverse("warengruppe_list"),
            reverse("warengruppe_add"),
            reverse("warengruppe_detail", args=[self.gruppe.pk]),
            reverse("warengruppe_edit", args=[self.gruppe.pk]),
            reverse("warengruppe_delete", args=[self.gruppe.pk]),
            reverse("rezepturartikel_list"),
            reverse("rezepturartikel_add"),
            reverse("rezepturartikel_detail", args=[self.zaehl.pk]),
            reverse("rezepturartikel_edit", args=[self.zaehl.pk]),
            reverse("rezepturartikel_delete", args=[self.zaehl.pk]),
            reverse("zaehlliste", args=[self.ort.pk]),
        ]
        for adresse in adressen:
            with self.subTest(adresse=adresse):
                self.assertEqual(self.client.get(adresse).status_code, 200)

    def test_langer_warengruppenname_wird_im_kopf_gekuerzt(self):
        """A long name pushed the button row below the title.

        Only the display is shortened. The full name stays readable in the tooltip.
        """
        gruppe = Warengruppe.objects.create(
            name="Beispielwarengruppe mit einem sehr langen Namen samt Zusatz"
        )
        antwort = self.client.get(reverse("warengruppe_detail", args=[gruppe.pk]))
        self.assertContains(antwort, '<div class="kopf-titel">')
        self.assertContains(
            antwort,
            f'<span class="text-truncate" title="{gruppe.name}">{gruppe.name}</span>',
            html=True,
        )

    def _zaehlliste_post(self, *erwartungen, **abweichend):
        """What the browser submits: management data, rows and the order.

        The order rides along as a hidden field per row, on ordinary saving too.
        Exactly on that the saving of the packs once broke, which is why it is
        always in here.
        """
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

    def test_sortieren_per_pfeil(self):
        zweite = Erwartung.objects.create(ort=self.ort, artikel=self.zaehl)
        antwort = self.client.post(
            reverse("zaehlliste", args=[self.ort.pk]),
            self._zaehlliste_post(self.erwartung, zweite, runter=str(self.erwartung.pk)),
        )
        self.assertEqual(antwort.status_code, 302)
        self.assertEqual(
            list(
                Erwartung.objects.filter(ort=self.ort)
                .order_by("sortierung")
                .values_list("pk", flat=True)
            ),
            [zweite.pk, self.erwartung.pk],
        )

    def test_sortieren_per_ziehen(self):
        """Dragging happens in the browser. Submitted is the row sequence of the DOM.

        The formset rows keep their number in the process. Only the hidden
        'reihenfolge' values are in the new sequence.
        """
        zweite = Erwartung.objects.create(ort=self.ort, artikel=self.zaehl)
        kasten = self.sammel.gebinde.get()
        self.client.post(
            reverse("zaehlliste", args=[self.ort.pk]),
            self._zaehlliste_post(
                self.erwartung,
                zweite,
                reihenfolge=[str(zweite.pk), str(self.erwartung.pk)],
                # "Save" insists on a valid list, and a row needs a pack.
                **{f"zeilen-0-gebinde_{kasten.pk}": "on"},
            ),
        )
        self.assertEqual(
            list(
                Erwartung.objects.filter(ort=self.ort)
                .order_by("sortierung")
                .values_list("pk", flat=True)
            ),
            [zweite.pk, self.erwartung.pk],
        )

    def test_speichern_steht_einmal_in_der_fussleiste(self):
        """With long lists one should not have to scroll to the end first.

        There used to be a second button in the header for that. Since the footer
        bar sticks, "Save" is always visible anyway, and two of them would be one
        too many.
        """
        html = self.client.get(reverse("zaehlliste", args=[self.ort.pk])).content.decode()
        self.assertEqual(html.count(">Speichern<"), 1)
        self.assertIn('class="fussleiste ', html)
        self.assertNotIn('form="zaehlliste-form"', html)

    def test_ohne_zeilen_kein_speichernknopf(self):
        """It otherwise pointed to a form the page does not even contain."""
        Erwartung.objects.filter(ort=self.ort).delete()
        html = self.client.get(reverse("zaehlliste", args=[self.ort.pk])).content.decode()
        self.assertNotIn(">Speichern<", html)

    def test_zaehlliste_zeigt_den_warennamen(self):
        """As in the counting app: the product, not the house's record."""
        antwort = self.client.get(reverse("zaehlliste", args=[self.ort.pk]))

        self.assertContains(antwort, self.sammel.name)
        # The house's designation stands small next to it because it deviates.
        self.assertContains(antwort, self.grund.name)

    def test_warenname_und_hausname_am_artikel(self):
        """Both are on the article. Template and API ask the same."""
        self.assertEqual(self.grund.warenname, "Artikel C 0,33")
        self.assertEqual(self.grund.name_im_haus, "Artikel C 0,33 Flasche")
        # A recipe article belongs to no house.
        self.assertEqual(self.zaehl.warenname, "Portion N")
        self.assertEqual(self.zaehl.name_im_haus, "")

    def test_im_auswahlreiter_kein_speichernknopf(self):
        html = self.client.get(
            reverse("zaehlliste", args=[self.ort.pk]), {"reiter": "auswahl"}
        ).content.decode()
        self.assertNotIn(">Speichern<", html)

    def test_suche_findet_auch_rezepturartikel(self):
        """Whoever types the name searches for the article, not the construction of the row."""
        antwort = self.client.get(
            reverse("zaehlliste", args=[self.ort.pk]), {"reiter": "auswahl", "q": "portion n"}
        )
        self.assertContains(antwort, "Portion N")
        # The house article does not fit and stays out.
        self.assertNotContains(antwort, "Artikel C 0,33 Flasche")

    def test_suche_ohne_treffer_sagt_es_auch_bei_den_rezepturartikeln(self):
        """An empty block would look as if there were no recipe articles here at all."""
        antwort = self.client.get(
            reverse("zaehlliste", args=[self.ort.pk]), {"reiter": "auswahl", "q": "gibtesnicht"}
        )
        self.assertNotContains(antwort, "Portion N")
        self.assertContains(antwort, "Kein Rezepturartikel passt zur Suche")

    def test_ohne_suche_stehen_alle_rezepturartikel_da(self):
        antwort = self.client.get(reverse("zaehlliste", args=[self.ort.pk]), {"reiter": "auswahl"})
        self.assertContains(antwort, "Portion N")
        self.assertNotContains(antwort, "Kein Rezepturartikel passt zur Suche")

    def test_speichern_behaelt_die_gebinde(self):
        """The form always sends the order along. It must not displace the saving
        of the packs."""
        gebinde = self.sammel.gebinde.get()
        antwort = self.client.post(
            reverse("zaehlliste", args=[self.ort.pk]),
            self._zaehlliste_post(
                self.erwartung,
                **{
                    f"zeilen-0-gebinde_{gebinde.pk}": "on",
                    f"zeilen-0-menge_{gebinde.pk}": "3",
                },
            ),
        )
        self.assertRedirects(antwort, self.ort.get_absolute_url())
        vorgabe = self.erwartung.gebinde_vorgaben.get()
        self.assertEqual((vorgabe.gebinde, vorgabe.standard_menge), (gebinde, Decimal("3")))

    def test_pfeil_speichert_die_gebinde_mit(self):
        """An arrow click in the middle of work must not discard inputs."""
        zweite = Erwartung.objects.create(ort=self.ort, artikel=self.zaehl)
        gebinde = self.sammel.gebinde.get()
        self.client.post(
            reverse("zaehlliste", args=[self.ort.pk]),
            self._zaehlliste_post(
                self.erwartung,
                zweite,
                runter=str(self.erwartung.pk),
                **{f"zeilen-0-gebinde_{gebinde.pk}": "on"},
            ),
        )
        self.assertEqual(self.erwartung.gebinde_vorgaben.get().gebinde, gebinde)
        self.assertEqual(
            list(
                Erwartung.objects.filter(ort=self.ort)
                .order_by("sortierung")
                .values_list("pk", flat=True)
            ),
            [zweite.pk, self.erwartung.pk],
        )

    def test_zusammenfuehren(self):
        antwort = ware_zusammenfuehren(
            self.client, reverse("ware_merge", args=[self.sammel.pk]), {"quelle": self.sammel2.pk}
        )
        self.assertRedirects(antwort, self.sammel.get_absolute_url())
        self.assertFalse(Ware.objects.filter(pk=self.sammel2.pk).exists())

    def _gefundene_nummern(self, parameter):
        antwort = self.client.get(reverse("hausartikel_list", args=[self.haus.pk]), parameter)
        return [artikel.artikelnummer for artikel in antwort.context["hausartikel"]]

    def test_hausartikelliste_sucht_ueber_alles_was_in_der_zeile_steht(self):
        """Number, designation, product and product group: whatever someone is looking for."""
        for parameter in (
            {"q": "101001"},
            {"q": "flasche"},
            {"q": "Artikel C 0,33"},
            {"q": "warengruppe g"},
        ):
            with self.subTest(parameter=parameter):
                self.assertEqual(self._gefundene_nummern(parameter), ["101001"])
        self.assertEqual(self._gefundene_nummern({"q": "gibtesnicht"}), [])

    def test_hausartikelliste_sortiert_nach_der_ware(self):
        """The same product stands together, even if the houses name it differently."""
        # Smaller number, but the product comes later alphabetically: only that
        # way the test says something. Otherwise every sort would happen to agree.
        Hausartikel.objects.create(
            haus=self.haus,
            ware=self.sammel2,  # "Artikel C 033", i.e. after "Artikel C 0,33"
            warengruppe=self.gruppe,
            artikelnummer="101000",
            name="Anders benannt",
        )
        self.assertEqual(self._gefundene_nummern({"sortierung": "ware"}), ["101001", "101000"])
        self.assertEqual(self._gefundene_nummern({"sortierung": "nummer"}), ["101000", "101001"])

    def test_hausartikelliste_zeigt_nur_das_eigene_haus(self):
        anderes = Haus.objects.create(name="Kino Süd")
        Hausartikel.objects.create(
            haus=anderes,
            ware=self.sammel,
            warengruppe=self.gruppe,
            artikelnummer="200002",
            name="Artikel C Kino Süd",
        )
        self.assertEqual(self._gefundene_nummern({}), ["101001"])

    def test_hausartikelliste_filtert_nach_status_und_warengruppe(self):
        """Without an entry the carried ones: active and inactive."""
        self.grund.deaktivieren()
        self.assertEqual(self._gefundene_nummern({}), ["101001"])
        self.assertEqual(self._gefundene_nummern({"status": "aktiv"}), [])
        self.assertEqual(self._gefundene_nummern({"status": "inaktiv"}), ["101001"])
        self.assertEqual(self._gefundene_nummern({"status": "alle"}), ["101001"])
        self.assertEqual(
            self._gefundene_nummern({"status": "alle", "warengruppe": self.gruppe.pk}),
            ["101001"],
        )

    def test_hausartikelliste_uebergeht_unsinnigen_filter(self):
        """A broken parameter must not drag the other filters down."""
        self.assertEqual(
            self._gefundene_nummern({"q": "artikel c", "warengruppe": "99999"}), ["101001"]
        )

    def _leeres_formset(self, prefix, extra=1):
        return {
            f"{prefix}-TOTAL_FORMS": str(extra),
            f"{prefix}-INITIAL_FORMS": "0",
            f"{prefix}-MIN_NUM_FORMS": "0",
            f"{prefix}-MAX_NUM_FORMS": "1000",
        }

    def test_ware_anlegen_ohne_gebinde(self):
        daten = {
            "name": "Artikel F 0,33",
            "basiseinheit": self.stueck.pk,
            "inventurart": "",
        } | self._leeres_formset("gebinde")
        antwort = self.client.post(reverse("ware_add"), daten)
        self.assertEqual(antwort.status_code, 302)
        self.assertTrue(Ware.objects.filter(name="Artikel F 0,33").exists())

    def test_ware_anlegen_mit_gebinde(self):
        daten = (
            {
                "name": "Artikel S 0,33",
                "basiseinheit": self.stueck.pk,
                "inventurart": "",
            }
            | self._leeres_formset("gebinde")
            | {"gebinde-0-name": "Kasten", "gebinde-0-faktor_in_basiseinheit": "24"}
        )
        self.client.post(reverse("ware_add"), daten)
        neu = Ware.objects.get(name="Artikel S 0,33")
        self.assertEqual(neu.gebinde.get().name, "Kasten")

    def _anlegen_daten(self, **abweichend):
        """The creation form including its three sections."""
        daten = {
            "artikelnummer": "101002",
            "name": "Artikel F Flasche",
            "ware": self.sammel2.pk,
            "neuer_ware": "",
            "basiseinheit": "",
            "inventurart": "",
            "warengruppe": self.gruppe.pk,
            "neue_warengruppe": "",
            "ean": "",
        }
        daten |= self._leeres_formset("gebinde", extra=3)
        daten |= self._leeres_formset("orte", extra=1) | {"orte-0-ort": self.ort.pk}
        return daten | abweichend

    def test_hausartikel_anlegen_im_haus(self):
        antwort = self.client.post(
            reverse("hausartikel_add", args=[self.haus.pk]), self._anlegen_daten()
        )
        self.assertEqual(antwort.status_code, 302)
        self.assertEqual(Hausartikel.objects.get(artikelnummer="101002").haus, self.haus)

    def test_hausartikel_mit_neuem_ware(self):
        self.client.post(
            reverse("hausartikel_add", args=[self.haus.pk]),
            self._anlegen_daten(
                ware="",
                neuer_ware="Artikel F 0,33",
                basiseinheit=self.stueck.pk,
                inventurart=jahresinventur().pk,
            ),
        )
        artikel = Hausartikel.objects.get(artikelnummer="101002")
        self.assertEqual(artikel.ware.name, "Artikel F 0,33")
        self.assertEqual(artikel.inventurart, jahresinventur())

    def test_neuer_ware_erbt_die_bezeichnung(self):
        self.client.post(
            reverse("hausartikel_add", args=[self.haus.pk]),
            self._anlegen_daten(ware="", basiseinheit=self.stueck.pk),
        )
        artikel = Hausartikel.objects.get(artikelnummer="101002")
        self.assertEqual(artikel.ware.name, "Artikel F Flasche")

    def test_anlegen_weist_ab_und_legt_nichts_an(self):
        doppelt = {
            "gebinde-0-name": "Kasten",
            "gebinde-0-faktor_in_basiseinheit": "24",
            "gebinde-1-name": "Kasten",
            "gebinde-1-faktor_in_basiseinheit": "20",
        }
        faelle = (
            # Case, deviating entries, what the page says about it (None: only the rejection counts)
            (
                "Ware gewählt und neue benannt",
                {"neuer_ware": "Noch einer", "basiseinheit": self.stueck.pk},
                "nicht beides",
            ),
            ("neue Ware ohne Basiseinheit", {"ware": ""}, "Einheit gebraucht"),
            # The location row knows only the product's packs and the newly typed ones.
            ("unbekanntes Gebinde am Ort", {"orte-0-gebinde": ["Gibtesnicht"]}, None),
            ("Gebinde ohne Menge", {"gebinde-0-name": "Kasten"}, "Ohne Menge je Gebinde"),
            ("derselbe Gebindename zweimal", doppelt, "nur einmal vorkommen"),
        )
        adresse = reverse("hausartikel_add", args=[self.haus.pk])
        for fall, angaben, meldung in faelle:
            with self.subTest(fall=fall):
                antwort = self.client.post(adresse, self._anlegen_daten(**angaben))
                self.assertEqual(antwort.status_code, 200)
                if meldung:
                    self.assertContains(antwort, meldung)
                self.assertFalse(Hausartikel.objects.filter(artikelnummer="101002").exists())

    def test_belegte_nummer_wird_abgewiesen(self):
        antwort = self.client.post(
            reverse("hausartikel_add", args=[self.haus.pk]),
            self._anlegen_daten(artikelnummer=self.grund.artikelnummer),
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "bereits vergeben")

    def test_ortszeile_bietet_gebinde_erst_wenn_es_welche_gibt(self):
        """The product is new: no empty box, but the selection is ready."""
        antwort = self.client.get(reverse("hausartikel_add", args=[self.haus.pk]))
        self.assertNotContains(antwort, 'id="id_orte-0-gebinde"')
        self.assertContains(antwort, "data-gebindeauswahl")

    def test_anlegen_mit_gebinden_und_erwartung(self):
        antwort = self.client.post(
            reverse("hausartikel_add", args=[self.haus.pk]),
            self._anlegen_daten(
                ware="",
                neuer_ware="Artikel F 0,33",
                basiseinheit=self.stueck.pk,
                **{
                    "gebinde-0-name": "Kasten",
                    "gebinde-0-faktor_in_basiseinheit": "24",
                    "gebinde-1-name": "Flasche(n)",
                    "gebinde-1-faktor_in_basiseinheit": "1",
                    "orte-0-erwartet": "on",
                    "orte-0-gebinde": ["Kasten", "Flasche(n)"],
                },
            ),
        )
        self.assertEqual(antwort.status_code, 302)
        artikel = Hausartikel.objects.get(artikelnummer="101002")
        self.assertEqual(
            sorted(artikel.ware.gebinde.values_list("name", flat=True)),
            ["Flasche(n)", "Kasten"],
        )
        erwartung = Erwartung.objects.get(ort=self.ort, artikel=artikel)
        self.assertEqual(
            sorted(erwartung.gebinde_vorgaben.values_list("gebinde__name", flat=True)),
            ["Flasche(n)", "Kasten"],
        )

    def test_angehaktes_gebinde_genuegt_als_erwartung(self):
        """Whoever ticks a pack means the location, even without a tick in front."""
        self.client.post(
            reverse("hausartikel_add", args=[self.haus.pk]),
            self._anlegen_daten(
                **{
                    "gebinde-0-name": "Kasten",
                    "gebinde-0-faktor_in_basiseinheit": "24",
                    "orte-0-gebinde": ["Kasten"],
                }
            ),
        )
        artikel = Hausartikel.objects.get(artikelnummer="101002")
        self.assertTrue(Erwartung.objects.filter(ort=self.ort, artikel=artikel).exists())

    def test_vorhandene_gebinde_des_wares_stehen_zur_wahl(self):
        sammel = Ware.objects.create(name="Artikel W 0,5", basiseinheit=self.stueck)
        Gebinde.objects.create(ware=sammel, name="Karton", faktor_in_basiseinheit=Decimal("12"))
        self.client.post(
            reverse("hausartikel_add", args=[self.haus.pk]),
            self._anlegen_daten(ware=sammel.pk, **{"orte-0-gebinde": ["Karton"]}),
        )
        erwartung = Erwartung.objects.get(ort=self.ort, artikel__hausartikel__ware=sammel)
        self.assertEqual(erwartung.gebinde_vorgaben.get().gebinde.name, "Karton")

    def test_abwiegbare_ware_mit_gebinde_am_ort_wird_dort_nicht_gewogen(self):
        """Whoever ticks the box has said how it is counted there."""
        sammel = Ware.objects.create(name="Ware N", basiseinheit=self.stueck, abwiegbar=True)
        Gebinde.objects.create(ware=sammel, name="Karton", faktor_in_basiseinheit=Decimal("12"))
        self.client.post(
            reverse("hausartikel_add", args=[self.haus.pk]),
            self._anlegen_daten(ware=sammel.pk, **{"orte-0-gebinde": ["Karton"]}),
        )
        erwartung = Erwartung.objects.get(ort=self.ort, artikel__hausartikel__ware=sammel)
        self.assertFalse(erwartung.wiegen_erwartet)

    def test_abwiegbare_ware_ohne_gebinde_am_ort_bleibt_bei_der_waage(self):
        """Otherwise the article would be expected there and get no row."""
        sammel = Ware.objects.create(name="Ware N", basiseinheit=self.stueck, abwiegbar=True)
        self.client.post(
            reverse("hausartikel_add", args=[self.haus.pk]),
            self._anlegen_daten(ware=sammel.pk, **{"orte-0-erwartet": "on"}),
        )
        erwartung = Erwartung.objects.get(ort=self.ort, artikel__hausartikel__ware=sammel)
        self.assertTrue(erwartung.wiegen_erwartet)

    def test_ort_ohne_gebinde_wird_abgewiesen(self):
        """The article would be expected there and get no row. Until 10/2026 it was taken."""
        antwort = self.client.post(
            reverse("hausartikel_add", args=[self.haus.pk]),
            self._anlegen_daten(
                **{
                    "gebinde-0-name": "Kasten",
                    "gebinde-0-faktor_in_basiseinheit": "24",
                    "orte-0-erwartet": "on",
                }
            ),
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(
            antwort.context["orte_formset"].forms[0].errors,
            {"gebinde": ["Ohne Gebinde bekäme der Artikel hier keine Zeile."]},
        )
        self.assertFalse(Hausartikel.objects.filter(artikelnummer="101002").exists())

    def test_danach_stehen_die_gebinde_der_vorhandenen_ware_zur_wahl(self):
        """The first view does not know them yet. With the message they stand at every location."""
        sammel = Ware.objects.create(name="Artikel W 0,5", basiseinheit=self.stueck)
        Gebinde.objects.create(ware=sammel, name="Karton", faktor_in_basiseinheit=Decimal("12"))
        antwort = self.client.post(
            reverse("hausartikel_add", args=[self.haus.pk]),
            self._anlegen_daten(ware=sammel.pk, **{"orte-0-erwartet": "on"}),
        )
        self.assertContains(antwort, "Ohne Gebinde bekäme der Artikel hier keine Zeile.")
        self.assertContains(antwort, 'name="orte-0-gebinde" value="Karton"')

    def test_nicht_zaehlbar_fragt_nicht_nach_gebinden(self):
        """A product in "Not countable" is expected nowhere. The location's tick falls away."""
        sammel = Ware.objects.create(
            name="Ware N", basiseinheit=self.stueck, warenfamilie=Warenfamilie.nicht_zaehlbar()
        )
        antwort = self.client.post(
            reverse("hausartikel_add", args=[self.haus.pk]),
            self._anlegen_daten(ware=sammel.pk, **{"orte-0-erwartet": "on"}),
        )
        self.assertEqual(antwort.status_code, 302)
        artikel = Hausartikel.objects.get(artikelnummer="101002")
        self.assertEqual(artikel.status, Artikelstatus.IGNORIERT)
        self.assertFalse(Erwartung.objects.filter(artikel=artikel).exists())

    def anlegen_mit_gebinde(self, name, menge, **abweichend):
        """A new pack typed in at the top, ticked at the location below. Followed to the page."""
        return self.client.post(
            reverse("hausartikel_add", args=[self.haus.pk]),
            self._anlegen_daten(
                **{
                    "gebinde-0-name": name,
                    "gebinde-0-faktor_in_basiseinheit": menge,
                    "orte-0-gebinde": [name],
                }
                | abweichend
            ),
            follow=True,
        )

    def test_ein_vergebener_name_mit_anderer_menge_wird_gemeldet(self):
        """Until 10/2026 the new pack vanished silently, and the location hung on the old one."""
        karton = Gebinde.objects.create(
            ware=self.sammel2, name="Karton", faktor_in_basiseinheit=Decimal("12")
        )
        antwort = self.anlegen_mit_gebinde("Karton", "24")

        self.assertContains(
            antwort,
            f"„Karton“ steht bei „Artikel C 033“ schon für 12 {self.stueck.kurz}. "
            "Ein zweites Gebinde mit diesem Namen wurde nicht angelegt.",
        )
        self.assertContains(antwort, "0 neue Gebinde an der Ware")
        self.assertEqual(list(self.sammel2.gebinde.all()), [karton])
        erwartung = Erwartung.objects.get(
            ort=self.ort, artikel__hausartikel__artikelnummer="101002"
        )
        self.assertEqual(erwartung.gebinde_vorgaben.get().gebinde, karton)

    def test_eine_belegte_menge_gibt_dem_ort_das_gebinde_das_sie_traegt(self):
        """Until 10/2026 the location got no pack at all and stood in the list without a row."""
        kiste = Gebinde.objects.create(
            ware=self.sammel2, name="Kiste", faktor_in_basiseinheit=Decimal("24")
        )
        antwort = self.anlegen_mit_gebinde("Kasten", "24")

        self.assertContains(antwort, "„Kiste“ steht bei „Artikel C 033“ schon für diese Menge.")
        self.assertContains(antwort, "0 neue Gebinde an der Ware")
        erwartung = Erwartung.objects.get(
            ort=self.ort, artikel__hausartikel__artikelnummer="101002"
        )
        self.assertEqual(erwartung.gebinde_vorgaben.get().gebinde, kiste)

    def test_ein_vergebener_name_mit_gleicher_menge_bleibt_still(self):
        """The row names the pack the product carries already: nothing to report, nothing new."""
        karton = Gebinde.objects.create(
            ware=self.sammel2, name="Karton", faktor_in_basiseinheit=Decimal("12")
        )
        antwort = self.anlegen_mit_gebinde("Karton", "12")

        self.assertNotContains(antwort, "schon für")
        self.assertContains(antwort, "0 neue Gebinde an der Ware")
        self.assertEqual(list(self.sammel2.gebinde.all()), [karton])
        erwartung = Erwartung.objects.get(
            ort=self.ort, artikel__hausartikel__artikelnummer="101002"
        )
        self.assertEqual(erwartung.gebinde_vorgaben.get().gebinde, karton)

    def test_zwei_namen_fuer_eine_menge_geben_dem_ort_eine_vorgabe(self):
        """The new name leads to the pack that holds the quantity, the old name too.

        One default per pack, the database allows no second. Until 10/2026 that
        ended as a server error and nothing was created.
        """
        kiste = Gebinde.objects.create(
            ware=self.sammel2, name="Kiste", faktor_in_basiseinheit=Decimal("24")
        )
        antwort = self.anlegen_mit_gebinde(
            "Kasten", "24", **{"orte-0-gebinde": ["Kiste", "Kasten"]}
        )

        self.assertEqual(antwort.redirect_chain[0][1], 302)
        self.assertContains(antwort, "„Kiste“ steht bei „Artikel C 033“ schon für diese Menge.")
        erwartung = Erwartung.objects.get(
            ort=self.ort, artikel__hausartikel__artikelnummer="101002"
        )
        self.assertEqual([v.gebinde for v in erwartung.gebinde_vorgaben.all()], [kiste])

    def test_die_meldung_zaehlt_nur_die_neuen_gebinde(self):
        """The product's existing packs do not count. Until 10/2026 they did."""
        Gebinde.objects.create(
            ware=self.sammel2, name="Kiste", faktor_in_basiseinheit=Decimal("24")
        )
        antwort = self.anlegen_mit_gebinde("Sixpack", "6")
        self.assertContains(antwort, "1 neue Gebinde an der Ware")

    def test_neu_angelegt_ist_immer_aktiv(self):
        """There is no tick any more. What arises here is carried."""
        self.client.post(reverse("hausartikel_add", args=[self.haus.pk]), self._anlegen_daten())
        artikel = Hausartikel.objects.get(artikelnummer="101002")
        self.assertTrue(artikel.aktiv)
        self.assertIsNone(artikel.gueltig_bis)

    def test_hausartikel_bearbeiten_behaelt_den_ware(self):
        """Editing does not know packs and locations. Those lie in their places."""
        antwort = self.client.post(
            reverse("hausartikel_edit", args=[self.grund.pk]),
            {
                "artikelnummer": self.grund.artikelnummer,
                "name": "Artikel C 0,33 Flasche neu",
                "ware": self.sammel.pk,
                "neuer_ware": "",
                "basiseinheit": "",
                "inventurart": self.grund.inventurart_id,
                "warengruppe": self.gruppe.pk,
                "neue_warengruppe": "",
                "ean": "",
            }
            | self._leeres_formset("gebinde"),
        )
        self.assertEqual(antwort.status_code, 302)
        self.grund.refresh_from_db()
        self.assertEqual(self.grund.name, "Artikel C 0,33 Flasche neu")
        self.assertEqual(self.grund.ware, self.sammel)

    def test_warengruppe_inaktiv_eingestellt_und_zurueck(self):
        adresse = reverse("warengruppe_status", args=[self.gruppe.pk])
        antwort = self.client.post(adresse, {"status": "INAKTIV"})
        self.assertRedirects(antwort, self.gruppe.get_absolute_url())
        self.grund.refresh_from_db()
        self.assertEqual(self.grund.status, Artikelstatus.INAKTIV)
        self.assertEqual(Warengruppe.objects.get(pk=self.gruppe.pk).sammelstatus, "INAKTIV")

        self.client.post(adresse, {"status": "EINGESTELLT"})
        self.grund.refresh_from_db()
        self.assertEqual(self.grund.status, Artikelstatus.EINGESTELLT)
        self.assertEqual(Warengruppe.objects.get(pk=self.gruppe.pk).sammelstatus, "EINGESTELLT")

        self.client.post(adresse, {"status": "AKTIV"})
        self.grund.refresh_from_db()
        self.assertEqual(self.grund.status, Artikelstatus.AKTIV)

    def pfandgruppe(self):
        """Two deposit products. Kino Süd carries them under another product group."""
        gruppe = Warengruppe.objects.create(name="Warengruppe P")
        kino_sued = Haus.objects.create(name="Kino Süd")
        leergut = Warengruppe.objects.create(name="Warengruppe K")
        artikel = []
        for nummer, name in (("901001", "Artikel P 0,25"), ("901002", "Artikel P 0,5")):
            ware = Ware.objects.create(name=name, basiseinheit=self.stueck)
            for haus, warengruppe in ((self.haus, gruppe), (kino_sued, leergut)):
                artikel.append(
                    Hausartikel.objects.create(
                        haus=haus,
                        ware=ware,
                        warengruppe=warengruppe,
                        artikelnummer=nummer,
                        name=name,
                    )
                )
        return gruppe, artikel

    def test_ganze_warengruppe_nicht_zaehlbar(self):
        """On all products of the group, even where another house sorts them differently."""
        gruppe, artikel = self.pfandgruppe()
        antwort = self.client.post(
            reverse("warengruppe_status", args=[gruppe.pk]), {"status": "IGNORIERT"}
        )
        self.assertRedirects(antwort, gruppe.get_absolute_url())
        for satz in artikel:
            satz.refresh_from_db()
            self.assertEqual(satz.status, Artikelstatus.IGNORIERT)
            self.assertEqual(satz.ware.warenfamilie, Warenfamilie.nicht_zaehlbar())

    def test_warengruppe_nicht_zaehlbar_ganz_oder_gar_nicht(self):
        """If a product is in a recipe, the whole group stays as it was."""
        self.client.post(
            reverse("warengruppe_status", args=[self.gruppe.pk]), {"status": "IGNORIERT"}
        )
        gruppe, artikel = self.pfandgruppe()
        Rezepturkomponente.objects.create(
            rezepturartikel=self.zaehl,
            ware=artikel[-1].ware,
            menge_in_basiseinheit=Decimal("1"),
        )
        antwort = self.client.post(
            reverse("warengruppe_status", args=[gruppe.pk]), {"status": "IGNORIERT"}, follow=True
        )
        self.assertContains(antwort, "steckt in der Rezeptur")
        for satz in artikel:
            satz.refresh_from_db()
            self.assertEqual(satz.status, Artikelstatus.AKTIV)
        # The group "Warengruppe G" refused it likewise: Artikel C is in the portion N.
        self.grund.refresh_from_db()
        self.assertEqual(self.grund.status, Artikelstatus.AKTIV)

    def test_warengruppenliste_zeigt_die_statusknoepfe(self):
        antwort = self.client.get(reverse("warengruppe_list"))
        self.assertContains(antwort, reverse("warengruppe_status", args=[self.gruppe.pk]))
        self.assertNotContains(antwort, "Stilllegen")
        self.assertContains(antwort, 'value="IGNORIERT"')

    def test_rezepturartikel_mit_rezeptur_anlegen(self):
        daten = (
            {"name": "Menü klein", "basiseinheit": self.stueck.pk}
            | self._leeres_formset("komponenten")
            | {
                "komponenten-0-ware": self.sammel.pk,
                "komponenten-0-menge_in_basiseinheit": "2",
            }
        )
        self.client.post(reverse("rezepturartikel_add"), daten)
        neu = Rezepturartikel.objects.get(name="Menü klein")
        self.assertEqual(neu.komponenten.get().menge_in_basiseinheit, Decimal("2"))

    def test_gewogenen_rezepturartikel_anlegen(self):
        """A mix that is weighed once and booked onto two products."""
        gramm = Basiseinheit.objects.get(name="g")
        eimer = Behaelter.objects.create(name="Eimer", tara=Decimal("300"))
        daten = (
            {
                "name": "Mischung N lose",
                "basiseinheit": gramm.pk,
                "behaelter": [eimer.pk],
            }
            | self._leeres_formset("komponenten", extra=2)
            | {
                "komponenten-0-ware": self.sammel.pk,
                "komponenten-0-menge_in_basiseinheit": "0,6",
                "komponenten-1-ware": self.sammel2.pk,
                "komponenten-1-menge_in_basiseinheit": "0,4",
            }
        )
        antwort = self.client.post(reverse("rezepturartikel_add"), daten)
        self.assertEqual(antwort.status_code, 302)

        mischung = Rezepturartikel.objects.get(name="Mischung N lose")
        self.assertTrue(mischung.wird_gewogen)
        self.assertEqual(list(mischung.behaelter.all()), [eimer])
        verteilt = mischung.mengen_je_ware(Decimal("1300"), eimer)
        self.assertEqual(verteilt[self.sammel], Decimal("600.0"))
        self.assertEqual(verteilt[self.sammel2], Decimal("400.0"))

    def _anteilige_mischung(self, name, anteile, gewicht_b=None, **abweichend):
        """Post a weighed mix with shares in percent from two litre syrups.

        Syrup A always knows its grams per litre, syrup B only with 'gewicht_b'.
        """
        gramm = Basiseinheit.objects.get(name="g")
        liter = Basiseinheit.objects.get(name="Liter")
        sirupe = []
        for kennung, gewicht in (("A", Decimal("1250")), ("B", gewicht_b)):
            sirup = Ware.objects.create(name=f"Sirup {kennung} {name}", basiseinheit=liter)
            if gewicht is not None:
                sirup.umrechnungen_setzen({Dimension.MASSE: gewicht})
            sirupe.append(sirup)
        daten = (
            {"name": name, "basiseinheit": gramm.pk, "anteilig": "on"}
            | self._leeres_formset("komponenten", extra=2)
            | {
                "komponenten-0-ware": sirupe[0].pk,
                "komponenten-0-menge_in_basiseinheit": anteile[0],
                "komponenten-1-ware": sirupe[1].pk,
                "komponenten-1-menge_in_basiseinheit": anteile[1],
            }
            | abweichend
        )
        return self.client.post(reverse("rezepturartikel_add"), daten), sirupe

    def test_anteilige_mischung_verlangt_das_gewicht_der_ware(self):
        """Syrup B knows no grams per litre. The row says what is missing."""
        antwort, _sirupe = self._anteilige_mischung("Mischung L", ("60", "40"))
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "wie viel Gramm eine Einheit wiegt")
        self.assertFalse(Rezepturartikel.objects.filter(name="Mischung L").exists())

    def test_anteile_muessen_hundert_ergeben(self):
        antwort, _sirupe = self._anteilige_mischung("Mischung M", ("60", "30"), Decimal("1000"))
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "90 %, nicht 100 %")
        self.assertFalse(Rezepturartikel.objects.filter(name="Mischung M").exists())

    def test_anteilige_mischung_anlegen(self):
        """60 % and 40 % of 1000 g, through 1250 and 1000 g per litre: 0.48 l and 0.4 l."""
        antwort, sirupe = self._anteilige_mischung("Mischung N", ("60", "40"), Decimal("1000"))
        self.assertEqual(antwort.status_code, 302)
        mischung = Rezepturartikel.objects.get(name="Mischung N")
        self.assertTrue(mischung.anteilig)
        verteilt = mischung.verteilen(Decimal("1000"))
        self.assertEqual(verteilt[sirupe[0]], Decimal("0.48"))
        self.assertEqual(verteilt[sirupe[1]], Decimal("0.4"))
        # The detail page shows the percent, not litres.
        seite = self.client.get(mischung.get_absolute_url())
        self.assertRegex(seite.content.decode(), r"60\s*%")
        self.assertNotContains(seite, "60\n                            l")

    def test_anteile_gibt_es_nur_beim_wiegen(self):
        daten = (
            {"name": "Portion A", "basiseinheit": self.stueck.pk, "anteilig": "on"}
            | self._leeres_formset("komponenten")
            | {
                "komponenten-0-ware": self.sammel.pk,
                "komponenten-0-menge_in_basiseinheit": "100",
            }
        )
        antwort = self.client.post(reverse("rezepturartikel_add"), daten)
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Anteile in Prozent gibt es nur beim Wiegen")

    def test_behaelter_ohne_waage_werden_abgewiesen(self):
        eimer = Behaelter.objects.create(name="Eimer", tara=Decimal("300"))
        daten = (
            {
                "name": "Portion mit Eimer",
                "basiseinheit": self.stueck.pk,
                "behaelter": [eimer.pk],
            }
            | self._leeres_formset("komponenten")
            | {
                "komponenten-0-ware": self.sammel.pk,
                "komponenten-0-menge_in_basiseinheit": "1",
            }
        )
        antwort = self.client.post(reverse("rezepturartikel_add"), daten)
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "nicht gewogen")
        self.assertFalse(Rezepturartikel.objects.filter(name="Portion mit Eimer").exists())

    def test_detailseite_nennt_die_einheit(self):
        antwort = self.client.get(reverse("rezepturartikel_detail", args=[self.zaehl.pk]))
        # Shown is the short form, stored is "Stück(e)".
        self.assertContains(antwort, "wird in Stk. gezählt")

    def test_einheitenauswahl_bietet_nur_stueck_und_gramm(self):
        einheiten = wahlmenge(RezepturartikelForm(), "basiseinheit")
        self.assertCountEqual(einheiten.values_list("name", flat=True), ["Stück(e)", "g"])

    def test_dimension_steht_an_jeder_einheit(self):
        """The scripts show and hide by it. They must not have to guess."""
        gerendert = str(RezepturartikelForm()["basiseinheit"])
        self.assertIn('data-dimension="MASSE"', gerendert)
        self.assertIn('data-dimension="STUECK"', gerendert)

    def test_wareformular_markiert_die_dimensionen(self):
        gerendert = str(WareForm()["basiseinheit"])
        for dimension in ("MASSE", "STUECK", "VOLUMEN"):
            with self.subTest(dimension=dimension):
                self.assertIn(f'data-dimension="{dimension}"', gerendert)

    def test_kilogramm_kommt_nicht_durch(self):
        # 'cls.gramm' is the gram. Here the kilogram is meant.
        kilo = Basiseinheit.objects.get(name="kg")
        daten = (
            {"name": "In Kilo", "basiseinheit": kilo.pk}
            | self._leeres_formset("komponenten")
            | {
                "komponenten-0-ware": self.sammel.pk,
                "komponenten-0-menge_in_basiseinheit": "1",
            }
        )
        antwort = self.client.post(reverse("rezepturartikel_add"), daten)
        self.assertEqual(antwort.status_code, 200)
        self.assertFalse(Rezepturartikel.objects.filter(name="In Kilo").exists())

    def _zaehlliste(self, *erwartungen, **abweichend):
        daten = {
            "zeilen-TOTAL_FORMS": str(len(erwartungen)),
            "zeilen-INITIAL_FORMS": str(len(erwartungen)),
            "zeilen-MIN_NUM_FORMS": "0",
            "zeilen-MAX_NUM_FORMS": "1000",
        }
        for stelle, erwartung in enumerate(erwartungen):
            daten[f"zeilen-{stelle}-erwartung"] = erwartung.pk
        return daten | abweichend

    def test_gebinde_ueber_die_zaehlliste_setzen(self):
        gebinde = self.sammel.gebinde.get()
        antwort = self.client.post(
            reverse("zaehlliste", args=[self.ort.pk]),
            self._zaehlliste(self.erwartung, **{f"zeilen-0-gebinde_{gebinde.pk}": "on"}),
        )
        self.assertRedirects(antwort, self.ort.get_absolute_url())
        vorgabe = self.erwartung.gebinde_vorgaben.get()
        self.assertEqual(vorgabe.gebinde, gebinde)
        self.assertIsNone(vorgabe.standard_menge)

    def test_standardmenge_wird_mitgespeichert(self):
        gebinde = self.sammel.gebinde.get()
        self.client.post(
            reverse("zaehlliste", args=[self.ort.pk]),
            self._zaehlliste(
                self.erwartung,
                **{
                    f"zeilen-0-gebinde_{gebinde.pk}": "on",
                    f"zeilen-0-menge_{gebinde.pk}": "2,5",
                },
            ),
        )
        self.assertEqual(self.erwartung.gebinde_vorgaben.get().standard_menge, Decimal("2.5"))

    def _zweites_gebinde(self) -> Gebinde:
        """A row needs one ticked pack. Whatever a test leaves out stands beside it."""
        return Gebinde.objects.create(
            ware=self.sammel, name="Flasche", faktor_in_basiseinheit=Decimal("1"), sortierung=2
        )

    def test_menge_ohne_haken_legt_keine_vorgabe_an(self):
        gebinde = self.sammel.gebinde.get()
        flasche = self._zweites_gebinde()
        antwort = self.client.post(
            reverse("zaehlliste", args=[self.ort.pk]),
            self._zaehlliste(
                self.erwartung,
                **{f"zeilen-0-menge_{gebinde.pk}": "4", f"zeilen-0-gebinde_{flasche.pk}": "on"},
            ),
        )
        self.assertEqual(antwort.status_code, 302)
        self.assertEqual(self.erwartung.gebinde_vorgaben.get().gebinde, flasche)

    def test_abgewaehltes_gebinde_verschwindet(self):
        gebinde = self.sammel.gebinde.get()
        flasche = self._zweites_gebinde()
        for satz in (gebinde, flasche):
            ErwartungGebinde.objects.create(erwartung=self.erwartung, gebinde=satz)
        antwort = self.client.post(
            reverse("zaehlliste", args=[self.ort.pk]),
            self._zaehlliste(self.erwartung, **{f"zeilen-0-gebinde_{flasche.pk}": "on"}),
        )
        self.assertEqual(antwort.status_code, 302)
        self.assertEqual(self.erwartung.gebinde_vorgaben.get().gebinde, flasche)

    def test_bestehende_vorgabe_behaelt_ihre_menge(self):
        """Re-sorting must not throw away a stored default quantity."""
        gebinde = self.sammel.gebinde.get()
        ErwartungGebinde.objects.create(
            erwartung=self.erwartung, gebinde=gebinde, standard_menge=Decimal("3")
        )
        self.client.post(
            reverse("zaehlliste", args=[self.ort.pk]),
            self._zaehlliste(
                self.erwartung,
                **{
                    f"zeilen-0-gebinde_{gebinde.pk}": "on",
                    f"zeilen-0-menge_{gebinde.pk}": "3",
                },
            ),
        )
        self.assertEqual(self.erwartung.gebinde_vorgaben.get().standard_menge, Decimal("3"))

    def test_menge_laesst_sich_wieder_leeren(self):
        gebinde = self.sammel.gebinde.get()
        ErwartungGebinde.objects.create(
            erwartung=self.erwartung, gebinde=gebinde, standard_menge=Decimal("3")
        )
        self.client.post(
            reverse("zaehlliste", args=[self.ort.pk]),
            self._zaehlliste(self.erwartung, **{f"zeilen-0-gebinde_{gebinde.pk}": "on"}),
        )
        self.assertIsNone(self.erwartung.gebinde_vorgaben.get().standard_menge)

    def test_zaehlliste_zeigt_die_hinterlegte_menge(self):
        gebinde = self.sammel.gebinde.get()
        ErwartungGebinde.objects.create(
            erwartung=self.erwartung, gebinde=gebinde, standard_menge=Decimal("2.5")
        )
        formset = self.client.get(reverse("zaehlliste", args=[self.ort.pk])).context["formset"]
        zeile = formset.forms[0]
        self.assertTrue(zeile[f"gebinde_{gebinde.pk}"].value())
        self.assertEqual(zeile[f"menge_{gebinde.pk}"].value(), Decimal("2.5"))
        # 2,500 becomes 2,5 in the field. Nobody reads three decimal places here.
        self.assertIn('value="2,5"', str(zeile[f"menge_{gebinde.pk}"]))

    def test_inaktives_gebinde_behaelt_seine_vorgabe(self):
        """Whoever saves the counting list should not throw away a default with it."""
        gebinde = self.sammel.gebinde.get()
        ErwartungGebinde.objects.create(
            erwartung=self.erwartung, gebinde=gebinde, standard_menge=Decimal("3")
        )
        Gebinde.objects.filter(pk=gebinde.pk).update(aktiv=False)
        self.client.post(
            reverse("zaehlliste", args=[self.ort.pk]), self._zaehlliste(self.erwartung)
        )
        self.assertEqual(self.erwartung.gebinde_vorgaben.get().standard_menge, Decimal("3"))

    def test_ort_bearbeiten_pflegt_nur_noch_den_ort(self):
        """The article selection sits in the counting list. Here it would only be in the way."""
        antwort = self.client.get(reverse("ort_edit", args=[self.ort.pk]))
        self.assertNotIn("artikel_form", antwort.context)
        self.assertNotContains(antwort, "Welche Artikel kommen hier vor")

    def test_ort_umbenennen_laesst_die_artikel_stehen(self):
        self.client.post(
            reverse("ort_edit", args=[self.ort.pk]),
            {"name": "Theke links", "bereich": self.ort.bereich_id},
        )
        self.ort.refresh_from_db()
        self.assertEqual(self.ort.name, "Theke links")
        self.assertEqual(Erwartung.objects.filter(ort=self.ort).count(), 1)


class Blaettern(AngemeldeteSeiten):
    """Page navigation above and below the list, with direct jump."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        einheit = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        for nummer in range(60):
            Ware.objects.create(name=f"Ware {nummer:03d}", basiseinheit=einheit)

    def test_navigation_steht_zweimal_auf_der_seite(self):
        antwort = self.client.get(reverse("ware_list"))
        self.assertContains(antwort, 'id="seite-oben"')
        self.assertContains(antwort, 'id="seite-unten"')

    def test_sprung_auf_eine_seite(self):
        antwort = self.client.get(reverse("ware_list"), {"page": "2"})
        self.assertEqual(antwort.context["page_obj"].number, 2)

    def test_sprung_behaelt_die_filter(self):
        """The active filters ride along as hidden fields in the jump form."""
        antwort = self.client.get(reverse("ware_list"), {"q": "Ware", "page": "2"})
        self.assertContains(antwort, '<input type="hidden" name="q" value="Ware">', html=True)
        self.assertNotContains(antwort, '<input type="hidden" name="page"', html=False)

    def test_ohne_zweite_seite_keine_navigation(self):
        Ware.objects.all().delete()
        antwort = self.client.get(reverse("ware_list"))
        self.assertNotContains(antwort, 'id="seite-oben"')


class Herkunftspfad(AngemeldeteSeiten):
    """The breadcrumb bar knows the way one came by."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Ost")
        cls.ort = Ort.objects.create(name="Theke Nord", bereich=bereich(cls.haus))
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe B")
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.sammel = Ware.objects.create(name="Posten B", basiseinheit=cls.stueck)
        cls.artikel = Hausartikel.objects.create(
            haus=cls.haus,
            ware=cls.sammel,
            warengruppe=cls.gruppe,
            artikelnummer="901001",
            name="Posten B groß",
        )

    def pfad(self, adresse, **parameter):
        antwort = self.client.get(adresse, parameter)
        return [(station.titel, str(station.url)) for station in antwort.context["pfad"]]

    def test_ohne_herkunft_der_standardweg(self):
        self.assertEqual(
            self.pfad(self.artikel.get_absolute_url()),
            [("Häuser", reverse("haus_list")), ("Kino Ost", self.haus.get_absolute_url())],
        )

    def test_ueber_die_warengruppe_gekommen(self):
        self.assertEqual(
            self.pfad(self.artikel.get_absolute_url(), von=f"warengruppe:{self.gruppe.pk}"),
            [
                ("Warengruppen", reverse("warengruppe_list")),
                ("Warengruppe B", self.gruppe.get_absolute_url()),
            ],
        )

    def test_ueber_den_ware_gekommen(self):
        self.assertEqual(
            self.pfad(self.artikel.get_absolute_url(), von=f"ware:{self.sammel.pk}"),
            [
                ("Waren", reverse("ware_list")),
                ("Posten B", self.sammel.get_absolute_url()),
            ],
        )

    def test_ueber_einen_ort_gekommen(self):
        self.assertEqual(
            self.pfad(self.artikel.get_absolute_url(), von=f"ort:{self.ort.pk}"),
            [
                ("Häuser", reverse("haus_list")),
                ("Kino Ost", self.haus.get_absolute_url()),
                (self.ort.bereich.name, self.ort.bereich.get_absolute_url()),
                ("Theke Nord", self.ort.get_absolute_url()),
            ],
        )

    def test_ortsseite_und_zaehlliste_zeigen_ihren_weg(self):
        """Both built their bar by hand and dropped the way one came by (until 10/2026)."""
        bereich = self.ort.bereich
        zum_ort = [
            ("Häuser", reverse("haus_list")),
            ("Kino Ost", self.haus.get_absolute_url()),
            (bereich.name, bereich.get_absolute_url()),
        ]
        zaehlliste = reverse("zaehlliste", args=[self.ort.pk])
        self.assertEqual(self.pfad(self.ort.get_absolute_url()), zum_ort)
        self.assertEqual(
            self.pfad(zaehlliste), [*zum_ort, ("Theke Nord", self.ort.get_absolute_url())]
        )
        for adresse in (self.ort.get_absolute_url(), zaehlliste):
            with self.subTest(adresse=adresse):
                antwort = self.client.get(adresse, {"von": f"hausartikel:{self.artikel.pk}"})
                self.assertTemplateUsed(antwort, "components/breadcrumbs.html")
                self.assertContains(
                    antwort,
                    f'<a href="{self.artikel.get_absolute_url()}">Posten B groß</a>',
                    html=True,
                )

    def test_ueber_die_hausartikelliste_gekommen(self):
        self.assertEqual(
            self.pfad(self.artikel.get_absolute_url(), von=f"haus_artikel:{self.haus.pk}")[-1],
            ("Hausartikel", reverse("hausartikel_list", args=[self.haus.pk])),
        )

    def test_ware_kennt_die_herkunft_auch(self):
        self.assertEqual(
            self.pfad(self.sammel.get_absolute_url(), von=f"warengruppe:{self.gruppe.pk}")[-1],
            ("Warengruppe B", self.gruppe.get_absolute_url()),
        )

    def test_unsinnige_herkunft_faellt_auf_den_standardweg_zurueck(self):
        standard = self.pfad(self.artikel.get_absolute_url())
        for unsinn in ("kaputt", "warengruppe:99999", "warengruppe:abc", "../../etc"):
            with self.subTest(von=unsinn):
                self.assertEqual(self.pfad(self.artikel.get_absolute_url(), von=unsinn), standard)

    def test_ganze_kette_wird_aufgeloest(self):
        """Only the first link brings its list, the following ones only themselves."""
        self.assertEqual(
            self.pfad(
                self.sammel.get_absolute_url(),
                von=f"warengruppe:{self.gruppe.pk},hausartikel:{self.artikel.pk}",
            ),
            [
                ("Warengruppen", reverse("warengruppe_list")),
                ("Warengruppe B", self.gruppe.get_absolute_url()),
                ("Posten B groß", self.artikel.get_absolute_url()),
            ],
        )

    def test_seite_reicht_ihre_eigene_station_weiter(self):
        antwort = self.client.get(
            self.artikel.get_absolute_url(), {"von": f"warengruppe:{self.gruppe.pk}"}
        )
        self.assertEqual(
            antwort.context["herkunft"],
            f"warengruppe:{self.gruppe.pk},hausartikel:{self.artikel.pk}",
        )

    def test_hin_und_zurueck_verlaengert_die_kette_nicht(self):
        """Whoever jumps between two pages should find their way back to the starting point."""
        antwort = self.client.get(
            self.gruppe.get_absolute_url(),
            {"von": f"warengruppe:{self.gruppe.pk},hausartikel:{self.artikel.pk}"},
        )
        self.assertEqual(antwort.context["herkunft"], f"warengruppe:{self.gruppe.pk}")

    def test_kette_ist_in_der_laenge_begrenzt(self):
        lang = ",".join(f"warengruppe:{self.gruppe.pk}" for _ in range(20))
        antwort = self.client.get(self.artikel.get_absolute_url(), {"von": lang})
        self.assertLessEqual(len(antwort.context["herkunft"].split(",")), 6)

    def test_kaputte_glieder_fallen_aus_der_kette(self):
        antwort = self.client.get(
            self.artikel.get_absolute_url(),
            {"von": f"kaputt:1,warengruppe:{self.gruppe.pk},haus:abc"},
        )
        self.assertEqual(
            [station.titel for station in antwort.context["pfad"]],
            ["Warengruppen", "Warengruppe B"],
        )

    def test_die_leiste_wird_auch_gerendert(self):
        antwort = self.client.get(
            self.artikel.get_absolute_url(), {"von": f"warengruppe:{self.gruppe.pk}"}
        )
        self.assertContains(antwort, reverse("warengruppe_list"))
        self.assertContains(antwort, "Warengruppe B")


class KnoepfeInDenListen(AngemeldeteSeiten):
    """Switch scope and active state directly from the row.

    The button sends the address of its list along as 'weiter', including
    filters and page number. Without that a pass through two hundred articles
    would be impossible, because every click would throw one back to page one.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Süd2")
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe I")
        cls.cola = Ware.objects.create(name="Artikel C 0,33", basiseinheit=cls.stueck)
        cls.artikel = Hausartikel.objects.create(
            haus=cls.haus,
            ware=cls.cola,
            warengruppe=cls.gruppe,
            artikelnummer="910001",
            name="Artikel C 0,33",
        )

    # -- What the list delivers ---------------------------------------------

    def test_liste_zeigt_die_knoepfe_mit_ruecksprung(self):
        adresse = reverse("ware_list") + "?q=artikel+c"
        antwort = self.client.get(adresse)
        self.assertContains(antwort, reverse("ware_inventurart", args=[self.cola.pk]))
        self.assertContains(antwort, reverse("ware_status", args=[self.cola.pk]))
        self.assertContains(antwort, f'name="weiter" value="{adresse}"'.replace("&", "&amp;"))

    def test_die_gesetzte_art_ist_abgeschaltet(self):
        """A click on the already set type would be a submit without effect."""
        jahr = jahresinventur()
        html = self.client.get(reverse("ware_list")).content.decode()
        knoepfe = {
            treffer.group(1): "disabled" in treffer.group(0)
            for treffer in re.finditer(
                r"<button[^>]*?name=\"inventurart\" value=\"(\d+)\"[^>]*>", html
            )
        }
        self.assertTrue(knoepfe[str(monatsinventur().pk)], "gesetzte Art muss abgeschaltet sein")
        self.assertFalse(knoepfe[str(jahr.pk)], "die andere Art muss anklickbar bleiben")

    def test_hausartikelliste_filtert_nach_status(self):
        """To reactivate one first has to show the inactive and discontinued ones."""
        liste = reverse("hausartikel_list", args=[self.haus.pk])
        ziel = reverse("hausartikel_status", args=[self.artikel.pk])
        self.assertContains(self.client.get(liste), ziel)
        self.artikel.deaktivieren()
        # "Carried" is the default: inactive belongs to it, active does not.
        self.assertContains(self.client.get(liste), ziel)
        self.assertNotContains(self.client.get(liste, {"status": "aktiv"}), ziel)
        self.assertContains(self.client.get(liste, {"status": "inaktiv"}), ziel)
        self.artikel.einstellen()
        self.assertNotContains(self.client.get(liste, {"status": "inaktiv"}), ziel)
        self.assertNotContains(self.client.get(liste), ziel)
        self.assertContains(self.client.get(liste, {"status": "eingestellt"}), ziel)
        self.assertContains(self.client.get(liste, {"status": "alle"}), ziel)

    def test_ignorierte_stehen_in_keiner_liste(self):
        self.artikel.ignorieren()
        ziel = reverse("hausartikel_detail", args=[self.artikel.pk])
        for adresse, daten in (
            (reverse("hausartikel_list", args=[self.haus.pk]), {"status": "alle"}),
            (reverse("warengruppe_detail", args=[self.gruppe.pk]), {}),
            (reverse("haus_detail", args=[self.haus.pk]), {}),
        ):
            with self.subTest(adresse=adresse):
                self.assertNotContains(self.client.get(adresse, daten), ziel)
        # The group has only ignored ones left. It does not even stand there.
        self.assertNotContains(
            self.client.get(reverse("warengruppe_list")), self.gruppe.get_absolute_url()
        )
        # The product is in the product list only if "Not countable" is chosen.
        liste = reverse("ware_list")
        kein = Warenfamilie.nicht_zaehlbar()
        self.assertNotIn(self.cola, self.client.get(liste).context["ware"])
        self.assertIn(self.cola, self.client.get(liste, {"familie": [kein.pk]}).context["ware"])
        # And on the family's page including the product.
        self.assertContains(self.client.get(kein.get_absolute_url()), self.cola.get_absolute_url())
        self.assertContains(self.client.get(self.cola.get_absolute_url()), ziel)

    # -- What the buttons cause ---------------------------------------------

    def test_art_je_ware_umstellen(self):
        liste = reverse("ware_list") + "?q=artikel+c&seite=1"
        antwort = self.client.post(
            reverse("ware_inventurart", args=[self.cola.pk]),
            {"inventurart": jahresinventur().pk, "weiter": liste},
        )
        self.assertRedirects(antwort, liste)
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.inventurart, jahresinventur())

    def test_unbekannte_art_aendert_nichts(self):
        antwort = self.client.post(
            reverse("ware_inventurart", args=[self.cola.pk]), {"inventurart": "0"}
        )
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.inventurart, monatsinventur())
        self.assertRedirects(antwort, self.cola.get_absolute_url())

    def test_ware_aus_der_liste_inaktiv_und_eingestellt(self):
        liste = reverse("ware_list") + "?q=artikel+c"
        ziel = reverse("ware_status", args=[self.cola.pk])
        for status in (Artikelstatus.INAKTIV, Artikelstatus.EINGESTELLT, Artikelstatus.AKTIV):
            with self.subTest(status=status):
                antwort = self.client.post(ziel, {"status": status, "weiter": liste})
                self.assertRedirects(antwort, liste)
                self.artikel.refresh_from_db()
                self.assertEqual(self.artikel.status, status)

    def test_ware_ignorieren_und_wieder_aufnehmen(self):
        ziel = reverse("ware_status", args=[self.cola.pk])
        self.client.post(ziel, {"status": Artikelstatus.IGNORIERT})
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.status, Artikelstatus.IGNORIERT)
        self.client.post(ziel, {"status": "WIEDER_AUFNEHMEN"})
        self.artikel.refresh_from_db()
        self.cola.refresh_from_db()
        self.assertEqual(self.artikel.status, Artikelstatus.AKTIV)
        self.assertIsNone(self.cola.warenfamilie)

    def test_wieder_aufnehmen_laesst_einer_ware_mit_familie_die_familie(self):
        """A stale page sends the button again. Until 10/2026 the family vanished silently."""
        familie = Warenfamilie.objects.create(name="Warenfamilie F")
        self.cola.warenfamilie = familie
        self.cola.save()

        antwort = self.client.post(
            reverse("ware_status", args=[self.cola.pk]), {"status": "WIEDER_AUFNEHMEN"}, follow=True
        )

        self.assertContains(antwort, "liegt nicht in „Nicht zählbar“")
        self.cola.refresh_from_db()
        self.assertEqual(self.cola.warenfamilie, familie)

    def test_hausartikel_aus_der_liste_umschalten(self):
        liste = reverse("hausartikel_list", args=[self.haus.pk]) + "?q=artikel+c"
        ziel = reverse("hausartikel_status", args=[self.artikel.pk])
        for status in (Artikelstatus.INAKTIV, Artikelstatus.EINGESTELLT, Artikelstatus.AKTIV):
            with self.subTest(status=status):
                antwort = self.client.post(ziel, {"status": status, "weiter": liste})
                self.assertRedirects(antwort, liste)
                self.artikel.refresh_from_db()
                self.assertEqual(self.artikel.status, status)

    def test_blockiertes_aktivieren_kehrt_trotzdem_zur_liste_zurueck(self):
        """The number has been reassigned in the meantime. The message must not lead nowhere."""
        liste = reverse("hausartikel_list", args=[self.haus.pk])
        self.artikel.einstellen()
        Hausartikel.objects.create(
            haus=self.haus,
            ware=Ware.objects.create(name="Artikel C neu", basiseinheit=self.stueck),
            warengruppe=self.gruppe,
            artikelnummer="910001",
            name="Artikel C 0,33 neu",
        )
        antwort = self.client.post(
            reverse("hausartikel_status", args=[self.artikel.pk]),
            {"status": Artikelstatus.AKTIV, "weiter": liste},
        )
        self.assertRedirects(antwort, liste)
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.status, Artikelstatus.EINGESTELLT)

    def test_warengruppe_aus_der_liste_umstellen(self):
        liste = reverse("warengruppe_list")
        antwort = self.client.post(
            reverse("warengruppe_inventurart", args=[self.gruppe.pk]),
            {"inventurart": jahresinventur().pk, "weiter": liste},
        )
        self.assertRedirects(antwort, liste)
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.inventurart, jahresinventur())

    # -- The way without a page reload --------------------------------------

    def test_liste_bietet_dem_skript_seine_angriffspunkte(self):
        """Without 'data-liste' and 'data-sofort' the script would fall back to the normal way."""
        html = self.client.get(reverse("ware_list")).content.decode()
        self.assertIn("data-liste>", html)
        self.assertIn("data-sofort", html)
        self.assertIn("data-meldungen", html)

    def test_aktion_liefert_die_frische_liste_zurueck(self):
        """What 'fetch' gets: the redirect leads to the fully rendered list.

        The script relies on that: it renders nothing itself but takes over list
        area and messages from this response. If this chain breaks, one would
        have two truths about what a row looks like.
        """
        liste = reverse("ware_list")
        jahr = jahresinventur()
        antwort = self.client.post(
            reverse("ware_inventurart", args=[self.cola.pk]),
            {"inventurart": jahr.pk, "weiter": liste},
            headers={"x-requested-with": "fetch"},
            follow=True,
        )
        self.assertEqual(antwort.redirect_chain, [(liste, 302)])
        html = antwort.content.decode()
        self.assertIn("data-liste>", html)
        self.assertIn("zählt jetzt in „Jahresinventur“", html)
        self.assertRegex(html, rf'name="inventurart" value="{jahr.pk}"[^>]*disabled')

    # -- Security -----------------------------------------------------------

    def test_fremdes_ziel_wird_verworfen(self):
        """'weiter' is user input. Otherwise the button would be an open redirect."""
        for ziel in ("https://example.com/phish", "//example.com/phish"):
            with self.subTest(ziel=ziel):
                antwort = self.client.post(
                    reverse("ware_status", args=[self.cola.pk]),
                    {"status": Artikelstatus.INAKTIV, "weiter": ziel},
                )
                self.assertRedirects(antwort, self.cola.get_absolute_url())


class LiveFilter(AngemeldeteSeiten):
    """The filters act at once, server-side it stays the same page.

    Checked here is only that the ingredients for that are in the template:
    the script swaps '[data-liste]' and the '[data-ersetzen]' places, and
    without JavaScript "Apply" has to keep working.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Ost")
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        Ware.objects.create(name="Artikel C", basiseinheit=cls.stueck)

    def test_zutaten_stehen_in_der_vorlage(self):
        antwort = self.client.get(reverse("ware_list"))
        for stueck in ("data-filter", "data-anwenden", "data-liste", 'data-ersetzen="anzahl"'):
            with self.subTest(stueck=stueck):
                self.assertContains(antwort, stueck)

    def test_anwenden_bleibt_der_weg_ohne_javascript(self):
        """The same address the script fetches, only as a whole page."""
        antwort = self.client.get(reverse("ware_list"), {"q": "artikel c"})
        self.assertEqual([ware.name for ware in antwort.context["ware"]], ["Artikel C"])
        self.assertContains(antwort, "data-liste")


class SortierenUeberSpaltenkoepfe(AngemeldeteSeiten):
    """A click on the header sorts ascending, a second one descending.

    Behind it there is always the secondary key of the list: for house
    articles the number, otherwise the name.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Sortierhaus")
        stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.getraenke = Warengruppe.objects.create(name="Warengruppe G")
        cls.suesses = Warengruppe.objects.create(name="Warengruppe T")
        cls.limo = Warenfamilie.objects.create(name="Warenfamilie M", leiteinheit=stueck)
        for nummer, name, gruppe, familie in (
            ("101003", "Artikel C", cls.getraenke, cls.limo),
            ("101001", "Ware S", cls.suesses, None),
            ("101002", "Artikel F", cls.getraenke, cls.limo),
        ):
            ware = Ware.objects.create(name=name, basiseinheit=stueck, warenfamilie=familie)
            Hausartikel.objects.create(
                haus=cls.haus,
                ware=ware,
                warengruppe=gruppe,
                artikelnummer=nummer,
                name=name,
            )
        cls.hausartikel = reverse("hausartikel_list", args=[cls.haus.pk])

    def nummern(self, **parameter):
        antwort = self.client.get(self.hausartikel, parameter)
        return [artikel.artikelnummer for artikel in antwort.context["hausartikel"]]

    def test_vorgabe_ist_die_nummer(self):
        self.assertEqual(self.nummern(), ["101001", "101002", "101003"])

    def test_absteigend_mit_minus(self):
        self.assertEqual(self.nummern(sortierung="-nummer"), ["101003", "101002", "101001"])

    def test_zweitschluessel_bleibt_aufsteigend(self):
        """Descending by product group, within the group still by number."""
        self.assertEqual(self.nummern(sortierung="-warengruppe"), ["101001", "101002", "101003"])

    def eigene(self, *waren):
        """Own articles without a number, as a workshop carries them."""
        stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        for name in waren:
            Hausartikel.objects.create(
                haus=self.haus,
                ware=Ware.objects.create(name=name, basiseinheit=stueck),
                warengruppe=self.getraenke,
                quelle=QUELLE_EIGEN,
                artikelnummer="",
                name=name,
            )

    def namen(self, **parameter):
        antwort = self.client.get(self.hausartikel, parameter)
        return [
            artikel.artikelnummer or artikel.ware.name for artikel in antwort.context["hausartikel"]
        ]

    def test_eigene_artikel_ohne_nummer_kommen_in_beiden_richtungen_zuletzt(self):
        """Among each other by their product (decided 2026-09-23). Until 10/2026 they came first."""
        self.eigene("Ware Y", "Ware X")
        self.assertEqual(self.namen(), ["101001", "101002", "101003", "Ware X", "Ware Y"])
        self.assertEqual(
            self.namen(sortierung="-nummer"), ["101003", "101002", "101001", "Ware Y", "Ware X"]
        )

    def test_auch_als_zweitschluessel_zuletzt(self):
        """Within a product group the own ones follow the numbered ones."""
        self.eigene("Ware X")
        self.assertEqual(
            self.namen(sortierung="-warengruppe"), ["101001", "101002", "101003", "Ware X"]
        )
        antwort = self.client.get(self.hausartikel, {"gliederung": "warengruppe"})
        self.assertEqual(
            [
                artikel.artikelnummer or artikel.ware.name
                for artikel in antwort.context["gruppen"][0]["eintraege"]
            ],
            ["101002", "101003", "Ware X"],
        )

    def test_der_kopf_zeigt_den_naechsten_klick(self):
        antwort = self.client.get(self.hausartikel, {"sortierung": "name", "status": "alle"})
        self.assertContains(antwort, "sortierung=-name")
        # The active filter rides along in the link.
        self.assertContains(antwort, "status=alle")

    def test_sortierung_faehrt_im_filterformular_mit(self):
        """Otherwise it would drop out at the next filtering."""
        antwort = self.client.get(self.hausartikel, {"sortierung": "-name"})
        self.assertContains(antwort, 'name="sortierung" value="-name"')

    def test_nach_warengruppe_gegliedert_ohne_blaettern(self):
        antwort = self.client.get(self.hausartikel, {"gliederung": "warengruppe"})
        gruppen = antwort.context["gruppen"]
        self.assertEqual([gruppe["name"] for gruppe in gruppen], ["Warengruppe G", "Warengruppe T"])
        self.assertEqual(
            [artikel.artikelnummer for artikel in gruppen[0]["eintraege"]], ["101002", "101003"]
        )
        self.assertFalse(antwort.context["is_paginated"])

    def test_ware_nach_familie_ohne_familie_zuletzt(self):
        antwort = self.client.get(reverse("ware_list"), {"gliederung": "familie"})
        gruppen = antwort.context["gruppen"]
        self.assertEqual([g.name for g in gruppen], ["Warenfamilie M", "Ohne Warenfamilie"])
        self.assertEqual([w.name for w in gruppen[0].eintraege], ["Artikel C", "Artikel F"])

    def test_einfache_liste_nach_zaehlung(self):
        """The count is annotated. Sorting is only possible afterwards."""
        antwort = self.client.get(reverse("warengruppe_list"), {"sortierung": "-artikel"})
        self.assertEqual(
            [g.name for g in antwort.context["warengruppen"]], ["Warengruppe G", "Warengruppe T"]
        )

    def test_unbekannte_spalte_bricht_nichts(self):
        antwort = self.client.get(reverse("behaelter_list"), {"sortierung": "-gibtsnicht"})
        self.assertEqual(antwort.status_code, 200)


class WarengruppeInSeiten(AngemeldeteSeiten):
    """The products of a group come forty at a time. Until 10/2026 all at once."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Ost")
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        for nummer in range(45):
            ware = Ware.objects.create(name=f"Ware {nummer:02d}", basiseinheit=stueck)
            Hausartikel.objects.create(
                haus=cls.haus,
                ware=ware,
                warengruppe=cls.gruppe,
                artikelnummer=f"101{nummer:03d}",
                name=ware.name,
            )

    def test_vierzig_je_seite_und_die_zahl_im_kopf_bleibt_die_ganze(self):
        antwort = self.client.get(reverse("warengruppe_detail", args=[self.gruppe.pk]))
        self.assertContains(antwort, "45 Waren · 45 Hausartikel")
        self.assertEqual(antwort.context["page_obj"].paginator.num_pages, 2)
        self.assertEqual(len(antwort.context["waren"]), 40)
        self.assertContains(antwort, "Seitennavigation oben")

        zweite = self.client.get(reverse("warengruppe_detail", args=[self.gruppe.pk]), {"page": 2})
        self.assertEqual(len(zweite.context["waren"]), 5)
        self.assertContains(zweite, "Ware 44")
        self.assertNotContains(zweite, "Ware 00")
