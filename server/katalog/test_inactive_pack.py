"""A typed pack the product carries inactive: the row stands out, and the person decides.

Creating and editing a house article. Until 10/2026 the row silently landed on
the inactive pack: a location that named it got no row in new inventories, and
when editing the pack simply stayed inactive.
"""

import re
from datetime import date
from decimal import Decimal

from django.urls import reverse

from django_project.testing import AngemeldeteSeiten, bereich, inventur_anlegen, monatsinventur
from organisation.models import Haus, Ort

from .forms import (
    INAKTIV_LASSEN,
    WIEDER_AKTIVIEREN,
    BaseNeueGebindeFormSet,
    NeueGebindeFormSet,
    NeuesGebindeForm,
)
from .models import (
    Artikelstatus,
    Basiseinheit,
    Dimension,
    Erwartung,
    Gebinde,
    Hausartikel,
    Ware,
    Warenfamilie,
    Warengruppe,
)

ENTSCHEIDUNG = f"gebinde-0-{NeuesGebindeForm.ENTSCHEIDUNG}"
OFFEN = "Noch nicht gespeichert: Unter „Gebinde“ wartet eine Frage zu einem inaktiven Gebinde."
ZEILENLOS = (
    "Ohne aktives Gebinde bekäme der Artikel hier keine Zeile. Ein Gebinde, das inaktiv "
    "bleibt, zählt dabei nicht."
)


