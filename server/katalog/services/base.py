"""What planning and executing the import share: kinds, procedures, plan row and report."""

import re
from collections.abc import Container
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import pandas as pd
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from django_project.decimals import dezimal_normalisieren
from django_project.labels import Beschriftung

from ..models import (
    GEBINDE_HOECHSTMENGE,
    GEBINDENAME_LAENGE,
    Artikelstatus,
    menge_kurz,
)

#: What the field shows at most for a quantity no pack holds. Beyond it no
#: typo is to be recognised any more, and written out in full "9e999999" has
#: a million digits.
GEBINDE_ANZEIGEGRENZE = Decimal("1e15")

#: What should happen with a row.
AKTIV = "aktiv"


INAKTIV = "inaktiv"


EINSTELLEN = "einstellen"


IGNORIEREN = "ignorieren"


UEBERGEHEN = "uebergehen"


AKTUALISIEREN = "aktualisieren"


ERSETZEN = "ersetzen"


ZURUECKHOLEN = "zurueckholen"


#: How a row relates to what the house already has ('einordnen'). By that
#: it is decided which procedures it knows and in which block it stands.
NEU = "neu"


EINGESTELLT = "eingestellt"


KONFLIKT = "konflikt"


UMBENANNT = "umbenannt"


#: The house carries the number, the file no longer knows it.
FEHLT = "fehlt"


#: Exists exactly so and without deviation, or is not countable. Does not get into the plan.
BEKANNT = "bekannt"


# Terse labels: they stand side by side in the plan as a button row, and
# there is one row of them per article. What they mean is explained by the help.
#
# **Not** creating a new article is not available: it would come back on
# every import. What is not carried arises discontinued or as not
# countable. Afterwards the plan recognises it by number and designation.
VORGEHEN_NEU = (
    (AKTIV, gettext_lazy("Aktiv")),
    (INAKTIV, gettext_lazy("Inaktiv")),
    (EINSTELLEN, gettext_lazy("Eingestellt")),
    (IGNORIEREN, gettext_lazy("Nicht zählbar")),
)


VORGEHEN_EINGESTELLT = (
    (UEBERGEHEN, gettext_lazy("Lassen")),
    (ZURUECKHOLEN, gettext_lazy("Zurückholen")),
)


VORGEHEN_KONFLIKT = (
    (UEBERGEHEN, gettext_lazy("Lassen")),
    (AKTUALISIEREN, gettext_lazy("Ändern")),
)


VORGEHEN_UMBENANNT = (
    (UEBERGEHEN, gettext_lazy("Lassen")),
    (AKTUALISIEREN, gettext_lazy("Umbenennen")),
    (ERSETZEN, gettext_lazy("Ersetzen")),
)


# An article the file no longer knows. "Not countable" is not available:
# that would apply to the product and thus to every house. An article a
# house delists should not take their product away from the others.
VORGEHEN_FEHLT = (
    (UEBERGEHEN, gettext_lazy("Lassen")),
    (INAKTIV, gettext_lazy("Inaktiv")),
    (EINSTELLEN, gettext_lazy("Eingestellt")),
)


VORGEHEN_JE_ART = {
    NEU: VORGEHEN_NEU,
    EINGESTELLT: VORGEHEN_EINGESTELLT,
    KONFLIKT: VORGEHEN_KONFLIKT,
    UMBENANNT: VORGEHEN_UMBENANNT,
    FEHLT: VORGEHEN_FEHLT,
}


#: What a conflict can consist of. With several deviations in one row one
#: can choose individually what "Change" adopts ('uebernimmt').
WARENGRUPPE = "warengruppe"


EINHEIT = "einheit"


GEBINDE = "gebinde"


TEILE = (
    (WARENGRUPPE, gettext_lazy("Warengruppe")),
    (EINHEIT, gettext_lazy("Einheit")),
    (GEBINDE, gettext_lazy("Gebinde")),
)


#: The status a new row arises with. For "Replace" 'status_neu' says it.
STATUS_JE_VORGEHEN = {
    AKTIV: Artikelstatus.AKTIV,
    INAKTIV: Artikelstatus.INAKTIV,
    EINSTELLEN: Artikelstatus.EINGESTELLT,
    IGNORIEREN: Artikelstatus.IGNORIERT,
}


#: What the new row occupies a slot in the house with: number and product.
FUEHREND = (AKTIV, INAKTIV)


#: The precision of a pack quantity. Below it, it would be lost when saving.
GEBINDE_STELLEN = Decimal("0.001")


