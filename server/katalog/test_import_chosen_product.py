"""A product chosen by hand in the import plan: first its packs, then the adoption.

The plan fills the pack field for the product it suggests. Until 10/2026 a
product chosen by hand was reconciled with that field anyway: what the field
lacked went inactive in every house, and a product chosen instead of the
suggested one took over the other one's packs. Now the row comes back with
the packs of the chosen product, and only the next submit writes.
"""

from decimal import Decimal

import pandas as pd
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from django_project.testing import AngemeldeteSeiten
from organisation.models import Haus

from .models import Artikelstatus, Basiseinheit, Gebinde, Hausartikel, Ware, Warengruppe
from .services import AKTUALISIEREN, GEBINDE, KONFLIKT, als_daten, ausfuehren, planen

#: What the row says when it comes back with the chosen product's packs.
PRUEFEN = "Bitte prüfen und erneut übernehmen"

SPALTEN = ["artikelnummer", "name", "warengruppe", "gebinde", "anzahl", "einheit", "haus"]


def ware_mit(name: str, einheit: Basiseinheit, *gebinde: tuple[str, int]) -> Ware:
    ware = Ware.objects.create(name=name, basiseinheit=einheit)
    for bezeichnung, menge in gebinde:
        Gebinde.objects.create(
            ware=ware,
            name=bezeichnung,
            faktor_in_basiseinheit=Decimal(menge),
            ist_einzeln=bezeichnung == "Einzeln",
        )
    return ware


def gebinde_von(ware: Ware) -> list[tuple[str, Decimal, bool]]:
    """Name, quantity and status of every pack of the product, by quantity."""
    return list(
        ware.gebinde.order_by("faktor_in_basiseinheit").values_list(
            "name", "faktor_in_basiseinheit", "aktiv"
        )
    )


class Grundlage:
    """A product of Kino Ost with a pallet only Ost carries. Kino Nord imports."""

    nord: Haus
    ost: Haus
    stueck: Basiseinheit
    gruppe: Warengruppe

    @classmethod
    def grundlage(cls) -> None:
        cls.nord = Haus.objects.create(name="Kino Nord")
        cls.ost = Haus.objects.create(name="Kino Ost")
        cls.stueck = Basiseinheit.objects.get(name="Stück(e)")
        cls.gruppe = Warengruppe.objects.create(name="Warengruppe G")

    def cola_in_ost(self) -> Ware:
        cola = ware_mit("Cola 0,5", self.stueck, ("Kasten", 24), ("Einzeln", 1), ("Palette", 120))
        Hausartikel.objects.create(
            haus=self.ost,
            ware=cola,
            warengruppe=self.gruppe,
            artikelnummer="102001",
            name="Cola 0,5",
        )
        return cola


