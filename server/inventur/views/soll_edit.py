"""The target stock by hand: the page on which the target per article is entered."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic.detail import SingleObjectMixin

from ..forms import SollPflegeForm
from ..models import Inventur, Inventurstatus
from ..services import OHNE_SOLL_VON_HAND, soll_von_hand_speichern, sollpflege


class SollPflegeView(SingleObjectMixin, View):
    """Enter the target per article, without a file.

    For houses whose inventory management system exports no stock, and for
    the individual article the file did not know. Only in preparation, like
    the upload: afterwards the counting apps hold their rows, and the target
    should no longer move under them.

    First the articles without target, then those with. Those with are locked
    until the button releases them (see 'SollPflegeForm').
    """

    model = Inventur
    template_name = "inventur/soll_edit.html"

    def get_queryset(self):
        return super().get_queryset().select_related("haus")

    def _nur_vorbereitung(self, request, inventur):
        if inventur.differenz_von_hand:
            messages.error(request, OHNE_SOLL_VON_HAND)
            return redirect(inventur.get_absolute_url())
        if inventur.status == Inventurstatus.VORBEREITUNG:
            return None
        messages.info(
            request,
            _(
                "Der Sollbestand wird in der Vorbereitung gepflegt. Läuft die Zählung schon: "
                "„Zurück in die Vorbereitung“. Die gezählten Mengen bleiben."
            ),
        )
        return redirect(inventur.get_absolute_url())

    def _zeigen(self, request, inventur, form):
        return render(
            request,
            self.template_name,
            {"inventur": inventur, "form": form},
        )

    def get(self, request, *args, **kwargs):
        inventur = self.get_object()
        if (umleitung := self._nur_vorbereitung(request, inventur)) is not None:
            return umleitung
        return self._zeigen(request, inventur, SollPflegeForm(sollpflege(inventur)))

    def post(self, request, *args, **kwargs):
        inventur = self.get_object()
        if (umleitung := self._nur_vorbereitung(request, inventur)) is not None:
            return umleitung
        form = SollPflegeForm(sollpflege(inventur), request.POST)
        if not form.is_valid():
            messages.error(request, _("Eine Eingabe ist keine Menge. Die Zeile ist markiert."))
            return self._zeigen(request, inventur, form)
        try:
            bericht = soll_von_hand_speichern(inventur, form.eingaben())
        except ValidationError as fehler:
            # The service checks status and difference by hand once more under
            # the lock, in the words of the upload. Changed since this page
            # looked, the page says it in its own words.
            frisch = get_object_or_404(Inventur, pk=inventur.pk)
            if (umleitung := self._nur_vorbereitung(request, frisch)) is not None:
                return umleitung
            messages.error(request, " ".join(fehler.messages))
            return self._zeigen(request, inventur, form)
        if bericht.eingetragen or bericht.geaendert or bericht.entfernt:
            messages.success(
                request,
                _(
                    "%(eingetragen)s Sollbestände eingetragen, %(geaendert)s geändert, "
                    "%(entfernt)s entfernt."
                )
                % {
                    "eingetragen": bericht.eingetragen,
                    "geaendert": bericht.geaendert,
                    "entfernt": bericht.entfernt,
                },
            )
        else:
            messages.info(request, _("Nichts geändert."))
        return redirect("inventur_soll_pflegen", pk=inventur.pk)
