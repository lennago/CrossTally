"""The files that belong to the demo, and the way to them.

They lie in the repo so article import and target stock upload can be
played through without anyone having to build a file first. They are
invented exports in the format of the inventory management system.
"""

from pathlib import Path

ORDNER = Path(__file__).resolve().parent / "exports"

#: The source language. Its files lie directly in 'exports/', every further
#: one gets a subfolder.
GRUNDSPRACHE = "de"

#: File name and what it is for. The description is also in DEMO.md.
BESCHREIBUNGEN: dict[str, str] = {
    "artikel_kino_west.csv": "Artikelstamm für das leere Haus, der erste Import",
    "artikel_kino_ost.csv": "Zweiter Import für ein gepflegtes Haus, mit Änderungen",
    "sollbestand_kino_nord.csv": "Sollbestand zur laufenden Inventur in Kino Nord",
    "sollbestand_kino_ost.csv": "Sollbestand zur laufenden Inventur in Kino Ost",
    "sollbestand_kino_west.csv": "Sollbestand für die erste Inventur in Kino West",
    "sollbestand_kino_west.xlsx": "Derselbe Sollbestand als Zählblatt, wie WinTICKET es ausgibt",
}

#: The same files in English. Translated are the designations, not the
#: skeleton: "gekauft in … zu …" is the format of the inventory management
#: system, and that is a German program.
BESCHREIBUNGEN_EN: dict[str, str] = {
    "artikel_kino_west.csv": "Article master for the empty site, the first import",
    "artikel_kino_ost.csv": "Second import for a maintained site, with changes",
    "sollbestand_kino_nord.csv": "Target stock for the running inventory at Cinema North",
    "sollbestand_kino_ost.csv": "Target stock for the running inventory at Cinema East",
    "sollbestand_kino_west.csv": "Target stock for the first inventory at Cinema West",
    "sollbestand_kino_west.xlsx": "The same target stock as the count sheet WinTICKET produces",
}


def beschreibungen(sprache: str = GRUNDSPRACHE) -> dict[str, str]:
    """The files of the stock, in the language it was built in."""
    return dict(BESCHREIBUNGEN_EN if sprache == "en" else BESCHREIBUNGEN)


def ordner(sprache: str = GRUNDSPRACHE) -> Path:
    return ORDNER if sprache == GRUNDSPRACHE else ORDNER / sprache


def datei(name: str, sprache: str = GRUNDSPRACHE) -> Path:
    pfad = ordner(sprache) / name
    if not pfad.is_file():
        raise FileNotFoundError(f"Die Demo-Datei „{name}“ fehlt unter {pfad.parent}.")
    return pfad
