"""The check in front of pandas: what a workbook may hold before it is read.

The files are built here from a small real workbook, with parts swapped,
moved or added. No file of the exports is changed. Until 10/2026 every case
of 'Durchgelassen' went through the check, and pandas built gigabytes from a
few kilobytes, or the request ended on the error page.

The old xls files in 'test_workbooks/' were written once with xlwt 1.3.0,
which the project does not carry: a count sheet as WinTICKET prints it, and
four that cross the limits with a cell or two ('AlteArbeitsmappe',
'LangerText'). Until 10/2026 an xls went to xlrd unchecked.
"""

import io
import logging
import struct
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import openpyxl
import pandas as pd
import pandas.io.excel._base as pandas_excel
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from django.urls import reverse

from django_project.testing import AngemeldeteSeiten, inventur_anlegen
from organisation.models import Haus

from .formats import Lesefehler, workbook
from .formats.winticket import WinTicket
from .formats.workbook import MAX_BLAETTER, MAX_ZEILEN, arbeitsmappe_pruefen

HAUPT = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
BEZIEHUNG = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
BLATT = "xl/worksheets/sheet1.xml"
KAPUTT = "Die Datei ist keine Excel-Arbeitsmappe oder beschädigt."
ZU_UMFANGREICH = "Die Arbeitsmappe ist zu umfangreich, um sie zu lesen."
ZU_VIELE_ZEILEN = "Die Arbeitsmappe hat mehr als 50000 Zeilen."
ZU_VIELE_SPALTEN = "Die Arbeitsmappe hat mehr als 100 Spalten."
ZU_LANG = "Eine Zelle der Arbeitsmappe hat mehr als 1000 Zeichen."
ZU_VIELE_BLAETTER = f"Die Arbeitsmappe hat mehr als {MAX_BLAETTER} Blätter."
TEXTART = b"application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"


def grundmappe() -> bytes:
    """A real workbook as openpyxl writes it: one sheet, one row."""
    arbeitsmappe = openpyxl.Workbook()
    blatt = arbeitsmappe.active
    assert blatt is not None
    blatt.append(["101001", "Artikel C", "12"])
    puffer = io.BytesIO()
    arbeitsmappe.save(puffer)
    return puffer.getvalue()


def umbauen(
    mappe: bytes,
    teile: dict[str, bytes] | None = None,
    ersetzen: dict[str, tuple[bytes, bytes]] | None = None,
    methode: int = zipfile.ZIP_DEFLATED,
) -> bytes:
    """The workbook with parts swapped or added ('teile') and text replaced in parts."""
    teile, ersetzen = teile or {}, ersetzen or {}
    puffer = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(mappe)) as alt,
        zipfile.ZipFile(puffer, "w", methode) as neu,
    ):
        for eintrag in alt.infolist():
            if eintrag.filename in teile:
                continue
            inhalt = alt.read(eintrag.filename)
            if eintrag.filename in ersetzen:
                vorher, nachher = ersetzen[eintrag.filename]
                assert vorher in inhalt, (eintrag.filename, vorher)
                inhalt = inhalt.replace(vorher, nachher)
            neu.writestr(eintrag.filename, inhalt)
        for name, inhalt in teile.items():
            neu.writestr(name, inhalt)
    return puffer.getvalue()


def blatt(*zeilen: str, vorne: str = "") -> bytes:
    """A sheet in the namespace openpyxl reads, with the given rows."""
    return (
        f'<worksheet xmlns="{HAUPT}">{vorne}<sheetData>{"".join(zeilen)}</sheetData></worksheet>'
    ).encode()


def zeile(nummer: int | None, *zellen: str) -> str:
    kopf = "<row>" if nummer is None else f'<row r="{nummer}">'
    return kopf + "".join(zellen) + "</row>"


def zelle(wo: str, wert: str = "1") -> str:
    return f'<c r="{wo}" t="inlineStr"><is><t>{wert}</t></is></c>'


def pruefen(mappe: bytes) -> None:
    arbeitsmappe_pruefen(io.BytesIO(mappe))


