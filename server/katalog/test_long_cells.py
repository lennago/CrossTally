"""A long cell in many rows: a message quotes it short, and past a limit rows are only counted.

A workbook keeps a text once and points at it from every cell that shows it
(the shared strings). Until 10/2026 every message about a refused row
carried the whole cell. A workbook of 6.6 KB, whose one text of a megabyte
stood in five hundred rows, passed the check and the upload limit, and
reading it held a gigabyte of messages. Since then the check of the
workbook refuses a shared text over 1,000 characters ('MAX_ZEICHEN'). The
workbooks here stay at that limit, in many rows, and the peak of what
reading holds is measured. A table straight into the plan has no such
check, and there the text is longer.
"""

import gc
import tracemalloc
from collections.abc import Callable
from decimal import Decimal

import pandas as pd
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.utils import translation

from django_project.testing import AngemeldeteSeiten, inventur_anlegen
from organisation.models import Haus
from scripts.transform import transform_artikelbestandsliste

from .formats import MAX_MELDUNGEN, Zeilenmeldungen, zitat
from .formats.winticket import WinTicket
from .formats.workbook import MAX_ZEICHEN
from .models import GEBINDENAME_LAENGE, Basiseinheit, Gebinde, Hausartikel, Ware, Warengruppe
from .services import planen
from .test_services import SPALTEN
from .test_services import zeile as artikelzeile
from .test_workbook_guard import BLATT, HAUPT, blatt, grundmappe, umbauen, zeile

TEXTART = b"application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"

#: The long text of a table. Before, every message about its rows held it once more.
LANG = "x" * 200_000
#: The longest text a workbook may share, and the rows that show it.
AM_RAND = "x" * MAX_ZEICHEN
#: The longest pack name the plan takes.
GEBINDE_AM_RAND = "K" * GEBINDENAME_LAENGE
ZEILEN = 12_000

#: What reading such a file may hold at once. Measured are 3.5 MB.
#: Before, 12,000 rows at the limit held 24.5 MB of messages: every one
#: quoted the 1,000 characters, two bytes each, because of the quotation marks.
SPEICHER_HOECHSTENS = 20 * 1024 * 1024


def geteilte_mappe(texte: list[str], zeilen: list[list[int]]) -> SimpleUploadedFile:
    """A workbook as Excel writes it: every text once, each cell points at one by its number."""
    spalten = "ABCDEFGH"
    inhalt = "".join(
        zeile(
            nummer,
            *(
                f'<c r="{spalten[stelle]}{nummer}" t="s"><v>{text}</v></c>'
                for stelle, text in enumerate(reihe)
            ),
        )
        for nummer, reihe in enumerate(zeilen, start=1)
    )
    geteilt = f'<sst xmlns="{HAUPT}">' + "".join(f"<si><t>{t}</t></si>" for t in texte) + "</sst>"
    mappe = umbauen(
        grundmappe(),
        {BLATT: blatt(inhalt), "xl/sharedStrings.xml": geteilt.encode()},
        {
            "[Content_Types].xml": (
                b"</Types>",
                b'<Override PartName="/xl/sharedStrings.xml" ContentType="'
                + TEXTART
                + b'" /></Types>',
            )
        },
    )
    return SimpleUploadedFile("datei.xlsx", mappe, "application/octet-stream")


def mit_spitze[T](aufruf: Callable[[], T]) -> tuple[T, int]:
    """What 'aufruf' returns, and the most it held in memory at once while it ran."""
    gc.collect()
    schon = tracemalloc.is_tracing()
    if not schon:
        tracemalloc.start()
    tracemalloc.reset_peak()
    vorher, _spitze = tracemalloc.get_traced_memory()
    try:
        ergebnis = aufruf()
        _jetzt, spitze = tracemalloc.get_traced_memory()
    finally:
        if not schon:
            tracemalloc.stop()
    return ergebnis, spitze - vorher


def csv_datei(inhalt: str) -> SimpleUploadedFile:
    return SimpleUploadedFile("soll.csv", inhalt.encode("utf-8"), "text/csv")


