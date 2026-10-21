"""The target by hand: the maintenance page, the lock and what file and closing do with it.

A target that is already in place only changes after a release. That is no
ornament in the browser but a rule of the form: without release the server
does not take the value. Both are checked, and in addition that a target by
hand applies everywhere the same as one from the file.
"""

from decimal import Decimal
from unittest import mock

from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.urls import reverse

from django_project.testing import jahresinventur, skript
from katalog.models import Artikelstatus, Hausartikel, Ware, Warengruppe

from .closing import klaeren
from .forms import SollPflegeForm
from .models import Inventur, Sollbestand, Sollherkunft
from .services import (
    soll_von_hand_speichern,
    sollbestand_einlesen,
    sollklaerung,
    sollpflege,
)
from .test_views import InventurSeiten, datei


class Sollpflege(InventurSeiten):
    def setUp(self):
        super().setUp()
        self.inv = self.inventur()
        self.adresse = reverse("inventur_soll_pflegen", args=[self.inv.pk])

    def artikel_anlegen(self, name, nummer, haus=None, **abweichend) -> Hausartikel:
        ware = Ware.objects.create(name=name, basiseinheit=self.stueck)
        return Hausartikel.objects.create(
            haus=haus or self.haus,
            ware=ware,
            warengruppe=Warengruppe.objects.get(),
            artikelnummer=nummer,
            name=name,
            **abweichend,
        )

    def soll(self, artikel, menge="5", herkunft=Sollherkunft.DATEI) -> Sollbestand:
        return Sollbestand.objects.create(
            inventur=self.inv,
            hausartikel=artikel,
            menge_basiseinheit=Decimal(menge),
            herkunft=herkunft,
        )

    def sichern(self, **felder):
        return self.client.post(self.adresse, felder)


class Liste(Sollpflege):
    def test_ohne_soll_zuerst_dann_mit(self):
        mit = self.artikel_anlegen("Artikel A", "101000")
        self.soll(mit)
        pflege = sollpflege(self.inv)
        self.assertEqual([z.hausartikel for z in pflege.ohne], [self.artikel])
        self.assertEqual([z.hausartikel for z in pflege.mit], [mit])
        soll = pflege.mit[0].soll
        assert soll is not None
        self.assertEqual(soll.menge_basiseinheit, Decimal("5"))

    def test_nur_aktive_artikel_die_dran_sind(self):
        self.artikel_anlegen("Artikel I", "101002", status=Artikelstatus.INAKTIV)
        jahresware = self.artikel_anlegen("Artikel J", "101003")
        jahresware.inventurart_setzen(jahresinventur())
        self.artikel_anlegen("Artikel X", "101004", haus=self.zweites)
        self.assertEqual([z.hausartikel for z in sollpflege(self.inv).zeilen], [self.artikel])

    def test_die_seite_zeigt_beide_bloecke_in_dieser_reihenfolge(self):
        mit = self.artikel_anlegen("Artikel A", "101000")
        self.soll(mit)
        seite = self.client.get(self.adresse).content.decode()
        self.assertLess(seite.index("Ohne Soll"), seite.index("Mit Soll"))
        self.assertLess(seite.index("Artikel C 0,33 Flasche"), seite.index("Artikel A"))
        self.assertIn("aus der Datei", seite)

    def test_ein_soll_das_steht_ist_gesperrt(self):
        mit = self.artikel_anlegen("Artikel A", "101000")
        self.soll(mit)
        form = self.client.get(self.adresse).context["form"]
        self.assertTrue(form.fields[f"soll_{mit.pk}"].disabled)
        self.assertFalse(form.fields[f"soll_{self.artikel.pk}"].disabled)
        self.assertContains(self.client.get(self.adresse), "data-frei")

    def test_das_skript_gibt_die_zeile_frei(self):
        quelle = skript("soll_edit")
        self.assertIn("eingabe.disabled = false", quelle)
        self.assertIn('freigabe.value = "1"', quelle)
        self.assertContains(self.client.get(self.adresse), "js/soll_edit.js")

    def test_die_detailseite_fuehrt_hierher(self):
        self.assertContains(self.client.get(self.inv.get_absolute_url()), self.adresse)


