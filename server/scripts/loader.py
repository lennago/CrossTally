import logging
import os
from types import EllipsisType
from typing import IO

import pandas as pd

from .transform import (
    COLUMNS,
    KERNSPALTEN,
    normalisieren,
    transform_artikelbestandsliste,
)

# A path or an opened file, because uploaded files are 'IO[bytes]'.
type SourceType = str | os.PathLike[str] | IO[bytes]
type ColnamesArgType = list[str] | EllipsisType
type PathArgType = str | EllipsisType

ROHSPALTEN = ["artikelnummer", "name"]

MIN_ROHSPALTEN = len(ROHSPALTEN)

SUPPORTED = (".xlsx", ".xls", ".csv")
ENCODINGS = ("utf-8", "cp1252")

#: The rows a CSV may have, the header counted, as many as a workbook may
#: ('katalog.formats.workbook.MAX_ZEILEN', a test holds the two together).
#: The loader runs without Django too, so the number stands here once more.
#: Until 10/2026 a CSV had no limit: one of the upload limit carries a
#: million short rows, and pandas and the transformation took them all.
MAX_ZEILEN = 50_000


def _(text: str) -> str:
    """Only a marker for 'makemessages'. Translation happens in the web interface.

    The scripts also run without Django, from the command line. There the
    message stays German. The import on the website passes 'vorlage' and
    'werte' through gettext ('katalog/views/imports.py').
    """
    return text


class LoaderError(Exception):
    """A source could not be turned into a dataframe.

    'vorlage' is the German sentence with placeholders, 'werte' fills them.
    Separate so the web interface can translate the sentence before filling it.
    """

    def __init__(self, vorlage: str, **werte: object):
        self.vorlage = vorlage
        self.werte = werte
        super().__init__(vorlage % werte if werte else vorlage)


def source_name(source: SourceType) -> str:
    """File name of a source, whether path or uploaded file object."""
    if isinstance(source, (str, os.PathLike)):
        return os.path.basename(os.fspath(source))
    return getattr(source, "name", "") or "unbenannt"


def read_dataframe(source: SourceType) -> pd.DataFrame:
    """Reads a source as a dataframe. All columns stay text.

    Text is on purpose: the article number has leading zeros that have to be
    preserved when reading back into WinTICKET.
    """
    name = source_name(source)
    ext = os.path.splitext(name.lower())[1]
    if ext not in SUPPORTED:
        raise LoaderError(
            _("%(name)s: nicht unterstütztes Format. Erlaubt: %(erlaubt)s"),
            name=name,
            erlaubt=", ".join(SUPPORTED),
        )

    if ext in (".xlsx", ".xls"):
        try:
            return pd.read_excel(source, dtype=str)
        except Exception as fehler:
            # Whatever the library throws, see 'formats.winticket': a truncated
            # copy, a zip without a workbook or a damaged xls raise classes of
            # their own. The reason is English and technical: to the log, not
            # to the person.
            logging.getLogger(__name__).warning("Workbook %s not readable: %s", name, fehler)
            raise LoaderError(
                _("%(name)s: keine Excel-Arbeitsmappe oder beschädigt."), name=name
            ) from fehler

    for encoding in ENCODINGS:
        _rewind(source)
        try:
            # At most one row more than allowed is read, enough to tell.
            tabelle = pd.read_csv(source, sep=";", dtype=str, encoding=encoding, nrows=MAX_ZEILEN)
        except UnicodeDecodeError:
            continue
        except (pd.errors.ParserError, pd.errors.EmptyDataError) as fehler:
            raise LoaderError(
                _("%(name)s: CSV-Datei nicht lesbar (%(grund)s)."), name=name, grund=fehler
            ) from fehler
        # The header is a row too, as in a workbook.
        if len(tabelle) >= MAX_ZEILEN:
            raise LoaderError(_("Die Datei hat mehr als %(grenze)s Zeilen."), grenze=MAX_ZEILEN)
        return tabelle
    raise LoaderError(
        _("%(name)s: weder %(kodierungen)s kodiert."),
        name=name,
        kodierungen=" noch ".join(ENCODINGS),
    )


def _rewind(source: SourceType) -> None:
    if not isinstance(source, (str, os.PathLike)):
        source.seek(0)


def is_transformed(df: pd.DataFrame) -> bool:
    """Whether the dataframe already brings the target columns.

    Checked against 'KERNSPALTEN', not against 'COLUMNS': a file written before
    the normalisation does not know 'einheit_roh' yet and still should not go
    through the transformation. There it lacks the 'gekauft in' rows, and four
    columns would be NaN.
    """
    return set(KERNSPALTEN).issubset(df.columns)


