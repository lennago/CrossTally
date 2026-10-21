"""Import from the finished loader table.

The table comes as 'scripts.loader' delivers it: seven columns, the count
as float (the raw export brings "24,000", transform.py makes 24.0 of it),
missing entries as NaN.
"""

from decimal import Decimal

import pandas as pd
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from django_project.testing import jahresinventur
from organisation.models import Bereich, Haus, Ort
from scripts import normalize

from .formats.winticket import WinTicket
from .models import (
    Artikelstatus,
    Basiseinheit,
    Dimension,
    Erwartung,
    Gebinde,
    Hausartikel,
    Rezepturartikel,
    Rezepturkomponente,
    Ware,
    Warenfamilie,
    Warengruppe,
)
from .services import (
    AKTIV,
    AKTUALISIEREN,
    EINGESTELLT,
    EINSTELLEN,
    ERSETZEN,
    FEHLT,
    GEBINDE,
    IGNORIEREN,
    INAKTIV,
    KONFLIKT,
    NEU,
    UEBERGEHEN,
    UMBENANNT,
    WARENGRUPPE,
    ZURUECKHOLEN,
    Planzeile,
    als_daten,
    ausfuehren,
    gebinde_lesen,
    menge_text,
    planen,
)

#: The ids as they stand in the table after normalisation.
STUECK = normalize.STUECK
LITER = normalize.LITER

SPALTEN = ["artikelnummer", "name", "warengruppe", "gebinde", "anzahl", "einheit", "haus"]


def zeile(nummer, name, gebinde, anzahl, einheit=STUECK, warengruppe="Warengruppe G"):
    """A row as it comes from normalisation: 'einheit' is the id."""
    return {
        "artikelnummer": nummer,
        "name": name,
        "warengruppe": warengruppe,
        "gebinde": gebinde,
        "anzahl": anzahl,
        "einheit": einheit,
        "haus": "Kino Ost",
    }


class ImportAnlegen(TestCase):
    """The plan, adopted unchanged: the standard case on the first import."""

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Ost")
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)

    def importieren(self, *zeilen):
        plan, _ = planen(pd.DataFrame(list(zeilen), columns=SPALTEN), self.haus)
        return ausfuehren([als_daten(satz) for satz in plan], self.haus)

    def test_artikel_mit_anzahl_wird_aktiv(self):
        self.importieren(zeile("101001", "Artikel C 0,33", "Kasten", 24.0))
        artikel = Hausartikel.objects.get(artikelnummer="101001")
        self.assertTrue(artikel.aktiv)
        self.assertIsNone(artikel.gueltig_bis)
        # To the box comes the loose single unit, because counted goods stand
        # opened on the shelf.
        self.assertEqual(
            dict(artikel.ware.gebinde.values_list("name", "faktor_in_basiseinheit")),
            {"Kasten": Decimal("24.000"), "Einzeln": Decimal("1.000")},
        )

    def test_anzahl_null_wird_eingestellt_angelegt(self):
        """The case from the file: 'gekauft in Karton zu 0 Stück(e)', a dead entry."""
        bericht = self.importieren(zeile("101002", "Karteileiche", "Karton", 0.0))
        artikel = Hausartikel.objects.get(artikelnummer="101002")
        self.assertEqual(artikel.status, Artikelstatus.EINGESTELLT)
        self.assertIsNotNone(artikel.gueltig_bis)
        self.assertEqual(bericht.artikel_eingestellt, ["101002 Karteileiche"])
        # From a 0 no pack arises. There is nothing to convert.
        self.assertFalse(artikel.ware.gebinde.exists())

    def test_fehlende_anzahl_wird_eingestellt_angelegt(self):
        self.importieren(zeile("101003", "Ohne Angabe", "", float("nan")))
        self.assertEqual(
            Hausartikel.objects.get(artikelnummer="101003").status, Artikelstatus.EINGESTELLT
        )

    def test_eingestellt_angelegt_belegt_weder_nummer_noch_ware(self):
        """That is why it is the suggestion for dead entries. They are in nobody's way."""
        self.importieren(zeile("101002", "Karteileiche", "Karton", 0.0))
        ware = Ware.objects.get(name="Karteileiche")
        Hausartikel.objects.create(
            haus=self.haus,
            ware=ware,
            warengruppe=Warengruppe.objects.get(name="Warengruppe G"),
            artikelnummer="101002",
            name="Echte Ware",
        )

    def test_eine_zeile_mit_anzahl_genuegt(self):
        """Several packs per article: one maintained row makes the article active."""
        self.importieren(
            zeile("101004", "Artikel N", "Karton", 0.0),
            zeile("101004", "Artikel N", "Beutel", 6.0),
        )
        self.assertTrue(Hausartikel.objects.get(artikelnummer="101004").aktiv)

    def test_zweiter_import_verdoppelt_die_karteileiche_nicht(self):
        """On the second run it stands under "Discontinued", with "Leave" as suggestion."""
        for _ in range(2):
            self.importieren(zeile("101005", "Karteileiche", "Karton", 0.0))
        self.assertEqual(Hausartikel.objects.filter(artikelnummer="101005").count(), 1)

    def test_eingestellter_satz_wird_nicht_wieder_angelegt(self):
        """The partial index does not exist for discontinued rows. The import has to watch out."""
        self.importieren(zeile("101005", "Alte Ware", "Karton", 6.0))
        Hausartikel.objects.get().einstellen()
        self.importieren(zeile("101005", "Alte Ware", "Karton", 6.0))
        self.assertEqual(Hausartikel.objects.filter(artikelnummer="101005").count(), 1)

    def test_ersetzen_stellt_den_alten_satz_ein(self):
        self.importieren(zeile("101008", "Artikel C alt", "Kasten", 24.0))
        plan, _ = planen(
            pd.DataFrame([zeile("101008", "Artikel F neu", "Kasten", 20.0)], columns=SPALTEN),
            self.haus,
        )
        self.assertEqual(plan[0].art, UMBENANNT)
        daten = als_daten(plan[0]) | {"vorgehen": ERSETZEN, "ware": None}
        bericht = ausfuehren([daten], self.haus)
        self.assertEqual(bericht.artikel_ersetzt, ["101008"])
        alt = Hausartikel.objects.get(name="Artikel C alt")
        self.assertEqual(alt.status, Artikelstatus.EINGESTELLT)
        self.assertTrue(Hausartikel.objects.get(name="Artikel F neu").aktiv)

    def test_ersetzen_legt_den_neuen_mit_dem_gewaehlten_status_an(self):
        """The same choice as for a new article. The old one is discontinued in any case."""
        for nummer, status_neu, erwartet in (
            ("101011", INAKTIV, Artikelstatus.INAKTIV),
            ("101012", EINSTELLEN, Artikelstatus.EINGESTELLT),
            ("101013", IGNORIEREN, Artikelstatus.IGNORIERT),
        ):
            with self.subTest(status_neu=status_neu):
                self.importieren(zeile(nummer, f"Alt {nummer}", "Kasten", 24.0))
                plan, _ = planen(
                    pd.DataFrame([zeile(nummer, f"Neu {nummer}", "Kasten", 20.0)], columns=SPALTEN),
                    self.haus,
                )
                daten = als_daten(plan[0]) | {
                    "vorgehen": ERSETZEN,
                    "status_neu": status_neu,
                    "ware": None,
                }
                ausfuehren([daten], self.haus)
                alt = Hausartikel.objects.get(name=f"Alt {nummer}")
                self.assertEqual(alt.status, Artikelstatus.EINGESTELLT)
                self.assertEqual(Hausartikel.objects.get(name=f"Neu {nummer}").status, erwartet)

    def test_ersetzen_durch_einen_zwilling_laesst_den_alten_stehen(self):
        """Otherwise the number would stand without a carried article in the end."""
        self.importieren(zeile("101008", "Artikel C alt", "Kasten", 24.0))
        alt = Hausartikel.objects.get(name="Artikel C alt")
        daten = als_daten(
            Planzeile(
                artikelnummer="101008",
                name="Artikel C alt",
                warengruppe="Warengruppe G",
                einheit=STUECK,
                art=UMBENANNT,
                vorgehen=ERSETZEN,
                vorhanden_pk=alt.pk,
            )
        )
        bericht = ausfuehren([daten], self.haus)
        self.assertEqual(len(bericht.fehler), 1)
        alt.refresh_from_db()
        self.assertTrue(alt.aktiv)

    def test_warengruppe_ist_aktiv_sobald_ein_artikel_aktiv_ist(self):
        self.importieren(
            zeile("101006", "Leiche", "Karton", 0.0, warengruppe="Warengruppe U"),
            zeile("101007", "Echte Ware", "Karton", 12.0, warengruppe="Warengruppe U"),
        )
        gruppe = Warengruppe.objects.get(name="Warengruppe U")
        self.assertTrue(gruppe.aktiv)

        self.assertEqual(gruppe.deaktivieren(), 1)
        self.assertFalse(gruppe.aktiv)
        self.assertFalse(Hausartikel.objects.filter(warengruppe=gruppe, aktiv=True).exists())


