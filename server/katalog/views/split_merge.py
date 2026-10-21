"""The common flow for splitting and merging: location, product family, product.

The templates already share a skeleton ('components/split_page.html' and
'components/merge_page.html'), and the views resembled each other just as
much until 09/2026. Here the flow stands once. The subclass contributes
the subject: where the row comes from, what the template calls it, which
service does the work and what the message says afterwards.

The product handles its merging itself ('WareZusammenfuehrenView'): there
the second step asks about the packs, and who stays is said by the form.
Copying a location shares the preview with merging ('VorschauView').
"""

from typing import Any
from urllib.parse import urlencode

from django.contrib import messages
from django.db.models import QuerySet
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext_lazy as _
from django.views.generic import FormView

#: What the page says when the button was pressed for a choice whose preview
#: was not on screen. The product's merge says it too ('katalog/views/ware.py').
VORIGE_WAHL = _(
    "Noch ist nichts geschehen. Die Vorschau zeigte die vorige Wahl, hier ist sie für die neue."
)


class AufteilenView(FormView):
    """One row becomes two, with the content distributed onto both."""

    #: Where the row comes from, including what form and template need prefetched.
    queryset: QuerySet
    #: What the template calls the row, next to 'satz' for the skeleton.
    kontextname: str

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.satz = get_object_or_404(self.queryset, pk=kwargs["pk"])

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"satz": self.satz}

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs) | {"satz": self.satz, self.kontextname: self.satz}

    def form_valid(self, form):
        daten = form.cleaned_data
        bericht = self.aufteilen(daten["name"], daten["neuer_name"], daten["wandern"])
        self.melden(bericht)
        return redirect(bericht.bleibt.get_absolute_url())

    def aufteilen(self, name: str, neuer_name: str, wandern):
        """Calls the service and returns its report ('bleibt', 'neu', 'gewandert')."""
        raise NotImplementedError

    def melden(self, bericht) -> None:
        """Says what happened."""
        raise NotImplementedError


class VorschauView(FormView):
    """Writes only for the choice whose preview was on screen.

    The preview comes via GET: the choice is in the address, and the preview
    can be reloaded without sending anything. The choice itself stands in the
    form that writes. In a form of its own, the button would send along the
    choice of the last preview, and whoever chose anew without looking would
    copy into the old area or merge the wrong row.

    The template names the shown choice in 'gezeigt'. If the selected one
    differs, nothing is written, and its preview comes first. With
    'live_preview.js' the preview follows the choice at once, so this way is
    left for a page without the script.
    """

    #: The field of the choice, in the form and in the address.
    wahlfeld: str

    def post(self, request, *args, **kwargs):
        gewaehlt = request.POST.get(self.wahlfeld, "")
        gezeigt = request.POST.get("gezeigt")
        if gewaehlt == gezeigt:
            return super().post(request, *args, **kwargs)
        # Without 'gezeigt' no preview was on screen yet, and the button was "Preview".
        if gezeigt is not None:
            messages.info(request, VORIGE_WAHL)
        abfrage = {
            self.wahlfeld: gewaehlt,
            "name": self.mitgenommener_name(request.POST.get("name", "").strip(), gezeigt),
        }
        abfrage = {feld: wert for feld, wert in abfrage.items() if wert}
        return redirect(f"{request.path}?{urlencode(abfrage)}" if abfrage else request.path)

    def mitgenommener_name(self, name: str, gezeigt: str | None) -> str:
        """The name from the form, so that a typed one is not lost on the way to the preview."""
        return name


class ZusammenfuehrenView(VorschauView):
    """A second row is absorbed into this one, in two steps.

    First choose and see what would happen, then confirm. Nothing is written
    beforehand, and a deleted row does not come back.
    """

    wahlfeld = "quelle"
    #: Where the remaining row comes from.
    queryset: QuerySet
    #: What the template calls it, next to 'satz' for the skeleton.
    kontextname: str

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.ziel = get_object_or_404(self.queryset, pk=kwargs["pk"])

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs() | {"ziel": self.ziel}
        if self.request.method == "GET" and self.request.GET.get("quelle"):
            kwargs["data"] = {
                "quelle": self.request.GET["quelle"],
                "name": self.request.GET.get("name") or self.ziel.name,
            }
        return kwargs

    def get(self, request, *args, **kwargs):
        form = self.get_form()
        if form.is_bound:
            form.is_valid()
        return self.render_to_response(self.get_context_data(form=form))

    def get_context_data(self, **kwargs):
        kontext: dict[str, Any] = super().get_context_data(**kwargs) | {
            "satz": self.ziel,
            self.kontextname: self.ziel,
        }
        form = kontext["form"]
        quelle = (
            form.cleaned_data.get("quelle")
            if form.is_bound and hasattr(form, "cleaned_data")
            else None
        )
        if quelle is not None:
            kontext |= {"quelle": quelle} | self.vorschau(quelle)
        return kontext

    def form_valid(self, form):
        quelle = form.cleaned_data["quelle"]
        # Remembered beforehand: the service deletes the source.
        name_quelle = quelle.name
        bericht = self.zusammenfuehren(quelle, form)
        self.melden(name_quelle, bericht)
        return redirect(self.ziel.get_absolute_url())

    def vorschau(self, quelle) -> dict[str, Any]:
        """What the template needs for the preview, without writing."""
        raise NotImplementedError

    def zusammenfuehren(self, quelle, form):
        """Calls the service and returns its report."""
        raise NotImplementedError

    def melden(self, name_quelle: str, bericht) -> None:
        """Says what happened. The source no longer exists by then."""
        raise NotImplementedError
