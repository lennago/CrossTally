"""The count sheet from WinTICKET, built from a target file of the demo.

In operation the target stock comes as "Inventurzählliste mit Sollbestand
nach Lager und Artikelnummer", saved as Excel without formatting. So that
this path can be played through too, the same lies next to the target file
of Kino West as a count sheet: a header row per page, a storage row, after
the actual stock the empty columns for entering. It is built from the CSV so
the two do not drift apart. A test in 'demo/tests.py' pins that down.

    uv run python -m demo.count_sheet
"""

import csv
from decimal import Decimal
from pathlib import Path

import openpyxl

from .files import GRUNDSPRACHE, datei, ordner

#: Which target file gets its count sheet.
ZAEHLBLAETTER = {"sollbestand_kino_west.csv": "sollbestand_kino_west.xlsx"}
SPRACHEN = (GRUNDSPRACHE, "en")

#: The header row as WinTICKET repeats it per page. "Artikel" spans number
#: and designation, the actual stock is therefore the second entry.
KOPF = ("Artikel", "Istbestand lt. Rechner", "Zählbestand", "Differenz", None)
LAGER = ("Lager Nummer 1 Hauptlager", None, None, None, None)
#: The columns for entering are dashes in the export.
LEER = "_____________"
JE_SEITE = 14
BLATTNAME = "Tabelle S. 1"


def wie_gedruckt(menge: str) -> str:
    """The quantity as WinTICKET writes it: thousands point, comma, two decimal places.

    More digits stay where the CSV has them. Otherwise the third digit of a
    weighed product would be gone, and the two files would no longer say the same.
    """
    wert = Decimal(menge.replace(",", "."))
    # The exponent of a finite number is always an integer.
    exponent = wert.as_tuple().exponent
    stellen = max(2, -exponent) if isinstance(exponent, int) else 2
    return f"{wert:,.{stellen}f}".replace(",", "\u0000").replace(".", ",").replace("\u0000", ".")


def zaehlblatt(zeilen: list[tuple[str, str, str]]) -> list[list[str | None]]:
    """The rows of the sheet to rows of number, quantity and designation."""
    blatt: list[list[str | None]] = [list(KOPF), list(LAGER)]
    for nummer, (artikel, menge, name) in enumerate(zeilen):
        if nummer and nummer % JE_SEITE == 0:
            blatt.append(list(KOPF))
        blatt.append([artikel, name, wie_gedruckt(menge), LEER, LEER])
    return blatt


def zeilen_lesen(csv_pfad: Path) -> list[tuple[str, str, str]]:
    with csv_pfad.open(encoding="utf-8", newline="") as quelle:
        leser = csv.reader(quelle, delimiter=";")
        next(leser)
        return [(nummer, menge, name) for nummer, menge, name in leser]


def schreiben(csv_pfad: Path, xlsx_pfad: Path) -> None:
    mappe = openpyxl.Workbook()
    blatt = mappe.active
    assert blatt is not None
    blatt.title = BLATTNAME
    for zeile in zaehlblatt(zeilen_lesen(csv_pfad)):
        blatt.append(zeile)
    mappe.save(xlsx_pfad)


def alle_schreiben() -> None:
    for sprache in SPRACHEN:
        for csv_name, xlsx_name in ZAEHLBLAETTER.items():
            schreiben(datei(csv_name, sprache), ordner(sprache) / xlsx_name)


if __name__ == "__main__":
    alle_schreiben()
