"""What a workbook may hold before pandas reads it.

pandas reads an xlsx through openpyxl, and what that costs is not what the
upload weighs. An xlsx is a zip whose parts unpack to many times their size.
openpyxl builds most parts as a whole tree (the content types, the workbook,
the relationships, the styles), the worksheet row by row and the shared
strings string by string. pandas then fills every row number up to the
highest and pads every row to the widest. Until 10/2026 the check measured
only the parts under 'xl/worksheets/' and 'xl/sharedStrings', counted row
tags instead of row numbers and let a file pass that failed while it was
counted. A row numbered 300000 after row 1, one cell in column XFD, a sheet
at another path or a styles part of 60 MB then cost gigabytes, from a file
of a few kilobytes.

The check therefore reads every part the way openpyxl does, with expat, and
counts what openpyxl would build of it: rows by their number and every row
element, columns by their position, and the elements and attributes it
holds at once. It counts only and keeps nothing. Every limit lies far above
a real export, which has a few thousand rows of five columns.

A shared string is counted too ('MAX_ZEICHEN'): every cell that shows it
hands the readers the whole text. What they do with a cell (strip it, read
it as a number) costs as much as it is long, so one long text in every row
multiplies. Only the shared strings repeat, a text in a cell itself stands
in the file once and its size counts already.

An old xls goes from pandas to xlrd, which builds every sheet at once and
pads each to its rows times its widest row ('_alte_arbeitsmappe_pruefen').
The same limits apply there.
"""

import io
import logging
import os
import posixpath
import zipfile
from typing import IO
from xml.parsers import expat

import xlrd
from django.template.defaultfilters import filesizeformat
from django.utils.translation import gettext as _
from openpyxl.utils.cell import coordinate_to_tuple, range_boundaries

from .base import Lesefehler

#: All parts together, unpacked. pandas loads the whole sheet into memory: a
#: file of 10 MB (under the upload limit) with a million rows took 89 s and
#: 1.5 GB (measured 2026-10-13). The sheets of a real export unpack to a few MB.
MAX_ARBEITSMAPPE_MB = 40
#: Rows by their number, because pandas fills every number up to the highest
#: with an empty row. No house has that many articles. And every row of a
#: sheet, whatever its number: openpyxl empties a row once it has read it but
#: keeps the element until the sheet is done. 400,000 rows numbered 1 took
#: 44 MB from a file of 14 KB and gave an empty table (measured 2026-10-21).
MAX_ZEILEN = 50_000
#: Columns by their position. pandas pads every row to the widest, and one
#: cell in column XFD made 16,384 of every row. A real export has five.
MAX_SPALTEN = 100
#: Elements and attributes openpyxl holds at once: every part it reads as a
#: whole tree, what a sheet holds outside its rows, and two per shared
#: string, for the string it keeps. A tree costs about 80 to 300 bytes per
#: element, and 40 MB of tiny elements made a tree of more than a gigabyte.
MAX_BESTANDTEILE = 500_000
#: What one row or one shared string may hold. openpyxl builds each of them
#: whole before it lets go of it.
MAX_JE_ZEILE = 5_000
#: Parts in the zip. A real export has about ten, and every one is opened.
MAX_TEILE = 1_000
#: Characters of one shared string. A real export's longest cell has some
#: sixty. Until 10/2026 there was no limit, and a text of a megabyte in 500
#: rows held the reader for eight seconds, from a file of 4 KB. At this limit
#: the largest file the other limits allow takes half a second longer than
#: with texts of one character, seven seconds in all (measured 2026-10-21).
MAX_ZEICHEN = 1_000
#: Sheets in an old xls. xlrd builds a sheet object for every entry of the
#: workbook at once, about 3 KB each: 200,000 sheets pointing at one empty
#: sheet grew a worker by 600 MB from a file of 3.6 MB (measured 2026-10-21).
#: An article list or a count sheet is one sheet. A hundred is far above that
#: and above every export seen, and bounds what xlrd builds after the check.
MAX_BLAETTER = 100

#: What pandas hands to xlrd ('pandas.io.excel._base.XLS_SIGNATURES'): the
#: compound document of an xls since Excel 97, and the bare streams before it.
_XLS_ANFAENGE = (
    b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
    b"\x09\x00\x04\x00\x07\x00\x10\x00",
    b"\x09\x02\x06\x00\x00\x00\x10\x00",
    b"\x09\x04\x06\x00\x00\x00\x10\x00",
)

