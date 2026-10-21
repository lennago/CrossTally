"""What the views of product and product group share."""

from django.contrib import messages
from django.utils.translation import gettext_lazy as _

from django_project.labels import Beschriftung
from django_project.views import (
    Zeilenaktion,
)

from ..models import (
    Artikelstatus,
)


class SammelstatusView(Zeilenaktion):
    """Sets a product group or product active, inactive or discontinued.

    Both have no status of their own: it is set on all their house articles,
    in every house ('AusHausartikeln.status_setzen'). What cannot be carried
    out (a row whose slot has been taken in the meantime) comes back as a
    message.
    """

    MELDUNGEN: dict[str, Beschriftung] = {
        Artikelstatus.AKTIV: _(
            "ist aktiv. Wo ein Hausartikel nicht zurückkonnte, steht es darunter."
        ),
        Artikelstatus.INAKTIV: _(
            "ist inaktiv: alle aktiven Hausartikel wurden auf inaktiv gesetzt. Sie bleiben "
            "in den Zähllisten."
        ),
        Artikelstatus.EINGESTELLT: _(
            "ist eingestellt: alle Hausartikel sind eingestellt und stehen in keiner "
            "Zählliste mehr."
        ),
    }

    def ausfuehren(self, satz) -> None:
        status = self.request.POST.get("status", "")
        if status not in self.MELDUNGEN:
            messages.error(self.request, _("Unbekannter Status."))
            return
        offen = self.umsetzen(satz, status)
        messages.success(
            self.request,
            _("„%(satz)s“ %(status)s") % {"satz": satz.name, "status": self.MELDUNGEN[status]},
        )
        for text in offen:
            messages.warning(self.request, text)

    def umsetzen(self, satz, status: str) -> list[str]:
        return satz.status_setzen(status)
