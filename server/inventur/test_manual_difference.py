"""The difference by hand: typed in per article where no target stock came in.

Some houses see the difference only in their inventory management system,
once the counted quantities are in there. Until 10/2026 it went into the
notes and stayed text. Checked here is the switch and its condition, that
the typed difference counts at closing and in the check list instead of a
computed one, and that it waits while switched off.
"""

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from django_project.testing import AngemeldeteSeiten, inventur_anlegen
from katalog.models import (
    Basiseinheit,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Ware,
    Warenfamilie,
    Warengruppe,
)
from organisation.models import Bereich, Haus, Ort

from .evaluation import familien_eines_laufs
from .models import Inventur, Inventurstatus, Sollbestand, Sollgrund, Sollzeile
from .services import ergebniszeilen, vermerk_aendern
from .test_views import gesehen

STICHTAG = date(2026, 1, 31)


class DifferenzVonHand(AngemeldeteSeiten):
    """Kino Ost counts Artikel C in crates of 20. Ware N is carried but not expected."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Ost")
        cls.ort = Ort.objects.create(
            name="Thekenfront", bereich=Bereich.objects.create(name="Theke", haus=cls.haus)
        )
        stueck = Basiseinheit.objects.get(name="Stück(e)")
        gruppe = Warengruppe.objects.create(name="Warengruppe G")
        ware = Ware.objects.create(name="Artikel C 0,33", basiseinheit=stueck)
        kasten = Gebinde.objects.create(
            ware=ware, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )
        cls.artikel = Hausartikel.objects.create(
            haus=cls.haus,
            ware=ware,
            warengruppe=gruppe,
            artikelnummer="101001",
            name="Artikel C 0,33 Flasche",
        )
        erwartung = Erwartung.objects.create(ort=cls.ort, artikel=cls.artikel)
        ErwartungGebinde.objects.create(erwartung=erwartung, gebinde=kasten)
        cls.anderer = Hausartikel.objects.create(
            haus=cls.haus,
            ware=Ware.objects.create(name="Ware N", basiseinheit=stueck),
            warengruppe=gruppe,
            artikelnummer="101002",
            name="Ware N",
        )

    def inventur(self, status=Inventurstatus.ZAEHLUNG, von_hand=True) -> Inventur:
        """Two crates of Artikel C counted, the difference by hand switched on."""
        inventur = inventur_anlegen(self.haus, stichtag=STICHTAG)
        inventur.zaehlpositionen_anlegen()
        inventur.zaehlpositionen.update(eingabemenge=Decimal("2"))
        if von_hand:
            inventur.differenz_von_hand_setzen(True)
        if status != Inventurstatus.VORBEREITUNG:
            inventur.zaehlung_starten()
        return inventur


class DerSchalter(DifferenzVonHand):
    def test_ohne_sollbestand_geht_er_an_und_aus(self):
        inventur = self.inventur(von_hand=False)

        inventur.differenz_von_hand_setzen(True)
        self.assertTrue(Inventur.objects.get(pk=inventur.pk).differenz_von_hand)
        inventur.differenz_von_hand_setzen(False)
        self.assertFalse(Inventur.objects.get(pk=inventur.pk).differenz_von_hand)

    def test_mit_sollbestand_bleibt_er_aus(self):
        """With a target the difference computes itself. Two would contradict each other."""
        inventur = self.inventur(von_hand=False)
        Sollbestand.objects.create(
            inventur=inventur, hausartikel=self.artikel, menge_basiseinheit=Decimal("30")
        )
        with self.assertRaises(ValidationError):
            inventur.differenz_von_hand_setzen(True)

    def test_eine_offene_sollzeile_zaehlt_als_sollbestand(self):
        inventur = self.inventur(von_hand=False)
        Sollzeile.objects.create(
            inventur=inventur,
            artikelnummer="101999",
            menge_basiseinheit=Decimal("4"),
            grund=Sollgrund.UNBEKANNT,
        )
        with self.assertRaises(ValidationError):
            inventur.differenz_von_hand_setzen(True)

    def test_eine_abgeschlossene_aendert_sich_nicht(self):
        inventur = self.inventur()
        inventur.abschliessen()
        with self.assertRaises(ValidationError):
            inventur.differenz_von_hand_setzen(False)

    def test_die_seite_bietet_ihn_nur_ohne_sollbestand_an(self):
        inventur = self.inventur(von_hand=False)
        adresse = reverse("inventur_differenz_von_hand", args=[inventur.pk])
        self.assertContains(self.client.get(inventur.get_absolute_url()), adresse)

        Sollbestand.objects.create(
            inventur=inventur, hausartikel=self.artikel, menge_basiseinheit=Decimal("30")
        )
        self.assertNotContains(self.client.get(inventur.get_absolute_url()), adresse)

    def test_ueber_die_seite(self):
        inventur = self.inventur(von_hand=False)
        adresse = reverse("inventur_differenz_von_hand", args=[inventur.pk])

        antwort = self.client.post(adresse, {"an": "1"}, follow=True)

        self.assertContains(antwort, "von Hand eingetragen")
        self.assertTrue(Inventur.objects.get(pk=inventur.pk).differenz_von_hand)
        self.client.post(adresse, {"an": "0"})
        self.assertFalse(Inventur.objects.get(pk=inventur.pk).differenz_von_hand)

    def test_auch_ueber_der_pruefliste(self):
        """Switched there as in the counting app, and back to the list afterwards."""
        inventur = self.inventur(von_hand=False)
        pruefliste = reverse("inventur_pruefen", args=[inventur.pk])
        schalter = reverse("inventur_differenz_von_hand", args=[inventur.pk])

        antwort = self.client.get(pruefliste)
        self.assertContains(antwort, f'action="{schalter}"')
        self.assertContains(antwort, f'name="weiter" value="{pruefliste}"')
        self.assertContains(antwort, "Differenz von Hand eintragen")

        antwort = self.client.post(schalter, {"an": "1", "weiter": pruefliste})
        self.assertRedirects(antwort, pruefliste)
        self.assertTrue(Inventur.objects.get(pk=inventur.pk).differenz_von_hand)
        antwort = self.client.get(pruefliste)
        self.assertContains(antwort, "Differenz von Hand ausschalten")
        self.assertContains(antwort, f'name="differenz_{self.artikel.pk}"')

    def test_ueber_der_pruefliste_nur_ohne_sollbestand(self):
        inventur = self.inventur(von_hand=False)
        Sollbestand.objects.create(
            inventur=inventur, hausartikel=self.artikel, menge_basiseinheit=Decimal("30")
        )

        antwort = self.client.get(reverse("inventur_pruefen", args=[inventur.pk]))

        self.assertNotContains(antwort, reverse("inventur_differenz_von_hand", args=[inventur.pk]))

    def test_mit_sollbestand_sagt_die_seite_warum_nicht(self):
        inventur = self.inventur(von_hand=False)
        Sollbestand.objects.create(
            inventur=inventur, hausartikel=self.artikel, menge_basiseinheit=Decimal("30")
        )

        antwort = self.client.post(
            reverse("inventur_differenz_von_hand", args=[inventur.pk]), {"an": "1"}, follow=True
        )

        self.assertContains(antwort, "rechnet sich die Differenz selbst")
        self.assertFalse(Inventur.objects.get(pk=inventur.pk).differenz_von_hand)


class KeinSollbestandSolangeEsAnIst(DifferenzVonHand):
    def test_kein_upload(self):
        inventur = self.inventur(status=Inventurstatus.VORBEREITUNG)
        datei = SimpleUploadedFile("soll.csv", b"101001;30\n", content_type="text/csv")

        antwort = self.client.post(
            inventur.get_absolute_url(), {"datei": datei, "format": "winticket"}, follow=True
        )

        self.assertContains(antwort, "Ein Sollbestand kommt erst")
        self.assertEqual(inventur.sollbestaende.count(), 0)

    def test_keine_pflege_von_hand(self):
        inventur = self.inventur(status=Inventurstatus.VORBEREITUNG)

        antwort = self.client.get(reverse("inventur_soll_pflegen", args=[inventur.pk]))

        self.assertRedirects(antwort, inventur.get_absolute_url())


class DieEingetrageneDifferenz(DifferenzVonHand):
    def test_sie_gilt_beim_abschluss_und_das_soll_bleibt_leer(self):
        inventur = self.inventur()
        vermerk_aendern(inventur, self.artikel.pk, differenz=Decimal("-3"))

        inventur.abschliessen()

        ergebnis = inventur.ergebnisse.get()
        self.assertEqual(ergebnis.gesamtmenge_basiseinheit, Decimal("40"))
        self.assertIsNone(ergebnis.sollbestand)
        self.assertEqual(ergebnis.differenz, Decimal("-3"))

    def test_ein_ungezaehlter_artikel_mit_differenz_wird_eingefroren(self):
        """Nothing found, but the system books some: exactly the row that interests."""
        inventur = self.inventur()
        vermerk_aendern(inventur, self.anderer.pk, differenz=Decimal("-12"))

        inventur.abschliessen()

        ergebnis = inventur.ergebnisse.get(hausartikel=self.anderer)
        self.assertEqual(ergebnis.gesamtmenge_basiseinheit, Decimal("0"))
        self.assertEqual(ergebnis.differenz, Decimal("-12"))

    def test_eingefroren_wie_die_pruefliste_sie_zeigt(self):
        """5.5 typed reads "+6" in the row and on the sheet, so 6 is frozen.

        The field keeps what was typed ('differenz_eingabe'). -0.4 reads "0"
        and is no deviation: until 10/2026 the row was marked and read "-0".
        """
        inventur = self.inventur()
        vermerk_aendern(inventur, self.artikel.pk, differenz=Decimal("5.5"))
        vermerk_aendern(inventur, self.anderer.pk, differenz=Decimal("-0.4"))

        zeilen = {zeile.hausartikel.pk: zeile for zeile in ergebniszeilen(inventur)}
        self.assertEqual(zeilen[self.artikel.pk].differenz_text, "+6")
        self.assertEqual(zeilen[self.artikel.pk].differenz_eingabe, "5,5")
        self.assertEqual(zeilen[self.anderer.pk].differenz_text, "0")
        self.assertFalse(zeilen[self.anderer.pk].auffaellig)

        inventur.abschliessen()

        self.assertEqual(
            dict(inventur.ergebnisse.values_list("hausartikel", "differenz")),
            {self.artikel.pk: Decimal("6"), self.anderer.pk: Decimal("0")},
        )

    def test_die_summe_der_familie_wie_der_abschluss_sie_einfriert(self):
        """Three rows of -0.4 pieces read "0" each, so their family reads 0, before and after.

        Until 10/2026 the check list and the run summed the typed values:
        -1.2 pieces made "-1 Stk." at the family, and after closing it was 0.
        """
        stueck = Basiseinheit.objects.get(name="Stück(e)")
        familie = Warenfamilie.objects.create(name="Warenfamilie R", leiteinheit=stueck)
        inventur = self.inventur()
        for nummer in ("101101", "101102", "101103"):
            artikel = Hausartikel.objects.create(
                haus=self.haus,
                ware=Ware.objects.create(
                    name=f"Ware {nummer}", basiseinheit=stueck, warenfamilie=familie
                ),
                warengruppe=Warengruppe.objects.get(name="Warengruppe G"),
                artikelnummer=nummer,
                name=f"Ware {nummer}",
            )
            vermerk_aendern(inventur, artikel.pk, differenz=Decimal("-0.4"))

        def summen():
            bloecke = familien_eines_laufs([Inventur.objects.get(pk=inventur.pk)])
            (lauf,) = [block for block in bloecke if block.familie == familie]
            return lauf.differenz_gesamt, lauf.differenz_klasse

        seite = self.client.get(
            reverse("inventur_pruefen", args=[inventur.pk]), {"gliederung": "familie"}
        )
        (block,) = [block for block in seite.context["gruppen"] if block.familie == familie]
        self.assertEqual((block.differenz, block.differenz_klasse), ("0 Stk.", "differenz-passt"))
        self.assertEqual(summen(), ("0 Stk.", "differenz-passt"))

        inventur.abschliessen()

        self.assertEqual(summen(), ("0 Stk.", "differenz-passt"))

    def test_ohne_eintrag_bleibt_die_differenz_leer(self):
        inventur = self.inventur()
        inventur.abschliessen()
        self.assertIsNone(inventur.ergebnisse.get().differenz)

    def test_ausgeschaltet_wartet_sie(self):
        """Switched off, it stays stored and counts again once switched on."""
        inventur = self.inventur()
        vermerk_aendern(inventur, self.anderer.pk, differenz=Decimal("-12"))
        inventur.differenz_von_hand_setzen(False)

        self.assertNotIn(self.anderer.pk, inventur.vermerke())
        zeilen = {zeile.hausartikel.pk: zeile for zeile in ergebniszeilen(inventur)}
        self.assertNotIn(self.anderer.pk, zeilen)

        inventur.differenz_von_hand_setzen(True)
        zeilen = {zeile.hausartikel.pk: zeile for zeile in ergebniszeilen(inventur)}
        self.assertEqual(zeilen[self.anderer.pk].differenz, Decimal("-12"))

    def test_die_pruefliste_rechnet_mit_ihr(self):
        inventur = self.inventur()
        vermerk_aendern(inventur, self.artikel.pk, differenz=Decimal("5.5"))

        zeile = ergebniszeilen(inventur)[0]

        self.assertEqual(zeile.differenz, Decimal("5.5"))
        self.assertIsNone(zeile.sollbestand)
        self.assertTrue(zeile.auffaellig)


class VermerkAendern(DifferenzVonHand):
    """The path of the counting app: one field at a time, the rest stays."""

    def test_nur_was_kommt_aendert_sich(self):
        inventur = self.inventur()
        vermerk_aendern(inventur, self.artikel.pk, text="zwei Kästen hinten")

        vermerk_aendern(inventur, self.artikel.pk, differenz=Decimal("-2"))

        vermerk = inventur.notizen.get()
        self.assertEqual((vermerk.text, vermerk.differenz), ("zwei Kästen hinten", Decimal("-2")))

    def test_was_nichts_mehr_traegt_geht(self):
        inventur = self.inventur()
        vermerk_aendern(inventur, self.artikel.pk, text="weg damit", differenz=Decimal("1"))

        self.assertIsNone(vermerk_aendern(inventur, self.artikel.pk, text="", differenz=None))

        self.assertEqual(inventur.notizen.count(), 0)

    def test_eine_differenz_nur_bei_differenz_von_hand(self):
        inventur = self.inventur(von_hand=False)
        with self.assertRaises(ValidationError):
            vermerk_aendern(inventur, self.artikel.pk, differenz=Decimal("1"))
        # Emptying is always allowed.
        vermerk_aendern(inventur, self.artikel.pk, differenz=None)

    def test_ein_fremder_artikel_wird_abgewiesen(self):
        inventur = self.inventur()
        fremd = Hausartikel.objects.create(
            haus=Haus.objects.create(name="Kino Süd"),
            ware=self.artikel.ware,
            warengruppe=self.artikel.warengruppe,
            artikelnummer="301001",
            name="Artikel C Kino Süd",
        )
        with self.assertRaises(ValidationError):
            vermerk_aendern(inventur, fremd.pk, text="geht nicht")


class DiePruefliste(DifferenzVonHand):
    def absenden(self, inventur, **felder):
        return self.client.post(reverse("inventur_pruefen", args=[inventur.pk]), felder)

    def felder(self, differenz, vorher=""):
        return {
            f"differenz_{self.artikel.pk}": differenz,
            f"differenz_vorher_{self.artikel.pk}": vorher,
        }

    def test_die_differenz_ist_ein_feld_und_das_soll_fehlt(self):
        inventur = self.inventur()
        vermerk_aendern(inventur, self.artikel.pk, differenz=Decimal("-3.5"))

        antwort = self.client.get(reverse("inventur_pruefen", args=[inventur.pk]))

        self.assertContains(antwort, f'name="differenz_{self.artikel.pk}" value="-3,5"')
        self.assertNotContains(antwort, "sollstattist_")

    def test_mit_komma_gespeichert(self):
        inventur = self.inventur()

        self.absenden(inventur, **self.felder("-3,5"))

        self.assertEqual(inventur.notizen.get().differenz, Decimal("-3.5"))

    def test_eine_aeltere_seite_ueberschreibt_sie_nicht(self):
        """The field shows nothing and nobody touched it: the app's difference stays."""
        inventur = self.inventur()
        vermerk_aendern(inventur, self.artikel.pk, differenz=Decimal("7"))

        self.absenden(inventur, **self.felder(""))

        self.assertEqual(inventur.notizen.get().differenz, Decimal("7"))

    def test_keine_zahl_wird_gemeldet_und_nicht_gespeichert(self):
        inventur = self.inventur()

        antwort = self.client.post(
            reverse("inventur_pruefen", args=[inventur.pk]), self.felder("drei"), follow=True
        )

        self.assertContains(antwort, "keine Zahl")
        self.assertContains(antwort, self.artikel.name)
        self.assertEqual(inventur.notizen.count(), 0)

    def test_nebenbei_kommt_die_meldung_als_antwort(self):
        inventur = self.inventur()

        antwort = self.client.post(
            reverse("inventur_pruefen", args=[inventur.pk]),
            self.felder("1,2345"),
            headers={"x-requested-with": "fetch"},
        )

        self.assertEqual(antwort.status_code, 400)
        self.assertIn("keine Zahl", antwort.content.decode())

    def test_keine_zahl_haelt_den_abschluss_auf(self):
        inventur = self.inventur()

        self.absenden(inventur, abschliessen="1", **gesehen(inventur), **self.felder("x"))

        self.assertEqual(Inventur.objects.get(pk=inventur.pk).status, Inventurstatus.ZAEHLUNG)

    def test_abschluss_von_der_seite_friert_sie_ein(self):
        inventur = self.inventur()

        antwort = self.absenden(
            inventur, abschliessen="1", **gesehen(inventur), **self.felder("-4")
        )

        self.assertRedirects(antwort, inventur.get_absolute_url())
        self.assertEqual(inventur.ergebnisse.get().differenz, Decimal("-4"))
