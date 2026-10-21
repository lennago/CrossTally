"""WinTICKET by Compeso: the inventory management system of the cinemas.

The article master is the article stock list per house, as xlsx or csv.
What is in it and how it becomes the table is known by 'scripts.transform'.
It also runs without Django from the command line, which is why it lives
there and not here.

The target stock comes in two ways. The convenient one is the **count
sheet** from WinTICKET ("Inventurzählliste mit Sollbestand nach Lager und
Artikelnummer", as Excel without formatting): article number, designation
and "Istbestand lt. Rechner" in the first three columns, behind them the
empty columns for entering. Header rows repeat per page, storage rows sit
in between. The older way is a CSV with semicolons: article number,
quantity, and optionally the designation. In which order quantity and
designation stand there Compeso does not say. The number is the quantity,
the other column the designation.
"""

import csv
import io
import logging
from collections.abc import Iterator
from decimal import Decimal
from typing import IO

import pandas as pd
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy, ngettext

from django_project.validation import ganzzahl
from scripts.loader import LoaderError, get_artikel_und_warengruppen

from .base import (
    NUMMER_LAENGE,
    Format,
    Lesefehler,
    Sollbestandsdatei,
    Sollzeile,
    Zeilenmeldungen,
    menge_lesen,
    zitat,
    zu_lang,
)
from .workbook import MAX_ZEILEN, arbeitsmappe_pruefen

#: Compeso writes semicolons. The encoding changes between exports.
TRENNER = ";"
KODIERUNGEN = ("utf-8-sig", "cp1252")
#: Article number and quantity. A third column may carry the designation.
MINDESTSPALTEN = 2
#: The count sheet comes as a table. Everything else is read as CSV.
TABELLENENDUNGEN = ("xlsx", "xls")
#: In the count sheet: article number, designation, actual stock. The rest is for entering.
ZAEHLBLATTSPALTEN = [0, 1, 2]

logger = logging.getLogger(__name__)