def rename_rohspalten(df: pd.DataFrame, name: str = "") -> pd.DataFrame:
    """Renames the first two columns to 'artikelnummer' and 'name'.

    The Compeso export brings its own headings ('Artikel', 'Lieferant', ...),
    but 'transform_artikelbestandsliste' works with the target names.
    """
    if set(ROHSPALTEN).issubset(df.columns):
        return df[ROHSPALTEN]
    if df.shape[1] < MIN_ROHSPALTEN:
        raise LoaderError(
            _("%(name)s: erwartet werden mindestens zwei Spalten (Artikelnummer und Bezeichnung)."),
            name=name or "Datei",
        )
    roh = df.iloc[:, :2].copy()
    roh.columns = ROHSPALTEN
    return roh


def read_artikelbestandsliste(source: SourceType, haus: str) -> pd.DataFrame:
    """Turn a source into the six-column article table.

    Sources that already bring the target columns bypass the transformation:
    their rows are numeric throughout and the 'gekauft in' rows are missing,
    'transform_artikelbestandsliste' would set four columns to NaN.
    """
    name = source_name(source)
    df = read_dataframe(source)
    if df.empty:
        raise LoaderError(_("%(name)s: enthält keine Zeilen."), name=name)
    if is_transformed(df):
        # A finished file goes through the mapping too. It is repeatable, and a
        # state from before the change still carries "Stück(e)" instead of STUECK.
        return normalisieren(df.reindex(columns=COLUMNS))
    try:
        return transform_artikelbestandsliste(rename_rohspalten(df, name), haus)
    except LoaderError:
        raise
    except (KeyError, ValueError, AttributeError, IndexError) as fehler:
        # The reason is English and technical: to the log, as for the
        # workbooks. Until 10/2026 it stood in the German sentence.
        logging.getLogger(__name__).warning("Article master %s not convertible: %s", name, fehler)
        raise LoaderError(
            _("%(name)s: Die Umwandlung ist gescheitert. Der Grund steht im Protokoll."), name=name
        ) from fehler


def get_artikel_und_warengruppen(
    sources: dict[str, SourceType],
    dest: PathArgType = ...,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reads all sources together into an article table and its product groups.

    An article then stands several times in the table if several houses carry
    it with different packs. Identical rows drop away.
    """
    artikel, warengruppen = get_artikel_und_spalten(sources, ["warengruppe"], dest)
    return artikel, warengruppen


def _get_artikel(
    sources: dict[str, SourceType],
) -> pd.DataFrame:
    """Reads all sources together into an article table.

    An article then stands several times in the table if several houses carry
    it with different packs. Identical rows drop away.
    """
    if not sources:
        raise LoaderError(_("Keine Datei übergeben."))

    tabellen = [read_artikelbestandsliste(source, haus) for haus, source in sources.items()]
    artikel = pd.concat(tabellen, ignore_index=True).reindex(columns=COLUMNS)
    artikel = artikel.drop_duplicates().dropna(subset=ROHSPALTEN)
    return drop_leere_gebinde(artikel).reset_index(drop=True)


def drop_leere_gebinde(artikel: pd.DataFrame) -> pd.DataFrame:
    """Throws away 'anzahl = 0' as soon as the same article has a real count elsewhere."""
    hat_wert = pd.to_numeric(artikel["anzahl"], errors="coerce").fillna(0) > 0
    gruppe_hat_wert = hat_wert.groupby(
        [artikel[spalte] for spalte in ROHSPALTEN], dropna=False
    ).transform("max")
    return artikel[hat_wert | ~gruppe_hat_wert]


def get_artikel_und_spalten(
    sources: dict[str, SourceType],
    additional_cols: ColnamesArgType,
    dest: PathArgType = ...,
) -> list[pd.DataFrame]:
    artikel = _get_artikel(sources)
    ret = [artikel]
    if additional_cols is not ...:
        for col in additional_cols:
            if col not in COLUMNS:
                raise ValueError(f"invalid columnname: {col}")
            ret.append(artikel[[col]].dropna().drop_duplicates().reset_index(drop=True))
        if dest is not ...:
            save(artikel, ret[1:], dest)
    elif dest is not ...:
        save(artikel, ..., dest)
    return ret


def save(artikel: pd.DataFrame, cols: list[pd.DataFrame] | EllipsisType, dest: str) -> list[str]:
    """Stores the results as CSV."""
    os.makedirs(dest, exist_ok=True)
    pfade = []
    df_names = [(artikel, "artikel_combined.csv")]
    if cols is not ...:
        for col in cols:
            df_names.append((col, f"{col.columns[0]}.csv"))
    for df, dateiname in df_names:
        pfad = os.path.join(dest, dateiname)
        df.to_csv(pfad, sep=";", index=False, header=True)
        pfade.append(pfad)
    return pfade
