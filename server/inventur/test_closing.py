"""What is asked before closing and what the answers cause.

Checked above all are two things: that **without a tick nothing** happens
to the master data, and that freezing comes first and applying second.
Otherwise the result would deviate from what was in the check list.
"""

from decimal import Decimal
from unittest import mock
from urllib.parse import urlencode

from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.urls import reverse

from django_project.testing import jahresinventur, monatsinventur
from katalog.models import (
    Artikelstatus,
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
from organisation.models import Bereich, Ort

from .closing import (
    DEM_ARTIKEL,
    DEM_HAUS,
    EINSTELLEN,
    INAKTIV_SETZEN,
    NEUE_WERTE,
    SOLL_EIGENER_WERT,
    SOLL_NULL,
    WECHSEL_PRAEFIX,
    Entscheidungen,
    abschliessen,
    klaeren,
)
from .closing import decisions as entscheidungen_modul
from .models import (
    Inventur,
    Inventurnotiz,
    Inventurstatus,
    Sollbestand,
    Wiegung,
    Zaehlposition,
)
from .services import bemerkungsstand, zaehlstand
from .test_views import InventurSeiten, gesehen
from .views import review as pruefliste


#: The switch of the fourth block into a type.
def nach(art) -> str:
    return f"{WECHSEL_PRAEFIX}{art.pk}"


class Klaerung(InventurSeiten):
    """The basis: Artikel C is expected at the counter in the crate."""

    def setUp(self):
        super().setUp()
        self.inv = self.inventur(status=Inventurstatus.ZAEHLUNG)
        self.kasten_vorgabe = ErwartungGebinde.objects.get()
        self.flasche = Gebinde.objects.create(
            ware=self.ware, name="Flasche", faktor_in_basiseinheit=Decimal("1")
        )

    def zaehlen(self, menge="2"):
        self.inv.zaehlpositionen.update(eingabemenge=Decimal(menge))

    def nicht_aktiv(self, name, nummer, status=Artikelstatus.INAKTIV):
        ware = Ware.objects.create(name=name, basiseinheit=self.stueck)
        artikel = Hausartikel.objects.create(
            haus=self.haus,
            ware=ware,
            warengruppe=Warengruppe.objects.get(name="Warengruppe G"),
            artikelnummer=nummer,
            name=name,
            status=status,
        )
        karton = Gebinde.objects.create(
            ware=ware, name="Karton", faktor_in_basiseinheit=Decimal("10")
        )
        return artikel, karton

    def unerwartet(self, artikel, gebinde=None, menge="3", ort=None):
        return Zaehlposition.objects.create(
            inventur=self.inv,
            ort=ort or self.ort,
            artikel=artikel,
            gebinde=gebinde,
            eingabemenge=Decimal(menge),
            unerwartet=True,
        )


class MehrAlsEinErgebnisFasst(Klaerung):
    """A typo in the count: 1.2 billion bottles do not fit into the result.

    Each row on its own fits ('Zaehlposition.clean'), the two together do not.
    Until 10/2026 PostgreSQL ended the closing with an error page, and SQLite
    closed and could not show the inventory afterwards.
    """

    MELDUNG = (
        "„Artikel C 0,33 Flasche“: 1200000000 Stk. sind mehr, als ein Ergebnis fasst. "
        "Bitte die Zählung prüfen."
    )

    def setUp(self):
        super().setUp()
        lager = Ort.objects.create(
            name="Trockenlager", bereich=Bereich.objects.create(name="Lager", haus=self.haus)
        )
        erwartung = Erwartung.objects.create(ort=lager, artikel=self.artikel)
        ErwartungGebinde.objects.create(erwartung=erwartung, gebinde=self.kasten)
        self.inv.zaehlpositionen_anlegen()
        self.zaehlen("30000000")  # 600 million bottles in each place

    def assertNichtsEingefroren(self):
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Inventurstatus.ZAEHLUNG)
        self.assertFalse(self.inv.ergebnisse.exists())
        self.assertEqual(self.inv.zaehlpositionen.count(), 2)

    def test_der_abschluss_nennt_den_artikel(self):
        with self.assertRaises(ValidationError) as fehler:
            abschliessen(self.inv, Entscheidungen())
        self.assertEqual(fehler.exception.messages, [self.MELDUNG])
        self.assertNichtsEingefroren()

    def test_die_pruefliste_meldet_es(self):
        antwort = self.client.post(
            reverse("inventur_pruefen", args=[self.inv.pk]),
            {"abschliessen": "1", **gesehen(self.inv)},
        )
        self.assertRedirects(antwort, self.inv.get_absolute_url())
        self.assertEqual([str(m) for m in get_messages(antwort.wsgi_request)], [self.MELDUNG])
        self.assertNichtsEingefroren()

    def test_der_lauf_meldet_es_mit_dem_haus(self):
        stichtag = self.inv.stichtag.isoformat()
        antwort = self.client.post(
            reverse("inventur_lauf_abschluss", args=[stichtag]),
            {
                f"zaehlstand_{self.inv.pk}": zaehlstand(self.inv),
                f"bemerkungsstand_{self.inv.pk}": bemerkungsstand(self.inv, mit_notizen=False),
            },
        )
        # A run of one house leads on to the inventory, hence not fetched.
        self.assertRedirects(
            antwort, reverse("inventur_lauf", args=[stichtag]), fetch_redirect_response=False
        )
        self.assertEqual(
            [str(m) for m in get_messages(antwort.wsgi_request)], [f"{self.inv}: {self.MELDUNG}"]
        )
        self.assertNichtsEingefroren()