class Zitat(SimpleTestCase):
    def test_was_kurz_ist_bleibt_wie_es_ist(self):
        for wert in ("", "101001", "Summe: Warengruppe G", "x" * 60):
            with self.subTest(wert=wert):
                self.assertEqual(zitat(wert), wert)

    def test_was_laenger_ist_endet_nach_sechzig_zeichen(self):
        self.assertEqual(zitat("x" * 61), "x" * 60 + "…")
        # A space at the cut does not stand before the ellipsis.
        self.assertEqual(zitat("x" * 59 + " Ware N"), "x" * 59 + "…")


class Zaehlung(SimpleTestCase):
    def test_bis_zur_grenze_steht_jede_zeile_da(self):
        meldungen = Zeilenmeldungen()
        for stelle in range(MAX_MELDUNGEN):
            meldungen.melden(f"Zeile {stelle}")

        self.assertEqual(meldungen.liste(), [f"Zeile {stelle}" for stelle in range(100)])

    def test_dahinter_wird_gezaehlt_und_die_zahl_steht_vorn(self):
        """The page shows ten messages and counts the rest. A count at the end would be lost."""
        meldungen = Zeilenmeldungen()
        for stelle in range(250):
            meldungen.melden(f"Zeile {stelle}")

        liste = meldungen.liste()

        self.assertEqual(
            liste[0],
            "250 Zeilen der Datei wurden übergangen. Einzeln genannt werden nur die ersten 100.",
        )
        self.assertEqual(liste[1:], [f"Zeile {stelle}" for stelle in range(100)])
        with translation.override("en"):
            self.assertEqual(
                meldungen.liste()[0],
                "250 rows of the file were skipped. Only the first 100 are named one by one.",
            )


class Zaehlblatt(SimpleTestCase):
    def test_eine_lange_zelle_in_vielen_zeilen_haelt_den_speicher_nicht(self):
        """One text of 1,000 characters as the number of 12,000 rows, a quantity beside it."""
        datei = geteilte_mappe([AM_RAND, "Ware N", "12"], [[0, 1, 2]] * ZEILEN)

        gelesen, spitze = mit_spitze(lambda: WinTicket().sollbestand_lesen(datei))

        self.assertLess(spitze, SPEICHER_HOECHSTENS)
        self.assertEqual(gelesen.zeilen, [])
        self.assertEqual(len(gelesen.fehler), MAX_MELDUNGEN + 1)
        self.assertEqual(
            gelesen.fehler[0],
            "12000 Zeilen der Datei wurden übergangen. Einzeln genannt werden nur die ersten 100.",
        )
        self.assertEqual(gelesen.fehler[1], f"Zeile 1: „{'x' * 60}…“ ist keine Artikelnummer.")

    def test_eine_zu_lange_nummer_ebenso(self):
        """A number of 1,000 digits is a number, but too long. The message quotes its start."""
        datei = geteilte_mappe(["1" * MAX_ZEICHEN, "Ware N", "12"], [[0, 1, 2]] * ZEILEN)

        gelesen, spitze = mit_spitze(lambda: WinTicket().sollbestand_lesen(datei))

        self.assertLess(spitze, SPEICHER_HOECHSTENS)
        self.assertEqual(len(gelesen.fehler), MAX_MELDUNGEN + 1)
        self.assertEqual(
            gelesen.fehler[1],
            f"Zeile 1: Die Artikelnummer „{'1' * 60}…“ ist länger als 20 Zeichen.",
        )


class Csv(SimpleTestCase):
    def test_ein_langes_feld_wird_kurz_zitiert(self):
        gelesen = WinTicket().sollbestand_lesen(csv_datei(f"Kopf;Zeile\n{'N' * 1000};viel\n"))

        self.assertEqual(gelesen.fehler, [f"Zeile 2: „{'N' * 60}…“ ist keine Menge."])

    def test_viele_unlesbare_zeilen_werden_gezaehlt(self):
        """The first row may be a header and stays silent: 249 rows remain."""
        gelesen = WinTicket().sollbestand_lesen(csv_datei("101001;keine Zahl\n" * 250))

        self.assertEqual(len(gelesen.fehler), MAX_MELDUNGEN + 1)
        self.assertEqual(
            gelesen.fehler[0],
            "249 Zeilen der Datei wurden übergangen. Einzeln genannt werden nur die ersten 100.",
        )
        self.assertEqual(gelesen.fehler[1], "Zeile 2: „101001;keine Zahl“ ist keine Menge.")
        self.assertEqual(gelesen.fehler[-1], "Zeile 101: „101001;keine Zahl“ ist keine Menge.")