class ImportplanTests(TestCase):
    """The import only writes once the plan is confirmed."""

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino West")
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)

    def planen(self, *zeilen):
        plan, _ = planen(pd.DataFrame(list(zeilen), columns=SPALTEN), self.haus)
        return plan

    def test_eine_dublette_steht_nicht_im_plan(self):
        """Discontinued on purpose: "bring back?" would stand there on every import."""
        gruppe = Warengruppe.objects.create(name="Warengruppe G")
        ware = Ware.objects.create(name="Artikel C 0,33", basiseinheit=self.stueck)
        bleibt = Hausartikel.objects.create(
            haus=self.haus, ware=ware, warengruppe=gruppe, artikelnummer="101001", name="Artikel C"
        )
        Hausartikel.objects.create(
            haus=self.haus,
            ware=ware,
            warengruppe=gruppe,
            artikelnummer="101002",
            name="Artikel C Dose",
            status=Artikelstatus.EINGESTELLT,
            zaehlt_als=bleibt,
        )
        plan, meldungen = planen(
            pd.DataFrame([zeile("101002", "Artikel C Dose", "Kasten", 24.0)], columns=SPALTEN),
            self.haus,
        )
        self.assertNotIn("101002", [satz.artikelnummer for satz in plan])
        self.assertTrue(any("eingestellte Dubletten" in str(text) for text in meldungen))

    def test_planen_schreibt_nichts(self):
        plan = self.planen(zeile("101001", "Artikel C 0,33", "Kasten", 24.0))
        self.assertEqual(len(plan), 1)
        self.assertFalse(Hausartikel.objects.exists())
        self.assertFalse(Ware.objects.echte().exists())
        self.assertFalse(Warengruppe.objects.exists())

    def test_zeile_mit_menge_wird_aktiv_vorgeschlagen(self):
        satz = self.planen(zeile("101001", "Artikel C 0,33", "Kasten", 24.0))[0]
        self.assertEqual(satz.vorgehen, AKTIV)
        self.assertEqual(satz.gebinde, {"Kasten": Decimal("24.000"), "Einzeln": Decimal("1.000")})
        self.assertEqual(satz.gebindetext, "Kasten = 24\nEinzeln = 1")

    def test_zeile_ohne_menge_wird_eingestellt_vorgeschlagen(self):
        satz = self.planen(zeile("101002", "Karteileiche", "Karton", 0.0))[0]
        self.assertEqual(satz.vorgehen, EINSTELLEN)
        self.assertIn("keine Anzahl je Gebinde", " ".join(satz.hinweise))

    def test_eine_nummer_unter_zwei_warengruppen_faellt_auf(self):
        """The first row applies, but the second product group must not drop away silently."""
        satz = self.planen(
            zeile("101001", "Artikel C 0,33", "Kasten", 24.0, warengruppe="Warengruppe G"),
            zeile("101001", "Artikel C 0,33", "Einzeln", 1.0, warengruppe="Warengruppe H"),
        )[0]
        self.assertEqual(satz.warengruppe, "Warengruppe G")
        self.assertIn(
            "mehreren Warengruppen: Warengruppe G, Warengruppe H", " ".join(satz.hinweise)
        )

    def test_eine_nummer_mit_zwei_einheiten_faellt_auf(self):
        satz = self.planen(
            zeile("101001", "Artikel C 0,33", "Kasten", 24.0),
            zeile("101001", "Artikel C 0,33", "Karton", 6.0, einheit="LITER"),
        )[0]
        self.assertIn("mehreren Einheiten", " ".join(satz.hinweise))

    def test_zwei_zeilen_derselben_gruppe_sind_kein_hinweis(self):
        satz = self.planen(
            zeile("101001", "Artikel C 0,33", "Kasten", 24.0),
            zeile("101001", "Artikel C 0,33", "Karton", 6.0),
        )[0]
        self.assertEqual([h for h in satz.hinweise if "mehreren" in str(h)], [])

    def test_unbekannte_einheit_wird_gemeldet(self):
        """Not creating is not available. The row does not even get into the plan."""
        plan, meldungen = planen(
            pd.DataFrame(
                [zeile("101003", "Merkwürdig", "Karton", 5.0, einheit="Furlong")], columns=SPALTEN
            ),
            self.haus,
        )
        self.assertEqual(plan, [])
        self.assertIn("unbekannte Einheit „Furlong“", meldungen[0])

    def test_vorhandener_artikel_wird_erkannt_und_verglichen(self):
        gruppe = Warengruppe.objects.create(name="Warengruppe G")
        sammel = Ware.objects.create(name="Artikel C 0,33", basiseinheit=self.stueck)
        alt = Hausartikel.objects.create(
            haus=self.haus,
            ware=sammel,
            warengruppe=gruppe,
            artikelnummer="101001",
            name="Artikel C alt",
        )
        satz = self.planen(zeile("101001", "Artikel C neu", "Kasten", 24.0))[0]
        self.assertEqual(satz.art, UMBENANNT)
        self.assertEqual(satz.vorhanden_pk, alt.pk)
        self.assertEqual(satz.vorgehen, UEBERGEHEN)
        self.assertEqual(satz.status_neu, AKTIV)
        self.assertIn("Bezeichnung bisher „Artikel C alt“", " ".join(satz.hinweise))

    def test_eine_zweite_bezeichnung_derselben_nummer_wird_genannt(self):
        """The first applies, as for the product group. Until 10/2026 the second went unsaid."""
        satz = self.planen(
            zeile("101001", "Artikel A", "Kasten", 24.0),
            zeile("101001", "Artikel B", "Karton", 12.0),
        )[0]
        self.assertEqual(satz.name, "Artikel A")
        self.assertIn(
            "Die Nummer steht in der Datei unter mehreren Bezeichnungen: „Artikel A“, "
            "„Artikel B“. Es gilt die erste.",
            satz.hinweise,
        )

    def test_die_vorgewaehlte_ware_des_bisherigen_satzes_will_geprueft_sein(self):
        """For "Rename" right, a reassigned number mostly belongs to another product."""
        gruppe = Warengruppe.objects.create(name="Warengruppe G")
        alt_ware = Ware.objects.create(name="Artikel C 0,33", basiseinheit=self.stueck)
        Hausartikel.objects.create(
            haus=self.haus,
            ware=alt_ware,
            warengruppe=gruppe,
            artikelnummer="101001",
            name="Artikel C alt",
        )
        satz = self.planen(zeile("101001", "Artikel M", "Kasten", 24.0))[0]
        self.assertEqual(satz.ware_pk, alt_ware.pk)
        self.assertIn("Für „Ersetzen“ bitte die Ware prüfen.", " ".join(satz.hinweise))

        neue = Ware.objects.create(name="Artikel M", basiseinheit=self.stueck)
        satz = self.planen(zeile("101001", "Artikel M", "Kasten", 24.0))[0]
        self.assertEqual(satz.ware_pk, neue.pk)
        self.assertNotIn("bitte die Ware prüfen", " ".join(satz.hinweise))

    def test_ohne_wahl_zaehlt_ein_neues_haus_die_ware_in_ihrer_art(self):
        """The row leaves the type empty: the rule decides, as with the form.

        The product is counted annually in Kino Ost. The new house counts it
        the same, and the house that already carries it stays as it is.
        """
        becher = Ware.objects.create(name="Artikel M", basiseinheit=self.stueck)
        Hausartikel.objects.create(
            haus=Haus.objects.create(name="Kino Ost"),
            ware=becher,
            warengruppe=Warengruppe.objects.create(name="Warengruppe N"),
            artikelnummer="400001",
            name="Artikel M",
            inventurart=jahresinventur(),
        )
        plan = self.planen(
            zeile("400001", "Artikel M", "Karton", 50.0, warengruppe="Warengruppe N")
        )
        self.assertEqual(plan[0].ware_pk, becher.pk)
        self.assertIsNone(plan[0].inventurart)

        ausfuehren([als_daten(satz) for satz in plan], self.haus)

        self.assertEqual(
            set(becher.hausartikel.values_list("inventurart", flat=True)), {jahresinventur().pk}
        )
        self.assertEqual(becher.hausartikel.count(), 2)

    def test_eine_gewaehlte_art_gilt_fuer_den_neuen_artikel(self):
        plan = self.planen(zeile("400002", "Artikel P", "Karton", 10.0))
        daten = [als_daten(satz) | {"inventurart": jahresinventur().pk} for satz in plan]
        ausfuehren(daten, self.haus)
        self.assertEqual(
            Hausartikel.objects.get(artikelnummer="400002").inventurart, jahresinventur()
        )

    def test_der_vorhandene_satz_bringt_seine_art_in_den_plan(self):
        alt = Hausartikel.objects.create(
            haus=self.haus,
            ware=Ware.objects.create(name="Artikel R", basiseinheit=self.stueck),
            warengruppe=Warengruppe.objects.create(name="Warengruppe R"),
            artikelnummer="400003",
            name="Artikel R alt",
            inventurart=jahresinventur(),
        )
        (satz,) = self.planen(zeile("400003", "Artikel R neu", "Karton", 10.0))
        self.assertEqual((satz.vorhanden_pk, satz.inventurart), (alt.pk, jahresinventur().pk))

    def test_ware_schon_unter_anderer_nummer(self):
        gruppe = Warengruppe.objects.create(name="Warengruppe G")
        sammel = Ware.objects.create(name="Artikel C 0,33", basiseinheit=self.stueck)
        Hausartikel.objects.create(
            haus=self.haus,
            ware=sammel,
            warengruppe=gruppe,
            artikelnummer="999999",
            name="Artikel C 0,33",
        )
        satz = self.planen(zeile("101001", "Artikel C 0,33", "Kasten", 24.0))[0]
        # Same designation, other number: new as a house article, only the
        # product is taken, and therefore not preselected.
        self.assertEqual(satz.vorgehen, AKTIV)
        self.assertIsNone(satz.ware_pk)
        self.assertIn("bereits unter 999999", " ".join(satz.hinweise))
        self.assertEqual(satz.gebindetext, "Kasten = 24\nEinzeln = 1")

    def test_gleiche_bezeichnung_zweimal_in_der_datei(self):
        """The "Beispielartikel" exists as sales and as purchase article: two products."""
        erste, zweite = self.planen(
            zeile("920001", "Beispielartikel", "Stück", 1.0),
            zeile("920002", "Beispielartikel", "Karton", 12.0),
        )
        self.assertEqual(erste.hinweise, [])
        self.assertEqual(zweite.vorgehen, AKTIV)
        self.assertIn("belegt in dieser Datei schon 920001", " ".join(zweite.hinweise))

    def test_zeile_ohne_artikelnummer_wird_gemeldet(self):
        """Subtotals and headings: they must not vanish silently."""
        tabelle = pd.DataFrame(
            [
                zeile("Summe: VK - Warenfamilie E", "", "", 0.0),
                zeile("101001", "Artikel C 0,33", "Kasten", 24.0),
            ],
            columns=SPALTEN,
        )
        plan, uebergangen = planen(tabelle, self.haus)
        self.assertEqual([satz.artikelnummer for satz in plan], ["101001"])
        self.assertEqual(len(uebergangen), 1)
        self.assertIn("keine Artikelnummer, wie WinTICKET", uebergangen[0])

    def test_kurze_artikelnummer_bleibt_kurz(self):
        """WinTICKET also carries five-digit numbers. Padded it would look different from there."""
        satz = self.planen(zeile("1234", "Kurz", "Karton", 1.0))[0]
        self.assertEqual(satz.artikelnummer, "1234")

    def test_planen_fragt_nicht_je_zeile(self):
        """On the first import of a house it is over a thousand rows."""
        bekannte = 3
        Warengruppe.objects.create(name="Warengruppe G")
        for i in range(bekannte):
            Ware.objects.create(name=f"Bekannt {i}", basiseinheit=self.stueck)

        def abfragen(anzahl):
            zeilen = [
                zeile(
                    f"{500000 + i}", f"Bekannt {i}" if i < bekannte else f"Neu {i}", "Kasten", 10.0
                )
                for i in range(anzahl)
            ]
            with CaptureQueriesContext(connection) as gezaehlt:
                self.planen(*zeilen)
            return len(gezaehlt)

        self.assertEqual(abfragen(4), abfragen(40))

    def test_widersprechende_mengen_werden_gemeldet(self):
        satz = self.planen(
            zeile("101001", "Artikel C 0,33", "Kasten", 24.0),
            zeile("101001", "Artikel C 0,33", "Kasten", 20.0),
        )[0]
        self.assertIn("mehreren Mengen", " ".join(satz.hinweise))
        self.assertEqual(satz.gebinde["Kasten"], Decimal("24.000"))