def geteilte_texte(*texte: str, blatt_inhalt: bytes | None = None) -> bytes:
    """The real workbook with shared strings, each 'text' the inside of one '<si>'."""
    teile = {
        "xl/sharedStrings.xml": (
            f'<sst xmlns="{HAUPT}">' + "".join(f"<si>{text}</si>" for text in texte) + "</sst>"
        ).encode()
    }
    if blatt_inhalt is not None:
        teile[BLATT] = blatt_inhalt
    return umbauen(
        grundmappe(),
        teile,
        {
            "[Content_Types].xml": (
                b"</Types>",
                b'<Override PartName="/xl/sharedStrings.xml" ContentType="' + TEXTART + b'" />'
                b"</Types>",
            )
        },
    )


#: The old xls files, see above.
MAPPEN = Path(__file__).parent / "test_workbooks"


def xls(name: str) -> bytes:
    return (MAPPEN / name).read_bytes()


def biff2(*zellen: tuple[int, int, str]) -> bytes:
    """A bare sheet of Excel 2.1, which pandas hands to xlrd as well: one text per cell."""

    def satz(art: int, daten: bytes = b"") -> bytes:
        return struct.pack("<HH", art, len(daten)) + daten

    teile = [
        satz(0x0009, struct.pack("<HH", 0x0007, 0x0010)),
        satz(0x0042, struct.pack("<H", 1252)),
    ]
    for zeile, spalte, text in zellen:
        roh = text.encode("cp1252")
        kopf = struct.pack("<HH", zeile, spalte) + b"\x00\x00\x00" + bytes([len(roh)])
        teile.append(satz(0x0004, kopf + roh))
    return b"".join([*teile, satz(0x000A)])