class WinTicket(Format):
    kennung = "winticket"
    name = gettext_lazy("WinTICKET (Compeso)")
    artikelstamm_endungen = ("xlsx", "xls", "csv")
    artikelstamm_hinweis = gettext_lazy("die Artikelbestandsliste des Hauses (xlsx, xls oder csv)")
    sollbestand_endungen = ("xlsx", "xls", "csv", "txt")
    sollbestand_hinweis = gettext_lazy(
        "die Inventurzählliste mit Sollbestand als Excel-Datei, oder eine CSV mit "
        "„Artikelnummer;Menge“. Eine Kopfzeile darf davorstehen."
    )
    artikelnummer_hinweis = gettext_lazy(
        "Eine Artikelnummer aus WinTICKET beginnt mit einer Ziffer und hat keine Leerzeichen."
    )

    def artikelstamm_lesen(self, quelle: IO[bytes] | str, haus: str) -> pd.DataFrame:
        # The loader expects {house name: source} and writes the key as house
        # column into the table. It also runs without Django, which is why its
        # message is only translated here.
        if not isinstance(quelle, str):
            arbeitsmappe_pruefen(quelle)
        try:
            tabelle, _gruppen = get_artikel_und_warengruppen({haus: quelle})
        except LoaderError as fehler:
            raise Lesefehler(_(fehler.vorlage) % fehler.werte) from fehler
        # A file the command line already converted keeps the house it was
        # written for, and 'artikel_combined.csv' carries several. Until
        # 10/2026 the plan took them all as articles of this house, and rows
        # of the same number from two houses merged into one.
        haeuser = sorted({wert.strip() for wert in tabelle["haus"].dropna().astype(str)} - {""})
        if len(haeuser) > 1:
            raise Lesefehler(
                _(
                    "Die Datei führt Artikel mehrerer Häuser (%(haeuser)s). Bitte je Haus eine "
                    "eigene Datei hochladen."
                )
                % {"haeuser": ", ".join(zitat(haus) for haus in haeuser)}
            )
        tabelle["haus"] = haus
        return tabelle

    def artikelnummer(self, rohwert: str) -> str | None:
        # As the file writes it. Until 09/2026 "exactly six digits, leading zeros
        # added" applied. The count sheet then showed "10137" and "101014-1":
        # WinTICKET has no scheme. What remains is what distinguishes a sum row
        # ("Summe: …") from a number.
        nummer = rohwert.strip()
        # The first character one of the ten digits: "²" is a digit to 'isdigit' too.
        if not nummer or ganzzahl(nummer[0]) is None:
            return None
        # Longer than its column, the number is too long whatever else is wrong
        # with it, and the caller says so. Until 10/2026 every character was
        # looked at first: 500 rows of a number of a megabyte took 12 seconds.
        if len(nummer) > NUMMER_LAENGE:
            return nummer
        if any(zeichen.isspace() for zeichen in nummer):
            return None
        return nummer

    def sollbestand_lesen(self, datei: IO[bytes]) -> Sollbestandsdatei:
        if _ist_tabelle(datei):
            return self._zaehlblatt_lesen(datei)
        ergebnis = Sollbestandsdatei()
        meldungen = Zeilenmeldungen()
        for stelle, spalten in _csv_zeilen(_entziffern(datei)):
            if not any(feld.strip() for feld in spalten):
                continue
            if len(spalten) < MINDESTSPALTEN:
                meldungen.melden(
                    _("Zeile %(zeile)s: erwartet wird „Artikelnummer%(trenner)sMenge“.")
                    % {"zeile": stelle, "trenner": TRENNER}
                )
                continue
            nummer = self.artikelnummer(spalten[0])
            menge, name = _menge_und_name(spalten)
            if nummer is None or menge is None:
                # The first row may be a header row. Nobody likes to report that
                # as an error.
                if stelle > 1:
                    meldungen.melden(
                        _("Zeile %(zeile)s: „%(wert)s“ ist keine Menge.")
                        % {"zeile": stelle, "wert": zitat(TRENNER.join(spalten[:3]))}
                    )
                continue
            if (zu_lange := zu_lang(stelle, nummer, name)) is not None:
                meldungen.melden(zu_lange)
                continue
            ergebnis.zeilen.append(Sollzeile(nummer, menge, name, stelle))
        ergebnis.fehler = meldungen.liste()
        return ergebnis

    def _zaehlblatt_lesen(self, datei: IO[bytes]) -> Sollbestandsdatei:
        """The count sheet: only rows whose third column is a number.

        Header rows and storage rows have none there, they drop out without a
        message. That is how the sheet is printed, that is no error. A number
        next to a number that is none, by contrast, is reported. The first sheet
        of the workbook is read.
        """
        arbeitsmappe_pruefen(datei)
        try:
            # All columns, counted here: with 'usecols' a sheet of two columns
            # failed in pandas and read as a damaged file (until 10/2026).
            tabelle = pd.read_excel(datei, header=None, dtype=str)
        except Exception as fehler:
            # Whatever the library throws: besides 'ValueError' and 'OSError' a
            # truncated copy raises 'BadZipFile', a zip without a workbook an
            # 'OptionError', a damaged xls a 'CompDocError', and the list grows
            # with every version (until 10/2026 those three were server errors).
            # The reason is English and technical. It goes to the log, the
            # person gets a sentence.
            logger.warning("Count sheet not readable: %s", fehler)
            raise Lesefehler(
                _("Die Datei ist keine Excel-Arbeitsmappe oder beschädigt.")
            ) from fehler
        if tabelle.shape[1] < len(ZAEHLBLATTSPALTEN):
            raise Lesefehler(
                _(
                    "Das Zählblatt braucht drei Spalten: Artikelnummer, Bezeichnung und Bestand. "
                    "Diese Mappe hat %(anzahl)s. Mit zwei Spalten geht die Datei als CSV "
                    "(„Artikelnummer;Menge“)."
                )
                % {"anzahl": tabelle.shape[1]}
            )
        tabelle = tabelle.iloc[:, ZAEHLBLATTSPALTEN]
        ergebnis = Sollbestandsdatei()
        meldungen = Zeilenmeldungen()
        ohne_menge = 0
        for stelle, zeile in enumerate(tabelle.itertuples(index=False), start=1):
            nummer_roh, name_roh, menge_roh = (_zelle(wert) for wert in zeile)
            menge = menge_lesen(menge_roh)
            nummer = self.artikelnummer(nummer_roh)
            if menge is None:
                # A number without a readable quantity: a formula without a
                # stored value, a date in the column. Header and storage rows
                # have no number and stay silent, these are counted and said.
                if nummer is not None and stelle > 1:
                    ohne_menge += 1
                continue
            if nummer is None:
                meldungen.melden(
                    _("Zeile %(zeile)s: „%(wert)s“ ist keine Artikelnummer.")
                    % {"zeile": stelle, "wert": zitat(nummer_roh)}
                )
                continue
            if (zu_lange := zu_lang(stelle, nummer, name_roh)) is not None:
                meldungen.melden(zu_lange)
                continue
            ergebnis.zeilen.append(Sollzeile(nummer, menge, name_roh, stelle))
        ergebnis.fehler = meldungen.liste()
        if ohne_menge:
            ergebnis.fehler.append(
                ngettext(
                    "%(anzahl)s Zeile mit Artikelnummer, aber ohne lesbare Menge, übergangen.",
                    "%(anzahl)s Zeilen mit Artikelnummer, aber ohne lesbare Menge, übergangen.",
                    ohne_menge,
                )
                % {"anzahl": ohne_menge}
            )
        return ergebnis