class UeberDieSeite(Grundlage, AngemeldeteSeiten):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.grundlage()

    def setUp(self):
        super().setUp()
        self.cola = self.cola_in_ost()
        self.vorher = gebinde_von(self.cola)

    @property
    def adresse(self) -> str:
        return reverse("hausartikel_import", args=[self.nord.pk])

    def hochladen(self, *zeilen: str):
        kopf = "artikelnummer;name;warengruppe;gebinde;anzahl;einheit;haus"
        datei = SimpleUploadedFile(
            "export.csv", "\n".join([kopf, *zeilen]).encode("utf-8"), "text/csv"
        )
        return self.client.post(self.adresse, {"datei": datei, "format": "winticket"})

    def absenden(self, antwort, **abweichend):
        """The shown plan back, as the browser sends it, with what someone changed."""
        formset = antwort.context["formset"]
        daten = {
            "schritt": "uebernehmen",
            "format": "winticket",
            "plan-TOTAL_FORMS": str(len(formset.forms)),
            "plan-INITIAL_FORMS": str(len(formset.forms)),
            "plan-MIN_NUM_FORMS": "0",
            "plan-MAX_NUM_FORMS": "5000",
        }
        for stelle, form in enumerate(formset.forms):
            for feld, wert in form.initial.items():
                daten[f"plan-{stelle}-{feld}"] = "" if wert is None else wert
        return self.client.post(self.adresse, daten | abweichend)

    def erneut(self, antwort):
        """The plan the page sent back, unchanged: whoever checked the packs submits again."""
        return self.client.post(self.adresse, dict(antwort.context["formset"].data.lists()))

    @staticmethod
    def zeile(antwort, nummer: str):
        return next(
            form
            for form in antwort.context["formset"].forms
            if form["artikelnummer"].value() == nummer
        )

    def test_eine_von_hand_gewaehlte_ware_kommt_mit_ihren_gebinden_zurueck(self):
        """Kino Nord names it differently. Whoever assigns the row to the product sees its packs."""
        plan = self.hochladen("101001;Cola 0,5 EK;Warengruppe G;Kiste(n);24;Stück(e);Kino Nord")
        self.assertEqual(self.zeile(plan, "101001")["ware"].value(), None)

        antwort = self.absenden(plan, **{"plan-0-ware": str(self.cola.pk)})

        self.assertContains(antwort, PRUEFEN)
        zeile = self.zeile(antwort, "101001")
        self.assertEqual(zeile["gebinde"].value(), "Kasten = 24\nEinzeln = 1\nPalette = 120")
        self.assertEqual(zeile["gebinde_von"].value(), str(self.cola.pk))
        self.assertFalse(Hausartikel.objects.filter(haus=self.nord).exists())
        self.assertEqual(gebinde_von(self.cola), self.vorher)

        self.assertRedirects(self.erneut(antwort), self.nord.get_absolute_url())
        self.assertEqual(gebinde_von(self.cola), self.vorher)
        self.assertEqual(Hausartikel.objects.get(haus=self.nord).ware, self.cola)

    def test_wie_der_hinweis_es_sagt_eingestellt_mit_der_ware(self):
        """The plan itself advises it for an outdated number: discontinued, product chosen."""
        Hausartikel.objects.create(
            haus=self.nord,
            ware=self.cola,
            warengruppe=self.gruppe,
            artikelnummer="101001",
            name="Cola 0,5",
        )
        plan = self.hochladen(
            "101001;Cola 0,5;Warengruppe G;Kiste(n);24;Stück(e);Kino Nord",
            "101099;Cola 0,5;Warengruppe G;Kiste(n);24;Stück(e);Kino Nord",
        )
        self.assertIn("zur Prüfung zurück", self.zeile(plan, "101099")["hinweise"].value())

        antwort = self.absenden(
            plan, **{"plan-0-ware": str(self.cola.pk), "plan-0-vorgehen": "einstellen"}
        )
        self.assertContains(antwort, PRUEFEN)
        self.assertRedirects(self.erneut(antwort), self.nord.get_absolute_url())

        self.assertEqual(gebinde_von(self.cola), self.vorher)
        alt = Hausartikel.objects.get(haus=self.nord, artikelnummer="101099")
        self.assertEqual((alt.status, alt.ware), (Artikelstatus.EINGESTELLT, self.cola))

    def test_eine_neue_ware_bekommt_nur_die_gebinde_der_datei(self):
        """Suggested is the cola, created is a product of its own: without the cola's pallet.

        Until 10/2026 the new product took over every pack the field showed
        for the suggested one.
        """
        plan = self.hochladen("101001;Cola 0,5;Warengruppe G;Kiste(n);6;Stück(e);Kino Nord")
        self.assertEqual(self.zeile(plan, "101001")["ware"].value(), self.cola.pk)

        antwort = self.absenden(plan, **{"plan-0-ware": "", "plan-0-neuer_ware": "Cola 0,5 Nord"})

        self.assertContains(antwort, "Angelegt wird eine neue Ware.")
        self.assertEqual(
            self.zeile(antwort, "101001")["gebinde"].value(), "Kasten = 6\nEinzeln = 1"
        )
        self.assertRedirects(self.erneut(antwort), self.nord.get_absolute_url())
        neu = Ware.objects.get(name="Cola 0,5 Nord")
        self.assertEqual(
            gebinde_von(neu),
            [("Einzeln", Decimal("1.000"), True), ("Kasten", Decimal("6.000"), True)],
        )
        self.assertEqual(gebinde_von(self.cola), self.vorher)

    def test_eine_andere_umrechnung_rechnet_das_feld_neu(self):
        """Kino Nord counts Ware K in pieces of half a kilo. Whoever corrects that to 250 g.

        The field showed the box of 5 kg as 10 pieces. Read back with a
        quarter kilo per piece, until 10/2026 that was a second box of 2.5 kg.
        """
        ware = ware_mit("Ware K", Basiseinheit.objects.get(name="kg"), ("Karton", 5))
        Hausartikel.objects.create(
            haus=self.nord,
            ware=ware,
            warengruppe=self.gruppe,
            artikelnummer="101005",
            name="Ware K",
            basiseinheit=self.stueck,
            faktor_zur_ware=Decimal("0.5"),
        )
        plan = self.hochladen("101005;Ware K;Warengruppe G;Kiste;8;Stück(e);Kino Nord")
        zeile = self.zeile(plan, "101005")
        self.assertEqual(zeile["art"].value(), KONFLIKT)
        self.assertEqual(zeile["gebinde"].value(), "Karton = 10\nKiste = 8\nEinzeln = 1")

        antwort = self.absenden(
            plan, **{"plan-0-vorgehen": AKTUALISIEREN, "plan-0-faktor_zur_ware": "0,25"}
        )

        self.assertContains(antwort, "Die Umrechnung ist eine andere als im Plan.")
        self.assertEqual(
            self.zeile(antwort, "101005")["gebinde"].value(), "Karton = 20\nKiste = 8\nEinzeln = 1"
        )
        self.assertRedirects(self.erneut(antwort), self.nord.get_absolute_url())
        self.assertEqual(
            gebinde_von(ware),
            [
                ("Einzeln", Decimal("0.250"), True),
                ("Kiste", Decimal("2.000"), True),
                ("Karton", Decimal("5.000"), True),
            ],
        )

    def test_die_hinterlegte_umrechnung_steht_im_feld(self):
        """The hint says what the house's row carries, and the field holds it already."""
        ware = ware_mit("Ware K", Basiseinheit.objects.get(name="kg"), ("Karton", 5))
        Hausartikel.objects.create(
            haus=self.nord,
            ware=ware,
            warengruppe=self.gruppe,
            artikelnummer="101005",
            name="Ware K",
            basiseinheit=self.stueck,
            faktor_zur_ware=Decimal("0.5"),
        )
        plan = self.hochladen("101005;Ware K;Warengruppe G;Kiste;8;Stück(e);Kino Nord")

        self.assertEqual(self.zeile(plan, "101005")["faktor_zur_ware"].value(), Decimal("0.5"))
        antwort = self.absenden(plan, **{"plan-0-vorgehen": AKTUALISIEREN})
        self.assertRedirects(antwort, self.nord.get_absolute_url())
        self.assertEqual(
            gebinde_von(ware),
            [
                ("Einzeln", Decimal("0.500"), True),
                ("Kiste", Decimal("4.000"), True),
                ("Karton", Decimal("5.000"), True),
            ],
        )

    def test_eine_umgewaehlte_ware_bekommt_nicht_die_gebinde_der_vorgeschlagenen(self):
        """Suggested is the product of the same name. Chosen is another one with its own box."""
        limo = ware_mit("Limo 0,5", self.stueck, ("Kasten", 20))
        plan = self.hochladen("101001;Cola 0,5;Warengruppe G;Kiste(n);24;Stück(e);Kino Nord")
        self.assertEqual(self.zeile(plan, "101001")["ware"].value(), self.cola.pk)

        antwort = self.absenden(plan, **{"plan-0-ware": str(limo.pk)})

        self.assertContains(antwort, PRUEFEN)
        # What the plan would have shown for this product: its box, then the file's.
        self.assertEqual(
            self.zeile(antwort, "101001")["gebinde"].value(),
            "Kasten = 20\nKasten 24 = 24\nEinzeln = 1",
        )
        self.assertContains(antwort, "„Kasten 24“ als eigenes Gebinde")
        self.assertRedirects(self.erneut(antwort), self.nord.get_absolute_url())

        self.assertEqual(
            gebinde_von(limo),
            [
                ("Einzeln", Decimal("1.000"), True),
                ("Kasten", Decimal("20.000"), True),
                ("Kasten 24", Decimal("24.000"), True),
            ],
        )
        self.assertEqual(gebinde_von(self.cola), self.vorher)

    def test_aendern_mit_umgewaehlter_ware_im_konflikt(self):
        """A conflict changed onto another product: neither product loses a pack."""
        limo = ware_mit("Limo 0,5", self.stueck, ("Kasten", 20))
        artikel = Hausartikel.objects.create(
            haus=self.nord,
            ware=self.cola,
            warengruppe=self.gruppe,
            artikelnummer="101001",
            name="Cola 0,5",
        )
        plan = self.hochladen(
            "101001;Cola 0,5;Warengruppe G;Kiste(n);24;Stück(e);Kino Nord",
            "101001;Cola 0,5;Warengruppe G;Palette;96;Stück(e);Kino Nord",
        )
        self.assertEqual(self.zeile(plan, "101001")["art"].value(), KONFLIKT)

        antwort = self.absenden(
            plan, **{"plan-0-vorgehen": AKTUALISIEREN, "plan-0-ware": str(limo.pk)}
        )

        self.assertContains(antwort, PRUEFEN)
        self.assertEqual(gebinde_von(self.cola), self.vorher)
        self.assertRedirects(self.erneut(antwort), self.nord.get_absolute_url())
        self.assertEqual(gebinde_von(self.cola), self.vorher)
        self.assertEqual(
            gebinde_von(limo),
            [
                ("Einzeln", Decimal("1.000"), True),
                ("Kasten", Decimal("20.000"), True),
                ("Kasten 24", Decimal("24.000"), True),
                ("Palette", Decimal("96.000"), True),
            ],
        )
        artikel.refresh_from_db()
        self.assertEqual(artikel.ware, limo)

    def test_ein_inaktives_gebinde_der_gewaehlten_ware_steht_im_feld_und_kommt_zurueck(self):
        """Kino Ost no longer counts the pallet. Kino Nord's file names it, and the row says so.

        The file carries it, so adopting brings it back in every house
        (decided on 2026-10-18), and the message afterwards counts it.
        Whoever does not want that takes the line out of the field.
        """
        Gebinde.objects.filter(ware=self.cola, name="Palette").update(aktiv=False)
        plan = self.hochladen("101001;Cola 0,5 EK;Warengruppe G;Palette;120;Stück(e);Kino Nord")

        antwort = self.absenden(plan, **{"plan-0-ware": str(self.cola.pk)})

        self.assertContains(antwort, PRUEFEN)
        self.assertContains(antwort, "„Palette = 120“ führt die Ware nur inaktiv.")
        self.assertContains(antwort, "Wer das nicht will, nimmt die Zeile aus dem Feld.")
        self.assertEqual(
            self.zeile(antwort, "101001")["gebinde"].value(),
            "Kasten = 24\nEinzeln = 1\nPalette = 120",
        )

        daten = dict(antwort.context["formset"].data.lists())
        weiter = self.client.post(self.adresse, daten, follow=True)

        self.assertRedirects(weiter, self.nord.get_absolute_url())
        self.assertIn(("Palette", Decimal("120.000"), True), gebinde_von(self.cola))
        self.assertContains(weiter, "stehen auf inaktiv und 1 wieder auf aktiv.")

    def test_stimmt_das_feld_schon_geht_es_ohne_umweg(self):
        """The chosen product carries exactly what the file brings. There is nothing to check."""
        limo = ware_mit("Limo 0,5", self.stueck, ("Kasten", 24), ("Einzeln", 1))
        plan = self.hochladen("101001;Limo 0,5 EK;Warengruppe G;Kiste(n);24;Stück(e);Kino Nord")

        antwort = self.absenden(plan, **{"plan-0-ware": str(limo.pk)})

        self.assertRedirects(antwort, self.nord.get_absolute_url())
        self.assertEqual(Hausartikel.objects.get(haus=self.nord).ware, limo)

    def test_die_vorgeschlagene_ware_schreibt_ohne_umweg(self):
        """Its packs stood in the field. Whoever strikes one means it, in every house."""
        plan = self.hochladen("101001;Cola 0,5;Warengruppe G;Kiste(n);24;Stück(e);Kino Nord")

        antwort = self.absenden(plan, **{"plan-0-gebinde": "Kasten = 24\nEinzeln = 1"})

        self.assertRedirects(antwort, self.nord.get_absolute_url())
        self.assertIn(("Palette", Decimal("120.000"), False), gebinde_von(self.cola))

    def test_ohne_gebinde_im_plan_kommt_nichts_zurueck(self):
        """Not countable carries no packs, so a product chosen for it needs no check."""
        pfand = ware_mit("Pfand 0,15", self.stueck)
        pfand.ignorieren()
        plan = self.hochladen("101001;Pfand;Warengruppe G;Karton;1;Stück(e);Kino Nord")

        antwort = self.absenden(
            plan, **{"plan-0-ware": str(pfand.pk), "plan-0-vorgehen": "ignorieren"}
        )

        self.assertRedirects(antwort, self.nord.get_absolute_url())


