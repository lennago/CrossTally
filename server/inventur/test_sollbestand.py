"""The clarification of the target stock: what the file leaves open, and what it lacks.

The file from WinTICKET is more than a list of numbers. It says what still
exists in the house. Checked is that nothing silently vanishes: every row
that finds no place becomes a question, and every answer does exactly what
its button promises.
"""

import html
import re
from datetime import date
from decimal import Decimal
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.http import QueryDict
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from django_project.testing import inventur_anlegen, jahresinventur, monatsinventur
from katalog.models import (
    Artikelstatus,
    Basiseinheit,
    Einheitenkennung,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Ware,
    Warengruppe,
)

from .models import (
    Inventurstatus,
    Sollbestand,
    Sollgrund,
    Sollherkunft,
    Sollzeile,
    Zaehlposition,
)
from .services import (
    ergebniszeilen,
    ohne_soll_ausscheiden,
    sollbestand_einlesen,
    sollklaerung,
    sollzeile_aktivieren,
    sollzeile_dazurechnen,
    sollzeilen_erneut_zuordnen,
)
from .test_views import InventurSeiten, datei


class Sollgrundlagen(InventurSeiten):
    def einlesen(self, inventur, inhalt: str):
        return sollbestand_einlesen(datei(inhalt), inventur)

    def zweiter_artikel(self, name="Artikel F", nummer="101002", **abweichend) -> Hausartikel:
        ware = Ware.objects.create(name=name, basiseinheit=self.stueck)
        return Hausartikel.objects.create(
            haus=self.haus,
            ware=ware,
            warengruppe=Warengruppe.objects.get(),
            artikelnummer=nummer,
            name=name,
            **abweichend,
        )

    def dublette(self, ziel=None, **abweichend) -> Hausartikel:
        """The same product under a second number, discontinued and counting as 'ziel'."""
        ziel = ziel or self.artikel
        angaben = {
            "haus": self.haus,
            "ware": ziel.ware,
            "warengruppe": Warengruppe.objects.get(),
            "artikelnummer": "101009",
            "name": "Artikel C Dose",
            "status": Artikelstatus.EINGESTELLT,
            "zaehlt_als": ziel,
        }
        return Hausartikel.objects.create(**(angaben | abweichend))


class DateiEinlesen(Sollgrundlagen):
    def test_bezeichnung_darf_in_beiden_reihenfolgen_stehen(self):
        """Compeso does not name the order. The number is the quantity."""
        inv = self.inventur()
        self.einlesen(inv, "101001;Artikel C Flasche;42,5\n")
        soll = Sollbestand.objects.get()
        self.assertEqual(
            (soll.menge_basiseinheit, soll.name_in_datei), (Decimal("42.5"), "Artikel C Flasche")
        )

        self.einlesen(inv, "101001;42,5;Artikel C Flasche\n")
        soll = Sollbestand.objects.get()
        self.assertEqual(
            (soll.menge_basiseinheit, soll.name_in_datei), (Decimal("42.5"), "Artikel C Flasche")
        )

    def test_viele_doppelte_nummern_werden_gezaehlt_statt_genannt(self):
        """Like every refused row of a file: the first hundred in words, the rest counted."""
        bericht = self.einlesen(self.inventur(), "101001;1\n" * 152)

        self.assertEqual(len(bericht.fehler), 101)
        self.assertIn("151 Zeilen der Datei wurden übergangen", bericht.fehler[0])
        self.assertIn("Zeile 2: Artikelnummer 101001 steht schon in Zeile 1.", bericht.fehler[1])
        self.assertEqual(Sollbestand.objects.get().menge_basiseinheit, Decimal("1"))

    def test_eine_menge_die_erst_gerundet_zu_gross_wird(self):
        """1000000000.000 after rounding is more than the column. Until 10/2026 it went in."""
        bericht = self.einlesen(self.inventur(), "Artikelnummer;Menge\n101001;999999999,9995\n")

        self.assertFalse(Sollbestand.objects.exists())
        self.assertEqual(bericht.fehler, ["Zeile 2: „101001;999999999,9995“ ist keine Menge."])

    def test_die_letzte_datei_gilt(self):
        """What it no longer names drops away: target stocks as well as open rows."""
        inv = self.inventur()
        fassbrause = self.zweiter_artikel()
        self.einlesen(inv, "101001;5\n101002;7\n999999;1\n")
        self.assertEqual(Sollbestand.objects.count(), 2)
        self.assertEqual(Sollzeile.objects.count(), 1)

        self.einlesen(inv, "101002;9\n")
        self.assertEqual(
            list(Sollbestand.objects.values_list("hausartikel_id", flat=True)), [fassbrause.pk]
        )
        self.assertEqual(Sollzeile.objects.count(), 0)

    def test_jahresware_ist_bei_der_monatsinventur_nicht_dran(self):
        """It is stored nonetheless. It only has a say once someone adds it."""
        self.artikel.inventurart_setzen(jahresinventur())
        inv = self.inventur()
        bericht = self.einlesen(inv, "101001;5\n")
        self.assertEqual((bericht.zugeordnet, bericht.offen, bericht.nicht_dran), (0, 0, 1))
        self.assertEqual(Sollbestand.objects.get().menge_basiseinheit, Decimal("5"))
        self.assertEqual(inv.wirksame_sollbestaende(), {})
        self.assertEqual(inv.sollbestaende_im_umfang().count(), 0)

        jahr = inventur_anlegen(
            self.haus, monatsinventur(), jahresinventur(), stichtag=date(2026, 12, 31)
        )
        self.assertEqual(self.einlesen(jahr, "101001;5\n").zugeordnet, 1)

    def test_inaktive_jahresware_ist_auch_keine_frage(self):
        self.artikel.inventurart_setzen(jahresinventur())
        self.artikel.deaktivieren()
        bericht = self.einlesen(self.inventur(), "101001;5\n")
        self.assertEqual((bericht.offen, bericht.nicht_dran), (0, 1))
        self.assertEqual(Sollbestand.objects.count(), 1)

    def test_mehrere_eingestellte_ohne_bezeichnung_sind_unbekannt(self):
        """The number carries its history. Without a designation it cannot be said who is meant.

        Then it counts as unknown: create or discard, but no guessing.
        """
        self.artikel.einstellen()
        self.zweiter_artikel("Artikel C alt", "101001", status=Artikelstatus.EINGESTELLT)
        self.einlesen(self.inventur(), "101001;5\n")
        zeile = Sollzeile.objects.get()
        self.assertEqual((zeile.grund, zeile.hausartikel), (Sollgrund.UNBEKANNT, None))

    def test_die_bezeichnung_entscheidet_zwischen_eingestellten(self):
        self.artikel.einstellen()
        alt = self.zweiter_artikel("Artikel C alt", "101001", status=Artikelstatus.EINGESTELLT)
        self.einlesen(self.inventur(), "101001;5;artikel c ALT\n")
        zeile = Sollzeile.objects.get()
        self.assertEqual((zeile.grund, zeile.hausartikel), (Sollgrund.EINGESTELLT, alt))

    def test_inaktiver_mit_bestand_null_ist_keine_frage(self):
        """The system lists the article, but with nothing on stock. The house agrees.

        Stored silently as a target of 0: no open row, no question on the
        clarification page, and the preparation does not count it either.
        """
        self.artikel.deaktivieren()
        inv = self.inventur()
        bericht = self.einlesen(inv, "101001;0\n")
        self.assertEqual((bericht.offen, bericht.nicht_aktiv_leer), (0, 1))
        self.assertFalse(Sollzeile.objects.exists())
        soll = Sollbestand.objects.get()
        self.assertEqual((soll.hausartikel, soll.menge_basiseinheit), (self.artikel, Decimal("0")))
        self.assertTrue(sollklaerung(inv).leer)
        self.assertEqual(inv.sollbestaende_im_umfang().count(), 0)

    def test_eingestellter_mit_bestand_null_ist_keine_frage(self):
        self.artikel.einstellen()
        inv = self.inventur()
        bericht = self.einlesen(inv, "101001;0,000;Artikel C von früher\n")
        self.assertEqual((bericht.offen, bericht.nicht_aktiv_leer), (0, 1))
        self.assertFalse(Sollzeile.objects.exists())
        self.assertEqual(Sollbestand.objects.get().hausartikel, self.artikel)
        # The deviating designation is no hint either: the article is out of the range.
        self.assertTrue(sollklaerung(inv).leer)

    def test_inaktiver_mit_bestand_bleibt_eine_frage(self):
        self.artikel.deaktivieren()
        bericht = self.einlesen(self.inventur(), "101001;0,5\n")
        self.assertEqual((bericht.offen, bericht.nicht_aktiv_leer), (1, 0))
        self.assertEqual(Sollzeile.objects.get().grund, Sollgrund.INAKTIV)


