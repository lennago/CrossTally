"""Re-sorting through buttons and dragging, for packs and the counting list.

Both keep their order through 'Sortiert'. Here stands how a POST becomes
the desired sequence.
"""

from collections.abc import Mapping
from typing import Any

from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.views import View

from django_project.validation import ganzzahl
from django_project.views import zurueck_zu

from ..models import (
    Sortiert,
)


def gewuenschte_reihenfolge(
    request, aktuell: list[int], abschnitt: Mapping[int, int] | None = None
) -> list[int] | None:
    """The new order from a form, or None if nothing changes.

    Two sources, and they **build on each other**: drag & drop sends the
    complete order as 'reihenfolge' (one hidden field per row, in the
    sequence of the DOM), the buttons of a row ('hoch', 'runter',
    'ganz_hoch', 'ganz_runter') a single row. A button acts on the
    **dragged** order, not on the stored one. Whoever first drags and then
    presses an arrow means both. The button must not throw away what was
    dragged.

    An incomplete or foreign 'reihenfolge' does not count: it is skipped,
    the button then acts on the stored sequence.

    'abschnitt' names per row the section it stands in (the counting list:
    its inventory type). A button then moves only within it. Without it the
    whole list is one section.
    """
    basis = aktuell
    gezogen = [
        zahl for wert in request.POST.getlist("reihenfolge") if (zahl := ganzzahl(wert)) is not None
    ]
    if len(gezogen) == len(aktuell) and set(gezogen) == set(aktuell):
        basis = gezogen
    neu = _verschoben(request, basis, abschnitt) or basis
    return neu if neu != aktuell else None


#: The buttons of a row that move it. A single step swaps with the
#: neighbour. A jump puts it right at the start or the end: for the rough
#: part, then one drags.
SCHRITTE = {"hoch": -1, "runter": 1}


SPRUENGE = ("ganz_hoch", "ganz_runter")


def ist_verschiebung(request) -> bool:
    """Whether a button of a row was pressed. Then one stays in the list."""
    return any(request.POST.get(name) for name in (*SCHRITTE, *SPRUENGE))


def _verschoben(
    request, reihenfolge: list[int], abschnitt: Mapping[int, int] | None = None
) -> list[int] | None:
    """Applies a row button to 'reihenfolge', or None if none is pressed.

    A step swaps with the next row **of the same section**, a jump goes to
    the edge of the own section. Rows of other sections keep their places.
    """

    def gleicher_abschnitt(pk: int, satz: int) -> bool:
        return abschnitt is None or abschnitt.get(pk) == abschnitt.get(satz)

    for richtung, versatz in SCHRITTE.items():
        if (satz := _gedrueckt(request, richtung, reihenfolge)) is not None:
            return _schritt(reihenfolge, satz, versatz, gleicher_abschnitt)
    for richtung in SPRUENGE:
        if (satz := _gedrueckt(request, richtung, reihenfolge)) is not None:
            return _sprung(reihenfolge, satz, richtung == "ganz_hoch", gleicher_abschnitt)
    return None


def _gedrueckt(request, richtung: str, reihenfolge: list[int]) -> int | None:
    """The row the button of this direction names, if it is in the list."""
    zahl = ganzzahl(request.POST.get(richtung))
    return zahl if zahl in reihenfolge else None


def _schritt(reihenfolge: list[int], satz: int, versatz: int, gleich) -> list[int] | None:
    """Swaps with the next row of the same section in that direction, if there is one."""
    stelle = reihenfolge.index(satz)
    ende = len(reihenfolge) if versatz > 0 else -1
    ziel = next(
        (
            index
            for index in range(stelle + versatz, ende, versatz)
            if gleich(reihenfolge[index], satz)
        ),
        None,
    )
    if ziel is None:
        return None
    neu = list(reihenfolge)
    neu[stelle], neu[ziel] = neu[ziel], neu[stelle]
    return neu


def _sprung(reihenfolge: list[int], satz: int, nach_oben: bool, gleich) -> list[int] | None:
    """Moves the row to the first or the last place of its own section."""
    uebrige = [pk for pk in reihenfolge if pk != satz]
    eigene = [index for index, pk in enumerate(uebrige) if gleich(pk, satz)]
    if not eigene:
        return None
    stelle = eigene[0] if nach_oben else eigene[-1] + 1
    return [*uebrige[:stelle], satz, *uebrige[stelle:]]


class SortierenView(View):
    """Receives the new order of a group.

    Two paths lead here: drag & drop sends the complete order as
    'reihenfolge', the arrow buttons send 'hoch'/'runter' with a single row.
    Both end in 'Sortiert.neu_sortieren'.

    Both paths lie in the same form, so an arrow click also sends the
    'reihenfolge' along. That is why the arrow takes precedence. Otherwise
    it would be overruled by the unchanged order and have no effect.

    "Save order" sends the form in the background (header
    'X-Requested-With: fetch') and gets 204 instead of the page. The page
    stays, and the button can say that it worked. An arrow comes with the same
    header but wants the list back ('instant_actions.js') and gets the redirect.

    The redirect leads to the page the list stands on ('weiter'): the packs of
    a product are also sorted from the page of a house article. Without it the
    page of the group.
    """

    #: The sorted model and the object within which sorting happens.
    #: Both are set by the subclass.
    model: type[Sortiert]
    #: Any model with 'get_absolute_url'. The view demands no more.
    gruppenmodell: type[Any]

    def post(self, request, pk):
        gruppe = get_object_or_404(self.gruppenmodell, pk=pk)
        aktuell = list(
            self.model._geschwister(gruppe.pk).order_by("sortierung").values_list("pk", flat=True)
        )
        if (neu := gewuenschte_reihenfolge(request, aktuell)) is not None:
            self.model.neu_sortieren(gruppe.pk, neu)
        if request.headers.get("X-Requested-With") == "fetch" and not ist_verschiebung(request):
            return HttpResponse(status=204)
        return HttpResponseRedirect(zurueck_zu(request, gruppe.get_absolute_url()))