def biff8_blaetter(anzahl: int) -> bytes:
    """A BIFF8 workbook in an OLE2 compound document that lists 'anzahl' empty sheets.

    pandas hands an xls to xlrd, and xlrd builds a sheet object for every
    BOUNDSHEET at once. All the BOUNDSHEETs here point at one empty worksheet
    substream, so the file stays small while the workbook claims many sheets.
    Only what xlrd needs to count the sheets and read them empty is written.
    This is no general xls writer. It replaces a binary fixture, so the file
    is reproducible and says what it contains.
    """

    def satz(code: int, daten: bytes = b"") -> bytes:
        return struct.pack("<HH", code, len(daten)) + daten

    def bof(streamtyp: int) -> bytes:
        return satz(0x0809, struct.pack("<HHHHIH", 0x0600, streamtyp, 0, 0, 0, 0))

    def globals_strom(blatt_stelle: int) -> bytes:
        teile = [bof(0x0005)]
        for nummer in range(anzahl):
            name = f"S{nummer}".encode("latin-1")
            # BOUNDSHEET: position(i32), visibility(u8), type(u8=0), then the
            # name as an 8-bit unicode string (length(u8), flags(u8=0), chars).
            inhalt = struct.pack("<iBBBB", blatt_stelle, 0, 0, len(name), 0) + name
            teile.append(satz(0x0085, inhalt))
        teile.append(satz(0x000A))
        return b"".join(teile)

    blatt_strom = bof(0x0010) + satz(0x000A)
    stelle = len(globals_strom(0))
    while len(globals_strom(stelle)) != stelle:
        stelle = len(globals_strom(stelle))
    strom = (globals_strom(stelle) + blatt_strom).ljust(4096, b"\x00")

    SEC = 512
    EINTRAEGE = SEC // 4
    EOCSID, FREESID, SATSID = -2, -1, -3
    ds = (len(strom) + SEC - 1) // SEC
    strom = strom.ljust(ds * SEC, b"\x00")
    # Sectors after the header: workbook stream, then one directory sector,
    # then the SAT sectors. The SAT count grows with the total, so solve for it.
    k = 1
    while (ds + 1 + k + EINTRAEGE - 1) // EINTRAEGE > k:
        k += 1
    sat = [i + 1 for i in range(ds)]
    if ds:
        sat[-1] = EOCSID
    sat += [EOCSID, *([SATSID] * k)]
    sat += [FREESID] * (k * EINTRAEGE - len(sat))

    def eintrag(name: str, art: int, first: int, groesse: int, wurzel: int) -> bytes:
        roh = name.encode("utf-16-le") + b"\x00\x00"
        kopf = roh.ljust(64, b"\x00") + struct.pack("<HBBiii", len(roh), art, 1, -1, -1, wurzel)
        schwanz = b"\x00" * 36 + struct.pack("<ii", first, groesse) + b"\x00" * 4
        return (kopf + schwanz).ljust(128, b"\x00")

    verzeichnis = (
        eintrag("Root Entry", 5, EOCSID, 0, 1) + eintrag("Workbook", 2, 0, len(strom), -1)
    ).ljust(SEC, b"\x00")

    kopf = bytearray(512)
    kopf[0:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    kopf[28:30] = b"\xfe\xff"
    kopf[30:34] = struct.pack("<HH", 9, 6)  # sector shift 512, short 64
    kopf[44:48] = struct.pack("<i", k)
    kopf[48:52] = struct.pack("<i", ds)  # first directory sector
    kopf[56:60] = struct.pack("<i", 4096)  # min size of a standard stream
    kopf[60:76] = struct.pack("<iiii", EOCSID, 0, EOCSID, 0)  # no SSAT, no MSAT extension
    kopf[76:512] = struct.pack("<109i", *(list(range(ds + 1, ds + 1 + k)) + [FREESID] * (109 - k)))
    return bytes(kopf) + strom + verzeichnis + struct.pack(f"<{k * EINTRAEGE}i", *sat)


class Durchgelassen(SimpleTestCase):
    """What pandas made of these files before: each would have passed the check."""

    def assertAbgewiesen(self, mappe: bytes, meldung: str):
        with self.assertRaisesMessage(Lesefehler, meldung):
            pruefen(mappe)

    def test_eine_zeilennummer_weit_hinten(self):
        """Two rows, numbered 1 and 300000. pandas fills the numbers between with empty rows."""
        mappe = umbauen(
            grundmappe(),
            {BLATT: blatt(zeile(1, zelle("A1")), zeile(300_000, zelle("C300000")))},
        )
        self.assertAbgewiesen(mappe, ZU_VIELE_ZEILEN)

    def test_zeilen_ohne_nummer_zaehlen_weiter(self):
        """Without 'r' a row follows the one before, also behind a numbered one."""
        zeilen = [zeile(49_990, zelle("A49990"))] + [zeile(None, zelle("A1"))] * 11
        self.assertAbgewiesen(umbauen(grundmappe(), {BLATT: blatt(*zeilen)}), ZU_VIELE_ZEILEN)

    def test_zeilen_mit_einer_nummer_die_schon_war(self):
        """openpyxl keeps every row it has read, emptied, until the sheet is done.

        A row whose number is not higher than the one before raised no count,
        and pandas made an empty table of a million such rows.
        """
        faelle = {
            "dieselbe": [zeile(1)] * (MAX_ZEILEN + 1),
            "fallend": [zeile(nummer) for nummer in range(MAX_ZEILEN, 0, -1)] + [zeile(1)],
        }
        for fall, zeilen in faelle.items():
            with self.subTest(fall):
                self.assertAbgewiesen(
                    umbauen(grundmappe(), {BLATT: blatt(*zeilen)}), ZU_VIELE_ZEILEN
                )

    def test_eine_zelle_in_spalte_xfd(self):
        """pandas pads every row to the widest: 16,384 columns per row."""
        mappe = umbauen(grundmappe(), {BLATT: blatt(zeile(1, zelle("A1"), zelle("XFD1", "x")))})
        self.assertAbgewiesen(mappe, ZU_VIELE_SPALTEN)

    def test_zellen_ohne_adresse_zaehlen_weiter(self):
        """openpyxl takes every child of a row as a cell, behind the one before."""
        mappe = umbauen(grundmappe(), {BLATT: blatt(zeile(1, "<c/>" * 101))})
        self.assertAbgewiesen(mappe, ZU_VIELE_SPALTEN)

    def test_ein_blatt_an_anderer_stelle(self):
        """The sheet lies where the relationships say, not necessarily under 'xl/worksheets/'."""
        zeilen = "".join(zeile(nummer, zelle(f"A{nummer}")) for nummer in range(1, 60_001))
        mappe = umbauen(
            grundmappe(),
            {BLATT: b"", "xl/blatt/sheet1.xml": blatt(zeilen)},
            {
                "xl/_rels/workbook.xml.rels": (
                    b"/xl/worksheets/sheet1.xml",
                    b"/xl/blatt/sheet1.xml",
                ),
                "[Content_Types].xml": (b"/xl/worksheets/sheet1.xml", b"/xl/blatt/sheet1.xml"),
            },
        )
        self.assertAbgewiesen(mappe, ZU_VIELE_ZEILEN)

    def test_styles_ueber_der_grenze(self):
        """Every part counts towards the size, not only sheets and shared strings."""
        styles = b'<styleSheet xmlns="' + HAUPT.encode() + b'"><!--'
        styles += b" " * (41 * 1024 * 1024) + b"--></styleSheet>"
        mappe = umbauen(grundmappe(), {"xl/styles.xml": styles})
        self.assertAbgewiesen(mappe, "Die Arbeitsmappe ist entpackt")

    def test_styles_aus_winzigen_elementen(self):
        """openpyxl builds the styles whole, and 2 MB of '<a/>' are half a million elements."""
        styles = f'<styleSheet xmlns="{HAUPT}">'.encode() + b"<a/>" * 600_000 + b"</styleSheet>"
        self.assertAbgewiesen(umbauen(grundmappe(), {"xl/styles.xml": styles}), ZU_UMFANGREICH)

    def test_ein_kaputter_eintrag_neben_dem_blatt(self):
        """openpyxl never reads it, but the check stopped at it and forgot what it had counted."""
        merkmal = b"MERKMAL-" + b"x" * 64
        puffer = io.BytesIO(umbauen(grundmappe()))
        with zipfile.ZipFile(puffer, "a") as archiv:
            archiv.writestr(
                zipfile.ZipInfo("xl/worksheets/zzz.xml"), merkmal, compress_type=zipfile.ZIP_STORED
            )
        roh = bytearray(puffer.getvalue())
        roh[roh.index(merkmal)] ^= 0xFF
        self.assertAbgewiesen(bytes(roh), KAPUTT)

    def test_eine_dtd(self):
        """Its entities could make more of a part than it weighs. No workbook carries one."""
        mappe = umbauen(
            grundmappe(),
            {BLATT: b'<!DOCTYPE worksheet [<!ENTITY z "1">]>' + blatt(zeile(1, zelle("A1")))},
        )
        self.assertAbgewiesen(mappe, KAPUTT)


class WasOpenpyxlZugleichHaelt(SimpleTestCase):
    """A row is let go after it is read. A part read whole is held whole."""

    #: 6,000 rows of 99 cells: about 600,000 elements, few per row.
    VIELE_ZEILEN = "".join(zeile(nummer, "<c/>" * 99) for nummer in range(1, 6_001))

    def test_viele_kleine_zeilen_im_blatt_gehen(self):
        pruefen(umbauen(grundmappe(), {BLATT: blatt(self.VIELE_ZEILEN)}))

    def test_dieselben_zeilen_in_den_styles_nicht(self):
        styles = f'<styleSheet xmlns="{HAUPT}">{self.VIELE_ZEILEN}</styleSheet>'.encode()
        with self.assertRaisesMessage(Lesefehler, ZU_UMFANGREICH):
            pruefen(umbauen(grundmappe(), {"xl/styles.xml": styles}))

    def test_und_nicht_in_einem_diagrammblatt(self):
        """The role comes from the package: openpyxl reads a chartsheet whole."""
        mappe = umbauen(
            grundmappe(),
            {BLATT: blatt(self.VIELE_ZEILEN)},
            {
                "xl/_rels/workbook.xml.rels": (
                    b"relationships/worksheet",
                    b"relationships/chartsheet",
                )
            },
        )
        with self.assertRaisesMessage(Lesefehler, ZU_UMFANGREICH):
            pruefen(mappe)

    def test_eine_riesige_zeile(self):
        """One cell of 6,000 elements: openpyxl builds the whole row before it lets go."""
        mappe = umbauen(grundmappe(), {BLATT: blatt(zeile(1, f"<c>{'<x/>' * 6_000}</c>"))})
        with self.assertRaisesMessage(Lesefehler, ZU_UMFANGREICH):
            pruefen(mappe)

    def test_geteilte_texte_bleiben_im_speicher(self):
        """Every shared string stays once it is read, the elements around it do not."""

        def mit_texten(anzahl: int) -> bytes:
            texte = f'<sst xmlns="{HAUPT}">' + "<si><t>x</t></si>" * anzahl + "</sst>"
            return umbauen(
                grundmappe(),
                {"xl/sharedStrings.xml": texte.encode()},
                {
                    "[Content_Types].xml": (
                        b"</Types>",
                        b'<Override PartName="/xl/sharedStrings.xml" ContentType="application/'
                        b'vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml" />'
                        b"</Types>",
                    )
                },
            )

        pruefen(mit_texten(200_000))
        with self.assertRaisesMessage(Lesefehler, ZU_UMFANGREICH):
            pruefen(mit_texten(300_000))


class LangerText(SimpleTestCase):
    """A shared string reaches the readers whole, in every cell that shows it.

    What they do with a cell (strip it, read it as a number, look at every
    character of a number) costs as much as it is long, and a few copies
    stay. Until 10/2026 a text of a megabyte in 50,000 rows of a workbook of a
    few kilobytes held the readers for minutes. A real export's longest cell
    has some sixty characters.
    """

    def test_ein_geteilter_text_ueber_der_grenze(self):
        with self.assertRaisesMessage(Lesefehler, ZU_LANG):
            pruefen(geteilte_texte(f"<t>{'x' * 1001}</t>"))

    def test_bis_an_die_grenze_geht_er_durch(self):
        pruefen(geteilte_texte(f"<t>{'x' * 1000}</t>", "<t>Artikel C</t>"))

    def test_die_stuecke_eines_textes_zaehlen_zusammen(self):
        """Rich text comes in runs, and openpyxl joins them into one string."""
        with self.assertRaisesMessage(Lesefehler, ZU_LANG):
            pruefen(geteilte_texte(f"<r><t>{'x' * 600}</t></r><r><t>{'x' * 401}</t></r>"))

    def test_jeder_text_zaehlt_fuer_sich(self):
        pruefen(geteilte_texte(*(f"<t>{'x' * 1000}</t>" for _ in range(30))))

    def test_ein_altes_xls_ebenso(self):
        """Excel 97 keeps its texts in a table of shared strings too, and xlrd hands them out."""
        with self.assertRaisesMessage(Lesefehler, ZU_LANG):
            pruefen(xls("text_over_limit.xls"))

    def test_der_leser_weist_die_mappe_ab_bevor_er_sie_liest(self):
        """1001 digits in the column of the quantity, in 500 rows."""
        zeilen = "".join(
            zeile(
                nummer,
                f'<c r="A{nummer}" t="s"><v>1</v></c>',
                f'<c r="C{nummer}" t="s"><v>0</v></c>',
            )
            for nummer in range(1, 501)
        )
        mappe = geteilte_texte(f"<t>{'1' * 1001}</t>", "<t>101001</t>", blatt_inhalt=blatt(zeilen))
        with self.assertRaisesMessage(Lesefehler, ZU_LANG):
            WinTicket().sollbestand_lesen(
                SimpleUploadedFile("soll.xlsx", mappe, "application/octet-stream")
            )


class Beschaedigt(SimpleTestCase):
    """What the zip throws while it is counted ends as the reader's sentence."""

    def kaputter_strom(self, methode: int) -> bytes:
        mappe = bytearray(umbauen(grundmappe(), methode=methode))
        eintrag = zipfile.ZipFile(io.BytesIO(bytes(mappe))).getinfo(BLATT)
        kopf = eintrag.header_offset
        beginn = kopf + 30 + int.from_bytes(mappe[kopf + 26 : kopf + 28], "little")
        beginn += int.from_bytes(mappe[kopf + 28 : kopf + 30], "little")
        for stelle in range(beginn + 4, beginn + 40):
            mappe[stelle] ^= 0xFF
        return bytes(mappe)

    def test_ein_kaputter_lzma_strom(self):
        with self.assertRaisesMessage(Lesefehler, KAPUTT):
            pruefen(self.kaputter_strom(zipfile.ZIP_LZMA))

    def test_ein_kaputter_bzip2_strom(self):
        with self.assertRaisesMessage(Lesefehler, KAPUTT):
            pruefen(self.kaputter_strom(zipfile.ZIP_BZIP2))

    def test_ein_name_der_kein_utf8_ist(self):
        """The zip says its names are UTF-8 (bit 11), and one is not."""
        mappe = bytearray(umbauen(grundmappe(), {"xl/worksheets/sheetX.xml": b"<x/>"}))
        verzeichnis = mappe.find(b"PK\x01\x02")
        while verzeichnis != -1:
            mappe[verzeichnis + 9] |= 0x08
            verzeichnis = mappe.find(b"PK\x01\x02", verzeichnis + 4)
        mappe[mappe.rfind(b"sheetX") + 5] = 0xFF
        with self.assertRaisesMessage(Lesefehler, KAPUTT):
            pruefen(bytes(mappe))


class Echt(SimpleTestCase):
    """Real workbooks pass, up to the limit, and pandas reads them as before."""

    def test_das_zaehlblatt_der_demo(self):
        pfad = Path(settings.BASE_DIR) / "demo" / "exports" / "sollbestand_kino_west.xlsx"
        with pfad.open("rb") as datei:
            arbeitsmappe_pruefen(datei)
            self.assertEqual(datei.tell(), 0)
            self.assertGreater(len(pd.read_excel(datei, header=None, dtype=str)), 10)

    def test_bis_an_die_grenze(self):
        zeilen = "".join(
            zeile(nummer, zelle(f"A{nummer}", "101001"), zelle(f"E{nummer}", "_____"))
            for nummer in range(1, MAX_ZEILEN + 1)
        )
        pruefen(umbauen(grundmappe(), {BLATT: blatt(zeilen)}))

    def test_eine_mappe_mit_geteilten_texten_wie_aus_excel(self):
        mappe = grundmappe()
        arbeitsmappe = openpyxl.load_workbook(io.BytesIO(mappe))
        puffer = io.BytesIO()
        arbeitsmappe.save(puffer)
        pruefen(puffer.getvalue())

    def test_keine_zip_datei_geht_an_den_leser(self):
        """A CSV is neither a zip nor an old xls. What it is says the reader."""
        pruefen(b"101001;4;Ware N\n")


class AlteArbeitsmappe(SimpleTestCase):
    """An old xls: xlrd builds every sheet at once and pads each to rows times columns."""

    def assertAbgewiesen(self, mappe: bytes, meldung: str):
        with self.assertRaisesMessage(Lesefehler, meldung):
            pruefen(mappe)

    def test_das_zaehlblatt_geht_und_liest_sich_wie_bisher(self):
        datei = SimpleUploadedFile("soll.xls", xls("count_sheet.xls"))
        arbeitsmappe_pruefen(datei)
        self.assertEqual(datei.tell(), 0)

        gelesen = WinTicket().sollbestand_lesen(datei)

        self.assertEqual(
            [(zeile.nummer, zeile.menge) for zeile in gelesen.zeilen],
            [
                ("101001", Decimal("12")),
                ("101002", Decimal("4.5")),
                ("101003", Decimal("0")),
                ("101004", Decimal("7")),
            ],
        )
        self.assertEqual(gelesen.fehler, [])

    def test_eine_zeile_hinter_der_grenze(self):
        """Row 50,001 after row 1: pandas fills the rows between."""
        self.assertAbgewiesen(xls("rows_over_limit.xls"), ZU_VIELE_ZEILEN)

    def test_eine_spalte_hinter_der_grenze(self):
        """Column 101 in one row: xlrd pads every row to it."""
        self.assertAbgewiesen(xls("columns_over_limit.xls"), ZU_VIELE_SPALTEN)

    def test_blaetter_die_einzeln_passen_aber_nicht_zusammen(self):
        """Three sheets of 50,000 rows and 100 columns: xlrd holds them all at once."""
        self.assertAbgewiesen(xls("sheets_over_limit.xls"), ZU_UMFANGREICH)

    def test_zu_viele_blaetter(self):
        """A workbook that only lists very many sheets. xlrd builds one object per sheet.

        The pre-check loaded and unloaded each sheet, one at a time, and let
        the file pass. pandas then built them all at once: 200,000 empty
        sheets of a file of 3.6 MB grew a worker by about 600 MB (measured
        2026-10-21). One over the limit is refused before anything is built.
        """
        self.assertAbgewiesen(biff8_blaetter(MAX_BLAETTER + 1), ZU_VIELE_BLAETTER)

    def test_bis_zur_blattgrenze_geht_es(self):
        """At the limit the file passes and xlrd reads its empty sheets."""
        datei = SimpleUploadedFile("viele.xls", biff8_blaetter(MAX_BLAETTER))
        arbeitsmappe_pruefen(datei)
        self.assertEqual(datei.tell(), 0)

    def test_der_bare_strom_von_excel_2_genauso(self):
        """Without the compound document around it. pandas hands it to xlrd all the same."""
        pruefen(biff2((0, 0, "101001"), (0, 1, "Artikel C"), (0, 2, "12")))
        self.assertAbgewiesen(biff2((0, 0, "101001"), (1, 150, "x")), ZU_VIELE_SPALTEN)

    def test_ueber_der_groesse(self):
        """Nothing is packed in an xls: its size is what xlrd holds."""
        self.assertAbgewiesen(
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * (41 * 1024 * 1024),
            "Die Arbeitsmappe ist entpackt",
        )

    def test_ein_kaputtes_xls_mit_dem_satz_des_lesers(self):
        """Until 10/2026 it passed, and the reader said it. The reason goes to the log."""
        with self.assertLogs("katalog.formats.workbook", logging.WARNING):
            self.assertAbgewiesen(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600, KAPUTT)

    def test_die_anfaenge_sind_die_von_pandas(self):
        """What pandas hands to xlrd it decides by the first bytes. A new kind would pass.

        Read from the module itself: the stubs of pandas do not name it.
        """
        self.assertEqual(
            set(vars(workbook)["_XLS_ANFAENGE"]), set(vars(pandas_excel)["XLS_SIGNATURES"])
        )


class UeberDieSeite(AngemeldeteSeiten):
    """Through the import page: a sentence at the file field instead of the error page."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.haus = Haus.objects.create(name="Kino Nord")

    def test_ein_kaputter_strom_steht_am_feld(self):
        mappe = Beschaedigt().kaputter_strom(zipfile.ZIP_LZMA)
        antwort = self.client.post(
            reverse("hausartikel_import", args=[self.haus.pk]),
            {
                "format": "winticket",
                "datei": SimpleUploadedFile("export.xlsx", mappe, "application/octet-stream"),
            },
        )
        self.assertEqual(antwort.status_code, 200)
        self.assertEqual(antwort.context["form"].errors["datei"], [KAPUTT])

    def test_ein_xls_steht_auf_beiden_seiten_am_feld(self):
        """The article import and the target stock check it the same way."""
        inventur = inventur_anlegen(self.haus, stichtag=date(2026, 1, 31))
        for adresse in (
            reverse("hausartikel_import", args=[self.haus.pk]),
            inventur.get_absolute_url(),
        ):
            with self.subTest(adresse):
                antwort = self.client.post(
                    adresse,
                    {
                        "format": "winticket",
                        "datei": SimpleUploadedFile("export.xls", xls("rows_over_limit.xls")),
                    },
                )
                self.assertEqual(antwort.status_code, 200)
                self.assertEqual(antwort.context["form"].errors["datei"], [ZU_VIELE_ZEILEN])

    def test_das_zaehlblatt_liest_der_leser_weiter(self):
        mappe = umbauen(
            grundmappe(),
            {
                BLATT: blatt(
                    zeile(1, zelle("A1", "101001"), zelle("B1", "Artikel C"), zelle("C1", "12"))
                )
            },
        )
        gelesen = WinTicket().sollbestand_lesen(
            SimpleUploadedFile("soll.xlsx", mappe, "application/octet-stream")
        )
        self.assertEqual([z.nummer for z in gelesen.zeilen], ["101001"])