class Dubletten(Sollgrundlagen):
    """A discontinued duplicate adds its stock to the row it counts as.

    The inventory management system may still book on both numbers. Counted
    is one thing, so the target is the sum.
    """

    def test_beide_nummern_ergeben_die_summe(self):
        self.dublette()
        bericht = self.einlesen(self.inventur(), "101001;10\n101009;4\n")
        self.assertEqual(Sollbestand.objects.get(hausartikel=self.artikel).menge_basiseinheit, 14)
        self.assertFalse(Sollzeile.objects.exists())
        self.assertEqual((bericht.zugeordnet, bericht.zugerechnet, bericht.offen), (1, 1, 0))

    def test_die_reihenfolge_in_der_datei_ist_gleich(self):
        self.dublette()
        self.einlesen(self.inventur(), "101009;4\n101001;10\n")
        self.assertEqual(Sollbestand.objects.get().menge_basiseinheit, 14)

    def test_nur_die_dublette_in_der_datei(self):
        self.dublette()
        self.einlesen(self.inventur(), "101009;4\n")
        self.assertEqual(Sollbestand.objects.get(hausartikel=self.artikel).menge_basiseinheit, 4)

    def test_nur_die_dublette_in_der_datei_traegt_deren_bezeichnung(self):
        """A reassigned number has no line of its own. Until 10/2026 its target named no row."""
        self.dublette()
        self.einlesen(self.inventur(), "101009;4;Artikel C Dose\n")
        soll = Sollbestand.objects.get(hausartikel=self.artikel)
        self.assertEqual(
            (soll.herkunft, soll.name_in_datei), (Sollherkunft.DATEI, "Artikel C Dose")
        )

    def test_die_eigene_zeile_behaelt_ihre_bezeichnung(self):
        self.dublette()
        self.einlesen(self.inventur(), "101009;4;Artikel C Dose\n101001;10;Artikel C\n")
        self.assertEqual(Sollbestand.objects.get().name_in_datei, "Artikel C")

    def test_in_der_einheit_des_bleibenden(self):
        gramm = Basiseinheit.objects.get(kennung=Einheitenkennung.GRAMM)
        kilo = Basiseinheit.objects.get(kennung=Einheitenkennung.KILOGRAMM)
        ware = Ware.objects.create(name="Ware N", basiseinheit=gramm)
        lose = self.zweiter_artikel("Ware N lose", "101005")
        Hausartikel.objects.filter(pk=lose.pk).update(ware=ware, basiseinheit=gramm)
        lose.refresh_from_db()
        self.dublette(lose, name="Ware N Sack", basiseinheit=kilo)
        self.einlesen(self.inventur(), "101005;500\n101009;2\n")
        self.assertEqual(Sollbestand.objects.get(hausartikel=lose).menge_basiseinheit, 2500)

    def test_eine_summe_ueber_der_spalte_bleibt_draussen(self):
        """A duplicate in kg with 5000000 makes 5000000000 g. Until 10/2026 the error page."""
        lose = self.lose_mit_dublette_in_kilo()

        bericht = self.einlesen(self.inventur(), "101005;500\n101009;5000000\n")

        self.assertEqual(Sollbestand.objects.get(hausartikel=lose).menge_basiseinheit, 500)
        self.assertIn(
            "101005 „Ware N lose“: Mit den Dubletten wären es 5000000500 g, mehr als ein "
            "Sollbestand fasst. Ihr Bestand bleibt draußen.",
            bericht.fehler,
        )

    def test_ohne_eigene_zeile_entsteht_dann_kein_soll(self):
        lose = self.lose_mit_dublette_in_kilo()

        self.einlesen(self.inventur(), "101009;5000000\n")

        self.assertFalse(Sollbestand.objects.filter(hausartikel=lose).exists())

    def lose_mit_dublette_in_kilo(self) -> Hausartikel:
        """Ware N carried in g, its discontinued duplicate in kg counts as it."""
        gramm = Basiseinheit.objects.get(kennung=Einheitenkennung.GRAMM)
        kilo = Basiseinheit.objects.get(kennung=Einheitenkennung.KILOGRAMM)
        ware = Ware.objects.create(name="Ware N", basiseinheit=gramm)
        lose = self.zweiter_artikel("Ware N lose", "101005")
        Hausartikel.objects.filter(pk=lose.pk).update(ware=ware, basiseinheit=gramm)
        lose.refresh_from_db()
        self.dublette(lose, name="Ware N Sack", basiseinheit=kilo)
        return lose

    def test_ohne_verweis_bleibt_es_eine_frage(self):
        self.dublette(zaehlt_als=None)
        self.einlesen(self.inventur(), "101009;4\n")
        self.assertEqual(Sollzeile.objects.get().grund, Sollgrund.EINGESTELLT)

    def test_eine_kette_endet_bei_der_gefuehrten_zeile(self):
        """Merged twice before 10/2026: the duplicate points at a row that counts as a third."""
        mitte = self.dublette(artikelnummer="101008", name="Artikel C Becher")
        self.dublette(mitte)

        bericht = self.einlesen(self.inventur(), "101001;10\n101009;4\n")

        self.assertEqual(Sollbestand.objects.get(hausartikel=self.artikel).menge_basiseinheit, 14)
        self.assertEqual((bericht.zugerechnet, bericht.offen), (1, 0))

    def test_eine_kette_in_eine_fremde_ware_bleibt_eine_frage(self):
        """Into another product the units would not convert. The stock stays a question."""
        fremd = self.zweiter_artikel()
        self.dublette(fremd, ware=self.artikel.ware)

        bericht = self.einlesen(self.inventur(), "101009;4\n")

        self.assertEqual(Sollzeile.objects.get().grund, Sollgrund.EINGESTELLT)
        self.assertEqual(bericht.zugerechnet, 0)

    def test_ein_kreis_bleibt_eine_frage(self):
        mitte = self.dublette(artikelnummer="101008", name="Artikel C Becher")
        ende = self.dublette(mitte)
        Hausartikel.objects.filter(pk=mitte.pk).update(zaehlt_als=ende)

        self.einlesen(self.inventur(), "101009;4\n")

        self.assertEqual(Sollzeile.objects.get().grund, Sollgrund.EINGESTELLT)

    def test_ein_inaktiver_bleibender_bekommt_nichts_zugerechnet(self):
        """Its own number is a question then, and a target beside it would answer it."""
        self.dublette()
        self.artikel.deaktivieren()
        self.einlesen(self.inventur(), "101009;4\n")
        self.assertEqual(Sollzeile.objects.get().grund, Sollgrund.EINGESTELLT)
        self.assertFalse(Sollbestand.objects.exists())

    def test_erneut_zuordnen_rechnet_zum_vorhandenen_soll(self):
        """The row keeps the target it already has, the duplicate's stock comes on top.

        Until 10/2026 matching again overwrote it: the row ended up with the
        duplicate's stock alone.
        """
        self.dublette()
        self.artikel.deaktivieren()
        inv = self.inventur()
        self.einlesen(inv, "101001;10;Artikel C\n101009;4\n")
        sollzeile_aktivieren(Sollzeile.objects.get(artikelnummer="101001"))

        bericht = sollzeilen_erneut_zuordnen(inv)

        soll = Sollbestand.objects.get(hausartikel=self.artikel)
        self.assertEqual((soll.menge_basiseinheit, soll.name_in_datei), (14, "Artikel C"))
        self.assertEqual((bericht.zugerechnet, bericht.offen), (1, 0))

    def test_viele_unlesbare_zeilen_werden_gekuerzt_gemeldet(self):
        """Ten by name, the rest as a number: the page must not drown in hints."""
        inv = self.inventur()
        # The first line may be a header and is not reported: twelve remain.
        unlesbar = "\n".join(f"10100{i};keine Zahl" for i in range(13)) + "\n"
        antwort = self.client.post(
            inv.get_absolute_url(), {"datei": datei(unlesbar), "format": "winticket"}, follow=True
        )
        self.assertContains(antwort, "… und 2 weitere unlesbare Zeilen.")
        texte = [str(m) for m in antwort.context["messages"]]
        self.assertEqual(sum("keine Zahl" in text or "10100" in text for text in texte), 10)

    def test_die_seite_meldet_es(self):
        self.dublette()
        inv = self.inventur()
        antwort = self.client.post(
            inv.get_absolute_url(),
            {"datei": datei("101009;4\n"), "format": "winticket"},
            follow=True,
        )
        self.assertContains(antwort, "eingestellten Dubletten")


