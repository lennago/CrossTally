"""The inventory's pages must not query per row and compute nothing several times.

The same pattern as 'katalog/test_views_queries.py': equally many queries
with few and with many rows. The tests there work with a handful of rows.
What a page does with five hundred articles is only shown by the
measurement against the real stock (2026-09-18). What stood out there is
pinned down here.
"""

from datetime import date
from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from django_project.testing import inventur_anlegen, jahresinventur
from katalog.models import (
    Artikelstatus,
    Erwartung,
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Ware,
    Warengruppe,
)
from organisation.models import Bereich, Ort

from .closing import klaeren
from .closing.decisions import WECHSEL_PRAEFIX, Entscheidungen, abschliessen
from .models import (
    Inventurnotiz,
    Inventurstatus,
    Sollbestand,
    Sollgrund,
    Sollzeile,
    Zaehlposition,
)
from .services import notizen_speichern
from .test_views import InventurSeiten


class AbfragenWachsenNichtMitDenZeilen(InventurSeiten):
    def setUp(self):
        super().setUp()
        self.inv = self.inventur(status=Inventurstatus.ZAEHLUNG)
        self.nummer = 500000

    def gezaehlter_artikel(self, mit_soll: bool) -> Hausartikel:
        """Counted unexpectedly, optionally without target. So it sits in several closing blocks."""
        self.nummer += 1
        ware = Ware.objects.create(name=f"Ware {self.nummer}", basiseinheit=self.stueck)
        artikel = Hausartikel.objects.create(
            haus=self.haus,
            ware=ware,
            warengruppe=Warengruppe.objects.get(name="Warengruppe G"),
            artikelnummer=str(self.nummer),
            name=ware.name,
        )
        karton = Gebinde.objects.create(
            ware=ware, name="Karton", faktor_in_basiseinheit=Decimal("10")
        )
        Zaehlposition.objects.create(
            inventur=self.inv,
            ort=self.ort,
            artikel=artikel,
            gebinde=karton,
            eingabemenge=Decimal("3"),
            unerwartet=True,
        )
        if mit_soll:
            Sollbestand.objects.create(
                inventur=self.inv, hausartikel=artikel, menge_basiseinheit=Decimal("25")
            )
        return artikel

    def abfragen(self, adresse: str) -> int:
        with CaptureQueriesContext(connection) as gezaehlt:
            self.assertEqual(self.client.get(adresse).status_code, 200)
        return len(gezaehlt)

    def erwarteter_artikel(self, art=None, menge=None) -> Hausartikel:
        """Expected at the location. Uncounted: block four. Counted in a foreign type: five."""
        self.nummer += 1
        ware = Ware.objects.create(name=f"Ware {self.nummer}", basiseinheit=self.stueck)
        artikel = Hausartikel.objects.create(
            haus=self.haus,
            ware=ware,
            warengruppe=Warengruppe.objects.get(name="Warengruppe G"),
            artikelnummer=str(self.nummer),
            name=ware.name,
            **({"inventurart": art} if art is not None else {}),
        )
        karton = Gebinde.objects.create(
            ware=ware, name="Karton", faktor_in_basiseinheit=Decimal("10")
        )
        ErwartungGebinde.objects.create(
            erwartung=Erwartung.objects.create(ort=self.ort, artikel=artikel), gebinde=karton
        )
        Zaehlposition.objects.create(
            inventur=self.inv, ort=self.ort, artikel=artikel, gebinde=karton, eingabemenge=menge
        )
        return artikel

    def test_ungezaehlte_und_artfremde_artikel_fragen_nicht_je_zeile(self):
        """The closing names the type of each of them. Until 10/2026 that was a query per row."""
        jahr = jahresinventur()
        for _ in range(2):
            self.erwarteter_artikel()
            self.erwarteter_artikel(art=jahr, menge=Decimal("3"))
        adresse = reverse("inventur_abschluss", args=[self.inv.pk])
        wenige = self.abfragen(adresse)
        for _ in range(4):
            self.erwarteter_artikel()
            self.erwarteter_artikel(art=jahr, menge=Decimal("3"))
        self.assertEqual(self.abfragen(adresse), wenige)

    def test_klaerung_pruefliste_und_detailseite(self):
        for i in range(2):
            self.gezaehlter_artikel(mit_soll=i == 0)
        seiten = {
            name: reverse(name, args=[self.inv.pk])
            for name in ("inventur_abschluss", "inventur_pruefen", "inventur_detail")
        }
        wenige = {name: self.abfragen(adresse) for name, adresse in seiten.items()}
        for i in range(2, 8):
            self.gezaehlter_artikel(mit_soll=i % 2 == 0)
        for name, adresse in seiten.items():
            with self.subTest(seite=name):
                self.assertEqual(self.abfragen(adresse), wenige[name])

    def test_die_klaerung_rechnet_die_zaehlung_nur_einmal(self):
        """Measured: 'klaeren' computed all count positions four times (58 queries)."""
        self.gezaehlter_artikel(mit_soll=True)
        self.gezaehlter_artikel(mit_soll=False)
        with CaptureQueriesContext(connection) as gezaehlt:
            klaeren(self.inv)
        # Whoever computes loads the full row including pack: once 'klaeren' for
        # the blocks per row, once 'mengen_je_hausartikel' for the sums.
        voll_geladen = sum(
            'FROM "inventur_zaehlposition"' in abfrage["sql"]
            and '"katalog_gebinde"."faktor_in_basiseinheit"' in abfrage["sql"]
            for abfrage in gezaehlt.captured_queries
        )
        self.assertEqual(voll_geladen, 2)


