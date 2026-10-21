"""Inventories with several types: rows, one per day, creating, changing, run, tabs, closing.

An inventory counts the types chosen when it is created, one tab each in the
app. Which house article belongs to a type says the article. There is no
rule between the types any more: an annual inventory is one that counts all
of them.
"""

import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse
from rest_framework import serializers

from api.checks import pruefe_nicht_aktiv
from api.master_data import artikel_des_hauses
from api.serializers import InventurSerializer, reiter_der_artikel
from django_project.testing import inventur_anlegen, jahresinventur, monatsinventur
from katalog.models import (
    Artikelstatus,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Inventurart,
    Rezepturartikel,
    Rezepturkomponente,
    Ware,
    Warengruppe,
    Zaehlumfang,
)
from organisation.models import Bereich, Ort

from .closing import (
    GANZE_WARE,
    NUR_DIESER,
    WECHSEL_PRAEFIX,
    Entscheidungen,
    abschliessen,
    klaeren,
)
from .models import (
    Artentscheidung,
    Inventur,
    Inventurstatus,
    Sollbestand,
    Zaehlposition,
    arten_des_laufs,
)
from .services import inventuren_anlegen
from .test_views import STICHTAG, InventurSeiten
from .views import positionszeilen


class MitArten(InventurSeiten):
    """Next to Artikel C, counted monthly, cups in a type of their own, both at the counter."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.monat = monatsinventur()
        cls.becherart = Inventurart.objects.create(name="Becherinventur")
        cls.gruppe = Warengruppe.objects.get(name="Warengruppe G")
        cls.becherware = Ware.objects.create(name="Ware B", basiseinheit=cls.stueck)
        cls.becher = Hausartikel.objects.create(
            haus=cls.haus,
            ware=cls.becherware,
            warengruppe=cls.gruppe,
            artikelnummer="101002",
            name="Ware B Becher",
            inventurart=cls.becherart,
        )
        cls.karton = Gebinde.objects.create(
            ware=cls.becherware, name="Karton", faktor_in_basiseinheit=Decimal("50")
        )
        erwartung = Erwartung.objects.create(ort=cls.ort, artikel=cls.becher)
        ErwartungGebinde.objects.create(erwartung=erwartung, gebinde=cls.karton)

    def mit(self, *arten, status=Inventurstatus.VORBEREITUNG, haus=None) -> Inventur:
        """An inventory of these types including its count positions."""
        (inventur,) = inventuren_anlegen([haus or self.haus], arten, STICHTAG)
        if status != Inventurstatus.VORBEREITUNG:
            inventur.zaehlung_starten()
        return inventur

    def gezaehlte_artikel(self, inventur: Inventur) -> set[str]:
        return set(
            inventur.zaehlpositionen.filter(aktiv=True).values_list("artikel__name", flat=True)
        )

    def zweites_haus_mit_artikel_c(self) -> Hausartikel:
        """Kino Süd carries Artikel C too, with a counting list: the product has two articles."""
        ort = Ort.objects.create(
            name="Theke", bereich=Bereich.objects.create(name="Theke", haus=self.zweites)
        )
        artikel = Hausartikel.objects.create(
            haus=self.zweites,
            ware=self.ware,
            warengruppe=self.gruppe,
            artikelnummer="101001",
            name="Artikel C 0,33 Flasche",
        )
        erwartung = Erwartung.objects.create(ort=ort, artikel=artikel)
        ErwartungGebinde.objects.create(erwartung=erwartung, gebinde=self.kasten)
        return artikel


class Zeilen(MitArten):
    def test_eine_art_zaehlt_genau_ihre_artikel(self):
        self.assertEqual(self.gezaehlte_artikel(self.mit(self.becherart)), {"Ware B Becher"})

    def test_zwei_arten_zaehlen_beide(self):
        self.assertEqual(
            self.gezaehlte_artikel(self.mit(self.monat, self.becherart)),
            {"Artikel C 0,33 Flasche", "Ware B Becher"},
        )

    def test_keine_art_holt_eine_andere_von_selbst(self):
        """The annual inventory alone counts no monthly goods: there is no rule between types."""
        self.assertEqual(self.gezaehlte_artikel(self.mit(jahresinventur())), set())

    def test_rezepturartikel_sobald_eine_zutat_dran_ist(self):
        portion = Rezepturartikel.objects.create(name="Portion B", basiseinheit=self.stueck)
        Rezepturkomponente.objects.create(
            rezepturartikel=portion, ware=self.becherware, menge_in_basiseinheit=Decimal("1")
        )
        Erwartung.objects.create(ort=self.ort, artikel=portion)
        self.assertIn("Portion B", self.gezaehlte_artikel(self.mit(self.becherart)))
        Inventur.objects.all().delete()
        self.assertNotIn("Portion B", self.gezaehlte_artikel(self.mit(self.monat)))

    def test_die_zutat_zaehlt_mit_dem_artikel_ihres_hauses(self):
        """In Kino Süd the cups count monthly. There the portion is due in the month."""
        portion = Rezepturartikel.objects.create(name="Portion B", basiseinheit=self.stueck)
        Rezepturkomponente.objects.create(
            rezepturartikel=portion, ware=self.becherware, menge_in_basiseinheit=Decimal("1")
        )
        Hausartikel.objects.create(
            haus=self.zweites,
            ware=self.becherware,
            warengruppe=self.gruppe,
            artikelnummer="101002",
            name="Ware B Becher",
            inventurart=self.monat,
        )
        monatlich = Zaehlumfang((self.monat.pk,))
        self.assertFalse(
            Rezepturartikel.objects.filter(monatlich.rezepturartikel(self.haus.pk)).exists()
        )
        self.assertTrue(
            Rezepturartikel.objects.filter(monatlich.rezepturartikel(self.zweites.pk)).exists()
        )

    def test_name_nennt_die_arten_in_ihrer_reihenfolge(self):
        inventur = self.mit(self.becherart, self.monat)
        self.assertEqual(inventur.arten_anzeige, "Monatsinventur + Becherinventur")
        self.assertIn("Monatsinventur + Becherinventur", str(inventur))

    def test_soll_redet_nur_fuer_die_arten_mit(self):
        inventur = self.mit(self.becherart)
        for artikel in (self.artikel, self.becher):
            Sollbestand.objects.create(
                inventur=inventur, hausartikel=artikel, menge_basiseinheit=Decimal("5")
            )
        self.assertEqual(set(inventur.wirksame_sollbestaende()), {self.becher.pk})
        self.assertEqual(
            [soll.hausartikel for soll in inventur.sollbestaende_im_umfang()], [self.becher]
        )


class EineJeTag(MitArten):
    def test_je_haus_und_tag_gibt_es_eine(self):
        self.mit(self.monat)
        with transaction.atomic(), self.assertRaises(IntegrityError):
            Inventur.objects.create(haus=self.haus, stichtag=STICHTAG)

    def test_das_formular_sagt_es_statt_eines_fehlers(self):
        self.mit(self.monat)
        antwort = self.client.post(
            reverse("inventur_add"),
            {
                "inventurarten": [self.becherart.pk],
                "stichtag": STICHTAG.isoformat(),
                "haeuser": [self.haus.pk],
            },
        )
        self.assertContains(antwort, "gibt es dort schon eine Inventur")
        self.assertEqual(Inventur.objects.count(), 1)


class Anlegen(MitArten):
    def test_die_haeuser_tragen_ihre_arten(self):
        """The script greys out a house by them. Kino Süd carries nothing yet."""
        antwort = self.client.get(reverse("inventur_add"))
        self.assertContains(antwort, f'data-arten="{self.monat.pk} {self.becherart.pk}"')
        self.assertContains(antwort, 'data-arten=""')

    def test_vorbelegt_ist_die_erste_art(self):
        antwort = self.client.get(reverse("inventur_add"))
        self.assertEqual(antwort.context["form"]["inventurarten"].initial, [self.monat.pk])

    def test_ein_haus_ohne_artikel_der_arten_wird_abgewiesen(self):
        antwort = self.client.post(
            reverse("inventur_add"),
            {
                "inventurarten": [self.becherart.pk],
                "stichtag": STICHTAG.isoformat(),
                "haeuser": [self.haus.pk, self.zweites.pk],
            },
        )
        self.assertContains(antwort, "Ohne aktiven Artikel der gewählten Arten: Kino Süd.")
        self.assertFalse(Inventur.objects.exists())

    def test_mit_einer_zweiten_art_kommt_das_haus_dazu(self):
        """Kino Süd carries no cups, but monthly goods: with both types it can be chosen."""
        self.zweites_haus_mit_artikel_c()
        antwort = self.client.post(
            reverse("inventur_add"),
            {
                "inventurarten": [self.monat.pk, self.becherart.pk],
                "stichtag": STICHTAG.isoformat(),
                "haeuser": [self.haus.pk, self.zweites.pk],
            },
        )
        self.assertEqual(antwort.status_code, 302)
        for inventur in Inventur.objects.all():
            with self.subTest(haus=inventur.haus.name):
                self.assertEqual(list(inventur.inventurarten.all()), [self.monat, self.becherart])
        sued = Inventur.objects.get(haus=self.zweites)
        self.assertEqual(self.gezaehlte_artikel(sued), {"Artikel C 0,33 Flasche"})


class ArtenAendern(MitArten):
    def test_eine_neue_art_bekommt_ihre_zeilen(self):
        inventur = self.mit(self.monat)
        dazu, entfernt = inventur.arten_aendern([self.monat, self.becherart])
        # Only the cup's row is new. Artikel C had its row already (until
        # 10/2026 the message counted it as new too).
        self.assertEqual((dazu, entfernt), (1, 0))
        self.assertIn("Ware B Becher", self.gezaehlte_artikel(inventur))

    def test_eine_entfernte_art_nimmt_ihre_ungezaehlten_zeilen_mit(self):
        inventur = self.mit(self.monat, self.becherart)
        dazu, entfernt = inventur.arten_aendern([self.monat])
        self.assertEqual((dazu, entfernt), (0, 1))
        zeile = inventur.zaehlpositionen.get(artikel=self.becher)
        # Marked, not deleted: an app that held the row learns of it.
        self.assertFalse(zeile.aktiv)
        self.assertEqual(self.gezaehlte_artikel(inventur), {"Artikel C 0,33 Flasche"})

    def test_gezaehltes_bleibt(self):
        inventur = self.mit(self.monat, self.becherart)
        inventur.zaehlpositionen.filter(artikel=self.becher).update(eingabemenge=Decimal("3"))
        inventur.arten_aendern([self.monat])
        self.assertTrue(inventur.zaehlpositionen.get(artikel=self.becher).aktiv)

    def test_wiederkommen_belebt_die_zeilen(self):
        inventur = self.mit(self.monat, self.becherart)
        vorher = inventur.zaehlpositionen.get(artikel=self.becher).pk
        inventur.arten_aendern([self.monat])
        dazu, _entfernt = inventur.arten_aendern([self.monat, self.becherart])
        zeile = inventur.zaehlpositionen.get(artikel=self.becher)
        self.assertEqual((zeile.pk, zeile.aktiv, dazu), (vorher, True, 1))

    def test_ein_inzwischen_inaktives_gebinde_kommt_nicht_wieder(self):
        """A new inventory gives it no row, and neither does the way back."""
        inventur = self.mit(self.monat, self.becherart)
        inventur.arten_aendern([self.monat])
        Gebinde.objects.filter(pk=self.karton.pk).update(aktiv=False)
        inventur.arten_aendern([self.monat, self.becherart])
        self.assertNotIn("Ware B Becher", self.gezaehlte_artikel(inventur))

    def test_nur_in_der_vorbereitung(self):
        inventur = self.mit(self.monat, status=Inventurstatus.ZAEHLUNG)
        with self.assertRaises(ValidationError):
            inventur.arten_aendern([self.monat, self.becherart])

    def test_eine_art_bleibt_immer(self):
        with self.assertRaises(ValidationError):
            self.mit(self.monat).arten_aendern([])

    def test_ueber_die_seite(self):
        inventur = self.mit(self.monat)
        adresse = reverse("inventur_arten", args=[inventur.pk])
        self.assertContains(self.client.get(adresse), "Becherinventur")
        antwort = self.client.post(
            adresse, {"inventurarten": [self.monat.pk, self.becherart.pk]}, follow=True
        )
        self.assertContains(antwort, "Zählpositionen dazu")
        # The page changed another object. This one holds its types since it
        # first read them ('Inventur.arten').
        inventur.refresh_from_db()
        self.assertEqual(list(inventur.inventurarten.all()), [self.monat, self.becherart])

    def test_die_seite_weist_eine_art_ohne_artikel_im_haus_ab(self):
        leer = Inventurart.objects.create(name="Leere Art")
        inventur = self.mit(self.monat)
        antwort = self.client.post(
            reverse("inventur_arten", args=[inventur.pk]), {"inventurarten": [leer.pk]}
        )
        self.assertContains(antwort, "führt keinen aktiven Artikel der gewählten Arten")
        self.assertEqual(list(inventur.inventurarten.all()), [self.monat])


class Lauf(MitArten):
    def test_der_lauf_ist_der_stichtag(self):
        inventur = self.mit(self.monat)
        sued = inventur_anlegen(self.zweites, self.becherart, stichtag=STICHTAG)
        self.assertEqual(set(inventur.lauf()), {inventur, sued})
        self.assertTrue(inventur.lauf_url().endswith(f"/{STICHTAG.isoformat()}/"))

    def test_der_lauf_nennt_jede_art_einmal(self):
        inventur = self.mit(self.monat)
        sued = inventur_anlegen(self.zweites, self.becherart, self.monat, stichtag=STICHTAG)
        self.assertEqual(arten_des_laufs([inventur, sued]), "Monatsinventur + Becherinventur")

    def test_die_liste_filtert_nach_art(self):
        self.mit(self.becherart)
        for art, anzahl in ((self.monat, 0), (self.becherart, 1)):
            with self.subTest(art=art.name):
                antwort = self.client.get(reverse("inventur_list"), {"inventurart": art.pk})
                self.assertEqual(len(antwort.context["laeufe"]), anzahl)


class Reiter(MitArten):
    def test_ein_reiter_je_art_mit_zeilen(self):
        inventur = self.mit(self.monat, self.becherart)
        adresse = reverse("inventur_detail", args=[inventur.pk])
        antwort = self.client.get(adresse)
        self.assertContains(antwort, f"?reiter={self.monat.pk}")
        self.assertContains(antwort, f"?reiter={self.becherart.pk}")
        antwort = self.client.get(adresse, {"reiter": self.becherart.pk})
        self.assertContains(antwort, "Ware B Becher")
        self.assertNotContains(antwort, "Artikel C 0,33 Flasche")

    def test_ein_reiter_allein_braucht_keine_leiste(self):
        inventur = self.mit(self.monat, jahresinventur())
        self.assertNotContains(
            self.client.get(reverse("inventur_detail", args=[inventur.pk])),
            f"?reiter={self.monat.pk}",
        )

    def test_was_keine_art_zaehlt_steht_im_ersten_reiter(self):
        inventur = self.mit(self.monat)
        Zaehlposition.objects.create(
            inventur=inventur, ort=self.ort, artikel=self.becher, gebinde=self.karton
        )
        reiter = {zeile["artikel"].name: zeile["reiter"] for zeile in positionszeilen(inventur)}
        self.assertEqual(reiter["Ware B Becher"], self.monat.pk)


class Abschluss(MitArten):
    """The switch of the type at closing asks, like the article's page, for whom it applies."""

    def wechsel(self, artikel: Hausartikel, art: Inventurart, geltung: str = "") -> Entscheidungen:
        schluessel = str(artikel.pk)
        return Entscheidungen(
            ungezaehlt={schluessel: f"{WECHSEL_PRAEFIX}{art.pk}"},
            geltung={schluessel: geltung} if geltung else {},
        )

    def test_gar_nicht_gezaehlt_bietet_die_anderen_arten(self):
        klaerung = klaeren(self.mit(self.monat, self.becherart, status=Inventurstatus.ZAEHLUNG))
        andere = {
            eintrag.hausartikel.name: eintrag.andere_arten
            for eintrag in klaerung.ungezaehlte_artikel
        }
        self.assertEqual(andere["Artikel C 0,33 Flasche"], [self.becherart])
        self.assertEqual(andere["Ware B Becher"], [self.monat])

    def test_ohne_weitere_artikel_fragt_niemand_nach_der_geltung(self):
        inventur = self.mit(self.monat, self.becherart, status=Inventurstatus.ZAEHLUNG)
        bericht = abschliessen(inventur, self.wechsel(self.becher, self.monat))
        self.assertEqual(bericht.art_gewechselt, 1)
        self.becher.refresh_from_db()
        self.assertEqual(self.becher.inventurart, self.monat)

    def test_ohne_antwort_zur_geltung_wird_nichts_abgeschlossen(self):
        self.zweites_haus_mit_artikel_c()
        inventur = self.mit(self.monat, status=Inventurstatus.ZAEHLUNG)
        with self.assertRaisesMessage(ValidationError, "nur für diesen Artikel"):
            abschliessen(inventur, self.wechsel(self.artikel, jahresinventur()))
        inventur.refresh_from_db()
        self.assertEqual(inventur.status, Inventurstatus.ZAEHLUNG)

    def test_nur_fuer_diesen_artikel(self):
        sued = self.zweites_haus_mit_artikel_c()
        inventur = self.mit(self.monat, status=Inventurstatus.ZAEHLUNG)
        abschliessen(inventur, self.wechsel(self.artikel, jahresinventur(), NUR_DIESER))
        self.artikel.refresh_from_db()
        sued.refresh_from_db()
        self.assertEqual(
            (self.artikel.inventurart, sued.inventurart), (jahresinventur(), self.monat)
        )
        self.assertFalse(Artentscheidung.objects.exists())

    def test_fuer_die_ganze_ware_und_im_lauf_festgehalten(self):
        sued = self.zweites_haus_mit_artikel_c()
        inventur = self.mit(self.monat, status=Inventurstatus.ZAEHLUNG)
        abschliessen(inventur, self.wechsel(self.artikel, jahresinventur(), GANZE_WARE))
        sued.refresh_from_db()
        self.assertEqual(sued.inventurart, jahresinventur())
        entscheidung = Artentscheidung.objects.get()
        self.assertEqual(
            (entscheidung.inventur, entscheidung.ware, entscheidung.inventurart),
            (inventur, self.ware, jahresinventur()),
        )

    def test_im_lauf_bleibt_die_ware_dabei_der_artikel_nicht(self):
        """Kino Süd put the whole product into the annual inventory. Kino Ost cannot reverse it."""
        self.zweites_haus_mit_artikel_c()
        (ost, sued) = inventuren_anlegen([self.haus, self.zweites], [self.monat], STICHTAG)
        for inventur in (ost, sued):
            inventur.zaehlung_starten()
        abschliessen(
            sued,
            self.wechsel(Hausartikel.objects.get(haus=self.zweites), jahresinventur(), GANZE_WARE),
        )

        klaerung = klaeren(ost)
        (eintrag,) = [e for e in klaerung.ungezaehlte_artikel if e.hausartikel == self.artikel]
        self.assertTrue(eintrag.ware_gesperrt)
        with self.assertRaisesMessage(ValidationError, "Das gilt auch hier"):
            abschliessen(ost, self.wechsel(self.artikel, self.becherart, GANZE_WARE))
        abschliessen(ost, self.wechsel(self.artikel, self.becherart, NUR_DIESER))
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.inventurart, self.becherart)

    def test_gezaehlt_ausserhalb_der_arten_kommt_in_eine_von_ihnen(self):
        inventur = self.mit(self.monat, status=Inventurstatus.ZAEHLUNG)
        Zaehlposition.objects.create(
            inventur=inventur,
            ort=self.ort,
            artikel=self.becher,
            gebinde=self.karton,
            eingabemenge=Decimal("2"),
            unerwartet=True,
        )
        (eintrag,) = klaeren(inventur).ausserhalb
        self.assertEqual(eintrag.ziele, [self.monat])
        bericht = abschliessen(
            inventur, Entscheidungen(in_art={str(self.becher.pk): str(self.monat.pk)})
        )
        self.assertEqual(bericht.in_die_art, 1)
        self.becher.refresh_from_db()
        self.assertEqual(self.becher.inventurart, self.monat)

    def test_nur_gesperrtes_haelt_den_abschluss_nicht_auf(self):
        self.zweites_haus_mit_artikel_c()
        (ost, sued) = inventuren_anlegen([self.haus, self.zweites], [self.monat], STICHTAG)
        for inventur in (ost, sued):
            inventur.zaehlung_starten()
        ost.zaehlpositionen.filter(artikel=self.artikel).update(eingabemenge=Decimal("1"))
        abschliessen(
            sued,
            self.wechsel(Hausartikel.objects.get(haus=self.zweites), jahresinventur(), GANZE_WARE),
        )

        klaerung = klaeren(ost)
        self.assertEqual([e.hausartikel for e in klaerung.ausserhalb], [self.artikel])
        self.assertTrue(klaerung.ausserhalb[0].gesperrt)
        self.assertTrue(klaerung.leer)

    def test_die_seite_fragt_nach_der_geltung(self):
        self.zweites_haus_mit_artikel_c()
        inventur = self.mit(self.monat, status=Inventurstatus.ZAEHLUNG)
        antwort = self.client.get(reverse("inventur_abschluss", args=[inventur.pk]))
        self.assertContains(antwort, f'name="geltung_{self.artikel.pk}"')
        self.assertContains(antwort, "auch den anderen Artikel der Ware")

    def test_die_frage_laesst_sich_verstecken(self):
        """The script hides it with 'hidden' until a switch is chosen in the row.

        A display class of Bootstrap on the same element wins over 'hidden'
        ('!important' on both, the utility comes later). The question then
        stood there all the time.
        """
        self.zweites_haus_mit_artikel_c()
        inventur = self.mit(self.monat, status=Inventurstatus.ZAEHLUNG)
        seite = self.client.get(reverse("inventur_abschluss", args=[inventur.pk])).content.decode()
        fragen = re.findall(r"<[^>]*data-geltung-frage[^>]*>", seite)
        self.assertTrue(fragen)
        for frage in fragen:
            self.assertNotRegex(frage, r"\bd-(flex|block|inline|grid)\b")