class Dazurechnen(Sollgrundlagen):
    """A discontinued number whose product the house now carries actively under another one.

    The system gave the product a new number and still books on the old one.
    The clarification names the active article, and the answer adds the target
    there and marks the old row as counting as it, so the next file asks no more.
    """

    def alte_nummer(self, inhalt="101001;10\n101009;4;Artikel C Dose\n"):
        """The old number without the mark of a duplicate, and an upload that names it."""
        alt = self.dublette(zaehlt_als=None)
        inventur = self.inventur()
        self.einlesen(inventur, inhalt)
        return inventur, alt

    def test_die_klaerung_nennt_den_aktiven_artikel(self):
        inventur, _alt = self.alte_nummer()
        zeile = Sollzeile.objects.get()
        self.assertEqual(zeile.grund, Sollgrund.EINGESTELLT)
        self.assertEqual(
            sollklaerung(inventur).eingestellte_mit_nachfolger, [(zeile, self.artikel)]
        )

    def test_das_soll_kommt_dazu_und_die_alte_nummer_zaehlt_als_der_aktive(self):
        inventur, alt = self.alte_nummer()

        ziel = sollzeile_dazurechnen(Sollzeile.objects.get())

        self.assertEqual(ziel, self.artikel)
        soll = Sollbestand.objects.get(inventur=inventur, hausartikel=self.artikel)
        self.assertEqual(soll.menge_basiseinheit, 14)
        self.assertFalse(Sollzeile.objects.exists())
        alt.refresh_from_db()
        self.assertEqual((alt.status, alt.zaehlt_als), (Artikelstatus.EINGESTELLT, self.artikel))

        # The next file asks no more.
        bericht = self.einlesen(inventur, "101001;10\n101009;4\n")
        self.assertEqual((bericht.zugerechnet, bericht.offen), (1, 0))
        self.assertEqual(Sollbestand.objects.get().menge_basiseinheit, 14)

    def test_ohne_eigene_zeile_entsteht_sein_soll(self):
        """The file names only the old number. The target says what stood there."""
        self.alte_nummer("101009;4;Artikel C Dose\n")

        sollzeile_dazurechnen(Sollzeile.objects.get())

        soll = Sollbestand.objects.get(hausartikel=self.artikel)
        self.assertEqual(
            (soll.menge_basiseinheit, soll.name_in_datei, soll.herkunft),
            (4, "Artikel C Dose", Sollherkunft.DATEI),
        )

    def test_in_der_einheit_des_aktiven(self):
        gramm = Basiseinheit.objects.get(kennung=Einheitenkennung.GRAMM)
        kilo = Basiseinheit.objects.get(kennung=Einheitenkennung.KILOGRAMM)
        ware = Ware.objects.create(name="Ware N", basiseinheit=gramm)
        lose = self.zweiter_artikel("Ware N lose", "101005")
        Hausartikel.objects.filter(pk=lose.pk).update(ware=ware, basiseinheit=gramm)
        lose.refresh_from_db()
        self.dublette(lose, name="Ware N Sack", basiseinheit=kilo, zaehlt_als=None)
        self.einlesen(self.inventur(), "101005;500\n101009;2,5\n")

        sollzeile_dazurechnen(Sollzeile.objects.get())

        self.assertEqual(Sollbestand.objects.get(hausartikel=lose).menge_basiseinheit, 3000)

    def test_eine_summe_ueber_der_spalte_bleibt_offen(self):
        gramm = Basiseinheit.objects.get(kennung=Einheitenkennung.GRAMM)
        kilo = Basiseinheit.objects.get(kennung=Einheitenkennung.KILOGRAMM)
        ware = Ware.objects.create(name="Ware N", basiseinheit=gramm)
        lose = self.zweiter_artikel("Ware N lose", "101005")
        Hausartikel.objects.filter(pk=lose.pk).update(ware=ware, basiseinheit=gramm)
        lose.refresh_from_db()
        self.dublette(lose, name="Ware N Sack", basiseinheit=kilo, zaehlt_als=None)
        self.einlesen(self.inventur(), "101005;500\n101009;5000000\n")
        zeile = Sollzeile.objects.get()

        with self.assertRaisesMessage(
            ValidationError, "Zusammen wären es 5000000500 g, mehr als ein Sollbestand fasst."
        ):
            sollzeile_dazurechnen(zeile)

        self.assertTrue(Sollzeile.objects.filter(pk=zeile.pk).exists())
        self.assertEqual(Sollbestand.objects.get(hausartikel=lose).menge_basiseinheit, 500)

    def test_ohne_aktiven_artikel_bleibt_die_zeile(self):
        """Inactive, its own number would be the question. The old one waits for that."""
        inventur, alt = self.alte_nummer()
        self.artikel.deaktivieren()
        zeile = Sollzeile.objects.get()

        self.assertEqual(sollklaerung(inventur).nachfolger, {})
        with self.assertRaises(ValidationError):
            sollzeile_dazurechnen(zeile)
        self.assertTrue(Sollzeile.objects.filter(pk=zeile.pk).exists())
        alt.refresh_from_db()
        self.assertIsNone(alt.zaehlt_als)

    def test_zurueckgeholt_ist_er_nicht_sein_eigener_nachfolger(self):
        inventur, alt = self.alte_nummer()
        self.artikel.einstellen()
        alt.aktivieren()

        self.assertEqual(sollklaerung(inventur).nachfolger, {})
        with self.assertRaises(ValidationError):
            sollzeile_dazurechnen(Sollzeile.objects.get())

    def test_wer_als_die_alte_nummer_zaehlte_zaehlt_als_der_aktive(self):
        """As after a merge. The upload would follow the chain, the article's page would not."""
        _inventur, alt = self.alte_nummer()
        noch_aelter = self.dublette(alt, artikelnummer="101008", name="Artikel C Becher")

        sollzeile_dazurechnen(Sollzeile.objects.get())

        noch_aelter.refresh_from_db()
        self.assertEqual(noch_aelter.zaehlt_als, self.artikel)

    def test_der_aktive_steht_solange_nicht_unter_ohne_soll(self):
        """The answer up there gives it a target. "Set inactive" beside it would contradict it."""
        inventur, _alt = self.alte_nummer("101009;4\n999999;1\n")
        self.assertEqual(sollklaerung(inventur).ohne_soll, [])

        Sollzeile.objects.filter(artikelnummer="101009").delete()

        self.assertEqual(sollklaerung(inventur).ohne_soll, [self.artikel])

    def test_ueber_die_seite(self):
        inventur, _alt = self.alte_nummer()
        adresse = reverse("inventur_sollbestand", args=[inventur.pk])
        seite = self.client.get(adresse)
        self.assertContains(seite, "Dieselbe Ware führt das Haus jetzt aktiv unter 101001")
        self.assertContains(seite, "Bei 101001 dazurechnen")
        # Bringing the old one back would fail on the product.
        self.assertNotContains(seite, 'value="aktivieren"')

        antwort = self.client.post(
            adresse, {"aktion": "dazurechnen", "zeile": Sollzeile.objects.get().pk}, follow=True
        )

        self.assertContains(antwort, "Das Soll von 101009 ist bei 101001")
        self.assertEqual(Sollbestand.objects.get(hausartikel=self.artikel).menge_basiseinheit, 14)