class WasGefragtWird(Klaerung):
    def test_alles_gezaehlt_nichts_zu_fragen(self):
        self.zaehlen()
        self.assertTrue(klaeren(self.inv).leer)

    def test_leeres_gebinde_und_ungezaehlter_artikel(self):
        jahr = jahresinventur()
        klaerung = klaeren(self.inv)
        self.assertEqual([e.vorgabe for e in klaerung.ungezaehlte_gebinde], [self.kasten_vorgabe])
        self.assertEqual([e.hausartikel for e in klaerung.ungezaehlte_artikel], [self.artikel])
        self.assertEqual(klaerung.ungezaehlte_artikel[0].andere_arten, [jahr])

    def test_in_der_app_geloeschtes_gebinde_zaehlt_als_leer(self):
        """Even if a number is still in the deleted row."""
        self.inv.zaehlpositionen.update(eingabemenge=Decimal("2"), aktiv=False)
        eintrag = klaeren(self.inv).ungezaehlte_gebinde[0]
        self.assertTrue(eintrag.in_der_app_geloescht)

    def test_soll_zaehlt_nimmt_den_artikel_aus_der_frage(self):
        Inventurnotiz.objects.create(
            inventur=self.inv, hausartikel=self.artikel, soll_statt_ist=True
        )
        self.assertEqual(klaeren(self.inv).ungezaehlte_artikel, [])

    def test_die_eigene_art_steht_nicht_zur_wahl(self):
        self.inv.inventurarten.add(jahresinventur())
        self.artikel.inventurart_setzen(jahresinventur())
        self.assertEqual(klaeren(self.inv).ungezaehlte_artikel[0].andere_arten, [monatsinventur()])

    def test_unerwartetes_gebinde_eines_erwarteten_artikels(self):
        self.zaehlen()
        self.unerwartet(self.artikel, self.flasche)
        eintrag = klaeren(self.inv).neue_erwartungen[0]
        self.assertEqual(eintrag.gebinde, self.flasche)
        self.assertTrue(eintrag.artikel_schon_erwartet)
        self.assertEqual(eintrag.menge, "3 × Flasche")

    def test_eingestellter_artikel_wird_nicht_zur_zaehlliste_angeboten(self):
        """Discontinued means over: the quantity goes into the result, it is expected nowhere."""
        self.zaehlen()
        fassbrause, karton = self.nicht_aktiv("Artikel F", "301009", status=Artikelstatus.AKTIV)
        self.unerwartet(fassbrause, karton, menge="2")
        fassbrause.einstellen()

        klaerung = klaeren(self.inv)
        self.assertNotIn(fassbrause.pk, [e.artikel.pk for e in klaerung.neue_erwartungen])
        abschliessen(self.inv, Entscheidungen())
        self.assertEqual(
            self.inv.ergebnisse.get(hausartikel=fassbrause).gesamtmenge_basiseinheit, Decimal("20")
        )
        self.assertFalse(Erwartung.objects.filter(artikel=fassbrause).exists())

    def test_eingestellter_mit_freiem_platz_steht_zur_wiederaufnahme(self):
        """Found on the shelf and counted. Then it should be able to come back too."""
        self.zaehlen()
        fassbrause, karton = self.nicht_aktiv(
            "Artikel F", "301010", status=Artikelstatus.EINGESTELLT
        )
        self.unerwartet(fassbrause, karton, menge="2")

        (eintrag,) = klaeren(self.inv).wiederaufnahmen
        self.assertEqual((eintrag.hausartikel, eintrag.hinderung), (fassbrause, ""))

        bericht = abschliessen(self.inv, Entscheidungen(wiederaufnehmen={eintrag.schluessel}))
        fassbrause.refresh_from_db()
        self.assertEqual(fassbrause.status, Artikelstatus.AKTIV)
        self.assertEqual(bericht.wieder_aktiv, 1)

    def test_eingestellter_mit_vergebenem_platz_laesst_sich_nicht_anhaken(self):
        """Counted while the number was free. By closing someone else has it."""
        self.zaehlen()
        fassbrause, karton = self.nicht_aktiv(
            "Artikel F", "301011", status=Artikelstatus.EINGESTELLT
        )
        self.unerwartet(fassbrause, karton, menge="2")
        self.nicht_aktiv("Artikel F neu", "301011", status=Artikelstatus.AKTIV)

        (eintrag,) = klaeren(self.inv).wiederaufnahmen
        self.assertIn("301011", str(eintrag.hinderung))
        with self.assertRaises(ValidationError):
            abschliessen(self.inv, Entscheidungen(wiederaufnehmen={eintrag.schluessel}))
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Inventurstatus.ZAEHLUNG, "Nichts davon ist geschehen.")

        # Without the tick the closing goes through, and the quantity is in the result.
        abschliessen(self.inv, Entscheidungen())
        self.assertEqual(
            self.inv.ergebnisse.get(hausartikel=fassbrause).gesamtmenge_basiseinheit, Decimal("20")
        )

    def test_inaktive_zutat_eines_rezepturartikels_steht_zur_wiederaufnahme(self):
        """The portion was counted. Its share lies on the inactive row, which is asked about."""
        self.zaehlen()
        chips, _ = self.nicht_aktiv("Ware C", "301004")
        tuete = Rezepturartikel.objects.create(name="Portion C", basiseinheit=self.stueck)
        Rezepturkomponente.objects.create(
            rezepturartikel=tuete,
            ware=chips.ware,
            menge_in_basiseinheit=Decimal("1"),
        )
        self.unerwartet(tuete, menge="5")
        self.assertEqual([e.hausartikel for e in klaeren(self.inv).wiederaufnahmen], [chips])

    def test_leer_gebliebene_unerwartete_zeile_wird_nicht_angeboten(self):
        """Whoever created something and entered nothing did not find it."""
        self.zaehlen()
        leer = self.unerwartet(self.artikel, self.flasche)
        Zaehlposition.objects.filter(pk=leer.pk).update(eingabemenge=None)
        self.assertEqual(klaeren(self.inv).neue_erwartungen, [])

    def test_inaktiv_gezaehlt(self):
        self.zaehlen()
        artikel, karton = self.nicht_aktiv("Artikel A", "301001")
        self.unerwartet(artikel, karton)
        self.assertEqual(
            [eintrag.hausartikel for eintrag in klaeren(self.inv).wiederaufnahmen], [artikel]
        )


class OhneHakenBleibtAlles(Klaerung):
    def test_abschluss_ohne_antworten_friert_nur_ein(self):
        self.unerwartet(self.artikel, self.flasche)
        abschliessen(self.inv, Entscheidungen())

        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Inventurstatus.ABGESCHLOSSEN)
        self.assertTrue(ErwartungGebinde.objects.filter(pk=self.kasten_vorgabe.pk).exists())
        self.assertFalse(ErwartungGebinde.objects.filter(gebinde=self.flasche).exists())
        self.artikel.refresh_from_db()
        self.assertTrue(self.artikel.aktiv)
        self.assertEqual(self.artikel.inventurart, monatsinventur())


