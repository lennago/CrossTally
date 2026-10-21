"""The run: all houses of a cut-off date on one page.

Three things are delicate here and therefore in the focus: the difference
per house (opposite differences cancel out in the sum), the joint closing
house by house and the lock of the opposite answer about the type of a whole
product when another house of the run has already decided.
"""

import re
from datetime import date
from decimal import Decimal
from unittest import mock
from urllib.parse import urlencode

from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from django_project.testing import (
    inventur_anlegen,
    jahresinventur,
    monatsinventur,
    oeffnet_neuen_tab,
)
from katalog.models import (
    Artikelstatus,
    Basiseinheit,
    Dimension,
    Erwartung,
    ErwartungGebinde,
    Hausartikel,
    Inventurart,
    Ware,
    Warenfamilie,
    Warengruppe,
)
from katalog.templatetags.mengen import teile_zusammenhalten
from organisation.models import Bereich, Haus, Ort

from .closing import (
    GANZE_WARE,
    NUR_DIESER,
    WECHSEL_PRAEFIX,
    Entscheidungen,
    abschliessen,
    festgelegte_arten,
    klaeren,
)
from .evaluation import (
    familien_eines_laufs,
    familien_ueber_inventuren,
    warengruppen_einer_inventur,
)
from .models import (
    Artentscheidung,
    Inventur,
    Inventurergebnis,
    Inventurstatus,
    Sollbestand,
    Zaehlposition,
)
from .pdf import pdf_html
from .services import bemerkungsstand, vermerk_aendern, zaehlstand
from .test_views import STICHTAG, InventurSeiten, gesehen
from .views import Lauf