@dataclass
class Planzeile:
    """An article from the file and what would happen with it.

    'artikelnummer', 'name', 'warengruppe' and 'einheit' are fixed, because
    they come from the inventory management system, and by number and
    designation the next import recognises the row. Changeable are product,
    inventory type and packs.
    """

    artikelnummer: str
    name: str
    warengruppe: str
    einheit: str
    #: The pack field: pack name -> quantity per pack, in the row's unit. The
    #: packs of the product first, then what only the file brings
    #: ('_gebinde_vorbelegen'). What the file brought alone is 'gebinde_datei'.
    gebinde: dict[str, Decimal] = field(default_factory=dict)
    vorgehen: str = AKTIV
    #: How the row relates to the house's stock: NEU, EINGESTELLT, KONFLIKT, UMBENANNT or FEHLT.
    art: str = NEU
    #: Only for "Replace": what the new row arises with, the same choice as for a new one.
    status_neu: str = AKTIV
    #: Primary key of the row the row concerns, otherwise None.
    vorhanden_pk: int | None = None
    #: Product found by designation, otherwise None.
    ware_pk: int | None = None
    #: Deviating name for a newly arising product.
    neuer_ware: str = ""
    #: The type the new or changed row gets. Empty: the one the product's other
    #: articles agree on, otherwise the first ('Inventurart.vorschlag_fuer').
    inventurart: int | None = None
    hinweise: list[str] = field(default_factory=list)
    #: Only for a conflict: what it consists of, as ids from TEILE.
    abweichungen: list[str] = field(default_factory=list)
    #: How many units of the product one unit of the house is. Only if the
    #: house counts in a different dimension than the product. Prefilled with
    #: what the house's row already carries.
    faktor_zur_ware: Decimal | None = None
    #: What the file brings for the packs, before the product's packs join it.
    #: If the product is changed in the form, the field is built anew from
    #: this ('gebindefeld_fuer').
    gebinde_datei: dict[str, Decimal] = field(default_factory=dict)
    #: The product whose packs the field shows. Only for that product does
    #: adopting the field retire what is missing from it ('_gebinde_uebernehmen').
    gebinde_von: int | None = None
    #: Across dimensions the factor the field converted the product's packs
    #: with. Another one in the form means other quantities, and the field is
    #: built anew ('BaseImportFormSet._gebinde_nachfuellen').
    gebinde_faktor: Decimal | None = None

    @property
    def gebindetext(self) -> str:
        """The packs as editable text, one line per pack."""
        return gebinde_text(self.gebinde)

    @property
    def gebinde_dateitext(self) -> str:
        """What the file brings, in the same notation. It rides along as a hidden field."""
        return gebinde_text(self.gebinde_datei)


def gebinde_text(gebinde: dict[str, Decimal]) -> str:
    """Packs as text, one line per pack, as 'gebinde_lesen' reads them back."""
    return "\n".join(f"{name} = {menge_text(menge)}" for name, menge in gebinde.items())


def freier_gebindename(name: str, menge: Decimal, vergeben: Container[str]) -> str:
    """The name for a new quantity whose own name the product already gives to another one.

    First the quantity joins it ("Kasten 20"): a house that buys a deviating
    size gets a second pack. If that is taken as well, by an active or an
    inactive pack, a counter follows ("Kasten 20 (2)"). Until 10/2026 the
    plan proposed the taken name anyway, and the pack fell out of the field.
    The front of the name gives way, so that it fits the column and the form
    reads it back. Plan and adoption both ask here: what the plan proposes is
    what adopting creates.
    """
    zusatz = f" {menge_text(menge)}"
    zaehler = 1
    while True:
        ende = zusatz if zaehler == 1 else f"{zusatz} ({zaehler})"
        kandidat = name[: GEBINDENAME_LAENGE - len(ende)].rstrip() + ende
        if kandidat not in vergeben:
            return kandidat
        zaehler += 1


