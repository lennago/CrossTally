from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _

from django_project.mixin_bases import AnsichtBasis
from django_project.validation import ganzzahl
from katalog.models import Hausartikel, Rezepturartikel, Ware, Warengruppe
from organisation.models import Bereich, Haus, Ort

if TYPE_CHECKING:
    from django.utils.functional import _StrPromise


@dataclass(frozen=True)
class Station:
    """One step of the breadcrumb bar. Without 'url' only text."""

    titel: str | _StrPromise
    url: str | _StrPromise = ""


def liste(titel: str | _StrPromise, name: str) -> Station:
    """A station that points to a list view."""
    return Station(titel, reverse_lazy(name))


HAEUSER = liste(_("Häuser"), "haus_list")


def _haus(haus: Haus) -> list[Station]:
    return [HAEUSER, Station(haus.name, haus.get_absolute_url())]


def _von_haus(pk: int) -> list[Station]:
    haus = Haus.objects.filter(pk=pk).first()
    return _haus(haus) if haus else []


def _von_haus_artikel(pk: int) -> list[Station]:
    """From a house's house article list."""
    haus = Haus.objects.filter(pk=pk).first()
    if haus is None:
        return []
    return [
        *_haus(haus),
        Station(_("Hausartikel"), reverse("hausartikel_list", args=[haus.pk])),
    ]


def _von_bereich(pk: int) -> list[Station]:
    bereich = Bereich.objects.filter(pk=pk).select_related("haus").first()
    if bereich is None:
        return []
    return [*_haus(bereich.haus), Station(bereich.name, bereich.get_absolute_url())]


def _von_ort(pk: int) -> list[Station]:
    ort = Ort.objects.filter(pk=pk).select_related("bereich__haus").first()
    if ort is None:
        return []
    return [*_von_bereich(ort.bereich_id), Station(ort.name, ort.get_absolute_url())]


def _von_warengruppe(pk: int) -> list[Station]:
    gruppe = Warengruppe.objects.filter(pk=pk).first()
    if gruppe is None:
        return []
    return [
        liste(_("Warengruppen"), "warengruppe_list"),
        Station(gruppe.name, gruppe.get_absolute_url()),
    ]


def _von_ware(pk: int) -> list[Station]:
    artikel = Ware.objects.filter(pk=pk).first()
    if artikel is None:
        return []
    return [
        liste(_("Waren"), "ware_list"),
        Station(artikel.name, artikel.get_absolute_url()),
    ]


def _von_rezepturartikel(pk: int) -> list[Station]:
    artikel = Rezepturartikel.objects.filter(pk=pk).first()
    if artikel is None:
        return []
    return [
        liste(_("Rezepturartikel"), "rezepturartikel_list"),
        Station(artikel.name, artikel.get_absolute_url()),
    ]


def _von_hausartikel(pk: int) -> list[Station]:
    artikel = Hausartikel.objects.filter(pk=pk).select_related("haus").first()
    if artikel is None:
        return []
    return [*_haus(artikel.haus), Station(artikel.name, artikel.get_absolute_url())]


#: Key in 'von' -> the stations that lead back there.
HERKUENFTE = {
    "haus": _von_haus,
    "haus_artikel": _von_haus_artikel,
    "bereich": _von_bereich,
    "ort": _von_ort,
    "warengruppe": _von_warengruppe,
    "ware": _von_ware,
    "rezepturartikel": _von_rezepturartikel,
    "hausartikel": _von_hausartikel,
}


#: Nobody remembers more steps, and every station costs a query.
MAX_SCHRITTE = 6


def kette(von: str | None) -> list[str]:
    """The usable parts of an origin entry, in their order."""
    glieder = []
    for glied in (von or "").split(",")[:MAX_SCHRITTE]:
        schluessel, _trenner, rohwert = glied.partition(":")
        if schluessel in HERKUENFTE and ganzzahl(rohwert) is not None:
            glieder.append(f"{schluessel}:{rohwert}")
    return glieder


def _aufloesen(glied: str) -> list[Station]:
    schluessel, _trenner, rohwert = glied.partition(":")
    return HERKUENFTE[schluessel](int(rohwert))


def stationen(von: str | None) -> list[Station]:
    """Resolves an origin chain. Unknown parts yield an empty list."""
    aufgeloest = []
    for stelle, glied in enumerate(kette(von)):
        if not (teil := _aufloesen(glied)):
            continue
        aufgeloest.extend(teil if stelle == 0 else teil[-1:])
    return aufgeloest


def weiterreichen(von: str | None, kennung: str) -> str:
    """The origin that links of *this* page pass on: path plus own station.

    If the path already passes through this page, it is cut there: whoever
    jumps back and forth between two pages should not build an ever longer bar
    but find their way back to their starting point.
    """
    glieder = kette(von)
    if kennung in glieder:
        glieder = glieder[: glieder.index(kennung)]
    return ",".join([*glieder, kennung][-MAX_SCHRITTE:])


class PfadMixin(AnsichtBasis):
    """Supplies the template with 'pfad' and 'herkunft'.

    'pfad' is the breadcrumb bar of this page. Without a usable origin it is the
    path the page knows by itself. 'herkunft' is what its own links pass on as
    'von', so the chain does not break.
    """

    def standard_pfad(self) -> list[Station]:
        return []

    def pfad_kennung(self) -> str:
        """What this page is called in an origin chain."""
        return ""

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        von = self.request.GET.get("von")
        kennung = self.pfad_kennung()
        return super().get_context_data(**kwargs) | {
            "pfad": stationen(von) or self.standard_pfad(),
            "herkunft": weiterreichen(von, kennung) if kennung else "",
        }