class Antworten(Klaerung):
    def test_leeres_gebinde_herausnehmen(self):
        abschliessen(self.inv, Entscheidungen(herausnehmen={str(self.kasten_vorgabe.pk)}))
        self.assertFalse(ErwartungGebinde.objects.filter(pk=self.kasten_vorgabe.pk).exists())
        # The article itself stays in the counting list.
        self.assertTrue(Erwartung.objects.filter(ort=self.ort, artikel=self.artikel).exists())

    def test_inaktiv_setzen(self):
        """Seasonal goods: they stay in the counting list, for the next season."""
        bericht = abschliessen(
            self.inv, Entscheidungen(ungezaehlt={str(self.artikel.pk): INAKTIV_SETZEN})
        )
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.status, Artikelstatus.INAKTIV)
        self.assertEqual(bericht.inaktiv_gesetzt, 1)
        self.assertTrue(Erwartung.objects.filter(artikel=self.artikel).exists())

    def test_einstellen(self):
        bericht = abschliessen(
            self.inv, Entscheidungen(ungezaehlt={str(self.artikel.pk): EINSTELLEN})
        )
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.status, Artikelstatus.EINGESTELLT)
        self.assertEqual(bericht.eingestellt, 1)
        self.assertFalse(Erwartung.objects.filter(artikel=self.artikel).exists())

    def test_in_eine_andere_art(self):
        """The product has no further article: no question for whom it applies."""
        bericht = abschliessen(
            self.inv, Entscheidungen(ungezaehlt={str(self.artikel.pk): nach(jahresinventur())})
        )
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.inventurart, jahresinventur())
        self.assertEqual(bericht.art_gewechselt, 1)

    def test_in_eine_andere_art_ans_ende_der_zaehlliste(self):
        """Behind what the counter already counts in that type, not at its old place."""
        andere, _ = self.nicht_aktiv("Ware N", "101900")
        andere.inventurart_setzen(jahresinventur())
        Erwartung.objects.create(ort=self.ort, artikel=andere)
        abschliessen(
            self.inv, Entscheidungen(ungezaehlt={str(self.artikel.pk): nach(jahresinventur())})
        )
        folge = Erwartung.objects.filter(ort=self.ort).order_by("sortierung")
        self.assertEqual([satz.artikel_id for satz in folge], [andere.pk, self.artikel.pk])

    def test_ware_ohne_waage_wird_ohne_waage_aufgenommen(self):
        """'wiegen_erwartet' says nothing here. Set, a later switch to the scale would

        bring a weighing row to exactly this location (until 10/2026).
        """
        self.zaehlen()
        lager = Ort.objects.create(
            name="Lager", bereich=Bereich.objects.create(name="Lager", haus=self.haus)
        )
        self.unerwartet(self.artikel, self.kasten, ort=lager)
        (eintrag,) = klaeren(self.inv).neue_erwartungen

        abschliessen(self.inv, Entscheidungen(aufnehmen={eintrag.schluessel}))
        erwartung = Erwartung.objects.get(ort=lager, artikel=self.artikel)
        self.assertFalse(erwartung.wiegen_erwartet)
        self.assertEqual([v.gebinde for v in erwartung.gebinde_vorgaben.all()], [self.kasten])

    def im_lager_gefunden(self):
        """Counted in the store, where nobody expected the article. The closing's question."""
        self.zaehlen()
        lager = Ort.objects.create(
            name="Lager", bereich=Bereich.objects.create(name="Lager", haus=self.haus)
        )
        self.unerwartet(self.artikel, self.kasten, ort=lager)
        (eintrag,) = klaeren(self.inv).neue_erwartungen
        return lager, eintrag

    def test_eine_gleichzeitig_angelegte_erwartung_wird_genommen(self):
        """The list's page took the article in after the closing looked, before it created.

        Its place wins, and the closing adds only what it found. Until 10/2026
        the closing ended in a server error then and froze nothing.
        """
        lager, eintrag = self.im_lager_gefunden()
        von_der_seite = Erwartung.objects.create(ort=lager, artikel=self.artikel)

        self.assertEqual(entscheidungen_modul._neu_in_der_liste(eintrag), von_der_seite)
        self.assertEqual(Erwartung.objects.filter(ort=lager).count(), 1)

    def test_ist_der_platz_am_ende_vergeben_versucht_es_der_abschluss_noch_einmal(self):
        """Another article took the place at the end in the same moment: the next one is free."""
        lager, eintrag = self.im_lager_gefunden()
        echt = Erwartung.anlegen
        versuche = []

        def erst_besetzt(ort, artikel, **angaben):
            versuche.append(ort)
            if len(versuche) == 1:
                raise IntegrityError("sortierung_je_ort_eindeutig")
            return echt(ort, artikel, **angaben)

        with mock.patch.object(Erwartung, "anlegen", side_effect=erst_besetzt):
            abschliessen(self.inv, Entscheidungen(aufnehmen={eintrag.schluessel}))

        self.assertEqual(len(versuche), 2)
        erwartung = Erwartung.objects.get(ort=lager, artikel=self.artikel)
        self.assertEqual([v.gebinde for v in erwartung.gebinde_vorgaben.all()], [self.kasten])
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Inventurstatus.ABGESCHLOSSEN)

    def test_unerwartetes_gebinde_aufnehmen(self):
        self.zaehlen()
        self.unerwartet(self.artikel, self.flasche)
        schluessel = klaeren(self.inv).neue_erwartungen[0].schluessel
        abschliessen(self.inv, Entscheidungen(aufnehmen={schluessel}))
        self.assertTrue(
            ErwartungGebinde.objects.filter(
                erwartung__ort=self.ort, erwartung__artikel=self.artikel, gebinde=self.flasche
            ).exists()
        )

    def test_unerwarteter_artikel_kommt_ans_ende_der_zaehlliste(self):
        self.zaehlen()
        lager = Ort.objects.create(
            name="Lager", bereich=Bereich.objects.create(name="Lager", haus=self.haus)
        )
        vorhanden = Erwartung.objects.create(
            ort=lager, artikel=self.nicht_aktiv("Artikel W", "301002")[0]
        )
        self.unerwartet(self.artikel, self.kasten, ort=lager)
        schluessel = klaeren(self.inv).neue_erwartungen[0].schluessel

        abschliessen(self.inv, Entscheidungen(aufnehmen={schluessel}))

        folge = list(
            Erwartung.objects.filter(ort=lager).order_by("sortierung").values_list("pk", flat=True)
        )
        self.assertEqual(folge[0], vorhanden.pk)
        self.assertEqual(len(folge), 2)

    def test_inaktiven_wieder_aktivieren(self):
        self.zaehlen()
        artikel, karton = self.nicht_aktiv("Artikel A", "301003")
        self.unerwartet(artikel, karton)
        abschliessen(self.inv, Entscheidungen(wiederaufnehmen={str(artikel.pk)}))
        artikel.refresh_from_db()
        self.assertTrue(artikel.aktiv)
        self.assertEqual(
            self.inv.ergebnisse.get(hausartikel=artikel).gesamtmenge_basiseinheit, Decimal("30")
        )

    def test_eingefroren_wird_was_die_pruefliste_zeigte(self):
        """The share from a recipe too lies on the inactive row before activating.

        Result and check list must not drift apart, whatever is answered.
        """
        self.zaehlen()
        artikel, karton = self.nicht_aktiv("Ware C", "301004")
        self.unerwartet(artikel, karton, menge="1")
        tuete = Rezepturartikel.objects.create(name="Portion C", basiseinheit=self.stueck)
        Rezepturkomponente.objects.create(
            rezepturartikel=tuete,
            ware=artikel.ware,
            menge_in_basiseinheit=Decimal("1"),
        )
        self.unerwartet(tuete, menge="5")
        vorher = self.inv.mengen_je_hausartikel()[artikel.pk]

        abschliessen(self.inv, Entscheidungen(wiederaufnehmen={str(artikel.pk)}))

        ergebnis = self.inv.ergebnisse.get(hausartikel=artikel)
        self.assertEqual(ergebnis.gesamtmenge_basiseinheit, vorher)
        self.assertEqual(ergebnis.gesamtmenge_basiseinheit, Decimal("15"))
        artikel.refresh_from_db()
        self.assertTrue(artikel.aktiv)


