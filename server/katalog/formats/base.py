"""The contract every file format of an inventory management system fulfils.

Two files come in from outside: the article master of a house and the
target stock at the cut-off date. What they look like only the system they
come from knows. Everything behind it (the import plan, the matching of
the target, the questions to the person) works with what comes out here
and knows no format.

A new system therefore means: a module next to 'winticket.py', a class, an
entry in the register. The article master comes out as the table with the
columns from 'scripts.transform.COLUMNS', the target stock as rows of
number, quantity and designation. What a format cannot deliver, say the
count per pack, stays empty and is decided in the plan.
"""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import IO, ClassVar

import pandas as pd
from django.utils.translation import gettext as _

from django_project.decimals import dezimal_normalisieren
from django_project.labels import Beschriftung

#: What the target's column holds: twelve digits, three of them after the
#: point. PostgreSQL rejects more, and SQLite would accept it and fail when reading.
HOECHSTMENGE = Decimal("1e9")

#: What the columns of an article number and a designation hold, on the
#: house article as on the target row of the inventory. PostgreSQL rejects
#: more ("value too long"), SQLite stores it.
NUMMER_LAENGE = 20
NAME_LAENGE = 200

#: How much of a cell a message quotes: enough to find the row again.
ZITAT_LAENGE = 60
#: How many refused rows of a file are reported one by one. The rest are
#: counted ('Zeilenmeldungen'). The page shows the first ten anyway.
MAX_MELDUNGEN = 100


class Lesefehler(Exception):
    """The file could not be read. The message is already translated.

    It lands on the form's field 'datei', hence a sentence for the person and
    no traceback.
    """


@dataclass(frozen=True)
class Sollzeile:
    """A readable row of the target stock file, not yet matched to an article."""

    nummer: str
    menge: Decimal
    name: str
    #: The row number in the file, for messages.
    stelle: int


@dataclass
class Sollbestandsdatei:
    """What a format reads out of the target stock file."""

    zeilen: list[Sollzeile] = field(default_factory=list)
    #: Rows that were not readable, as sentences for the person. Past
    #: 'MAX_MELDUNGEN' rows a count stands in front instead ('Zeilenmeldungen').
    fehler: list[str] = field(default_factory=list)


def zitat(wert: str) -> str:
    """A cell for a message, cut after 'ZITAT_LAENGE' characters.

    Until 10/2026 every message about a refused row carried the whole cell.
    A workbook of a few kilobytes, whose shared string of a megabyte stood in
    five hundred rows, then held a gigabyte of messages.
    """
    if len(wert) <= ZITAT_LAENGE:
        return wert
    return wert[:ZITAT_LAENGE].rstrip() + "…"


class Zeilenmeldungen:
    """The messages about the refused rows of a file: the first in words, the rest as a count.

    Without a limit the list grew with every row, and a file of a few
    megabytes made millions of messages. The count leads the list, because
    the page shows only its first ten messages and counts the rest itself
    ('meldungen_gekuerzt'). A sentence at the end would be lost there and
    counted as one row.
    """

    def __init__(self) -> None:
        self.texte: list[str] = []
        #: Refused rows beyond 'MAX_MELDUNGEN', counted only.
        self.weitere = 0

    def melden(self, text: str) -> None:
        if len(self.texte) < MAX_MELDUNGEN:
            self.texte.append(text)
        else:
            self.weitere += 1

    def liste(self) -> list[str]:
        if not self.weitere:
            return list(self.texte)
        zahl = _(
            "%(anzahl)s Zeilen der Datei wurden übergangen. Einzeln genannt werden nur die "
            "ersten %(genannt)s."
        ) % {"anzahl": len(self.texte) + self.weitere, "genannt": len(self.texte)}
        return [zahl, *self.texte]


def zu_lang(stelle: int, nummer: str, name: str) -> str | None:
    """Why a row of the target file does not fit its columns, or None if it does.

    Until 10/2026 a number of 25 characters reached the database and ended
    under PostgreSQL on the error page, the whole file with it. Now the row
    stays out with a sentence, and the rest of the file is read.
    """
    if len(nummer) > NUMMER_LAENGE:
        return _(
            "Zeile %(zeile)s: Die Artikelnummer „%(nummer)s“ ist länger als %(laenge)s Zeichen."
        ) % {"zeile": stelle, "nummer": zitat(nummer), "laenge": NUMMER_LAENGE}
    if len(name) > NAME_LAENGE:
        return _("Zeile %(zeile)s: Die Bezeichnung ist länger als %(laenge)s Zeichen.") % {
            "zeile": stelle,
            "laenge": NAME_LAENGE,
        }
    return None


def menge_lesen(rohwert: str) -> Decimal | None:
    """A number from a file, written in German or English.

    Which separator is what is decided by 'dezimal_normalisieren': the last
    one separates the decimal places, "1.234,5" like "1,234.5".
    """
    text = dezimal_normalisieren(rohwert)
    if not text:
        return None
    try:
        wert = Decimal(text)
    except InvalidOperation:
        return None
    # 'NaN' and 'Infinity' are valid numbers for Decimal, not for a stock.
    # And what the column does not hold is a typo, not a stock.
    if not wert.is_finite() or abs(wert) >= HOECHSTMENGE:
        return None
    # Checked once more after rounding: 999999999.9995 becomes 1000000000.000,
    # and until 10/2026 that went on to the database.
    gerundet = wert.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return gerundet if abs(gerundet) < HOECHSTMENGE else None


class Format:
    """A system whose files this application can read.

    The class attributes describe the format for the forms, the two methods
    read. Both may raise 'Lesefehler' if the whole file is unusable.
    Individual unreadable rows of the target are no reason to abort, they are
    in 'Sollbestandsdatei.fehler'.
    """

    #: The key in the register and in the form. Stays stable, because it is
    #: stored.
    kennung: ClassVar[str]
    #: What the system is called in the selection.
    name: ClassVar[Beschriftung]
    #: Extensions the article master may have, lower case and without a dot.
    artikelstamm_endungen: ClassVar[tuple[str, ...]]
    #: Which file of the system the article master is, for the help on the field.
    artikelstamm_hinweis: ClassVar[Beschriftung]
    sollbestand_endungen: ClassVar[tuple[str, ...]]
    sollbestand_hinweis: ClassVar[Beschriftung]
    #: The message when a number does not fit the system's scheme.
    artikelnummer_hinweis: ClassVar[Beschriftung]

    def artikelstamm_lesen(self, quelle: IO[bytes] | str, haus: str) -> pd.DataFrame:
        """The file as a table with the columns from 'scripts.transform.COLUMNS'.

        'haus' is the name of the house and stands in every row, because the plan
        attributes the rows to this house.
        """
        raise NotImplementedError

    def sollbestand_lesen(self, datei: IO[bytes]) -> Sollbestandsdatei:
        """The file as rows of number, quantity and designation.

        The quantity is in the unit in which the house carries the article. The
        number is normalised, as it stands on the house article.
        """
        raise NotImplementedError

    def artikelnummer(self, rohwert: str) -> str | None:
        """The number as it stands on the house article, or None if it is none.

        A system with a fixed scheme checks it here. WinTICKET has none: the
        number stays as the file writes it. One longer than its column
        ('NUMMER_LAENGE') comes back unexamined, and the caller refuses it as
        too long. Looking at every character of such a cell cost seconds.
        """
        raise NotImplementedError

    def __str__(self) -> str:
        return str(self.name)
