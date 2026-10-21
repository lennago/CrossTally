"""What several views of the inventory workflow share: progress and closing message."""

from django.contrib import messages
from django.db.models import Count
from django.utils.translation import gettext_lazy as _

from ..models import (
    IST_DABEI,
    IST_GEZAEHLT,
    Inventur,
)


def mit_fortschritt(queryset):
    """Inventories including the numbers for the progress bar.

    Two counts in one query instead of two properties on the model: a list
    with twenty inventories would otherwise run forty queries. 'distinct',
    because the two 'Count' join over the same relation.
    """
    return (
        queryset.select_related("haus")
        .prefetch_related("inventurarten")
        .annotate(
            anzahl_positionen=Count("zaehlpositionen", filter=IST_DABEI, distinct=True),
            anzahl_gezaehlt=Count(
                "zaehlpositionen",
                filter=IST_GEZAEHLT,
                distinct=True,
            ),
            anzahl_ergebnisse=Count("ergebnisse", distinct=True),
        )
    )


def melde_abschluss(request, inventur: Inventur, bericht) -> None:
    """The message after closing, including what happened to the master data."""
    geaendert = [
        _("%(anzahl)s %(text)s") % {"anzahl": anzahl, "text": text}
        for anzahl, text in (
            (bericht.wieder_aktiv, _("wieder aktiviert")),
            (bericht.aufgenommen, _("in Zähllisten aufgenommen")),
            (bericht.herausgenommen, _("Gebinde aus Zähllisten genommen")),
            (bericht.inaktiv_gesetzt, _("auf inaktiv gesetzt")),
            (bericht.eingestellt, _("eingestellt")),
            (bericht.art_gewechselt, _("in eine andere Inventurart gewechselt")),
            (bericht.in_die_art, _("in eine Inventurart dieser Inventur aufgenommen")),
            (bericht.soll_gesetzt, _("Sollbestände von Hand gesetzt")),
            (bericht.behaelter_zugeordnet, _("Behälter zugeordnet")),
        )
        if anzahl
    ]
    meldung = _(
        "%(inventur)s ist abgeschlossen: %(eingefroren)s Artikel eingefroren. "
        "Zählpositionen, Sollbestände und Notizen sind damit abgeräumt. Die Notizen "
        "stehen jetzt an den Ergebniszeilen."
    ) % {"inventur": inventur, "eingefroren": bericht.eingefroren}
    if geaendert:
        meldung += " " + _("Außerdem: %(geaendert)s.") % {
            "geaendert": ", ".join(str(eine) for eine in geaendert)
        }
    messages.success(request, meldung)