class ImportIgnorierte(TestCase):
    """What Compeso carries as an article without it being one."""

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Ost")
        cls.anderes = Haus.objects.create(name="Kino Süd")

    def planen(self, *zeilen, haus=None):
        return planen(pd.DataFrame(list(zeilen), columns=SPALTEN), haus or self.haus)

    def importieren(self, *zeilen, haus=None, **abweichend):
        plan, _ = self.planen(*zeilen, haus=haus)
        return ausfuehren([als_daten(satz) | abweichend for satz in plan], haus or self.haus)

    def test_ignorieren_legt_die_ware_in_nicht_zaehlbar(self):
        bericht = self.importieren(zeile("901001", "Artikel P", "Stück", 1.0), vorgehen=IGNORIEREN)
        artikel = Hausartikel.objects.get()
        self.assertEqual(artikel.ware.name, "Artikel P")
        self.assertEqual(artikel.ware.warenfamilie, Warenfamilie.nicht_zaehlbar())
        self.assertEqual(artikel.status, Artikelstatus.IGNORIERT)
        self.assertEqual(bericht.artikel_ignoriert, ["901001 Artikel P"])
        # No real product, so no packs either.
        self.assertFalse(Ware.objects.echte().exists())
        self.assertFalse(Gebinde.objects.exists())

    def test_ignorierte_nummer_kommt_nicht_wieder_in_den_plan(self):
        self.importieren(zeile("901001", "Artikel P", "Stück", 1.0), vorgehen=IGNORIEREN)
        plan, meldungen = self.planen(
            zeile("901001", "Artikel P", "Stück", 1.0), zeile("101001", "Artikel C", "Kasten", 24.0)
        )
        self.assertEqual([satz.artikelnummer for satz in plan], ["101001"])
        self.assertIn("1 nicht zählbare Artikel übersprungen", meldungen[0])

    def test_neu_belegte_ignorierte_nummer_steht_im_plan(self):
        """The same number under another name may be a real product."""
        self.importieren(zeile("901001", "Artikel P", "Stück", 1.0), vorgehen=IGNORIEREN)
        plan, _ = self.planen(zeile("901001", "Artikel L", "Kasten", 12.0))
        self.assertEqual(plan[0].vorgehen, AKTIV)

    def test_anderswo_ignorierte_ware_wird_wiedererkannt(self):
        """Through the ordinary name match: the product is the same, and it is none."""
        self.importieren(zeile("901001", "Artikel P", "Stück", 1.0), vorgehen=IGNORIEREN)
        plan, _ = self.planen(zeile("800001", "ARTIKEL P", "Stück", 1.0), haus=self.anderes)
        self.assertEqual(plan[0].vorgehen, IGNORIEREN)
        self.assertEqual(plan[0].ware_pk, Ware.objects.get(name="Artikel P").pk)

        self.importieren(zeile("800001", "ARTIKEL P", "Stück", 1.0), haus=self.anderes)
        self.assertEqual(Ware.objects.count(), 1)
        self.assertEqual(Hausartikel.objects.get(haus=self.anderes).status, Artikelstatus.IGNORIERT)

    def test_vorschlag_auch_ohne_bekannte_einheit(self):
        self.importieren(zeile("901001", "Posten G", "Stück", 1.0), vorgehen=IGNORIEREN)
        plan, _ = self.planen(
            zeile("800002", "Posten G", "Stück", 1.0, einheit="Euro"), haus=self.anderes
        )
        self.assertEqual(plan[0].vorgehen, IGNORIEREN)

    def test_ware_in_einer_rezeptur_wird_nicht_ignoriert(self):
        """Otherwise the recipe article could no longer be resolved anywhere.

        The same rule applies on the product's button.
        """
        dip = Ware.objects.create(
            name="Artikel D", basiseinheit=Basiseinheit.objects.get(dimension=Dimension.STUECK)
        )
        portion = Rezepturartikel.objects.create(name="Portion D", basiseinheit=dip.basiseinheit)
        Rezepturkomponente.objects.create(
            rezepturartikel=portion, ware=dip, menge_in_basiseinheit=Decimal("1")
        )
        bericht = self.importieren(zeile("901003", "Artikel D", "Stück", 1.0), vorgehen=IGNORIEREN)
        self.assertEqual(len(bericht.fehler), 1)
        self.assertIn("Rezeptur", bericht.fehler[0])
        dip.refresh_from_db()
        self.assertFalse(dip.ist_nicht_zaehlbar)
        self.assertFalse(Hausartikel.objects.exists())

    def test_echte_gleichnamige_ware_wird_nicht_mit_ignoriert(self):
        """That would concern every house that carries it. The row stays, with a message."""
        self.importieren(zeile("101001", "Posten G", "Stück", 1.0), haus=self.anderes)
        plan, _ = self.planen(zeile("901002", "Posten G", "Stück", 1.0))
        daten = als_daten(plan[0]) | {"vorgehen": IGNORIEREN, "ware": None}
        bericht = ausfuehren([daten], self.haus)
        self.assertEqual(len(bericht.fehler), 1)
        self.assertIsNone(Ware.objects.get(name="Posten G").warenfamilie)
        self.assertFalse(Hausartikel.objects.filter(haus=self.haus).exists())

    def test_inaktiver_satz_gilt_als_bekannt(self):
        """Even inactive it holds number and designation. Without deviation it is not planned."""
        self.importieren(zeile("101001", "Artikel G", "Kasten", 6.0), vorgehen=INAKTIV)
        plan, meldungen = self.planen(zeile("101001", "Artikel G", "Kasten", 6.0))
        self.assertEqual(plan, [])
        self.assertIn("1 Artikel führt Kino Ost schon genau so", meldungen[0])


