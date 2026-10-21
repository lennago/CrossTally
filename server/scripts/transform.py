import pandas as pd

from .normalize import einheitenkennung, gebindename, zahl_normalisieren

#: What makes up an article table. 'einheit' carries the id, no longer the
#: word from the file.
KERNSPALTEN = [
    "artikelnummer",
    "name",
    "warengruppe",
    "gebinde",
    "anzahl",
    "einheit",
    "haus",
]

#: The word as it stood in the file. Only for the hint in the import plan
#: when the unit had to be guessed.
EINHEIT_ROH = "einheit_roh"

COLUMNS = [*KERNSPALTEN, EINHEIT_ROH]


#: Stands in place of an article where a product group row ends the one above.
GRUPPENGRENZE = -1

#: What of an info row the pattern reads. A real one has some forty
#: characters, and with a pack name its column holds it stays below this.
#: Where the pattern does not fit, it costs the square of the length: 0.1 ms
#: at 200 characters, 54 ms at 4,000. Every row also took a copy of the pack
#: name it found, twenty rows of a shared text of a megabyte 21 MB (until
#: 10/2026). A pack name too long for its column still comes through within
#: this, and the plan says that it is too long.
INFO_LAENGE = 200


def transform_artikelbestandsliste(df: pd.DataFrame, haus: str) -> pd.DataFrame:
    """The export of one house as an article table, one row per article row of the file.

    Under every article row stands its info row, "gekauft in <Gebinde> zu <n>
    <Einheit>". It belongs to the article directly above, and a product group
    row in between ends that article. Until 10/2026 the i-th info row went to
    the i-th article: one missing shifted pack and unit of every article after
    it. An article without an info row now stands without a pack, and the plan
    suggests it discontinued.

    "gekauft in zu 0" names neither pack nor unit. Such an article comes
    along without a pack as well. Until 10/2026 it dropped out here, and the
    plan offered a carried one under "Missing from the file".
    """
    nummern = df["artikelnummer"]
    namen = df["name"]
    hat_nummer = nummern.str.match("^[0123456789]", na=False)
    ohne_namen = namen.isna() | (namen.astype(str).str.strip() == "")
    ohne_nummer = nummern.isna() | (nummern.astype(str).str.strip() == "")
    # A product group row ("230 VK - Eis …") carries no designation in the
    # second column, an article row always does. It cannot be pinned on the
    # number itself: WinTICKET also carries "101014-1".
    ist_warengruppe = hat_nummer & ohne_namen
    ist_artikel = hat_nummer & ~ohne_namen
    ist_info = ohne_nummer & ~ohne_namen

    # Per row the article above it, or the product group row that ended it.
    anker = (
        pd.Series(df.index, index=df.index)
        .where(ist_artikel)
        .mask(ist_warengruppe, GRUPPENGRENZE)
        .ffill()
    )
    info_je_artikel = namen[ist_info].groupby(anker[ist_info]).first()

    artikel = df[ist_artikel].copy()
    artikel["warengruppe"] = nummern.where(ist_warengruppe).ffill()[ist_artikel]
    artikel["info"] = info_je_artikel.reindex(artikel.index).str.slice(0, INFO_LAENGE)
    artikel = artikel.reset_index(drop=True)
    artikel[["gebinde", "anzahl", "einheit"]] = artikel["info"].str.extract(
        r"^.*\s(.*)\szu\s+([\d.,]+)\s*(.*)"
    )

    artikel["anzahl"] = artikel["anzahl"].map(_anzahl)
    # No pack, not "in": the pattern takes the word before "zu" for one.
    artikel.loc[artikel["info"].str.match(r"gekauft in\s+zu\s", na=False), "gebinde"] = ""
    # "gekauft in Tüte(n) zu 6 Karton": the unit names the packaging. Has to
    # run before the normalisation, while the words are still there.
    artikel.loc[artikel["einheit"] == "Karton", "gebinde"] = "Karton"
    artikel = artikel[~artikel["name"].str.contains(r"-\s*FREI\s*-", na=False, regex=True)]
    artikel["haus"] = haus

    artikel = artikel.drop(["info"], axis=1)
    return normalisieren(artikel)


def _anzahl(wert: object) -> float:
    """The count of the info row, read as every other number ('zahl_normalisieren').

    Until 10/2026 only the comma became a point: "1.000,00" broke off the
    import. A thousands point without decimal places ("1.000") stays one, as
    everywhere, because WinTICKET writes decimal places. What is no number
    raises 'ValueError', and the loader says so.
    """
    if not isinstance(wert, str):
        return float("nan")
    return float(zahl_normalisieren(wert))


def normalisieren(artikel: pd.DataFrame) -> pd.DataFrame:
    """Turns the file's words into the house's terms.

    Also runs over an already transformed file, because the mapping is
    repeatable: an id maps onto itself, and "Einzeln" stays "Einzeln".
    """
    normalisiert = artikel.copy()
    if EINHEIT_ROH not in normalisiert.columns:
        normalisiert[EINHEIT_ROH] = normalisiert["einheit"]

    kennungen = normalisiert["einheit"].map(lambda wort: einheitenkennung(wort)[0])
    normalisiert["gebinde"] = [
        gebindename(gebinde, anzahl, kennung)
        for gebinde, anzahl, kennung in zip(
            normalisiert["gebinde"], normalisiert["anzahl"], kennungen, strict=True
        )
    ]
    normalisiert["einheit"] = kennungen
    return normalisiert


def main():
    from .cli_parser import get_parser

    args = get_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