#: The namespace openpyxl reads rows and shared strings in.
_HAUPT = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_ZEILE = f"{_HAUPT}}}row"
_TEXT = f"{_HAUPT}}}si"
#: The content types under which a part may be large: openpyxl reads a sheet
#: row by row and the shared strings string by string.
_BLATTART = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
_TEXTART = "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"
_INHALTSTYPEN = "[Content_Types].xml"
#: Parts openpyxl reads as a whole tree under a fixed name, whatever else points there.
_FESTE_TEILE = frozenset(
    {_INHALTSTYPEN, "xl/workbook.xml", "xl/styles.xml", "docProps/core.xml", "docProps/custom.xml"}
)

#: The roles in which openpyxl reads a part piece by piece. Every other part it
#: builds whole, or it does not read it at all.
_BLATT = "blatt"
_TEXTE = "texte"

logger = logging.getLogger(__name__)


def _beschaedigt() -> Lesefehler:
    return Lesefehler(_("Die Datei ist keine Excel-Arbeitsmappe oder beschädigt."))


def _zu_umfangreich() -> Lesefehler:
    return Lesefehler(_("Die Arbeitsmappe ist zu umfangreich, um sie zu lesen."))


def _zu_viele_zeilen() -> Lesefehler:
    """Counting stops at the first row too many. How many follow nobody needs to know."""
    return Lesefehler(
        _("Die Arbeitsmappe hat mehr als %(grenze)s Zeilen.") % {"grenze": MAX_ZEILEN}
    )


def _zu_viele_spalten() -> Lesefehler:
    return Lesefehler(
        _("Die Arbeitsmappe hat mehr als %(grenze)s Spalten.") % {"grenze": MAX_SPALTEN}
    )


def _zu_lange_zelle() -> Lesefehler:
    return Lesefehler(
        _("Eine Zelle der Arbeitsmappe hat mehr als %(grenze)s Zeichen.") % {"grenze": MAX_ZEICHEN}
    )


def _zu_viele_blaetter() -> Lesefehler:
    return Lesefehler(
        _("Die Arbeitsmappe hat mehr als %(grenze)s Blätter.") % {"grenze": MAX_BLAETTER}
    )


def _zu_gross(groesse: int) -> Lesefehler:
    return Lesefehler(
        _("Die Arbeitsmappe ist entpackt %(gross)s groß. Erlaubt sind höchstens %(grenze)s.")
        % {
            "gross": filesizeformat(groesse),
            "grenze": filesizeformat(MAX_ARBEITSMAPPE_MB * 1024 * 1024),
        }
    )


def _attribut(attribute: list[str], name: str) -> str | None:
    """An attribute from expat's flat list (name, value, name, value …)."""
    for stelle in range(0, len(attribute), 2):
        if attribute[stelle] == name:
            return attribute[stelle + 1]
    return None


def _zeilennummer(wert: str | None) -> int | None:
    """The number of a row as openpyxl reads it ('WorkSheetParser.parse_row'), or none."""
    if wert is None:
        return None
    try:
        return int(wert)
    except ValueError:
        pass
    try:
        zahl = float(wert)
    except ValueError:
        return None
    return int(zahl) if zahl.is_integer() else None


def _spalte(wert: str | None) -> int | None:
    """The column of a cell as openpyxl reads it, or none if it reads none there."""
    if not wert:
        return None
    try:
        return coordinate_to_tuple(wert)[1]
    except ValueError, KeyError, IndexError:
        return None