class ImportBekannte(TestCase):
    """Number and designation match: known, discontinued or in conflict."""

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Ost")
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        cls.ware = Ware.objects.create(name="Artikel C 0,33", basiseinheit=cls.stueck)
        Gebinde.objects.create(ware=cls.ware, name="Kasten", faktor_in_basiseinheit=Decimal("24"))
        cls.cola = Hausartikel.objects.create(
            haus=cls.haus,
            ware=cls.ware,
            warengruppe=cls.gruppe,
            artikelnummer="101001",
            name="Artikel C 0,33",
        )

    def planen(self, *zeilen):
        return planen(pd.DataFrame(list(zeilen), columns=SPALTEN), self.haus)

    def test_ohne_abweichung_nicht_im_plan(self):
        plan, meldungen = self.planen(zeile("101001", "Artikel C 0,33", "Kasten", 24.0))
        self.assertEqual(plan, [])
        self.assertIn("stehen nicht im Plan", meldungen[0])

    def test_menge_zaehlt_nicht_der_gebindename(self):
        """A "Kiste" of 24 is stored if the product knows a "Kasten" of 24."""
        plan, _ = self.planen(zeile("101001", "Artikel C 0,33", "Kiste", 24.0))
        self.assertEqual(plan, [])

    def test_ohne_anzahl_kein_konflikt(self):
        plan, _ = self.planen(zeile("101001", "Artikel C 0,33", "Kasten", 0.0))
        self.assertEqual(plan, [])

    def test_anzahl_nicht_hinterlegt_ist_ein_konflikt(self):
        plan, _ = self.planen(zeile("101001", "Artikel C 0,33", "Kasten", 20.0))
        satz = plan[0]
        self.assertEqual((satz.art, satz.vorgehen), (KONFLIKT, UEBERGEHEN))
        self.assertEqual(satz.vorhanden_pk, self.cola.pk)
        self.assertEqual(satz.ware_pk, self.ware.pk)
        self.assertIn("„Kasten = 20“", satz.hinweise[0])
        self.assertIn("Kasten = 24", satz.hinweise[0])
        self.assertEqual(satz.abweichungen, [GEBINDE])
        # The field shows everything the product carries. "Kasten" stays at 24,
        # the missing quantity stands next to it as a pack of its own.
        self.assertEqual(
            satz.gebinde,
            {
                "Kasten": Decimal("24.000"),
                "Kasten 20": Decimal("20.000"),
                "Einzeln": Decimal("1.000"),
            },
        )
        self.assertIn("„Kasten 20“ als eigenes Gebinde", " ".join(satz.hinweise))

    def test_unbekanntes_gebinde_steht_schon_im_feld(self):
        """If the name is free on the product, it stays as the file names it."""
        plan, _ = self.planen(
            zeile("101001", "Artikel C 0,33", "Kasten", 24.0),
            zeile("101001", "Artikel C 0,33", "Tray", 6.0),
        )
        self.assertEqual(
            plan[0].gebinde,
            {
                "Kasten": Decimal("24.000"),
                "Tray": Decimal("6.000"),
                "Einzeln": Decimal("1.000"),
            },
        )

        ausfuehren([als_daten(plan[0]) | {"vorgehen": AKTUALISIEREN}], self.haus)
        mengen = dict(self.ware.gebinde.values_list("name", "faktor_in_basiseinheit"))
        self.assertEqual(
            mengen,
            {"Kasten": Decimal("24"), "Tray": Decimal("6"), "Einzeln": Decimal("1")},
        )

    def test_nur_ein_teil_des_konflikts_laesst_sich_uebernehmen(self):
        """What is not adopted stays and is there again on the next import."""
        datei = zeile("101001", "Artikel C 0,33", "Tray", 6.0, warengruppe="Warengruppe M")
        plan, _ = self.planen(datei)
        self.assertEqual(plan[0].abweichungen, [WARENGRUPPE, GEBINDE])

        daten = als_daten(plan[0]) | {"vorgehen": AKTUALISIEREN, "uebernehmen": [GEBINDE]}
        ausfuehren([daten], self.haus)
        self.cola.refresh_from_db()
        self.assertEqual(self.cola.warengruppe.name, "Warengruppe G")
        self.assertTrue(self.ware.gebinde.filter(name="Tray").exists())
        plan, _ = self.planen(datei)
        self.assertEqual(plan[0].abweichungen, [WARENGRUPPE])

        daten = als_daten(plan[0]) | {"vorgehen": AKTUALISIEREN}
        ausfuehren([daten], self.haus)
        self.assertEqual(self.planen(datei)[0], [])

    def test_eine_neue_art_stellt_den_artikel_ans_ende_der_zaehlliste(self):
        ort = Ort.objects.create(
            name="Lager", bereich=Bereich.objects.create(name="Lager", haus=self.haus)
        )
        ware_d = Hausartikel.objects.create(
            haus=self.haus,
            ware=Ware.objects.create(name="Ware D", basiseinheit=self.stueck),
            warengruppe=self.gruppe,
            artikelnummer="101002",
            name="Ware D",
            inventurart=jahresinventur(),
        )
        Erwartung.objects.create(ort=ort, artikel=self.cola)
        Erwartung.objects.create(ort=ort, artikel=ware_d)

        plan, _ = self.planen(zeile("101001", "Artikel C 0,33", "Kasten", 20.0))
        daten = als_daten(plan[0]) | {"vorgehen": AKTUALISIEREN, "inventurart": jahresinventur().pk}
        ausfuehren([daten], self.haus)

        folge = Erwartung.objects.filter(ort=ort).order_by("sortierung")
        self.assertEqual([satz.artikel_id for satz in folge], [ware_d.pk, self.cola.pk])

    def test_nur_die_warengruppe_laesst_die_gebinde_liegen(self):
        plan, _ = self.planen(
            zeile("101001", "Artikel C 0,33", "Tray", 6.0, warengruppe="Warengruppe M")
        )
        daten = als_daten(plan[0]) | {"vorgehen": AKTUALISIEREN, "uebernehmen": [WARENGRUPPE]}
        ausfuehren([daten], self.haus)
        self.cola.refresh_from_db()
        self.assertEqual(self.cola.warengruppe.name, "Warengruppe M")
        self.assertFalse(self.ware.gebinde.filter(name="Tray").exists())

    def test_einheit_in_fremder_dimension_braucht_die_umrechnung(self):
        """Pieces against litres: without the factor the row stays as it was."""
        plan, _ = self.planen(zeile("101001", "Artikel C 0,33", "Kasten", 24.0, einheit=LITER))
        self.assertIn("Bitte die Umrechnung angeben", " ".join(plan[0].hinweise))
        bericht = ausfuehren([als_daten(plan[0]) | {"vorgehen": AKTUALISIEREN}], self.haus)
        self.assertIn("Ohne die Umrechnung", " ".join(bericht.fehler))
        self.cola.refresh_from_db()
        self.assertEqual(self.cola.basiseinheit.kennung, "STUECK")

        daten = als_daten(plan[0]) | {"vorgehen": AKTUALISIEREN, "faktor_zur_ware": Decimal("2")}
        ausfuehren([daten], self.haus)
        self.cola.refresh_from_db()
        self.assertEqual(self.cola.basiseinheit.kennung, "LITER")
        self.assertEqual(self.cola.faktor_zur_ware, Decimal("2"))
        # The product stays in pieces. The file's crate, 24 litres, is 48 pieces there.
        self.assertEqual(self.ware.basiseinheit.kennung, "STUECK")
        self.assertEqual(
            sorted(self.ware.gebinde.values_list("faktor_in_basiseinheit", flat=True)),
            [Decimal("24.000"), Decimal("48.000")],
        )

    def test_andere_einheit_ist_ein_konflikt(self):
        plan, _ = self.planen(zeile("101001", "Artikel C 0,33", "Kasten", 24.0, einheit=LITER))
        self.assertEqual(plan[0].art, KONFLIKT)
        # Both sides in the short form: the file says the id, the product is in
        # the database. The hint should be readable, not technical.
        self.assertIn("Einheit bisher „Stk.“, in der Datei „l“", plan[0].hinweise[0])

    def test_andere_warengruppe_ist_ein_konflikt_und_aendern_uebernimmt_sie(self):
        plan, _ = self.planen(
            zeile("101001", "Artikel C 0,33", "Kasten", 24.0, warengruppe="Warengruppe M")
        )
        self.assertEqual(plan[0].art, KONFLIKT)
        ausfuehren([als_daten(plan[0]) | {"vorgehen": AKTUALISIEREN}], self.haus)
        self.cola.refresh_from_db()
        self.assertEqual(self.cola.warengruppe.name, "Warengruppe M")
        # After that the file says nothing different any more.
        plan, _ = self.planen(
            zeile("101001", "Artikel C 0,33", "Kasten", 24.0, warengruppe="Warengruppe M")
        )
        self.assertEqual(plan, [])

    def test_konflikt_bei_einem_neuen_namen_ist_keiner(self):
        """Same number, other designation: that is the question of rename or replace."""
        plan, _ = self.planen(zeile("101001", "Artikel C 0,5", "Kasten", 20.0))
        self.assertEqual(plan[0].art, UMBENANNT)

    def test_eingestellter_laesst_sich_zurueckholen(self):
        self.cola.einstellen()
        plan, _ = self.planen(zeile("101001", "Artikel C 0,33", "Kasten", 24.0))
        satz = plan[0]
        self.assertEqual((satz.art, satz.vorgehen), (EINGESTELLT, UEBERGEHEN))
        bericht = ausfuehren([als_daten(satz) | {"vorgehen": ZURUECKHOLEN}], self.haus)
        self.assertEqual(bericht.artikel_zurueckgeholt, ["101001 Artikel C 0,33"])
        self.cola.refresh_from_db()
        self.assertTrue(self.cola.aktiv)

    def test_zurueckholen_meldet_was_das_modell_verweigert(self):
        self.cola.einstellen()
        Hausartikel.objects.create(
            haus=self.haus,
            ware=self.ware,
            warengruppe=self.gruppe,
            artikelnummer="101002",
            name="Artikel C neu",
        )
        plan, _ = self.planen(zeile("101001", "Artikel C 0,33", "Kasten", 24.0))
        self.assertIn("inzwischen unter 101002", " ".join(plan[0].hinweise))
        bericht = ausfuehren([als_daten(plan[0]) | {"vorgehen": ZURUECKHOLEN}], self.haus)
        self.assertEqual(len(bericht.fehler), 1)
        self.cola.refresh_from_db()
        self.assertEqual(self.cola.status, Artikelstatus.EINGESTELLT)

    def test_mehrere_mengen_in_der_datei_allein_sind_kein_konflikt(self):
        Gebinde.objects.create(ware=self.ware, name="Tray", faktor_in_basiseinheit=Decimal("12"))
        plan, _ = self.planen(
            zeile("101001", "Artikel C 0,33", "Kasten", 24.0),
            zeile("101001", "Artikel C 0,33", "Kasten", 12.0),
        )
        self.assertEqual(plan, [])


