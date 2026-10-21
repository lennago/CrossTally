"""Sorting through the column headers of a list, as in the Django admin.

A click on a header sorts ascending by that column, a second one descending.
In the address that reads '?sortierung=name' or '?sortierung=-name'. A sorted
list can thus be reloaded, passed on and bookmarked like any filter.

Behind every column there is always the same **secondary key** of the list:
for house articles the number, otherwise the name. It stays ascending even
when the column is sorted descending: whoever sorts by "of which active"
descending still wants to read equally sized product groups alphabetically.

Empty values come last in **both** directions. Otherwise SQLite and PostgreSQL
would sort them differently (SQLite takes NULL as the smallest value,
PostgreSQL as the largest), and a good without a family would sit at the top
or the bottom depending on the database. In a query that holds for NULL. A
text that may stay empty is turned into NULL by its list first, like the
number of an own house article ('nummer_oder_leer'), otherwise "" would sort
in front of every number.

Two uses, one set of rules: the filter forms ('ListenFilterForm') sort queries
with it, the check list a list of computed rows.

**While someone searches, the columns rest** ('ruht'), unless a column was
chosen explicitly: then the hits come by how well they match, and no header
carries the arrow. The column order stays behind it as the order within a
tier. Whoever clicks a header sorts by it, search or not.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from operator import attrgetter
from typing import Any

from django.db.models import F

from django_project.labels import Beschriftung
from django_project.mixin_bases import AnsichtBasis

#: The character before the key that means "descending".
ABSTEIGEND = "-"

#: Key to (label, fields), as 'Spaltensortierung' expects it.
#: 'Mapping' instead of 'dict', so a dictionary with narrower values fits in.
Spalten = Mapping[str, tuple[Beschriftung, tuple[str, ...]]]


@dataclass(frozen=True)
class Kopf:
    """What the template needs for a column header."""

    titel: Beschriftung
    #: The value a click on this header writes into 'sortierung'.
    naechster: str
    aktiv: bool
    absteigend: bool

    @property
    def aria_sort(self) -> str:
        if not self.aktiv:
            return "none"
        return "descending" if self.absteigend else "ascending"


class Spaltensortierung:
    """Reads '?sortierung=' and translates it into an order.

    'spalten' maps a key to (label, fields). For queries the fields are field
    names including relations ('warengruppe__name'), for Python lists attribute
    paths ('hausartikel.ware.name'). An unknown key in the address silently
    falls back to the default. A stale bookmark must not break the list.
    """

    def __init__(
        self,
        rohwert: str | None,
        spalten: Spalten,
        standard: str,
        zweitschluessel: tuple[str, ...] = (),
    ):
        self.spalten = spalten
        self.zweitschluessel = zweitschluessel
        rohwert = (rohwert or "").strip()
        absteigend = rohwert.startswith(ABSTEIGEND)
        schluessel = rohwert.removeprefix(ABSTEIGEND)
        #: Whether the address names a column. Without it the default applies.
        self.gewaehlt = schluessel in spalten
        #: Set by the list while another order goes first (the relevance of
        #: a search). Then no header shows itself as the sorted one.
        self.ruht = False
        if schluessel not in spalten:
            absteigend = standard.startswith(ABSTEIGEND)
            schluessel = standard.removeprefix(ABSTEIGEND)
        self.schluessel = schluessel
        self.absteigend = absteigend

    @property
    def rohwert(self) -> str:
        """The value as it stands in the address, for the hidden field in the filter."""
        return f"{ABSTEIGEND if self.absteigend else ''}{self.schluessel}"

    @property
    def felder(self) -> tuple[str, ...]:
        return self.spalten[self.schluessel][1]

    def _zweitfelder(self) -> tuple[str, ...]:
        """The secondary key without what the column itself already sorts."""
        return tuple(feld for feld in self.zweitschluessel if feld not in self.felder)

    def order_by(self) -> list:
        """For 'QuerySet.order_by', with empty values last, in either direction.

        The primary key closes the order. Without it rows that tie (own
        articles without a number, two discontinued rows of one number) may
        come in another order on the next page, and PostgreSQL does not
        promise otherwise: an article then stands on two pages or on none.
        """
        reihenfolge = [
            F(feld).desc(nulls_last=True) if self.absteigend else F(feld).asc(nulls_last=True)
            for feld in self.felder
        ]
        reihenfolge += [F(feld).asc(nulls_last=True) for feld in self._zweitfelder()]
        if "pk" not in self.felder and "pk" not in self.zweitschluessel:
            reihenfolge.append(F("pk").asc())
        return reihenfolge

    def sortieren(self, zeilen: list) -> list:
        """The same order for a Python list.

        In two stable passes: first ascending by the secondary key, then by the
        column in its direction. That way the secondary key stays ascending even
        under a descending column.
        """
        ergebnis = sorted(zeilen, key=_schluessel(self._zweitfelder()))
        # Empty values last: they are split off and appended again instead of
        # being taken into the reversal, which would otherwise bring them to the front.
        voll = [zeile for zeile in ergebnis if not _hat_leere(zeile, self.felder)]
        leer = [zeile for zeile in ergebnis if _hat_leere(zeile, self.felder)]
        voll.sort(key=_schluessel(self.felder), reverse=self.absteigend)
        return voll + leer

    def kopf(self) -> dict[str, Kopf]:
        """Per column, what its header shows and where a click leads.

        As a dictionary, so the template can access it by the fixed key:
        '{{ spalten.kopf.name }}'.
        """
        koepfe = {}
        for schluessel, (titel, _) in self.spalten.items():
            aktiv = schluessel == self.schluessel and not self.ruht
            # The first click sorts ascending, the next on the same column
            # reverses, the one after that goes back again.
            naechster = f"{ABSTEIGEND}{schluessel}" if aktiv and not self.absteigend else schluessel
            koepfe[schluessel] = Kopf(titel, naechster, aktiv, aktiv and self.absteigend)
        return koepfe


def _wert(zeile, feld):
    wert = attrgetter(feld)(zeile)
    return wert.casefold() if isinstance(wert, str) else wert


def _hat_leere(zeile, felder) -> bool:
    return any(_wert(zeile, feld) in (None, "") for feld in felder)


def _schluessel(felder):
    def schluessel(zeile):
        # Empty values to the back. The pair makes sure None is never compared with
        # a real value: when two None face each other they are equal, and a
        # comparison with '<' does not happen at all. Not 'wert or ""'.
        # That turned a zero into a text and failed on the comparison with numbers.
        paare = []
        for feld in felder:
            wert = _wert(zeile, feld)
            paare.append((wert is None, wert))
        return tuple(paare)

    return schluessel


def gliedern(objekte, name_von) -> list[dict]:
    """A sequence already sorted by the grouping, split into blocks.

    'name_von' yields the name of its block per object. It is assumed that
    equal names sit next to each other. That is ensured by the sort by the
    grouping that 'ListenFilterForm.reihenfolge' puts in front.
    """
    bloecke: list[dict] = []
    for objekt in objekte:
        name = name_von(objekt)
        if not bloecke or bloecke[-1]["name"] != name:
            bloecke.append({"name": name, "eintraege": []})
        bloecke[-1]["eintraege"].append(objekt)
    return bloecke


class SpaltenMixin(AnsichtBasis):
    """Column sorting for a 'ListView' without a filter form.

    The view sets 'spalten', 'standard_spalte' and 'zweitschluessel' and passes
    its query through 'sortiert()' at the end. Explicitly at the end and not
    through Django's 'get_ordering': that kicks in before the view annotates its
    counts, and a column like "of which active" would not exist yet.
    """

    spalten: Spalten = {}
    standard_spalte = ""
    zweitschluessel: tuple[str, ...] = ()

    @property
    def spaltensortierung(self) -> Spaltensortierung:
        return Spaltensortierung(
            self.request.GET.get("sortierung"),
            self.spalten,
            self.standard_spalte,
            self.zweitschluessel,
        )

    def sortiert(self, queryset):
        return queryset.order_by(*self.spaltensortierung.order_by())

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        return super().get_context_data(**kwargs) | {"spalten": self.spaltensortierung.kopf()}