class InEinemZug(Sollgrundlagen):
    """The rows are collected and written together, not one by one.

    A count sheet names several hundred numbers. Row by row that was over 4000
    queries, each a round trip to the database (until 09/2026).
    """

    def abfragen(self, inhalt: str) -> int:
        inventur = self.inventur()
        with CaptureQueriesContext(connection) as gezaehlt:
            self.einlesen(inventur, inhalt)
        inventur.delete()
        return len(gezaehlt)

    def test_die_abfragen_wachsen_nicht_mit_den_zeilen(self):
        wenige = "101001;40\n" + "".join(f"1019{n:02};1\n" for n in range(3))
        viele = "101001;40\n" + "".join(f"1019{n:02};1\n" for n in range(60))

        self.assertEqual(self.abfragen(wenige), self.abfragen(viele))

    def test_erneut_zuordnen_ersetzt_ein_soll_von_hand(self):
        """The row that already exists takes the file's values, as row by row before."""
        neu = self.zweiter_artikel(status=Artikelstatus.INAKTIV)
        inventur = self.inventur()
        self.einlesen(inventur, "101002;7;Artikel F\n")
        Sollbestand.objects.create(
            inventur=inventur,
            hausartikel=neu,
            menge_basiseinheit=Decimal("3"),
            herkunft=Sollherkunft.HAND,
        )
        neu.aktivieren()

        bericht = sollzeilen_erneut_zuordnen(inventur)

        self.assertEqual((bericht.zugeordnet, bericht.offen), (1, 0))
        soll = Sollbestand.objects.get(inventur=inventur, hausartikel=neu)
        self.assertEqual(soll.menge_basiseinheit, Decimal("7"))
        self.assertEqual(soll.herkunft, Sollherkunft.DATEI)
        self.assertEqual(soll.name_in_datei, "Artikel F")


