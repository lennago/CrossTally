"""Recipe articles: list, detail page and maintenance including recipe."""

from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
)

from django_project.deletion import GeschuetztesLoeschen
from django_project.navigation import PfadMixin, liste
from django_project.views import (
    GefilterteListe,
    InlineFormsetMixin,
)
from organisation.models import Bereich, Haus

from ..forms import (
    RezepturartikelFilterForm,
    RezepturartikelForm,
    RezepturkomponenteFormSet,
)
from ..models import (
    Artikelstatus,
    Erwartung,
    Rezepturartikel,
)


class RezepturartikelListView(GefilterteListe, ListView):
    model = Rezepturartikel
    template_name = "katalog/rezepturartikel_list.html"
    context_object_name = "rezepturartikel"
    filterform_klasse = RezepturartikelFilterForm

    def filterform_kwargs(self):
        # Once for all: the filter needs it, and the row shows it.
        self.status_je_artikel = Rezepturartikel.status_je_artikel()
        return {"status_je_artikel": self.status_je_artikel}

    def basis(self):
        return Rezepturartikel.objects.select_related("basiseinheit").prefetch_related(
            "komponenten__ware__basiseinheit",
            # For 'Artikel.haeuser': the path through expectation, location and area
            # would otherwise cost three queries per row.
            "erwartet_an__ort__bereich__haus",
        )

    def get_context_data(self, **kwargs):
        kontext = super().get_context_data(**kwargs)
        for artikel in kontext["object_list"]:
            artikel.listenstatus = self.status_je_artikel.get(artikel.pk, Artikelstatus.AKTIV)
        return kontext


class RezepturartikelDetailView(PfadMixin, DetailView):
    model = Rezepturartikel
    template_name = "katalog/rezepturartikel_detail.html"
    context_object_name = "rezepturartikel"

    def standard_pfad(self):
        return [liste(_("Rezepturartikel"), "rezepturartikel_list")]

    def pfad_kennung(self):
        return f"rezepturartikel:{self.object.pk}"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related("basiseinheit")
            .prefetch_related(
                "komponenten__ware__basiseinheit",
                "komponenten__ware__hausartikel__haus",
                "erwartet_an__ort__bereich__haus",
            )
        )

    def get_context_data(self, **kwargs):
        # Whether the recipe article can be resolved hangs on the house: every
        # component needs a carried house article there. With an inactive
        # ingredient it stands like an inactive article ('aufloesbar_in'), and
        # the counting list offers it the same way. Until 10/2026 this page
        # said "component missing" there and offered no button.
        aktiv = self.object.verfuegbar_in_haeusern()
        gefuehrt = self.object.aufloesbar_in_haeusern()

        def stufe(haus_id: int) -> str:
            if haus_id in aktiv:
                return "ja"
            return "inaktiv" if haus_id in gefuehrt else "fehlt"

        haeuser = [(haus, stufe(haus.pk)) for haus in Haus.objects.all()]
        return super().get_context_data(**kwargs) | {
            "haeuser": haeuser,
            "behaelter": self.object.waehlbare_behaelter() if self.object.wird_gewogen else [],
            "ortswahl": self.ortswahl(haeuser),
        }

    def ortswahl(self, haeuser):
        """All locations, by house and area, with the position in the counting list.

        Unlike with the house article this spans **all** houses: a recipe
        article belongs to none. Where it cannot be resolved, the template shows
        the reason instead of the buttons, because 'Erwartung.clean()' would
        reject it there anyway.
        """
        stellung = {
            erwartung.ort_id: erwartung
            for erwartung in Erwartung.objects.filter(artikel=self.object.artikel_ptr)
        }
        bereiche = Bereich.objects.prefetch_related("orte").order_by("haus__name", "name")
        nach_haus: dict[int, list] = {}
        for bereich in bereiche:
            nach_haus.setdefault(bereich.haus_id, []).append(
                (bereich, [(ort, stellung.get(ort.pk)) for ort in bereich.orte.all()])
            )
        return [(haus, stufe, nach_haus.get(haus.pk, [])) for haus, stufe in haeuser]


class RezepturartikelCreateView(InlineFormsetMixin, CreateView):
    model = Rezepturartikel
    form_class = RezepturartikelForm
    formset_classes = {"komponenten": RezepturkomponenteFormSet}
    template_name = "katalog/rezepturartikel_form.html"


class RezepturartikelUpdateView(InlineFormsetMixin, UpdateView):
    model = Rezepturartikel
    form_class = RezepturartikelForm
    formset_classes = {"komponenten": RezepturkomponenteFormSet}
    template_name = "katalog/rezepturartikel_form.html"


class RezepturartikelDeleteView(GeschuetztesLoeschen, DeleteView):
    model = Rezepturartikel
    template_name = "katalog/rezepturartikel_delete.html"
    success_url = reverse_lazy("rezepturartikel_list")
    # The app may be creating a row for it just now.
    zuerst_sperren = True
    schutz_meldung = _(
        "„{name}“ lässt sich nicht löschen. Daran hängen noch {woran} einer laufenden "
        "Inventur. Nach deren Abschluss geht es."
    )
