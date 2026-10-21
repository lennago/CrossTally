"""The administration writes what its page changed, under the locks every other path takes.

Django's change page reads its row without a lock, compares the sent form
with the row as it stands when the form comes back, and saves the row whole,
its relations to many as the page showed them. Where the app or the website
writes the same rows, that put back what they wrote while the page was open
(until 10/2026: quantities of count positions, counters of an inventory).
The administration deletes under the same rule ('deletion.VerwaltungSperrtZuerst').
"""

from django.contrib import messages
from django.contrib.admin.options import csrf_protect_m
from django.contrib.admin.utils import unquote
from django.db import router, transaction
from django.http import HttpResponseRedirect

from django_project.form_fields import wahlmenge
from django_project.labels import Beschriftung
from django_project.mixin_bases import ModelAdminBasis
from django_project.validation import ganzzahl


class NurGeaendertesSchreiben(ModelAdminBasis):
    """For the administration: a sent form locks first and writes only what the person changed.

    Every field carries what its page showed ('formfield_for_dbfield'), a
    sent form locks before Django reads ('vor_dem_schreiben_sperren'), and
    written are the columns the person changed ('save_model') and, of a
    relation to many, the entries ticked or unticked ('save_related'). A new
    row is saved whole, as Django saves it.
    """

    #: Columns written along with every change, such as the timestamp the app
    #: fetches rows by.
    immer_mitschreiben: tuple[str, ...] = ()

    def vor_dem_schreiben_sperren(self, daten, nummer: int | None) -> None:
        """Locks what the sent form 'daten' writes, in the order every path locks in.

        'nummer' is the row the form changes, None for a new one. Called in
        the transaction Django saves in, before it reads anything.
        """
        raise NotImplementedError

    def nicht_mehr_zu_aendern(self, nummer: int) -> Beschriftung | None:
        """Asked under the lock: why the row may no longer be changed here, or None.

        A row whose state changed while its page was open, so that
        'has_change_permission' turns it down. Django answers that with a bare
        403, and the person learns neither why nor that nothing was saved.
        """
        return None

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        """Every field sends back what its page showed, beside what stands in it now.

        Django compares a sent form with the row as it is when the form comes
        back. A page opened before someone else wrote took what they wrote for
        a change of its own. With the shown value along ('show_hidden_initial')
        a change is what the person changed, and only that is written.
        """
        feld = super().formfield_for_dbfield(db_field, request, **kwargs)
        if feld is not None:
            feld.show_hidden_initial = True
        return feld

    @csrf_protect_m
    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        """A sent form locks before Django reads the row, in the transaction Django saves in.

        A row that may no longer be changed by then gets its page back, to
        look at, with the reason and without anything written.
        """
        if request.method != "POST":
            return super().changeform_view(request, object_id, form_url, extra_context)
        nummer = None
        if object_id is not None and "_saveasnew" not in request.POST:
            nummer = ganzzahl(unquote(object_id))
        with transaction.atomic(using=router.db_for_write(self.model)):
            self.vor_dem_schreiben_sperren(request.POST, nummer)
            if nummer is not None and (grund := self.nicht_mehr_zu_aendern(nummer)):
                messages.error(request, grund)
                return HttpResponseRedirect(request.get_full_path())
            return super().changeform_view(request, object_id, form_url, extra_context)

    def save_model(self, request, obj, form, change):
        """A changed row only in the columns the person changed. Where none changed, nothing.

        The rest of the row stays as it stands, read under the lock.
        """
        if not change:
            super().save_model(request, obj, form, change)
            return
        spalten = {feld.name for feld in obj._meta.concrete_fields}
        if geaendert := [name for name in form.changed_data if name in spalten]:
            obj.save(update_fields=[*geaendert, *self.immer_mitschreiben])

    def save_related(self, request, form, formsets, change):
        """Of a relation to many, the entries the person ticked or unticked, not the whole list."""
        if not change:
            super().save_related(request, form, formsets, change)
            return
        beziehungen = {feld.name for feld in form.instance._meta.many_to_many}
        for name in form.changed_data:
            if name in beziehungen:
                _beziehung_angleichen(form, name)
        for formset in formsets:
            self.save_formset(request, form, formset, change=change)


def _beziehung_angleichen(form, name: str) -> None:
    """Adds what the person ticked and takes out what they unticked, against what the page showed.

    Django's 'save_m2m' sets the relation to the sent list, and an entry
    someone else made while the page was open went out again. The shown
    entries come back beside the field ('show_hidden_initial'). Taken out is
    only what the field offers: an entry it does not offer, such as a
    deactivated account, the page did not show either.
    """
    feld = form.fields[name]
    gesendet = feld.hidden_widget().value_from_datadict(
        form.data, form.files, form[name].html_initial_name
    )
    gezeigt = {nummer for wert in gesendet if (nummer := ganzzahl(wert)) is not None}
    gewaehlt = {satz.pk for satz in form.cleaned_data[name]}
    beziehung = getattr(form.instance, name)
    if neu := gewaehlt - gezeigt:
        beziehung.add(*neu)
    if weg := gezeigt - gewaehlt:
        beziehung.remove(*wahlmenge(form, name).filter(pk__in=weg))