class WirksamesSoll(Sollgrundlagen):
    """Everything is stored. Only what is due or was added has a say."""

    def setUp(self):
        super().setUp()
        self.becher = self.zweiter_artikel("Artikel M", "101002")
        self.becher.inventurart_setzen(jahresinventur())
        self.inv = self.inventur()
        self.einlesen(self.inv, "101001;40\n101002;12\n")
        self.inv.zaehlung_starten()

    def dazuzaehlen(self, artikel, menge="3", **abweichend):
        return Zaehlposition.objects.create(
            inventur=self.inv,
            ort=self.ort,
            artikel=artikel,
            gebinde=self.kasten if artikel == self.artikel else None,
            eingabemenge=Decimal(menge),
            unerwartet=True,
            **abweichend,
        )

    def zeilen(self) -> dict[str, tuple]:
        return {
            zeile.hausartikel.artikelnummer: (zeile.sollbestand, zeile.differenz)
            for zeile in ergebniszeilen(self.inv)
        }

    def noch_einmal_einlesen(self, inhalt: str) -> None:
        """Another file, the way the page allows it: back into preparation and out again."""
        self.inv.zurueck_in_vorbereitung()
        self.einlesen(self.inv, inhalt)
        self.inv.zaehlung_starten()

    def test_ungezaehlte_jahresware_steht_nicht_in_der_pruefliste(self):
        self.assertEqual(Sollbestand.objects.count(), 2)
        self.assertEqual(list(self.zeilen()), ["101001"])

    def test_ungezaehlte_jahresware_friert_nicht_ein(self):
        self.inv.abschliessen()
        self.assertFalse(self.inv.ergebnisse.filter(hausartikel=self.becher).exists())

    def test_dazugezaehlte_jahresware_bekommt_ihr_soll(self):
        self.dazuzaehlen(self.becher, "10")
        self.assertEqual(self.zeilen()["101002"], (Decimal("12"), Decimal("-2")))
        self.inv.abschliessen()
        ergebnis = self.inv.ergebnisse.get(hausartikel=self.becher)
        self.assertEqual((ergebnis.sollbestand, ergebnis.differenz), (Decimal("12"), Decimal("-2")))

    def test_leere_und_geloeschte_zeilen_wecken_das_soll_nicht(self):
        zeile = self.dazuzaehlen(self.becher)
        Zaehlposition.objects.filter(pk=zeile.pk).update(eingabemenge=None)
        self.assertNotIn(self.becher.pk, self.inv.wirksame_sollbestaende())
        Zaehlposition.objects.filter(pk=zeile.pk).update(eingabemenge=Decimal("1"), aktiv=False)
        self.assertNotIn(self.becher.pk, self.inv.wirksame_sollbestaende())

    def test_wechselt_der_artikel_die_art_redet_sein_soll_mit(self):
        self.becher.inventurart_setzen(monatsinventur())
        self.assertEqual(self.inv.wirksame_sollbestaende()[self.becher.pk], Decimal("12"))

    def test_gezaehlter_inaktiver_nimmt_das_soll_seiner_offenen_zeile(self):
        """The question "activate?" still lies. The quantity from the file applies nonetheless."""
        saft = self.zweiter_artikel("Artikel A", "101003", status=Artikelstatus.INAKTIV)
        self.noch_einmal_einlesen("101003;6\n")
        self.assertEqual(self.inv.wirksame_sollbestaende(), {})
        self.dazuzaehlen(saft, "4")
        self.assertEqual(self.inv.wirksame_sollbestaende(), {saft.pk: Decimal("6")})

    def test_gezaehlter_inaktiver_mit_null_hat_sein_soll(self):
        """Silently stored, but not lost: counted after all, the 0 is the target."""
        saft = self.zweiter_artikel("Artikel A", "101003", status=Artikelstatus.INAKTIV)
        self.noch_einmal_einlesen("101003;0\n")
        self.assertEqual(self.inv.wirksame_sollbestaende(), {})
        self.assertEqual(list(self.zeilen()), ["101001"])
        self.dazuzaehlen(saft, "4")
        self.assertEqual(self.zeilen()["101003"], (Decimal("0"), Decimal("4")))

    def test_vorbereitung_zaehlt_nur_was_dran_ist(self):
        antwort = self.client.get(reverse("inventur_detail", args=[self.inv.pk]))
        self.assertEqual(antwort.context["anzahl_soll"], 1)


class WasGeklaertWird(Sollgrundlagen):
    def test_ohne_soll_erst_wenn_eine_datei_da_war(self):
        inv = self.inventur()
        self.assertEqual(sollklaerung(inv).ohne_soll, [])
        self.einlesen(inv, "999999;1\n")
        self.assertEqual(sollklaerung(inv).ohne_soll, [self.artikel])

    def test_jahresware_fehlt_bei_der_monatsinventur_nicht(self):
        inv = self.inventur()
        gluehwein = self.zweiter_artikel("Artikel G", "101003")
        gluehwein.inventurart_setzen(jahresinventur())
        self.einlesen(inv, "101001;5\n")
        self.assertEqual(sollklaerung(inv).ohne_soll, [])

    def test_abweichende_bezeichnung_faellt_auf(self):
        inv = self.inventur()
        self.einlesen(inv, "101001;5;Artikel C Zero 0,33\n")
        klaerung = sollklaerung(inv)
        self.assertEqual([s.hausartikel for s in klaerung.abweichende_namen], [self.artikel])
        # Upper and lower case is no deviation.
        self.einlesen(inv, "101001;5;ARTIKEL C 0,33 FLASCHE\n")
        self.assertEqual(sollklaerung(inv).abweichende_namen, [])


