"""Product families: list, maintenance including products, splitting and merging."""

from typing import TYPE_CHECKING, cast

from django.contrib import messages
from django.db.models import Count
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
)

from django_project.deletion import NurMitRueckfrage
from django_project.mixin_bases import ModellformularAnsichtBasis
from django_project.validation import ganzzahl

from ..forms import (
    WarenfamilieAufteilenForm,
    WarenfamilieForm,
    WarenfamilieZusammenfuehrenForm,
)
from ..forms.filters import familien_geordnet
from ..models import Warenfamilie
from ..warenfamilie_split_merge import aufteilen as aufteilen_familie
from ..warenfamilie_split_merge import zusammenfuehren as zusammenfuehren_familien
from .split_merge import AufteilenView, ZusammenfuehrenView

if TYPE_CHECKING:
    # Only for pyright: what the list annotates and computes per family.
    class _GezaehlteFamilie(Warenfamilie):
        warenzahl: int
        gesamtzahl: int


class WarenfamilieListView(ListView):
    """The families the reports are grouped by, as the tree they form.

    In the order of the tree, a family below another indented under it. No
    sorting by columns: sorted by the number of products, the tree would
    fall apart. "Not countable" stands last, it belongs to no tree.
    """

    model = Warenfamilie
    template_name = "katalog/warenfamilie_list.html"
    context_object_name = "familien"

    def get_queryset(self):
        familien = cast(
            "list[_GezaehlteFamilie]",
            familien_geordnet(
                super()
                .get_queryset()
                .select_related("leiteinheit")
                # Not 'anzahl_waren': that is already the name of the property on the
                # model, and an annotation of the same name cannot be set.
                .annotate(warenzahl=Count("waren", distinct=True))
            ),
        )
        # The tree sorts "Not countable" first. Here it goes last.
        familien.sort(key=lambda familie: familie.sammelt_ignorierte)
        self._gesamtzahlen(familien)
        return familien

    @staticmethod
    def _gesamtzahlen(familien: list[_GezaehlteFamilie]) -> None:
        """Every family also carries the products of the families below ('gesamtzahl')."""
        gesamt = {familie.pk: 0 for familie in familien}
        je_nummer = {familie.pk: familie for familie in familien}
        for familie in familien:
            aktuell = familie
            gesehen: set[int] = set()
            while aktuell is not None and aktuell.pk not in gesehen:
                gesehen.add(aktuell.pk)
                gesamt[aktuell.pk] += familie.warenzahl
                aktuell = je_nummer.get(aktuell.uebergeordnet_id or 0)
        for familie in familien:
            familie.gesamtzahl = gesamt[familie.pk]


class WarenfamilieDetailView(DetailView):
    model = Warenfamilie
    template_name = "katalog/warenfamilie_detail.html"
    context_object_name = "familie"

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs) | {
            "waren": self.object.waren.select_related("basiseinheit", "warenfamilie")
            .mit_statuszahlen()
            .prefetch_related("umrechnungen")
            .order_by("name"),
            "unterfamilien": self.object.unterfamilien.select_related("leiteinheit")
            .annotate(warenzahl=Count("waren", distinct=True))
            .order_by("name"),
            "anzahl_darunter": self.object.anzahl_aller_waren,
            # Over the whole subtree: a family above sums the products below too.
            "ohne_faktor": self.object.waren_ohne_faktor(),
        }


#: "Not countable" can neither be split nor merged, and nothing stands below
#: it. A product that switches into or out of it is ignored or active again
#: in every house, and nobody should trigger that on the side with "Split".
class WarenfamilieFormMixin(ModellformularAnsichtBasis):
    """Family and products stand in **one** form.

    Two separate steps would mean: first create a family that cannot sum
    anything yet, and then remember to pick the products. Which unit is up
    for choice derives from the **chosen products**, in the browser as in
    the form.
    """

    model = Warenfamilie
    form_class = WarenfamilieForm
    template_name = "katalog/warenfamilie_form.html"

    def form_valid(self, form):
        self.object = form.save()
        return redirect(self.object.get_absolute_url())


class WarenfamilieCreateView(WarenfamilieFormMixin, CreateView):
    """A new family, from a family's page also as one below it ('?unter=<pk>').

    Then the parent is preset and its products are up for choice first,
    to move down. The number rides along in the form as a hidden field, so
    the choice is the same when validating as it was when shown.
    """

    object = None

    def oberfamilie(self) -> Warenfamilie | None:
        pk = ganzzahl(self.request.POST.get("unter") or self.request.GET.get("unter"))
        if pk is None:
            return None
        return Warenfamilie.echte().filter(pk=pk).first()

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"oberfamilie": self.oberfamilie()}


class WarenfamilieUpdateView(WarenfamilieFormMixin, UpdateView):
    # The template tells creating from editing by 'familie', like the detail
    # page. Until 10/2026 the record came only as 'warenfamilie', and the page
    # for editing was headed "New product family".
    context_object_name = "familie"


class WarenfamilieAufteilenView(AufteilenView):
    """One family becomes two, with the products distributed onto both."""

    form_class = WarenfamilieAufteilenForm
    template_name = "katalog/warenfamilie_split.html"
    queryset = Warenfamilie.echte()
    kontextname = "familie"

    def aufteilen(self, name, neuer_name, wandern):
        return aufteilen_familie(self.satz, name, neuer_name, wandern)

    def melden(self, bericht):
        messages.success(
            self.request,
            _("„%(bleibt)s“ ist aufgeteilt: %(gewandert)s Waren stehen jetzt in „%(neu)s“.")
            % {
                "bleibt": bericht.bleibt.name,
                "gewandert": bericht.gewandert,
                "neu": bericht.neu.name,
            },
        )


class WarenfamilieZusammenfuehrenView(ZusammenfuehrenView):
    """A second family is absorbed into this one.

    The preview shows which products are added and which of them still
    lack their factor to the lead unit.
    """

    form_class = WarenfamilieZusammenfuehrenForm
    template_name = "katalog/warenfamilie_merge.html"
    queryset = Warenfamilie.echte()
    kontextname = "familie"

    def get_context_data(self, **kwargs):
        # In the preview nothing is wrong yet. The factors are just not
        # entered there yet.
        return super().get_context_data(**kwargs) | {"zeige_fehler": self.request.method == "POST"}

    def vorschau(self, quelle):
        return {
            "bleibende": self.ziel.waren.select_related("basiseinheit"),
            "dazu": quelle.waren.select_related("basiseinheit"),
        }

    def zusammenfuehren(self, quelle, form):
        return zusammenfuehren_familien(
            self.ziel, quelle, form.cleaned_data["name"], form.faktoren()
        )

    def melden(self, name_quelle, bericht):
        messages.success(
            self.request,
            _(
                "„%(name_quelle)s“ ist in „%(familie)s“ aufgegangen: %(neue_waren)s Waren kamen "
                "dazu."
            )
            % {
                "name_quelle": name_quelle,
                "familie": bericht.familie.anzeigename,
                "neue_waren": bericht.neue_waren,
            },
        )


class WarenfamilieDeleteView(NurMitRueckfrage, DeleteView):
    """Deleting leaves the products. They stand without assignment afterwards.

    The families below move up one level ('Warenfamilie.delete'). Not "Not
    countable": without it its products would be products again in one go,
    without their house articles noticing.
    """

    model = Warenfamilie
    queryset = Warenfamilie.objects.exclude(sammelt_ignorierte=True)
    template_name = "katalog/warenfamilie_delete.html"
    success_url = reverse_lazy("warenfamilie_list")
    context_object_name = "familie"
