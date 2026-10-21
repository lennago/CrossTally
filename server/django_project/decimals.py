"""Read decimals the way people and inventory management systems write them.

In German the comma precedes the decimal places, in English the point. Both
come in: typed into a form, read from a file. Both are therefore read
everywhere, and what is displayed is what the account's language writes
('katalog.models.units.menge_kurz').
"""

from django.utils import formats


def dezimal_normalisieren(text: str) -> str:
    """A typed or read number into the notation with a point.

    If both point and comma appear, the last one is the decimal separator and
    the other the thousands separator: "1.234,5" and "1,234.5" are both twelve
    hundred and thirty-four and a half. If only one appears, it separates the
    decimal places: "1,5" and "1.5" are one and a half. A thousands point
    without decimal places ("1.234") cannot be told apart from that. It is not
    typed, and WinTICKET always writes decimal places.
    """
    text = text.strip().replace(" ", "").replace(" ", "")
    punkt, komma = text.rfind("."), text.rfind(",")
    if punkt >= 0 and komma >= 0:
        tausender = "," if punkt > komma else "."
        text = text.replace(tausender, "")
    return text.replace(",", ".")


def dezimaltrenner() -> str:
    """Comma or point, as the account's language writes it."""
    return str(formats.get_format("DECIMAL_SEPARATOR"))