class Antworten(Sollgrundlagen):
    def test_aktivieren_uebernimmt_das_soll_und_legt_die_positionen_an(self):
        self.artikel.deaktivieren()
        inv = self.inventur()
        self.assertEqual(inv.zaehlpositionen.count(), 0)
        self.einlesen(inv, "101001;5\n")

        angelegt = sollzeile_aktivieren(Sollzeile.objects.get())

        self.artikel.refresh_from_db()
        self.assertTrue(self.artikel.aktiv)
        self.assertEqual(Sollbestand.objects.get().menge_basiseinheit, Decimal("5"))
        self.assertEqual((angelegt, inv.zaehlpositionen.count()), (1, 1))
        self.assertFalse(Sollzeile.objects.exists())

    def inaktiv_gesetzt_dann_berichtigt(self, inv):
        """'Set inactive' after a file without Artikel C, then the corrected file with it."""
        self.einlesen(inv, "101002;5\n")
        ohne_soll_ausscheiden(inv, self.artikel, Artikelstatus.INAKTIV)
        self.assertFalse(inv.zaehlpositionen.get(artikel=self.artikel).aktiv)
        self.einlesen(inv, "101001;5\n101002;5\n")

    def test_aktivieren_holt_die_markierten_zeilen_zurueck(self):
        """A new row cannot arise beside a marked one. Until 10/2026 the article had none."""
        self.zweiter_artikel()
        inv = self.inventur()
        vorher = inv.zaehlpositionen.get(artikel=self.artikel).pk
        self.inaktiv_gesetzt_dann_berichtigt(inv)

        angelegt = sollzeile_aktivieren(Sollzeile.objects.get(artikelnummer="101001"))

        zeile = inv.zaehlpositionen.get(artikel=self.artikel)
        self.assertEqual((angelegt, zeile.pk, zeile.aktiv), (1, vorher, True))

    def test_erneut_zuordnen_holt_die_markierten_zeilen_zurueck(self):
        """Activated on the article's page, then "Match again"."""
        self.zweiter_artikel()
        inv = self.inventur()
        self.inaktiv_gesetzt_dann_berichtigt(inv)
        Hausartikel.objects.get(pk=self.artikel.pk).aktivieren()

        sollzeilen_erneut_zuordnen(inv)

        self.assertTrue(inv.zaehlpositionen.get(artikel=self.artikel).aktiv)
        self.assertEqual(Sollbestand.objects.get(hausartikel=self.artikel).menge_basiseinheit, 5)

    def test_eine_in_der_app_entfernte_zeile_bleibt_entfernt(self):
        """Only the rows of the article that comes back, not another one a counter removed."""
        anderer = self.zweiter_artikel()
        karton = Gebinde.objects.create(
            ware=anderer.ware, name="Karton", faktor_in_basiseinheit=Decimal("10")
        )
        ErwartungGebinde.objects.create(
            erwartung=Erwartung.objects.create(ort=self.ort, artikel=anderer), gebinde=karton
        )
        inv = self.inventur()
        entfernt = inv.zaehlpositionen.get(artikel=anderer)
        entfernt.aktiv = False
        entfernt.save(update_fields=["aktiv", "zuletzt_geaendert"])
        self.inaktiv_gesetzt_dann_berichtigt(inv)

        angelegt = sollzeile_aktivieren(Sollzeile.objects.get(artikelnummer="101001"))

        self.assertEqual(angelegt, 1)
        entfernt.refresh_from_db()
        self.assertFalse(entfernt.aktiv)

    def test_aktivieren_zaehlt_nur_neue_zeilen(self):
        """The message names what arose, not every row of the inventory."""
        anderer = self.zweiter_artikel()
        karton = Gebinde.objects.create(
            ware=anderer.ware, name="Karton", faktor_in_basiseinheit=Decimal("10")
        )
        ErwartungGebinde.objects.create(
            erwartung=Erwartung.objects.create(ort=self.ort, artikel=anderer), gebinde=karton
        )
        self.artikel.deaktivieren()
        inv = self.inventur()
        self.assertEqual(inv.zaehlpositionen.count(), 1)
        self.einlesen(inv, "101001;5\n101002;5\n")

        angelegt = sollzeile_aktivieren(Sollzeile.objects.get(artikelnummer="101001"))

        self.assertEqual((angelegt, inv.zaehlpositionen.count()), (1, 2))

    def test_aktivieren_scheitert_wenn_die_ware_belegt_ist(self):
        """The row stays. The message says who has the slot."""
        alt = self.zweiter_artikel("Artikel C alt", "101009", status=Artikelstatus.EINGESTELLT)
        Hausartikel.objects.filter(pk=alt.pk).update(ware=self.ware)
        inv = self.inventur()
        self.einlesen(inv, "101009;5\n")
        zeile = Sollzeile.objects.get()
        self.assertEqual(zeile.grund, Sollgrund.EINGESTELLT)

        with self.assertRaises(ValidationError):
            sollzeile_aktivieren(zeile)
        self.assertTrue(Sollzeile.objects.filter(pk=zeile.pk).exists())
        self.assertFalse(Sollbestand.objects.exists())

    def test_erneut_zuordnen_nach_dem_anlegen(self):
        """The file does not have to be uploaded again."""
        inv = self.inventur()
        self.einlesen(inv, "101001;5\n101002;7\n")
        self.assertEqual(Sollzeile.objects.get().grund, Sollgrund.UNBEKANNT)
        fassbrause = self.zweiter_artikel()
        Erwartung.objects.create(ort=self.ort, artikel=fassbrause)

        bericht = sollzeilen_erneut_zuordnen(inv)

        self.assertEqual((bericht.zugeordnet, bericht.offen), (1, 0))
        self.assertEqual(
            Sollbestand.objects.get(hausartikel=fassbrause).menge_basiseinheit, Decimal("7")
        )
        self.assertFalse(Sollzeile.objects.exists())
        # The expectation without pack default counts too: nothing to create, nothing to report.
        self.assertEqual(inv.zaehlpositionen.filter(artikel=fassbrause).count(), 0)

    def test_ohne_soll_einstellen_nimmt_die_leeren_positionen_mit(self):
        """Marked as deleted, not deleted: a device may hold the row since a count before."""
        inv = self.inventur()
        self.einlesen(inv, "999999;1\n")
        position = inv.zaehlpositionen.get()
        vorher = position.zuletzt_geaendert

        entfernt = ohne_soll_ausscheiden(inv, self.artikel, Artikelstatus.EINGESTELLT)

        self.artikel.refresh_from_db()
        position.refresh_from_db()
        self.assertEqual(self.artikel.status, Artikelstatus.EINGESTELLT)
        self.assertEqual((entfernt, inv.zaehlpositionen.filter(aktiv=True).count()), (1, 0))
        # The sync of the app sees the row by its timestamp and takes it out.
        self.assertFalse(position.aktiv)
        self.assertGreater(position.zuletzt_geaendert, vorher)

    def test_gezaehlte_positionen_bleiben_beim_inaktiv_setzen(self):
        """Counted is counted. After a way back out of the count there can be some."""
        inv = self.inventur()
        inv.zaehlpositionen.update(eingabemenge=Decimal("2"))
        self.einlesen(inv, "999999;1\n")

        geloescht = ohne_soll_ausscheiden(inv, self.artikel, Artikelstatus.INAKTIV)

        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.status, Artikelstatus.INAKTIV)
        self.assertEqual((geloescht, inv.zaehlpositionen.count()), (0, 1))


