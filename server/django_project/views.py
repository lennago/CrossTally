"""What the views of all apps share.

The return after an action, the frame of the row buttons, lists with a
filter form and forms with inline formsets. The module lies in the project
package because catalog and inventory both build on it. The inventory should
not reach into the catalog's views for that.
"""

from typing import Any

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import get_language
from django.views import View
from django.views.generic import TemplateView
from django.views.generic.detail import SingleObjectMixin

from organisation.models import Erscheinungsbild

from .list_filters import ListenFilterForm
from .mixin_bases import AnsichtBasis, ModellformularAnsichtBasis
from .textformat import als_html

#: This many hints of a report are reported individually, the rest as a number.
MAX_FEHLERMELDUNGEN = 10


def meldungen_gekuerzt(request, texte, rest_vorlage, stufe: int = messages.WARNING) -> None:
    """The first hints of a report as messages, the rest as one sentence with their number.

    'rest_vorlage' is the caller's sentence with '%(anzahl)s': what the rest
    consists of (skipped rows, hints, unreadable rows) differs per report.
    """
    texte = list(texte)
    for text in texte[:MAX_FEHLERMELDUNGEN]:
        messages.add_message(request, stufe, text)
    if len(texte) > MAX_FEHLERMELDUNGEN:
        messages.add_message(
            request, stufe, rest_vorlage % {"anzahl": len(texte) - MAX_FEHLERMELDUNGEN}
        )


def zurueck_zu(request, standard: str) -> str:
    """Where to go after an action triggered from within a list.

    The row sends the address of its list along as 'weiter', including filters
    and page number. Without that one would land on page one without filters
    after every button press, and a pass through two hundred articles would be
    impossible.

    'weiter' is user input: foreign targets are discarded, otherwise the button
    would be an open redirect.
    """
    return sicheres_ziel(request, request.POST.get("weiter") or "") or standard