class LaufSeiten(InventurSeiten):
    """Kino Ost and Kino Süd carry the same product, each house under its own number."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.sued_ort = Ort.objects.create(
            name="Lager", bereich=Bereich.objects.create(name="Keller", haus=cls.zweites)
        )
        cls.sued_artikel = Hausartikel.objects.create(
            haus=cls.zweites,
            ware=cls.ware,
            warengruppe=Warengruppe.objects.get(name="Warengruppe G"),
            artikelnummer="101002",
            name="Artikel C 0,33 Flasche",
        )
        erwartung = Erwartung.objects.create(ort=cls.sued_ort, artikel=cls.sued_artikel)
        ErwartungGebinde.objects.create(erwartung=erwartung, gebinde=cls.kasten)

    def lauf(self, status=Inventurstatus.ZAEHLUNG) -> tuple[Inventur, Inventur]:
        return self.inventur(status=status), self.inventur(status=status, haus=self.zweites)

    @staticmethod
    def zaehlen(inventur: Inventur, kaesten: str = "2") -> None:
        inventur.zaehlpositionen.update(eingabemenge=Decimal(kaesten))

    @staticmethod
    def adresse(name: str = "inventur_lauf", stichtag=STICHTAG) -> str:
        return reverse(name, kwargs={"stichtag": stichtag.isoformat()})

    @staticmethod
    def meldungen(antwort) -> list[str]:
        return [str(meldung) for meldung in get_messages(antwort.wsgi_request)]

    def alle_abschliessen(self, gesehen: dict[str, str] | None = None):
        """Sends "Close all" as the tab "Total" does, with both states of every house."""
        if gesehen is None:
            gesehen = self.staende()
        return self.client.post(self.adresse("inventur_lauf_abschluss"), gesehen)

    @staticmethod
    def staende() -> dict[str, str]:
        """The count state and the remarks' state per house, the latter without the notes' text."""
        felder = {}
        for inventur in Inventur.objects.filter(status=Inventurstatus.ZAEHLUNG):
            felder[f"zaehlstand_{inventur.pk}"] = zaehlstand(inventur)
            felder[f"bemerkungsstand_{inventur.pk}"] = bemerkungsstand(inventur, mit_notizen=False)
        return felder

    @staticmethod
    def zwischenformular(inventur: Inventur) -> dict[str, str]:
        """What the form of that page carries: the states it came with, as hidden fields."""
        return {
            "zaehlstand": zaehlstand(inventur),
            "bemerkungsstand": bemerkungsstand(inventur, mit_notizen=False),
        }

    @staticmethod
    def zwischenseite(inventur: Inventur) -> str:
        """The page of a house with questions in the run, with the states it is compared with."""
        ziel = reverse("inventur_abschluss", args=[inventur.pk])
        abfrage = {
            "lauf": 1,
            "stand": zaehlstand(inventur),
            "bemerkungen": bemerkungsstand(inventur, mit_notizen=False),
        }
        return f"{ziel}?{urlencode(abfrage)}"


class Gesamtansicht(LaufSeiten):
    def test_zeigt_die_reiter_und_jedes_haus(self):
        ost, sued = self.lauf()
        antwort = self.client.get(self.adresse())
        self.assertContains(antwort, "Gesamt")
        for inventur in (ost, sued):
            self.assertContains(antwort, f'href="{inventur.get_absolute_url()}"')
        self.assertContains(antwort, reverse("inventur_lauf_abschluss", args=["2026-01-31"]))

    def test_ein_einzelnes_haus_ist_kein_lauf(self):
        inventur = self.inventur()
        self.assertRedirects(self.client.get(self.adresse()), inventur.get_absolute_url())

    def test_unbekannter_lauf_gibt_es_nicht(self):
        self.lauf()
        for adresse in (
            self.adresse(stichtag=date(2026, 2, 28)),
            "/inventuren/lauf/31.01.2026/",
            # The addresses from before the types: a bookmark leads nowhere.
            "/inventuren/lauf/2026-01-31/MONAT/",
        ):
            with self.subTest(adresse=adresse):
                self.assertEqual(self.client.get(adresse).status_code, 404)

    def test_gegenlaeufige_differenzen_je_haus(self):
        """Ost is missing ten bottles, Süd has ten too many. Summed it would say "fine"."""
        ost, sued = self.lauf()
        self.zaehlen(ost, "2")  # 40 bottles
        self.zaehlen(sued, "3")  # 60 bottles
        Sollbestand.objects.create(inventur=ost, hausartikel=self.artikel, menge_basiseinheit=50)
        Sollbestand.objects.create(
            inventur=sued, hausartikel=self.sued_artikel, menge_basiseinheit=50
        )

        antwort = self.client.get(self.adresse())
        zeile = antwort.context["bloecke"][0].zeilen[0]
        self.assertEqual(zeile.differenz, Decimal("0"))
        self.assertEqual(zeile.je_haus, [Decimal("-10"), Decimal("10")])
        # The plus stands there like the minus. Otherwise the surplus would look
        # like a stock, and the colour separates the find from the shortage.
        self.assertEqual(
            zeile.je_haus_zellen,
            [("-10 Stk.", "differenz-fehlt"), ("+10 Stk.", "differenz-mehr")],
        )
        self.assertTrue(zeile.gegenlaeufig)
        self.assertEqual(antwort.context["anzahl_gegenlaeufig"], 1)
        self.assertContains(antwort, "gegenläufig")
        # The colour stands *in* the cell: on the cell itself Bootstrap would beat it.
        self.assertContains(antwort, '<span class="differenz-fehlt">-10 Stk.</span>')
        self.assertContains(antwort, '<span class="differenz-mehr">+10 Stk.</span>')
        self.assertContains(antwort, '<span class="differenz-passt">0 Stk.</span>')

    def test_eine_differenz_die_auf_null_rundet_ist_nicht_gegenlaeufig(self):
        """Ost counts in kg at 4 bottles per kg. 0.1 kg missing are 0.4 bottles: "0 Stk."."""
        Hausartikel.objects.filter(pk=self.artikel.pk).update(
            basiseinheit=Basiseinheit.objects.get(name="kg"), faktor_zur_ware=Decimal("4")
        )
        ost, sued = self.lauf()
        ost.differenz_von_hand_setzen(True)
        vermerk_aendern(ost, self.artikel.pk, differenz=Decimal("-0.1"))
        self.zaehlen(sued, "3")  # 60 bottles against 50
        Sollbestand.objects.create(
            inventur=sued, hausartikel=self.sued_artikel, menge_basiseinheit=50
        )

        antwort = self.client.get(self.adresse())

        zeile = antwort.context["bloecke"][0].zeilen[0]
        self.assertEqual(
            zeile.je_haus_zellen,
            [("0 Stk.", "differenz-passt"), ("+10 Stk.", "differenz-mehr")],
        )
        self.assertFalse(zeile.gegenlaeufig)
        self.assertEqual(antwort.context["anzahl_gegenlaeufig"], 0)

    def zweiter_artikel_derselben_ware(self, ost: Inventur) -> Hausartikel:
        """A duplicate merged during the count: discontinued, its counted row stays."""
        alt = Hausartikel.objects.create(
            haus=self.haus,
            ware=self.ware,
            warengruppe=Warengruppe.objects.get(name="Warengruppe G"),
            artikelnummer="101009",
            name="Artikel C alt",
            status=Artikelstatus.EINGESTELLT,
        )
        Zaehlposition.objects.create(
            inventur=ost,
            ort=self.ort,
            artikel=alt,
            gebinde=self.kasten,
            eingabemenge=Decimal("1"),
            unerwartet=True,
        )
        return alt

    def test_zwei_artikel_einer_ware_in_einem_haus_zaehlen_zusammen(self):
        """Ost's column is its whole difference, not that of the article read last."""
        ost, sued = self.lauf()
        self.zaehlen(ost, "2")  # 40 against 50
        self.zaehlen(sued, "3")  # 60 against 50
        Sollbestand.objects.create(inventur=ost, hausartikel=self.artikel, menge_basiseinheit=50)
        Sollbestand.objects.create(
            inventur=sued, hausartikel=self.sued_artikel, menge_basiseinheit=50
        )
        self.zweiter_artikel_derselben_ware(ost)  # 20 more, without a target

        for zustand in ("offen", "abgeschlossen"):
            with self.subTest(zustand):
                if zustand == "abgeschlossen":
                    for inventur in (ost, sued):
                        abschliessen(Inventur.objects.get(pk=inventur.pk), Entscheidungen())
                (zeile,) = familien_eines_laufs(list(Lauf.aus_adresse("2026-01-31").inventuren))[
                    0
                ].zeilen
                self.assertEqual(zeile.je_haus, [Decimal("-10"), Decimal("10")])
                self.assertEqual((zeile.ist, zeile.differenz), (Decimal("120"), Decimal("0")))
                self.assertEqual((zeile.haeuser, zeile.ergebnisse, zeile.mit_differenz), (2, 2, 2))
                self.assertTrue(zeile.gegenlaeufig)

        (auswertung,) = familien_ueber_inventuren([ost, sued])[0][0].zeilen
        # Two inventories, both with a difference: no "from 2 of 3".
        self.assertEqual((auswertung.ergebnisse, auswertung.mit_differenz), (2, 2))
        self.assertEqual(auswertung.teilsumme_text, "")

    def test_zaehlt_waren_und_gegenlaeufige_auf_jeder_stufe(self):
        """The line above the table counts the families below as well, as the sheet does."""
        oben = Warenfamilie.objects.create(name="Getränke", leiteinheit=self.stueck)
        self.ware.warenfamilie = Warenfamilie.objects.create(
            name="Säfte", leiteinheit=self.stueck, uebergeordnet=oben
        )
        self.ware.save()
        ost, sued = self.lauf()
        self.zaehlen(ost, "2")  # 40 bottles against 50: ten missing
        self.zaehlen(sued, "3")  # 60 bottles against 50: ten too many
        Sollbestand.objects.create(inventur=ost, hausartikel=self.artikel, menge_basiseinheit=50)
        Sollbestand.objects.create(
            inventur=sued, hausartikel=self.sued_artikel, menge_basiseinheit=50
        )

        antwort = self.client.get(self.adresse())

        self.assertEqual(
            (antwort.context["anzahl_waren"], antwort.context["anzahl_gegenlaeufig"]), (1, 1)
        )
        self.assertContains(antwort, "1 Ware, davon 1 gegenläufig")

    def test_die_differenz_ueber_alle_haeuser_steht_rechts(self):
        """As on the sheet: after the houses it adds up, not in front of them."""
        ost, sued = self.lauf()
        self.zaehlen(ost)
        self.zaehlen(sued)

        antwort = self.client.get(self.adresse())

        familie = antwort.content.decode().split("<details", 1)[1]
        self.assertEqual(
            re.findall(r"<th[^>]*>\s*([^<]+?)\s*</th>", familie.split("</thead>", 1)[0]),
            ["Ware", "Ergebnis", "Soll", "Kino Ost", "Kino Süd", "Differenz"],
        )

    def test_ein_haus_ohne_die_ware_bleibt_leer(self):
        ost, _ = self.lauf()
        self.zaehlen(ost)
        zeile = familien_eines_laufs(list(Inventur.objects.order_by("haus__name")))[0].zeilen[0]
        self.assertEqual(zeile.im_haus, [True, False])
        # Without target "-", without the product nothing at all. Both without
        # colour: nothing was compared.
        self.assertEqual(zeile.je_haus_zellen, [("-", ""), ("", "")])

    def test_ungezaehlt_und_ohne_soll_steht_nicht_da(self):
        """As when freezing: a row without any entry only bloats."""
        self.lauf()
        self.assertEqual(familien_eines_laufs(list(Inventur.objects.all())), [])

    def test_halb_abgeschlossen_rechnet_eingefroren_und_laufend(self):
        ost, sued = self.lauf()
        self.zaehlen(ost, "1")
        self.zaehlen(sued, "2")
        abschliessen(ost, Entscheidungen())
        antwort = self.client.get(self.adresse())
        self.assertEqual(antwort.context["bloecke"][0].zeilen[0].ist, Decimal("60"))
        self.assertContains(antwort, "1 noch offen")

    def test_die_detailseite_steht_als_reiter_darunter(self):
        ost, _ = self.lauf()
        antwort = self.client.get(ost.get_absolute_url())
        self.assertContains(antwort, f'href="{self.adresse()}"')
        self.assertContains(antwort, 'class="nav-link active"')

    def test_allein_keine_reiter(self):
        inventur = self.inventur()
        self.assertNotContains(self.client.get(inventur.get_absolute_url()), "nav-tabs")

    def test_die_kopfzeile_der_liste_fuehrt_in_den_lauf(self):
        self.lauf()
        self.assertContains(self.client.get(reverse("inventur_list")), f'href="{self.adresse()}"')

    def test_mehrere_haeuser_anlegen_fuehrt_in_den_lauf(self):
        antwort = self.client.post(
            reverse("inventur_add"),
            {
                "inventurarten": [monatsinventur().pk],
                "stichtag": "2026-01-31",
                "haeuser": [self.haus.pk, self.zweites.pk],
            },
        )
        self.assertRedirects(antwort, self.adresse())


