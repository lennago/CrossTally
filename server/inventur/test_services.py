"""Target stock in, result out.

The target stock comes from a foreign application: the encoding changes,
the numbers are sometimes written in German and sometimes in English, and
a header row is sometimes there and sometimes not. How each of those is
read is pinned down here.
"""

from datetime import date
from decimal import Decimal
from io import BytesIO

from django.test import TestCase

from accounts.models import CustomUser
from django_project.testing import inventur_anlegen, jahresinventur, monatsinventur
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
    Warengruppe,
)
from organisation.models import Bereich, Haus, Ort

from .evaluation import Warenzeile, warengruppen_einer_inventur
from .models import Inventurnotiz, Sollbestand, Sollgrund, Sollzeile, Wiegung
from .services import (
    Ergebniszeile,
    betroffene_hausartikel,
    ergebniszeilen,
    inventuren_anlegen,
    notizen_speichern,
    sollbestand_einlesen,
)

STICHTAG = date(2026, 1, 31)


def als_datei(inhalt: str, kodierung: str = "utf-8") -> BytesIO:
    return BytesIO(inhalt.encode(kodierung))


class Grundlagen(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Ost")
        cls.ort = Ort.objects.create(
            name="Thekenfront", bereich=Bereich.objects.create(name="Theke", haus=cls.haus)
        )
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        cls.stueck = Basiseinheit.objects.get(name="Stück(e)")
        cls.ware = Ware.objects.create(name="Artikel C 0,33", basiseinheit=cls.stueck)
        cls.kasten = Gebinde.objects.create(
            ware=cls.ware, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        cls.artikel = Hausartikel.objects.create(
            haus=cls.haus,
            ware=cls.ware,
            warengruppe=cls.gruppe,
            artikelnummer="101001",
            name="Artikel C 0,33 Flasche",
        )
        cls.inventur = inventur_anlegen(cls.haus, stichtag=STICHTAG)


class SollbestandEinlesen(Grundlagen):
    def einlesen(self, inhalt: str, kodierung: str = "utf-8"):
        return sollbestand_einlesen(als_datei(inhalt, kodierung), self.inventur)

    def test_kopfzeile_wird_uebergangen(self):
        bericht = self.einlesen("Artikelnummer;Bestand\n101001;12\n")
        self.assertEqual(bericht.zeilen, 1)
        self.assertEqual(bericht.fehler, [])
        self.assertEqual(Sollbestand.objects.get().menge_basiseinheit, Decimal("12"))

    def test_komma_und_punkt_werden_beide_gelesen(self):
        """'1.234,5' is written in German, '1.5' is one and a half."""
        self.einlesen("101001;1.234,5\n")
        self.assertEqual(Sollbestand.objects.get().menge_basiseinheit, Decimal("1234.500"))

        self.einlesen("101001;1.5\n")
        self.assertEqual(Sollbestand.objects.get().menge_basiseinheit, Decimal("1.500"))

    def test_eine_kurze_nummer_wird_nicht_aufgefuellt(self):
        """A "42" and a "000042" are two numbers. WinTICKET writes them as it carries them."""
        Hausartikel.objects.create(
            haus=self.haus,
            ware=Ware.objects.create(name="Artikel W", basiseinheit=self.stueck),
            warengruppe=self.gruppe,
            artikelnummer="42",
            name="Artikel W kurz",
        )
        bericht = self.einlesen("42;7\n000042;1\n")
        self.assertEqual(bericht.zugeordnet, 1)
        self.assertEqual(Sollbestand.objects.get().hausartikel.artikelnummer, "42")
        self.assertEqual(
            list(Sollzeile.objects.values_list("artikelnummer", flat=True)), ["000042"]
        )

    def test_cp1252_wird_erkannt(self):
        bericht = self.einlesen("Artikelnummer;Menge für Zählung\n101001;3\n", "cp1252")
        self.assertEqual(bericht.zugeordnet, 1)

    def test_leere_zeilen_stoeren_nicht(self):
        bericht = self.einlesen("101001;3\n\n\n")
        self.assertEqual(bericht.zeilen, 1)
        self.assertEqual(bericht.fehler, [])

    def test_unlesbare_zeile_wird_gemeldet(self):
        bericht = self.einlesen("101001;3\n101001;keine Zahl\n")
        self.assertEqual(bericht.zugeordnet, 1)
        self.assertEqual(len(bericht.fehler), 1)
        self.assertIn("Zeile 2", bericht.fehler[0])

    def test_doppelte_nummer_wird_gemeldet_und_die_erste_gilt(self):
        """Silently taking the last would look as if read in (that is how it was until 09/2026)."""
        bericht = self.einlesen("101001;5\n101001;7\n")
        self.assertEqual(bericht.zugeordnet, 1)
        self.assertEqual(Sollbestand.objects.get().menge_basiseinheit, Decimal("5"))
        self.assertEqual(len(bericht.fehler), 1)
        self.assertIn("Zeile 2", bericht.fehler[0])
        self.assertIn("steht schon in Zeile 1", bericht.fehler[0])

    def test_nummer_eines_anderen_hauses_gilt_nicht(self):
        """The search is in the inventory's house. Otherwise it would hit the wrong row."""
        kino_sued = Haus.objects.create(name="Kino Süd")
        Hausartikel.objects.create(
            haus=kino_sued,
            ware=self.ware,
            warengruppe=self.gruppe,
            artikelnummer="200002",
            name="Artikel C Kino Süd",
        )
        bericht = self.einlesen("200002;5\n")
        self.assertEqual((bericht.zugeordnet, bericht.offen), (0, 1))
        self.assertEqual(Sollzeile.objects.get().grund, Sollgrund.UNBEKANNT)

    def test_eingestellter_artikel_wird_nicht_getroffen(self):
        """A reassigned number must not land on the historical row. It becomes a question."""
        self.artikel.einstellen()
        bericht = self.einlesen("101001;5\n")
        self.assertEqual((bericht.zugeordnet, bericht.offen), (0, 1))
        self.assertEqual(Sollbestand.objects.count(), 0)
        zeile = Sollzeile.objects.get()
        self.assertEqual((zeile.grund, zeile.hausartikel), (Sollgrund.EINGESTELLT, self.artikel))

    def test_inaktiver_artikel_wird_zur_frage(self):
        """Seasonal goods WinTICKET still carries in stock. Reactivate or not?"""
        self.artikel.deaktivieren()
        bericht = self.einlesen("101001;5\n")
        self.assertEqual((bericht.zugeordnet, bericht.offen), (0, 1))
        self.assertEqual(Sollzeile.objects.get().grund, Sollgrund.INAKTIV)

    def test_unmoegliche_mengen_sind_fehlerzeilen(self):
        """Too large or not a number: error row. SQLite would accept it and fail when reading."""
        bericht = self.einlesen("101001;7\n101001;1e10\n101001;NaN\n101001;Infinity\n")
        self.assertEqual(len(bericht.fehler), 3)
        self.assertEqual(Sollbestand.objects.get().menge_basiseinheit, Decimal("7"))

    def test_ignorierte_nummern_werden_still_uebergangen(self):
        self.artikel.ignorieren()
        bericht = self.einlesen("101001;5\n")
        self.assertEqual((bericht.nicht_zaehlbar, bericht.offen, bericht.fehler), (1, 0, []))
        self.assertEqual(Sollbestand.objects.count(), 0)
        self.assertEqual(Sollzeile.objects.count(), 0)


class Ergebniszeilen(Grundlagen):
    def test_differenz_schon_waehrend_der_zaehlung(self):
        """Without closing it is computed. Exactly that makes troubleshooting possible."""
        self.inventur.zaehlpositionen_anlegen()
        Sollbestand.objects.create(
            inventur=self.inventur, hausartikel=self.artikel, menge_basiseinheit=Decimal("50")
        )
        self.inventur.zaehlpositionen.create(
            ort=self.ort, artikel=self.artikel, gebinde=self.kasten, eingabemenge=Decimal("2")
        )

        zeile = ergebniszeilen(self.inventur)[0]
        self.assertEqual(zeile.gesamtmenge_basiseinheit, Decimal("40"))
        self.assertEqual(zeile.differenz, Decimal("-10"))
        self.assertTrue(zeile.auffaellig)
        self.assertEqual(zeile.differenz_text, "-10")
        self.assertEqual(zeile.differenz_klasse, "differenz-fehlt")

    def test_ein_ueberhang_traegt_sein_plus(self):
        """A "+10" and a "-10" are two different messages. The zero stays bare."""
        self.inventur.zaehlpositionen_anlegen()
        soll = Sollbestand.objects.create(
            inventur=self.inventur, hausartikel=self.artikel, menge_basiseinheit=Decimal("30")
        )
        self.inventur.zaehlpositionen.create(
            ort=self.ort, artikel=self.artikel, gebinde=self.kasten, eingabemenge=Decimal("2")
        )
        zeile = ergebniszeilen(self.inventur)[0]
        self.assertEqual(zeile.differenz_text, "+10")
        # More than expected is no loss. Hence yellow and not red.
        self.assertEqual(zeile.differenz_klasse, "differenz-mehr")

        soll.menge_basiseinheit = Decimal("40")
        soll.save()
        zeile = ergebniszeilen(self.inventur)[0]
        self.assertEqual(zeile.differenz_text, "0")
        self.assertEqual(zeile.differenz_klasse, "differenz-passt")

    def test_artikel_mit_soll_ohne_zaehlung_steht_mit_null_da(self):
        Sollbestand.objects.create(
            inventur=self.inventur, hausartikel=self.artikel, menge_basiseinheit=Decimal("5")
        )
        zeile = ergebniszeilen(self.inventur)[0]
        self.assertEqual(zeile.gesamtmenge_basiseinheit, Decimal("0"))
        self.assertEqual(zeile.differenz, Decimal("-5"))

    def test_ohne_soll_bleibt_die_differenz_offen(self):
        self.inventur.zaehlpositionen.create(
            ort=self.ort, artikel=self.artikel, gebinde=self.kasten, eingabemenge=Decimal("1")
        )
        zeile = ergebniszeilen(self.inventur)[0]
        self.assertIsNone(zeile.differenz)
        self.assertEqual(zeile.soll_text, "")
        self.assertFalse(zeile.auffaellig)
        # Without target nothing was compared. A colour would claim a result.
        self.assertEqual(zeile.differenz_klasse, "")

    def test_nach_dem_abschluss_kommen_die_eingefrorenen_zeilen(self):
        """A target stock corrected later changes nothing any more."""
        self.inventur.zaehlpositionen.create(
            ort=self.ort, artikel=self.artikel, gebinde=self.kasten, eingabemenge=Decimal("1")
        )
        Sollbestand.objects.create(
            inventur=self.inventur, hausartikel=self.artikel, menge_basiseinheit=Decimal("25")
        )
        self.inventur.zaehlung_starten()
        self.inventur.abschliessen()

        zeile = ergebniszeilen(self.inventur)[0]
        self.assertEqual(zeile.gesamtmenge_basiseinheit, Decimal("20"))
        self.assertEqual(zeile.differenz, Decimal("-5"))
        self.assertEqual(Sollbestand.objects.count(), 0)


class EinmalGerundet(Grundlagen):
    """Actual and target to the places of the house's unit first, the difference from those.

    Then the check list, the frozen result and the sheet say the same. Until
    10/2026 each number was rounded on its own: half a piece against a target
    of 3 read "3, 3, -1", and a difference shown as "0" still marked the row.
    """

    def setUp(self):
        self.inventur.zaehlung_starten()

    def portionen(self, anzahl: str, je_portion: str) -> None:
        """Counted over a recipe. That is where fractions of a piece come from."""
        portion = Rezepturartikel.objects.create(name="Portion R", basiseinheit=self.stueck)
        Rezepturkomponente.objects.create(
            rezepturartikel=portion, ware=self.ware, menge_in_basiseinheit=Decimal(je_portion)
        )
        self.inventur.zaehlpositionen.create(
            ort=self.ort, artikel=portion, eingabemenge=Decimal(anzahl)
        )

    def soll(self, menge: str, artikel: Hausartikel | None = None) -> None:
        Sollbestand.objects.create(
            inventur=self.inventur,
            hausartikel=artikel or self.artikel,
            menge_basiseinheit=Decimal(menge),
        )

    def in_kilogramm(self, **felder) -> Hausartikel:
        ware = Ware.objects.create(
            name="Ware K", basiseinheit=Basiseinheit.objects.get(name="kg"), **felder
        )
        return Hausartikel.objects.create(
            haus=self.haus,
            ware=ware,
            warengruppe=self.gruppe,
            artikelnummer="101002",
            name=ware.name,
        )

    def zeile(self, artikel: Hausartikel | None = None) -> Ergebniszeile:
        artikel = artikel or self.artikel
        return next(z for z in ergebniszeilen(self.inventur) if z.hausartikel == artikel)

    def eingefroren(self, artikel: Hausartikel | None = None) -> tuple:
        """Closes. Returns the frozen numbers and the row of the sheet."""
        artikel = artikel or self.artikel
        self.inventur.abschliessen()
        ergebnis = self.inventur.ergebnisse.get(hausartikel=artikel)
        blatt = warengruppen_einer_inventur(self.inventur)[0].zeilen[0]
        zahlen = (ergebnis.gesamtmenge_basiseinheit, ergebnis.sollbestand, ergebnis.differenz)
        return zahlen, (blatt.ist_text, blatt.soll_text, blatt.differenz_text)

    @staticmethod
    def angezeigt(zeile) -> tuple[str, str, str]:
        return zeile.ist_text, zeile.soll_text, zeile.differenz_text

    def test_ein_halbes_stueck_gegen_ein_ganzes_soll(self):
        """2.5 pieces against 3. The minus of half a piece rounded to -1."""
        self.portionen("5", "0.5")
        self.soll("3")

        zeile = self.zeile()
        self.assertEqual(self.angezeigt(zeile), ("3", "3", "0"))
        self.assertFalse(zeile.auffaellig)

        zahlen, blatt = self.eingefroren()
        self.assertEqual(zahlen, (Decimal("3"), Decimal("3"), Decimal("0")))
        self.assertEqual(blatt, ("3 Stk.", "3 Stk.", "0 Stk."))

    def test_einmal_gerundet_und_nicht_zweimal(self):
        """2.4996 pieces are 2 in the check list. Frozen to three places they were 2.500, then 3."""
        self.portionen("3", "0.8332")
        self.soll("2")

        self.assertEqual(self.angezeigt(self.zeile()), ("2", "2", "0"))

        zahlen, blatt = self.eingefroren()
        self.assertEqual(zahlen, (Decimal("2"), Decimal("2"), Decimal("0")))
        self.assertEqual(blatt, ("2 Stk.", "2 Stk.", "0 Stk."))

    def test_eine_auf_null_gerundete_differenz_faellt_nicht_auf(self):
        """2.4 pieces against 2 read "2, 2, 0" in green. The row was marked all the same."""
        self.portionen("6", "0.4")
        self.soll("2")

        zeile = self.zeile()
        self.assertEqual(self.angezeigt(zeile), ("2", "2", "0"))
        self.assertEqual(zeile.differenz_klasse, "differenz-passt")
        self.assertFalse(zeile.auffaellig)

    def test_gewogen_auf_das_gramm(self):
        """1234.5 g against 1.235 kg: both read 1.235, so the difference is 0, not -0.001."""
        artikel = self.in_kilogramm(abwiegbar=True)
        position = self.inventur.zaehlpositionen.create(ort=self.ort, artikel=artikel)
        Wiegung.objects.create(
            zaehlposition=position,
            brutto_gramm=Decimal("1234.5"),
            behaelter=Behaelter.ohne_behaelter(),
        )
        self.soll("1.235", artikel)

        self.assertEqual(self.angezeigt(self.zeile(artikel)), ("1,235", "1,235", "0"))

        (ist, soll, differenz), _blatt = self.eingefroren(artikel)
        self.assertEqual((ist, soll, differenz), (Decimal("1.235"), Decimal("1.235"), Decimal("0")))
        self.assertEqual(ist - soll, differenz)

    def test_auffaellig_ist_was_die_zeile_als_abweichung_zeigt(self):
        """The rule of the app, on the website and on the sheet.

        A typed difference of 0.0004 kg reads "0" and is none. Rounded to
        nothing, a minus is no "-0" either: it would read like a shortage.
        """
        artikel = self.in_kilogramm()
        faelle = (
            ("0.0004", "0", False),
            ("-0.0004", "0", False),
            ("0.0005", "+0,001", True),
            ("-0.0005", "-0,001", True),
        )
        for differenz, text, auffaellig in faelle:
            with self.subTest(differenz=differenz):
                zeile = Ergebniszeile(
                    hausartikel=artikel,
                    gesamtmenge_basiseinheit=Decimal("20.311"),
                    sollbestand=None,
                    differenz=Decimal(differenz),
                )
                self.assertEqual((zeile.differenz_text, zeile.auffaellig), (text, auffaellig))
                warenzeile = Warenzeile(ware=artikel.ware, differenz=Decimal(differenz))
                self.assertEqual(warenzeile.auffaellig, auffaellig)


class LaufAnlegen(Grundlagen):
    def test_je_haus_eine_inventur_mit_ihren_positionen(self):
        erwartung = Erwartung.objects.create(ort=self.ort, artikel=self.artikel)
        ErwartungGebinde.objects.create(erwartung=erwartung, gebinde=self.kasten)
        kino_sued = Haus.objects.create(name="Kino Süd")
        arten = [monatsinventur(), jahresinventur()]

        angelegt = inventuren_anlegen([self.haus, kino_sued], arten, date(2026, 12, 31))
        self.assertEqual([inventur.anzahl_positionen for inventur in angelegt], [1, 0])
        # Every house gets the same types, also one without articles of one of them.
        for inventur in angelegt:
            self.assertEqual(list(inventur.inventurarten.all()), arten)

    def test_wer_anlegt_zaehlt_mit(self):
        """Otherwise the manager would have to enter themselves first."""
        leitung = CustomUser.objects.create_user(
            username="anlegerin", password="geheim", email="an@example.org", is_staff=True
        )
        angelegt = inventuren_anlegen(
            [self.haus], [monatsinventur()], date(2026, 2, 28), angelegt_von=leitung
        )
        self.assertEqual(list(angelegt[0].zaehlende.all()), [leitung])

    def test_ein_admin_bekommt_keinen_eintrag(self):
        """They may count everywhere anyway. A box would say nothing about it."""
        admin = CustomUser.objects.create_superuser(
            username="ada", password="geheim", email="ada@example.org"
        )
        angelegt = inventuren_anlegen(
            [self.haus], [monatsinventur()], date(2026, 3, 31), angelegt_von=admin
        )
        self.assertEqual(angelegt[0].zaehlende.count(), 0)
        self.assertTrue(angelegt[0].darf_zaehlen(admin))


class Notizen(Grundlagen):
    """Notes are working data. They apply until closing and then migrate along."""

    def test_umfang_enthaelt_artikel_mit_offener_position(self):
        self.inventur.zaehlpositionen.create(
            ort=self.ort, artikel=self.artikel, gebinde=self.kasten
        )
        zeile = ergebniszeilen(self.inventur)[0]
        self.assertEqual(zeile.hausartikel, self.artikel)
        self.assertFalse(zeile.gezaehlt)
        self.assertEqual(zeile.gesamtmenge_basiseinheit, Decimal("0"))

    def test_umfang_loest_rezepturartikel_ueber_die_rezeptur_auf(self):
        """Even uncounted: the product behind a portion belongs on the check list."""
        portion = Rezepturartikel.objects.create(name="Portion N", basiseinheit=self.stueck)
        Rezepturkomponente.objects.create(
            rezepturartikel=portion, ware=self.ware, menge_in_basiseinheit=Decimal("1")
        )
        self.inventur.zaehlpositionen.create(ort=self.ort, artikel=portion)

        self.assertEqual(betroffene_hausartikel(self.inventur), {self.artikel.pk})

    def test_notiz_allein_bringt_eine_zeile_hervor(self):
        Inventurnotiz.objects.create(
            inventur=self.inventur, hausartikel=self.artikel, text="ausgelistet"
        )
        zeile = ergebniszeilen(self.inventur)[0]
        self.assertEqual(zeile.notiz, "ausgelistet")

    def test_notiz_ueberlebt_den_abschluss(self):
        """Even without count and without target. Otherwise it would silently get lost."""
        Inventurnotiz.objects.create(
            inventur=self.inventur, hausartikel=self.artikel, text="Regal abgebaut"
        )
        self.inventur.zaehlung_starten()
        self.assertEqual(self.inventur.abschliessen(), 1)

        ergebnis = self.inventur.ergebnisse.get()
        self.assertEqual(ergebnis.notiz, "Regal abgebaut")
        self.assertEqual(ergebnis.gesamtmenge_basiseinheit, Decimal("0"))
        self.assertEqual(Inventurnotiz.objects.count(), 0)

    def test_speichern_legt_an_aendert_und_loescht(self):
        notizen_speichern(self.inventur, {self.artikel.pk: "erst so"})
        self.assertEqual(Inventurnotiz.objects.get().text, "erst so")

        notizen_speichern(self.inventur, {self.artikel.pk: "dann so"})
        self.assertEqual(Inventurnotiz.objects.get().text, "dann so")

        notizen_speichern(self.inventur, {self.artikel.pk: ""})
        self.assertEqual(Inventurnotiz.objects.count(), 0)

    def test_artikel_eines_anderen_hauses_wird_verworfen(self):
        kino_sued = Haus.objects.create(name="Kino Süd")
        fremd = Hausartikel.objects.create(
            haus=kino_sued,
            ware=self.ware,
            warengruppe=self.gruppe,
            artikelnummer="200002",
            name="Artikel C Kino Süd",
        )
        self.assertEqual(notizen_speichern(self.inventur, {fremd.pk: "geht nicht"}).anzahl, 0)
        self.assertEqual(Inventurnotiz.objects.count(), 0)
