"""The file formats of the inventory management systems this application can read.

On import the person chooses which system the file comes from. The
register here says which ones exist. Today it is one. A second is a module
next to it and a line in 'FORMATE', nothing else (see 'base.Format').
"""

from django_project.labels import Beschriftung

from .base import (
    HOECHSTMENGE,
    MAX_MELDUNGEN,
    NAME_LAENGE,
    NUMMER_LAENGE,
    Format,
    Lesefehler,
    Sollbestandsdatei,
    Sollzeile,
    Zeilenmeldungen,
    menge_lesen,
    zitat,
)
from .winticket import WinTicket

#: All formats, in the order of the selection.
FORMATE: dict[str, Format] = {format.kennung: format for format in (WinTicket(),)}

#: Preselected as long as nothing else is known.
STANDARD = WinTicket.kennung


def format_holen(kennung: str) -> Format:
    """The format for the id. An unknown one is a programming error, not a user error.

    The forms let only known ids through.
    """
    return FORMATE[kennung]


def formatwahl() -> list[tuple[str, Beschriftung]]:
    """The choices for a 'ChoiceField'."""
    return [(kennung, format.name) for kennung, format in FORMATE.items()]


def hinweise(datei: str) -> str:
    """The help on the file field: per format one sentence what the file is there.

    'datei' is 'artikelstamm' or 'sollbestand'.
    """
    return " ".join(
        f"{format.name}: {getattr(format, f'{datei}_hinweis')}" for format in FORMATE.values()
    )


__all__ = [
    "FORMATE",
    "HOECHSTMENGE",
    "MAX_MELDUNGEN",
    "NAME_LAENGE",
    "NUMMER_LAENGE",
    "STANDARD",
    "Format",
    "Lesefehler",
    "Sollbestandsdatei",
    "Sollzeile",
    "Zeilenmeldungen",
    "format_holen",
    "formatwahl",
    "hinweise",
    "menge_lesen",
    "zitat",
]