class GemeinsamerAbschluss(LaufSeiten):
    def test_ohne_fragen_schliesst_jedes_haus_sofort_ab(self):
        ost, sued = self.lauf()
        self.zaehlen(ost)
        self.zaehlen(sued)
        antwort = self.alle_abschliessen()
        self.assertRedirects(antwort, self.adresse())
        self.assertEqual(
            set(Inventur.objects.values_list("status", flat=True)), {Inventurstatus.ABGESCHLOSSEN}
        )

    def test_nur_per_post(self):
        self.lauf()
        self.assertEqual(self.client.get(self.adresse("inventur_lauf_abschluss")).status_code, 405)

    def test_nicht_solange_ein_haus_in_der_vorbereitung_steht(self):
        ost = self.inventur(status=Inventurstatus.ZAEHLUNG)
        self.inventur(haus=self.zweites)
        self.zaehlen(ost)
        antwort = self.alle_abschliessen()
        self.assertRedirects(antwort, self.adresse())
        ost.refresh_from_db()
        self.assertEqual(ost.status, Inventurstatus.ZAEHLUNG)
        self.assertIn("Noch in der Vorbereitung: Kino Süd.", " ".join(self.meldungen(antwort)))
        # The button, not the word: the page's help always names "Close all".
        self.assertNotContains(
            self.client.get(self.adresse()), self.adresse("inventur_lauf_abschluss")
        )

    def test_haus_mit_fragen_kommt_danach_und_fuehrt_zurueck(self):
        ost, sued = self.lauf()
        self.zaehlen(sued)  # Ost stays uncounted: that is a question.
        antwort = self.alle_abschliessen()
        zwischenseite = self.zwischenseite(ost)
        self.assertRedirects(antwort, zwischenseite)
        sued.refresh_from_db()
        self.assertEqual(sued.status, Inventurstatus.ABGESCHLOSSEN)

        seite = self.client.get(zwischenseite)
        self.assertContains(seite, "Gemeinsamer Abschluss")
        self.assertContains(seite, "Kino Süd abgeschlossen")
        self.assertContains(seite, "Zurück zum Lauf")

        antwort = self.client.post(zwischenseite, self.zwischenformular(ost))
        self.assertRedirects(antwort, self.adresse())
        ost.refresh_from_db()
        self.assertEqual(ost.status, Inventurstatus.ABGESCHLOSSEN)

    @staticmethod
    def nachzaehlen(inventur: Inventur, kaesten: str) -> None:
        """As the app does it, through 'save()': that moves the count state."""
        position = inventur.zaehlpositionen.get()
        position.eingabemenge = Decimal(kaesten)
        position.save()

    def test_der_knopf_traegt_den_zaehlstand_jedes_hauses(self):
        ost, sued = self.lauf()
        seite = self.client.get(self.adresse())
        for inventur in (ost, sued):
            self.assertContains(
                seite, f'name="zaehlstand_{inventur.pk}" value="{zaehlstand(inventur)}"'
            )

    def test_neue_werte_seit_dem_laden_schliessen_nichts_ab(self):
        """Counted on after "Total" was loaded: as on the check list, nothing freezes unseen."""
        ost, sued = self.lauf()
        self.zaehlen(ost)
        self.zaehlen(sued)
        gesehen = self.staende()
        self.nachzaehlen(ost, "3")

        antwort = self.alle_abschliessen(gesehen)

        self.assertRedirects(antwort, self.adresse())
        self.assertEqual(
            set(Inventur.objects.values_list("status", flat=True)), {Inventurstatus.ZAEHLUNG}
        )
        self.assertIn(
            "In Kino Ost kamen seit dem Laden der Seite neue Werte",
            " ".join(self.meldungen(antwort)),
        )

    def test_ohne_gesehenen_stand_schliesst_nichts_ab(self):
        ost, sued = self.lauf()
        self.zaehlen(ost)
        self.zaehlen(sued)

        self.alle_abschliessen({})

        self.assertEqual(
            set(Inventur.objects.values_list("status", flat=True)), {Inventurstatus.ZAEHLUNG}
        )

    def test_im_weiteren_lauf_gilt_der_stand_vom_knopf(self):
        """Süd counted on while Ost's questions were open: Süd waits for another look."""
        ost, sued = self.lauf()  # Both uncounted: both have questions.
        self.assertRedirects(self.alle_abschliessen(), self.zwischenseite(ost))
        self.nachzaehlen(sued, "2")

        antwort = self.client.post(self.zwischenseite(ost), self.zwischenformular(ost))

        self.assertRedirects(antwort, self.adresse())
        ost.refresh_from_db()
        sued.refresh_from_db()
        self.assertEqual(
            (ost.status, sued.status), (Inventurstatus.ABGESCHLOSSEN, Inventurstatus.ZAEHLUNG)
        )
        self.assertIn("In Kino Süd kamen", " ".join(self.meldungen(antwort)))

    def test_ein_get_schliesst_nie_ab(self):
        """Back from the history or a second window: the page of a closed house only leads back."""
        ost, sued = self.lauf()
        self.zaehlen(ost)
        self.zaehlen(sued)
        abschliessen(ost, Entscheidungen())

        antwort = self.client.get(reverse("inventur_abschluss", args=[ost.pk]) + "?lauf=1")

        self.assertRedirects(antwort, self.adresse())
        sued.refresh_from_db()
        self.assertEqual(sued.status, Inventurstatus.ZAEHLUNG)
        self.assertIn("schon abgeschlossen", " ".join(self.meldungen(antwort)))

    def test_ein_get_auf_eine_vorbereitung_schliesst_nie_ab(self):
        ost, sued = self.lauf()
        self.zaehlen(ost)
        self.zaehlen(sued)
        west = self.inventur(haus=Haus.objects.create(name="Kino West"))

        antwort = self.client.get(reverse("inventur_abschluss", args=[west.pk]) + "?lauf=1")

        self.assertRedirects(antwort, self.adresse())
        self.assertEqual(
            set(Inventur.objects.values_list("status", flat=True)),
            {Inventurstatus.ZAEHLUNG, Inventurstatus.VORBEREITUNG},
        )

    def test_eine_vorbereitung_haelt_den_lauf_nach_einem_haus_an(self):
        """After the answers of one house it goes on only as far as "Close all" would."""
        ost, sued = self.lauf()
        self.zaehlen(sued)  # Ost stays uncounted: that is a question.
        self.inventur(haus=Haus.objects.create(name="Kino West"))

        antwort = self.client.post(self.zwischenseite(ost), self.zwischenformular(ost))

        self.assertRedirects(antwort, self.adresse())
        ost.refresh_from_db()
        sued.refresh_from_db()
        self.assertEqual(
            (ost.status, sued.status), (Inventurstatus.ABGESCHLOSSEN, Inventurstatus.ZAEHLUNG)
        )
        self.assertIn("Noch in der Vorbereitung: Kino West.", " ".join(self.meldungen(antwort)))

    def test_neue_werte_unter_der_sperre_schliessen_nichts_ab(self):
        """The run compares early, for the message. Final is the comparison under the lock."""
        ost, sued = self.lauf()
        self.zaehlen(ost)
        self.zaehlen(sued)

        with mock.patch("inventur.closing.decisions.zaehlstand", return_value="anders"):
            antwort = self.alle_abschliessen()

        self.assertRedirects(antwort, self.adresse())
        self.assertIn("In Kino Ost kamen", " ".join(self.meldungen(antwort)))
        self.assertEqual(
            set(Inventur.objects.values_list("status", flat=True)), {Inventurstatus.ZAEHLUNG}
        )

    def test_ist_alles_abgeschlossen_sagt_der_knopf_es(self):
        ost, sued = self.lauf()
        for inventur in (ost, sued):
            self.zaehlen(inventur)
            abschliessen(inventur, Entscheidungen())

        antwort = self.alle_abschliessen({})

        self.assertRedirects(antwort, self.adresse())
        self.assertEqual(
            self.meldungen(antwort),
            ["Abgeschlossen wird aus der Zählung heraus. Offen ist keine Inventur mehr."],
        )

    def test_schliesst_ein_zweites_fenster_das_haus_geht_der_lauf_weiter(self):
        """Ost's questions were open, a second window closed Ost. The button only goes on."""
        ost, sued = self.lauf()
        self.zaehlen(sued)  # Ost stays uncounted: that is a question.
        self.assertRedirects(self.alle_abschliessen(), self.zwischenseite(ost))
        zwischenseite = self.zwischenseite(ost)
        abschliessen(ost, Entscheidungen())

        antwort = self.client.post(zwischenseite)

        self.assertRedirects(antwort, self.adresse())
        self.assertEqual(
            set(Inventur.objects.values_list("status", flat=True)),
            {Inventurstatus.ABGESCHLOSSEN},
        )
        self.assertNotIn("nicht in der Zählung", " ".join(self.meldungen(antwort)))

    def test_einzeln_auf_eine_abgeschlossene_schliesst_nichts_mehr(self):
        ost, _ = self.lauf()
        self.zaehlen(ost)
        abschliessen(ost, Entscheidungen())

        antwort = self.client.post(reverse("inventur_abschluss", args=[ost.pk]))

        self.assertRedirects(antwort, ost.get_absolute_url())
        self.assertEqual(
            self.meldungen(antwort),
            [f"{ost} steht nicht in der Zählung. Abgeschlossen wird nur von dort."],
        )

    def test_einzeln_abgeschlossen_geht_es_zur_inventur_zurueck(self):
        ost, _ = self.lauf()
        antwort = self.client.post(reverse("inventur_abschluss", args=[ost.pk]), gesehen(ost))
        self.assertRedirects(antwort, ost.get_absolute_url(), fetch_redirect_response=False)

    def test_pdf_erst_wenn_alle_abgeschlossen_sind(self):
        ost, sued = self.lauf()
        self.zaehlen(ost)
        self.zaehlen(sued)
        abschliessen(ost, Entscheidungen())
        antwort = self.client.get(self.adresse("inventur_lauf_pdf"))
        self.assertRedirects(antwort, self.adresse())
        self.assertNotContains(self.client.get(self.adresse()), "PDF über alle Häuser")

        abschliessen(sued, Entscheidungen())
        seite = self.client.get(self.adresse())
        self.assertContains(seite, "PDF über alle Häuser")
        # Both open in a new tab. The run stays where it was.
        self.assertTrue(oeffnet_neuen_tab(seite, self.adresse("inventur_lauf_pdf")))
        self.assertTrue(oeffnet_neuen_tab(seite, reverse("inventur_pdf", args=[ost.pk])))
        antwort = self.client.get(self.adresse("inventur_lauf_pdf"))
        self.assertEqual(antwort["Content-Type"], "application/pdf")
        self.assertTrue(antwort["Content-Disposition"].startswith("inline;"))