@dataclass
class Importbericht:
    zeilen: int = 0
    warengruppen_neu: list[str] = field(default_factory=list)
    ware_neu: list[str] = field(default_factory=list)
    artikel_neu: list[str] = field(default_factory=list)
    #: Subset of 'artikel_neu': created but set to inactive right away.
    artikel_inaktiv: list[str] = field(default_factory=list)
    #: Subset of 'artikel_neu': created under "Not countable".
    artikel_ignoriert: list[str] = field(default_factory=list)
    artikel_geaendert: list[str] = field(default_factory=list)
    artikel_ersetzt: list[str] = field(default_factory=list)
    #: Subset of 'artikel_neu': created as discontinued right away.
    artikel_eingestellt: list[str] = field(default_factory=list)
    #: Discontinued rows that are active again.
    artikel_zurueckgeholt: list[str] = field(default_factory=list)
    #: Missing from the file and now set to inactive.
    artikel_fehlend_inaktiv: list[str] = field(default_factory=list)
    #: Missing from the file and now discontinued.
    artikel_fehlend_eingestellt: list[str] = field(default_factory=list)
    gebinde_neu: int = 0
    #: An existing pack is now called differently. The counted rows stay on it.
    gebinde_umbenannt: int = 0
    #: Struck from the field. None is deleted, see '_gebinde_sichern'.
    gebinde_stillgelegt: int = 0
    #: An inactive pack whose quantity someone entered in the field. It is active
    #: again in every house, which is why the report names the number.
    gebinde_reaktiviert: int = 0
    uebergangen: int = 0
    fehler: list[str] = field(default_factory=list)


def _faktor(rohwert) -> Decimal | None:
    """Count per pack. 0 means: no pack is stored in this file.

    'NaN' and 'Infinity' are numbers for Decimal, not for a pack. Until
    10/2026 they ended on the error page. What the column does not hold
    comes back unrounded: the plan shows it in the field, and reading the
    field says it is too large ('gebinde_lesen'). Beyond the display limit
    it comes back as that limit. Until 10/2026 every plan row wrote
    "9e999999" out with a million digits, several times, and "1e1000000"
    no longer fit Decimal's context and ended on the error page.
    """
    try:
        wert = Decimal(dezimal_normalisieren(_text(rohwert)))
    except InvalidOperation:
        return None
    if not wert.is_finite() or wert <= 0:
        return None
    if wert >= GEBINDE_HOECHSTMENGE:
        return min(wert, GEBINDE_ANZEIGEGRENZE)
    gerundet = wert.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    return gerundet if gerundet > 0 else None


def _text(rohwert) -> str:
    return "" if rohwert is None or pd.isna(rohwert) else str(rohwert).strip()


def menge_text(wert: Decimal) -> str:
    """A quantity as the account's language writes it: 24, then 1,5 or 1.5.

    Both are read ('_faktor' takes comma as well as point), written is what
    the language prescribes. The list separator is therefore the semicolon.
    """
    return menge_kurz(wert)


#: Separates a list. The semicolon is the unambiguous separator. A comma
#: only counts as a separator if no digit follows it. Otherwise it would
#: tear apart the decimal comma in "Kasten = 1,5".
AUFZAEHLUNG = re.compile(r";|,(?!\d)")


def gebinde_lesen(text: str) -> tuple[dict[str, Decimal], list[Beschriftung]]:
    """Reads 'Name = Menge', per line or separated by semicolons.

    Returns the packs and the errors.
    """
    gebinde: dict[str, Decimal] = {}
    fehler: list[Beschriftung] = []
    for zeile in _angaben(text):
        name, trenner, rohwert = zeile.rpartition("=")
        name = name.strip()
        if not trenner or not name:
            fehler.append(_("„%(zeile)s“: erwartet wird „Name = Menge“.") % {"zeile": zeile})
            continue
        menge = _faktor(rohwert)
        if menge is None:
            fehler.append(
                _("„%(zeile)s“: „%(wert)s“ ist keine Menge größer null.")
                % {"zeile": zeile, "wert": rohwert.strip()}
            )
        elif menge >= GEBINDE_HOECHSTMENGE:
            fehler.append(
                _("„%(zeile)s“: „%(wert)s“ ist zu groß. Ein Gebinde fasst höchstens %(grenze)s.")
                % {
                    "zeile": zeile,
                    "wert": rohwert.strip(),
                    "grenze": menge_kurz(GEBINDE_HOECHSTMENGE - Decimal("0.001")),
                }
            )
        elif len(name) > GEBINDENAME_LAENGE:
            fehler.append(
                _("„%(name)s“ ist als Name zu lang. Erlaubt sind %(laenge)s Zeichen.")
                % {"name": name, "laenge": GEBINDENAME_LAENGE}
            )
        elif name in gebinde:
            fehler.append(_("„%(name)s“ steht mehrfach in der Liste.") % {"name": name})
        else:
            gebinde[name] = menge
    return gebinde, fehler


def _angaben(text: str) -> list[str]:
    """The individual "Name = Menge" entries from lines and lists.

    "Kasten = 20; Flasche(n) = 1" yields two entries, "Kasten = 1,5" one.
    """
    return [
        geputzt
        for zeile in (text or "").splitlines()
        for teil in AUFZAEHLUNG.split(zeile)
        if (geputzt := teil.strip())
    ]