def _ist_tabelle(datei: IO[bytes]) -> bool:
    """Whether the file is an Excel workbook by its name. Without a name it is CSV."""
    name = str(getattr(datei, "name", "") or "")
    return "." in name and name.rsplit(".", 1)[-1].lower() in TABELLENENDUNGEN


def _csv_zeilen(text: str) -> Iterator[tuple[int, list[str]]]:
    """The rows of a CSV with their numbers. What the reader cannot read is a 'Lesefehler'.

    Read with 'newline=""', as the csv module asks for it. A carriage return
    alone then ends a row too, as old Excel for the Mac writes it. Until
    10/2026 such a file was one long line to the reader, and it broke off
    with an error page, as it did on a field beyond its limit of 131,072
    characters ('csv.field_size_limit'). No target stock has such a field.
    The reader's reason is English and technical: it goes to the log, the
    person gets a sentence.

    A CSV may have as many rows as a workbook ('MAX_ZEILEN'). Until 10/2026
    it had no limit, and 1 MB of one row repeated made 116,508 rows and
    39 MB, a file of the upload limit ten times that.
    """
    leser = csv.reader(io.StringIO(text, newline=""), delimiter=TRENNER)
    try:
        for stelle, spalten in enumerate(leser, start=1):
            if stelle > MAX_ZEILEN:
                raise Lesefehler(
                    _("Die Datei hat mehr als %(grenze)s Zeilen.") % {"grenze": MAX_ZEILEN}
                )
            yield stelle, spalten
    except csv.Error as fehler:
        logger.warning("Target stock CSV not readable: %s", fehler)
        raise Lesefehler(
            _("Die Datei lässt sich ab Zeile %(zeile)s nicht als CSV lesen.")
            % {"zeile": leser.line_num}
        ) from fehler


def _zelle(wert) -> str:
    """A cell as text. Empty cells come from pandas as NaN."""
    return "" if pd.isna(wert) else str(wert).strip()


def _menge_und_name(spalten: list[str]) -> tuple[Decimal | None, str]:
    """Quantity and designation from the columns behind the number, wherever they stand."""
    zweite, dritte = spalten[1], spalten[2] if len(spalten) > MINDESTSPALTEN else ""
    menge = menge_lesen(zweite)
    if menge is not None:
        return menge, dritte.strip()
    return menge_lesen(dritte), zweite.strip()


def _entziffern(datei: IO[bytes]) -> str:
    """The uploaded file as text, encoding guessed as in the article import."""
    rohdaten = datei.read()
    if isinstance(rohdaten, str):
        return rohdaten
    for kodierung in KODIERUNGEN:
        try:
            return rohdaten.decode(kodierung)
        except UnicodeDecodeError:
            continue
    return rohdaten.decode("utf-8", errors="replace")