class InventurlistenFragenNichtJeInventur(InventurSeiten):
    """'Inventur.__str__' reads the types. Every list that names inventories loads them along."""

    def abgeschlossen(self, tag: int) -> None:
        inventur = inventur_anlegen(self.haus, stichtag=date(2026, 3, tag))
        inventur.zaehlpositionen_anlegen()
        inventur.zaehlung_starten()
        inventur.abschliessen()

    def abfragen(self, adresse: str, daten: dict) -> int:
        with CaptureQueriesContext(connection) as gezaehlt:
            self.assertEqual(self.client.get(adresse, daten).status_code, 200)
        return len(gezaehlt)

    def test_die_auswertung_waechst_nicht_mit_den_inventuren(self):
        """Until 10/2026 one query per closed inventory in the period (38 of 56 measured)."""
        for tag in (1, 2):
            self.abgeschlossen(tag)
        adresse = reverse("auswertung")
        zeitraum = {"von": "2026-03-01", "bis": "2026-03-31"}
        wenige = self.abfragen(adresse, zeitraum)
        for tag in range(3, 9):
            self.abgeschlossen(tag)
        self.assertEqual(self.abfragen(adresse, zeitraum), wenige)


class ArtwechselImAbschluss(InventurSeiten):
    """Every switch of a type compared the counting lists of all houses anew (15 queries each)."""

    def setUp(self):
        super().setUp()
        self.nummer = 101900

    def artikel_ohne_zaehlung(self, anzahl: int) -> list[Hausartikel]:
        artikel = []
        for _ in range(anzahl):
            self.nummer += 1
            ware = Ware.objects.create(name=f"Ware {self.nummer}", basiseinheit=self.stueck)
            satz = Hausartikel.objects.create(
                haus=self.haus,
                ware=ware,
                warengruppe=self.artikel.warengruppe,
                artikelnummer=str(self.nummer),
                name=ware.name,
            )
            karton = Gebinde.objects.create(
                ware=ware, name="Karton", faktor_in_basiseinheit=Decimal("10")
            )
            ErwartungGebinde.objects.create(
                erwartung=Erwartung.objects.create(ort=self.ort, artikel=satz), gebinde=karton
            )
            artikel.append(satz)
        return artikel

    def lesevorgaenge_auf_erwartungen(self, anzahl: int, tag: int) -> int:
        """Closes an inventory with 'anzahl' switches into the annual inventory."""
        artikel = self.artikel_ohne_zaehlung(anzahl)
        inventur = inventur_anlegen(self.haus, stichtag=date(2026, 5, tag))
        inventur.zaehlpositionen_anlegen()
        inventur.zaehlung_starten()
        # Artikel C counted, the others not: they are the fourth block.
        inventur.zaehlpositionen.filter(artikel=self.artikel).update(eingabemenge=Decimal("1"))
        jahr = jahresinventur()
        antworten = {str(satz.pk): f"{WECHSEL_PRAEFIX}{jahr.pk}" for satz in artikel}
        with CaptureQueriesContext(connection) as gezaehlt:
            bericht = abschliessen(inventur, Entscheidungen(ungezaehlt=antworten))
        self.assertEqual(bericht.art_gewechselt, anzahl)
        return sum(
            1
            for abfrage in gezaehlt.captured_queries
            if "katalog_erwartung" in abfrage["sql"] and abfrage["sql"].startswith("SELECT")
        )

    def test_die_zaehllisten_werden_einmal_verglichen_nicht_je_wechsel(self):
        eins = self.lesevorgaenge_auf_erwartungen(1, 1)
        vier = self.lesevorgaenge_auf_erwartungen(4, 2)
        self.assertEqual(vier, eins)