class WasNichtGeht(Klaerung):
    def test_eingestellten_satz_mit_vergebener_ware_gibt_es_nicht_zurueck(self):
        """Its product has long been carried by another row. Page and server lock the tick."""
        self.zaehlen()
        alt = Hausartikel.objects.create(
            haus=self.haus,
            ware=self.ware,
            warengruppe=self.artikel.warengruppe,
            artikelnummer="101009",
            name="Artikel C alt",
            status=Artikelstatus.EINGESTELLT,
        )
        self.unerwartet(alt, self.flasche)

        with self.assertRaises(ValidationError):
            abschliessen(self.inv, Entscheidungen(wiederaufnehmen={str(alt.pk)}))
        abschliessen(self.inv, Entscheidungen())

        alt.refresh_from_db()
        self.assertEqual(alt.status, Artikelstatus.EINGESTELLT)
        # Counted is counted: the quantity is in the result.
        self.assertEqual(
            self.inv.ergebnisse.get(hausartikel=alt).gesamtmenge_basiseinheit, Decimal("3")
        )

    def test_eine_unbekannte_art_wird_abgewiesen(self):
        with self.assertRaises(ValidationError):
            abschliessen(
                self.inv, Entscheidungen(ungezaehlt={str(self.artikel.pk): f"{WECHSEL_PRAEFIX}0"})
            )
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Inventurstatus.ZAEHLUNG)

    def test_erledigte_frage_wird_uebergangen(self):
        """The app has submitted in the meantime. The answer from the page no longer applies."""
        schluessel = str(self.kasten_vorgabe.pk)
        self.zaehlen()
        abschliessen(self.inv, Entscheidungen(herausnehmen={schluessel}))
        self.assertTrue(ErwartungGebinde.objects.filter(pk=self.kasten_vorgabe.pk).exists())

    def test_zurueck_in_der_vorbereitung_schliesst_nicht_ab(self):
        """Decided is the status under the lock, not the one the object was loaded with."""
        self.zaehlen()
        veraltet = Inventur.objects.get(pk=self.inv.pk)
        stand = zaehlstand(veraltet)
        Inventur.objects.get(pk=self.inv.pk).zurueck_in_vorbereitung()

        with self.assertRaises(ValidationError):
            abschliessen(veraltet, Entscheidungen(), gesehen=stand)

        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Inventurstatus.VORBEREITUNG)
        self.assertFalse(self.inv.ergebnisse.exists())

    def test_ueber_die_pruefliste_ebenso(self):
        """The page checked the count, a second tab goes back before the closing takes its lock."""
        self.zaehlen()
        echt = pruefliste.klaeren

        def zweiter_tab(inventur):
            Inventur.objects.get(pk=inventur.pk).zurueck_in_vorbereitung()
            return echt(inventur)

        with mock.patch.object(pruefliste, "klaeren", side_effect=zweiter_tab):
            antwort = self.client.post(
                reverse("inventur_pruefen", args=[self.inv.pk]),
                {"abschliessen": "1", **gesehen(self.inv)},
            )

        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Inventurstatus.VORBEREITUNG)
        meldungen = " ".join(str(meldung) for meldung in get_messages(antwort.wsgi_request))
        self.assertIn("steht nicht in der Zählung", meldungen)

    def test_eine_portion_ohne_gefuehrte_zutat_wird_nicht_aufgenommen(self):
        """Its ingredient is carried nowhere in the house. The page says it, the closing too."""
        self.zaehlen()
        ware = Ware.objects.create(name="Ware M", basiseinheit=self.stueck)
        portion = Rezepturartikel.objects.create(name="Portion M", basiseinheit=self.stueck)
        Rezepturkomponente.objects.create(
            rezepturartikel=portion, ware=ware, menge_in_basiseinheit=Decimal("1")
        )
        self.unerwartet(portion, menge="2")
        eintrag = klaeren(self.inv).neue_erwartungen[0]
        self.assertEqual(
            str(eintrag.hinderung),
            "Eine Rezepturkomponente hat in diesem Haus keinen geführten Hausartikel mehr.",
        )

        with self.assertRaises(ValidationError) as fehler:
            abschliessen(self.inv, Entscheidungen(aufnehmen={eintrag.schluessel}))

        self.assertEqual(
            fehler.exception.messages, [f"„Portion M“ an Thekenfront: {eintrag.hinderung}"]
        )
        self.assertFalse(self.inv.ergebnisse.exists())
        self.assertFalse(Erwartung.objects.filter(artikel=portion).exists())

    def test_eine_art_die_diese_inventur_nicht_zaehlt(self):
        self.zaehlen()
        becher, karton = self.nicht_aktiv("Artikel M", "301020", status=Artikelstatus.AKTIV)
        becher.inventurart_setzen(jahresinventur())
        self.unerwartet(becher, karton)

        with self.assertRaises(ValidationError) as fehler:
            abschliessen(
                self.inv, Entscheidungen(in_art={str(becher.pk): str(jahresinventur().pk)})
            )

        self.assertEqual(
            fehler.exception.messages, ["„Artikel M“: diese Inventur zählt die gewählte Art nicht."]
        )
        self.assertFalse(self.inv.ergebnisse.exists())
        becher.refresh_from_db()
        self.assertEqual(becher.inventurart, jahresinventur())


