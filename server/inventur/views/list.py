"""The list of inventories, creating a run and deleting."""

from itertools import groupby

from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from django.views.generic import DeleteView, FormView, ListView

from django_project.deletion import NurMitRueckfrage
from django_project.roles import ist_administrator

from ..forms import (
    InventurAnlegenForm,
    InventurFilterForm,
)
from ..models import (
    Inventur,
    arten_des_laufs,
    zeilen_der_inventuren_sperren,
)
from ..services import (
    inventuren_anlegen,
)
from .base import mit_fortschritt


class InventurListView(ListView):
    """All inventories, newest first, combined into runs.

    A "run" is the cut-off date. The bracket over the houses needs no more.
    The template therefore gets no flat list but blocks: per run a header
    row and below it its houses, like house → area → location on the house
    page.
    """

    model = Inventur
    template_name = "inventur/inventur_list.html"
    context_object_name = "inventuren"
    paginate_by = 50

    def get_queryset(self):
        self.filterform = InventurFilterForm(self.request.GET)
        return self.filterform.anwenden(mit_fortschritt(super().get_queryset()))

    def get_context_data(self, **kwargs):
        kontext = super().get_context_data(**kwargs)
        return kontext | {
            "filterform": self.filterform,
            "laeufe": self.laeufe(kontext["object_list"]),
        }

    @staticmethod
    def laeufe(inventuren) -> list[dict]:
        """The page split into blocks per cut-off date.

        'groupby' is enough because the sort of the filter form leads with the
        cut-off date. There is deliberately no sort by house here, it would tear
        the blocks apart. A block containing exactly one house is no run over
        several houses but a single inventory. The template tells that by the
        length.
        """
        bloecke = []
        for stichtag, block in groupby(inventuren, key=lambda inventur: inventur.stichtag):
            gruppe = list(block)
            bloecke.append(
                {
                    "stichtag": stichtag,
                    "arten": arten_des_laufs(gruppe),
                    "inventuren": gruppe,
                    "url": gruppe[0].lauf_url(),
                }
            )
        return bloecke


class InventurCreateView(FormView):
    """A run arises: per chosen house an inventory including count positions.

    'FormView' and not 'CreateView': saved is not *one* row but one per
    house. That is done by 'inventuren_anlegen' in one transaction: either
    the whole run is in place or none of its inventories. Several houses then
    lead into the run, one onto its page.
    """

    form_class = InventurAnlegenForm
    template_name = "inventur/inventur_form.html"

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "benutzer": self.request.user}

    def form_valid(self, form):
        zaehlende = list(form.cleaned_data["zaehlende"])
        angelegt = inventuren_anlegen(
            form.cleaned_data["haeuser"],
            form.cleaned_data["inventurarten"],
            form.cleaned_data["stichtag"],
            angelegt_von=self.request.user,
            zaehlende=zaehlende,
        )
        positionen = sum(inventur.anzahl_positionen for inventur in angelegt)
        if zaehlende:
            text = ngettext(
                "Eine Inventur angelegt mit %(positionen)s Zählpositionen: %(haeuser)s. "
                "Zählende außer dir: %(zaehlende)s. Sie sehen die Inventur in der Zähl-App, "
                "sobald die Zählung beginnt.",
                "%(anzahl)s Inventuren angelegt mit zusammen %(positionen)s "
                "Zählpositionen: %(haeuser)s. Zählende außer dir, in jedem Haus: "
                "%(zaehlende)s. Sie sehen die Inventuren in der Zähl-App, sobald die Zählung "
                "beginnt.",
                len(angelegt),
            )
        else:
            text = ngettext(
                "Eine Inventur angelegt mit %(positionen)s Zählpositionen: %(haeuser)s. "
                "Du zählst mit. Wer sonst noch zählt, wird an der Inventur zugeordnet. "
                "Ohne Eintrag sieht niemand die Inventur in der Zähl-App.",
                "%(anzahl)s Inventuren angelegt mit zusammen %(positionen)s "
                "Zählpositionen: %(haeuser)s. Du zählst mit. Wer sonst noch zählt, wird "
                "je Inventur zugeordnet. Ohne Eintrag sieht niemand die Inventur in der "
                "Zähl-App.",
                len(angelegt),
            )
        messages.success(
            self.request,
            text
            % {
                "anzahl": len(angelegt),
                "positionen": positionen,
                "haeuser": ", ".join(
                    f"{inventur.haus.name} ({inventur.anzahl_positionen})" for inventur in angelegt
                ),
                "zaehlende": ", ".join(str(person) for person in zaehlende),
            },
        )
        if len(angelegt) == 1:
            return redirect(angelegt[0].get_absolute_url())
        return redirect(angelegt[0].lauf_url())


class InventurDeleteView(NurMitRueckfrage, DeleteView):
    """Deleting takes count positions and target stocks along, hence the confirmation.

    A **closed** inventory is not deleted here at all, not by the
    administrator either: the frozen results hang on it, the only evidence of
    what was counted. A button next to "PDF" on the page one works on daily
    was too close for that. It can still go (a trial run should not clog the
    list forever), but only in the administration, where one goes on purpose
    for that.
    """

    model = Inventur
    template_name = "inventur/inventur_delete.html"
    context_object_name = "inventur"
    success_url = reverse_lazy("inventur_list")

    def get_queryset(self):
        return mit_fortschritt(super().get_queryset())

    def get_context_data(self, **kwargs):
        # The administrator is shown the way into the administration.
        return super().get_context_data(**kwargs) | {
            "ist_administrator": ist_administrator(self.request.user)
        }

    def form_valid(self, form):
        with transaction.atomic():
            # Read under the lock, not as the page loaded it: an inventory
            # closed in the meantime keeps its frozen results. The closing
            # locks the same row (until 10/2026 it went along). Without the
            # key, as the closing and a new row of the app lock it: a merge
            # of products that touched rows of this inventory twice checks
            # their keys against it at its commit. With 'FOR UPDATE' that
            # merge waited here while the delete waited for its rows, and
            # PostgreSQL broke one of the two off (until 10/2026). The delete
            # takes the full lock itself, at its end.
            frisch = get_object_or_404(
                Inventur.objects.select_for_update(no_key=True), pk=self.object.pk
            )
            if not frisch.laeuft:
                messages.error(
                    self.request,
                    _(
                        "%(object)s ist abgeschlossen. An ihr hängen die eingefrorenen "
                        "Ergebnisse. Gelöscht wird sie nur in der Verwaltung."
                    )
                    % {"object": self.object},
                )
                return redirect(self.object.get_absolute_url())
            # Then its rows: a quantity or a weighing of the app locks only its
            # row ('zeilen_der_inventuren_sperren').
            zeilen_der_inventuren_sperren([frisch.pk])
            bezeichnung = str(self.object)
            antwort = super().form_valid(form)
        messages.success(
            self.request, _("%(bezeichnung)s wurde gelöscht.") % {"bezeichnung": bezeichnung}
        )
        return antwort