class GebindetextTests(SimpleTestCase):
    def test_lesen(self):
        faelle = (
            (
                "je Zeile, mit Leerraum",
                "Kasten = 24\n Flasche(n) = 1,5 ",
                {"Kasten": "24", "Flasche(n)": "1.5"},
            ),
            (
                "Aufzählung mit Semikolon",
                "Kasten = 20; Flasche(n) = 1",
                {"Kasten": "20", "Flasche(n)": "1"},
            ),
            (
                "Semikolon und Dezimalkomma",
                "Kasten = 1,5; Beutel = 2",
                {"Kasten": "1.5", "Beutel": "2"},
            ),
            # A comma separates too, only not before a digit.
            (
                "Aufzählung mit Komma",
                "Kasten = 20, Flasche(n) = 1",
                {"Kasten": "20", "Flasche(n)": "1"},
            ),
            # The comma in "1,5" does not separate, otherwise it would tear apart the quantity.
            (
                "das Dezimalkomma bleibt heil",
                "Kasten = 1,5, Beutel = 2",
                {"Kasten": "1.5", "Beutel": "2"},
            ),
            (
                "Zeilen und Trenner gemischt",
                "Kasten = 20; Karton = 6\nFlasche(n) = 1",
                {"Kasten": "20", "Karton": "6", "Flasche(n)": "1"},
            ),
            ("Komma ohne Leerzeichen", "Kasten = 20,Beutel = 6", {"Kasten": "20", "Beutel": "6"}),
        )
        for fall, text, erwartet in faelle:
            with self.subTest(fall=fall):
                gebinde, fehler = gebinde_lesen(text)
                self.assertEqual(
                    gebinde, {name: Decimal(menge) for name, menge in erwartet.items()}
                )
                self.assertEqual(fehler, [])

    def test_fehlerhafte_zeilen(self):
        """Without quantity, no number, the same name twice: every line reports individually."""
        gebinde, fehler = gebinde_lesen("Kasten\nKarton = null\nKiste = 2\nKiste = 3")
        self.assertEqual(len(fehler), 3)
        self.assertEqual(gebinde, {"Kiste": Decimal("2")})