class GezaehltAusserhalb(Klaerung):
    """Annual goods that were added in the app in the monthly inventory."""

    def setUp(self):
        super().setUp()
        self.zaehlen()
        self.becher, self.karton = self.nicht_aktiv(
            "Artikel M", "301020", status=Artikelstatus.AKTIV
        )
        self.becher.inventurart_setzen(jahresinventur())

    def test_wird_gefragt(self):
        self.unerwartet(self.becher, self.karton, menge="2")
        eintrag = klaeren(self.inv).ausserhalb[0]
        self.assertEqual(eintrag.hausartikel, self.becher)
        self.assertEqual(eintrag.ist_text, "20")
        self.assertEqual(eintrag.ziele, [monatsinventur()])

    def test_zaehlt_die_inventur_seine_art_gibt_es_nichts_zu_fragen(self):
        self.inv.inventurarten.add(jahresinventur())
        self.unerwartet(self.becher, self.karton)
        self.assertEqual(klaeren(self.inv).ausserhalb, [])

    def test_monatsware_wird_nicht_gefragt(self):
        fassbrause, karton = self.nicht_aktiv("Artikel F", "301021", status=Artikelstatus.AKTIV)
        self.unerwartet(fassbrause, karton)
        self.assertEqual(klaeren(self.inv).ausserhalb, [])

    def test_leere_und_geloeschte_zeilen_zaehlen_nicht(self):
        leer = self.unerwartet(self.becher, self.karton)
        Zaehlposition.objects.filter(pk=leer.pk).update(eingabemenge=None)
        self.assertEqual(klaeren(self.inv).ausserhalb, [])
        Zaehlposition.objects.filter(pk=leer.pk).update(eingabemenge=Decimal("1"), aktiv=False)
        self.assertEqual(klaeren(self.inv).ausserhalb, [])

    def test_nur_als_zutat_gezaehlt_wird_nicht_gefragt(self):
        """The cup in the monthly counted portion is therefore no monthly goods."""
        portion = Rezepturartikel.objects.create(name="Portion N", basiseinheit=self.stueck)
        for ware in (self.ware, self.becher.ware):
            Rezepturkomponente.objects.create(
                rezepturartikel=portion, ware=ware, menge_in_basiseinheit=Decimal("1")
            )
        self.unerwartet(portion, menge="4")
        self.assertEqual(klaeren(self.inv).ausserhalb, [])

    def test_ohne_antwort_bleibt_die_art(self):
        self.unerwartet(self.becher, self.karton)
        bericht = abschliessen(self.inv, Entscheidungen())
        self.becher.refresh_from_db()
        self.assertEqual(self.becher.inventurart, jahresinventur())
        self.assertEqual(bericht.in_die_art, 0)

    def test_mit_antwort_kommt_er_in_die_art_der_inventur(self):
        self.unerwartet(self.becher, self.karton, menge="2")
        bericht = abschliessen(
            self.inv, Entscheidungen(in_art={str(self.becher.pk): str(monatsinventur().pk)})
        )
        self.becher.refresh_from_db()
        self.assertEqual(self.becher.inventurart, monatsinventur())
        self.assertEqual(bericht.in_die_art, 1)
        # The quantity goes into the result either way.
        self.assertEqual(
            self.inv.ergebnisse.get(hausartikel=self.becher).gesamtmenge_basiseinheit,
            Decimal("20"),
        )

    def test_seite_zeigt_den_block_und_nimmt_die_antwort(self):
        self.unerwartet(self.becher, self.karton)
        adresse = reverse("inventur_abschluss", args=[self.inv.pk])
        self.assertContains(self.client.get(adresse), "in eine aufnehmen?")
        self.client.post(
            adresse, gesehen(self.inv) | {f"in_art_{self.becher.pk}": monatsinventur().pk}
        )
        self.becher.refresh_from_db()
        self.assertEqual(self.becher.inventurart, monatsinventur())