class _Zaehlung:
    """Counts the parts of a workbook one by one and refuses as soon as it is too much.

    'bestand' is what openpyxl would hold at once over all parts: a part it
    reads whole counts entirely, of a sheet only what stands outside its rows
    (a row is let go after it is read), of the shared strings what stands
    outside the strings and two per string.
    """

    def __init__(self) -> None:
        self.bestand = 0
        #: From the content types: (part, content type).
        self.inhaltstypen: list[tuple[str, str]] = []
        #: From the relationship parts: (type, target).
        self.beziehungen: list[tuple[str, str]] = []

    def zaehlen(self, archiv: zipfile.ZipFile, eintrag: zipfile.ZipInfo, rolle: str | None):
        teil = _Teil(self, eintrag.filename, rolle)
        parser = expat.ParserCreate(namespace_separator="}")
        parser.ordered_attributes = True
        parser.StartElementHandler = teil.anfang
        parser.EndElementHandler = teil.ende
        parser.StartDoctypeDeclHandler = teil.dokumenttyp
        if rolle == _TEXTE:
            # Only there: in a sheet every value would call it.
            parser.buffer_text = True
            parser.CharacterDataHandler = teil.text
        with archiv.open(eintrag) as quelle:
            try:
                while stueck := quelle.read(1024 * 1024):
                    parser.Parse(stueck, False)
                parser.Parse(b"", True)
            except expat.ExpatError:
                # No XML (an image), or broken XML: openpyxl stops at the same
                # place, after what has been counted so far.
                return

    def dazu(self, anzahl: int) -> None:
        self.bestand += anzahl
        if self.bestand > MAX_BESTANDTEILE:
            raise _zu_umfangreich()

    def rollen(self, namen: list[str]) -> dict[str, str | None]:
        """Which parts openpyxl reads piece by piece, as far as the package says so.

        A sheet is what a relationship of the type worksheet points at, the
        shared strings what the content types declare as such. A part that
        anything else points at, that has another content type, a fixed name
        or is itself a relationships part, openpyxl may read whole. It then
        counts whole, whatever its role elsewhere.
        """
        blaetter, texte, ganz = set(), set(), set(_FESTE_TEILE)
        for name, art in self.inhaltstypen:
            if art == _TEXTART:
                texte.add(name)
            elif art != _BLATTART:
                ganz.add(name)
        for art, ziel in self.beziehungen:
            if art.endswith("/worksheet") and "chartsheet" not in art:
                blaetter.add(ziel)
            elif not art.endswith("/sharedStrings"):
                ganz.add(ziel)
        rollen: dict[str, str | None] = {}
        for name in namen:
            if name in ganz or name.endswith(".rels") or (name in blaetter and name in texte):
                rollen[name] = None
            elif name in blaetter:
                rollen[name] = _BLATT
            elif name in texte:
                rollen[name] = _TEXTE
            else:
                rollen[name] = None
        return rollen


