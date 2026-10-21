"""Views and templates of the inventory workflow.

'SeitenRendern' first opens every address once. That catches typos in
templates and missing context names that would otherwise only show up in
the browser. Then the paths where something can go wrong: the partial
index when creating, the three status changes with their locks and the
export, which only exists after closing.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import models
from django.urls import reverse

from accounts.models import CustomUser
from django_project.testing import (
    AngemeldeteSeiten,
    inventur_anlegen,
    jahresinventur,
    monatsinventur,
)
from katalog.models import (
    Basiseinheit,
    Behaelter,
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
from organisation.models import Bereich, Erscheinungsbild, Haus, Ort

from .closing import NEUE_WERTE
from .closing import klaeren as klaeren_echt
from .models import (
    Inventur,
    Inventurergebnis,
    Inventurnotiz,
    Inventurstatus,
    Sollbestand,
    Wiegung,
    Zaehlposition,
)
from .services import (
    InventurAbgeschlossen,
    bemerkungsstand,
    inventuren_anlegen,
    vermerk_aendern,
    zaehlstand,
)
from .views import InventurDeleteView

STICHTAG = date(2026, 1, 31)


def gesehen(inventur: Inventur) -> dict[str, str]:
    """What the check list of 'inventur' sends at closing: what its numbers and remarks show."""
    return {"zaehlstand": zaehlstand(inventur), "bemerkungsstand": bemerkungsstand(inventur)}


def datei(inhalt: str, name: str = "soll.csv") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, inhalt.encode("utf-8"), content_type="text/csv")


class InventurSeiten(AngemeldeteSeiten):
    """Shared setup: a house, a location, a product with pack."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Ost")
        cls.zweites = Haus.objects.create(name="Kino Süd")
        cls.ort = Ort.objects.create(
            name="Thekenfront", bereich=Bereich.objects.create(name="Theke", haus=cls.haus)
        )
        cls.stueck = Basiseinheit.objects.get(name="Stück(e)")
        cls.ware = Ware.objects.create(name="Artikel C 0,33", basiseinheit=cls.stueck)
        cls.kasten = Gebinde.objects.create(
            ware=cls.ware, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        cls.artikel = Hausartikel.objects.create(
            haus=cls.haus,
            ware=cls.ware,
            warengruppe=Warengruppe.objects.create(name="Warengruppe G"),
            artikelnummer="101001",
            name="Artikel C 0,33 Flasche",
        )
        erwartung = Erwartung.objects.create(ort=cls.ort, artikel=cls.artikel)
        ErwartungGebinde.objects.create(erwartung=erwartung, gebinde=cls.kasten)

    def inventur(self, status=Inventurstatus.VORBEREITUNG, haus=None, **abweichend) -> Inventur:
        """An inventory including its count positions, optionally already in the count."""
        inventur = inventur_anlegen(haus or self.haus, stichtag=STICHTAG, **abweichend)
        inventur.zaehlpositionen_anlegen()
        if status != Inventurstatus.VORBEREITUNG:
            inventur.zaehlung_starten()
        return inventur


class SeitenRendern(InventurSeiten):
    def test_alle_seiten(self):
        inventur = self.inventur()
        for adresse in [
            reverse("inventur_list"),
            reverse("inventur_add"),
            reverse("inventur_detail", args=[inventur.pk]),
            reverse("inventur_delete", args=[inventur.pk]),
        ]:
            with self.subTest(adresse=adresse):
                self.assertEqual(self.client.get(adresse).status_code, 200)

    def test_detailseite_in_jedem_status(self):
        """The three stages show different blocks. Each has to render on its own."""
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        adresse = reverse("inventur_detail", args=[inventur.pk])
        self.assertContains(self.client.get(adresse), "Zählpositionen")

        inventur.abschliessen()
        antwort = self.client.get(adresse)
        self.assertContains(antwort, "Ergebnis")
        # The button, not the word: the page's help always names "Start counting".
        self.assertNotContains(antwort, reverse("inventur_zaehlung_starten", args=[inventur.pk]))

    def test_das_ergebnis_nennt_die_ware_und_klein_den_namen_im_haus(self):
        """As everywhere else: the maintained name in front, the house's designation small."""
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.zaehlpositionen.update(eingabemenge=Decimal("2"))
        inventur.abschliessen()

        antwort = self.client.get(reverse("inventur_detail", args=[inventur.pk]))

        self.assertContains(antwort, '<span class="ms-1">Artikel C 0,33</span>', html=True)
        self.assertContains(
            antwort,
            '<span class="text-body-secondary small ms-1">Artikel C 0,33 Flasche</span>',
            html=True,
        )

    def test_liste_fasst_den_lauf_zusammen(self):
        self.inventur()
        inventur_anlegen(self.zweites, stichtag=STICHTAG)
        laeufe = self.client.get(reverse("inventur_list")).context["laeufe"]
        self.assertEqual(len(laeufe), 1)
        self.assertEqual(len(laeufe[0]["inventuren"]), 2)

    def test_die_ganze_zeile_fuehrt_zur_inventur(self):
        """Not only the house name: the link covers the whole row."""
        inventur = self.inventur()
        antwort = self.client.get(reverse("inventur_list"))
        self.assertContains(antwort, '<tr class="position-relative">')
        seite = antwort.content.decode()
        link = seite[seite.index(f'href="{inventur.get_absolute_url()}"') :]
        self.assertIn("stretched-link", link[: link.index(">")])

    def test_verschiedene_tage_bleiben_getrennt(self):
        self.inventur()
        inventur_anlegen(self.haus, stichtag=date(2026, 2, 28))
        laeufe = self.client.get(reverse("inventur_list")).context["laeufe"]
        self.assertEqual(len(laeufe), 2)

    def test_die_sortierung_steht_nur_einmal_im_filter(self):
        """The skeleton output it additionally as a "hidden" field. Here it is visible."""
        seite = self.client.get(reverse("inventur_list")).content.decode()
        self.assertEqual(seite.count('name="sortierung"'), 1)

    def test_filter_nach_status(self):
        self.inventur()
        self.inventur(haus=self.zweites, status=Inventurstatus.ZAEHLUNG)
        antwort = self.client.get(reverse("inventur_list"), {"status": Inventurstatus.ZAEHLUNG})
        self.assertEqual(len(antwort.context["inventuren"]), 1)

    def test_filter_nach_haus(self):
        self.inventur()
        self.inventur(haus=self.zweites)
        antwort = self.client.get(reverse("inventur_list"), {"haus": self.zweites.pk})
        self.assertEqual(
            [inventur.haus for inventur in antwort.context["inventuren"]], [self.zweites]
        )


def sued_fuehrt_artikel_c(seiten: InventurSeiten) -> Hausartikel:
    """Kino Süd carries the article too, without a counting list.

    A house can only be chosen when it carries an active article of the
    chosen types. It then gets its inventory, but no rows.
    """
    return Hausartikel.objects.create(
        haus=seiten.zweites,
        ware=seiten.ware,
        warengruppe=seiten.artikel.warengruppe,
        artikelnummer="101001",
        name="Artikel C 0,33 Flasche",
    )


class InventurAnlegen(InventurSeiten):
    def test_je_haus_eine_inventur_mit_positionen(self):
        sued_fuehrt_artikel_c(self)
        antwort = self.client.post(
            reverse("inventur_add"),
            {
                "inventurarten": [monatsinventur().pk],
                "stichtag": "2026-01-31",
                "haeuser": [self.haus.pk, self.zweites.pk],
            },
        )
        self.assertRedirects(antwort, reverse("inventur_lauf", args=["2026-01-31"]))
        self.assertEqual(Inventur.objects.count(), 2)
        # Only Kino Ost has an expectation. Kino Süd comes away empty, but it arises.
        kino_ost = Inventur.objects.get(haus=self.haus)
        self.assertEqual(kino_ost.zaehlpositionen.count(), 1)
        self.assertEqual(Inventur.objects.get(haus=self.zweites).zaehlpositionen.count(), 0)

    def test_einzelnes_haus_landet_auf_der_detailseite(self):
        antwort = self.client.post(
            reverse("inventur_add"),
            {
                "inventurarten": [monatsinventur().pk],
                "stichtag": "2026-01-31",
                "haeuser": [self.haus.pk],
            },
        )
        self.assertRedirects(antwort, Inventur.objects.get().get_absolute_url())

    def test_doppelter_lauf_wird_als_formularfehler_abgewiesen(self):
        """The unique index applies, but as a message, not as an IntegrityError."""
        sued_fuehrt_artikel_c(self)
        self.inventur()
        antwort = self.client.post(
            reverse("inventur_add"),
            {
                "inventurarten": [monatsinventur().pk],
                "stichtag": "2026-01-31",
                "haeuser": [self.haus.pk, self.zweites.pk],
            },
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertIn("Kino Ost", antwort.context["form"].errors["haeuser"][0])
        # Nothing created, not for the free house either.
        self.assertEqual(Inventur.objects.count(), 1)


class Statuswechsel(InventurSeiten):
    def test_zaehlung_starten(self):
        inventur = self.inventur()
        self.client.post(reverse("inventur_zaehlung_starten", args=[inventur.pk]))
        inventur.refresh_from_db()
        self.assertEqual(inventur.status, Inventurstatus.ZAEHLUNG)

    def test_zweites_starten_meldet_statt_zu_scheitern(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        antwort = self.client.post(
            reverse("inventur_zaehlung_starten", args=[inventur.pk]), follow=True
        )
        meldungen = [str(meldung) for meldung in get_messages(antwort.wsgi_request)]
        self.assertIn("Gezählt wird nur aus der Vorbereitung heraus.", meldungen)

    def test_zurueck_in_die_vorbereitung_laesst_die_mengen_stehen(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        position = inventur.zaehlpositionen.get()
        position.eingabemenge = Decimal("3")
        position.save()

        self.client.post(reverse("inventur_zurueck", args=[inventur.pk]))
        inventur.refresh_from_db()
        position.refresh_from_db()
        self.assertEqual(inventur.status, Inventurstatus.VORBEREITUNG)
        self.assertEqual(position.eingabemenge, Decimal("3"))

    def test_abgeschlossene_laesst_sich_nicht_zuruecknehmen(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.abschliessen()
        self.client.post(reverse("inventur_zurueck", args=[inventur.pk]))
        inventur.refresh_from_db()
        self.assertEqual(inventur.status, Inventurstatus.ABGESCHLOSSEN)

    def test_rueckweg_folgt_weiter_aber_nicht_in_die_fremde(self):
        """A button from within the list leads back there. A foreign target does not."""
        for fall, haus, weiter, ziel in (
            ("eigene Liste", self.haus, reverse("inventur_list"), reverse("inventur_list")),
            ("fremde Adresse", self.zweites, "https://example.com/", None),
        ):
            with self.subTest(fall):
                inventur = self.inventur(haus=haus)
                antwort = self.client.post(
                    reverse("inventur_zaehlung_starten", args=[inventur.pk]), {"weiter": weiter}
                )
                self.assertRedirects(antwort, ziel or inventur.get_absolute_url())


class InventurLoeschen(InventurSeiten):
    def test_laufende_verschwindet_samt_positionen(self):
        inventur = self.inventur()
        antwort = self.client.post(reverse("inventur_delete", args=[inventur.pk]))
        self.assertRedirects(antwort, reverse("inventur_list"))
        self.assertEqual(Inventur.objects.count(), 0)
        self.assertEqual(Zaehlposition.objects.count(), 0)

    def test_abgeschlossene_bleibt_fuer_fuehrungskraefte(self):
        """The frozen results hang on it, the only evidence."""
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.abschliessen()
        self.client.force_login(
            CustomUser.objects.create_user(
                username="leitung", password="geheim", email="l@example.org", is_staff=True
            )
        )
        antwort = self.client.post(reverse("inventur_delete", args=[inventur.pk]), follow=True)
        self.assertEqual(Inventur.objects.count(), 1)
        self.assertContains(antwort, "nur in der Verwaltung")

    def test_auch_der_admin_loescht_eine_abgeschlossene_nur_in_der_verwaltung(self):
        """The way there is on the page. Deletion happens there, not here."""
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        position = inventur.zaehlpositionen.order_by("pk")[0]
        position.eingabemenge = Decimal("2")
        position.save()
        inventur.abschliessen()

        seite = self.client.get(reverse("inventur_delete", args=[inventur.pk]))
        antwort = self.client.post(reverse("inventur_delete", args=[inventur.pk]), follow=True)

        self.assertContains(seite, reverse("admin:inventur_inventur_delete", args=[inventur.pk]))
        self.assertContains(antwort, "nur in der Verwaltung")
        self.assertEqual(Inventur.objects.count(), 1)
        self.assertEqual(Inventurergebnis.objects.count(), 1)

    def test_die_detailseite_bietet_bei_abgeschlossenen_kein_loeschen(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.abschliessen()

        antwort = self.client.get(inventur.get_absolute_url())

        self.assertNotContains(antwort, reverse("inventur_delete", args=[inventur.pk]))

    def test_was_inzwischen_abgeschlossen_wurde_bleibt(self):
        """Closed between loading the page and deleting: decided is the state under the lock."""
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.zaehlpositionen.update(eingabemenge=Decimal("2"))
        echt = InventurDeleteView.get_object

        def geladen_dann_abgeschlossen(view, *args, **kwargs):
            objekt = echt(view, *args, **kwargs)
            Inventur.objects.get(pk=objekt.pk).abschliessen()
            return objekt

        with patch.object(
            InventurDeleteView,
            "get_object",
            autospec=True,
            side_effect=geladen_dann_abgeschlossen,
        ):
            antwort = self.client.post(reverse("inventur_delete", args=[inventur.pk]), follow=True)

        self.assertContains(antwort, "nur in der Verwaltung")
        self.assertEqual(
            list(inventur.ergebnisse.values_list("gesamtmenge_basiseinheit", flat=True)),
            [Decimal("40")],
        )


class SollbestandHochladen(InventurSeiten):
    def test_zeilen_werden_zugeordnet(self):
        inventur = self.inventur()
        antwort = self.client.post(
            inventur.get_absolute_url(),
            {"datei": datei("artikelnummer;sollbestand\n101001;42,5\n"), "format": "winticket"},
            follow=True,
        )
        soll = Sollbestand.objects.get()
        self.assertEqual(soll.hausartikel, self.artikel)
        self.assertEqual(soll.menge_basiseinheit, Decimal("42.500"))
        self.assertContains(antwort, "1 von 1 Zeilen als Sollbestand übernommen")

    def test_unbekannte_nummer_wird_gemeldet(self):
        inventur = self.inventur()
        antwort = self.client.post(
            inventur.get_absolute_url(),
            {"datei": datei("101001;5\n999999;7\n"), "format": "winticket"},
            follow=True,
        )
        self.assertEqual(Sollbestand.objects.count(), 1)
        self.assertContains(antwort, "999999")

    def test_zweiter_upload_ueberschreibt(self):
        inventur = self.inventur()
        self.client.post(
            inventur.get_absolute_url(), {"datei": datei("101001;5\n"), "format": "winticket"}
        )
        self.client.post(
            inventur.get_absolute_url(), {"datei": datei("101001;9\n"), "format": "winticket"}
        )
        self.assertEqual(Sollbestand.objects.get().menge_basiseinheit, Decimal("9.000"))

    def test_zu_grosse_datei_wird_abgewiesen(self):
        """Otherwise pandas would read it in one piece before anyone can check it."""
        inventur = self.inventur()
        riesig = datei("101001;5\n" + "x" * (11 * 1024 * 1024))
        antwort = self.client.post(
            inventur.get_absolute_url(), {"datei": riesig, "format": "winticket"}
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "höchstens")
        self.assertFalse(Sollbestand.objects.exists())

    def test_falsches_dateiformat_bleibt_auf_der_seite(self):
        """The error belongs on the field, not as a message after a redirect."""
        inventur = self.inventur()
        antwort = self.client.post(
            inventur.get_absolute_url(),
            {"datei": datei("101001;5\n", name="soll.pdf"), "format": "winticket"},
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertTrue(antwort.context["form"].errors["datei"])
        self.assertEqual(Sollbestand.objects.count(), 0)

    def test_abgeschlossene_nimmt_nichts_mehr(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.abschliessen()
        self.client.post(
            inventur.get_absolute_url(), {"datei": datei("101001;5\n"), "format": "winticket"}
        )
        self.assertEqual(Sollbestand.objects.count(), 0)


class LoeschenMitSchutz(InventurSeiten):
    """'PROTECT' holds what occurs in an inventory. The page says so instead of crashing."""

    def loeschen(self, name, *args):
        antwort = self.client.post(reverse(name, args=args), follow=True)
        self.assertEqual(antwort.status_code, 200)
        return " ".join(str(m) for m in get_messages(antwort.wsgi_request))

    def test_hausartikel_mit_sollbestand(self):
        inventur = self.inventur()
        Sollbestand.objects.create(
            inventur=inventur, hausartikel=self.artikel, menge_basiseinheit=Decimal("1")
        )
        meldung = self.loeschen("hausartikel_delete", self.artikel.pk)
        self.assertIn("lässt sich nicht löschen", meldung)
        self.assertIn("1 Sollbestand", meldung)
        self.assertIn("1 Zählposition", meldung)
        self.assertTrue(Hausartikel.objects.filter(pk=self.artikel.pk).exists())

    def test_ort_und_bereich_mit_zaehlpositionen(self):
        self.inventur()
        self.assertIn("1 Zählposition", self.loeschen("ort_delete", self.ort.pk))
        self.assertIn("1 Zählposition", self.loeschen("bereich_delete", self.ort.bereich.pk))
        self.assertTrue(Ort.objects.filter(pk=self.ort.pk).exists())

    def test_rezepturartikel_in_laufender_inventur(self):
        inventur = self.inventur()
        portion = Rezepturartikel.objects.create(name="Portion", basiseinheit=self.stueck)
        Zaehlposition.objects.create(
            inventur=inventur, ort=self.ort, artikel=portion, unerwartet=True
        )
        self.assertIn("laufenden Inventur", self.loeschen("rezepturartikel_delete", portion.pk))
        self.assertTrue(Rezepturartikel.objects.filter(pk=portion.pk).exists())

    def test_behaelter_mit_wiegung_ohne_zuordnung(self):
        """The weighing points to it, even if no product carries it any more."""
        inventur = self.inventur()
        Ware.objects.filter(pk=self.ware.pk).update(abwiegbar=True)
        schale = Behaelter.objects.create(name="Schale", tara=Decimal("100"))
        position = Zaehlposition.objects.create(
            inventur=inventur, ort=self.ort, artikel=self.artikel, unerwartet=True
        )
        Wiegung.objects.create(
            zaehlposition=position, brutto_gramm=Decimal("500"), behaelter=schale
        )
        self.assertIn("noch zugeordnet", self.loeschen("behaelter_delete", schale.pk))
        self.assertTrue(Behaelter.objects.filter(pk=schale.pk).exists())


class ZaehlendeZuordnen(InventurSeiten):
    """Who may count stands explicitly on the inventory, admins excepted."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.zaehler = CustomUser.objects.create_user(
            username="zaehler", password="geheim", email="z@example.org"
        )
        cls.zaehler.haeuser.add(cls.haus)
        cls.aushilfe = CustomUser.objects.create_user(
            username="aushilfe", password="geheim", email="a@example.org"
        )

    def test_anlegen_traegt_die_anlegende_ein(self):
        """Whoever creates is there on the day and would otherwise have to enter themselves."""
        leitung = CustomUser.objects.create_user(
            username="anlegerin", password="geheim", email="an@example.org", is_staff=True
        )
        self.client.force_login(leitung)
        sued_fuehrt_artikel_c(self)
        antwort = self.client.post(
            reverse("inventur_add"),
            {
                "inventurarten": [monatsinventur().pk],
                "stichtag": "2026-01-31",
                "haeuser": [self.haus.pk, self.zweites.pk],
            },
            follow=True,
        )
        self.assertEqual(Inventur.objects.count(), 2)
        for inventur in Inventur.objects.all():
            self.assertEqual(list(inventur.zaehlende.all()), [leitung])
        self.assertContains(antwort, "Du zählst mit")

    def test_anlegender_admin_bekommt_keinen_eintrag(self):
        """They count everywhere anyway. An entry would say nothing about it."""
        self.client.post(
            reverse("inventur_add"),
            {
                "inventurarten": [monatsinventur().pk],
                "stichtag": "2026-01-31",
                "haeuser": [self.haus.pk],
            },
        )
        inventur = Inventur.objects.get()
        self.assertEqual(inventur.zaehlende.count(), 0)
        self.assertTrue(inventur.darf_zaehlen(self.user))

    def test_vorschlag_kommt_aus_der_hauszuordnung(self):
        """What stands on the house the inventory suggests. Entered it is there."""
        inventur = self.inventur()
        self.assertEqual(list(inventur.vorgeschlagene_zaehlende()), [self.zaehler])
        self.assertEqual(inventur.zaehlende.count(), 0)
        self.assertFalse(inventur.darf_zaehlen(self.zaehler))

    def test_zuordnen_ueber_die_seite(self):
        inventur = self.inventur()
        adresse = reverse("inventur_zaehlende", args=[inventur.pk])
        self.assertEqual(self.client.get(adresse).status_code, 200)

        antwort = self.client.post(adresse, {"zaehlende": [self.zaehler.pk, self.aushilfe.pk]})
        self.assertEqual(inventur.zaehlende.count(), 2)
        # In preparation the app does not list the inventory yet. Until 10/2026
        # the page said "from now on".
        self.assertEqual(
            [str(meldung) for meldung in get_messages(antwort.wsgi_request)],
            [
                f"2 Zählende für {inventur} eingetragen. Sie sehen die Inventur in der "
                "Zähl-App, sobald die Zählung beginnt."
            ],
        )

        # Saving again with fewer ticks takes out again.
        self.client.post(adresse, {"zaehlende": [self.aushilfe.pk]})
        self.assertEqual(list(inventur.zaehlende.all()), [self.aushilfe])

    def test_waehrend_der_zaehlung_sehen_sie_sie_sofort(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        antwort = self.client.post(
            reverse("inventur_zaehlende", args=[inventur.pk]), {"zaehlende": [self.zaehler.pk]}
        )
        self.assertEqual(
            [str(meldung) for meldung in get_messages(antwort.wsgi_request)],
            [
                f"1 Zählende für {inventur} eingetragen. Sie sehen die Inventur ab sofort in "
                "der Zähl-App."
            ],
        )
        self.assertEqual(list(Inventur.zaehlbare_fuer(self.zaehler)), [inventur])

    def test_nach_dem_abschluss_gibt_es_keine_zaehlenden_mehr(self):
        """Closing cleared the assignment. Neither the page nor a card offers it again."""
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.zaehlende.add(self.zaehler)
        inventur.abschliessen()
        adresse = reverse("inventur_zaehlende", args=[inventur.pk])

        self.assertRedirects(self.client.get(adresse), inventur.get_absolute_url())
        seite = self.client.get(inventur.get_absolute_url())
        self.assertNotContains(seite, adresse)
        self.assertNotContains(seite, self.zaehler.get_absolute_url())

    def test_gruppen_trennen_haus_leitung_und_rest(self):
        """Managers stand on their own. Under "further" they went under."""
        leitung = CustomUser.objects.create_user(
            username="leitung", password="geheim", email="l@example.org", is_staff=True
        )
        inventur = self.inventur()
        form = self.client.get(reverse("inventur_zaehlende", args=[inventur.pk])).context["form"]
        titel = [titel for titel, _, _ in form.gruppen()]
        vorschlag, fuehrung, weitere = (eintraege for _, eintraege, _ in form.gruppen())

        self.assertIn("Kino Ost", titel[0])
        self.assertEqual(titel[1], "Führungskräfte")
        self.assertEqual([str(e.choice_label) for e in vorschlag], [str(self.zaehler)])
        self.assertEqual([str(e.choice_label) for e in fuehrung], [str(leitung)])
        # Whoever is not assigned to the house and does not manage stands under
        # "further", selectable but not suggested.
        self.assertIn(str(self.aushilfe), [str(e.choice_label) for e in weitere])

    def test_fuehrungskraft_des_hauses_steht_oben_statt_doppelt(self):
        leitung = CustomUser.objects.create_user(
            username="hausleitung", password="geheim", email="hl@example.org", is_staff=True
        )
        leitung.haeuser.add(self.haus)
        inventur = self.inventur()
        form = self.client.get(reverse("inventur_zaehlende", args=[inventur.pk])).context["form"]
        vorschlag, fuehrung, _ = (eintraege for _, eintraege, _ in form.gruppen())

        self.assertIn(str(leitung), [str(e.choice_label) for e in vorschlag])
        self.assertEqual(fuehrung, [])
        self.assertContains(
            self.client.get(reverse("inventur_zaehlende", args=[inventur.pk])),
            "stehen schon oben bei ihrem Haus",
        )

    def test_admin_steht_nicht_zur_wahl_darf_aber_zaehlen(self):
        """A box for admins would be a sham decision, because they count everywhere."""
        admin = CustomUser.objects.create_superuser(
            username="chefin",
            password="geheim",
            email="chefin@example.org",
            first_name="Ada",
            last_name="Admin",
        )
        inventur = self.inventur()
        form = self.client.get(reverse("inventur_zaehlende", args=[inventur.pk])).context["form"]
        zur_wahl = [
            str(eintrag.choice_label) for _, gruppe, _ in form.gruppen() for eintrag in gruppe
        ]

        self.assertNotIn(str(admin), zur_wahl)
        self.assertTrue(inventur.darf_zaehlen(admin))
        self.assertFalse(inventur.darf_zaehlen(self.zaehler))

    def test_abgeschlossene_wird_nicht_mehr_umbesetzt(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.abschliessen()
        self.client.post(
            reverse("inventur_zaehlende", args=[inventur.pk]), {"zaehlende": [self.zaehler.pk]}
        )
        self.assertEqual(inventur.zaehlende.count(), 0)


class ZaehlendeBeimAnlegen(InventurSeiten):
    """With the box the counters come along for every chosen house, without it as before."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.leitung = CustomUser.objects.create_user(
            username="anlegerin",
            password="geheim",
            email="an@example.org",
            is_staff=True,
            first_name="Lea",
            last_name="Leitung",
        )
        cls.zaehlerin = CustomUser.objects.create_user(
            username="zaehlerin",
            password="geheim",
            email="z@example.org",
            first_name="Zoe",
            last_name="Zählerin",
        )
        cls.zaehlerin.haeuser.add(cls.haus, cls.zweites)
        cls.aushilfe = CustomUser.objects.create_user(
            username="aushilfe",
            password="geheim",
            email="a@example.org",
            first_name="Ali",
            last_name="Aushilfe",
        )

    def setUp(self):
        super().setUp()
        self.client.force_login(self.leitung)
        sued_fuehrt_artikel_c(self)

    def anlegen(self, **felder):
        return self.client.post(
            reverse("inventur_add"),
            {
                "inventurarten": [monatsinventur().pk],
                "stichtag": "2026-01-31",
                "haeuser": [self.haus.pk, self.zweites.pk],
                **felder,
            },
            follow=True,
        )

    def test_mit_haken_zaehlen_die_gewaehlten_in_jedem_haus(self):
        antwort = self.anlegen(
            zaehlende_fuer_alle="on", zaehlende=[self.zaehlerin.pk, self.aushilfe.pk]
        )

        self.assertEqual(Inventur.objects.count(), 2)
        for inventur in Inventur.objects.all():
            with self.subTest(haus=inventur.haus.name):
                self.assertEqual(
                    set(inventur.zaehlende.all()), {self.leitung, self.zaehlerin, self.aushilfe}
                )
        self.assertContains(
            antwort, "Zählende außer dir, in jedem Haus: Ali Aushilfe, Zoe Zählerin"
        )

    def test_ohne_haken_bleibt_es_bei_der_anlegenden(self):
        """A ticked name without the box counts for nothing: assigned per inventory, as before."""
        antwort = self.anlegen(zaehlende=[self.zaehlerin.pk])

        for inventur in Inventur.objects.all():
            self.assertEqual(list(inventur.zaehlende.all()), [self.leitung])
        self.assertContains(antwort, "Du zählst mit")

    def test_die_liste_nennt_die_haeuser_und_laesst_die_anlegende_weg(self):
        """Lea counts anyway, and the administrator may count everywhere."""
        antwort = self.client.get(reverse("inventur_add"))
        (_, fuehrung, _), (_, weitere, _) = antwort.context["form"].zaehlende_gruppen()

        self.assertEqual(fuehrung, [])
        self.assertEqual(
            [(str(eintrag.choice_label), haeuser) for eintrag, haeuser in weitere],
            [("Ali Aushilfe", ""), ("Zoe Zählerin", "Kino Ost, Kino Süd")],
        )
        self.assertContains(antwort, "Außer dir gibt es keine Führungskraft.")

    def test_auch_der_dienst_traegt_keine_admins_ein(self):
        """The form does not offer them. Called directly, the service keeps the same rule."""
        [inventur] = inventuren_anlegen(
            [self.haus], [monatsinventur()], STICHTAG, zaehlende=[self.user, self.aushilfe]
        )
        self.assertEqual(list(inventur.zaehlende.all()), [self.aushilfe])


class NotizenNebenbei(InventurSeiten):
    """When leaving a field or the page the script saves by itself.

    Server-side that is the same path as "Save notes". Only the response is
    brief, because nobody needs the whole page back.
    """

    def test_einzelne_notiz_per_fetch(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        antwort = self.client.post(
            reverse("inventur_pruefen", args=[inventur.pk]),
            {f"notiz_{self.artikel.pk}": "nebenbei getippt"},
            headers={"x-requested-with": "fetch"},
        )
        self.assertEqual(antwort.status_code, 204)
        self.assertEqual(Inventurnotiz.objects.get().text, "nebenbei getippt")
        # No message: it would otherwise appear at some point on another page.
        self.assertEqual(len(get_messages(antwort.wsgi_request)), 0)

    def test_beim_verlassen_der_seite(self):
        """'sendBeacon' cannot set headers. For that the field 'nebenbei'."""
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        antwort = self.client.post(
            reverse("inventur_pruefen", args=[inventur.pk]),
            {"nebenbei": "1", f"notiz_{self.artikel.pk}": "beim Weggehen"},
        )
        self.assertEqual(antwort.status_code, 204)
        self.assertEqual(Inventurnotiz.objects.get().text, "beim Weggehen")

    def test_knopf_bleibt_der_gewoehnliche_weg(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        antwort = self.client.post(
            reverse("inventur_pruefen", args=[inventur.pk]),
            {f"notiz_{self.artikel.pk}": "von Hand"},
            follow=True,
        )
        self.assertContains(antwort, "Notizen gespeichert")

    def test_eine_inzwischen_abgeschlossene_antwortet_mit_einem_satz(self):
        """Closed while the page stood open: a status and a sentence in the account's language.

        Until 10/2026 the script got the redirect to the inventory's page.
        'fetch' followed it and heard 200, the page said "Notes saved"
        although nothing was stored, and the message for the inventory's
        page was used up.
        """
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.abschliessen()
        adresse = reverse("inventur_pruefen", args=[inventur.pk])
        notiz = {f"notiz_{self.artikel.pk}": "zu spät"}
        saetze = {
            "de": "Notizen nicht gesichert, denn die Inventur ist inzwischen abgeschlossen.",
            "en": "Notes not saved because the inventory has been closed in the meantime.",
        }

        for sprache, satz in saetze.items():
            CustomUser.objects.filter(pk=self.user.pk).update(sprache=sprache)
            for weg, antwort in (
                ("fetch", self.client.post(adresse, notiz, headers={"x-requested-with": "fetch"})),
                ("sendBeacon", self.client.post(adresse, notiz | {"nebenbei": "1"})),
            ):
                with self.subTest(sprache=sprache, weg=weg):
                    self.assertEqual((antwort.status_code, antwort.content.decode()), (409, satz))
                    self.assertEqual(len(get_messages(antwort.wsgi_request)), 0)
        self.assertFalse(Inventurnotiz.objects.exists())


class NotizenAusEinerAelterenSeite(InventurSeiten):
    """A page older than what is stored must not overwrite it.

    On 2026-10-12 a closing emptied every note of an inventory: they had
    been written in one tab, closed was from another one whose fields still
    stood empty. Every
    row now carries what it showed, and only what was changed there is
    stored.
    """

    def absenden(self, inventur, **felder):
        return self.client.post(reverse("inventur_pruefen", args=[inventur.pk]), felder)

    def felder(self, notiz="", vorher="", **weitere):
        return {
            f"notiz_{self.artikel.pk}": notiz,
            f"notiz_vorher_{self.artikel.pk}": vorher,
            **weitere,
        }

    def test_ein_leeres_unberuehrtes_feld_loescht_keine_notiz(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.notizen.create(hausartikel=self.artikel, text="in einem anderen Tab")

        self.absenden(inventur, **self.felder())

        self.assertEqual(inventur.notizen.get().text, "in einem anderen Tab")

    def test_abschluss_aus_der_aelteren_seite_friert_die_notiz_ein(self):
        """The case of 2026-10-12: closed from the tab without the notes."""
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.zaehlpositionen.update(eingabemenge=Decimal("2"))
        inventur.notizen.create(hausartikel=self.artikel, text="-3")

        antwort = self.absenden(
            inventur,
            **self.felder(abschliessen="1", **gesehen(inventur)),
        )

        self.assertRedirects(antwort, inventur.get_absolute_url())
        self.assertEqual(inventur.ergebnisse.get().notiz, "-3")

    def test_was_hier_geaendert_wurde_gilt(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.notizen.create(hausartikel=self.artikel, text="alt")

        self.absenden(inventur, **self.felder(notiz="neu", vorher="alt"))

        self.assertEqual(inventur.notizen.get().text, "neu")

    def test_geleert_ist_geleert(self):
        """Emptying a note on purpose still removes it."""
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.notizen.create(hausartikel=self.artikel, text="weg damit")

        self.absenden(inventur, **self.felder(notiz="", vorher="weg damit"))

        self.assertEqual(inventur.notizen.count(), 0)

    def test_leerzeichen_sind_keine_aenderung(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.notizen.create(hausartikel=self.artikel, text="von woanders")

        self.absenden(inventur, **self.felder(notiz="  ", vorher=""))

        self.assertEqual(inventur.notizen.get().text, "von woanders")

    def test_der_haken_nimmt_keinen_alten_text_mit(self):
        """Ticking a box sends the row. Its outdated note must stay out."""
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        Sollbestand.objects.create(
            inventur=inventur, hausartikel=self.artikel, menge_basiseinheit=Decimal("10")
        )
        inventur.notizen.create(hausartikel=self.artikel, text="von woanders")

        self.absenden(
            inventur,
            **self.felder(
                **{
                    f"sollstattist_{self.artikel.pk}": "on",
                    f"sollstattist_vorher_{self.artikel.pk}": "",
                }
            ),
        )

        vermerk = inventur.notizen.get()
        self.assertEqual(vermerk.text, "von woanders")
        self.assertTrue(vermerk.soll_statt_ist)

    def test_ein_neuer_text_laesst_den_haken_von_woanders_stehen(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        Sollbestand.objects.create(
            inventur=inventur, hausartikel=self.artikel, menge_basiseinheit=Decimal("10")
        )
        inventur.notizen.create(hausartikel=self.artikel, text="", soll_statt_ist=True)

        # The page showed no tick and its box is empty: nothing changed there.
        self.absenden(
            inventur,
            **self.felder(notiz="Bruch", **{f"sollstattist_vorher_{self.artikel.pk}": ""}),
        )

        vermerk = inventur.notizen.get()
        self.assertEqual(vermerk.text, "Bruch")
        self.assertTrue(vermerk.soll_statt_ist)

    def test_eine_seite_ohne_vorher_schreibt_wie_bisher(self):
        """A page from before the change, still open during the update."""
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.notizen.create(hausartikel=self.artikel, text="alt")

        self.absenden(inventur, **{f"notiz_{self.artikel.pk}": ""})

        self.assertEqual(inventur.notizen.count(), 0)

    #: What the check list answers when someone else wrote in a row meanwhile.
    VERALTET = (
        "Nicht gespeichert, weil inzwischen jemand anderes dort etwas eingetragen hat: "
        "%s. Neu laden zeigt, was jetzt dort steht."
    )

    def test_was_die_app_inzwischen_schrieb_bleibt_stehen(self):
        """The page showed no note, the app wrote one since. The page's text does not replace it.

        Until 10/2026 the website overwrote it without a word, while the app
        in the same position heard that someone had written meanwhile.
        """
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        vermerk_aendern(inventur, self.artikel.pk, text="Regal hinten", text_vorher="")

        antwort = self.absenden(
            inventur, nebenbei="1", **self.felder(notiz="zwei Kästen", vorher="")
        )

        self.assertEqual(
            (antwort.status_code, antwort.content.decode()),
            (400, self.VERALTET % "Artikel C 0,33 Flasche"),
        )
        self.assertEqual(inventur.notizen.get().text, "Regal hinten")

    def test_ebenso_eine_differenz_von_hand(self):
        """The page showed -2, the app made it -5 since. The page's -3 does not replace it."""
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.differenz_von_hand_setzen(True)
        vermerk_aendern(inventur, self.artikel.pk, differenz=Decimal("-5"))
        pk = self.artikel.pk

        antwort = self.absenden(
            inventur, nebenbei="1", **{f"differenz_{pk}": "-3", f"differenz_vorher_{pk}": "-2"}
        )

        self.assertEqual(antwort.status_code, 400)
        self.assertEqual(inventur.notizen.get().differenz, Decimal("-5"))

    def test_die_uebrigen_zeilen_sind_gespeichert(self):
        """Only the row someone else wrote in stays. The page sends all its rows at once."""
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        zweiter = Hausartikel.objects.create(
            haus=self.haus,
            ware=Ware.objects.create(name="Artikel C 0,5", basiseinheit=self.stueck),
            warengruppe=Warengruppe.objects.get(name="Warengruppe G"),
            artikelnummer="101003",
            name="Artikel C 0,5 Flasche",
        )
        vermerk_aendern(inventur, self.artikel.pk, text="Regal hinten", text_vorher="")

        antwort = self.absenden(
            inventur,
            **self.felder(notiz="zwei Kästen", vorher=""),
            **{f"notiz_{zweiter.pk}": "Bruch", f"notiz_vorher_{zweiter.pk}": ""},
        )

        self.assertRedirects(antwort, reverse("inventur_pruefen", args=[inventur.pk]))
        self.assertEqual(
            [str(meldung) for meldung in get_messages(antwort.wsgi_request)],
            [self.VERALTET % "Artikel C 0,33 Flasche"],
        )
        self.assertEqual(
            dict(inventur.notizen.values_list("hausartikel__name", "text")),
            {"Artikel C 0,33 Flasche": "Regal hinten", "Artikel C 0,5 Flasche": "Bruch"},
        )

    def test_der_eigene_text_zweimal_ist_kein_widerspruch(self):
        """The answer to the first send got lost on the way. The second finds its own text."""
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        felder = self.felder(notiz="zwei Kästen", vorher="")

        for _versuch in range(2):
            antwort = self.absenden(inventur, nebenbei="1", **felder)
            self.assertEqual(antwort.status_code, 204)
        self.assertEqual(inventur.notizen.get().text, "zwei Kästen")

    def test_die_seite_traegt_was_sie_zeigt(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.notizen.create(hausartikel=self.artikel, text="Regal hinten")

        antwort = self.client.get(reverse("inventur_pruefen", args=[inventur.pk]))

        self.assertContains(
            antwort,
            f'<input type="hidden" name="notiz_vorher_{self.artikel.pk}" value="Regal hinten">',
            html=True,
        )
        # Back and Forward must not put the old field contents back.
        self.assertContains(antwort, 'data-pruefliste autocomplete="off"')


class Startseite(InventurSeiten):
    def test_wurzel_fuehrt_zu_den_inventuren(self):
        """The welcome page is gone. The daily work is in the list."""
        self.assertRedirects(self.client.get("/"), reverse("inventur_list"))


class ZaehlAppAusliefern(InventurSeiten):
    """The PWA lies under '/app/', reachable without signing in.

    The build location is bent here instead of relying on whether someone
    just ran 'npm run build': the test should describe both, the built and the
    unbuilt state.
    """

    def test_ohne_build_sagt_die_seite_was_zu_tun_ist(self):
        self.client.logout()
        with (
            TemporaryDirectory() as leer,
            patch("django_project.urls.PWA", Path(leer)),
        ):
            antwort = self.client.get("/app/")
        self.assertEqual(antwort.status_code, 404)
        self.assertIn("npm run build", antwort.content.decode())

    def test_gebaute_app_wird_ausgeliefert(self):
        self.client.logout()
        with TemporaryDirectory() as ordner:
            (Path(ordner) / "index.html").write_text("<!DOCTYPE html><title>CrossTally</title>")
            with patch("django_project.urls.PWA", Path(ordner)):
                antwort = self.client.get("/app/")
        self.assertEqual(antwort.status_code, 200)
        self.assertIn(b"<title>CrossTally</title>", antwort.content)
        self.assertEqual(antwort["Cache-Control"], "max-age=60")

    def test_die_app_traegt_den_eigenen_namen(self):
        """Whoever renames the application in the administration reads the name in the app too."""
        eigenes = Erscheinungsbild.standard().kopie("Eigenes")
        eigenes.anwendungsname = "Lager & Co"
        eigenes.save()
        eigenes.aktivieren()
        self.client.logout()
        with TemporaryDirectory() as ordner:
            (Path(ordner) / "index.html").write_text("<!DOCTYPE html><title>CrossTally</title>")
            (Path(ordner) / "manifest.webmanifest").write_text(
                '{"name": "CrossTally", "short_name": "CrossTally", "start_url": "/app/"}'
            )
            with patch("django_project.urls.PWA", Path(ordner)):
                seite = self.client.get("/app/")
                manifest = self.client.get("/app/manifest.webmanifest")
        self.assertIn(b"<title>Lager &amp; Co</title>", seite.content)
        self.assertEqual(manifest["Content-Type"], "application/manifest+json")
        self.assertEqual(manifest.json()["name"], "Lager & Co")
        self.assertEqual(manifest.json()["short_name"], "Lager & Co")
        self.assertEqual(manifest.json()["start_url"], "/app/")

    def test_zaehler_ohne_is_staff_kommt_an_die_app(self):
        """Counters have no 'is_staff'. The middleware must not reject them."""
        zaehler = CustomUser.objects.create_user(
            username="zaehlerin", password="geheim", email="z2@example.org"
        )
        self.client.force_login(zaehler)
        antwort = self.client.get("/app/")
        self.assertNotEqual(antwort.status_code, 403)


class SpaltenDerPruefliste(InventurSeiten):
    def test_ist_steht_vor_soll(self):
        """The counted amount is the result. The target stands next to it as a comparison."""
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inhalt = self.client.get(reverse("inventur_pruefen", args=[inventur.pk])).content.decode()
        self.assertLess(inhalt.index(">Ist<"), inhalt.index(">Soll<"))


class NamenInDerPruefliste(InventurSeiten):
    """The product on top, the Compeso designation below. Both are needed."""

    def test_beide_namen_stehen_da(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.zaehlpositionen.update(eingabemenge=Decimal("1"))
        antwort = self.client.get(reverse("inventur_pruefen", args=[inventur.pk]))
        self.assertContains(antwort, "Artikel C 0,33")  # the product
        self.assertContains(antwort, "Artikel C 0,33 Flasche")  # WinTICKET

    def test_suche_findet_ueber_beide(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.zaehlpositionen.update(eingabemenge=Decimal("1"))
        adresse = reverse("inventur_pruefen", args=[inventur.pk])
        self.assertEqual(len(self.client.get(adresse, {"q": "Flasche"}).context["zeilen"]), 1)
        self.assertEqual(
            len(self.client.get(adresse, {"q": "Artikel C 0,33"}).context["zeilen"]), 1
        )


class AdminsBeiDenZaehlenden(InventurSeiten):
    def test_admin_steht_in_der_liste(self):
        """They are in no assignment but may count. Otherwise the card would look empty."""
        admin = CustomUser.objects.create_superuser(
            username="adah",
            password="geheim",
            email="ada@example.org",
            first_name="Ada",
            last_name="Admin",
        )
        inventur = self.inventur()
        self.assertIn(admin, inventur.alle_zaehlenden())

        antwort = self.client.get(inventur.get_absolute_url())
        self.assertContains(antwort, "Ada Admin")
        self.assertContains(antwort, "immer dabei")


class SeltenereZeilen(InventurSeiten):
    """What else the position list has to show.

    Weighed goods and recipe articles take other branches in the template
    than a pack row: the unit comes from elsewhere, the input is a gross
    weight per vessel. Unexpected positions carry their badge.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.gramm = Basiseinheit.objects.get(name="g")
        cls.nachos = Ware.objects.create(name="Artikel N", basiseinheit=cls.gramm, abwiegbar=True)
        cls.nacho_artikel = Hausartikel.objects.create(
            haus=cls.haus,
            ware=cls.nachos,
            warengruppe=cls.artikel.warengruppe,
            artikelnummer="200002",
            name="Artikel N lose",
        )
        cls.schale = Behaelter.objects.create(name="Schale 250 g", tara=Decimal("250"))
        cls.nachos.behaelter.add(cls.schale)

        cls.portion = Rezepturartikel.objects.create(
            name="Portion N", basiseinheit=Basiseinheit.objects.get(name="Stück(e)")
        )
        Rezepturkomponente.objects.create(
            rezepturartikel=cls.portion,
            ware=cls.nachos,
            menge_in_basiseinheit=Decimal("140"),
        )
        Erwartung.objects.create(ort=cls.ort, artikel=cls.nacho_artikel, wiegen_erwartet=True)
        Erwartung.objects.create(ort=cls.ort, artikel=cls.portion)

    def test_waage_rezepturartikel_und_unerwartetes_rendern(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        gewogen = inventur.zaehlpositionen.get(artikel=self.nacho_artikel, gebinde__isnull=True)
        # Two bowls, each with its own tare: 1000 g + 560 g.
        Wiegung.objects.create(
            zaehlposition=gewogen, brutto_gramm=Decimal("1250"), behaelter=self.schale
        )
        Wiegung.objects.create(
            zaehlposition=gewogen, brutto_gramm=Decimal("810"), behaelter=self.schale
        )

        portion = inventur.zaehlpositionen.get(artikel=self.portion)
        portion.eingabemenge = Decimal("4")
        portion.unerwartet = True
        portion.save()

        antwort = self.client.get(inventur.get_absolute_url())
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Schale 250 g")
        self.assertContains(antwort, "unerwartet")

        # 1000 g + 560 g from two bowls, plus 4 portions of 140 g. The
        # recipe article resolves over the recipe onto the same article.
        pruefen = self.client.get(reverse("inventur_pruefen", args=[inventur.pk]))
        zeilen = {zeile.hausartikel.pk: zeile for zeile in pruefen.context["zeilen"]}
        self.assertEqual(zeilen[self.nacho_artikel.pk].gesamtmenge_basiseinheit, Decimal("2120"))

    def test_menge_steht_in_der_einheit_des_hauses(self):
        """The product is kept in kilograms, the house counts in grams.

        The quantity of the row is computed in the house's unit. The label
        next to it has to say the same, otherwise 2200 g read as 2200 kg.
        """
        kg = Basiseinheit.objects.get(name="kg")
        sahne = Ware.objects.create(name="Ware S", basiseinheit=kg)
        sack = Gebinde.objects.create(ware=sahne, name="Sack", faktor_in_basiseinheit="2.2")
        artikel = Hausartikel.objects.create(
            haus=self.haus,
            ware=sahne,
            warengruppe=self.artikel.warengruppe,
            artikelnummer="200003",
            name="Ware S Sack",
            basiseinheit=self.gramm,
        )
        ErwartungGebinde.objects.create(
            erwartung=Erwartung.objects.create(ort=self.ort, artikel=artikel), gebinde=sack
        )
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.zaehlpositionen.filter(artikel=artikel).update(eingabemenge=Decimal("1"))

        antwort = self.client.get(inventur.get_absolute_url())

        [zeile] = [
            zeile
            for ort in antwort.context["orte"]
            for zeile in ort["zeilen"]
            if zeile["artikel"].pk == artikel.pk
        ]
        self.assertEqual(zeile["menge"], Decimal("2200"))
        self.assertEqual(zeile["einheit"], self.gramm)
        self.assertRegex(
            antwort.content.decode(), r'2200\s*<span class="text-body-secondary small">g</span>'
        )


def zwei_waren_in_der_zaehlliste(cls) -> tuple[Gebinde, Hausartikel]:
    """An "Artikel C" with two packs before "Artikel A", which comes first in the alphabet.

    Returns the second pack of "Artikel C" and the "Artikel A".
    """
    einzeln = Gebinde.objects.create(
        ware=cls.ware, name="Einzeln", faktor_in_basiseinheit=Decimal("1")
    )
    # On the product "Einzeln" comes before "Kasten".
    Gebinde.neu_sortieren(cls.ware.pk, [einzeln.pk, cls.kasten.pk])
    cola = Erwartung.objects.get(ort=cls.ort, artikel=cls.artikel)
    ErwartungGebinde.objects.create(erwartung=cola, gebinde=einzeln)

    ware_a = Ware.objects.create(name="Artikel A", basiseinheit=cls.stueck)
    artikel_a = Hausartikel.objects.create(
        haus=cls.haus,
        ware=ware_a,
        warengruppe=cls.artikel.warengruppe,
        artikelnummer="101003",
        name="Artikel A",
    )
    karton = Gebinde.objects.create(
        ware=ware_a, name="Karton", faktor_in_basiseinheit=Decimal("12")
    )
    # In the counting list behind "Artikel C", although it comes first in the alphabet.
    ErwartungGebinde.objects.create(
        erwartung=Erwartung.objects.create(ort=cls.ort, artikel=artikel_a), gebinde=karton
    )
    return einzeln, artikel_a


class ReihenfolgeDerPositionen(InventurSeiten):
    """The positions of a running inventory stand per location as in the counting app.

    Whoever looks on the page for what is still missing should have the same
    list in front of them as the counters in storage: the order of the
    counting list, in it the packs in their order on the product. By the
    alphabet "Artikel A" came first although it stands behind "Artikel C" on
    the shelf.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.einzeln, cls.artikel_a = zwei_waren_in_der_zaehlliste(cls)

    def test_wie_in_der_zaehl_app(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)

        antwort = self.client.get(inventur.get_absolute_url())

        [ort] = antwort.context["orte"]
        self.assertEqual(
            [(zeile["artikel"].warenname, zeile["worin"]) for zeile in ort["zeilen"]],
            [
                ("Artikel C 0,33", "Einzeln"),
                ("Artikel C 0,33", "Kasten"),
                ("Artikel A", "Karton"),
            ],
        )

    def test_eine_umsortierte_zaehlliste_wirkt_ab_der_naechsten_inventur(self):
        """The positions carry their position from creation. Re-sorting happens at the location."""
        erwartungen = Erwartung.objects.filter(ort=self.ort)
        Erwartung.neu_sortieren(
            self.ort.pk,
            [erwartungen.get(artikel=self.artikel_a).pk, erwartungen.get(artikel=self.artikel).pk],
        )
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)

        [ort] = self.client.get(inventur.get_absolute_url()).context["orte"]

        self.assertEqual(ort["zeilen"][0]["artikel"].warenname, "Artikel A")


class ReiterDerJahresinventur(InventurSeiten):
    """In the annual inventory a tab per type, as in the counting app.

    In front the monthly round, behind it what is only due once a year. Every
    tab names its own state, and per location only its rows stand.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.einzeln, cls.artikel_a = zwei_waren_in_der_zaehlliste(cls)
        cls.artikel_a.inventurart_setzen(jahresinventur())

    def jahresinventur(self) -> Inventur:
        inventur = inventur_anlegen(
            self.haus, monatsinventur(), jahresinventur(), stichtag=STICHTAG
        )
        inventur.zaehlpositionen_anlegen()
        inventur.zaehlung_starten()
        return inventur

    def zeilen(self, antwort) -> list[str]:
        return [
            f"{zeile['artikel'].warenname} {zeile['worin']}"
            for ort in antwort.context["orte"]
            for zeile in ort["zeilen"]
        ]

    def test_vorn_der_monatsrundgang(self):
        inventur = self.jahresinventur()
        position = inventur.zaehlpositionen.get(gebinde=self.kasten)
        position.eingabemenge = Decimal("2")
        position.save()

        antwort = self.client.get(inventur.get_absolute_url())

        self.assertEqual(self.zeilen(antwort), ["Artikel C 0,33 Einzeln", "Artikel C 0,33 Kasten"])
        self.assertEqual(
            [
                (r["name"], r["gezaehlt"], r["gesamt"], r["aktiv"])
                for r in antwort.context["arten_reiter"]
            ],
            [("Monatsinventur", 1, 2, True), ("Jahresinventur", 0, 1, False)],
        )
        self.assertContains(antwort, f'href="?reiter={jahresinventur().pk}"')

    def test_dahinter_was_nur_jaehrlich_dran_ist(self):
        inventur = self.jahresinventur()

        antwort = self.client.get(inventur.get_absolute_url(), {"reiter": jahresinventur().pk})

        self.assertEqual(self.zeilen(antwort), ["Artikel A Karton"])
        self.assertTrue(antwort.context["arten_reiter"][1]["aktiv"])

    def test_die_monatsinventur_hat_keine_reiter(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        antwort = self.client.get(inventur.get_absolute_url(), {"reiter": jahresinventur().pk})
        self.assertNotIn("arten_reiter", antwort.context)
        # The tab link, not the bare parameter: the page carries its own address
        # with '?reiter=' in the return fields of the menus, and under PostgreSQL
        # the sequences do not roll back, so '?reiter=1' is a prefix of '?reiter=130'.
        self.assertNotContains(antwort, f'href="?reiter={monatsinventur().pk}"')

    def test_zeigt_den_warennamen_wie_die_app(self):
        """The house's designation stands small next to it because it deviates."""
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        antwort = self.client.get(inventur.get_absolute_url())
        seite = antwort.content.decode()
        self.assertIn('<span class="ms-1">Artikel C 0,33</span>', seite)
        self.assertIn("Artikel C 0,33 Flasche</span>", seite)


class Pruefansicht(InventurSeiten):
    """Check and close: the list, the notes and the only closing path."""

    def test_seite_rendert_nach_spalten_und_gegliedert(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        adresse = reverse("inventur_pruefen", args=[inventur.pk])
        antwort = self.client.get(adresse, {"sortierung": "-differenz"})
        self.assertEqual(antwort.status_code, 200)
        self.assertIsNone(antwort.context["gruppen"])

        antwort = self.client.get(adresse, {"gliederung": "warengruppe"})
        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(
            [gruppe["name"] for gruppe in antwort.context["gruppen"]], ["Warengruppe G"]
        )
        # Grouped, the product group stands in the group's header, not as a column.
        self.assertNotContains(antwort, 'title="Nach „Warengruppe“ sortieren"')

    def test_nach_warenfamilie_mit_summe_der_differenzen(self):
        """Per family what is missing by the current state, in its lead unit.

        Two products in kilograms and grams flow together into the lead unit.
        What has no family stands last and on its own.
        """
        kg = Basiseinheit.objects.get(name="kg")
        gramm = Basiseinheit.objects.get(name="g")
        familie = Warenfamilie.objects.create(name="Warenfamilie R", leiteinheit=kg)
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        gruppe = self.artikel.warengruppe
        for nummer, (name, einheit, soll) in enumerate(
            [("Ware N lose", kg, "2"), ("Ware N Beutel", gramm, "500")], start=101010
        ):
            Sollbestand.objects.create(
                inventur=inventur,
                hausartikel=Hausartikel.objects.create(
                    haus=self.haus,
                    ware=Ware.objects.create(name=name, basiseinheit=einheit, warenfamilie=familie),
                    warengruppe=gruppe,
                    artikelnummer=str(nummer),
                    name=name,
                ),
                menge_basiseinheit=Decimal(soll),
            )

        antwort = self.client.get(
            reverse("inventur_pruefen", args=[inventur.pk]), {"gliederung": "familie"}
        )
        gruppen = antwort.context["gruppen"]
        self.assertEqual(
            [gruppe.name for gruppe in gruppen], ["Warenfamilie R", "Ohne Warenfamilie"]
        )
        # Nothing counted: 2 kg and 500 g are missing, together 2.5 kg.
        self.assertEqual(gruppen[0].differenz, "-2,5 kg")
        self.assertEqual(gruppen[0].differenz_klasse, "differenz-fehlt")
        self.assertContains(antwort, f'data-wert="familiensumme-{familie.pk}"')
        # The article without family has no target. There is nothing to sum.
        self.assertEqual(gruppen[1].differenz, "")
        self.assertEqual(gruppen[1].differenz_klasse, "")

    def test_ein_ueberhang_traegt_sein_plus(self):
        """In the row and in the family's sum. The minus comes with the number itself."""
        familie = Warenfamilie.objects.create(name="Warenfamilie R", leiteinheit=self.stueck)
        self.ware.warenfamilie = familie
        self.ware.save()
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.zaehlpositionen.update(eingabemenge=Decimal("2"))  # two crates, 40 bottles
        Sollbestand.objects.create(
            inventur=inventur, hausartikel=self.artikel, menge_basiseinheit=Decimal("30")
        )

        antwort = self.client.get(
            reverse("inventur_pruefen", args=[inventur.pk]), {"gliederung": "familie"}
        )

        gruppe = antwort.context["gruppen"][0]
        self.assertEqual(gruppe.differenz, "+10 Stk.")
        self.assertEqual(gruppe.differenz_klasse, "differenz-mehr")
        self.assertEqual(gruppe.eintraege[0].differenz_text, "+10")
        # The colour stands inside the cell: "Refresh values" only swaps the content.
        self.assertContains(antwort, '<span class="differenz-mehr">+10</span>')

    def test_nach_warengruppe_ohne_summe(self):
        """A product group mixes units. A sum over it would mean nothing."""
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        antwort = self.client.get(
            reverse("inventur_pruefen", args=[inventur.pk]), {"gliederung": "warengruppe"}
        )
        self.assertNotIn("differenz", antwort.context["gruppen"][0])
        self.assertNotContains(antwort, 'data-wert="familiensumme-')

    def test_ungezaehlte_position_steht_trotzdem_in_der_liste(self):
        """The chosen scope: what there is a position for belongs on the list."""
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        antwort = self.client.get(reverse("inventur_pruefen", args=[inventur.pk]))
        zeile = antwort.context["zeilen"][0]
        self.assertEqual(zeile.hausartikel, self.artikel)
        self.assertFalse(zeile.gezaehlt)
        self.assertEqual(antwort.context["anzahl_offen"], 1)
        self.assertContains(antwort, "noch nicht gezählt")

    def test_suche_und_filter(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        adresse = reverse("inventur_pruefen", args=[inventur.pk])

        self.assertEqual(len(self.client.get(adresse, {"q": "artikel c"}).context["zeilen"]), 1)
        self.assertEqual(len(self.client.get(adresse, {"q": "artikel x"}).context["zeilen"]), 0)
        self.assertEqual(len(self.client.get(adresse, {"q": "101001"}).context["zeilen"]), 1)
        # Filtered is the display, not the scope.
        gefiltert = self.client.get(adresse, {"abweichung": "abweichung"})
        self.assertEqual(len(gefiltert.context["zeilen"]), 0)
        self.assertEqual(gefiltert.context["anzahl_gesamt"], 1)

    def test_nur_noch_nicht_gezaehlte_und_nur_mit_notiz(self):
        """Artikel C stays uncounted. Ware N is counted and carries a note."""
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        ware = Ware.objects.create(name="Ware N", basiseinheit=self.stueck)
        andere = Hausartikel.objects.create(
            haus=self.haus,
            ware=ware,
            warengruppe=self.artikel.warengruppe,
            artikelnummer="101002",
            name="Ware N",
        )
        inventur.zaehlpositionen.create(
            ort=self.ort,
            artikel=andere,
            gebinde=Gebinde.objects.create(
                ware=ware, name="Karton", faktor_in_basiseinheit=Decimal("10")
            ),
            eingabemenge=Decimal("1"),
            unerwartet=True,
        )
        Inventurnotiz.objects.create(inventur=inventur, hausartikel=andere, text="hinten")
        adresse = reverse("inventur_pruefen", args=[inventur.pk])

        for auswahl, erwartet in (("offen", [self.artikel]), ("notiz", [andere])):
            with self.subTest(auswahl=auswahl):
                zeilen = self.client.get(adresse, {"abweichung": auswahl}).context["zeilen"]
                self.assertEqual([zeile.hausartikel for zeile in zeilen], erwartet)

    def test_eine_abgeschlossene_nimmt_keine_notiz_mehr(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.abschliessen()

        antwort = self.client.post(
            reverse("inventur_pruefen", args=[inventur.pk]),
            {f"notiz_{self.artikel.pk}": "zu spät"},
        )

        self.assertRedirects(antwort, inventur.get_absolute_url())
        self.assertEqual(
            [str(meldung) for meldung in get_messages(antwort.wsgi_request)],
            ["Diese Inventur ist abgeschlossen."],
        )
        self.assertFalse(Inventurnotiz.objects.exists())

    def test_schliesst_jemand_beim_speichern_ab_sagt_die_seite_es(self):
        """Closed between the page's look at the status and the lock of the notes."""
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        adresse = reverse("inventur_pruefen", args=[inventur.pk])
        daten = {f"notiz_{self.artikel.pk}": "zwei Kästen"}

        with patch(
            "inventur.views.review.notizen_speichern",
            side_effect=InventurAbgeschlossen(inventur),
        ):
            antwort = self.client.post(adresse, daten)
            nebenbei = self.client.post(adresse, daten | {"nebenbei": "1"})

        self.assertRedirects(antwort, inventur.get_absolute_url())
        self.assertEqual(
            [str(meldung) for meldung in get_messages(antwort.wsgi_request)],
            ["Diese Inventur ist abgeschlossen."],
        )
        # The script hears it as the answer to its background save.
        self.assertEqual(
            (nebenbei.status_code, nebenbei.content.decode()),
            (409, "Notizen nicht gesichert, denn die Inventur ist inzwischen abgeschlossen."),
        )

    def test_neue_werte_unter_der_sperre_halten_den_abschluss_an(self):
        """The page compares early, for the message. Final is the comparison under the lock."""
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.zaehlpositionen.update(eingabemenge=Decimal("2"))
        adresse = reverse("inventur_pruefen", args=[inventur.pk])

        with patch("inventur.closing.decisions.zaehlstand", return_value="anders"):
            antwort = self.client.post(adresse, {"abschliessen": "1", **gesehen(inventur)})

        self.assertRedirects(antwort, adresse)
        self.assertEqual(
            [str(meldung) for meldung in get_messages(antwort.wsgi_request)], [NEUE_WERTE]
        )
        inventur.refresh_from_db()
        self.assertEqual(inventur.status, Inventurstatus.ZAEHLUNG)

    def test_eine_auf_null_gerundete_differenz_ist_keine_abweichung(self):
        """40 bottles against a target of 40.4 read "40, 40, 0" in green.

        Until 10/2026 the row read "-0", was marked and stood under "Only with
        deviation".
        """
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.zaehlpositionen.update(eingabemenge=Decimal("2"))  # two crates, 40 bottles
        Sollbestand.objects.create(
            inventur=inventur, hausartikel=self.artikel, menge_basiseinheit=Decimal("40.4")
        )
        adresse = reverse("inventur_pruefen", args=[inventur.pk])

        antwort = self.client.get(adresse)
        zeile = antwort.context["zeilen"][0]
        self.assertEqual((zeile.ist_text, zeile.soll_text, zeile.differenz_text), ("40", "40", "0"))
        self.assertEqual(antwort.context["anzahl_auffaellig"], 0)
        gefiltert = self.client.get(adresse, {"abweichung": "abweichung"})
        self.assertEqual(gefiltert.context["zeilen"], [])

    def test_die_suche_ordnet_nach_relevanz(self):
        """As in the lists of the catalog: the designation first, the group last.

        The numbers run against it. Sorted by number, as without a search, the
        row found through its group would come first.
        """
        gruppe = Warengruppe.objects.create(name="Warengruppe Limo")
        for nummer, name, wo in (
            ("101011", "Apfelsaft", gruppe),
            ("101012", "Zitronenlimo", self.artikel.warengruppe),
            ("101013", "Limo Orange", self.artikel.warengruppe),
        ):
            artikel = Hausartikel.objects.create(
                haus=self.haus,
                ware=Ware.objects.create(name=name, basiseinheit=self.stueck),
                warengruppe=wo,
                artikelnummer=nummer,
                name=name,
            )
            Erwartung.objects.create(ort=self.ort, artikel=artikel)
            ErwartungGebinde.objects.create(
                erwartung=artikel.erwartet_an.get(),
                gebinde=Gebinde.objects.create(
                    ware=artikel.ware, name="Kasten", faktor_in_basiseinheit=Decimal("20")
                ),
            )
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        adresse = reverse("inventur_pruefen", args=[inventur.pk])

        def namen(**parameter):
            zeilen = self.client.get(adresse, parameter).context["zeilen"]
            return [zeile.hausartikel.name for zeile in zeilen]

        self.assertEqual(namen(q="limo"), ["Limo Orange", "Zitronenlimo", "Apfelsaft"])
        # A chosen column sorts, search or not.
        self.assertEqual(
            namen(q="limo", sortierung="nummer"), ["Apfelsaft", "Zitronenlimo", "Limo Orange"]
        )

    def test_notiz_wird_gespeichert_und_wieder_geloescht(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        adresse = reverse("inventur_pruefen", args=[inventur.pk])

        self.client.post(adresse, {f"notiz_{self.artikel.pk}": "  Regal war leer  "})
        self.assertEqual(Inventurnotiz.objects.get().text, "Regal war leer")

        self.client.post(adresse, {f"notiz_{self.artikel.pk}": "   "})
        self.assertEqual(Inventurnotiz.objects.count(), 0)

    def test_gefilterte_ansicht_loescht_nicht_die_uebrigen_notizen(self):
        """A submit carries only the visible rows. The rest stays."""
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        # Own product: per house only one house article per product may be active.
        anderer = Hausartikel.objects.create(
            haus=self.haus,
            ware=Ware.objects.create(name="Artikel C light 0,33", basiseinheit=self.stueck),
            warengruppe=self.artikel.warengruppe,
            artikelnummer="101002",
            name="Artikel C light",
        )
        Inventurnotiz.objects.create(inventur=inventur, hausartikel=anderer, text="bleibt")

        self.client.post(
            reverse("inventur_pruefen", args=[inventur.pk]),
            {f"notiz_{self.artikel.pk}": "neu"},
        )
        self.assertEqual(Inventurnotiz.objects.filter(hausartikel=anderer).count(), 1)
        self.assertEqual(Inventurnotiz.objects.count(), 2)

    def test_fremdes_haus_wird_abgewiesen(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        fremd = Hausartikel.objects.create(
            haus=self.zweites,
            ware=self.ware,
            warengruppe=self.artikel.warengruppe,
            artikelnummer="301003",
            name="Artikel C Kino Süd",
        )
        self.client.post(
            reverse("inventur_pruefen", args=[inventur.pk]), {f"notiz_{fremd.pk}": "geht nicht"}
        )
        self.assertEqual(Inventurnotiz.objects.count(), 0)

    def test_abschluss_speichert_die_notiz_und_friert_sie_ein(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.zaehlpositionen.update(eingabemenge=Decimal("2"))

        antwort = self.client.post(
            reverse("inventur_pruefen", args=[inventur.pk]),
            {
                f"notiz_{self.artikel.pk}": "zwei Kästen hinten im Lager",
                "abschliessen": "1",
                **gesehen(inventur),
            },
        )
        self.assertRedirects(antwort, inventur.get_absolute_url())

        ergebnis = inventur.ergebnisse.get()
        self.assertEqual(ergebnis.notiz, "zwei Kästen hinten im Lager")
        self.assertEqual(ergebnis.gesamtmenge_basiseinheit, Decimal("40"))
        self.assertEqual(Inventurnotiz.objects.count(), 0)

    def test_zweiter_fast_gleichzeitiger_abschluss_wird_zur_meldung(self):
        """The second click finds the inventory already closed: no error page."""
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.zaehlpositionen.update(eingabemenge=Decimal("2"))
        # The view has already loaded the open state, then the first click closes.
        with patch("inventur.views.review.klaeren") as klaeren:
            klaeren.side_effect = lambda inv: (inventur.abschliessen(), klaeren_echt(inv))[1]
            antwort = self.client.post(
                reverse("inventur_pruefen", args=[inventur.pk]),
                {"abschliessen": "1", **gesehen(inventur)},
                follow=True,
            )
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "bereits abgeschlossen")
        self.assertEqual(inventur.ergebnisse.count(), 1)

    def test_kein_abschluss_aus_der_vorbereitung(self):
        inventur = self.inventur()
        self.client.post(reverse("inventur_pruefen", args=[inventur.pk]), {"abschliessen": "1"})
        inventur.refresh_from_db()
        self.assertEqual(inventur.status, Inventurstatus.VORBEREITUNG)

    def test_in_der_vorbereitung_nimmt_die_pruefliste_notizen_an(self):
        """Unlike the app, which holds its job: here they wait for the count and freeze then."""
        inventur = self.inventur()

        antwort = self.client.post(
            reverse("inventur_pruefen", args=[inventur.pk]),
            {f"notiz_{self.artikel.pk}": "vorab notiert", "nebenbei": "1"},
        )

        self.assertEqual(antwort.status_code, 204)
        self.assertEqual(inventur.notizen.get().text, "vorab notiert")

    def test_abgeschlossene_leitet_auf_die_uebersicht(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        inventur.abschliessen()
        antwort = self.client.get(reverse("inventur_pruefen", args=[inventur.pk]))
        self.assertRedirects(antwort, inventur.get_absolute_url())

    def test_detailseite_fuehrt_zum_pruefen_statt_selbst_abzuschliessen(self):
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        antwort = self.client.get(inventur.get_absolute_url())
        self.assertContains(antwort, reverse("inventur_pruefen", args=[inventur.pk]))
        # No second result table: the differences are in the check view.
        self.assertNotIn("zeilen", antwort.context)


class SollstattIst(InventurSeiten):
    """The tick "Target counts": the mark only takes effect at closing."""

    def vorbereiten(self):
        """An inventory with counted quantity and deviating target."""
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        position = inventur.zaehlpositionen.get()
        position.eingabemenge = Decimal("3")  # 3 crates of 20 = 60 pieces
        position.save()
        Sollbestand.objects.create(
            inventur=inventur, hausartikel=self.artikel, menge_basiseinheit=Decimal("100")
        )
        return inventur

    def pruefen_absenden(self, inventur, **felder):
        return self.client.post(reverse("inventur_pruefen", args=[inventur.pk]), felder)

    def test_der_haken_heisst_ueberall_soll_zaehlt(self):
        """The column said "Soll zählt", a screen reader and the administration something else."""
        inventur = self.vorbereiten()
        seite = self.client.get(reverse("inventur_pruefen", args=[inventur.pk]))
        self.assertContains(seite, f'aria-label="Soll zählt bei {self.artikel.name}"')
        feld = Inventurnotiz._meta.get_field("soll_statt_ist")
        assert isinstance(feld, models.BooleanField)
        self.assertEqual(str(feld.verbose_name), "Soll zählt")

    def test_haken_wird_gemerkt_und_aendert_die_zaehlung_nicht(self):
        inventur = self.vorbereiten()

        self.pruefen_absenden(
            inventur,
            **{
                f"notiz_{self.artikel.pk}": "",
                f"sollstattist_{self.artikel.pk}": "on",
            },
        )

        vermerk = inventur.notizen.get()
        self.assertTrue(vermerk.soll_statt_ist)
        self.assertEqual(vermerk.text, "")
        # The count stays as it was.
        self.assertEqual(inventur.zaehlpositionen.get().eingabemenge, Decimal("3.000"))
        self.assertEqual(inventur.mengen_je_hausartikel()[self.artikel.pk], Decimal("60"))

    def test_beim_abschluss_gilt_dann_der_sollbestand(self):
        inventur = self.vorbereiten()
        inventur.notizen.create(hausartikel=self.artikel, text="", soll_statt_ist=True)

        inventur.abschliessen()

        ergebnis = Inventurergebnis.objects.get()
        self.assertEqual(ergebnis.gesamtmenge_basiseinheit, Decimal("100.000"))
        self.assertEqual(ergebnis.sollbestand, Decimal("100.000"))
        self.assertEqual(ergebnis.differenz, Decimal("0"))

    def test_ohne_haken_bleibt_es_beim_gezaehlten(self):
        inventur = self.vorbereiten()

        inventur.abschliessen()

        ergebnis = Inventurergebnis.objects.get()
        self.assertEqual(ergebnis.gesamtmenge_basiseinheit, Decimal("60"))
        self.assertEqual(ergebnis.differenz, Decimal("-40.000"))

    def test_ohne_sollbestand_laeuft_der_haken_ins_leere(self):
        """Then there is nothing to adopt. A zero would be a claim."""
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        position = inventur.zaehlpositionen.get()
        position.eingabemenge = Decimal("2")
        position.save()
        inventur.notizen.create(hausartikel=self.artikel, text="", soll_statt_ist=True)

        inventur.abschliessen()

        ergebnis = Inventurergebnis.objects.get()
        self.assertEqual(ergebnis.gesamtmenge_basiseinheit, Decimal("40"))
        self.assertIsNone(ergebnis.differenz)

    def test_abgewaehlt_verschwindet_der_vermerk(self):
        inventur = self.vorbereiten()
        inventur.notizen.create(hausartikel=self.artikel, text="", soll_statt_ist=True)

        self.pruefen_absenden(inventur, **{f"notiz_{self.artikel.pk}": ""})

        self.assertEqual(inventur.notizen.count(), 0)

    def test_notiz_und_haken_stehen_nebeneinander(self):
        inventur = self.vorbereiten()

        self.pruefen_absenden(
            inventur,
            **{
                f"notiz_{self.artikel.pk}": "Bruch, Rest verworfen",
                f"sollstattist_{self.artikel.pk}": "on",
            },
        )

        vermerk = inventur.notizen.get()
        self.assertEqual(vermerk.text, "Bruch, Rest verworfen")
        self.assertTrue(vermerk.soll_statt_ist)

    def test_die_pruefliste_zeigt_das_kaestchen(self):
        inventur = self.vorbereiten()

        antwort = self.client.get(reverse("inventur_pruefen", args=[inventur.pk]))

        self.assertContains(antwort, f"sollstattist_{self.artikel.pk}")


class VeralteteZahlen(InventurSeiten):
    """The check view notices when counting continued in the counting app.

    It carries the id of the count state with which its numbers were fetched
    and queries the current one. If the two differ, the script highlights
    "Refresh values" and asks separately before closing ('static/js/refresh.js').
    """

    def setUp(self):
        super().setUp()
        self.inv = self.inventur(status=Inventurstatus.ZAEHLUNG)
        self.position = self.inv.zaehlpositionen.get()

    def test_die_seite_traegt_den_stand_und_die_adresse(self):
        antwort = self.client.get(reverse("inventur_pruefen", args=[self.inv.pk]))
        stand = zaehlstand(self.inv)
        self.assertContains(antwort, f'data-zaehlstand="{stand}"')
        self.assertContains(
            antwort,
            f'data-zaehlstand-adresse="{reverse("inventur_stand", args=[self.inv.pk])}"',
        )
        # The form sends it along at closing.
        self.assertContains(antwort, f'name="zaehlstand" value="{stand}"')
        # The hint next to the closing is in the page, the script shows it.
        self.assertContains(antwort, "data-veraltet-hinweis hidden")

    def abschliessen(self, stand, **weitere):
        """Closes from the check list with this count state and the remarks' state as it stands."""
        daten = {"abschliessen": "1", "bemerkungsstand": bemerkungsstand(self.inv), **weitere}
        if stand is not None:
            daten["zaehlstand"] = stand
        return self.client.post(reverse("inventur_pruefen", args=[self.inv.pk]), daten)

    def test_mit_veraltetem_stand_wird_nicht_abgeschlossen(self):
        """Seen was the old state. The note is saved nonetheless."""
        self.position.eingabemenge = Decimal("2")
        self.position.save()
        gesehen = zaehlstand(self.inv)
        self.position.eingabemenge = Decimal("3")
        self.position.save()

        antwort = self.abschliessen(gesehen, **{f"notiz_{self.artikel.pk}": "Regal hinten"})

        self.assertRedirects(antwort, reverse("inventur_pruefen", args=[self.inv.pk]))
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Inventurstatus.ZAEHLUNG)
        self.assertEqual(Inventurnotiz.objects.get().text, "Regal hinten")
        meldungen = [str(m) for m in get_messages(antwort.wsgi_request)]
        self.assertIn("weitergezählt", " ".join(meldungen))

    def test_ohne_stand_wird_nicht_abgeschlossen(self):
        self.position.eingabemenge = Decimal("2")
        self.position.save()
        self.abschliessen(None)
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Inventurstatus.ZAEHLUNG)

    def test_mit_dem_gesehenen_stand_wird_abgeschlossen(self):
        self.position.eingabemenge = Decimal("2")
        self.position.save()
        self.abschliessen(zaehlstand(self.inv))
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Inventurstatus.ABGESCHLOSSEN)

    def test_die_adresse_nennt_den_jetzigen_stand(self):
        antwort = self.client.get(reverse("inventur_stand", args=[self.inv.pk]))
        self.assertEqual(antwort.json(), {"stand": zaehlstand(self.inv)})

    def test_eine_eingabe_aendert_den_stand(self):
        vorher = zaehlstand(self.inv)
        self.position.eingabemenge = Decimal("3")
        self.position.save()
        self.assertNotEqual(zaehlstand(self.inv), vorher)

    def test_eine_geloeschte_zeile_aendert_den_stand(self):
        """Marked as deleted it touches the timestamp, entirely removed the count."""
        vorher = zaehlstand(self.inv)
        self.position.aktiv = False
        self.position.save(update_fields=["aktiv", "zuletzt_geaendert"])
        markiert = zaehlstand(self.inv)
        self.assertNotEqual(markiert, vorher)

        Zaehlposition.objects.filter(pk=self.position.pk).delete()
        self.assertNotEqual(zaehlstand(self.inv), markiert)

    def test_eine_notiz_aendert_den_stand_nicht(self):
        """Notes arise on this page itself. Nothing is stale because of that."""
        vorher = zaehlstand(self.inv)
        Inventurnotiz.objects.create(inventur=self.inv, hausartikel=self.artikel, text="Regal leer")
        self.assertEqual(zaehlstand(self.inv), vorher)


class GemerkteAnsicht(InventurSeiten):
    """The check list comes back as one last had it, per inventory."""

    def setUp(self):
        super().setUp()
        self.inv = self.inventur(status=Inventurstatus.ZAEHLUNG)
        self.adresse = reverse("inventur_pruefen", args=[self.inv.pk])

    def test_ohne_gemerkte_ansicht_kommt_die_liste_wie_sie_ist(self):
        self.assertEqual(self.client.get(self.adresse).status_code, 200)

    def test_die_zuletzt_benutzte_ansicht_kommt_wieder(self):
        self.client.get(self.adresse, {"gliederung": "warengruppe", "sortierung": "-differenz"})

        antwort = self.client.get(self.adresse)

        self.assertRedirects(
            antwort, f"{self.adresse}?gliederung=warengruppe&sortierung=-differenz"
        )

    def test_filtern_im_hintergrund_merkt_auch_das_leeren(self):
        """The search queries in the background. If it empties everything, that counts as chosen."""
        self.client.get(self.adresse, {"gliederung": "warengruppe"})
        self.client.get(self.adresse, headers={"X-Requested-With": "fetch"})
        self.assertEqual(self.client.get(self.adresse).status_code, 200)

    def test_zuruecksetzen_vergisst_die_ansicht(self):
        self.client.get(self.adresse, {"gliederung": "warengruppe"})

        antwort = self.client.get(self.adresse, {"zuruecksetzen": "1"})

        self.assertRedirects(antwort, self.adresse)
        self.assertEqual(self.client.get(self.adresse).status_code, 200)

    def test_jede_inventur_hat_ihre_eigene(self):
        andere = self.inventur(status=Inventurstatus.ZAEHLUNG, haus=self.zweites)
        self.client.get(self.adresse, {"gliederung": "warengruppe"})
        antwort = self.client.get(reverse("inventur_pruefen", args=[andere.pk]))
        self.assertEqual(antwort.status_code, 200)

    def test_die_seite_meldet_sich_als_dauerhaft_und_setzt_richtig_zurueck(self):
        antwort = self.client.get(self.adresse, {"q": "Artikel"})
        self.assertContains(antwort, "data-ansicht-dauerhaft")
        self.assertContains(antwort, f'href="{self.adresse}?zuruecksetzen=1"')
