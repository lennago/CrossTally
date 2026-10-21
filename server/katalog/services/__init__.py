"""Import of house articles from the article master of an inventory management system, per house.

Reading, transforming and merging files is done by 'scripts.loader'. Here
stands only what the catalog makes of the finished table.

The import writes **nothing** before someone has looked. 'planen' reads the
table and says per article what would happen, including the hints that stand
out. The yardstick is the model's uniqueness: by number and designation
'einordnen' recognises whether an article is new, lies discontinued, is
carried or has another name under its number. What the house already carries
exactly like that does not even get into the plan. From that a form arises.
Only 'ausfuehren' writes, and exactly what stands there.

Between the two steps lies no server state: the form carries all values,
the unchangeable ones too. The file is no longer needed after planning, and
a validation error throws nobody back to the start.

Products are matched by designation. If the names differ between the
houses, duplicates inevitably arise. They are united afterwards through
'WareZusammenfuehrenView'.

The two steps lie in 'planning.py' and 'apply.py', what both need in
'base.py'. To the outside everything stays with 'katalog.services'.
"""

from .apply import (
    ausfuehren,
    belegt_platz,
    schreibt_gebinde,
    schreibt_hausartikel,
    status_der_zeile,
    uebernimmt,
    warename,
)
from .base import (
    AKTIV,
    AKTUALISIEREN,
    AUFZAEHLUNG,
    BEKANNT,
    EINGESTELLT,
    EINHEIT,
    EINSTELLEN,
    ERSETZEN,
    FEHLT,
    FUEHREND,
    GEBINDE,
    IGNORIEREN,
    INAKTIV,
    KONFLIKT,
    NEU,
    STATUS_JE_VORGEHEN,
    TEILE,
    UEBERGEHEN,
    UMBENANNT,
    VORGEHEN_EINGESTELLT,
    VORGEHEN_FEHLT,
    VORGEHEN_JE_ART,
    VORGEHEN_KONFLIKT,
    VORGEHEN_NEU,
    VORGEHEN_UMBENANNT,
    WARENGRUPPE,
    ZURUECKHOLEN,
    Importbericht,
    Planzeile,
    gebinde_lesen,
    gebinde_text,
    menge_text,
)
from .planning import (
    FEHLENDE_WARNSCHWELLE,
    Namensregister,
    als_daten,
    einordnen,
    gebindefeld_fuer,
    planen,
    saetze_je_nummer,
)

__all__ = [
    "AKTIV",
    "AKTUALISIEREN",
    "AUFZAEHLUNG",
    "BEKANNT",
    "EINGESTELLT",
    "EINHEIT",
    "EINSTELLEN",
    "ERSETZEN",
    "FEHLENDE_WARNSCHWELLE",
    "FEHLT",
    "FUEHREND",
    "GEBINDE",
    "IGNORIEREN",
    "INAKTIV",
    "Importbericht",
    "KONFLIKT",
    "NEU",
    "Namensregister",
    "Planzeile",
    "STATUS_JE_VORGEHEN",
    "TEILE",
    "UEBERGEHEN",
    "UMBENANNT",
    "VORGEHEN_EINGESTELLT",
    "VORGEHEN_FEHLT",
    "VORGEHEN_JE_ART",
    "VORGEHEN_KONFLIKT",
    "VORGEHEN_NEU",
    "VORGEHEN_UMBENANNT",
    "WARENGRUPPE",
    "ZURUECKHOLEN",
    "als_daten",
    "ausfuehren",
    "belegt_platz",
    "einordnen",
    "gebinde_lesen",
    "gebinde_text",
    "gebindefeld_fuer",
    "menge_text",
    "planen",
    "saetze_je_nummer",
    "schreibt_gebinde",
    "schreibt_hausartikel",
    "warename",
    "status_der_zeile",
    "uebernimmt",
]
