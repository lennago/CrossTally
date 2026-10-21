"""Deleting that may fail on a foreign key, with an explanation instead of a server error.

'on_delete=PROTECT' holds a row as long as something points to it: a house
with house articles, a location with count positions, a container with a
weighing. Django reports that as 'ProtectedError', and unhandled it becomes a
500. Here it becomes a message that says what it hangs on.

In the project package because both sides need it: 'organisation' does not
know the catalog, and the catalog should not write the rule a second time.
The administration deletes the same rows and locks them the same way
('VerwaltungSperrtZuerst').
"""

from collections import Counter
from collections.abc import Iterable

from django.contrib import messages
from django.db import models, router, transaction
from django.db.models import ProtectedError
from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _

from django_project.mixin_bases import LoeschansichtBasis, ModelAdminBasis
from django_project.validation import ganzzahl


def woran_es_haengt(fehler: ProtectedError) -> str:
    """Like "3 count positions, 1 target stock": what holds the row, counted by kind."""
    je_modell = Counter(type(satz) for satz in fehler.protected_objects)
    teile = [
        f"{anzahl} {modell._meta.verbose_name if anzahl == 1 else modell._meta.verbose_name_plural}"
        for modell, anzahl in je_modell.items()
    ]
    return ", ".join(sorted(teile))


def zum_loeschen_sperren(modell: type[models.Model], nummern: Iterable[int]) -> None:
    """Locks the rows about to be deleted until the transaction ends, before PROTECT looks.

    For what a new row of the counting app hangs on (location and article).
    The app locks it before it writes ('api.serializers.counting.ort_sperren',
    'gesperrter_artikel'). A row created first is then seen and becomes the
    message. One that comes later waits and finds the row gone. Without the
    lock PROTECT missed a row that was not committed yet, and the delete
    ended in a server error at its commit (until 10/2026).

    Locked is the row of the topmost model: for a house article that of
    'Artikel', which the app locks too. Read by number alone, a house
    article's own table would do, and the lock would miss the app's.
    'FOR UPDATE', as every path that deletes the row it locks. By number,
    through the plain manager and without 'Meta.ordering', whose joins would
    lock other rows along. Called inside 'transaction.atomic()'.
    """
    wurzel = ([modell, *modell._meta.get_parent_list()])[-1]
    list(
        wurzel._base_manager.select_for_update()
        .filter(pk__in=list(nummern))
        .order_by("pk")
        .values_list("pk", flat=True)
    )


class NurMitRueckfrage(LoeschansichtBasis):
    """For every 'DeleteView': deletion goes through the confirmation, i.e. via POST.

    Django's 'DeleteView' also answers the HTTP verb DELETE, through
    'DeletionMixin.delete()', which deletes the row **without** calling
    'form_valid'. But that is exactly where the rules live here: who may delete
    a closed inventory, that "Without container" stays, that 'ProtectedError'
    becomes a message. With DELETE a manager therefore deleted a closed
    inventory including its results (shown on 2026-09-18). Nobody needs the
    verb here: the pages send forms.
    """

    http_method_names = ["get", "post", "head", "options"]


class GeschuetztesLoeschen(NurMitRueckfrage):
    """For 'DeleteView': 'ProtectedError' becomes a message and a return.

    'schutz_meldung' may name '{name}' and '{woran}'. '{woran}' is the counted
    list of what holds the row. The return goes to the row's page unless
    'schutz_ziel' says otherwise.
    """

    schutz_meldung = _("„{name}“ lässt sich nicht löschen. Daran hängen noch {woran}.")

    #: Lock the row before PROTECT looks, for what a new row of the counting
    #: app hangs on (location and article, 'zum_loeschen_sperren').
    zuerst_sperren = False

    def schutz_ziel(self) -> str:
        return self.object.get_absolute_url()

    def form_valid(self, form):
        """Looked at and deleted in one transaction, the row locked first where it says so."""
        try:
            with transaction.atomic():
                if self.zuerst_sperren:
                    zum_loeschen_sperren(type(self.object), [self.object.pk])
                return super().form_valid(form)
        except ProtectedError as fehler:
            name = getattr(self.object, "name", str(self.object))
            messages.error(
                self.request,
                self.schutz_meldung.format(name=name, woran=woran_es_haengt(fehler)),
            )
            return redirect(self.schutz_ziel())


def _zum_loeschen_markiert(request, prefix: str, modell: type[models.Model]) -> list[int]:
    """The rows an inline marks for deleting, by number, as the browser sent them.

    Marked is what sends '<prefix>-<n>-DELETE', as Django's own check of the
    inline reads it. The number stands in the form's key field beside it.
    """
    nummern = []
    kopf, ende = f"{prefix}-", "-DELETE"
    for schluessel in request.POST:
        if not (schluessel.startswith(kopf) and schluessel.endswith(ende)):
            continue
        stelle = schluessel[len(kopf) : -len(ende)]
        nummer = ganzzahl(request.POST.get(f"{kopf}{stelle}-{modell._meta.pk.name}"))
        if nummer is not None:
            nummern.append(nummer)
    return nummern


class VerwaltungSperrtZuerst(ModelAdminBasis):
    """For the administration: deleting locks first, as the website's delete pages do.

    Django's delete page and its action "Delete selected" look at what holds
    the rows ('get_deleted_objects') and only then delete. The lock
    ('vor_dem_loeschen_sperren') therefore comes before that look, in the
    same transaction as the delete. The delete page runs in one of its own,
    the action in one from here ('changelist_view'). Taken in 'delete_model'
    or 'delete_queryset', after the look, the lock would turn a row the app
    created a moment before into a 'ProtectedError', a server error. The
    forms of an inline look at the rows they delete while they are checked,
    so their lock comes when Django asks for the formsets ('get_formset_kwargs').

    Until 10/2026 the administration deleted without the lock. Most of these
    models it hides from its overview ('NurUeberDieSeite'), but their
    addresses answer.
    """

    def vor_dem_loeschen_sperren(self, nummern: list[int]) -> None:
        """Locks what a new row of the app hangs on, to delete these rows. By default the rows."""
        zum_loeschen_sperren(self.model, nummern)

    def inline_sperren(self, inline, eltern: models.Model, nummern: list[int]) -> None:
        """The same for the rows 'nummern' an inline of 'eltern' deletes. By default those rows."""
        zum_loeschen_sperren(inline.model, nummern)

    def get_deleted_objects(self, objs, request):
        if request.method == "POST":
            self.vor_dem_loeschen_sperren([satz.pk for satz in objs])
        return super().get_deleted_objects(objs, request)

    def changelist_view(self, request, extra_context=None):
        """The action "Delete selected" looks and deletes in one transaction, as the delete page."""
        if request.method == "POST" and "delete_selected" in request.POST.getlist("action"):
            with transaction.atomic(using=router.db_for_write(self.model)):
                return super().changelist_view(request, extra_context)
        return super().changelist_view(request, extra_context)

    def get_formset_kwargs(self, request, obj, inline, prefix):
        """Asked once the form is sent, inside its transaction and before the inline is checked."""
        if request.method == "POST" and obj.pk is not None:
            if nummern := _zum_loeschen_markiert(request, prefix, inline.model):
                self.inline_sperren(inline, obj, nummern)
        return super().get_formset_kwargs(request, obj, inline, prefix)
