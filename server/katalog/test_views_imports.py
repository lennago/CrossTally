"""The article import through the page: first the plan, then the adoption."""

import re
from decimal import Decimal
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from django_project.testing import AngemeldeteSeiten, jahresinventur, skript, stylesheet
from organisation.models import Haus

from .forms import ImportFormSet
from .models import (
    Artikelstatus,
    Basiseinheit,
    Dimension,
    Hausartikel,
    Rezepturartikel,
    Rezepturkomponente,
    Ware,
    Warenfamilie,
    Warengruppe,
)


class ImportInZweiSchritten(AngemeldeteSeiten):
    """Uploading shows the plan. Only the second submit writes."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Nord")

    def datei(self, *zeilen) -> SimpleUploadedFile:
        """An already transformed table. The loader passes it through."""
        kopf = "artikelnummer;name;warengruppe;gebinde;anzahl;einheit;haus"
        inhalt = "\n".join([kopf, *zeilen])
        return SimpleUploadedFile("export.csv", inhalt.encode("utf-8"), "text/csv")

    def hochladen(self, *zeilen):
        return self.client.post(
            reverse("hausartikel_import", args=[self.haus.pk]),
            {"datei": self.datei(*zeilen), "format": "winticket"},
        )

    @staticmethod
    def zeilen_je_block(antwort) -> dict[str, list]:
        """The blocks of the plan, per block its rows across all product groups."""
        return {
            block.kennung: [form for gruppe in block.gruppen for form in gruppe.zeilen]
            for block in antwort.context["formset"].bloecke()
        }

    def bloecke_mit_nummern(self, antwort) -> dict[str, list[str]]:
        return {
            kennung: [form["artikelnummer"].value() for form in formen]
            for kennung, formen in self.zeilen_je_block(antwort).items()
        }

    def test_hochladen_zeigt_den_plan_und_schreibt_nichts(self):
        antwort = self.hochladen(
            "101001;Artikel C 0,33;Warengruppe G;Kasten;24;Stück(e);Kino Nord",
            "101002;Karteileiche;Warengruppe G;Karton;0;Stück(e);Kino Nord",
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertTemplateUsed(antwort, "katalog/import_plan.html")
        self.assertFalse(Hausartikel.objects.exists())
        self.assertFalse(Warengruppe.objects.exists())

        bloecke = {
            kennung: len(zeilen) for kennung, zeilen in self.zeilen_je_block(antwort).items()
        }
        # Without a count it is a dead entry: discontinued, not inactive. Blocks
        # for the stock exist only where they contain something.
        self.assertEqual(bloecke, {"aktiv": 1, "inaktiv": 0, "einstellen": 1, "ignorieren": 0})

    def plan_absenden(self, antwort, **abweichend):
        """Send the shown plan back unchanged, except for 'abweichend'."""
        formset = antwort.context["formset"]
        daten = {
            "schritt": "uebernehmen",
            "plan-TOTAL_FORMS": str(len(formset.forms)),
            "plan-INITIAL_FORMS": str(len(formset.forms)),
            "plan-MIN_NUM_FORMS": "0",
            "plan-MAX_NUM_FORMS": "5000",
        }
        for stelle, form in enumerate(formset.forms):
            for feld, wert in form.initial.items():
                daten[f"plan-{stelle}-{feld}"] = "" if wert is None else wert
        return self.client.post(
            reverse("hausartikel_import", args=[self.haus.pk]), daten | abweichend
        )

    def test_plan_unveraendert_uebernehmen(self):
        antwort = self.plan_absenden(
            self.hochladen(
                "101001;Artikel C 0,33;Warengruppe G;Kasten;24;Stück(e);Kino Nord",
                "101002;Karteileiche;Warengruppe G;Karton;0;Stück(e);Kino Nord",
            )
        )
        self.assertRedirects(antwort, self.haus.get_absolute_url())
        self.assertTrue(Hausartikel.objects.get(artikelnummer="101001").aktiv)
        self.assertEqual(
            Hausartikel.objects.get(artikelnummer="101002").status, Artikelstatus.EINGESTELLT
        )
        self.assertEqual(Warengruppe.objects.get().name, "Warengruppe G")

    def test_zweiter_lauf_derselben_datei_hat_nichts_zu_entscheiden(self):
        """Actively carried is missing from the plan. The discontinued dead entry can come back."""
        zeilen = (
            "101001;Artikel C 0,33;Warengruppe G;Kasten;24;Stück(e);Kino Nord",
            "101002;Karteileiche;Warengruppe G;Karton;0;Stück(e);Kino Nord",
        )
        self.plan_absenden(self.hochladen(*zeilen))
        antwort = self.hochladen(*zeilen)
        bloecke = self.bloecke_mit_nummern(antwort)
        self.assertEqual(
            bloecke,
            {
                "aktiv": [],
                "inaktiv": [],
                "einstellen": [],
                "ignorieren": [],
                "eingestellt": ["101002"],
            },
        )
        self.assertContains(antwort, "1 Artikel führt Kino Nord schon genau so")

        antwort = self.hochladen(zeilen[0])
        self.assertTemplateUsed(antwort, "katalog/import.html")
        self.assertContains(antwort, "nichts, was zu entscheiden wäre")

    def test_bloecke_lassen_sich_auf_und_zuklappen(self):
        """Open by default. 'expand.js' remembers what someone collapses."""
        antwort = self.hochladen("101001;Artikel C 0,33;Warengruppe G;Kasten;24;Stück(e);Kino Nord")
        inhalt = antwort.content.decode()
        # Four blocks for creating, in each once the one product group.
        self.assertEqual(inhalt.count("<details"), 8)
        self.assertIn('data-gruppe="import:aktiv" data-anfangs-offen open', inhalt)
        self.assertIn('data-gruppe="import:aktiv:Warengruppe G"', inhalt)
        self.assertIn("data-alle-gruppen", inhalt)
        self.assertIn("data-anfangs-offen", skript("expand"))

    def test_warengruppen_gliedern_jeden_block(self):
        """The product group stands as a level of its own, with buttons for the whole group."""
        antwort = self.hochladen(
            "101001;Artikel C 0,33;Warengruppe G;Kasten;24;Stück(e);Kino Nord",
            "101002;Ware N;Warenfamilie R;Karton;6;Stück(e);Kino Nord",
        )
        gruppen = {
            (block.kennung, gruppe.name): len(gruppe.zeilen)
            for block in antwort.context["formset"].bloecke()
            for gruppe in block.gruppen
        }
        # Both product groups stand in each of the four blocks: rows migrate
        # there when someone changes the procedure.
        self.assertEqual(
            gruppen,
            {
                ("aktiv", "Warengruppe G"): 1,
                ("aktiv", "Warenfamilie R"): 1,
                ("inaktiv", "Warengruppe G"): 0,
                ("inaktiv", "Warenfamilie R"): 0,
                ("einstellen", "Warengruppe G"): 0,
                ("einstellen", "Warenfamilie R"): 0,
                ("ignorieren", "Warengruppe G"): 0,
                ("ignorieren", "Warenfamilie R"): 0,
            },
        )
        inhalt = antwort.content.decode()
        # The same product group carries the same number in every block, otherwise
        # the script would not find its counterpart when migrating.
        self.assertIn('data-warengruppe-block="aktiv:2"', inhalt)
        self.assertIn('data-warengruppe-block="ignorieren:2"', inhalt)
        self.assertIn('data-alle-setzen="ignorieren"', inhalt)
        self.assertIn("data-alle-setzen", skript("import_plan"))
        # The heading of a product group is a grid ('site.css'): the buttons
        # keep their place, the name is shortened and stays whole in the tooltip.
        self.assertIn('<summary class="warengruppen-kopf', inhalt)
        self.assertIn('title="Warengruppe G">Warengruppe G</strong>', inhalt)
        self.assertIn(".warengruppen-kopf {", stylesheet("site.css"))

    def test_sammelknopf_bietet_kein_ersetzen(self):
        """Replacing demands a second decision per row. A button cannot do that."""
        Hausartikel.objects.create(
            haus=self.haus,
            artikelnummer="101001",
            name="Alter Name",
            warengruppe=Warengruppe.objects.create(name="Warengruppe G"),
            ware=Ware.objects.create(
                name="Alter Name",
                basiseinheit=Basiseinheit.objects.get(dimension=Dimension.STUECK),
            ),
        )
        antwort = self.hochladen("101001;Neuer Name;Warengruppe G;Kasten;24;Stück(e);Kino Nord")
        block = next(b for b in antwort.context["formset"].bloecke() if b.kennung == "umbenannt")
        self.assertEqual([wert for wert, _ in block.vorgehen], ["uebergehen", "aktualisieren"])

    def test_skript_findet_was_die_seite_bietet(self):
        """The blocks are named after the procedure leading into them. The script builds on that."""
        inhalt = self.hochladen(
            "101001;Artikel C 0,33;Warengruppe G;Kasten;24;Stück(e);Kino Nord"
        ).content.decode()
        for vorgehen in ("aktiv", "inaktiv", "einstellen", "ignorieren"):
            self.assertIn(f'data-block="{vorgehen}"', inhalt)
            self.assertIn(f'value="{vorgehen}"', inhalt)
        quelle = skript("import_plan")
        self.assertIn("bloecke[vorgehen]", quelle)
        self.assertIn('wert(zeile, "art")', quelle)
        self.assertIn("[data-nur-bei-ersetzen]", quelle)

    def test_die_waren_stehen_einmal_auf_der_seite(self):
        """All products as options per row: with a thousand rows that was 35 MB."""
        for i in range(3):
            Ware.objects.create(
                name=f"Ware {i}", basiseinheit=Basiseinheit.objects.get(dimension=Dimension.STUECK)
            )
        antwort = self.hochladen(
            "101001;Ware 0;Warengruppe G;Kasten;24;Stück(e);Kino Nord",
            "101002;Neu;Warengruppe G;Kasten;24;Stück(e);Kino Nord",
        )
        inhalt = antwort.content.decode()
        self.assertEqual(inhalt.count('id="ware-optionen"'), 1)
        # The row with a recognised product carries only this one option, the new one none.
        felder = re.findall(r'<select name="plan-\d-ware".*?</select>', inhalt, re.S)
        self.assertEqual([feld.count("<option") for feld in felder], [2, 1])
        self.assertIn("data-optionen", felder[0])

    def test_der_plan_nennt_jede_ware_einmal_und_nicht_je_zeile(self):
        """Until 10/2026 every row named all products anew: twelve seconds for 600 rows."""
        stueck = Basiseinheit.objects.get(name="Stück(e)")
        for nummer in range(30):
            Ware.objects.create(name=f"Ware {nummer:03d}", basiseinheit=stueck)
        zeile = "{n};Artikel {n};Warengruppe G;Kasten;24;Stück(e);Kino Nord"

        def benennungen(anzahl: int) -> int:
            with mock.patch.object(
                Ware, "__str__", autospec=True, side_effect=lambda ware: ware.name
            ) as gezaehlt:
                antwort = self.hochladen(*(zeile.format(n=101000 + i) for i in range(anzahl)))
                self.assertEqual(antwort.status_code, 200)
            return gezaehlt.call_count

        wenige = benennungen(2)
        viele = benennungen(6)
        self.assertLessEqual(viele - wenige, 4 * 3, "höchstens drei Benennungen je weiterer Zeile")

    def test_der_plan_kostet_nicht_je_zeile_eine_abfrage(self):
        def abfragen(anzahl):
            zeilen = [
                f"{601000 + i};Neu {i};Warengruppe G;Kasten;24;Stück(e);Kino Nord"
                for i in range(anzahl)
            ]
            with CaptureQueriesContext(connection) as gezaehlt:
                self.hochladen(*zeilen)
            return len(gezaehlt)

        self.assertEqual(abfragen(3), abfragen(30))

    def test_das_schreiben_kostet_je_zeile_nur_wenige_abfragen(self):
        """Six hundred new rows cost over nine thousand queries until 10/2026.

        Per row the twin check asked the house, the type was asked for a
        product that did not exist yet, and per pack the product was asked
        twice for a name. What remains per row are its five inserts, the
        product lookup, the packs' positions and the rules on the model:
        about a dozen, and the count grows by that per row, not by twenty.
        """

        def abfragen(anzahl):
            zeilen = [
                f"{602000 + i};Neu {i};Warengruppe G;Kasten;24;Stück(e);Kino Nord"
                for i in range(anzahl)
            ]
            antwort = self.hochladen(*zeilen)
            with CaptureQueriesContext(connection) as gezaehlt:
                self.assertEqual(self.plan_absenden(antwort).status_code, 302)
            return len(gezaehlt)

        wenige = abfragen(3)
        viele = abfragen(30)
        je_zeile = (viele - wenige) / 27
        self.assertLessEqual(je_zeile, 14, f"{je_zeile:.1f} Abfragen je weiterer Zeile")

    def test_bezeichnung_steht_fest(self):
        """By number and designation the next import recognises the article."""
        antwort = self.hochladen("101001;Artikel C 0,33;Warengruppe G;Kasten;24;Stück(e);Kino Nord")
        self.assertContains(
            antwort,
            '<input type="hidden" name="plan-0-name" value="Artikel C 0,33" id="id_plan-0-name">',
            html=True,
        )

    def test_gebinde_und_art_lassen_sich_aendern(self):
        antwort = self.plan_absenden(
            self.hochladen("101001;Artikel C 0,33;Warengruppe G;Kasten;24;Stück(e);Kino Nord"),
            **{
                "plan-0-gebinde": "Kasten = 20\nFlasche(n) = 1",
                "plan-0-inventurart": str(jahresinventur().pk),
            },
        )
        self.assertEqual(antwort.status_code, 302)
        artikel = Hausartikel.objects.get(artikelnummer="101001")
        self.assertEqual(artikel.inventurart, jahresinventur())
        self.assertEqual(
            sorted(artikel.ware.gebinde.values_list("name", flat=True)),
            ["Flasche(n)", "Kasten"],
        )

    def test_zeile_laesst_sich_auf_inaktiv_zuruecksetzen(self):
        antwort = self.plan_absenden(
            self.hochladen("101001;Artikel C 0,33;Warengruppe G;Kasten;24;Stück(e);Kino Nord"),
            **{"plan-0-vorgehen": "inaktiv"},
        )
        self.assertEqual(antwort.status_code, 302)
        self.assertFalse(Hausartikel.objects.get(artikelnummer="101001").aktiv)

    def test_neuen_artikel_nicht_anlegen_steht_nicht_zur_wahl(self):
        """It would otherwise come back on every import. For that there is "Discontinued"."""
        antwort = self.hochladen("101001;Artikel C 0,33;Warengruppe G;Kasten;24;Stück(e);Kino Nord")
        self.assertEqual(
            [wert for wert, _ in antwort.context["formset"].forms[0].fields["vorgehen"].choices],
            ["aktiv", "inaktiv", "einstellen", "ignorieren"],
        )
        antwort = self.plan_absenden(antwort, **{"plan-0-vorgehen": "uebergehen"})
        self.assertEqual(antwort.status_code, 200)
        self.assertIn("vorgehen", antwort.context["formset"].forms[0].errors)
        self.assertFalse(Hausartikel.objects.exists())

    def test_zeile_laesst_sich_eingestellt_anlegen(self):
        antwort = self.plan_absenden(
            self.hochladen("101001;Artikel C 0,33;Warengruppe G;Kasten;24;Stück(e);Kino Nord"),
            **{"plan-0-vorgehen": "einstellen"},
        )
        self.assertEqual(antwort.status_code, 302)
        self.assertEqual(Hausartikel.objects.get().status, Artikelstatus.EINGESTELLT)

    def test_kaputter_gebindetext_schreibt_nichts(self):
        antwort = self.plan_absenden(
            self.hochladen("101001;Artikel C 0,33;Warengruppe G;Kasten;24;Stück(e);Kino Nord"),
            **{"plan-0-gebinde": "Kasten ist zwanzig"},
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "erwartet wird")
        self.assertFalse(Hausartikel.objects.exists())

    def test_vorhandenen_satz_aktualisieren(self):
        gruppe = Warengruppe.objects.create(name="Warengruppe G")
        sammel = Ware.objects.create(
            name="Artikel C 0,33", basiseinheit=Basiseinheit.objects.get(dimension=Dimension.STUECK)
        )
        alt = Hausartikel.objects.create(
            haus=self.haus,
            ware=sammel,
            warengruppe=gruppe,
            artikelnummer="101001",
            name="Artikel C alt",
        )
        antwort = self.plan_absenden(
            self.hochladen("101001;Artikel C neu;Warengruppe G;Kasten;24;Stück(e);Kino Nord"),
            **{"plan-0-vorgehen": "aktualisieren"},
        )
        self.assertEqual(antwort.status_code, 302)
        alt.refresh_from_db()
        self.assertEqual(alt.name, "Artikel C neu")
        self.assertEqual(Hausartikel.objects.count(), 1)

    def test_vorhandener_satz_muss_zu_haus_und_nummer_gehoeren(self):
        """'vorhanden_pk' comes from the form. "Change" would otherwise hit another house."""
        kino_sued = Haus.objects.create(name="Kino Süd")
        ware = Ware.objects.create(
            name="Ware C", basiseinheit=Basiseinheit.objects.get(dimension=Dimension.STUECK)
        )
        fremd = Hausartikel.objects.create(
            haus=kino_sued,
            ware=ware,
            warengruppe=Warengruppe.objects.create(name="Warengruppe S"),
            artikelnummer="777777",
            name="Ware C Kino Süd",
        )
        formset = ImportFormSet(
            {
                "plan-TOTAL_FORMS": "1",
                "plan-INITIAL_FORMS": "0",
                "plan-0-artikelnummer": "301001",
                "plan-0-warengruppe": "Warengruppe S",
                "plan-0-einheit": "Stück(e)",
                "plan-0-art": "umbenannt",
                "plan-0-vorhanden_pk": str(fremd.pk),
                "plan-0-name": "Umbenannt",
                "plan-0-ware": str(ware.pk),
                "plan-0-inventurart": "",
                "plan-0-gebinde": "",
                "plan-0-vorgehen": "aktualisieren",
            },
            prefix="plan",
        )
        formset.haus = self.haus
        self.assertFalse(formset.is_valid())
        self.assertIn("vorgehen", formset.forms[0].errors)

    def test_nicht_zaehlbar_geht_nicht_fuer_eine_ware_in_einer_rezeptur(self):
        """The same rule as on the product's button. The import is no way around it."""
        dip = Ware.objects.create(
            name="Artikel D", basiseinheit=Basiseinheit.objects.get(dimension=Dimension.STUECK)
        )
        portion = Rezepturartikel.objects.create(name="Portion D", basiseinheit=dip.basiseinheit)
        Rezepturkomponente.objects.create(
            rezepturartikel=portion, ware=dip, menge_in_basiseinheit=Decimal("1")
        )
        antwort = self.plan_absenden(
            self.hochladen("301001;Artikel D;Warengruppe S;Stück;1;Stück(e);Kino Nord"),
            **{"plan-0-vorgehen": "ignorieren", "plan-0-ware": str(dip.pk)},
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Rezeptur")
        self.assertFalse(Hausartikel.objects.exists())
        dip.refresh_from_db()
        self.assertFalse(dip.ist_nicht_zaehlbar)

    def test_vorhandenen_satz_ersetzen(self):
        gruppe = Warengruppe.objects.create(name="Warengruppe G")
        sammel = Ware.objects.create(
            name="Irgendwas", basiseinheit=Basiseinheit.objects.get(dimension=Dimension.STUECK)
        )
        alt = Hausartikel.objects.create(
            haus=self.haus,
            ware=sammel,
            warengruppe=gruppe,
            artikelnummer="101001",
            name="Alte Ware",
        )
        antwort = self.plan_absenden(
            self.hochladen("101001;Neue Ware;Warengruppe G;Kasten;24;Stück(e);Kino Nord"),
            **{"plan-0-vorgehen": "ersetzen", "plan-0-ware": ""},
        )
        self.assertEqual(antwort.status_code, 302)
        alt.refresh_from_db()
        self.assertFalse(alt.aktiv)
        neu = Hausartikel.objects.get(artikelnummer="101001", aktiv=True)
        self.assertEqual(neu.name, "Neue Ware")

    def test_ersetzen_fragt_womit_der_neue_entsteht(self):
        """The same choice as for a new article, here as "inactive"."""
        alt = Hausartikel.objects.create(
            haus=self.haus,
            ware=Ware.objects.create(
                name="Irgendwas", basiseinheit=Basiseinheit.objects.get(dimension=Dimension.STUECK)
            ),
            warengruppe=Warengruppe.objects.create(name="Warengruppe G"),
            artikelnummer="101001",
            name="Alte Ware",
        )
        plan = self.hochladen("101001;Neue Ware;Warengruppe G;Kasten;24;Stück(e);Kino Nord")
        self.assertContains(plan, "data-nur-bei-ersetzen")
        antwort = self.plan_absenden(
            plan,
            **{
                "plan-0-vorgehen": "ersetzen",
                "plan-0-status_neu": "inaktiv",
                "plan-0-ware": "",
            },
        )
        self.assertEqual(antwort.status_code, 302)
        alt.refresh_from_db()
        self.assertEqual(alt.status, Artikelstatus.EINGESTELLT)
        self.assertEqual(Hausartikel.objects.get(name="Neue Ware").status, Artikelstatus.INAKTIV)

    def test_konflikt_zeigt_nach_einem_fehler_noch_worin_er_besteht(self):
        """The hints ride along in the form. Without them the row could not be understood."""
        Hausartikel.objects.create(
            haus=self.haus,
            ware=Ware.objects.create(
                name="Artikel C 0,33",
                basiseinheit=Basiseinheit.objects.get(dimension=Dimension.STUECK),
            ),
            warengruppe=Warengruppe.objects.create(name="Warengruppe G"),
            artikelnummer="101001",
            name="Artikel C 0,33",
        )
        plan = self.hochladen("101001;Artikel C 0,33;Warengruppe G;Kasten;24;Stück(e);Kino Nord")
        self.assertEqual(
            [kennung for kennung, zeilen in self.zeilen_je_block(plan).items() if zeilen],
            ["konflikt"],
        )
        self.assertContains(plan, "Diese Menge ist bei „Artikel C 0,33“ nicht hinterlegt")
        antwort = self.plan_absenden(
            plan, **{"plan-0-vorgehen": "aktualisieren", "plan-0-gebinde": "kaputt"}
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Diese Menge ist bei „Artikel C 0,33“ nicht hinterlegt")

        antwort = self.plan_absenden(
            plan, **{"plan-0-vorgehen": "aktualisieren", "plan-0-gebinde": "Kasten = 24"}
        )
        self.assertEqual(antwort.status_code, 302)
        # The conflict is thus settled. The file says nothing different any more.
        erneut = self.hochladen("101001;Artikel C 0,33;Warengruppe G;Kasten;24;Stück(e);Kino Nord")
        self.assertTemplateUsed(erneut, "katalog/import.html")

    def konflikt_hochladen(self):
        """Artikel C is carried, but the file names a quantity that is not stored."""
        self.cola = Hausartikel.objects.create(
            haus=self.haus,
            ware=Ware.objects.create(
                name="Artikel C 0,33",
                basiseinheit=Basiseinheit.objects.get(dimension=Dimension.STUECK),
            ),
            warengruppe=Warengruppe.objects.create(name="Warengruppe G"),
            artikelnummer="101001",
            name="Artikel C 0,33",
        )
        return self.hochladen(
            "101001;Artikel C 0,33;Warengruppe G;Kasten;24;Stück(e);Kino Nord",
            "101002;Artikel F 0,33;Warengruppe G;Kasten;24;Stück(e);Kino Nord",
        )

    def test_konflikt_kommt_ohne_vorauswahl(self):
        """A preselected "Leave" would look like decided. What is asked for is a decision."""
        plan = self.konflikt_hochladen()
        konflikt = next(f for f in plan.context["formset"].forms if f.zeilenart == "konflikt")
        self.assertEqual(konflikt["vorgehen"].value(), "")
        self.assertNotIn("checked", str(konflikt["vorgehen"]))
        # Both blocks stand there: the empty one is the target the row migrates to.
        bloecke = self.bloecke_mit_nummern(plan)
        self.assertEqual(bloecke["konflikt"], ["101001"])
        self.assertEqual(bloecke["behoben"], [])
        self.assertContains(plan, "Behobene Konflikte")

    def test_offener_konflikt_haelt_das_uebernehmen_auf(self):
        """The other rows do not arise either. Writing happens entirely or not at all."""
        antwort = self.plan_absenden(self.konflikt_hochladen())
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Bitte entscheiden: lassen oder ändern.")
        self.assertContains(antwort, "Offene Konflikte: 1.")
        self.assertFalse(Hausartikel.objects.filter(artikelnummer="101002").exists())
        self.assertEqual(self.bloecke_mit_nummern(antwort)["konflikt"], ["101001"])

    def test_auch_lassen_behebt_den_konflikt(self):
        plan = self.konflikt_hochladen()
        stelle = next(
            i for i, f in enumerate(plan.context["formset"].forms) if f.zeilenart == "konflikt"
        )
        antwort = self.plan_absenden(plan, **{f"plan-{stelle}-vorgehen": "uebergehen"})
        self.assertRedirects(antwort, self.haus.get_absolute_url())
        self.assertTrue(Hausartikel.objects.filter(artikelnummer="101002").exists())
        self.assertFalse(self.cola.ware.gebinde.exists())

    def test_entschiedener_konflikt_steht_bei_den_behobenen(self):
        """After an error on submit the row stays where the choice moved it."""
        plan = self.konflikt_hochladen()
        formen = plan.context["formset"].forms
        konflikt = next(i for i, f in enumerate(formen) if f.zeilenart == "konflikt")
        andere = next(i for i, f in enumerate(formen) if f.zeilenart == "neu")
        antwort = self.plan_absenden(
            plan,
            **{f"plan-{konflikt}-vorgehen": "aktualisieren", f"plan-{andere}-gebinde": "kaputt"},
        )
        self.assertEqual(antwort.status_code, 200)
        bloecke = self.bloecke_mit_nummern(antwort)
        self.assertEqual(bloecke["konflikt"], [])
        self.assertEqual(bloecke["behoben"], ["101001"])
        self.assertNotContains(antwort, "Offene Konflikte: 1.")

    def test_skript_sperrt_das_uebernehmen_bei_offenen_konflikten(self):
        inhalt = self.konflikt_hochladen().content.decode()
        for kennung in ("konflikt", "behoben"):
            self.assertIn(f'data-block="{kennung}"', inhalt)
        self.assertIn("data-uebernehmen", inhalt)
        self.assertIn("data-offene-konflikte", inhalt)
        quelle = skript("import_plan")
        self.assertIn("bloecke.behoben", quelle)
        self.assertIn("[data-uebernehmen]", quelle)
        self.assertIn("[data-offene-konflikte]", quelle)

    def mehrfach_konflikt_hochladen(self):
        """Another product group **and** a quantity the product lacks."""
        self.konflikt_hochladen()
        return self.hochladen("101001;Artikel C 0,33;Warengruppe M;Kasten;24;Stück(e);Kino Nord")

    def test_unbekanntes_gebinde_steht_schon_im_feld(self):
        plan = self.konflikt_hochladen()
        konflikt = next(f for f in plan.context["formset"].forms if f.zeilenart == "konflikt")
        # The product carries no pack yet. In the field stands what the file
        # brings, plus the loose single unit.
        self.assertEqual(konflikt["gebinde"].value(), "Kasten = 24\nEinzeln = 1")
        # A single deviation, there is nothing to choose individually.
        self.assertFalse(konflikt.teile_waehlbar)
        self.assertNotContains(plan, "data-nur-bei-aendern")

    def test_skript_laeuft_auch_ohne_neue_artikel(self):
        """A maintained house: the file brings only a conflict, no body for "aktiv".

        The script used to bail out on the missing block, and with it the
        group buttons and the lock on the submit button were dead.
        """
        self.konflikt_hochladen()
        plan = self.hochladen("101001;Artikel C 0,33;Warengruppe G;Kasten;24;Stück(e);Kino Nord")
        inhalt = plan.content.decode()
        self.assertNotIn('data-block="aktiv"', inhalt)
        self.assertIn('data-block="konflikt"', inhalt)
        quelle = skript("import_plan")
        self.assertNotIn("bloecke.aktiv) return", quelle)
        self.assertIn("Object.keys(bloecke).length", quelle)

    def test_mehrere_abweichungen_lassen_sich_einzeln_uebernehmen(self):
        plan = self.mehrfach_konflikt_hochladen()
        zeile = plan.context["formset"].forms[0]
        self.assertTrue(zeile.teile_waehlbar)
        self.assertEqual(
            [wert for wert, _ in zeile.fields["uebernehmen"].choices], ["warengruppe", "gebinde"]
        )
        self.assertContains(plan, "data-nur-bei-aendern")
        self.assertIn("[data-nur-bei-aendern]", skript("import_plan"))
        # Several entries may be ticked at once. They stand in another colour
        # than the single choice next to them, and a ticked one gets a check
        # mark from the stylesheet.
        inhalt = plan.content.decode()
        self.assertIn(
            '<label class="btn btn-outline-primary" for="id_plan-0-uebernehmen_0">', inhalt
        )
        self.assertIn(
            '<label class="btn btn-outline-secondary" for="id_plan-0-vorgehen_0">', inhalt
        )
        self.assertIn('.btn-check[type="checkbox"]:checked + .btn::before', stylesheet("site.css"))

        antwort = self.plan_absenden(
            plan, **{"plan-0-vorgehen": "aktualisieren", "plan-0-uebernehmen": ["gebinde"]}
        )
        self.assertEqual(antwort.status_code, 302)
        self.cola.refresh_from_db()
        self.assertEqual(self.cola.warengruppe.name, "Warengruppe G")
        self.assertTrue(self.cola.ware.gebinde.filter(name="Kasten").exists())

    def test_aendern_ohne_gewaehlten_teil_wird_abgewiesen(self):
        antwort = self.plan_absenden(
            self.mehrfach_konflikt_hochladen(),
            **{"plan-0-vorgehen": "aktualisieren", "plan-0-uebernehmen": []},
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Bitte wählen, was übernommen wird")

    def test_einheit_in_fremder_dimension_braucht_die_umrechnung(self):
        """The file says litres, the product is carried in pieces: the house counts in litres."""
        self.konflikt_hochladen()
        plan = self.hochladen("101001;Artikel C 0,33;Warengruppe G;;0;Liter;Kino Nord")
        self.assertContains(plan, "data-umrechnung")
        self.assertIn("[data-umrechnung]", skript("import_plan"))
        # The label says in which direction the factor applies.
        self.assertContains(plan, 'data-wert="1 {haus} = wie viele {ware}?"')
        antwort = self.plan_absenden(plan, **{"plan-0-vorgehen": "aktualisieren"})
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "weiß nur, wer die Ware kennt")

        antwort = self.plan_absenden(
            plan, **{"plan-0-vorgehen": "aktualisieren", "plan-0-faktor_zur_ware": "0,5"}
        )
        self.assertEqual(antwort.status_code, 302)
        self.cola.refresh_from_db()
        self.assertEqual(self.cola.basiseinheit.name, "Liter")
        self.assertEqual(self.cola.faktor_zur_ware, Decimal("0.5"))
        self.assertEqual(self.cola.ware.basiseinheit.name, "Stück(e)")

    def test_die_umrechnung_nimmt_zwoelf_stellen(self):
        """A litre is 3.0303… bottles of 0.33 l. The field took six places until 10/2026."""
        self.konflikt_hochladen()
        plan = self.hochladen("101001;Artikel C 0,33;Warengruppe G;;0;Liter;Kino Nord")
        antwort = self.plan_absenden(
            plan,
            **{"plan-0-vorgehen": "aktualisieren", "plan-0-faktor_zur_ware": "3,030303030303"},
        )
        self.assertEqual(antwort.status_code, 302)
        self.cola.refresh_from_db()
        self.assertEqual(self.cola.faktor_zur_ware, Decimal("3.030303030303"))

    def test_eine_neue_ware_entsteht_in_der_einheit_der_datei(self):
        self.konflikt_hochladen()
        plan = self.hochladen("101001;Artikel C 0,33;Warengruppe G;;0;Liter;Kino Nord")
        antwort = self.plan_absenden(
            plan,
            **{
                "plan-0-vorgehen": "aktualisieren",
                "plan-0-ware": "",
                "plan-0-neuer_ware": "Artikel C lose",
            },
        )
        self.assertEqual(antwort.status_code, 302)
        self.cola.refresh_from_db()
        self.assertEqual(self.cola.ware.basiseinheit.name, "Liter")
        self.assertEqual(self.cola.basiseinheit.name, "Liter")
        self.assertIsNone(self.cola.faktor_zur_ware)

    def test_uebernehmen_klebt_am_unteren_rand(self):
        self.assertContains(self.konflikt_hochladen(), 'class="fussleiste ')

    def test_gleiche_bezeichnung_unter_anderer_nummer_braucht_eigenen_ware(self):
        """No conflict as a house article. But the product the house already carries."""
        ware = Ware.objects.create(
            name="Beispielartikel",
            basiseinheit=Basiseinheit.objects.get(dimension=Dimension.STUECK),
        )
        Hausartikel.objects.create(
            haus=self.haus,
            ware=ware,
            warengruppe=Warengruppe.objects.create(name="Warengruppe V"),
            artikelnummer="920001",
            name="Beispielartikel",
        )
        plan = self.hochladen("920002;Beispielartikel;Warengruppe E;Karton;12;Stück(e);Kino Nord")
        self.assertIsNone(plan.context["formset"].forms[0].initial["ware"])

        antwort = self.plan_absenden(plan)
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Eine Ware „Beispielartikel“ gibt es schon")
        self.assertEqual(Hausartikel.objects.count(), 1)

        antwort = self.plan_absenden(plan, **{"plan-0-neuer_ware": "Beispielartikel Einkauf"})
        self.assertEqual(antwort.status_code, 302)
        neu = Hausartikel.objects.get(artikelnummer="920002")
        self.assertEqual((neu.name, neu.ware.name), ("Beispielartikel", "Beispielartikel Einkauf"))

    def test_zwei_zeilen_auf_derselben_ware_werden_abgewiesen(self):
        """A house carries the same product only once. Otherwise only the DB would notice."""
        antwort = self.plan_absenden(
            self.hochladen(
                "101001;Artikel C 0,33;Warengruppe G;Kasten;24;Stück(e);Kino Nord",
                "101002;Artikel C 0,33 EK;Warengruppe G;Kasten;24;Stück(e);Kino Nord",
            ),
            **{"plan-1-neuer_ware": "Artikel C 0,33"},
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Ein Haus führt sie nur einmal")
        self.assertFalse(Hausartikel.objects.exists())

    def test_auch_inaktiv_belegt_die_zweite_zeile_die_ware(self):
        """Inactive means: comes back. The slot in the house stays occupied."""
        antwort = self.plan_absenden(
            self.hochladen(
                "101001;Artikel C 0,33;Warengruppe G;Kasten;24;Stück(e);Kino Nord",
                "101002;Artikel C 0,33 EK;Warengruppe G;Kasten;24;Stück(e);Kino Nord",
            ),
            **{"plan-1-neuer_ware": "Artikel C 0,33", "plan-1-vorgehen": "inaktiv"},
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertFalse(Hausartikel.objects.exists())

    def test_ware_die_das_haus_schon_fuehrt_wird_vor_dem_schreiben_abgewiesen(self):
        """Otherwise the row would end as a server error when writing."""
        ware = Ware.objects.create(
            name="Artikel C 0,33", basiseinheit=Basiseinheit.objects.get(dimension=Dimension.STUECK)
        )
        Hausartikel.objects.create(
            haus=self.haus,
            ware=ware,
            warengruppe=Warengruppe.objects.create(name="Warengruppe G"),
            artikelnummer="101009",
            name="Artikel C alt",
            status=Artikelstatus.INAKTIV,
        )
        antwort = self.plan_absenden(
            self.hochladen("101001;Artikel C 0,33 neu;Warengruppe G;Kasten;24;Stück(e);Kino Nord"),
            **{"plan-0-ware": ware.pk, "plan-0-vorgehen": "aktiv"},
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "führt diese Ware schon unter 101009")
        self.assertEqual(Hausartikel.objects.count(), 1)

    def test_ignorieren_legt_unter_nicht_zaehlbar_an(self):
        antwort = self.plan_absenden(
            self.hochladen("101001;Artikel P 0,25;Warengruppe P;Stück;0,25;Stück(e);Kino Nord"),
            **{"plan-0-vorgehen": "ignorieren"},
        )
        self.assertEqual(antwort.status_code, 302)
        artikel = Hausartikel.objects.get()
        self.assertEqual(artikel.ware.warenfamilie, Warenfamilie.nicht_zaehlbar())
        self.assertEqual(artikel.status, Artikelstatus.IGNORIERT)
        self.assertFalse(artikel.ware.gebinde.exists())

    def test_eigener_name_fuer_den_ware(self):
        antwort = self.plan_absenden(
            self.hochladen("101001;Artikel C 0,33 Fl;Warengruppe G;Kasten;24;Stück(e);Kino Nord"),
            **{"plan-0-neuer_ware": "Eigener Name 0,33 l"},
        )
        self.assertEqual(antwort.status_code, 302)
        artikel = Hausartikel.objects.get(artikelnummer="101001")
        self.assertEqual(artikel.name, "Artikel C 0,33 Fl")
        self.assertEqual(artikel.ware.name, "Eigener Name 0,33 l")

    def test_beides_angegeben_wird_abgewiesen(self):
        sammel = Ware.objects.create(
            name="Vorhanden", basiseinheit=Basiseinheit.objects.get(dimension=Dimension.STUECK)
        )
        antwort = self.plan_absenden(
            self.hochladen("101001;Artikel C 0,33;Warengruppe G;Kasten;24;Stück(e);Kino Nord"),
            **{"plan-0-ware": sammel.pk, "plan-0-neuer_ware": "Noch einer"},
        )
        self.assertContains(antwort, "nicht beides")
        self.assertFalse(Hausartikel.objects.exists())

    def test_zwei_haeuser_duerfen_dieselbe_ware_fuehren(self):
        """The rule applies per house. A second house is no conflict."""
        anderes = Haus.objects.create(name="Kino Süd")
        Hausartikel.objects.create(
            haus=anderes,
            ware=Ware.objects.create(
                name="Artikel C 0,33",
                basiseinheit=Basiseinheit.objects.get(dimension=Dimension.STUECK),
            ),
            warengruppe=Warengruppe.objects.create(name="Warengruppe G"),
            artikelnummer="101001",
            name="Artikel C Kino Süd",
        )
        antwort = self.plan_absenden(
            self.hochladen("101001;Artikel C 0,33;Warengruppe G;Kasten;24;Stück(e);Kino Nord")
        )
        self.assertEqual(antwort.status_code, 302)
        self.assertEqual(Hausartikel.objects.filter(haus=self.haus, aktiv=True).count(), 1)

    def test_umgestellte_zeile_landet_im_richtigen_block(self):
        """After an error the server rebuilds the blocks. The row follows along."""
        antwort = self.plan_absenden(
            self.hochladen(
                "101001;Artikel C 0,33;Warengruppe G;Kasten;24;Stück(e);Kino Nord",
                "101002;Artikel F 0,33;Warengruppe G;Kasten;24;Stück(e);Kino Nord",
            ),
            **{"plan-0-vorgehen": "inaktiv", "plan-1-gebinde": "kaputt"},
        )
        self.assertEqual(antwort.status_code, 200)
        bloecke = self.bloecke_mit_nummern(antwort)
        self.assertEqual(bloecke["inaktiv"], ["101001"])
        self.assertEqual(bloecke["aktiv"], ["101002"])

    def test_einheit_steht_in_der_tabelle(self):
        antwort = self.hochladen("101001;Artikel C 0,33;Warengruppe G;Kasten;24;Stück(e);Kino Nord")
        self.assertContains(antwort, '<th scope="col" class="breite-7">Einheit</th>', html=True)
        # Carried along is the id, visible is the short form.
        self.assertContains(antwort, 'value="STUECK"')
        self.assertContains(antwort, "Stk.")


class ImportFehlendeUeberDieSeite(AngemeldeteSeiten):
    """What the file no longer knows stands in the plan and can be closed."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        cls.alt = Hausartikel.objects.create(
            haus=cls.haus,
            ware=Ware.objects.create(name="Ware N", basiseinheit=cls.stueck),
            warengruppe=cls.gruppe,
            artikelnummer="101002",
            name="Ware N",
        )

    def hochladen(self, *zeilen):
        kopf = "artikelnummer;name;warengruppe;gebinde;anzahl;einheit;haus"
        datei = SimpleUploadedFile(
            "export.csv", "\n".join([kopf, *zeilen]).encode("utf-8"), "text/csv"
        )
        return self.client.post(
            reverse("hausartikel_import", args=[self.haus.pk]),
            {"datei": datei, "format": "winticket"},
        )

    def plan_absenden(self, antwort, **abweichend):
        formset = antwort.context["formset"]
        daten = {
            "schritt": "uebernehmen",
            "plan-TOTAL_FORMS": str(len(formset.forms)),
            "plan-INITIAL_FORMS": str(len(formset.forms)),
            "plan-MIN_NUM_FORMS": "0",
            "plan-MAX_NUM_FORMS": "5000",
        }
        for stelle, form in enumerate(formset.forms):
            for feld, wert in form.initial.items():
                daten[f"plan-{stelle}-{feld}"] = "" if wert is None else wert
        return self.client.post(
            reverse("hausartikel_import", args=[self.haus.pk]), daten | abweichend
        )

    def stelle_des_fehlenden(self, antwort) -> int:
        formset = antwort.context["formset"]
        return next(i for i, form in enumerate(formset.forms) if form.zeilenart == "fehlt")

    def test_der_block_steht_da_und_zeigt_keine_wahl_zur_ware(self):
        antwort = self.hochladen("101001;Artikel C 0,33;Warengruppe G;Kasten;24;Stück(e);Kino Nord")
        bloecke = {
            block.kennung: [form for gruppe in block.gruppen for form in gruppe.zeilen]
            for block in antwort.context["formset"].bloecke()
        }
        self.assertEqual([f["artikelnummer"].value() for f in bloecke["fehlt"]], ["101002"])
        self.assertContains(antwort, "Fehlt in der Datei")
        # The product only stands there. To choose is the procedure alone.
        zeile = bloecke["fehlt"][0]
        self.assertTrue(zeile.nur_bestand)
        self.assertIn("Ware N", zeile.warenname)
        self.assertEqual(
            [wert for wert, _ in zeile.fields["vorgehen"].choices],
            ["uebergehen", "inaktiv", "einstellen"],
        )

    def test_unveraendert_uebernommen_bleibt_der_artikel_aktiv(self):
        antwort = self.plan_absenden(
            self.hochladen("101001;Artikel C 0,33;Warengruppe G;Kasten;24;Stück(e);Kino Nord")
        )
        self.assertRedirects(antwort, self.haus.get_absolute_url())
        self.alt.refresh_from_db()
        self.assertEqual(self.alt.status, Artikelstatus.AKTIV)

    def test_eingestellt_gibt_die_ware_fuer_eine_neue_zeile_frei(self):
        """A new number may take over the product as soon as the old row makes room."""
        plan = self.hochladen(f"101003;Ware N;Warengruppe G;Kasten;24;Stück(e);{self.haus.name}")
        stelle = self.stelle_des_fehlenden(plan)
        antwort = self.plan_absenden(
            plan,
            **{
                f"plan-{stelle}-vorgehen": "einstellen",
                f"plan-{1 - stelle}-ware": str(self.alt.ware_id),
            },
        )
        self.assertRedirects(antwort, self.haus.get_absolute_url())
        self.alt.refresh_from_db()
        self.assertEqual(self.alt.status, Artikelstatus.EINGESTELLT)
        neu = Hausartikel.objects.get(artikelnummer="101003")
        self.assertEqual(neu.ware_id, self.alt.ware_id)

    def test_eine_gelassene_zeile_haelt_ihre_ware_fest(self):
        """As long as the row stays carried, no other number can take its product."""
        plan = self.hochladen(f"101003;Ware N;Warengruppe G;Kasten;24;Stück(e);{self.haus.name}")
        stelle = self.stelle_des_fehlenden(plan)
        antwort = self.plan_absenden(plan, **{f"plan-{1 - stelle}-ware": str(self.alt.ware_id)})
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Ein Haus führt sie nur einmal")
        self.assertFalse(Hausartikel.objects.filter(artikelnummer="101003").exists())

    def test_ein_veraenderter_bestand_weist_die_zeile_ab(self):
        """'art' and 'vorhanden_pk' come from outside. They are measured against the stock."""
        plan = self.hochladen("101001;Artikel C 0,33;Warengruppe G;Kasten;24;Stück(e);Kino Nord")
        stelle = self.stelle_des_fehlenden(plan)
        self.alt.einstellen()
        antwort = self.plan_absenden(plan, **{f"plan-{stelle}-vorgehen": "einstellen"})
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Bitte die Datei neu einlesen")
        self.assertEqual(Hausartikel.objects.get(pk=self.alt.pk).status, "EINGESTELLT")