class AbfragenDesBlattsUeberAlleHaeuser(LaufSeiten):
    """The sheet over all houses reads the frozen rows once per house, never per row."""

    def abfragen(self) -> int:
        with CaptureQueriesContext(connection) as gezaehlt:
            antwort = self.client.get(self.adresse("inventur_lauf_pdf"))
            self.assertEqual(antwort["Content-Type"], "application/pdf")
        return len(gezaehlt)

    def weitere_ergebnisse(self, inventur: Inventur, anzahl: int) -> None:
        gruppe = Warengruppe.objects.get(name="Warengruppe G")
        schon = inventur.ergebnisse.count()
        for nummer in range(schon, schon + anzahl):
            ware = Ware.objects.create(
                name=f"Ware {inventur.pk}-{nummer}", basiseinheit=self.stueck
            )
            artikel = Hausartikel.objects.create(
                haus=inventur.haus,
                ware=ware,
                warengruppe=gruppe,
                artikelnummer=f"1019{inventur.pk:02d}{nummer}",
                name=ware.name,
            )
            Inventurergebnis.objects.create(
                inventur=inventur,
                hausartikel=artikel,
                gesamtmenge_basiseinheit=Decimal(nummer),
                eingefroren_am=timezone.now(),
            )

    def test_vier_haeuser_mit_wenigen_und_mit_vielen_zeilen(self):
        inventuren = list(self.lauf())
        for inventur in inventuren:
            self.zaehlen(inventur)
        for name in ("Kino Nord", "Kino West"):
            inventuren.append(
                self.inventur(status=Inventurstatus.ZAEHLUNG, haus=Haus.objects.create(name=name))
            )
        for inventur in inventuren:
            abschliessen(inventur, Entscheidungen())
            # Every house with a frozen row: an empty one costs no query for its rows.
            self.weitere_ergebnisse(inventur, 1)
        wenige = self.abfragen()
        for inventur in inventuren:
            self.weitere_ergebnisse(inventur, 4)
        self.assertEqual(self.abfragen(), wenige)