class Eintragen(Sollpflege):
    def test_ein_wert_legt_ein_soll_von_hand_an(self):
        antwort = self.sichern(**{f"soll_{self.artikel.pk}": "12,5"})
        self.assertRedirects(antwort, self.adresse, fetch_redirect_response=False)
        soll = Sollbestand.objects.get(hausartikel=self.artikel)
        self.assertEqual((soll.menge_basiseinheit, soll.herkunft), (Decimal("12.5"), "HAND"))
        self.assertContains(self.client.get(self.adresse), "1 Sollbestände eingetragen")

    def test_leer_bleibt_ohne_soll(self):
        self.sichern(**{f"soll_{self.artikel.pk}": ""})
        self.assertFalse(Sollbestand.objects.exists())
        self.assertContains(self.client.get(self.adresse), "Nichts geändert")

    def test_keine_menge_wird_gemeldet_und_nichts_geschrieben(self):
        antwort = self.sichern(**{f"soll_{self.artikel.pk}": "viel"})
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "tabellenzeile-auffaellig")
        self.assertFalse(Sollbestand.objects.exists())

    def test_ohne_freigabe_bleibt_ein_stehendes_soll(self):
        mit = self.artikel_anlegen("Artikel A", "101000")
        self.soll(mit)
        self.sichern(**{f"soll_{mit.pk}": "99"})
        soll = Sollbestand.objects.get(hausartikel=mit)
        self.assertEqual((soll.menge_basiseinheit, soll.herkunft), (Decimal("5"), "DATEI"))

    def test_mit_freigabe_wird_es_geaendert_und_ist_von_hand(self):
        mit = self.artikel_anlegen("Artikel A", "101000")
        self.soll(mit)
        self.sichern(**{f"soll_{mit.pk}": "99", f"frei_{mit.pk}": "1"})
        soll = Sollbestand.objects.get(hausartikel=mit)
        self.assertEqual((soll.menge_basiseinheit, soll.herkunft), (Decimal("99"), "HAND"))
        self.assertContains(self.client.get(self.adresse), "1 geändert")

    def test_freigegeben_und_leer_entfernt_das_soll(self):
        mit = self.artikel_anlegen("Artikel A", "101000")
        self.soll(mit)
        self.sichern(**{f"soll_{mit.pk}": "", f"frei_{mit.pk}": "1"})
        self.assertFalse(Sollbestand.objects.filter(hausartikel=mit).exists())

    def test_nach_einem_fehler_bleibt_die_zeile_freigegeben(self):
        mit = self.artikel_anlegen("Artikel A", "101000")
        self.soll(mit)
        antwort = self.sichern(**{f"soll_{mit.pk}": "x", f"frei_{mit.pk}": "1"})
        form = antwort.context["form"]
        self.assertFalse(form.fields[f"soll_{mit.pk}"].disabled)
        self.assertContains(antwort, "tabellenzeile-auffaellig")

    def test_unveraendert_bestaetigt_wird_von_hand(self):
        """Whoever releases and saves the same value has checked it."""
        mit = self.artikel_anlegen("Artikel A", "101000")
        self.soll(mit)
        self.sichern(**{f"soll_{mit.pk}": "5", f"frei_{mit.pk}": "1"})
        self.assertEqual(Sollbestand.objects.get(hausartikel=mit).herkunft, "HAND")

    def test_ein_artikel_eines_anderen_hauses_wird_abgewiesen(self):
        fremd = self.artikel_anlegen("Artikel X", "101004", haus=self.zweites)
        with self.assertRaises(ValidationError):
            soll_von_hand_speichern(self.inv, {fremd.pk: Decimal("1")})

    def test_ein_inzwischen_inaktiver_artikel_bekommt_kein_soll(self):
        """A second window is older than the status. It must not write anything any more."""
        self.artikel.deaktivieren()
        with self.assertRaisesMessage(ValidationError, "nicht mehr aktiv"):
            soll_von_hand_speichern(self.inv, {self.artikel.pk: Decimal("1")})
        self.assertFalse(Sollbestand.objects.exists())

    def test_jahresware_bekommt_bei_der_monatsinventur_kein_soll(self):
        self.artikel.inventurart_setzen(jahresinventur())
        with self.assertRaises(ValidationError):
            soll_von_hand_speichern(self.inv, {self.artikel.pk: Decimal("1")})

    def test_ein_negatives_soll_geht_durch(self):
        """An inventory management system reports that when more was booked than delivered."""
        self.sichern(**{f"soll_{self.artikel.pk}": "-3,5"})
        self.assertEqual(Sollbestand.objects.get().menge_basiseinheit, Decimal("-3.5"))

    def test_jedes_feld_traegt_seine_beschriftung_und_die_tabelle_einen_kopf(self):
        """Without a label a screen reader would find 106 nameless fields (axe, 2026-09-23)."""
        antwort = self.client.get(self.adresse)
        self.assertContains(antwort, f'aria-label="{self.artikel.name}"')
        self.assertContains(antwort, "<thead>")

    def test_nur_in_der_vorbereitung(self):
        self.inv.zaehlung_starten()
        for antwort in (self.client.get(self.adresse), self.sichern()):
            with self.subTest(methode=antwort.request["REQUEST_METHOD"]):
                self.assertRedirects(antwort, self.inv.get_absolute_url())
        self.assertContains(self.client.get(self.inv.get_absolute_url()), "in der Vorbereitung")

    def test_ein_start_zwischen_blick_und_eintrag_sagt_es_mit_den_worten_der_seite(self):
        """Refused under the lock, as the upload is. The page still speaks of itself."""
        echt = SollPflegeForm.is_valid

        def mit_start(form):
            Inventur.objects.get(pk=self.inv.pk).zaehlung_starten()
            return echt(form)

        with mock.patch.object(SollPflegeForm, "is_valid", mit_start):
            antwort = self.sichern(**{f"soll_{self.artikel.pk}": "3"})

        self.assertRedirects(antwort, self.inv.get_absolute_url())
        texte = [str(meldung) for meldung in get_messages(antwort.wsgi_request)]
        self.assertEqual(len(texte), 1)
        self.assertIn("wird in der Vorbereitung gepflegt", texte[0])
        self.assertNotIn("dann die Datei", texte[0])
        self.assertFalse(Sollbestand.objects.exists())