class NamensregisterTests(TestCase):
    """An existing product is preselected on import."""

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Nord")
        stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.cola = Ware.objects.create(name="Artikel C 0,33", basiseinheit=stueck)
        cls.fassbrause = Ware.objects.create(name="Artikel F 0,33", basiseinheit=stueck)

    def gewaehlt(self, name: str):
        tabelle = pd.DataFrame([zeile("101001", name, "Kasten", 24.0)], columns=SPALTEN)
        return planen(tabelle, self.haus)[0][0].ware_pk

    def test_gleicher_name_ist_vorausgewaehlt(self):
        self.assertEqual(self.gewaehlt("Artikel C 0,33"), self.cola.pk)

    def test_auch_bei_anderer_schreibweise(self):
        """WinTICKET does not always write the same product the same way."""
        for schreibweise in ("ARTIKEL C 0,33", "artikel c 0,33", "Artikel C 0,33"):
            with self.subTest(name=schreibweise):
                self.assertEqual(self.gewaehlt(schreibweise), self.cola.pk)

    def test_unbekannter_name_bleibt_neu(self):
        self.assertIsNone(self.gewaehlt("Artikel S 0,33"))

    def test_treffer_haengt_nicht_an_anderen_zeilen(self):
        """The search used to find only what a row had written exactly."""
        tabelle = pd.DataFrame(
            [
                zeile("101001", "Artikel C 0,33", "Kasten", 24.0),
                zeile("101002", "artikel f 0,33", "Kasten", 6.0),
            ],
            columns=SPALTEN,
        )
        plan = {satz.artikelnummer: satz.ware_pk for satz in planen(tabelle, self.haus)[0]}
        self.assertEqual(plan, {"101001": self.cola.pk, "101002": self.fassbrause.pk})

    def test_uebernehmen_legt_keinen_zweiten_an(self):
        tabelle = pd.DataFrame([zeile("101001", "ARTIKEL C 0,33", "Kasten", 24.0)], columns=SPALTEN)
        ausfuehren([als_daten(satz) for satz in planen(tabelle, self.haus)[0]], self.haus)
        self.assertEqual(Ware.objects.filter(name__iexact="artikel c 0,33").count(), 1)
        self.assertEqual(Hausartikel.objects.get(artikelnummer="101001").ware, self.cola)


class MengenschreibweiseTests(TestCase):
    """Written with a comma, both are read."""

    def test_komma_statt_punkt_und_ohne_nullen(self):
        self.assertEqual(
            [menge_text(Decimal(wert)) for wert in ("24.000", "1.500", "0.140", "1000.000")],
            ["24", "1,5", "0,14", "1000"],
        )

    def test_angezeigtes_laesst_sich_zurueckleisen(self):
        """What the plan shows it has to be able to read in again."""
        satz = Planzeile(
            "101001",
            "Artikel C",
            "Warengruppe G",
            "Stück(e)",
            gebinde={"Kasten": Decimal("24.000"), "Beutel": Decimal("1.500")},
        )
        einzeilig = "; ".join(satz.gebindetext.splitlines())
        gelesen, fehler = gebinde_lesen(einzeilig)
        self.assertEqual(fehler, [])
        self.assertEqual(gelesen, satz.gebinde)