class UeberDieSeite(Sollgrundlagen):
    def hochladen(self, inventur, inhalt, **kwargs):
        return self.client.post(
            inventur.get_absolute_url(), {"datei": datei(inhalt), "format": "winticket"}, **kwargs
        )

    def klaerung(self, inventur):
        return reverse("inventur_sollbestand", args=[inventur.pk])

    def test_hochladen_nur_in_der_vorbereitung(self):
        inv = self.inventur(status=Inventurstatus.ZAEHLUNG)
        antwort = self.hochladen(inv, "101001;5\n", follow=True)
        self.assertContains(antwort, "Zurück in die Vorbereitung")
        self.assertFalse(Sollbestand.objects.exists())

    def test_offene_zeilen_fuehren_zur_klaerung(self):
        inv = self.inventur()
        antwort = self.hochladen(inv, "101001;5\n999999;1;Neues Zeug\n")
        self.assertRedirects(antwort, self.klaerung(inv))
        seite = self.client.get(self.klaerung(inv))
        self.assertContains(seite, "999999")
        self.assertContains(seite, "Neues Zeug")
        # The path to creating brings number and designation along.
        self.assertContains(seite, "artikelnummer=999999")
        self.assertContains(seite, "name=Neues%20Zeug")

    def test_ohne_offene_fragen_zurueck_zur_inventur(self):
        inv = self.inventur()
        antwort = self.hochladen(inv, "101001;5\n")
        self.assertRedirects(antwort, inv.get_absolute_url())

    def test_erneut_zuordnen_steht_auch_ohne_unbekannte_nummer_da(self):
        """After creating the article for a reassigned number the button has to be there.

        Until 10/2026 it stood only in the block "Number unknown".
        """
        self.zweiter_artikel("Artikel S", "101003").einstellen()
        inv = self.inventur()
        self.hochladen(inv, "101003;2\n")

        seite = self.client.get(self.klaerung(inv))

        self.assertContains(seite, "Eingestellt, aber mit Sollbestand")
        # The heading of the block, not the help text, which names every block.
        self.assertNotContains(seite, "Nummer unbekannt: anlegen?")
        self.assertContains(seite, 'value="erneut"')

    def test_erneut_zuordnen_findet_den_neu_angelegten_artikel(self):
        """The reassigned number: the new article takes the target, the row is gone."""
        alt = self.zweiter_artikel("Artikel S", "101003")
        alt.einstellen()
        inv = self.inventur()
        self.hochladen(inv, "101003;2\n")
        neu = self.zweiter_artikel("Artikel T", "101003")

        self.client.post(self.klaerung(inv), {"aktion": "erneut"})

        self.assertFalse(inv.sollzeilen.exists())
        self.assertEqual(inv.sollbestaende.get().hausartikel, neu)

    def test_eine_kaputte_arbeitsmappe_wird_am_feld_gemeldet(self):
        """Not a server error: the sentence written for it (until 10/2026 a 500)."""
        inv = self.inventur()
        kaputt = SimpleUploadedFile("soll.xlsx", b"kein zip, keine arbeitsmappe")

        antwort = self.client.post(inv.get_absolute_url(), {"datei": kaputt, "format": "winticket"})

        self.assertContains(antwort, "keine Excel-Arbeitsmappe oder beschädigt")
        self.assertFalse(Sollbestand.objects.exists())

    def test_das_anlegeformular_ist_vorbelegt(self):
        antwort = self.client.get(
            reverse("hausartikel_add", args=[self.haus.pk]),
            {"artikelnummer": "999999", "name": "Neues Zeug"},
        )
        self.assertEqual(
            antwort.context["form"].initial,
            {"artikelnummer": "999999", "name": "Neues Zeug"},
        )

    def anlegen(self, weiter: str):
        """The creation form as "Create new" opens it, sent off with a new product."""
        daten = {
            "artikelnummer": "999999",
            "name": "Neues Zeug",
            "ware": "",
            "neuer_ware": "Neues Zeug",
            "basiseinheit": self.stueck.pk,
            "inventurart": monatsinventur().pk,
            "warengruppe": Warengruppe.objects.get().pk,
            "neue_warengruppe": "",
            "ean": "",
            "weiter": weiter,
        }
        for prefix, anzahl in (("gebinde", "1"), ("orte", "0")):
            daten |= {
                f"{prefix}-TOTAL_FORMS": anzahl,
                f"{prefix}-INITIAL_FORMS": "0",
                f"{prefix}-MIN_NUM_FORMS": "0",
                f"{prefix}-MAX_NUM_FORMS": "1000",
            }
        return self.client.post(reverse("hausartikel_add", args=[self.haus.pk]), daten)

    def test_neu_anlegen_fuehrt_zurueck_zur_klaerung(self):
        """Back to "Re-assign" after creating (until 10/2026 the page of the new article)."""
        inv = self.inventur()
        self.hochladen(inv, "999999;1;Neues Zeug\n")
        seite = self.client.get(self.klaerung(inv))
        self.assertContains(seite, f"weiter={self.klaerung(inv)}")
        formular = self.client.get(
            reverse("hausartikel_add", args=[self.haus.pk]), {"weiter": self.klaerung(inv)}
        )
        self.assertContains(formular, f'name="weiter" value="{self.klaerung(inv)}"')

        antwort = self.anlegen(self.klaerung(inv))

        self.assertRedirects(antwort, self.klaerung(inv))
        self.assertTrue(Hausartikel.objects.filter(artikelnummer="999999").exists())

    def test_die_nummer_kommt_ganz_im_anlegeformular_an(self):
        """A number with '+', '&' or '#' from the file. Until 10/2026 it arrived cut off."""
        inv = self.inventur()
        Sollzeile.objects.create(
            inventur=inv,
            artikelnummer="101+1&2#3",
            menge_basiseinheit=Decimal("1"),
            name="Neues Zeug",
            grund=Sollgrund.UNBEKANNT,
        )

        seite = self.client.get(self.klaerung(inv)).content.decode()

        anlegen = reverse("hausartikel_add", args=[self.haus.pk])
        (link,) = re.findall(rf'href="({re.escape(anlegen)}[^"]*)"', seite)
        adresse = urlsplit(html.unescape(link))
        self.assertEqual(adresse.fragment, "")
        self.assertEqual(
            QueryDict(adresse.query).dict() | {"weiter": ""},
            {"artikelnummer": "101+1&2#3", "name": "Neues Zeug", "weiter": ""},
        )

    def test_ein_fremdes_ziel_gilt_nicht(self):
        fremd = "https://example.com/"
        formular = self.client.get(
            reverse("hausartikel_add", args=[self.haus.pk]), {"weiter": fremd}
        )
        self.assertNotContains(formular, fremd)

        antwort = self.anlegen(fremd)

        artikel = Hausartikel.objects.get(artikelnummer="999999")
        self.assertRedirects(antwort, artikel.get_absolute_url())

    def test_die_detailseite_nennt_die_offenen_fragen(self):
        inv = self.inventur()
        self.hochladen(inv, "999999;1\n")
        antwort = self.client.get(inv.get_absolute_url())
        # The unknown number and the article without target.
        self.assertContains(antwort, "2 offene Frage(n)")

    def test_aktivieren_ueber_die_seite(self):
        self.artikel.deaktivieren()
        inv = self.inventur()
        self.hochladen(inv, "101001;5\n")
        zeile = Sollzeile.objects.get()

        antwort = self.client.post(
            self.klaerung(inv), {"aktion": "aktivieren", "zeile": zeile.pk}, follow=True
        )

        self.assertContains(antwort, "ist wieder aktiv")
        self.artikel.refresh_from_db()
        self.assertTrue(self.artikel.aktiv)
        self.assertEqual(inv.zaehlpositionen.count(), 1)

    def test_verwerfen_und_einstellen_ueber_die_seite(self):
        inv = self.inventur()
        self.hochladen(inv, "999999;1\n")
        zeile = Sollzeile.objects.get()

        self.client.post(self.klaerung(inv), {"aktion": "verwerfen", "zeile": zeile.pk})
        self.assertFalse(Sollzeile.objects.exists())

        antwort = self.client.post(
            self.klaerung(inv), {"aktion": "einstellen", "artikel": self.artikel.pk}, follow=True
        )
        self.assertContains(antwort, "ist eingestellt")
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.status, Artikelstatus.EINGESTELLT)
        self.assertEqual(inv.zaehlpositionen.filter(aktiv=True).count(), 0)

    def test_ein_block_laesst_sich_auf_einmal_verwerfen(self):
        """Only the reason on the button. The other blocks stay."""
        self.artikel.deaktivieren()
        inv = self.inventur()
        self.hochladen(inv, "101001;5\n999998;1\n999999;2\n")
        self.assertEqual(Sollzeile.objects.count(), 3)

        antwort = self.client.post(
            self.klaerung(inv), {"aktion": "alle_verwerfen", "grund": "UNBEKANNT"}, follow=True
        )

        self.assertContains(antwort, "2 Zeile(n) verworfen")
        self.assertEqual(
            list(Sollzeile.objects.values_list("grund", flat=True)), [Sollgrund.INAKTIV]
        )
        self.artikel.refresh_from_db()
        self.assertFalse(self.artikel.aktiv)

    def test_alle_offenen_zeilen_verwerfen(self):
        self.artikel.deaktivieren()
        inv = self.inventur()
        andere = self.inventur(haus=self.zweites)
        self.hochladen(inv, "101001;5\n999999;2\n")
        self.hochladen(andere, "999999;2\n")

        self.client.post(self.klaerung(inv), {"aktion": "alle_verwerfen"})

        # The other inventory stays untouched.
        self.assertEqual(list(Sollzeile.objects.values_list("inventur", flat=True)), [andere.pk])
        antwort = self.client.get(self.klaerung(inv))
        self.assertNotContains(antwort, "Alle offenen Zeilen verwerfen")

    def test_ein_erfundener_grund_verwirft_nichts(self):
        inv = self.inventur()
        self.hochladen(inv, "999999;2\n")
        antwort = self.client.post(
            self.klaerung(inv), {"aktion": "alle_verwerfen", "grund": "ALLES"}
        )
        self.assertEqual(antwort.status_code, 404)
        self.assertEqual(Sollzeile.objects.count(), 1)

    def test_fremde_zeile_und_fremder_artikel_gehen_nicht(self):
        inv = self.inventur()
        andere = self.inventur(haus=self.zweites)
        self.hochladen(inv, "999999;1\n")
        zeile = Sollzeile.objects.get()
        antwort = self.client.post(
            self.klaerung(andere), {"aktion": "verwerfen", "zeile": zeile.pk}
        )
        self.assertEqual(antwort.status_code, 404)
        self.assertTrue(Sollzeile.objects.exists())

    def test_eine_zeile_ohne_artikel_laesst_sich_nicht_aktivieren(self):
        """A second window or a stale page. The row stays, the page says why."""
        inv = self.inventur()
        self.hochladen(inv, "999999;1\n")
        zeile = Sollzeile.objects.get()

        antwort = self.client.post(
            self.klaerung(inv), {"aktion": "aktivieren", "zeile": zeile.pk}, follow=True
        )

        self.assertContains(
            antwort, "Zu dieser Zeile gibt es keinen Artikel, der sich aktivieren ließe."
        )
        self.assertTrue(Sollzeile.objects.filter(pk=zeile.pk).exists())

    def test_eine_nummer_die_keine_ist_findet_nichts(self):
        """Only a crafted form sends it. Until 10/2026 it was a server error."""
        inv = self.inventur()
        self.hochladen(inv, "999999;1\n")
        for daten in (
            {"aktion": "verwerfen", "zeile": "abc"},
            {"aktion": "aktivieren"},
            {"aktion": "einstellen", "artikel": "1 OR 1"},
        ):
            with self.subTest(daten=daten):
                self.assertEqual(self.client.post(self.klaerung(inv), daten).status_code, 404)
        self.assertTrue(Sollzeile.objects.exists())

    def test_klaerung_nur_in_der_vorbereitung(self):
        inv = self.inventur(status=Inventurstatus.ZAEHLUNG)
        antwort = self.client.get(self.klaerung(inv))
        self.assertRedirects(antwort, inv.get_absolute_url())

    def test_die_klaerungsseite_rendert_jeden_block(self):
        """One block per question. Each has to render on its own."""
        inaktiv = self.zweiter_artikel("Artikel F", "101002")
        inaktiv.deaktivieren()
        eingestellt = self.zweiter_artikel("Artikel S", "101003")
        eingestellt.einstellen()
        inv = self.inventur()
        self.hochladen(inv, "101001;5;Artikel C anders\n101002;3\n101003;2\n999999;1;Neu\n")
        seite = self.client.get(self.klaerung(inv))
        for text in (
            "Inaktiv, aber mit Sollbestand",
            "Eingestellt, aber mit Sollbestand",
            "Nummer unbekannt",
            "Bezeichnung weicht ab",
            "Erneut zuordnen",
        ):
            with self.subTest(block=text):
                self.assertContains(seite, text)