class SollklaerungFragtNichtJeZeile(InventurSeiten):
    """Measured: per open row one query for the unit of its product."""

    def setUp(self):
        super().setUp()
        self.inv = self.inventur()
        self.nummer = 601000

    def offene_fragen(self, anzahl: int) -> None:
        """Per pass: an inactive article with target, an active one without, one named otherwise."""
        gruppe = Warengruppe.objects.get(name="Warengruppe G")
        for _ in range(anzahl):
            for status, art in (
                (Artikelstatus.INAKTIV, "zeile"),
                (Artikelstatus.AKTIV, "ohne"),
                (Artikelstatus.AKTIV, "name"),
            ):
                self.nummer += 1
                ware = Ware.objects.create(name=f"Ware {self.nummer}", basiseinheit=self.stueck)
                artikel = Hausartikel.objects.create(
                    haus=self.haus,
                    ware=ware,
                    warengruppe=gruppe,
                    artikelnummer=str(self.nummer),
                    name=ware.name,
                    status=status,
                )
                if art == "zeile":
                    Sollzeile.objects.create(
                        inventur=self.inv,
                        artikelnummer=artikel.artikelnummer,
                        menge_basiseinheit=Decimal("3"),
                        grund=Sollgrund.INAKTIV,
                        hausartikel=artikel,
                    )
                elif art == "name":
                    Sollbestand.objects.create(
                        inventur=self.inv,
                        hausartikel=artikel,
                        menge_basiseinheit=Decimal("4"),
                        name_in_datei="Heißt in der Datei anders",
                    )

    def test_sollbestand_klaeren(self):
        adresse = reverse("inventur_sollbestand", args=[self.inv.pk])
        self.offene_fragen(2)
        with CaptureQueriesContext(connection) as wenige:
            self.assertEqual(self.client.get(adresse).status_code, 200)
        self.offene_fragen(4)
        with CaptureQueriesContext(connection) as viele:
            self.assertEqual(self.client.get(adresse).status_code, 200)
        self.assertEqual(len(viele), len(wenige))


class RohdatenInDerVerwaltung(InventurSeiten):
    """The lists of the administration. Until 09/2026 no test loaded them, each queried per row.

    Measured against the real stock: 110 to 205 queries per list (the house
    behind every article, every location), and "Add count position" wrote out
    all 1100 packs with their product: 1226 queries.
    """

    LISTEN = ("zaehlposition", "sollbestand", "sollzeile", "inventurnotiz")

    def setUp(self):
        super().setUp()
        self.inv = self.inventur(status=Inventurstatus.ZAEHLUNG)
        self.nummer = 700000

    def zeilen(self, anzahl: int) -> None:
        gruppe = Warengruppe.objects.get(name="Warengruppe G")
        for _ in range(anzahl):
            self.nummer += 1
            ware = Ware.objects.create(name=f"Ware {self.nummer}", basiseinheit=self.stueck)
            artikel = Hausartikel.objects.create(
                haus=self.haus,
                ware=ware,
                warengruppe=gruppe,
                artikelnummer=str(self.nummer),
                name=ware.name,
            )
            karton = Gebinde.objects.create(
                ware=ware, name="Karton", faktor_in_basiseinheit=Decimal("10")
            )
            Zaehlposition.objects.create(
                inventur=self.inv, ort=self.ort, artikel=artikel, gebinde=karton, unerwartet=True
            )
            Sollbestand.objects.create(
                inventur=self.inv, hausartikel=artikel, menge_basiseinheit=Decimal("1")
            )
            Sollzeile.objects.create(
                inventur=self.inv,
                artikelnummer=f"9{self.nummer % 101000:05d}",
                menge_basiseinheit=Decimal("1"),
                grund=Sollgrund.INAKTIV,
                hausartikel=artikel,
            )
            Inventurnotiz.objects.create(inventur=self.inv, hausartikel=artikel, text="Notiz")

    def abfragen(self, adresse: str) -> int:
        with CaptureQueriesContext(connection) as gezaehlt:
            self.assertEqual(self.client.get(adresse).status_code, 200, adresse)
        return len(gezaehlt)

    def adressen(self) -> dict[str, str]:
        seiten = {f"{m}: Liste": reverse(f"admin:inventur_{m}_changelist") for m in self.LISTEN}
        seiten["zaehlposition: Anlegen"] = reverse("admin:inventur_zaehlposition_add")
        # The inventory's form shows every target stock as a row, and each
        # names its house article including house.
        seiten["inventur: Ändern"] = reverse("admin:inventur_inventur_change", args=[self.inv.pk])
        position = self.inv.zaehlpositionen.order_by("pk")[0]
        seiten["zaehlposition: Ändern"] = reverse(
            "admin:inventur_zaehlposition_change", args=[position.pk]
        )
        return seiten

    def test_listen_und_formulare_fragen_nicht_je_zeile(self):
        self.zeilen(2)
        # Once up front: the first call fills Django's cache of the content types.
        for adresse in self.adressen().values():
            self.client.get(adresse)
        wenige = {name: self.abfragen(adresse) for name, adresse in self.adressen().items()}
        self.zeilen(5)
        for name, adresse in self.adressen().items():
            with self.subTest(seite=name):
                self.assertEqual(self.abfragen(adresse), wenige[name])

    def test_filter_und_auswahl_wachsen_nicht_mit_inventuren_und_orten(self):
        """The filter "Inventory" and the selection lists named each with its house.

        Measured against the demo: one query per inventory and two per location.
        The inventories grow every month.
        """
        self.zeilen(2)
        for adresse in self.adressen().values():
            self.client.get(adresse)
        wenige = {name: self.abfragen(adresse) for name, adresse in self.adressen().items()}
        bereich = Bereich.objects.create(name="Lager", haus=self.zweites)
        for monat in range(2, 7):
            inventur_anlegen(self.zweites, stichtag=date(2026, monat, 1))
            Ort.objects.create(name=f"Regal {monat}", bereich=bereich)
        for name, adresse in self.adressen().items():
            with self.subTest(seite=name):
                self.assertEqual(self.abfragen(adresse), wenige[name])


