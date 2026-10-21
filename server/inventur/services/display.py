"""Quantities and differences as the pages show them."""

from decimal import Decimal

from katalog.models import (
    menge_kurz,
    menge_mit_vorzeichen,
)


def menge_text(wert: Decimal) -> str:
    """A quantity as the account's language writes it: 24, then 1,5 or 1.5.

    Since 2026-09-23 the same as 'menge_kurz'. The name stays where it
    distinguishes the display from the number in the result.
    """
    return menge_kurz(wert)


def menge_text_mit_vorzeichen(wert: Decimal) -> str:
    """Like 'menge_text', with a sign. For differences, see 'menge_mit_vorzeichen'."""
    return menge_mit_vorzeichen(wert)


#: The colour for the difference. What the classes do is in 'static/site.css'.
#: Green means it fits. Red means something is missing. Yellow means there is
#: more than expected. A surplus is no loss, hence not the same colour.
PASST = "differenz-passt"
FEHLT = "differenz-fehlt"
MEHR = "differenz-mehr"


def differenz_klasse(wert: Decimal | None) -> str:
    """Expects the already rounded value, so colour and number say the same.

    Empty where there is no difference: without a target nothing was
    compared, and a colour would claim a result.
    """
    if wert is None:
        return ""
    if wert > 0:
        return MEHR
    return FEHLT if wert < 0 else PASST
