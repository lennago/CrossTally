"""The forms of the catalog, spread over modules by subject.

From outside it stays 'from katalog.forms import …': here stands everything
views, administration and tests need.

The formset factories ('ImportFormSet', 'GebindeFormSet' …) all carry a
'cast' onto their base class: the stubs only return 'BaseFormSet[Formular]'.
The own parameters ('haus', 'ort') and results ('gebinde', 'erwartungen')
would otherwise be lost to the type checker.
"""

from .base import EinheitenAuswahl, MitKlartextfehler, WarenAuswahl
from .behaelter import BehaelterForm
from .filters import (
    ALLE_STATUS,
    NUR_AKTIVE,
    NUR_EINGESTELLTE,
    NUR_INAKTIVE,
    OHNE_FAMILIE,
    OHNE_FAMILIE_NAME,
    STATUS_AUSWAHL,
    STATUS_JE_AUSWAHL,
    BehaelterFilterForm,
    HausartikelFilterForm,
    RezepturartikelFilterForm,
    WareFilterForm,
    WarengruppeFilterForm,
    familienbedingung,
    sammelstatus_bedingung,
)
from .hausartikel import (
    INAKTIV_LASSEN,
    WIEDER_AKTIVIEREN,
    BaseNeueGebindeFormSet,
    BaseOrtErwartungFormSet,
    HausartikelForm,
    NeueGebindeFormSet,
    NeuesGebindeForm,
    OrtErwartungForm,
    OrtErwartungFormSet,
)
from .imports import (
    FELDER_JE_PLANZEILE,
    HOECHSTENS_PLANZEILEN,
    PLAN_PRAEFIX,
    WARENOPTIONEN,
    ArtikelImportForm,
    BaseImportFormSet,
    DateiImportForm,
    GeteilteWarenwahl,
    ImportFormSet,
    ImportZeileForm,
    NurGewaehlteOption,
    plan_zu_gross,
)
from .inventurart import InventurartForm, InventurartLoeschenForm
from .rezepturartikel import (
    BaseRezepturkomponenteFormSet,
    RezepturartikelAdminForm,
    RezepturartikelForm,
    RezepturartikelRegeln,
    RezepturkomponenteFormSet,
)
from .split_merge import AufteilenForm, ZusammenfuehrenForm
from .ware import (
    BaseGebindeFormSet,
    DublettenForm,
    GebindeAufloesenForm,
    GebindeForm,
    GebindeFormSet,
    HausartikelZusammenfuehrenForm,
    WareAufteilenForm,
    WareEinstellungenForm,
    WareForm,
    WareZusammenfuehrenForm,
)
from .warenfamilie import (
    WarenfamilieAufteilenForm,
    WarenfamilieForm,
    WarenfamilieZusammenfuehrenForm,
)
from .warengruppe import WarengruppeForm
from .zaehlliste import (
    BaseZaehllisteFormSet,
    OrtAufteilenForm,
    OrtKopierenForm,
    OrtZusammenfuehrenForm,
    ZaehllisteFormSet,
    ZaehllisteZeileForm,
)

__all__ = [
    "ALLE_STATUS",
    "ArtikelImportForm",
    "AufteilenForm",
    "DateiImportForm",
    "BaseGebindeFormSet",
    "BaseImportFormSet",
    "BaseNeueGebindeFormSet",
    "BaseOrtErwartungFormSet",
    "BaseRezepturkomponenteFormSet",
    "BaseZaehllisteFormSet",
    "BehaelterFilterForm",
    "BehaelterForm",
    "EinheitenAuswahl",
    "DublettenForm",
    "FELDER_JE_PLANZEILE",
    "GebindeAufloesenForm",
    "GebindeForm",
    "GebindeFormSet",
    "GeteilteWarenwahl",
    "HausartikelFilterForm",
    "HausartikelForm",
    "HausartikelZusammenfuehrenForm",
    "HOECHSTENS_PLANZEILEN",
    "INAKTIV_LASSEN",
    "ImportFormSet",
    "ImportZeileForm",
    "InventurartForm",
    "InventurartLoeschenForm",
    "MitKlartextfehler",
    "NUR_AKTIVE",
    "NUR_EINGESTELLTE",
    "NUR_INAKTIVE",
    "NeueGebindeFormSet",
    "NeuesGebindeForm",
    "NurGewaehlteOption",
    "OHNE_FAMILIE",
    "OHNE_FAMILIE_NAME",
    "OrtAufteilenForm",
    "OrtKopierenForm",
    "OrtErwartungForm",
    "OrtErwartungFormSet",
    "OrtZusammenfuehrenForm",
    "PLAN_PRAEFIX",
    "RezepturkomponenteFormSet",
    "STATUS_AUSWAHL",
    "STATUS_JE_AUSWAHL",
    "WareAufteilenForm",
    "WareEinstellungenForm",
    "WareFilterForm",
    "WareForm",
    "WareZusammenfuehrenForm",
    "WARENOPTIONEN",
    "WIEDER_AKTIVIEREN",
    "WarenfamilieAufteilenForm",
    "WarenfamilieForm",
    "WarenfamilieZusammenfuehrenForm",
    "WarenAuswahl",
    "WarengruppeFilterForm",
    "WarengruppeForm",
    "RezepturartikelAdminForm",
    "RezepturartikelFilterForm",
    "RezepturartikelForm",
    "RezepturartikelRegeln",
    "ZaehllisteFormSet",
    "ZaehllisteZeileForm",
    "ZusammenfuehrenForm",
    "familienbedingung",
    "plan_zu_gross",
    "sammelstatus_bedingung",
]