class BlattUeberAlleHaeuser(LaufSeiten):
    """Checked is the template before WeasyPrint. The finished PDF contains only colour.

    Ost is missing ten bottles, Süd has ten too many, together it fits.
    """

    def abgeschlossen(self, soll_ost: int = 50) -> None:
        ost, sued = self.lauf()
        self.zaehlen(ost, "2")
        self.zaehlen(sued, "3")
        Sollbestand.objects.create(
            inventur=ost, hausartikel=self.artikel, menge_basiseinheit=soll_ost
        )
        Sollbestand.objects.create(
            inventur=sued, hausartikel=self.sued_artikel, menge_basiseinheit=50
        )
        for inventur in (ost, sued):
            abschliessen(inventur, Entscheidungen())

    @staticmethod
    def blatt() -> tuple[str, str]:
        """The overview in front and the products from the new page on."""
        # Fresh from the database: the frozen rows hang on the status.
        lauf = list(Inventur.objects.order_by("haus__name"))
        html = pdf_html(
            "pdf/run.html",
            {
                "lauf": Lauf(stichtag=STICHTAG, inventuren=lauf),
                "bloecke": familien_eines_laufs(lauf),
                "anzahl_waren": 1,
            },
        )
        uebersicht, je_ware = html.split('class="abschnitt neue-seite"')
        return uebersicht, je_ware

    def in_familie(self, familie: Warenfamilie) -> None:
        self.ware.warenfamilie = familie
        self.ware.save()

    def test_das_blatt_faerbt_auch_die_spalten_je_haus(self):
        self.abgeschlossen()
        _, je_ware = self.blatt()
        self.assertIn('class="zahl leise differenz-fehlt"', je_ware)
        self.assertIn('class="zahl leise differenz-mehr"', je_ware)
        self.assertIn('class="zahl gesamt differenz-passt"', je_ware)

    def test_die_differenz_ueber_alle_haeuser_steht_rechts(self):
        """After the houses it adds up, not in front of them."""
        self.abgeschlossen()
        _, je_ware = self.blatt()
        kopf = je_ware.split("</thead>")[0]
        self.assertEqual(
            re.findall(r"<th[^>]*>\s*([^<]+?)\s*</th>", kopf),
            ["Ware", "Ergebnis", "Soll", "Kino Ost", "Kino Süd", "Differenz"],
        )

    def test_vorne_jede_familie_mit_dem_weg_zu_ihren_waren(self):
        oben = Warenfamilie.objects.create(name="Warenfamilie R", leiteinheit=self.stueck)
        unten = Warenfamilie.objects.create(
            name="Warenfamilie S", leiteinheit=self.stueck, uebergeordnet=oben
        )
        self.in_familie(unten)
        self.abgeschlossen()

        uebersicht, je_ware = self.blatt()

        # Top down, every family once, linked to its block. The page number
        # behind the link is only filled in by WeasyPrint.
        self.assertLess(uebersicht.index("Warenfamilie R"), uebersicht.index("Warenfamilie S"))
        for familie in (oben, unten):
            self.assertEqual(uebersicht.count(f'href="#familie:{familie.pk}"'), 2)
            self.assertIn(f'id="familie:{familie.pk}"', je_ware)
        # Only the family at the top closes its block with the band.
        self.assertEqual(je_ware.count('<tr class="summe oberste">'), 1)
        self.assertIn('<tr class="summe">', je_ware)
        # The overview is built like the blocks: the family at the top is a
        # band, and the one below stands in its block with a line of its own.
        self.assertEqual(uebersicht.count('<div class="familie oberste">'), 1)
        self.assertEqual(uebersicht.count('<tr class="band">'), 1)
        self.assertEqual(uebersicht.count('<div class="familie">'), 1)
        self.assertLess(
            uebersicht.index('<tr class="band">'), uebersicht.index('<div class="familie">')
        )

    def test_eine_ware_heisst_im_kopf_beider_blaetter_eine(self):
        """The head says "1 Ware", not "1 Waren": over all houses and for one house."""
        self.abgeschlossen()
        uebersicht, _ = self.blatt()  # one product, Artikel C
        ost = Inventur.objects.order_by("haus__name")[0]
        bloecke = warengruppen_einer_inventur(ost)
        einzeln = pdf_html(
            "pdf/inventur.html", {"inventur": ost, "bloecke": bloecke, "anzahl_waren": 1}
        )
        for html in (uebersicht, einzeln):
            self.assertRegex(html, r"\b1 Ware\b")
            self.assertNotIn("1 Waren", html)

    def test_lange_hausnamen_brechen_im_kopf_der_bloecke_um(self):
        """Four names of fourteen letters pushed "Difference" off the page (until 10/2026).

        Now the head of a block breaks a house's name and hyphenates a long
        word, as the overview breaks it. That the blocks then fit was looked at
        on paper, with two and four houses.
        """
        self.abgeschlossen()
        uebersicht, je_ware = self.blatt()
        self.assertIn('<th class="zahl leise">Kino Ost</th>', je_ware.split("</thead>")[0])
        self.assertRegex(
            uebersicht, r"\.familie th\.zahl\.leise \{\s*white-space: normal;\s*hyphens: auto;\s*\}"
        )

    def test_lange_hausnamen_brechen_auch_im_kopf_der_uebersicht_um(self):
        """Four names of one long word each made the head of the overview wider than its tables.

        "Difference" and "Page" fell off the page (until 10/2026). Only the
        house names are hyphenated: "Page" fits once they give way. Looked at
        on paper with four houses.
        """
        self.abgeschlossen()
        uebersicht, _ = self.blatt()
        kopf = uebersicht.split('<div class="spaltenkoepfe">')[1].split("</thead>")[0]
        self.assertIn('<th class="zahl leise haus">Kino Ost</th>', kopf)
        self.assertRegex(kopf, r'<th class="zahl leise">\s*Seite\s*</th>')
        self.assertRegex(uebersicht, r"\.uebersicht th\.haus \{\s*hyphens: auto;\s*\}")

    def test_auf_dem_blatt_ist_die_portion_kurz(self):
        """Four houses with "+3 Portionen" were wider than a portrait page. "Port." stays whole."""
        portionen = Warenfamilie.objects.create(
            name="Warenfamilie P", leiteinheit=Basiseinheit.objects.get(kennung="PORTION")
        )
        self.in_familie(portionen)
        self.ware.umrechnungen_setzen({Dimension.PORTION: Decimal("1")})
        self.abgeschlossen()

        _, je_ware = self.blatt()

        self.assertIn('class="zahl differenz-fehlt">-10 Port.<', je_ware)
        self.assertIn('class="zahl gesamt differenz-passt">0 Port.<', je_ware)
        self.assertNotIn("Portion", je_ware)

    def test_eine_lange_summe_darf_umbrechen(self):
        """A large number still makes a sum too wide for four houses. Short sums stay whole."""
        self.in_familie(Warenfamilie.objects.create(name="Warenfamilie R", leiteinheit=self.stueck))
        self.abgeschlossen(soll_ost=1000050)

        _, je_ware = self.blatt()

        self.assertIn('class="zahl differenz-fehlt umbrechbar">-1000010 Stk.<', je_ware)
        self.assertIn('class="zahl differenz-mehr">+10 Stk.<', je_ware)
        self.assertIn('class="zahl gesamt differenz-fehlt umbrechbar">-1000000 Stk.<', je_ware)

    def test_eine_lange_summe_der_uebersicht_darf_umbrechen(self):
        """The overview breaks a long sum as the blocks do. Until 10/2026 its row ran off."""
        self.in_familie(Warenfamilie.objects.create(name="Warenfamilie R", leiteinheit=self.stueck))
        self.abgeschlossen(soll_ost=1000050)

        uebersicht, _ = self.blatt()

        self.assertIn('class="zahl differenz-fehlt umbrechbar">-1000010 Stk.<', uebersicht)
        self.assertIn('class="zahl differenz-mehr">+10 Stk.<', uebersicht)
        self.assertIn('class="zahl gesamt differenz-fehlt umbrechbar">-1000000 Stk.<', uebersicht)
        self.assertRegex(
            uebersicht, r"\.uebersicht \.zahl\.umbrechbar \{\s*white-space: normal;\s*\}"
        )