class _Teil:
    """The handlers for one part. openpyxl reads a row at its end, the cells in their order."""

    def __init__(self, zaehlung: _Zaehlung, name: str, rolle: str | None):
        self.zaehlung = zaehlung
        self.name = name
        self.rolle = rolle
        #: What openpyxl lets go of after reading it: a row of a sheet, a string.
        self.einheit = {_BLATT: _ZEILE, _TEXTE: _TEXT}.get(rolle or "")
        self.tiefe = 0
        self.einheit_tiefe: int | None = None
        self.einheit_teile = 0
        #: The characters of the shared string read so far.
        self.zeichen = 0
        #: Open rows: [depth, column so far, 'r']. Rows in any namespace count:
        #: counting too much only refuses a made-up file.
        self.zeilen: list[list] = []
        #: openpyxl's 'row_counter': a row without number follows the one before.
        self.zeilenzaehler = 0
        #: Every row element, also one whose number was there before or is lower.
        self.zeilen_gelesen = 0

    def dokumenttyp(self, *_angaben) -> None:
        """A DTD has no place in a workbook, and its entities could multiply what is counted."""
        raise _beschaedigt()

    def anfang(self, name: str, attribute: list[str]) -> None:
        teile = 1 + len(attribute) // 2
        self.tiefe += 1
        lokal = name.rpartition("}")[2]
        if self.zeilen and self.zeilen[-1][0] == self.tiefe - 1:
            # openpyxl takes every child of a row as a cell: at its 'r', or
            # behind the one before.
            zeile = self.zeilen[-1]
            spalte = _spalte(_attribut(attribute, "r"))
            zeile[1] = spalte if spalte is not None else zeile[1] + 1
            if zeile[1] > MAX_SPALTEN:
                raise _zu_viele_spalten()
        if lokal == "row":
            self.zeilen.append([self.tiefe, 0, _attribut(attribute, "r")])
            self.zeilen_gelesen += 1
            if self.zeilen_gelesen > MAX_ZEILEN:
                raise _zu_viele_zeilen()
        elif lokal == "dimension":
            self._umfang(_attribut(attribute, "ref"))
        elif self.name == _INHALTSTYPEN and lokal == "Override":
            # As openpyxl reads it: the name without its leading slash.
            teilname = (_attribut(attribute, "PartName") or "")[1:]
            self.zaehlung.inhaltstypen.append((teilname, _attribut(attribute, "ContentType") or ""))
        elif self.name.endswith(".rels") and lokal == "Relationship":
            self._beziehung(attribute)

        if self.rolle == _TEXTE and name == _TEXT:
            # Every string stays in memory once it is read.
            self.zaehlung.dazu(2)
        if self.einheit_tiefe is None and name == self.einheit:
            self.einheit_tiefe, self.einheit_teile, self.zeichen = self.tiefe, 0, 0
        if self.einheit_tiefe is None:
            self.zaehlung.dazu(teile)
        else:
            self.einheit_teile += teile
            if self.einheit_teile > MAX_JE_ZEILE:
                raise _zu_umfangreich()

    def ende(self, _name: str) -> None:
        if self.zeilen and self.zeilen[-1][0] == self.tiefe:
            nummer = _zeilennummer(self.zeilen.pop()[2])
            self.zeilenzaehler = nummer if nummer is not None else self.zeilenzaehler + 1
            if self.zeilenzaehler > MAX_ZEILEN:
                raise _zu_viele_zeilen()
        if self.einheit_tiefe == self.tiefe:
            self.einheit_tiefe = None
        self.tiefe -= 1

    def text(self, daten: str) -> None:
        """The text of a shared string, its runs together, as openpyxl joins them."""
        if self.einheit_tiefe is None:
            return
        self.zeichen += len(daten)
        if self.zeichen > MAX_ZEICHEN:
            raise _zu_lange_zelle()

    def _umfang(self, bereich: str | None) -> None:
        """What the sheet's dimension claims counts too. A real export claims what it holds."""
        try:
            _von_spalte, _von_zeile, bis_spalte, bis_zeile = range_boundaries(bereich or "")
        except ValueError, KeyError, TypeError:
            return
        if bis_zeile is not None and bis_zeile > MAX_ZEILEN:
            raise _zu_viele_zeilen()
        if bis_spalte is not None and bis_spalte > MAX_SPALTEN:
            raise _zu_viele_spalten()

    def _beziehung(self, attribute: list[str]) -> None:
        """Where a relationship points, resolved as openpyxl does it ('get_dependents')."""
        ziel = _attribut(attribute, "Target") or ""
        if _attribut(attribute, "TargetMode") == "External":
            return
        if ziel.startswith("/"):
            ziel = ziel[1:]
        else:
            ordner = posixpath.split(posixpath.dirname(self.name))[0]
            ziel = posixpath.normpath(posixpath.join(ordner, ziel))
        self.zaehlung.beziehungen.append((_attribut(attribute, "Type") or "", ziel))


def _verzeichnis(name: str) -> bool:
    """The content types and the relationships: they say what the other parts are."""
    return name == _INHALTSTYPEN or name.endswith(".rels")