class GebinderegelnTests(TestCase):
    """Per product a quantity only once, and at most one pack for weighing."""

    @classmethod
    def setUpTestData(cls):
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.kg = Basiseinheit.objects.filter(dimension=Dimension.MASSE).first()

    def test_zweites_festgebinde_mit_gleicher_menge(self):
        sammel = Ware.objects.create(name="Artikel C", basiseinheit=self.stueck)
        Gebinde.objects.create(ware=sammel, name="Kasten", faktor_in_basiseinheit=Decimal("20"))
        zweites = Gebinde(ware=sammel, name="Kiste", faktor_in_basiseinheit=Decimal("20"))
        with self.assertRaises(ValidationError) as fehler:
            zweites.full_clean()
        self.assertIn("faktor_in_basiseinheit", fehler.exception.message_dict)

    def test_andere_menge_geht(self):
        sammel = Ware.objects.create(name="Artikel C", basiseinheit=self.stueck)
        Gebinde.objects.create(ware=sammel, name="Kasten", faktor_in_basiseinheit=Decimal("20"))
        Gebinde(ware=sammel, name="Sixpack", faktor_in_basiseinheit=Decimal("6")).full_clean()

    def test_gleiche_menge_bei_anderer_ware_geht(self):
        andere = Ware.objects.create(name="Artikel F", basiseinheit=self.stueck)
        cola = Ware.objects.create(name="Artikel C", basiseinheit=self.stueck)
        Gebinde.objects.create(ware=cola, name="Kasten", faktor_in_basiseinheit=Decimal("20"))
        Gebinde(ware=andere, name="Kasten", faktor_in_basiseinheit=Decimal("20")).full_clean()

    def test_abwiegen_geht_auch_ohne_masse_einheit(self):
        """Through the piece weight the scale converts into pieces."""
        stueckware = Ware.objects.create(name="Becher", basiseinheit=self.stueck, abwiegbar=True)
        stueckware.umrechnungen_setzen({Dimension.MASSE: Decimal("4.5")})
        stueckware.full_clean()
        self.assertEqual(stueckware.in_basiseinheit(Decimal("45")), Decimal("10"))

    def test_abwiegbare_ware_bleibt_bearbeitbar(self):
        nachos = Ware.objects.create(name="Artikel N", basiseinheit=self.kg, abwiegbar=True)
        nachos.name = "Artikel N lose"
        nachos.full_clean()


class ImportGebindefeld(TestCase):
    """The pack field shows what the product carries and what the file would add."""

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino West2")
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.sammel = Ware.objects.create(name="Artikel C 0,33", basiseinheit=cls.stueck)
        cls.kasten = Gebinde.objects.create(
            ware=cls.sammel, name="Kasten", faktor_in_basiseinheit=Decimal("20")
        )

    def planzeile(self, name="Artikel C 0,33", gebinde="Kasten", anzahl=24.0):
        tabelle = pd.DataFrame([zeile("101001", name, gebinde, anzahl)], columns=SPALTEN)
        return planen(tabelle, self.haus)[0][0]

    def test_feld_zeigt_die_gefuehrten_gebinde(self):
        satz = self.planzeile()
        self.assertEqual(satz.ware_pk, self.sammel.pk)
        self.assertEqual(satz.gebinde["Kasten"], Decimal("20.000"))

    def test_eine_neue_menge_kommt_unter_eigenem_namen_dazu(self):
        satz = self.planzeile()
        # "Kasten" is taken for 20 on the product. The 24 from the file
        # therefore gets a name of its own.
        self.assertEqual(satz.gebinde["Kasten 24"], Decimal("24.000"))
        self.assertIn("schon mit einer anderen Menge", " ".join(satz.hinweise))

    def test_stueckware_bekommt_das_lose_einzelstueck(self):
        self.assertEqual(self.planzeile().gebinde["Einzeln"], Decimal("1.000"))

    def test_uebernehmen_laesst_die_gefuehrte_menge_stehen(self):
        ausfuehren([als_daten(self.planzeile())], self.haus)
        self.kasten.refresh_from_db()
        self.assertEqual(self.kasten.faktor_in_basiseinheit, Decimal("20.000"))

    def test_uebernehmen_legt_die_neuen_mengen_an(self):
        bericht = ausfuehren([als_daten(self.planzeile())], self.haus)
        self.assertEqual(bericht.gebinde_neu, 2)
        self.assertEqual(
            sorted(self.sammel.gebinde.values_list("name", flat=True)),
            ["Einzeln", "Kasten", "Kasten 24"],
        )

    def test_das_einzelstueck_traegt_seinen_schalter(self):
        ausfuehren([als_daten(self.planzeile())], self.haus)
        self.assertTrue(self.sammel.gebinde.get(name="Einzeln").ist_einzeln)

    def test_neue_ware_bringt_ihre_gebinde_mit(self):
        satz = self.planzeile(name="Artikel S 0,33")
        self.assertIsNone(satz.ware_pk)
        self.assertEqual(satz.gebinde, {"Kasten": Decimal("24.000"), "Einzeln": Decimal("1.000")})


class ImportGebindeAbgleich(TestCase):
    """The button "Change" reconciles the field against the product: by quantity, not by name."""

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino West3")
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.sammel = Ware.objects.create(name="Artikel C 0,33", basiseinheit=cls.stueck)
        cls.kasten = Gebinde.objects.create(
            ware=cls.sammel, name="Kiste(n)", faktor_in_basiseinheit=Decimal("20")
        )

    def ausfuehren(self, gebinde: dict):
        tabelle = pd.DataFrame([zeile("101001", "Artikel C 0,33", "Kasten", 20.0)], columns=SPALTEN)
        daten = als_daten(planen(tabelle, self.haus)[0][0])
        daten["gebinde"] = gebinde
        return ausfuehren([daten], self.haus)

    def test_gleiche_menge_anderer_name_ist_eine_umbenennung(self):
        bericht = self.ausfuehren({"Kasten": Decimal("20.000")})
        self.kasten.refresh_from_db()
        self.assertEqual(self.kasten.name, "Kasten")
        self.assertEqual(bericht.gebinde_umbenannt, 1)
        self.assertEqual(self.sammel.gebinde.count(), 1)

    def test_was_nicht_mehr_im_feld_steht_wird_inaktiv(self):
        bericht = self.ausfuehren({"Sixpack": Decimal("6.000")})
        self.kasten.refresh_from_db()
        self.assertFalse(self.kasten.aktiv)
        self.assertEqual(bericht.gebinde_stillgelegt, 1)
        # Nothing is deleted: counted rows hang on a pack.
        self.assertEqual(self.sammel.gebinde.count(), 2)

    def test_eine_menge_holt_ihr_inaktives_gebinde_zurueck(self):
        self.kasten.aktiv = False
        self.kasten.save(update_fields=["aktiv"])
        self.ausfuehren({"Kasten": Decimal("20.000")})
        self.kasten.refresh_from_db()
        self.assertTrue(self.kasten.aktiv)

    def test_ein_schon_vergebener_name_bleibt_beim_alten_gebinde(self):
        Gebinde.objects.create(
            ware=self.sammel, name="Kasten", faktor_in_basiseinheit=Decimal("24")
        )
        bericht = self.ausfuehren({"Kasten": Decimal("20.000"), "Karton": Decimal("24.000")})
        self.kasten.refresh_from_db()
        self.assertEqual(self.kasten.name, "Kiste(n)")
        self.assertIn("führt die Ware schon mit einer anderen Menge", " ".join(bericht.fehler))