class TeileZusammenhalten(SimpleTestCase):
    """A sum of several parts breaks between them, never between a number and its unit."""

    def test_mehrere_teile_halten_zahl_und_einheit_zusammen(self):
        self.assertEqual(
            teile_zusammenhalten("0 Stk. · 2,5 g = 0,0025 kg"),
            "0\u00a0Stk. · 2,5\u00a0g = 0,0025\u00a0kg",
        )
        self.assertEqual(teile_zusammenhalten("+1 pcs · -3 l"), "+1\u00a0pcs · -3\u00a0l")

    def test_ein_teil_darf_seine_einheit_unter_die_zahl_stellen(self):
        """A large number has no other place to break ('umbrechbar' in 'pdf/base.html')."""
        self.assertEqual(teile_zusammenhalten("-1000010 Stk."), "-1000010 Stk.")

    def test_nichts_bleibt_nichts(self):
        self.assertEqual(teile_zusammenhalten(None), "")
        self.assertEqual(teile_zusammenhalten(""), "")


class OffeneLoeschen(LaufSeiten):
    def test_rueckfrage_nennt_die_offenen(self):
        self.lauf()
        antwort = self.client.get(self.adresse("inventur_lauf_delete"))
        self.assertContains(antwort, "Kino Ost")
        self.assertContains(antwort, "Kino Süd")

    def test_abgeschlossene_bleiben_stehen(self):
        ost, sued = self.lauf()
        self.zaehlen(ost)
        abschliessen(ost, Entscheidungen())
        antwort = self.client.post(self.adresse("inventur_lauf_delete"))
        self.assertFalse(Inventur.objects.filter(pk=sued.pk).exists())
        self.assertTrue(Inventur.objects.filter(pk=ost.pk).exists())
        # One house is left, so no run any more: on to its page.
        self.assertRedirects(antwort, self.adresse(), target_status_code=302)

    def test_alle_offen_geht_es_zur_liste(self):
        self.lauf()
        antwort = self.client.post(self.adresse("inventur_lauf_delete"))
        self.assertRedirects(antwort, reverse("inventur_list"))
        self.assertFalse(Inventur.objects.exists())

    def test_ohne_offene_ist_der_knopf_gesperrt(self):
        ost, sued = self.lauf()
        for inventur in (ost, sued):
            self.zaehlen(inventur)
            abschliessen(inventur, Entscheidungen())
        self.assertContains(self.client.get(self.adresse("inventur_lauf_delete")), "disabled")
        self.client.post(self.adresse("inventur_lauf_delete"))
        self.assertEqual(Inventur.objects.count(), 2)

    def test_was_inzwischen_abgeschlossen_wurde_bleibt(self):
        """Closed between loading the run and deleting: decided is the state under the lock."""
        ost, sued = self.lauf()
        self.zaehlen(ost)
        echt = Lauf.aus_adresse

        def geladen_dann_abgeschlossen(stichtag):
            lauf = echt(stichtag)
            abschliessen(Inventur.objects.get(pk=ost.pk), Entscheidungen())
            return lauf

        with mock.patch.object(Lauf, "aus_adresse", side_effect=geladen_dann_abgeschlossen):
            antwort = self.client.post(self.adresse("inventur_lauf_delete"))

        self.assertFalse(Inventur.objects.filter(pk=sued.pk).exists())
        self.assertEqual(
            list(ost.ergebnisse.values_list("gesamtmenge_basiseinheit", flat=True)),
            [Decimal("40")],
        )
        self.assertIn("Eine Inventur gelöscht: Kino Süd.", self.meldungen(antwort))


