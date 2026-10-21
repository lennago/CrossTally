"""Containers: list and maintenance."""

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils.translation import gettext as _
from django.views.generic import (
    CreateView,
    DeleteView,
    ListView,
    UpdateView,
)

from django_project.deletion import GeschuetztesLoeschen
from django_project.views import (
    GefilterteListe,
)

from ..forms import (
    BehaelterFilterForm,
    BehaelterForm,
)
from ..models import (
    Behaelter,
)


class BehaelterListView(GefilterteListe, ListView):
    model = Behaelter
    template_name = "katalog/behaelter_list.html"
    context_object_name = "behaelter"
    filterform_klasse = BehaelterFilterForm

    def basis(self):
        return Behaelter.objects.prefetch_related("haeuser", "waren", "rezepturartikel")


class BehaelterCreateView(CreateView):
    model = Behaelter
    form_class = BehaelterForm
    template_name = "katalog/behaelter_form.html"
    success_url = reverse_lazy("behaelter_list")


class BehaelterUpdateView(UpdateView):
    model = Behaelter
    form_class = BehaelterForm
    template_name = "katalog/behaelter_form.html"
    success_url = reverse_lazy("behaelter_list")


class BehaelterDeleteView(GeschuetztesLoeschen, DeleteView):
    model = Behaelter
    template_name = "katalog/behaelter_delete.html"
    success_url = reverse_lazy("behaelter_list")
    # The app may be weighing in it just now ('api.serializers.counting.behaelter_sperren').
    zuerst_sperren = True

    def form_valid(self, form):
        """The row "Without container" stays, and what is used is not removed silently."""
        if self.object.immer_erlaubt:
            messages.error(
                self.request,
                _("„Ohne Behälter“ gehört zum Datenmodell und lässt sich nicht löschen."),
            )
            return redirect("behaelter_list")
        if self.object.wird_benutzt():
            messages.error(
                self.request,
                _("„%(object)s“ ist noch zugeordnet. Erst die Zuordnungen lösen, dann löschen.")
                % {"object": self.object.name},
            )
            return redirect(self.object.get_absolute_url())
        return super().form_valid(form)