class AmFormularVorbei(Grundlage, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.grundlage()

    def planen(self, *zeilen):
        plan, _meldungen = planen(pd.DataFrame(list(zeilen), columns=SPALTEN), self.nord)
        return plan

    @staticmethod
    def zeile(nummer, name, gebinde, anzahl):
        return {
            "artikelnummer": nummer,
            "name": name,
            "warengruppe": "Warengruppe G",
            "gebinde": gebinde,
            "anzahl": anzahl,
            "einheit": "STUECK",
            "haus": "Kino Nord",
        }

    def test_eine_andere_ware_bekommt_nur_dazu(self):
        """Past the form nothing comes back. The product keeps its packs, the report says so."""
        cola = self.cola_in_ost()
        vorher = gebinde_von(cola)
        (satz,) = self.planen(self.zeile("101001", "Cola 0,5 EK", "Tray", 6.0))
        self.assertIsNone(satz.gebinde_von)

        bericht = ausfuehren([als_daten(satz) | {"ware": cola}], self.nord)

        self.assertEqual(
            gebinde_von(cola), [*vorher[:1], ("Tray", Decimal("6.000"), True), *vorher[1:]]
        )
        self.assertIn("Das Feld zeigte nicht die Gebinde von „Cola 0,5“", " ".join(bericht.fehler))

    def test_eine_andere_ware_bekommt_ihr_inaktives_gebinde_nicht_zurueck(self):
        """Its packs stay as they are, an inactive one too. Nobody saw it in the field."""
        cola = self.cola_in_ost()
        Gebinde.objects.filter(ware=cola, name="Palette").update(aktiv=False)
        vorher = gebinde_von(cola)
        (satz,) = self.planen(self.zeile("101001", "Cola 0,5 EK", "Palette", 120.0))

        bericht = ausfuehren([als_daten(satz) | {"ware": cola}], self.nord)

        self.assertEqual(gebinde_von(cola), vorher)
        self.assertIn("Das Feld zeigte nicht die Gebinde von „Cola 0,5“", " ".join(bericht.fehler))
        self.assertEqual(bericht.gebinde_reaktiviert, 0)

    def test_der_plan_merkt_sich_wessen_gebinde_im_feld_stehen(self):
        cola = self.cola_in_ost()
        (satz,) = self.planen(self.zeile("101001", "Cola 0,5", "Kasten", 24.0))
        self.assertEqual(satz.gebinde_von, cola.pk)
        self.assertEqual(satz.gebinde_datei, {"Kasten": Decimal("24.000"), "Einzeln": Decimal("1")})

    def test_ein_inaktives_gebinde_aus_der_datei_kommt_zurueck(self):
        """Inactive does not count as stored (decided on 2026-10-18), so the row is a conflict.

        The file carries the pallet, so the import brings it back. The field
        shows it under its own name and the hint says what "Change" does: it
        switches the pallet on, in every house. Until 10/2026 it did so without
        a word. Taken out of the field, it stays inactive.
        """
        cola = self.cola_in_ost()
        Gebinde.objects.filter(ware=cola, name="Palette").update(aktiv=False)
        Hausartikel.objects.create(
            haus=self.nord,
            ware=cola,
            warengruppe=self.gruppe,
            artikelnummer="101001",
            name="Cola 0,5",
        )
        (satz,) = self.planen(
            self.zeile("101001", "Cola 0,5", "Kasten", 24.0),
            self.zeile("101001", "Cola 0,5", "Palette", 120.0),
        )
        self.assertEqual((satz.art, satz.abweichungen), (KONFLIKT, [GEBINDE]))
        self.assertEqual(satz.gebinde["Palette"], Decimal("120"))
        self.assertIn("„Palette = 120“ führt die Ware nur inaktiv.", " ".join(satz.hinweise))

        ohne = {name: menge for name, menge in satz.gebinde.items() if name != "Palette"}
        daten = als_daten(satz) | {"vorgehen": AKTUALISIEREN, "gebinde": ohne}
        self.assertEqual(ausfuehren([daten], self.nord).gebinde_reaktiviert, 0)
        self.assertIn(("Palette", Decimal("120.000"), False), gebinde_von(cola))

        bericht = ausfuehren([als_daten(satz) | {"vorgehen": AKTUALISIEREN}], self.nord)
        self.assertIn(("Palette", Decimal("120.000"), True), gebinde_von(cola))
        self.assertEqual(bericht.gebinde_reaktiviert, 1)