def _alte_arbeitsmappe_pruefen(datei: IO[bytes]) -> None:
    """An old xls, checked sheet by sheet before pandas lets xlrd read all of it at once.

    pandas opens an xls with xlrd in one go. xlrd then builds every sheet
    and pads each to its rows times its widest row, up to 65,536 by 256
    cells per sheet: three sheets of 50,000 rows and 100 columns took
    285 MB from a file of 6 KB (measured 2026-10-20). Here xlrd reads one
    sheet at a time, each row only as wide as it is, and lets go of it
    before the next. Counted are the rows and columns pandas would get, per
    sheet and over all sheets, because xlrd holds them all together. At
    worst the check itself holds one sheet whose 65,536 rows each reach
    column 256: 164 MB for a file of 2 MB, before it refuses it. Nothing is
    packed in an xls, so its size is what xlrd holds.

    The number of sheets is bounded first. xlrd builds a sheet object for
    every entry of the workbook, about 3 KB each, and so does pandas after
    this check: 200,000 sheets that all point at one empty sheet passed this
    check (it loads and unloads each sheet, 0 cells) and grew a worker by
    600 MB from a file of 3.6 MB (until 10/2026, measured 2026-10-21). The
    bound stops the check and pandas alike, because both read the same file.
    """
    datei.seek(0, os.SEEK_END)
    if (groesse := datei.tell()) > MAX_ARBEITSMAPPE_MB * 1024 * 1024:
        raise _zu_gross(groesse)
    datei.seek(0)
    # xlrd writes its warnings there, not into the log.
    mappe = xlrd.open_workbook(
        file_contents=datei.read(), on_demand=True, ragged_rows=True, logfile=io.StringIO()
    )
    try:
        if mappe.nsheets > MAX_BLAETTER:
            raise _zu_viele_blaetter()
        # Its shared strings xlrd keeps in '_sharedstrings', and every cell
        # showing one holds that very string. There is no other way to them,
        # and xlrd is no longer developed. The bare streams before Excel 97
        # have none, there it is None.
        geteilt = getattr(mappe, "_sharedstrings", None) or ()
        if max(map(len, geteilt), default=0) > MAX_ZEICHEN:
            raise _zu_lange_zelle()
        zellen = 0
        for stelle in range(mappe.nsheets):
            blatt = mappe.sheet_by_index(stelle)
            try:
                if blatt.nrows > MAX_ZEILEN:
                    raise _zu_viele_zeilen()
                if blatt.ncols > MAX_SPALTEN:
                    raise _zu_viele_spalten()
                zellen += blatt.nrows * blatt.ncols
                if zellen > MAX_ZEILEN * MAX_SPALTEN:
                    raise _zu_umfangreich()
            finally:
                mappe.unload_sheet(stelle)
    finally:
        mappe.release_resources()


def arbeitsmappe_pruefen(datei: IO[bytes]) -> None:
    """Refuses a workbook that would exhaust the memory, before anything reads it.

    The upload limit measures the compressed file. The sizes of the parts
    stand in the zip's directory and cost nothing to read, so they come
    first. Then every part is counted ('_Zaehlung'), the content types and
    the relationships first: they say which parts openpyxl reads piece by
    piece. An old xls goes to xlrd the way pandas will read it
    ('_alte_arbeitsmappe_pruefen'). Until 10/2026 it passed unchecked. What
    is neither (a CSV) passes: the reader says what it is. A file that fails
    while it is counted (a damaged entry, an unknown compression, a name
    that is no UTF-8, a broken xls) is refused with the sentence of the
    reader. Until 10/2026 the check let a zip like that pass and the rows it
    had counted so far were forgotten, and some of these errors ended on
    the error page.
    """
    datei.seek(0)
    try:
        if datei.read(8).startswith(_XLS_ANFAENGE):
            _alte_arbeitsmappe_pruefen(datei)
            return
        datei.seek(0)
        if not zipfile.is_zipfile(datei):
            return
        datei.seek(0)
        with zipfile.ZipFile(datei) as archiv:
            eintraege = archiv.infolist()
            if len(eintraege) > MAX_TEILE:
                raise _zu_umfangreich()
            entpackt = sum(eintrag.file_size for eintrag in eintraege)
            if entpackt > MAX_ARBEITSMAPPE_MB * 1024 * 1024:
                raise _zu_gross(entpackt)
            zaehlung = _Zaehlung()
            # What the package says about its parts comes first.
            for eintrag in eintraege:
                if _verzeichnis(eintrag.filename):
                    zaehlung.zaehlen(archiv, eintrag, None)
            rollen = zaehlung.rollen([eintrag.filename for eintrag in eintraege])
            for eintrag in eintraege:
                if not _verzeichnis(eintrag.filename):
                    zaehlung.zaehlen(archiv, eintrag, rollen[eintrag.filename])
    except Lesefehler:
        raise
    except Exception as fehler:
        # Whatever the zip throws: besides 'BadZipFile' and 'zlib.error' a
        # broken LZMA or bzip2 stream, a name that is no UTF-8, an encrypted
        # entry. Of an xls whatever xlrd throws. The reason is technical. It
        # goes to the log, the person gets the reader's sentence.
        logger.warning("Workbook refused while checking: %r", fehler)
        raise _beschaedigt() from fehler
    finally:
        datei.seek(0)
