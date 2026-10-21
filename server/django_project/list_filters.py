"""Filter forms over the GET parameters of a list.

The skeleton ('ListenFilterForm') and the multiple selection in the filter
('Mehrfachwahl') belong to no app: catalog and inventory both build on them.
What a particular list filters lives with its app.
"""

from collections.abc import Callable
from dataclasses import dataclass

from django import forms
from django.db import connection
from django.db.models import Case, F, IntegerField, Q, QuerySet, Value, When
from django.utils.translation import gettext_lazy as _

from .columns import Spalten, Spaltensortierung, gliedern
from .form_fields import auswahl
from .labels import Beschriftung
from .validation import ganzzahl


def mit_aehnlichen(queryset: QuerySet, genau: Q, aehnlich: Callable[[], Q]) -> QuerySet:
    """What contains the term, and only where that is nothing, what sounds like it.

    'aehnlich' builds the 'trigram_word_similar' lookups of pg_trgm: the term is
    laid over every stretch of the field, so "nachoss" finds "Nachos" and
    "kaese dip" finds "Käse-Dip". They only work on PostgreSQL, hence a
    callable: SQLite stays with 'genau' and never sees them, and the
    development run is that much smaller than the operation. The similar ones
    do not stand next to the exact hits: "Artikel C" would otherwise always
    bring "Artikel F" along. Numbers do not belong in 'aehnlich', a typed
    article number is meant exactly.
    """
    treffer = queryset.filter(genau)
    if connection.vendor != "postgresql" or treffer.exists():
        return treffer
    return queryset.filter(aehnlich())


@dataclass(frozen=True)
class Wahleintrag:
    """One checkbox of a 'Mehrfachwahl', as the template needs it."""

    wert: str
    name: Beschriftung
    gewaehlt: bool
    #: Whether the box would be ticked without any entry in the address. The
    #: filter script leaves the whole selection out of the address as long as it
    #: matches the default. Forty-five product groups would otherwise make it
    #: unreadably long.
    vorgabe: bool
    #: Entries that are not ordinary ones: "Without product family", "Not countable".
    kursiv: bool
    #: How deep the entry stands in a tree, 0 at the top. The template indents by it.
    tiefe: int = 0
    #: The value of the entry above it in the tree, empty at the top. A tick
    #: on the entry above sets every entry below ('multi_select.js').
    oben: str = ""


class Mehrfachwahl:
    """A multiple selection in the filter: any number of boxes in one menu.

    For product families and product groups, because there one rarely asks for
    exactly one ("all beverage groups", "everything except merch"). The field
    itself is an ordinary 'MultipleChoiceField'. This class carries what the
    template needs beyond that ('components/multi_select.html'): the boxes with
    their default and the short text for the button that opens the menu.

    **If the entry is missing from the address, the default applies**. Without a
    default all entries apply. There is no "nothing selected": the list would be
    empty, and the script disables "Apply" accordingly.
    """

    def __init__(
        self,
        formular: ListenFilterForm,
        feld: str,
        eintraege: list[tuple[str, Beschriftung]],
        *,
        vorgabe: list[str] | None = None,
        vorgabe_text: Beschriftung = _("Alle"),
        kursiv: tuple[str, ...] = (),
        struktur: dict[str, tuple[int, str]] | None = None,
    ):
        self.formular = formular
        self.feld = feld
        self.eintraege = eintraege
        self.alle = [wert for wert, _text in eintraege]
        self.vorgabe = self.alle if vorgabe is None else vorgabe
        self.vorgabe_text = vorgabe_text
        self.kursiv = set(kursiv)
        #: Per value its depth and the value above it, where the entries form a tree.
        self.struktur = struktur or {}
        auswahl(formular, feld).choices = eintraege

    @property
    def titel(self) -> str:
        """The label above the menu, that of the form field."""
        return str(self.formular.fields[self.feld].label or self.feld)

    @property
    def gewaehlt(self) -> list[str]:
        return self.formular.daten.get(self.feld) or self.vorgabe

    @property
    def ist_eingeschraenkt(self) -> bool:
        return set(self.gewaehlt) != set(self.vorgabe)

    @property
    def kaestchen(self) -> list[Wahleintrag]:
        gewaehlt, vorgabe = set(self.gewaehlt), set(self.vorgabe)
        return [
            Wahleintrag(
                wert,
                name,
                wert in gewaehlt,
                wert in vorgabe,
                wert in self.kursiv,
                *self.struktur.get(wert, (0, "")),
            )
            for wert, name in self.eintraege
        ]

    @property
    def text(self) -> Beschriftung:
        """What is selected, in short, for the button that opens the selection."""
        gewaehlt = set(self.gewaehlt)
        if gewaehlt == set(self.vorgabe):
            return self.vorgabe_text
        if gewaehlt >= set(self.alle):
            return _("Alle")
        if len(gewaehlt) == 1:
            return dict(self.eintraege).get(next(iter(gewaehlt)), _("1 gewählt"))
        return _("%(anzahl)s gewählt") % {"anzahl": len(gewaehlt)}

    def nummern(self) -> list[int]:
        """The selected entries that stand for a record."""
        return [zahl for wert in self.gewaehlt if (zahl := ganzzahl(wert)) is not None]

    def __contains__(self, wert: str) -> bool:
        return wert in self.gewaehlt