class DieArtenEinmalJeSeite(InventurSeiten):
    """The inventory's types are read once per page, not once per question.

    Name, title, scope and the clarification each read 'inventurarten' anew:
    seven times on the closing page, six times on the target stock page and
    three times for the counters (measured on 2026-10-18).
    """

    def arten_gelesen(self, name: str, inventur) -> int:
        with CaptureQueriesContext(connection) as erfasst:
            antwort = self.client.get(reverse(name, args=[inventur.pk]))
        self.assertEqual(antwort.status_code, 200)
        return sum(
            '"inventur_inventurart"' in abfrage["sql"] for abfrage in erfasst.captured_queries
        )

    def test_seiten_der_vorbereitung(self):
        inventur = self.inventur()
        for name in ("inventur_sollbestand", "inventur_zaehlende", "inventur_arten"):
            with self.subTest(seite=name):
                self.assertEqual(self.arten_gelesen(name, inventur), 1)

    def test_abschluss_mit_einem_artikel_einer_fremden_art(self):
        """Counted in a type the inventory does not count: the clarification names the targets."""
        inventur = self.inventur(status=Inventurstatus.ZAEHLUNG)
        ware = Ware.objects.create(name="Ware N", basiseinheit=self.stueck)
        artikel = Hausartikel.objects.create(
            haus=self.haus,
            ware=ware,
            warengruppe=Warengruppe.objects.get(name="Warengruppe G"),
            artikelnummer="101900",
            name="Ware N",
            inventurart=jahresinventur(),
        )
        karton = Gebinde.objects.create(
            ware=ware, name="Karton", faktor_in_basiseinheit=Decimal("10")
        )
        Zaehlposition.objects.create(
            inventur=inventur,
            ort=self.ort,
            artikel=artikel,
            gebinde=karton,
            eingabemenge=Decimal("3"),
            unerwartet=True,
        )
        self.assertTrue(klaeren(inventur).ausserhalb)
        self.assertEqual(self.arten_gelesen("inventur_abschluss", inventur), 1)


class NotizenEinerSeiteUnterEinerSperre(InventurSeiten):
    """Saving the check list costs the same with two changed remarks as with eight.

    Until 10/2026 every changed row checked the house, locked the inventory,
    read its remark and wrote it in a savepoint of its own: ten queries per
    row, 204 for twenty notes.
    """

    def setUp(self):
        super().setUp()
        self.inv = self.inventur(status=Inventurstatus.ZAEHLUNG)
        gruppe = Warengruppe.objects.get(name="Warengruppe G")
        self.artikel = [
            Hausartikel.objects.create(
                haus=self.haus,
                ware=Ware.objects.create(name=f"Ware {i}", basiseinheit=self.stueck),
                warengruppe=gruppe,
                artikelnummer=f"1015{i:02d}",
                name=f"Ware {i}",
            )
            for i in range(10)
        ]

    def gespeichert(self, eingaben: dict[int, str]) -> int:
        with CaptureQueriesContext(connection) as erfasst:
            notizen_speichern(self.inv, eingaben)
        return len(erfasst)

    def test_neue_geaenderte_und_geleerte_notizen(self):
        wenige = self.gespeichert({artikel.pk: "Notiz" for artikel in self.artikel[:2]})
        viele = self.gespeichert({artikel.pk: "Notiz" for artikel in self.artikel[2:]})
        self.assertEqual(viele, wenige)
        # Changed and emptied in one go: one statement each, not one per row.
        gemischt = {artikel.pk: "anders" for artikel in self.artikel[:5]}
        gemischt |= {artikel.pk: "" for artikel in self.artikel[5:]}
        self.assertLessEqual(self.gespeichert(gemischt), wenige + 1)
        self.assertEqual(
            sorted(Inventurnotiz.objects.filter(inventur=self.inv).values_list("text", flat=True)),
            ["anders"] * 5,
        )