class DateiUndAbschluss(Sollpflege):
    def test_die_datei_ersetzt_das_soll_von_hand_und_sagt_es(self):
        self.soll(self.artikel, herkunft=Sollherkunft.HAND)
        bericht = sollbestand_einlesen(datei("101001;7\n"), self.inv)
        self.assertEqual(bericht.von_hand_ersetzt, 1)
        soll = Sollbestand.objects.get()
        self.assertEqual((soll.menge_basiseinheit, soll.herkunft), (Decimal("7"), "DATEI"))

    def test_der_upload_meldet_die_ersetzten_werte(self):
        self.soll(self.artikel, herkunft=Sollherkunft.HAND)
        self.client.post(
            self.inv.get_absolute_url(), {"datei": datei("101001;7\n"), "format": "winticket"}
        )
        self.assertContains(self.client.get(self.inv.get_absolute_url()), "1 von Hand")

    def test_ein_soll_von_hand_zaehlt_in_der_klaerung_wie_eines_aus_der_datei(self):
        """The block "Active, but without target" names who has none. By hand counts."""
        self.assertEqual(sollklaerung(self.inv).ohne_soll, [])
        mit = self.artikel_anlegen("Artikel A", "101000")
        self.soll(mit, herkunft=Sollherkunft.HAND)
        self.assertEqual(sollklaerung(self.inv).ohne_soll, [self.artikel])
        self.soll(self.artikel, herkunft=Sollherkunft.HAND)
        self.assertEqual(sollklaerung(self.inv).ohne_soll, [])

    def test_das_soll_eines_inaktiven_artikels_verfaellt_ohne_zaehlung(self):
        """Whoever takes the article out of the range wants no difference for it (2026-09-23)."""
        self.soll(self.artikel, menge="30")
        self.inv.zaehlung_starten()
        self.artikel.deaktivieren()
        self.assertNotIn(self.artikel.pk, self.inv.wirksame_sollbestaende())
        self.inv.abschliessen()
        self.assertFalse(self.inv.ergebnisse.filter(hausartikel=self.artikel).exists())

    def test_das_soll_eines_inaktiven_artikels_gilt_wenn_er_gezaehlt_wurde(self):
        self.soll(self.artikel, menge="30")
        self.inv.zaehlung_starten()
        self.inv.zaehlpositionen.update(eingabemenge=Decimal("2"))
        self.artikel.deaktivieren()
        self.assertEqual(self.inv.wirksame_sollbestaende()[self.artikel.pk], Decimal("30"))

    def test_beim_abschluss_fragt_block_fuenf_auch_bei_soll_von_hand(self):
        mit = self.artikel_anlegen("Artikel A", "101000")
        self.soll(mit, herkunft=Sollherkunft.HAND)
        self.inv.zaehlung_starten()
        self.inv.zaehlpositionen.update(eingabemenge=Decimal("2"))
        self.assertEqual([e.hausartikel for e in klaeren(self.inv).ohne_soll], [self.artikel])

    def test_das_soll_von_hand_friert_mit_ein(self):
        self.soll(self.artikel, menge="30", herkunft=Sollherkunft.HAND)
        self.inv.zaehlung_starten()
        self.inv.zaehlpositionen.update(eingabemenge=Decimal("2"))
        self.inv.abschliessen()
        ergebnis = self.inv.ergebnisse.get(hausartikel=self.artikel)
        self.assertEqual((ergebnis.sollbestand, ergebnis.differenz), (Decimal("30"), Decimal("10")))
