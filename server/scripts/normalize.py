"""Turn Compeso's words into the house's terms.

Two mappings, both shown on the real exports:

**The unit.** Compeso often carries no unit at all there but a packaging:
"Becher", "Eimer", "Flasche(n)", "Tüte(n)", "Dose", "Glas". Real units are
only pieces, grams, kilograms, millilitres and litres. Everything else is
counted, i.e. runs as pieces.

**The pack.** With a count of 1 the designation says nothing: in the exports
it reads sometimes "Kiste(n)", sometimes "Karton", sometimes "Stück(e)", and
what is meant is always the same loose single unit. But that applies only to
piece goods: "gekauft in Flasche(n) zu 1 Liter" is a one-litre bottle and
very much a pack.

The module gets by **without Django**, because 'python -m scripts.transform'
runs without 'django.setup()'. That the ids here are the same as in
'katalog.models.Einheitenkennung' is pinned down by a test.
"""

import math
from decimal import Decimal

STUECK = "STUECK"
GRAMM = "GRAMM"
KILOGRAMM = "KILOGRAMM"
MILLILITER = "MILLILITER"
LITER = "LITER"

#: What is no recognisable unit is counted.
RUECKFALL = STUECK

#: Spelling (lower case) to the id. The ids themselves stand here on
#: purpose too: that way an already normalised file can be read in again
#: without anything tipping over.
EINHEITEN: dict[str, str] = {
    # Pieces, in every spelling that occurred in the files or suggests itself.
    "stück(e)": STUECK,
    "stücke": STUECK,
    "stück": STUECK,
    "stk": STUECK,
    "stk.": STUECK,
    "st": STUECK,
    "st.": STUECK,
    "pcs": STUECK,
    "pieces": STUECK,
    "piece(s)": STUECK,
    "stueck": STUECK,
    # Mass.
    "g": GRAMM,
    "gr": GRAMM,
    "gramm": GRAMM,
    "gram": GRAMM,
    "grams": GRAMM,
    "kg": KILOGRAMM,
    "kilo": KILOGRAMM,
    "kilogramm": KILOGRAMM,
    "kilogram": KILOGRAMM,
    "kilograms": KILOGRAMM,
    # Volume.
    "ml": MILLILITER,
    "milliliter": MILLILITER,
    "millilitre": MILLILITER,
    "millilitres": MILLILITER,
    "l": LITER,
    "ltr": LITER,
    "liter": LITER,
    "litre": LITER,
    "litres": LITER,
}

#: The name for the loose single unit.
EINZELN = "Einzeln"

#: Compeso designation to the name usual in the house. What is missing here
#: stays: "Karton", "Becher", "Beutel", "Eimer", "Dose" and "Glas" are
#: already called so.
GEBINDE: dict[str, str] = {
    "kiste(n)": "Kasten",
    "flasche(n)": "Flasche",
    "sack(säcke)": "Sack",
    "tüte(n)": "Tüte",
    "schale(n)": "Schale",
    "karnister": "Kanister",  # that is how it stands in the file
    "pom": "BiB",  # postmix syrup in a bag
    # Here the file has the unit where a pack belongs. With a count above one
    # it is the outer packaging, and that is what it is called in the house.
    "stück(e)": "Karton",
    "kg": "Karton",
}


def einheitenkennung(wort: object) -> tuple[str, bool]:
    """The id for a spelling, and whether it had to be guessed.

    The second field decides whether the plan shows a hint. Silently turning a
    unit into pieces would be a loss nobody notices.
    """
    gestrafft = _wort(wort)
    kennung = EINHEITEN.get(gestrafft.casefold())
    return (kennung, False) if kennung else (RUECKFALL, True)


def gebindename(bezeichnung: object, anzahl: object, kennung: str) -> str:
    """The name under which a pack is carried in the house.

    A count of 1 on piece goods is no pack but the single piece. For
    everything else the designation from the file counts, translated into the
    house term.
    """
    gestrafft = _wort(bezeichnung)
    if kennung == STUECK and _als_zahl(anzahl) == 1:
        return EINZELN
    return GEBINDE.get(gestrafft.casefold(), gestrafft)


def zahl_normalisieren(text: str) -> str:
    """'django_project.decimals.dezimal_normalisieren', here without Django.

    The last separator separates the decimal places: "1.234,5" and "1,234.5"
    are both twelve hundred and thirty-four and a half. A test holds both to
    the same answers.
    """
    text = text.strip().replace(" ", "").replace("\u00a0", "")
    punkt, komma = text.rfind("."), text.rfind(",")
    if punkt >= 0 and komma >= 0:
        tausender = "," if punkt > komma else "."
        text = text.replace(tausender, "")
    return text.replace(",", ".")


def _wort(wert: object) -> str:
    """A cell as text. An empty one comes from pandas as NaN, and "nan" is no pack."""
    if wert is None or (isinstance(wert, float) and math.isnan(wert)):
        return ""
    return str(wert).strip()


def _als_zahl(wert: object) -> Decimal | None:
    """The count as a number, with comma as with point. Unreadable yields nothing."""
    try:
        return Decimal(zahl_normalisieren(str(wert)))
    except ArithmeticError, ValueError:
        return None
