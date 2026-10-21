"""Template filters for quantities, so a page reads like a message or a sheet.

'floatformat' rounds to a fixed number of places and knows nothing of the
project. These filters hand the Decimal to 'menge_kurz', the same text the
messages and the PDFs use: no superfluous places, the language's decimal
separator. Loaded with '{% load mengen %}'.
"""

import re
from decimal import Decimal, InvalidOperation

from django import template

from katalog.models import menge_kurz as _menge_kurz
from katalog.models import menge_mit_vorzeichen as _menge_mit_vorzeichen

register = template.Library()


def _dezimal(wert) -> Decimal | None:
    if wert is None or wert == "":
        return None
    try:
        return wert if isinstance(wert, Decimal) else Decimal(str(wert))
    except InvalidOperation:
        return None


@register.filter
def menge_kurz(wert) -> str:
    """A quantity without superfluous places. Nothing stays nothing."""
    zahl = _dezimal(wert)
    return "" if zahl is None else _menge_kurz(zahl)


@register.filter
def menge_mit_vorzeichen(wert) -> str:
    """Like 'menge_kurz', with the plus on a positive number: for differences."""
    zahl = _dezimal(wert)
    return "" if zahl is None else _menge_mit_vorzeichen(zahl)


#: The space between a number and its unit.
_ZAHL_UND_EINHEIT = re.compile(r"(?<=\d) (?=\S)")


@register.filter
def teile_zusammenhalten(wert) -> str:
    """A sum of several parts breaks between its parts, not between a number and its unit.

    "0 Stk. · 0 g = 0 kg" in a narrow column of the run sheet broke as
    "0 g = 0" above a lone "kg" (until 10/2026). A sum of one part may still
    put its unit below its number ('umbrechbar' in 'pdf/base.html'): a large
    number has no other place to break.
    """
    text = "" if wert is None else str(wert)
    if " · " not in text and " = " not in text:
        return text
    return _ZAHL_UND_EINHEIT.sub("\u00a0", text)