def sicheres_ziel(request, ziel: str) -> str:
    """'ziel' if it stays on this site, otherwise empty.

    Also for a 'weiter' that comes with a link and ends up in a form and its
    "Cancel" (the creating of a house article, 'HausartikelCreateView').
    """
    if ziel and url_has_allowed_host_and_scheme(
        ziel, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return ziel
    return ""


def fehler_melden(request, fehler: ValidationError) -> None:
    """Every text of a 'ValidationError' as an error message of its own."""
    for text in fehler.messages:
        messages.error(request, text)


class Zeilenaktion(SingleObjectMixin, View):
    """A button on a row: execute, report, back to the list.

    The buttons in the lists (status, scope, weighing …) are all built the
    same: a POST on the row, then back to where the button was ('zurueck_zu').
    The models watch themselves over what is allowed. A 'ValidationError'
    becomes a message here instead of an error page.

    Subclasses write 'ausfuehren' and report in it themselves what happened.
    """

    def standardziel(self, satz) -> str:
        """Where to go when the row sends no 'weiter'."""
        return satz.get_absolute_url()

    def ausfuehren(self, satz) -> None:
        raise NotImplementedError

    def post(self, request, *args, **kwargs):
        satz = self.get_object()
        ziel = zurueck_zu(request, self.standardziel(satz))
        try:
            self.ausfuehren(satz)
        except ValidationError as fehler:
            fehler_melden(request, fehler)
        return redirect(ziel)


class _FormsetUngueltig(Exception):
    def __init__(self, formsets):
        self.formsets = formsets
        super().__init__("Formset ungültig")


class InlineFormsetMixin(ModellformularAnsichtBasis):
    """CreateView/UpdateView with one or more inline formsets.

    The parent row is saved first so the formsets can validate against a real
    instance: 'Gebinde.clean()' and 'ErwartungGebinde.clean()' bail out early
    without a set foreign key. If a formset is invalid, the transaction takes
    the parent row back.
    """

    #: Name in the template -> formset class
    formset_classes: dict = {}

    def get_formsets(self, instance=None, **kwargs) -> dict:
        if instance is None:
            instance = self.object
        return {
            name: klasse(instance=instance, prefix=name, **kwargs)
            for name, klasse in self.formset_classes.items()
        }

    def get_context_data(self, **kwargs):
        kontext = super().get_context_data(**kwargs)
        kontext.setdefault("formsets", self.get_formsets())
        return kontext

    def form_valid(self, form):
        vorher = self.object
        try:
            with transaction.atomic():
                self.object = form.save()
                formsets = self.get_formsets(instance=self.object, data=self.request.POST)
                if not all(formset.is_valid() for formset in formsets.values()):
                    raise _FormsetUngueltig(formsets)
                for formset in formsets.values():
                    formset.save()
        except _FormsetUngueltig as fehler:
            self.object = vorher
            return self.render_to_response(
                self.get_context_data(form=form, formsets=fehler.formsets)
            )
        return redirect(self.get_success_url())


class GegliedertOhneSeiten(AnsichtBasis):
    """Grouped means no paging. A group over two pages would be none.

    For lists with a filter form that knows 'gegliedert'. Until 10/2026 the
    product and the house article list carried this themselves, word for word.
    """

    #: Both Django's ('MultipleObjectMixin') and the list's own. Named here so
    #: pyright knows them on the mixin.
    filterform: Any
    paginate_by: int | None

    def get_paginate_by(self, queryset):
        return None if self.filterform.gegliedert else self.paginate_by


class GefilterteListe(AnsichtBasis):
    """A 'ListView' with a search field and column headers from a 'ListenFilterForm'.

    For the short lists without paging (product groups, recipe articles,
    containers). The view builds its query in 'basis()' with all counts.
    Filtering and sorting happen afterwards, because a column like "of which
    active" would not exist before.
    """

    filterform_klasse: type[ListenFilterForm]

    def filterform_kwargs(self) -> dict[str, Any]:
        return {}

    def basis(self):
        raise NotImplementedError

    def get_queryset(self):
        self.filterform = self.filterform_klasse(self.request.GET, **self.filterform_kwargs())
        return self.filterform.anwenden(self.basis())

    def get_context_data(self, **kwargs) -> dict[str, Any]:
        return super().get_context_data(**kwargs) | {
            "filterform": self.filterform,
            "spalten": self.filterform.spalten.kopf(),
        }


class DatenschutzView(TemplateView):
    """The information under Art. 13 GDPR, as it stands on the appearance.

    The text is prefilled with the bundled one and is extended, changed or
    replaced in the administration, one per language. Whoever empties it takes
    the page out: then it does not exist, and the footer no longer links to it.
    It is rendered as text with five rules ('textformat.als_html'), not as a
    template. That way no HTML from outside reaches the page, and the
    Content-Security-Policy stays as it is. Controller and contact come from
    the fields next to it, as in the footer.
    """

    template_name = "privacy.html"

    def get_context_data(self, **kwargs):
        text = Erscheinungsbild.aktuell_fuer(self.request).datenschutztext_fuer(
            get_language() or "de"
        )
        if not text:
            raise Http404
        return super().get_context_data(**kwargs) | {"datenschutztext": als_html(text)}


class ImpressumView(TemplateView):
    """The legal notice, written whole in the administration.

    Unlike the privacy page there is no frame around it: what a legal notice
    has to say depends on the business, and the application cannot know it.
    Empty, there is no page, as for an internal tool usually ('DEPLOYMENT.md').
    The same five rules as the privacy text, no HTML from outside.
    """

    template_name = "legal_notice.html"

    def get_context_data(self, **kwargs):
        text = Erscheinungsbild.aktuell_fuer(self.request).impressum_fuer(get_language() or "de")
        if not text:
            raise Http404
        return super().get_context_data(**kwargs) | {"impressum": als_html(text)}