class AufDerSeite(AngemeldeteSeiten):
    """The page shows ten messages and counts the rest. The count of the file stands first."""

    def test_die_zahl_der_uebergangenen_zeilen_steht_vorn(self):
        inventur = inventur_anlegen(Haus.objects.create(name="Kino Nord"))

        antwort = self.client.post(
            inventur.get_absolute_url(),
            {"datei": csv_datei("101001;keine Zahl\n" * 250), "format": "winticket"},
            follow=True,
        )

        texte = [str(meldung) for meldung in antwort.context["messages"]]
        zahl = "249 Zeilen der Datei wurden übergangen. Einzeln genannt werden nur die ersten 100."
        self.assertIn(zahl, texte)
        self.assertEqual(
            texte[texte.index(zahl) + 1], "Zeile 2: „101001;keine Zahl“ ist keine Menge."
        )
        self.assertEqual(texte[-1], "… und 91 weitere unlesbare Zeilen.")


class Artikelstamm(TestCase):
    """The plan of the article import says the same as briefly, and counts the same way."""

    @classmethod
    def setUpTestData(cls):
        cls.haus = Haus.objects.create(name="Kino Nord")

    def plan(self, *zeilen: dict):
        return planen(pd.DataFrame(list(zeilen), columns=SPALTEN), self.haus)

    def test_eine_lange_nummer_in_vielen_zeilen_haelt_den_speicher_nicht(self):
        """One number of 1,000 characters in 12,000 rows of the export, each with its own name."""
        texte = ["Artikel", "Bezeichnung", "1" + AM_RAND[1:], *(f"Ware {i}" for i in range(ZEILEN))]
        datei = geteilte_mappe(texte, [[0, 1], *([2, 3 + i] for i in range(ZEILEN))])

        def lesen():
            return planen(WinTicket().artikelstamm_lesen(datei, self.haus.name), self.haus)

        (plan, meldungen), spitze = mit_spitze(lesen)

        self.assertLess(spitze, SPEICHER_HOECHSTENS)
        self.assertEqual(plan, [])
        self.assertEqual(len(meldungen), MAX_MELDUNGEN + 1)
        self.assertEqual(
            meldungen[0],
            "12000 Zeilen der Datei wurden übergangen. Einzeln genannt werden nur die ersten 100.",
        )
        self.assertEqual(
            meldungen[1],
            f"„1{'x' * 59}…“: Die Artikelnummer ist länger als 20 Zeichen, übergangen.",
        )

    def test_jede_meldung_zitiert_die_zelle_kurz(self):
        """A sum row is no number, one of 1000 digits is too long, the name is cut."""
        _plan, meldungen = self.plan(
            artikelzeile("Summe " + "x" * 994, "Ware N", "Karton", 6),
            artikelzeile("1" * 1000, "Ware M", "Karton", 6),
            artikelzeile("101003", "N" * 150, "Karton", 6, warengruppe="W" * 101),
        )

        self.assertEqual(
            meldungen,
            [
                f"„Summe {'x' * 54}…“ ist keine Artikelnummer, wie WinTICKET (Compeso) sie führt.",
                f"„{'1' * 60}…“: Die Artikelnummer ist länger als 20 Zeichen, übergangen.",
                f"101003 „{'N' * 60}…“: Die Warengruppe ist länger als 100 Zeichen, übergangen.",
            ],
        )

    def test_ein_langer_gebindename_in_vielen_zeilen_haelt_den_speicher_nicht(self):
        """A pack name of 200,000 characters with two quantities, under 150 numbers.

        Until 10/2026 every row came into the plan, and the hint that the
        file contradicts itself quoted the whole name. Now the rows stay out
        with a message, as for the other columns.
        """
        zeilen = [
            artikelzeile(f"{101000 + i}", f"Ware {i}", LANG, menge)
            for i in range(150)
            for menge in (6, 12)
        ]

        (plan, meldungen), spitze = mit_spitze(lambda: self.plan(*zeilen))

        self.assertLess(spitze, SPEICHER_HOECHSTENS)
        self.assertEqual(plan, [])
        self.assertEqual(
            meldungen[0],
            "300 Zeilen der Datei wurden übergangen. Einzeln genannt werden nur die ersten 100.",
        )
        self.assertEqual(
            meldungen[1],
            "101000 „Ware 0“: Der Name des Gebindes ist länger als 100 Zeichen, übergangen.",
        )

    def test_der_hinweis_auf_zwei_mengen_zitiert_den_gebindenamen_kurz(self):
        (satz,), _meldungen = self.plan(
            artikelzeile("101001", "Ware N", GEBINDE_AM_RAND, 6),
            artikelzeile("101001", "Ware N", GEBINDE_AM_RAND, 12),
        )

        self.assertIn(
            f"Gebinde „{'K' * 60}…“ steht in der Datei mit mehreren Mengen: 6; 12.", satz.hinweise
        )

    def test_ein_langer_gebindename_im_konflikt_ebenso(self):
        """The house carries the article, and the file brings a quantity the product does not."""
        ware = self.ware()
        Gebinde.objects.create(ware=ware, name="Karton", faktor_in_basiseinheit=Decimal("6"))
        Hausartikel.objects.create(
            haus=self.haus,
            ware=ware,
            warengruppe=Warengruppe.objects.create(name="Warengruppe G"),
            artikelnummer="101001",
            name="Ware N",
        )

        (satz,), _meldungen = self.plan(artikelzeile("101001", "Ware N", GEBINDE_AM_RAND, 12))

        self.assertIn(
            f"In der Datei steht „{'K' * 60}… = 12“. Diese Menge ist bei „Ware N“ nicht "
            "hinterlegt (Karton = 6).",
            satz.hinweise,
        )

    def test_ein_langer_gebindename_mit_der_menge_eines_inaktiven_ebenso(self):
        ware = self.ware()
        Gebinde.objects.create(
            ware=ware, name="Karton", faktor_in_basiseinheit=Decimal("12"), aktiv=False
        )

        (satz,), _meldungen = self.plan(artikelzeile("101001", "Ware N", GEBINDE_AM_RAND, 12))

        anfang = f"„{'K' * 60}… = 12“ hat die Menge des inaktiven Gebindes „Karton“."
        self.assertEqual([h for h in satz.hinweise if h.startswith(anfang)], [satz.hinweise[0]])

    def ware(self) -> Ware:
        return Ware.objects.create(
            name="Ware N", basiseinheit=Basiseinheit.objects.get(kennung="STUECK")
        )


class Infozeile(SimpleTestCase):
    """The row below an article in the raw export: "gekauft in Kiste(n) zu 24 Stück(e)"."""

    def test_eine_lange_infozeile_in_vielen_zeilen_haelt_den_speicher_nicht(self):
        """Twenty articles below the same info row of a megabyte, as a workbook hands it on.

        The pattern copied the pack name it found into every row: 21 MB
        (until 10/2026). The check of the workbook refuses such a text by
        now, the table of the command line or a CSV does not.
        """
        info = f"gekauft in {'K' * 1_000_000} zu 6 Stück(e)"
        roh = pd.DataFrame(
            [zeile for i in range(20) for zeile in ([f"{101000 + i}", f"Ware {i}"], [None, info])],
            columns=["artikelnummer", "name"],
        )

        tabelle, spitze = mit_spitze(lambda: transform_artikelbestandsliste(roh, "Kino Nord"))

        self.assertLess(spitze, 2 * 1024 * 1024)
        self.assertEqual(tabelle["gebinde"].tolist(), [""] * 20)