class ListenFilterForm(forms.Form):
    """Skeleton of the filter forms over the GET parameters of a list.

    All fields are optional, so the form is always valid. Without input the list
    stays unchanged. A nonsensical parameter in the URL must also not drag the
    other filters down: 'daten' therefore yields 'cleaned_data' with exactly the
    faulty field missing, while the error stays attached to the form for display.

    **Sorting goes through the column headers** ('SPALTEN', see
    'django_project/columns.py'). The form carries the chosen sort as a hidden
    field: otherwise it would drop out at the next filtering, because the filter
    script builds the address from exactly this form.

    Where there are **groupings** ('GLIEDERUNGEN'), one can choose whether to
    sort by columns or group by one of them. The grouping is then a collapsible
    block, and inside it the secondary key sorts. The column headers do not sort
    in this view.

    **While someone searches, the hits come by how well they match**
    ('relevanz', since 2026-09-29): first the rows whose designation begins
    with the term, then those that contain it, then what only matches through
    a number or a group. Before that a search for "Cola" in a house sorted by
    number put the colas somewhere among everything that stands in a product
    group with the word in its name. Within a tier the column order stays.
    It applies as long as nobody has chosen a column or a grouping: a click on
    a header is a decision, and the search does not overrule it.

    Subclasses set the class attributes and override 'filtern'.
    """

    #: Key -> (label, fields for 'order_by')
    SPALTEN: Spalten = {}
    #: The default, with '-' in front for descending if desired.
    STANDARD_SPALTE = ""
    #: Sits behind every column: for house articles the number, otherwise the name.
    ZWEITSCHLUESSEL: tuple[str, ...] = ()
    #: Per grouping (key, label in the selection, fields to group by). What a
    #: block is called is said by 'gruppenname'.
    GLIEDERUNGEN: tuple[tuple[str, Beschriftung, tuple[str, ...]], ...] = ()
    #: Fields the template renders as a row of selection lists
    AUSWAHLFELDER: tuple[str, ...] = ()
    #: Fields whose value means "the list is restricted"
    FILTERFELDER: tuple[str, ...] = ()

    NACH_SPALTEN = ""

    def __init__(self, *args, gliederbar: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.gliederbar = gliederbar and bool(self.GLIEDERUNGEN)
        if self.SPALTEN:
            self.fields["sortierung"] = forms.CharField(required=False, widget=forms.HiddenInput)
        if self.gliederbar:
            self.fields["gliederung"] = forms.ChoiceField(
                choices=(
                    (self.NACH_SPALTEN, _("Nach Spalten")),
                    *((schluessel, titel) for schluessel, titel, _feld in self.GLIEDERUNGEN),
                ),
                required=False,
                label=_("Ansicht"),
            )
        if "q" in self.fields:
            self.fields["q"].widget.attrs["class"] = "form-control form-control-sm"
        # Only the search field takes typed input. The select lists of a filter
        # hold a handful of entries each and stay plain, without the text
        # search that 'select.js' otherwise attaches to every <select>.
        for name in self._auswahlnamen():
            self.fields[name].widget.attrs["class"] = "form-select form-select-sm"
            self.fields[name].widget.attrs["data-ohne-suche"] = ""

    def _auswahlnamen(self) -> tuple[str, ...]:
        return (*self.AUSWAHLFELDER, "gliederung") if self.gliederbar else self.AUSWAHLFELDER

    @property
    def auswahlfelder(self):
        """The selection lists, so the template can render them in a loop."""
        return [self[name] for name in self._auswahlnamen()]

    @property
    def daten(self) -> dict:
        # 'is_valid' fills 'cleaned_data' and remembers the result.
        self.is_valid()
        return getattr(self, "cleaned_data", {})

    #: The multiple selections of the form. Subclasses fill them in '__init__'.
    mehrfachwahlen: tuple[Mehrfachwahl, ...] = ()

    @property
    def ist_eingeschraenkt(self) -> bool:
        """Whether anything is filtered at all, for the hint 'Reset filter'."""
        return any(self.daten.get(feld) for feld in self.FILTERFELDER) or any(
            wahl.ist_eingeschraenkt for wahl in self.mehrfachwahlen
        )

    @property
    def suchbegriff(self) -> str:
        return (self.daten.get("q") or "").strip()

    def relevanz(self, begriff: str) -> tuple:
        """The tiers of a search, best first, as conditions for 'When'.

        What meets none of them comes last: the hits through a group or a
        family, and on PostgreSQL those that only sound like the term. Empty
        means the list knows no tiers and sorts by column as ever.
        """
        return ()

    @property
    def spalten(self) -> Spaltensortierung:
        spalten = Spaltensortierung(
            self.daten.get("sortierung"), self.SPALTEN, self.STANDARD_SPALTE, self.ZWEITSCHLUESSEL
        )
        spalten.ruht = (
            not spalten.gewaehlt
            and not self.gegliedert
            and bool(self.suchbegriff)
            and bool(self.relevanz(self.suchbegriff))
        )
        return spalten

    @property
    def nach_relevanz(self) -> bool:
        """Whether the hits of a search come by how well they match."""
        return self.spalten.ruht

    @property
    def gliederung(self) -> tuple[str, Beschriftung, tuple[str, ...]] | None:
        """The chosen grouping or none. Without grouping the columns sort."""
        if not self.gliederbar:
            return None
        gewaehlt = self.daten.get("gliederung")
        return next((eine for eine in self.GLIEDERUNGEN if eine[0] == gewaehlt), None)

    @property
    def gegliedert(self) -> bool:
        return self.gliederung is not None

    def reihenfolge(self) -> list:
        """By the grouping and within it by the secondary key, or by the column."""
        if (gliederung := self.gliederung) is not None:
            return [
                F(feld).asc(nulls_last=True) for feld in (*gliederung[2], *self.ZWEITSCHLUESSEL)
            ]
        spalten = self.spalten
        if spalten.ruht:
            return [self._rang(), *spalten.order_by()]
        return spalten.order_by()

    def _rang(self):
        """The tier of a row as a number, the best one zero."""
        stufen = self.relevanz(self.suchbegriff)
        return Case(
            *(When(stufe, then=Value(nummer)) for nummer, stufe in enumerate(stufen)),
            default=Value(len(stufen)),
            output_field=IntegerField(),
        ).asc()

    def gruppenname(self, objekt, schluessel: str) -> str:
        """What the block is called that 'objekt' belongs to under this grouping."""
        raise NotImplementedError

    def gruppen(self, objekte) -> list[dict] | None:
        """The sorted sequence split into its blocks, or 'None' if not grouped.

        Besides name and entries, every block carries a 'kennung': under it the
        browser remembers whether it was expanded ('static/js/expand.js'). The
        grouping's key belongs in it, otherwise "Beverages" as a product group and
        as a family would count as the same.
        """
        if (gliederung := self.gliederung) is None:
            return None
        schluessel = gliederung[0]
        bloecke = gliedern(objekte, lambda objekt: self.gruppenname(objekt, schluessel))
        for block in bloecke:
            block["kennung"] = f"{schluessel}:{block['name']}"
            block["anzahl"] = len(block["eintraege"])
        return bloecke

    def anwenden(self, queryset: QuerySet) -> QuerySet:
        return self.filtern(queryset, self.daten).order_by(*self.reihenfolge())

    def filtern(self, queryset: QuerySet, daten: dict) -> QuerySet:
        raise NotImplementedError