class ImportFehlende(TestCase):
    """What the house carries and the file no longer knows.

    Compeso exports per chosen product group everything in it. A number
    missing from an exported product group is delisted there. A product
    group nobody selected, by contrast, says nothing at all.
    """

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Ost")
        cls.stueck = Basiseinheit.objects.get(dimension=Dimension.STUECK)
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")
        cls.andere = Warengruppe.objects.create(name="Warenfamilie R")

    @classmethod
    def artikel(cls, nummer, name, gruppe=None, status=Artikelstatus.AKTIV):
        return Hausartikel.objects.create(
            haus=cls.haus,
            ware=Ware.objects.create(name=name, basiseinheit=cls.stueck),
            warengruppe=gruppe or cls.gruppe,
            artikelnummer=nummer,
            name=name,
            status=status,
        )

    def planen(self, *zeilen):
        return planen(pd.DataFrame(list(zeilen), columns=SPALTEN), self.haus)

    def test_was_in_der_datei_fehlt_steht_im_plan(self):
        alt = self.artikel("101002", "Ware N")
        plan, _ = self.planen(zeile("101001", "Artikel C 0,33", "Kasten", 24.0))
        fehlend = [satz for satz in plan if satz.art == FEHLT]
        self.assertEqual([satz.artikelnummer for satz in fehlend], ["101002"])
        satz = fehlend[0]
        # Preselected is "Leave": an incomplete export should clear nothing away.
        self.assertEqual(satz.vorgehen, UEBERGEHEN)
        self.assertEqual(satz.vorhanden_pk, alt.pk)
        self.assertEqual(satz.ware_pk, alt.ware_id)
        self.assertEqual(satz.warengruppe, "Warengruppe G")
        # Nothing from the file: the row keeps what it has.
        self.assertEqual(satz.gebinde, {})

    def test_gekauft_in_zu_0_fehlt_nicht(self):
        """The file still lists the article, only without a pack. Until 10/2026 it fell out.

        Read from the raw export: the transformation decided it. The carried
        one counts as known, the new one is suggested discontinued.
        """
        self.artikel("101002", "Ware N", gruppe=Warengruppe.objects.create(name="01 Warengruppe G"))
        kopf = "Artikel;Lieferant;Unnamed: 2;VKPreis;Lagerwert"
        zeilen = [
            "01 Warengruppe G;;;;",
            "101002;Ware N;Lieferant A; ;2,00",
            ";gekauft in zu 0;Bestand: 0,00 Stück(e) zu;1,00 =;0,00",
            "101003;Ware M;Lieferant A; ;2,00",
            ";gekauft in zu 0;Bestand: 0,00 Stück(e) zu;1,00 =;0,00",
        ]
        tabelle = WinTicket().artikelstamm_lesen(
            SimpleUploadedFile("Artikel.csv", "\n".join([kopf, *zeilen]).encode(), "text/csv"),
            "Kino Ost",
        )

        plan, _ = planen(tabelle, self.haus)

        self.assertEqual([(satz.artikelnummer, satz.art) for satz in plan], [("101003", NEU)])
        self.assertEqual(plan[0].vorgehen, EINSTELLEN)
        self.assertIn("In der Datei steht keine Anzahl je Gebinde.", plan[0].hinweise)

    def test_eine_nicht_exportierte_warengruppe_bleibt_unberuehrt(self):
        self.artikel("101002", "Ware N", gruppe=self.andere)
        plan, meldungen = self.planen(zeile("101001", "Artikel C 0,33", "Kasten", 24.0))
        self.assertEqual([satz for satz in plan if satz.art == FEHLT], [])
        self.assertIn("bleiben unberührt: Warenfamilie R", meldungen[0])

    def test_eine_nummer_in_der_datei_fehlt_nicht(self):
        """If the number is there, the row from the file already holds the row."""
        self.artikel("101001", "Alter Name")
        plan, _ = self.planen(zeile("101001", "Neuer Name", "Kasten", 24.0))
        self.assertEqual([satz.art for satz in plan], [UMBENANNT])

    def test_eingestellte_und_nicht_zaehlbare_fehlen_nicht(self):
        """Only what the house carries can be delisted."""
        self.artikel("101002", "Ware N", status=Artikelstatus.EINGESTELLT)
        plan, _ = self.planen(zeile("101001", "Artikel C 0,33", "Kasten", 24.0))
        self.assertEqual([satz for satz in plan if satz.art == FEHLT], [])

    def test_eine_leere_datei_listet_nichts_aus(self):
        self.artikel("101002", "Ware N")
        plan, _ = self.planen()
        self.assertEqual(plan, [])

    def test_ein_inaktiver_satz_sagt_es(self):
        self.artikel("101002", "Ware N", status=Artikelstatus.INAKTIV)
        plan, _ = self.planen(zeile("101001", "Artikel C 0,33", "Kasten", 24.0))
        satz = next(satz for satz in plan if satz.art == FEHLT)
        self.assertEqual(satz.hinweise, ["Steht schon auf inaktiv."])

    def test_eine_halb_leere_warengruppe_wird_gemeldet(self):
        """More likely a filtered export than a half delisted product group."""
        for nummer in ("101002", "101003", "101004"):
            self.artikel(nummer, f"Ware {nummer}")
        self.artikel("101001", "Artikel C 0,33")
        _, meldungen = self.planen(zeile("101001", "Artikel C 0,33", "Kasten", 24.0))
        self.assertIn("fehlen 3 von 4 Artikeln", " ".join(meldungen))

    def test_lassen_ruehrt_nichts_an(self):
        alt = self.artikel("101002", "Ware N")
        plan, _ = self.planen(zeile("101001", "Artikel C 0,33", "Kasten", 24.0))
        satz = next(satz for satz in plan if satz.art == FEHLT)
        ausfuehren([als_daten(satz)], self.haus)
        alt.refresh_from_db()
        self.assertEqual(alt.status, Artikelstatus.AKTIV)

    def test_inaktiv_setzt_den_satz_inaktiv(self):
        alt = self.artikel("101002", "Ware N")
        plan, _ = self.planen(zeile("101001", "Artikel C 0,33", "Kasten", 24.0))
        satz = next(satz for satz in plan if satz.art == FEHLT)
        satz.vorgehen = INAKTIV
        bericht = ausfuehren([als_daten(satz)], self.haus)
        alt.refresh_from_db()
        self.assertEqual(alt.status, Artikelstatus.INAKTIV)
        self.assertEqual(bericht.artikel_fehlend_inaktiv, ["101002 Ware N"])

    def test_eingestellt_gibt_nummer_und_ware_frei(self):
        alt = self.artikel("101002", "Ware N")
        plan, _ = self.planen(zeile("101001", "Artikel C 0,33", "Kasten", 24.0))
        satz = next(satz for satz in plan if satz.art == FEHLT)
        satz.vorgehen = EINSTELLEN
        bericht = ausfuehren([als_daten(satz)], self.haus)
        alt.refresh_from_db()
        self.assertEqual(alt.status, Artikelstatus.EINGESTELLT)
        self.assertEqual(bericht.artikel_fehlend_eingestellt, ["101002 Ware N"])