class Schnittstelle(MitArten):
    def test_die_inventur_bringt_ihre_reiter_in_ihrer_reihenfolge(self):
        daten = InventurSerializer(self.mit(self.becherart, self.monat)).data
        self.assertEqual(
            daten["inventurarten"],
            [
                {"id": self.monat.pk, "name": "Monatsinventur"},
                {"id": self.becherart.pk, "name": "Becherinventur"},
            ],
        )
        self.assertEqual(daten["arten_anzeige"], "Monatsinventur + Becherinventur")

    def test_jede_zeile_steht_im_reiter_ihrer_art(self):
        inventur = self.mit(self.monat, self.becherart)
        self.assertEqual(
            reiter_der_artikel(inventur.zaehlpositionen.all()),
            {self.artikel.pk: self.monat.pk, self.becher.pk: self.becherart.pk},
        )

    def test_der_rezepturartikel_steht_im_ersten_reiter_seiner_zutaten(self):
        portion = Rezepturartikel.objects.create(name="Portion BC", basiseinheit=self.stueck)
        for ware in (self.ware, self.becherware):
            Rezepturkomponente.objects.create(
                rezepturartikel=portion, ware=ware, menge_in_basiseinheit=Decimal("1")
            )
        Erwartung.objects.create(ort=self.ort, artikel=portion)
        reiter = reiter_der_artikel(self.mit(self.becherart, self.monat).zaehlpositionen.all())
        self.assertEqual(reiter[portion.pk], self.monat.pk)

    def test_ausserhalb_der_arten_nennt_die_zeile_ihre_eigene(self):
        inventur = self.mit(self.monat)
        Zaehlposition.objects.create(
            inventur=inventur, ort=self.ort, artikel=self.becher, gebinde=self.karton
        )
        self.assertEqual(
            reiter_der_artikel(inventur.zaehlpositionen.all())[self.becher.pk], self.becherart.pk
        )

    def test_die_stammdaten_tragen_die_art(self):
        inventur = self.mit(self.monat)
        arten = {
            eintrag["name"]: eintrag["inventurart"]
            for eintrag in artikel_des_hauses(self.haus, inventur.zaehlumfang)
        }
        self.assertEqual(arten["Ware B"], self.becherart.pk)
        self.assertEqual(arten["Artikel C 0,33"], self.monat.pk)

    def test_ein_inaktiver_artikel_nur_mit_seiner_art(self):
        self.becher.deaktivieren()
        inventur = self.mit(self.monat, status=Inventurstatus.ZAEHLUNG)
        with self.assertRaises(serializers.ValidationError):
            pruefe_nicht_aktiv(self.becher, inventur)
        inventur.inventurarten.add(self.becherart)
        pruefe_nicht_aktiv(self.becher, Inventur.objects.get(pk=inventur.pk))
        self.assertEqual(self.becher.status, Artikelstatus.INAKTIV)
