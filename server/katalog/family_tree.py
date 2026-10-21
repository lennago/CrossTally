"""The tree of product families over a flat list of entries.

Every grouped list and every report shows the same picture: the families at
the top, below them their subfamilies, and the products only in the family
they belong to directly. A family above sums everything below it. Built once
here, over whatever the entries are: products, house articles, rows of the
check list or of a report. The caller says which family an entry belongs to.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Self

from django.utils.translation import gettext_lazy as _

from django_project.labels import Beschriftung

from .models import Warenfamilie

#: Products without a family do not vanish. They gather here, at the end.
OHNE_FAMILIE = _("Ohne Warenfamilie")


@dataclass
class Familienknoten:
    """A family with its direct entries and the families below it."""

    name: Beschriftung
    familie: Warenfamilie | None = None
    tiefe: int = 0
    eintraege: list[Any] = field(default_factory=list)
    unterbloecke: list[Self] = field(default_factory=list)

    @property
    def kennung(self) -> str:
        """Under this the browser remembers whether the block was expanded ('expand.js')."""
        return f"familie:{self.familie.pk if self.familie is not None else 'ohne'}"

    @property
    def anzahl(self) -> int:
        """How many entries the whole subtree holds."""
        return len(self.eintraege) + sum(block.anzahl for block in self.unterbloecke)

    def alle_eintraege(self) -> list[Any]:
        """The entries of this family and of every family below, own first."""
        gesammelt = list(self.eintraege)
        for block in self.unterbloecke:
            gesammelt.extend(block.alle_eintraege())
        return gesammelt


def familienbaum(
    eintraege: Iterable[Any],
    familie_von: Callable[[Any], Warenfamilie | None],
    *,
    knoten: type[Familienknoten] = Familienknoten,
    erlaubt: set[int] | None = None,
    reihenfolge: Callable[[Any], Any] | None = None,
) -> list[Familienknoten]:
    """Sorts the entries into the tree of their families.

    A family appears if it holds an entry or stands above a family that
    does. 'erlaubt' cuts the tree: a family outside it is skipped, and the
    families below it become tops of their own. That is what a filter needs
    when someone chose "beers" alone and not "bottled drinks" above it.
    Families sort by name on every level, and "without family" stands last.
    'reihenfolge' sorts the entries within a family, otherwise they keep the
    order they came in.

    All families are read in one query. The chain is walked in memory,
    because the entries bring only their own family along.
    """
    alle = {familie.pk: familie for familie in Warenfamilie.objects.select_related("leiteinheit")}
    bloecke: dict[int | None, Familienknoten] = {}
    wurzeln: list[Familienknoten] = []

    def block_von(nummer: int | None, gesehen: set[int]) -> Familienknoten:
        if nummer in bloecke:
            return bloecke[nummer]
        familie = alle.get(nummer) if nummer is not None else None
        block = bloecke[nummer] = knoten(
            name=familie.anzeigename if familie is not None else OHNE_FAMILIE, familie=familie
        )
        oben = familie.uebergeordnet_id if familie is not None else None
        # A cycle is refused by the form. Guarded anyway, so data written past
        # it cannot make this walk endless.
        if (
            oben is not None
            and oben in alle
            and oben not in gesehen
            and (erlaubt is None or oben in erlaubt)
        ):
            block_von(oben, gesehen | {oben}).unterbloecke.append(block)
        else:
            wurzeln.append(block)
        return block

    for eintrag in eintraege:
        familie = familie_von(eintrag)
        nummer = familie.pk if familie is not None else None
        if nummer is not None and nummer not in alle:
            nummer = None
        block_von(nummer, {nummer} if nummer is not None else set()).eintraege.append(eintrag)

    def ordnen(bloecke: list[Familienknoten], tiefe: int) -> None:
        bloecke.sort(key=lambda block: (block.familie is None, str(block.name)))
        for block in bloecke:
            block.tiefe = tiefe
            if reihenfolge is not None:
                block.eintraege.sort(key=reihenfolge)
            ordnen(block.unterbloecke, tiefe + 1)

    ordnen(wurzeln, 0)
    return wurzeln


def flach[Knoten: Familienknoten](bloecke: list[Knoten]) -> list[Knoten]:
    """The tree as a list, top down, every family once. For tests and counts."""
    liste: list[Knoten] = []
    for block in bloecke:
        liste.append(block)
        liste.extend(flach(block.unterbloecke))
    return liste