class Seiten(Klaerung):
    def test_pruefliste_fuehrt_in_die_klaerung(self):
        stand = zaehlstand(self.inv)
        antwort = self.client.post(
            reverse("inventur_pruefen", args=[self.inv.pk]),
            {"abschliessen": "1", **gesehen(self.inv)},
        )
        ziel = reverse("inventur_abschluss", args=[self.inv.pk])
        # The seen states go along and are in the form of the clarification.
        abfrage = {"stand": stand, "bemerkungen": bemerkungsstand(self.inv)}
        self.assertRedirects(antwort, f"{ziel}?{urlencode(abfrage)}")
        self.assertContains(self.client.get(ziel, abfrage), f'name="zaehlstand" value="{stand}"')
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Inventurstatus.ZAEHLUNG)

    def test_klaerung_schliesst_nicht_ab_wenn_seither_gezaehlt_wurde(self):
        formular = gesehen(self.inv)
        position = self.inv.zaehlpositionen.filter(aktiv=True).earliest("pk")
        position.eingabemenge = (position.eingabemenge or Decimal("0")) + 1
        position.save()

        antwort = self.client.post(reverse("inventur_abschluss", args=[self.inv.pk]), formular)

        self.assertRedirects(antwort, reverse("inventur_pruefen", args=[self.inv.pk]))
        self.assertEqual([str(m) for m in get_messages(antwort.wsgi_request)], [str(NEUE_WERTE)])
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Inventurstatus.ZAEHLUNG)

    def test_was_nach_dem_vergleich_der_seite_eintrifft_friert_nicht_ein(self):
        """The view compares early, for the message. Final is the comparison under the lock."""
        formular = gesehen(self.inv)

        with mock.patch("inventur.closing.decisions.zaehlstand", return_value="anders"):
            antwort = self.client.post(reverse("inventur_abschluss", args=[self.inv.pk]), formular)

        self.assertRedirects(antwort, reverse("inventur_pruefen", args=[self.inv.pk]))
        self.assertEqual([str(m) for m in get_messages(antwort.wsgi_request)], [str(NEUE_WERTE)])
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Inventurstatus.ZAEHLUNG)

    def test_klaerung_schliesst_mit_dem_gesehenen_stand_ab(self):
        antwort = self.client.post(
            reverse("inventur_abschluss", args=[self.inv.pk]), gesehen(self.inv)
        )
        self.assertRedirects(antwort, self.inv.get_absolute_url())
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Inventurstatus.ABGESCHLOSSEN)

    def test_seite_zeigt_alle_bloecke(self):
        artikel, karton = self.nicht_aktiv("Artikel A", "301005")
        self.unerwartet(artikel, karton)
        self.unerwartet(self.artikel, self.flasche)
        # An article with target but without any row. It stays uncounted.
        eistee = Hausartikel.objects.create(
            haus=self.haus,
            ware=Ware.objects.create(name="Artikel E", basiseinheit=self.stueck),
            warengruppe=self.artikel.warengruppe,
            artikelnummer="301006",
            name="Artikel E",
        )
        Sollbestand.objects.create(
            inventur=self.inv, hausartikel=eistee, menge_basiseinheit=Decimal("4")
        )
        antwort = self.client.get(reverse("inventur_abschluss", args=[self.inv.pk]))
        for text in (
            "wieder aktivieren",
            "einstellen",
            "in die Zählliste aufnehmen",
            "aus der Zählliste nehmen",
            "Gar nicht gezählt",
        ):
            with self.subTest(text=text):
                self.assertContains(antwort, text)

    def test_abschliessen_ueber_die_seite(self):
        antwort = self.client.post(
            reverse("inventur_abschluss", args=[self.inv.pk]),
            gesehen(self.inv)
            | {
                "herausnehmen": [self.kasten_vorgabe.pk],
                f"ungezaehlt_{self.artikel.pk}": nach(jahresinventur()),
            },
        )
        self.assertRedirects(antwort, self.inv.get_absolute_url())
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Inventurstatus.ABGESCHLOSSEN)
        self.artikel.refresh_from_db()
        self.assertEqual(self.artikel.inventurart, jahresinventur())

    def test_fehler_zeigen_die_seite_mit_den_antworten_wieder(self):
        antwort = self.client.post(
            reverse("inventur_abschluss", args=[self.inv.pk]),
            gesehen(self.inv)
            | {
                "herausnehmen": [self.kasten_vorgabe.pk],
                f"ungezaehlt_{self.artikel.pk}": f"{WECHSEL_PRAEFIX}0",
            },
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Abgeschlossen wurde nichts")
        self.assertContains(
            antwort, f'value="{self.kasten_vorgabe.pk}" id="raus-{self.kasten_vorgabe.pk}"\n'
        )

    def test_nicht_aus_der_vorbereitung(self):
        self.inv.zurueck_in_vorbereitung()
        antwort = self.client.get(reverse("inventur_abschluss", args=[self.inv.pk]))
        self.assertRedirects(antwort, self.inv.get_absolute_url())


class GezaehltOhneSoll(Klaerung):
    """The fifth block: counted, but not in the file from WinTICKET."""

    def setUp(self):
        super().setUp()
        self.zaehlen()
        # A second article with target, so that "a target stock was read in" applies.
        self.anderer, _ = self.nicht_aktiv("Artikel F", "100777", status=Artikelstatus.AKTIV)
        Sollbestand.objects.create(
            inventur=self.inv, hausartikel=self.anderer, menge_basiseinheit=Decimal("5")
        )
        self.cola = Hausartikel.objects.get(ware=self.ware, haus=self.haus)
        self.adresse = reverse("inventur_abschluss", args=[self.inv.pk])

    def ergebnis(self):
        return self.inv.ergebnisse.get(hausartikel=self.cola)

    def test_gefragt_wird_nach_gezaehlten_ohne_soll(self):
        self.assertEqual(
            [eintrag.hausartikel for eintrag in klaeren(self.inv).ohne_soll], [self.cola]
        )

    def test_ohne_eingelesenen_sollbestand_keine_frage(self):
        self.inv.sollbestaende.all().delete()
        self.assertEqual(klaeren(self.inv).ohne_soll, [])

    def test_mit_soll_keine_frage(self):
        Sollbestand.objects.create(
            inventur=self.inv, hausartikel=self.cola, menge_basiseinheit=Decimal("40")
        )
        self.assertEqual(klaeren(self.inv).ohne_soll, [])

    def test_ungezaehlt_gehoert_nicht_hierher(self):
        self.inv.zaehlpositionen.update(eingabemenge=None)
        self.assertEqual(klaeren(self.inv).ohne_soll, [])

    def test_ohne_antwort_bleibt_die_zeile_ohne_soll(self):
        abschliessen(self.inv, Entscheidungen())
        self.assertIsNone(self.ergebnis().sollbestand)
        self.assertIsNone(self.ergebnis().differenz)

    def test_soll_null_macht_das_gezaehlte_zum_ueberschuss(self):
        schluessel = str(self.cola.pk)
        bericht = abschliessen(self.inv, Entscheidungen(soll={schluessel: SOLL_NULL}))
        self.assertEqual(bericht.soll_gesetzt, 1)
        self.assertEqual(self.ergebnis().sollbestand, Decimal("0"))
        self.assertEqual(self.ergebnis().differenz, self.ergebnis().gesamtmenge_basiseinheit)

    def test_eigener_wert_mit_komma(self):
        """The comma is the decimal separator: 37.5, neither 375 nor 37.

        Frozen as the sheet shows it, in whole pieces and half up.
        """
        schluessel = str(self.cola.pk)
        abschliessen(
            self.inv,
            Entscheidungen(soll={schluessel: SOLL_EIGENER_WERT}, sollwerte={schluessel: "37,5"}),
        )
        ergebnis = self.ergebnis()
        self.assertEqual(ergebnis.sollbestand, Decimal("38"))
        self.assertEqual(ergebnis.differenz, ergebnis.gesamtmenge_basiseinheit - Decimal("38"))

    def test_eigener_wert_darf_negativ_sein(self):
        """As on the maintenance page: more sold than booked is a stock below zero."""
        schluessel = str(self.cola.pk)
        abschliessen(
            self.inv,
            Entscheidungen(soll={schluessel: SOLL_EIGENER_WERT}, sollwerte={schluessel: "-3"}),
        )
        self.assertEqual(self.ergebnis().sollbestand, Decimal("-3"))

    def test_eigener_wert_ohne_zahl_schliesst_nichts_ab(self):
        schluessel = str(self.cola.pk)
        for wert in ("", "viel", "1.234.567.890.123"):
            with self.subTest(wert=wert), self.assertRaises(ValidationError):
                abschliessen(
                    self.inv,
                    Entscheidungen(
                        soll={schluessel: SOLL_EIGENER_WERT}, sollwerte={schluessel: wert}
                    ),
                )
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.status, Inventurstatus.ZAEHLUNG)
        self.assertFalse(self.inv.sollbestaende.filter(hausartikel=self.cola).exists())

    def test_getippter_wert_ohne_die_wahl_dazu_wird_uebergangen(self):
        """Back to "without target" means without target, even if the field still holds a value."""
        abschliessen(self.inv, Entscheidungen(sollwerte={str(self.cola.pk): "12"}))
        self.assertIsNone(self.ergebnis().sollbestand)

    def test_antwort_zu_fremdem_artikel_wird_uebergangen(self):
        abschliessen(self.inv, Entscheidungen(soll={str(self.anderer.pk): SOLL_NULL}))
        self.assertEqual(
            self.inv.ergebnisse.get(hausartikel=self.anderer).sollbestand, Decimal("5")
        )

    def test_die_antwort_gilt_auch_fuer_jahresware_aus_einer_rezeptur(self):
        """A target set by hand always applies, even where the file's has no say.

        Artikel M is only due annually and sits in a monthly counted portion: it
        is not counted itself, its target from the file therefore stays mute, and
        block 5 asks about it. Until 09/2026 the page reported "target set", and
        the row froze without a difference nonetheless.
        """
        becher_ware = Ware.objects.create(name="Artikel M", basiseinheit=self.stueck)
        becher = Hausartikel.objects.create(
            haus=self.haus,
            ware=becher_ware,
            warengruppe=Warengruppe.objects.get(name="Warengruppe G"),
            artikelnummer="400001",
            name="Artikel M",
            inventurart=jahresinventur(),
        )
        portion = Rezepturartikel.objects.create(name="Portion", basiseinheit=self.stueck)
        Rezepturkomponente.objects.create(
            rezepturartikel=portion, ware=becher_ware, menge_in_basiseinheit=Decimal("1")
        )
        self.unerwartet(portion, menge="5")
        # The file knew it: stored, but mute in the monthly inventory.
        Sollbestand.objects.create(
            inventur=self.inv, hausartikel=becher, menge_basiseinheit=Decimal("100")
        )
        self.assertIn(becher, [eintrag.hausartikel for eintrag in klaeren(self.inv).ohne_soll])

        bericht = abschliessen(self.inv, Entscheidungen(soll={str(becher.pk): SOLL_NULL}))

        ergebnis = self.inv.ergebnisse.get(hausartikel=becher)
        self.assertEqual(bericht.soll_gesetzt, 1)
        self.assertEqual(ergebnis.sollbestand, Decimal("0"))
        self.assertEqual(ergebnis.differenz, Decimal("5"))

    def test_seite_zeigt_den_block_und_nimmt_die_antwort(self):
        antwort = self.client.get(self.adresse)
        self.assertContains(antwort, "Gezählt, aber ohne Soll")
        self.assertContains(antwort, f'name="soll_{self.cola.pk}"')

        antwort = self.client.post(
            self.adresse,
            gesehen(self.inv)
            | {f"soll_{self.cola.pk}": SOLL_EIGENER_WERT, f"sollwert_{self.cola.pk}": "10"},
            follow=True,
        )
        self.assertContains(antwort, "1 Sollbestände von Hand gesetzt")
        self.assertEqual(self.ergebnis().sollbestand, Decimal("10"))

    def test_fehlerhafte_antwort_kommt_mit_der_wahl_zurueck(self):
        antwort = self.client.post(
            self.adresse, gesehen(self.inv) | {f"soll_{self.cola.pk}": SOLL_EIGENER_WERT}
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertContains(antwort, "Abgeschlossen wurde nichts")
        eintrag = antwort.context["klaerung"].ohne_soll[0]
        self.assertEqual(eintrag.antwort, SOLL_EIGENER_WERT)


class FremderBehaelter(Klaerung):
    """The seventh block: weighed in a container belonging neither to the article nor the house.

    This is how it comes about: the app offers the containers of **other
    houses** last. Whoever reaches there shows that the assignment is wrong.
    """

    def setUp(self):
        super().setUp()
        self.zaehlen()
        gramm = Basiseinheit.objects.get(name="g")
        self.nachos_ware = Ware.objects.create(name="Artikel N", basiseinheit=gramm, abwiegbar=True)
        self.nachos = Hausartikel.objects.create(
            haus=self.haus,
            ware=self.nachos_ware,
            warengruppe=Warengruppe.objects.get(name="Warengruppe G"),
            artikelnummer="250001",
            name="Artikel N",
        )
        self.wanne = Behaelter.objects.create(name="Wanne", tara=Decimal("900"))
        self.wanne.haeuser.add(self.zweites)
        self.waage = Zaehlposition.objects.create(
            inventur=self.inv, ort=self.ort, artikel=self.nachos, unerwartet=True
        )

    def wiegen(self, behaelter, brutto="1900"):
        return Wiegung.objects.create(
            zaehlposition=self.waage, brutto_gramm=Decimal(brutto), behaelter=behaelter
        )

    def schluessel(self) -> str:
        return f"w{self.nachos_ware.pk}-{self.wanne.pk}"

    def test_gefragt_wird_nach_dem_geliehenen_behaelter(self):
        self.wiegen(self.wanne)
        self.wiegen(self.wanne, "1500")
        (eintrag,) = klaeren(self.inv).fremde_behaelter
        self.assertEqual((eintrag.traeger, eintrag.behaelter), (self.nachos_ware, self.wanne))
        self.assertEqual(
            eintrag.wiegungen, 2, "Eine Frage je Artikel und Behälter, nicht je Schale."
        )
        self.assertEqual(eintrag.haeuser, "Kino Süd")

    def test_was_zugeordnet_ist_oder_ueberall_gilt_steht_nicht_da(self):
        eigener = Behaelter.objects.create(name="Schale", tara=Decimal("250"))
        self.nachos_ware.behaelter.add(eigener)
        im_haus = Behaelter.objects.create(name="Eimer", tara=Decimal("300"))
        im_haus.haeuser.add(self.haus)
        for behaelter in (eigener, im_haus, Behaelter.ohne_behaelter()):
            self.wiegen(behaelter)
        self.assertEqual(klaeren(self.inv).fremde_behaelter, [])

    def test_eine_null_im_fremden_behaelter_fragt_nicht(self):
        """0 g means "nothing to weigh". The vessel did not matter."""
        self.wiegen(self.wanne, "0")
        self.assertEqual(klaeren(self.inv).fremde_behaelter, [])

    def test_ohne_antwort_bleibt_die_zuordnung(self):
        self.wiegen(self.wanne)
        bericht = abschliessen(self.inv, Entscheidungen())
        self.assertEqual(bericht.behaelter_zugeordnet, 0)
        self.assertFalse(self.nachos_ware.behaelter.exists())
        self.assertEqual(list(self.wanne.haeuser.all()), [self.zweites])

    def abschliessen_mit(self, antwort: str):
        """Tests of their own instead of 'subTest': every answer writes and closes the inventory."""
        self.wiegen(self.wanne)
        bericht = abschliessen(self.inv, Entscheidungen(behaelter={self.schluessel(): antwort}))
        self.assertEqual(bericht.behaelter_zugeordnet, 1)
        # The weighing applied in every case: 1900 g gross, 900 g tare.
        ergebnis = self.inv.ergebnisse.get(hausartikel=self.nachos)
        self.assertEqual(ergebnis.gesamtmenge_basiseinheit, Decimal("1000"))

    def test_dem_artikel_zuordnen(self):
        self.abschliessen_mit(DEM_ARTIKEL)
        self.assertEqual(list(self.nachos_ware.behaelter.all()), [self.wanne])
        self.assertEqual(list(self.wanne.haeuser.all()), [self.zweites])

    def test_dem_haus_zuordnen(self):
        self.abschliessen_mit(DEM_HAUS)
        self.assertCountEqual(self.wanne.haeuser.all(), [self.haus, self.zweites])
        self.assertFalse(self.nachos_ware.behaelter.exists())

    def test_die_seite_zeigt_den_block_und_nimmt_die_antwort(self):
        self.wiegen(self.wanne)
        adresse = reverse("inventur_abschluss", args=[self.inv.pk])
        seite = self.client.get(adresse)
        self.assertContains(seite, "In einem fremden Behälter gewogen")
        self.assertContains(seite, f'name="behaelter_{self.schluessel()}"')

        antwort = self.client.post(
            adresse, gesehen(self.inv) | {f"behaelter_{self.schluessel()}": DEM_HAUS}, follow=True
        )
        self.assertContains(antwort, "1 Behälter zugeordnet")
        self.assertIn(self.haus, self.wanne.haeuser.all())


class WiegenJeOrt(Klaerung):
    """The location's counting list says whether weighing happens, and the clarification updates it.

    Artikel N lies at the counter in the box **and** in the bowl, both expected.
    """

    def setUp(self):
        super().setUp()
        self.zaehlen()
        gramm = Basiseinheit.objects.get(name="g")
        self.nachos_ware = Ware.objects.create(name="Artikel N", basiseinheit=gramm, abwiegbar=True)
        self.nachos = Hausartikel.objects.create(
            haus=self.haus,
            ware=self.nachos_ware,
            warengruppe=Warengruppe.objects.get(name="Warengruppe G"),
            artikelnummer="250001",
            name="Artikel N",
        )
        self.karton = Gebinde.objects.create(
            ware=self.nachos_ware, name="Karton", faktor_in_basiseinheit=Decimal("5000")
        )
        self.erwartung = Erwartung.objects.create(
            ort=self.ort, artikel=self.nachos, wiegen_erwartet=True
        )
        ErwartungGebinde.objects.create(erwartung=self.erwartung, gebinde=self.karton)
        self.inv.zaehlpositionen_anlegen()
        self.kartonzeile = self.inv.zaehlpositionen.get(artikel=self.nachos, gebinde=self.karton)
        self.kartonzeile.eingabemenge = Decimal("2")
        self.kartonzeile.save(update_fields=["eingabemenge"])
        self.waage = self.inv.zaehlpositionen.get(artikel=self.nachos, gebinde=None)

    def wiegen(self, position=None):
        return Wiegung.objects.create(
            zaehlposition=position or self.waage,
            brutto_gramm=Decimal("1200"),
            behaelter=Behaelter.ohne_behaelter(),
        )

    def test_leere_waage_wird_zum_herausnehmen_angeboten(self):
        (eintrag,) = klaeren(self.inv).ungezaehlte_gebinde
        self.assertIsNone(eintrag.vorgabe)
        self.assertEqual(eintrag.bezeichnung, "Wiegen")
        self.assertEqual(eintrag.schluessel, f"wiegen-{self.erwartung.pk}")

    def test_wo_gewogen_wurde_ist_nichts_zu_fragen(self):
        self.wiegen()
        self.assertEqual(klaeren(self.inv).ungezaehlte_gebinde, [])

    def test_ohne_gebinde_bleibt_die_waage_die_einzige_zeile(self):
        """Then the question would be "take the article out?", and another block asks that."""
        self.kartonzeile.delete()
        self.erwartung.gebinde_vorgaben.all().delete()
        self.assertEqual(klaeren(self.inv).ungezaehlte_gebinde, [])

    def test_neben_einem_inaktiven_gebinde_bleibt_die_waage_die_einzige_zeile(self):
        """The pack gets no row in the next inventory. Without the scale nothing would be left."""
        Gebinde.objects.filter(pk=self.karton.pk).update(aktiv=False)
        self.assertEqual(klaeren(self.inv).ungezaehlte_gebinde, [])

    def test_herausnehmen_schaltet_das_wiegen_am_ort_ab(self):
        bericht = abschliessen(
            self.inv, Entscheidungen(herausnehmen={f"wiegen-{self.erwartung.pk}"})
        )
        self.erwartung.refresh_from_db()
        self.assertFalse(self.erwartung.wiegen_erwartet)
        self.assertEqual(bericht.herausgenommen, 1)
        # The pack stays. Only the scale was taken out.
        self.assertTrue(self.erwartung.gebinde_vorgaben.exists())

    def test_unerwartet_gewogen_bietet_das_wiegen_an(self):
        self.waage.delete()
        Erwartung.objects.filter(pk=self.erwartung.pk).update(wiegen_erwartet=False)
        self.wiegen(self.unerwartet(self.nachos, menge="0"))
        Zaehlposition.objects.filter(artikel=self.nachos, gebinde=None).update(eingabemenge=None)

        (eintrag,) = klaeren(self.inv).neue_erwartungen
        self.assertTrue(eintrag.gewogen)
        self.assertTrue(eintrag.artikel_schon_erwartet)

        abschliessen(self.inv, Entscheidungen(aufnehmen={eintrag.schluessel}))
        self.erwartung.refresh_from_db()
        self.assertTrue(self.erwartung.wiegen_erwartet)

    def test_eine_null_auf_der_erwarteten_waage_ist_gezaehlt(self):
        """Nothing was open for weighing: then the scale should not be up for removal."""
        Wiegung.objects.create(
            zaehlposition=self.waage,
            brutto_gramm=Decimal("0"),
            behaelter=Behaelter.ohne_behaelter(),
        )
        self.assertEqual(klaeren(self.inv).ungezaehlte_gebinde, [])

    def test_nur_nullen_gewogen_bietet_das_wiegen_nicht_an(self):
        self.waage.delete()
        Erwartung.objects.filter(pk=self.erwartung.pk).update(wiegen_erwartet=False)
        position = self.unerwartet(self.nachos, menge="0")
        Wiegung.objects.create(
            zaehlposition=position, brutto_gramm=Decimal("0"), behaelter=Behaelter.ohne_behaelter()
        )
        Zaehlposition.objects.filter(artikel=self.nachos, gebinde=None).update(eingabemenge=None)
        self.assertEqual(klaeren(self.inv).neue_erwartungen, [])

    def test_erwartetes_wiegen_wird_nicht_noch_einmal_angeboten(self):
        """An unexpected weighing row whose location expects weighing by now."""
        self.waage.unerwartet = True
        self.waage.save(update_fields=["unerwartet"])
        self.wiegen()
        self.assertEqual(klaeren(self.inv).neue_erwartungen, [])

    def test_nur_als_karton_gefunden_wird_ohne_wiegen_aufgenommen(self):
        lager = Ort.objects.create(
            name="Lager", bereich=Bereich.objects.create(name="Lager", haus=self.haus)
        )
        self.wiegen()
        self.unerwartet(self.nachos, self.karton, ort=lager)
        (eintrag,) = klaeren(self.inv).neue_erwartungen

        abschliessen(self.inv, Entscheidungen(aufnehmen={eintrag.schluessel}))
        self.assertFalse(Erwartung.objects.get(ort=lager, artikel=self.nachos).wiegen_erwartet)

    def test_auf_der_waage_gefunden_wird_mit_wiegen_aufgenommen(self):
        lager = Ort.objects.create(
            name="Lager", bereich=Bereich.objects.create(name="Lager", haus=self.haus)
        )
        self.wiegen()
        position = self.unerwartet(self.nachos, ort=lager, menge="0")
        self.wiegen(position)
        Zaehlposition.objects.filter(pk=position.pk).update(eingabemenge=None)
        (eintrag,) = klaeren(self.inv).neue_erwartungen

        abschliessen(self.inv, Entscheidungen(aufnehmen={eintrag.schluessel}))
        self.assertTrue(Erwartung.objects.get(ort=lager, artikel=self.nachos).wiegen_erwartet)

    def test_die_seite_nennt_das_wiegen(self):
        antwort = self.client.get(reverse("inventur_abschluss", args=[self.inv.pk]))
        self.assertContains(antwort, f'value="wiegen-{self.erwartung.pk}"')


class BehaelterAmModell(Klaerung):
    def test_eine_wiegung_nimmt_nur_was_zur_wahl_steht(self):
        """The same rule as at the API, for the administration too."""
        gramm = Basiseinheit.objects.get(name="g")
        ware = Ware.objects.create(name="Artikel N", basiseinheit=gramm, abwiegbar=True)
        artikel = Hausartikel.objects.create(
            haus=self.haus,
            ware=ware,
            warengruppe=Warengruppe.objects.get(name="Warengruppe G"),
            artikelnummer="250001",
            name="Artikel N",
        )
        position = Zaehlposition.objects.create(
            inventur=self.inv, ort=self.ort, artikel=artikel, unerwartet=True
        )
        flasche = Behaelter.objects.create(name="Druckflasche", tara=Decimal("120"))
        with self.assertRaises(ValidationError) as ausnahme:
            Wiegung(
                zaehlposition=position, brutto_gramm=Decimal("500"), behaelter=flasche
            ).full_clean()
        self.assertIn("behaelter", ausnahme.exception.message_dict)
        Wiegung(
            zaehlposition=position,
            brutto_gramm=Decimal("500"),
            behaelter=Behaelter.ohne_behaelter(),
        ).full_clean()