class MitInaktivemGebinde(AngemeldeteSeiten):
    """'Ware N' carries the crate inactive and the box active. Kino Nord has one store room."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Nord")
        cls.ort = Ort.objects.create(bereich=bereich(cls.haus, "Lager"), name="Getränkelager")
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        cls.ware = Ware.objects.create(name="Ware N", basiseinheit=cls.stueck)
        cls.karton = Gebinde.objects.create(
            ware=cls.ware, name="Karton", faktor_in_basiseinheit=Decimal("12")
        )
        cls.kiste = Gebinde.objects.create(
            ware=cls.ware, name="Kiste", faktor_in_basiseinheit=Decimal("24"), aktiv=False
        )

    @staticmethod
    def antwort(wahl: str, gebinde: Gebinde) -> dict:
        return {ENTSCHEIDUNG: f"{wahl}:{gebinde.pk}"}

    def kiste_ist_aktiv(self) -> bool:
        self.kiste.refresh_from_db()
        return self.kiste.aktiv

    def gebindenamen(self) -> list[str]:
        return sorted(self.ware.gebinde.values_list("name", flat=True))

    @staticmethod
    def meldungen(antwort) -> list[str]:
        return [str(meldung) for meldung in antwort.context["messages"]]


class Anlegen(MitInaktivemGebinde):
    """Creating, without tests of its own: two classes below share it."""

    def anlegen(self, name="Kiste", menge="24", orte=("Kiste",), **abweichend):
        """The new article for Kino Nord, the pack typed at the top and ticked at the store room."""
        daten = {
            "artikelnummer": "101001",
            "name": "Artikel N",
            "ware": self.ware.pk,
            "neuer_ware": "",
            "basiseinheit": "",
            "inventurart": "",
            "warengruppe": self.gruppe.pk,
            "neue_warengruppe": "",
            "ean": "",
            "gebinde-TOTAL_FORMS": "1",
            "gebinde-INITIAL_FORMS": "0",
            "gebinde-MIN_NUM_FORMS": "0",
            "gebinde-MAX_NUM_FORMS": "1000",
            "gebinde-0-name": name,
            "gebinde-0-faktor_in_basiseinheit": menge,
            "orte-TOTAL_FORMS": "1",
            "orte-INITIAL_FORMS": "0",
            "orte-MIN_NUM_FORMS": "0",
            "orte-MAX_NUM_FORMS": "1000",
            "orte-0-ort": self.ort.pk,
            "orte-0-gebinde": list(orte),
        }
        return self.client.post(
            reverse("hausartikel_add", args=[self.haus.pk]), daten | abweichend, follow=True
        )

    def angelegt(self) -> Hausartikel | None:
        return Hausartikel.objects.filter(haus=self.haus, artikelnummer="101001").first()

    def vorgaben(self) -> list[str]:
        erwartung = Erwartung.objects.get(ort=self.ort, artikel=self.angelegt())
        return sorted(erwartung.gebinde_vorgaben.values_list("gebinde__name", flat=True))


class BeimAnlegen(Anlegen):
    def test_erst_wird_gefragt_und_nichts_gespeichert(self):
        antwort = self.anlegen()

        self.assertEqual(antwort.redirect_chain, [])
        self.assertIsNone(self.angelegt())
        self.assertFalse(Erwartung.objects.exists())
        self.assertFalse(self.kiste_ist_aktiv())
        self.assertEqual(self.gebindenamen(), ["Karton", "Kiste"])

        zeile = antwort.context["gebinde_formset"].forms[0]
        self.assertTrue(zeile.auffaellig)
        self.assertEqual(zeile.inaktives_gebinde, self.kiste)
        self.assertContains(antwort, "border-warning bg-warning-subtle")
        self.assertContains(
            antwort,
            f"„Kiste“ gibt es bei „Ware N“ schon, aber inaktiv (24 {self.stueck.kurz}). "
            "Inaktive Gebinde bekommen in neuen Inventuren keine Zeile.",
        )
        for wahl, text in (
            (WIEDER_AKTIVIEREN, "Wieder aktivieren"),
            (INAKTIV_LASSEN, "Inaktiv lassen"),
        ):
            self.assertContains(antwort, f'value="{wahl}:{self.kiste.pk}"')
            self.assertContains(antwort, text)
        self.assertEqual(
            zeile.errors[NeuesGebindeForm.ENTSCHEIDUNG],
            ["Bitte wählen: wieder aktivieren oder inaktiv lassen."],
        )
        self.assertEqual(self.meldungen(antwort), [OFFEN])

    def test_die_zeile_sagt_dass_wieder_aktivieren_in_allen_haeusern_gilt(self):
        antwort = self.anlegen()
        self.assertContains(
            antwort, "Wieder aktivieren gilt in allen Häusern, denn das Gebinde gehört der Ware."
        )

    def test_der_haken_am_ort_bleibt_und_traegt_inaktiv(self):
        """Until 10/2026 a typed name was no choice while the pack rows did not hold up."""
        antwort = self.anlegen()

        orte = antwort.context["orte_formset"]
        self.assertEqual(orte.inaktive, ["Kiste"])
        self.assertEqual(orte.forms[0].errors, {})
        self.assertEqual(orte.forms[0]["gebinde"].value(), ["Kiste"])
        self.assertRegex(
            antwort.content.decode(),
            r'value="Kiste"[^>]*checked[^>]*>\s*<label[^>]*>\s*Kiste\s*<span class="badge[^"]*">'
            r"inaktiv</span>",
        )

    def test_der_haken_auf_einem_getippten_bleibt_auch_bei_einem_anderen_fehler(self):
        """Until 10/2026 the location reported an unknown choice, and the tick was gone."""
        antwort = self.anlegen(
            name="Sixpack",
            menge="6",
            orte=("Sixpack",),
            **{"gebinde-TOTAL_FORMS": "2", "gebinde-1-name": "Tray"},
        )

        self.assertIsNone(self.angelegt())
        self.assertContains(antwort, "Ohne Menge je Gebinde lässt sich nicht umrechnen.")
        orte = antwort.context["orte_formset"]
        self.assertEqual(orte.forms[0].errors, {})
        self.assertEqual(orte.forms[0]["gebinde"].value(), ["Sixpack"])
        self.assertEqual(orte.inaktive, [])
        self.assertEqual(self.meldungen(antwort), [])

    def test_wieder_aktivieren_holt_es_zurueck_und_die_vorgabe_kommt_dorthin(self):
        antwort = self.anlegen(**self.antwort(WIEDER_AKTIVIEREN, self.kiste))

        self.assertEqual(antwort.redirect_chain[0][1], 302)
        self.assertTrue(self.kiste_ist_aktiv())
        self.assertEqual(self.gebindenamen(), ["Karton", "Kiste"])
        self.assertEqual(self.vorgaben(), ["Kiste"])
        self.assertIn("Gebinde „Kiste“ ist jetzt aktiv.", self.meldungen(antwort))
        self.assertContains(antwort, "0 neue Gebinde an der Ware")
        # The next inventory gives it its row.
        inventur = inventur_anlegen(self.haus, stichtag=date(2026, 10, 31))
        inventur.zaehlpositionen_anlegen()
        self.assertEqual(
            list(inventur.zaehlpositionen.values_list("gebinde__name", flat=True)), ["Kiste"]
        )

    def test_inaktiv_lassen_neben_einem_aktiven_gebinde(self):
        """The default on the crate waits. The store room counts the box until then."""
        antwort = self.anlegen(orte=("Kiste", "Karton"), **self.antwort(INAKTIV_LASSEN, self.kiste))

        self.assertEqual(antwort.redirect_chain[0][1], 302)
        self.assertFalse(self.kiste_ist_aktiv())
        self.assertEqual(self.gebindenamen(), ["Karton", "Kiste"])
        self.assertEqual(self.vorgaben(), ["Karton", "Kiste"])
        self.assertIn(
            "„Kiste“ bleibt inaktiv. Seine Vorgaben gelten erst, wenn es wieder aktiv ist.",
            self.meldungen(antwort),
        )
        inventur = inventur_anlegen(self.haus, stichtag=date(2026, 10, 31))
        inventur.zaehlpositionen_anlegen()
        self.assertEqual(
            list(inventur.zaehlpositionen.values_list("gebinde__name", flat=True)), ["Karton"]
        )

    def test_inaktiv_lassen_als_einziges_gebinde_am_ort_wird_abgewiesen(self):
        """The article would be expected there and get no row, as in the counting list."""
        antwort = self.anlegen(**self.antwort(INAKTIV_LASSEN, self.kiste))

        self.assertEqual(antwort.redirect_chain, [])
        self.assertIsNone(self.angelegt())
        self.assertEqual(antwort.context["orte_formset"].forms[0].errors, {"gebinde": [ZEILENLOS]})
        # The answer stays chosen, and no question is open any more.
        zeile = antwort.context["gebinde_formset"].forms[0]
        self.assertEqual(
            zeile[NeuesGebindeForm.ENTSCHEIDUNG].value(), f"{INAKTIV_LASSEN}:{self.kiste.pk}"
        )
        self.assertEqual(zeile.errors, {})
        self.assertNotIn(OFFEN, self.meldungen(antwort))

    def test_abwiegbare_ware_kommt_dort_auf_die_waage_wenn_es_inaktiv_bleibt(self):
        """Without a pack that counts, the scale stands in, as for a location without any."""
        Ware.objects.filter(pk=self.ware.pk).update(abwiegbar=True)
        antwort = self.anlegen(**self.antwort(INAKTIV_LASSEN, self.kiste))

        self.assertEqual(antwort.redirect_chain[0][1], 302)
        erwartung = Erwartung.objects.get(ort=self.ort, artikel=self.angelegt())
        self.assertTrue(erwartung.wiegen_erwartet)
        self.assertEqual(self.vorgaben(), ["Kiste"])

    def test_ein_treffer_ueber_die_menge_nennt_das_inaktive_gebinde(self):
        """The typed name is not created either way. Reactivated, the crate gets the default."""
        antwort = self.anlegen(name="Kasten", orte=("Kasten",))

        self.assertIsNone(self.angelegt())
        self.assertContains(
            antwort,
            f"Die Menge 24 {self.stueck.kurz} gehört bei „Ware N“ zum inaktiven Gebinde „Kiste“. "
            "„Kasten“ wird so oder so nicht angelegt. Wieder aktivieren holt „Kiste“ zurück. "
            "Inaktive Gebinde bekommen in neuen Inventuren keine Zeile.",
        )
        self.assertEqual(antwort.context["orte_formset"].inaktive, ["Kasten"])

        antwort = self.anlegen(
            name="Kasten", orte=("Kasten",), **self.antwort(WIEDER_AKTIVIEREN, self.kiste)
        )
        self.assertEqual(antwort.redirect_chain[0][1], 302)
        self.assertTrue(self.kiste_ist_aktiv())
        self.assertEqual(self.gebindenamen(), ["Karton", "Kiste"])
        self.assertEqual(self.vorgaben(), ["Kiste"])
        self.assertEqual(
            self.meldungen(antwort)[:2],
            [
                "„Kiste“ steht bei „Ware N“ schon für diese Menge. „Kasten“ wurde nicht angelegt.",
                "Gebinde „Kiste“ ist jetzt aktiv.",
            ],
        )

    def test_ein_treffer_ueber_die_menge_inaktiv_gelassen(self):
        antwort = self.anlegen(
            name="Kasten", orte=("Kasten", "Karton"), **self.antwort(INAKTIV_LASSEN, self.kiste)
        )
        self.assertEqual(antwort.redirect_chain[0][1], 302)
        self.assertFalse(self.kiste_ist_aktiv())
        self.assertEqual(self.gebindenamen(), ["Karton", "Kiste"])
        self.assertEqual(self.vorgaben(), ["Karton", "Kiste"])

    def test_ein_aktives_gebinde_fragt_nichts(self):
        """As before: the box is there and gets the default, without a word."""
        antwort = self.anlegen(name="Karton", menge="12", orte=("Karton",))

        self.assertEqual(antwort.redirect_chain[0][1], 302)
        self.assertEqual(self.vorgaben(), ["Karton"])
        self.assertNotIn(OFFEN, self.meldungen(antwort))
        self.assertNotContains(antwort, "bleibt inaktiv")

    def test_ware_in_nicht_zaehlbar_fragt_nichts(self):
        """Its article gets no packs and is expected nowhere."""
        Ware.objects.filter(pk=self.ware.pk).update(warenfamilie=Warenfamilie.nicht_zaehlbar())
        antwort = self.anlegen()

        self.assertEqual(antwort.redirect_chain[0][1], 302)
        artikel = self.angelegt()
        assert artikel is not None
        self.assertEqual(artikel.status, Artikelstatus.IGNORIERT)
        self.assertFalse(self.kiste_ist_aktiv())

    def test_eine_neue_ware_fragt_nichts(self):
        antwort = self.anlegen(ware="", neuer_ware="Ware M", basiseinheit=self.stueck.pk)
        self.assertEqual(antwort.redirect_chain[0][1], 302)
        self.assertEqual(
            list(Gebinde.objects.filter(ware__name="Ware M").values_list("name", "aktiv")),
            [("Kiste", True)],
        )


class VeralteteAntwort(Anlegen):
    """The answer is bound to the pack it was given for. Nothing ends in an error page."""

    def test_eine_antwort_fuer_ein_anderes_gebinde_fragt_neu(self):
        """Say the page went stale: another inactive pack must not be switched on."""
        kanister = Gebinde.objects.create(
            ware=self.ware, name="Kanister", faktor_in_basiseinheit=Decimal("6"), aktiv=False
        )
        antwort = self.anlegen(**self.antwort(WIEDER_AKTIVIEREN, kanister))

        self.assertEqual(antwort.redirect_chain, [])
        self.assertIsNone(self.angelegt())
        kanister.refresh_from_db()
        self.assertFalse(kanister.aktiv)
        self.assertFalse(self.kiste_ist_aktiv())
        zeile = antwort.context["gebinde_formset"].forms[0]
        self.assertEqual(
            zeile.errors[NeuesGebindeForm.ENTSCHEIDUNG],
            ["Das Gebinde hat sich inzwischen geändert. Bitte neu wählen."],
        )
        self.assertContains(antwort, f'value="{WIEDER_AKTIVIEREN}:{self.kiste.pk}"')
        self.assertIn(OFFEN, self.meldungen(antwort))

    def test_eine_unsinnige_antwort_fragt_neu(self):
        for wert in ("aktivieren:abc", "weg", f"loeschen:{self.kiste.pk}"):
            with self.subTest(wert=wert):
                antwort = self.anlegen(**{ENTSCHEIDUNG: wert})
                self.assertEqual(antwort.redirect_chain, [])
                self.assertIsNone(self.angelegt())
                self.assertFalse(self.kiste_ist_aktiv())

    def inzwischen_wieder_aktiv(self, wahl: str) -> None:
        """The answer has nothing left to decide, and the pack stays active."""
        self.kiste.aktiv_setzen(True)
        antwort = self.anlegen(**self.antwort(wahl, self.kiste))

        self.assertEqual(antwort.redirect_chain[0][1], 302)
        self.assertTrue(self.kiste_ist_aktiv())
        self.assertEqual(self.vorgaben(), ["Kiste"])
        self.assertNotContains(antwort, "bleibt inaktiv")
        self.assertNotContains(antwort, "ist jetzt aktiv")

    def test_inzwischen_wieder_aktiv_mit_wieder_aktivieren(self):
        self.inzwischen_wieder_aktiv(WIEDER_AKTIVIEREN)

    def test_inzwischen_wieder_aktiv_mit_inaktiv_lassen(self):
        """It is not switched off: the answer was to leave it, not to set it inactive."""
        self.inzwischen_wieder_aktiv(INAKTIV_LASSEN)

    def test_inzwischen_geloescht_entsteht_das_getippte(self):
        """Nothing holds name or quantity any more, so the row becomes a new pack."""
        antwort_vorher = self.antwort(WIEDER_AKTIVIEREN, self.kiste)
        self.kiste.delete()
        antwort = self.anlegen(**antwort_vorher)

        self.assertEqual(antwort.redirect_chain[0][1], 302)
        self.assertEqual(self.gebindenamen(), ["Karton", "Kiste"])
        self.assertTrue(Gebinde.objects.get(ware=self.ware, name="Kiste").aktiv)
        self.assertContains(antwort, "1 neue Gebinde an der Ware")
        self.assertEqual(self.vorgaben(), ["Kiste"])


class HauptformularAbgewiesen(Anlegen):
    """A rejected main form keeps the rows below. Until 10/2026 they came back empty."""

    def test_getippte_gebinde_und_haken_am_ort_bleiben(self):
        antwort = self.anlegen(name="Palette", menge="48", orte=("Palette",), warengruppe="")

        self.assertEqual(antwort.redirect_chain, [])
        self.assertTrue(antwort.context["form"].errors)
        self.assertIsNone(self.angelegt())
        self.assertEqual(self.gebindenamen(), ["Karton", "Kiste"])
        zeile = antwort.context["gebinde_formset"].forms[0]
        self.assertEqual(zeile["name"].value(), "Palette")
        self.assertEqual(zeile["faktor_in_basiseinheit"].value(), "48")
        self.assertEqual(antwort.context["orte_formset"].forms[0]["gebinde"].value(), ["Palette"])
        self.assertRegex(antwort.content.decode(), r'value="Palette"[^>]*checked')

    def test_die_antwort_zum_inaktiven_gebinde_bleibt_stehen(self):
        wert = f"{WIEDER_AKTIVIEREN}:{self.kiste.pk}"
        antwort = self.anlegen(warengruppe="", **self.antwort(WIEDER_AKTIVIEREN, self.kiste))

        self.assertIsNone(self.angelegt())
        self.assertFalse(self.kiste_ist_aktiv())
        zeile = antwort.context["gebinde_formset"].forms[0]
        self.assertEqual(zeile[NeuesGebindeForm.ENTSCHEIDUNG].value(), wert)
        self.assertRegex(antwort.content.decode(), rf'value="{wert}"[^>]*checked')
        self.assertNotIn(OFFEN, self.meldungen(antwort))

    def test_eine_offene_frage_steht_neben_dem_fehler_des_hauptformulars(self):
        antwort = self.anlegen(warengruppe="")

        self.assertTrue(antwort.context["form"].errors)
        self.assertTrue(antwort.context["gebinde_formset"].forms[0].auffaellig)
        self.assertIn(OFFEN, self.meldungen(antwort))


class BeimBearbeiten(MitInaktivemGebinde):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.artikel = Hausartikel.objects.create(
            haus=cls.haus,
            ware=cls.ware,
            warengruppe=cls.gruppe,
            artikelnummer="101001",
            name="Artikel N",
        )

    def bearbeiten(self, name="Kiste", menge="24", **abweichend):
        daten = {
            "artikelnummer": "101001",
            "name": "Artikel N, neu benannt",
            "ware": self.ware.pk,
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
            "gebinde-0-name": name,
            "gebinde-0-faktor_in_basiseinheit": menge,
        }
        return self.client.post(
            reverse("hausartikel_edit", args=[self.artikel.pk]), daten | abweichend, follow=True
        )

    def artikelname(self) -> str:
        self.artikel.refresh_from_db()
        return self.artikel.name

    def test_die_seite_nennt_das_inaktive_in_worten(self):
        antwort = self.client.get(reverse("hausartikel_edit", args=[self.artikel.pk]))
        einheit = re.escape(self.stueck.kurz)
        self.assertRegex(
            antwort.content.decode(), rf"Kiste\s*<span[^>]*>\s*· 24\s*{einheit}\s*· inaktiv"
        )

    def test_erst_wird_gefragt_auch_der_artikel_bleibt_wie_er_war(self):
        antwort = self.bearbeiten()

        self.assertEqual(antwort.redirect_chain, [])
        self.assertEqual(self.artikelname(), "Artikel N")
        self.assertFalse(self.kiste_ist_aktiv())
        self.assertTrue(antwort.context["gebinde_formset"].forms[0].auffaellig)
        self.assertContains(antwort, "„Kiste“ gibt es bei „Ware N“ schon, aber inaktiv")
        self.assertEqual(self.meldungen(antwort), [OFFEN])

    def test_wieder_aktivieren(self):
        antwort = self.bearbeiten(**self.antwort(WIEDER_AKTIVIEREN, self.kiste))

        self.assertEqual(antwort.redirect_chain[0][1], 302)
        self.assertEqual(self.artikelname(), "Artikel N, neu benannt")
        self.assertTrue(self.kiste_ist_aktiv())
        self.assertEqual(self.gebindenamen(), ["Karton", "Kiste"])
        self.assertEqual(self.meldungen(antwort), ["Gebinde „Kiste“ ist jetzt aktiv."])

    def test_inaktiv_lassen(self):
        """Until 10/2026 the same happened without a word."""
        antwort = self.bearbeiten(**self.antwort(INAKTIV_LASSEN, self.kiste))

        self.assertEqual(antwort.redirect_chain[0][1], 302)
        self.assertEqual(self.artikelname(), "Artikel N, neu benannt")
        self.assertFalse(self.kiste_ist_aktiv())
        self.assertEqual(self.gebindenamen(), ["Karton", "Kiste"])
        self.assertEqual(
            self.meldungen(antwort),
            ["„Kiste“ bleibt inaktiv. Seine Vorgaben gelten erst, wenn es wieder aktiv ist."],
        )

    def test_ein_treffer_ueber_die_menge(self):
        antwort = self.bearbeiten(name="Kasten")
        self.assertContains(antwort, "zum inaktiven Gebinde „Kiste“. „Kasten“ wird so oder so")

        antwort = self.bearbeiten(name="Kasten", **self.antwort(WIEDER_AKTIVIEREN, self.kiste))
        self.assertEqual(antwort.redirect_chain[0][1], 302)
        self.assertTrue(self.kiste_ist_aktiv())
        self.assertEqual(self.gebindenamen(), ["Karton", "Kiste"])

    def test_ein_gleicher_name_mit_anderer_menge_fragt_ebenso(self):
        """The name leads to the crate. Its quantity stays, and the old note says so."""
        antwort = self.bearbeiten(menge="20")
        self.assertContains(antwort, f"aber inaktiv (24 {self.stueck.kurz})")

        antwort = self.bearbeiten(menge="20", **self.antwort(WIEDER_AKTIVIEREN, self.kiste))
        self.assertTrue(self.kiste_ist_aktiv())
        self.kiste.refresh_from_db()
        self.assertEqual(self.kiste.faktor_in_basiseinheit, Decimal("24"))
        self.assertEqual(
            self.meldungen(antwort),
            [
                f"„Kiste“ steht bei „Ware N“ schon für 24 {self.stueck.kurz}. "
                "Ein zweites Gebinde mit diesem Namen wurde nicht angelegt.",
                "Gebinde „Kiste“ ist jetzt aktiv.",
            ],
        )

    def test_zwei_zeilen_zum_selben_inaktiven_sagen_es_einmal(self):
        """One row by name, one by quantity: the crate is left once, and said once."""
        zwei = {
            "gebinde-TOTAL_FORMS": "2",
            "gebinde-1-name": "Kasten",
            "gebinde-1-faktor_in_basiseinheit": "24",
            "gebinde-1-entscheidung": f"{INAKTIV_LASSEN}:{self.kiste.pk}",
        }
        antwort = self.bearbeiten(menge="20", **zwei, **self.antwort(INAKTIV_LASSEN, self.kiste))

        self.assertEqual(antwort.redirect_chain[0][1], 302)
        self.assertFalse(self.kiste_ist_aktiv())
        self.assertEqual(self.gebindenamen(), ["Karton", "Kiste"])
        self.assertEqual(
            self.meldungen(antwort),
            [
                f"„Kiste“ steht bei „Ware N“ schon für 24 {self.stueck.kurz}. "
                "Ein zweites Gebinde mit diesem Namen wurde nicht angelegt.",
                "„Kiste“ bleibt inaktiv. Seine Vorgaben gelten erst, wenn es wieder aktiv ist.",
                "„Kiste“ steht bei „Ware N“ schon für diese Menge. „Kasten“ wurde nicht angelegt.",
            ],
        )

    def test_ein_abgewiesenes_hauptformular_behaelt_das_getippte_gebinde(self):
        """Until 10/2026 the typed rows came back empty, and an answer was gone too."""
        antwort = self.bearbeiten(name="Palette", menge="48", warengruppe="")

        self.assertEqual(antwort.redirect_chain, [])
        self.assertTrue(antwort.context["form"].errors)
        self.assertEqual(self.artikelname(), "Artikel N")
        self.assertEqual(self.gebindenamen(), ["Karton", "Kiste"])
        self.assertEqual(antwort.context["gebinde_formset"].forms[0]["name"].value(), "Palette")
        self.assertContains(antwort, 'value="Palette"')

    def test_ein_aktives_gebinde_fragt_nichts(self):
        antwort = self.bearbeiten(name="Karton", menge="12")
        self.assertEqual(antwort.redirect_chain[0][1], 302)
        self.assertEqual(self.meldungen(antwort), [])

    def test_eine_andere_ware_fragt_nach_ihren_gebinden(self):
        """The packs land on the product the article has afterwards. Its packs decide."""
        andere = Ware.objects.create(name="Ware M", basiseinheit=self.stueck)
        antwort = self.bearbeiten(ware=andere.pk)
        self.assertEqual(antwort.redirect_chain[0][1], 302)
        self.assertEqual(list(andere.gebinde.values_list("name", "aktiv")), [("Kiste", True)])
        self.assertFalse(self.kiste_ist_aktiv())

    def test_englisches_konto(self):
        self.user.sprache = "en"
        self.user.save(update_fields=["sprache"])
        antwort = self.bearbeiten()
        self.assertContains(antwort, "“Kiste” already exists for “Ware N”, but inactive")
        self.assertContains(antwort, "Inactive packs get no line in new inventories.")
        self.assertContains(
            antwort,
            "Reactivating applies at every site, because the pack belongs to the product.",
        )
        self.assertContains(antwort, "Activate again")
        self.assertContains(antwort, "Leave inactive")
        self.assertNotContains(antwort, "Inaktiv lassen")

        antwort = self.bearbeiten(**self.antwort(INAKTIV_LASSEN, self.kiste))
        self.assertEqual(
            self.meldungen(antwort),
            ["“Kiste” stays inactive. Its defaults only apply once it is active again."],
        )


class FormularAllein(MitInaktivemGebinde):
    """The rows without the page: which one asks, and what the answers come to."""

    def formset(self, *zeilen, ware=None, **weitere) -> BaseNeueGebindeFormSet:
        daten = {
            "gebinde-TOTAL_FORMS": str(len(zeilen)),
            "gebinde-INITIAL_FORMS": "0",
            "gebinde-MIN_NUM_FORMS": "0",
            "gebinde-MAX_NUM_FORMS": "1000",
        }
        for index, (name, menge) in enumerate(zeilen):
            daten[f"gebinde-{index}-name"] = name
            daten[f"gebinde-{index}-faktor_in_basiseinheit"] = menge
        return NeueGebindeFormSet(daten | weitere, prefix="gebinde", ware=ware or self.ware)

    def test_nur_die_zeile_mit_dem_inaktiven_fragt(self):
        formset = self.formset(("Karton", "12"), ("Kiste", "24"), ("Sixpack", "6"))
        self.assertFalse(formset.is_valid())
        self.assertEqual(
            [NeuesGebindeForm.ENTSCHEIDUNG in form.fields for form in formset.forms],
            [False, True, False],
        )
        self.assertTrue(formset.fragen_offen)
        self.assertEqual(formset.inaktive_namen, ["Kiste"])
        self.assertEqual(formset.inaktiv_bleibende_namen, [])

    def test_die_antworten_je_gebinde(self):
        kanister = Gebinde.objects.create(
            ware=self.ware, name="Kanister", faktor_in_basiseinheit=Decimal("6"), aktiv=False
        )
        formset = self.formset(
            ("Kiste", "24"),
            ("Kasten", "6"),
            **{
                "gebinde-0-entscheidung": f"{INAKTIV_LASSEN}:{self.kiste.pk}",
                "gebinde-1-entscheidung": f"{WIEDER_AKTIVIEREN}:{kanister.pk}",
            },
        )
        self.assertTrue(formset.is_valid(), formset.errors)
        self.assertFalse(formset.fragen_offen)
        self.assertEqual(
            formset.entscheidungen,
            {self.kiste.pk: INAKTIV_LASSEN, kanister.pk: WIEDER_AKTIVIEREN},
        )
        self.assertEqual(formset.inaktive_namen, ["Kiste", "Kasten"])
        self.assertEqual(formset.inaktiv_bleibende_namen, ["Kiste"])

    def test_zwei_zeilen_zum_selben_inaktiven_antworten_gleich(self):
        """One by name, one by quantity: the crate is switched on or left, not both."""
        formset = self.formset(
            ("Kiste", "20"),
            ("Kasten", "24"),
            **{
                "gebinde-0-entscheidung": f"{WIEDER_AKTIVIEREN}:{self.kiste.pk}",
                "gebinde-1-entscheidung": f"{INAKTIV_LASSEN}:{self.kiste.pk}",
            },
        )
        self.assertFalse(formset.is_valid())
        self.assertEqual(
            formset.non_form_errors(),
            ["Zwei Zeilen führen zum inaktiven Gebinde „Kiste“. Beide gleich wählen."],
        )
        self.assertEqual(
            [form.inaktives_gebinde for form in formset.forms], [self.kiste, self.kiste]
        )

    def test_ohne_ware_fragt_keine_zeile(self):
        formset = NeueGebindeFormSet(
            {
                "gebinde-TOTAL_FORMS": "1",
                "gebinde-INITIAL_FORMS": "0",
                "gebinde-0-name": "Kiste",
                "gebinde-0-faktor_in_basiseinheit": "24",
            },
            prefix="gebinde",
        )
        self.assertTrue(formset.is_valid())
        self.assertEqual(formset.entscheidungen, {})

    def test_eine_unlesbare_menge_fragt_nicht_und_wird_gemeldet(self):
        formset = self.formset(("Kasten", "vierundzwanzig"))
        self.assertFalse(formset.is_valid())
        self.assertNotIn(NeuesGebindeForm.ENTSCHEIDUNG, formset.forms[0].fields)
        self.assertIn("faktor_in_basiseinheit", formset.forms[0].errors)

    def test_der_name_geht_vor_der_menge(self):
        """Typed "Kiste" with the box's quantity: the name leads, as it always did."""
        self.assertEqual(Gebinde.vorhandenes(self.ware, "Kiste", Decimal("12")), self.kiste)
        self.assertEqual(Gebinde.vorhandenes(self.ware, "Kasten", Decimal("12")), self.karton)
        self.assertIsNone(Gebinde.vorhandenes(self.ware, "Kasten", Decimal("6")))