class ArtImLauf(LaufSeiten):
    """Kino Ost decides about the type of the whole product. Kino Süd cannot reverse it."""

    def ganze_ware_in_ost(self) -> tuple[Inventur, Inventur]:
        """Ost does not count the product and puts all its articles into the annual inventory.

        Süd has counted it. Its article now counts in a type its inventory
        does not count.
        """
        ost, sued = self.lauf()
        self.zaehlen(sued)
        schluessel = str(self.artikel.pk)
        abschliessen(
            ost,
            Entscheidungen(
                ungezaehlt={schluessel: f"{WECHSEL_PRAEFIX}{jahresinventur().pk}"},
                geltung={schluessel: GANZE_WARE},
            ),
        )
        return ost, sued

    def in_den_monat(self, geltung: str) -> Entscheidungen:
        schluessel = str(self.sued_artikel.pk)
        return Entscheidungen(
            in_art={schluessel: str(monatsinventur().pk)}, geltung={schluessel: geltung}
        )

    def test_die_entscheidung_wird_festgehalten(self):
        ost, _ = self.ganze_ware_in_ost()
        entscheidung = Artentscheidung.objects.get()
        self.assertEqual(
            (entscheidung.inventur, entscheidung.ware, entscheidung.inventurart),
            (ost, self.ware, jahresinventur()),
        )
        self.sued_artikel.refresh_from_db()
        self.assertEqual(self.sued_artikel.inventurart, jahresinventur())

    def test_fuer_die_ganze_ware_ist_es_im_anderen_haus_gesperrt(self):
        ost, sued = self.ganze_ware_in_ost()
        klaerung = klaeren(sued)
        eintrag = klaerung.ausserhalb[0]
        self.assertTrue(eintrag.gesperrt)
        assert eintrag.festgelegt is not None
        self.assertEqual(eintrag.festgelegt.inventur, ost)
        # Only locked ones: there is nothing to decide, the page would be skipped.
        self.assertTrue(klaerung.leer)
        erwartet = "Kino Ost hat „Artikel C 0,33 Flasche“ in diesem Lauf"
        with self.assertRaisesMessage(ValidationError, erwartet):
            abschliessen(sued, self.in_den_monat(GANZE_WARE))
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.inventurart, jahresinventur())

    def test_fuer_den_eigenen_artikel_bleibt_es_frei(self):
        _, sued = self.ganze_ware_in_ost()
        abschliessen(sued, self.in_den_monat(NUR_DIESER))
        self.sued_artikel.refresh_from_db()
        self.artikel.refresh_from_db()
        self.assertEqual(
            (self.sued_artikel.inventurart, self.artikel.inventurart),
            (monatsinventur(), jahresinventur()),
        )

    def test_die_seite_markiert_und_sperrt(self):
        _, sued = self.ganze_ware_in_ost()
        antwort = self.client.get(reverse("inventur_abschluss", args=[sued.pk]))
        self.assertContains(antwort, "Im Lauf entschieden: Kino Ost")
        seite = antwort.content.decode()
        kennung = seite.index(f'name="geltung_{self.sued_artikel.pk}" value="{GANZE_WARE}"')
        knopf = seite[seite.rindex("<input", 0, kennung) : seite.index(">", kennung)]
        self.assertIn("disabled", knopf)

    def test_von_hand_umgestellt_sperrt_nichts_mehr(self):
        _, sued = self.ganze_ware_in_ost()
        becher = Inventurart.objects.create(name="Becherinventur")
        self.sued_artikel.inventurart_setzen(becher)
        self.assertFalse(klaeren(sued).ausserhalb[0].gesperrt)

    def test_ein_anderer_lauf_bleibt_frei(self):
        self.ganze_ware_in_ost()
        spaeter = inventur_anlegen(self.zweites, stichtag=date(2026, 2, 28))
        self.assertEqual(festgelegte_arten(spaeter), {})

    def test_im_gemeinsamen_abschluss_steht_es_beim_naechsten_haus(self):
        """Süd has a question of its own. On its page Ost's answer is already fixed."""
        ost, sued = self.lauf()
        self.zaehlen(sued)
        andere = Hausartikel.objects.create(
            haus=self.zweites,
            ware=Ware.objects.create(name="Ware N", basiseinheit=self.stueck),
            warengruppe=Warengruppe.objects.get(name="Warengruppe G"),
            artikelnummer="101003",
            name="Ware N",
        )
        Zaehlposition.objects.create(inventur=sued, ort=self.sued_ort, artikel=andere)

        self.alle_abschliessen()
        antwort = self.client.post(
            self.zwischenseite(ost),
            {
                f"ungezaehlt_{self.artikel.pk}": f"{WECHSEL_PRAEFIX}{jahresinventur().pk}",
                f"geltung_{self.artikel.pk}": GANZE_WARE,
                **self.zwischenformular(ost),
            },
        )
        zwischenseite = self.zwischenseite(sued)
        self.assertRedirects(antwort, zwischenseite)
        self.assertContains(self.client.get(zwischenseite), "Im Lauf entschieden: Kino Ost")
