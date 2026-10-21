from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Prefetch, Q, QuerySet
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
from django.views.generic.edit import FormMixin

from django_project.columns import SpaltenMixin
from django_project.deletion import GeschuetztesLoeschen
from django_project.navigation import HAEUSER, PfadMixin, Station

from .forms import BereichForm, HausForm, OrtForm
from .models import Bereich, Haus, Ort, orte_der_bereiche_sperren


def orte_mit_erwartungszahl() -> QuerySet[Ort]:
    """Locations including the number of articles expected there.

    'erwartungen' is the reverse relation of 'katalog.Erwartung'. The number is
    annotated instead of fetched per location, so the house list gets by with
    a single query for all locations.
    """
    return Ort.objects.annotate(anzahl_erwartungen=Count("erwartungen"))


def bereiche_mit_orten() -> QuerySet[Bereich]:
    """Areas including their locations: the floor plan of a house in one query."""
    return Bereich.objects.prefetch_related(Prefetch("orte", queryset=orte_mit_erwartungszahl()))


def mit_artikelzahlen(queryset: QuerySet[Haus]) -> QuerySet[Haus]:
    """Houses including the numbers that count when planning an inventory.

    Total and one per status. How the active ones split over the inventory
    types, the actual workload per tab, is added by 'arten_zahlen'.

    Inactive and discontinued stand on their own: the discontinued were always
    part of the total, one just could not tell how much of it is over, in one
    of the houses a quarter.

    The catalog is only fetched here: 'organisation' does not depend on
    'katalog', and for a display number that should not change.
    """
    from katalog.models import (  # noqa: PLC0415
        SICHTBAR,
        Artikelstatus,
    )

    aktiv = Q(hausartikel__aktiv=True)

    def mit_status(status):
        return Count("hausartikel", filter=Q(hausartikel__status=status), distinct=True)

    return queryset.annotate(
        # Ignored articles are none. They do not count here either.
        anzahl_artikel=Count(
            "hausartikel", filter=Q(hausartikel__status__in=SICHTBAR), distinct=True
        ),
        anzahl_aktiv=Count("hausartikel", filter=aktiv, distinct=True),
        anzahl_inaktiv=mit_status(Artikelstatus.INAKTIV),
        anzahl_eingestellt=mit_status(Artikelstatus.EINGESTELLT),
    )


def arten_zahlen(haeuser) -> None:
    """Hangs on every house its active articles per inventory type ('arten_zahlen').

    A list of (type, number) in the order of the types, one query for all
    houses. Not as an annotation: there are as many types as someone
    creates, and one column each would not fit into the list.
    """
    from katalog.models import Hausartikel, Inventurart  # noqa: PLC0415

    haeuser = list(haeuser)
    arten = list(Inventurart.objects.all())
    zahlen = {
        (haus_id, art_id): anzahl
        for haus_id, art_id, anzahl in Hausartikel.objects.filter(haus__in=haeuser, aktiv=True)
        .values_list("haus_id", "inventurart_id")
        .annotate(anzahl=Count("pk"))
        .order_by()
    }
    for haus in haeuser:
        haus.arten_zahlen = [
            (art, zahlen[haus.pk, art.pk]) for art in arten if (haus.pk, art.pk) in zahlen
        ]


class HausListView(SpaltenMixin, ListView):
    model = Haus
    template_name = "organisation/haus_list.html"
    context_object_name = "haeuser"
    spalten = {
        "name": (_("Haus"), ("name",)),
        "artikel": (_("Hausartikel"), ("anzahl_artikel",)),
        "aktiv": (_("aktiv"), ("anzahl_aktiv",)),
        "inaktiv": (_("inaktiv"), ("anzahl_inaktiv",)),
        "eingestellt": (_("eingestellt"), ("anzahl_eingestellt",)),
        "orte": (_("Orte"), ("anzahl_orte",)),
    }
    standard_spalte = "name"
    zweitschluessel = ("name",)

    def get_queryset(self):
        # The locations as a count instead of 'alle_orte.count' in the template:
        # only what is in the query can be sorted. And on the side one query
        # per house drops away.
        return self.sortiert(
            mit_artikelzahlen(super().get_queryset())
            .annotate(anzahl_orte=Count("bereiche__orte", distinct=True))
            .prefetch_related(Prefetch("bereiche", queryset=bereiche_mit_orten()))
        )

    def get_context_data(self, **kwargs):
        kontext = super().get_context_data(**kwargs)
        # The same objects the template shows: the numbers hang on them.
        haeuser = list(kontext["haeuser"])
        arten_zahlen(haeuser)
        return kontext | {"haeuser": haeuser, "object_list": haeuser}


class HausDetailView(FormMixin, DetailView):
    """Detail page of a house: areas with their locations, house articles, import.

    The embedded form creates a new area. Locations arise below it, because a
    location cannot exist without an area.
    """

    model = Haus
    form_class = BereichForm
    template_name = "organisation/haus_detail.html"
    context_object_name = "haus"

    def get_queryset(self):
        # The numbers in the header as in the list: one query instead of five.
        return mit_artikelzahlen(Haus.objects.all()).prefetch_related(
            Prefetch("bereiche", queryset=bereiche_mit_orten()), "mitarbeitende"
        )

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"haus": self.object}

    #: This many articles the preview shows. The search happens in the own list.
    VORSCHAU = 10

    def get_context_data(self, **kwargs):
        # Imported late: 'organisation' does not depend on 'katalog', here it is
        # only about displaying the articles of this house.
        from katalog.models import SICHTBAR, Hausartikel  # noqa: PLC0415

        artikel = Hausartikel.objects.filter(haus=self.object, status__in=SICHTBAR)
        zahlen = {
            name: getattr(self.object, name)
            for name in (
                "anzahl_artikel",
                "anzahl_aktiv",
                "anzahl_inaktiv",
                "anzahl_eingestellt",
            )
        }
        arten_zahlen([self.object])
        return (
            super().get_context_data(**kwargs)
            | zahlen
            | {
                "neueste_artikel": artikel.select_related(
                    "basiseinheit", "warengruppe", "ware__basiseinheit"
                ).order_by("-geaendert_am")[: self.VORSCHAU],
            }
        )

    def get_success_url(self):
        return self.object.get_absolute_url()

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = self.get_form()
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        bereich = form.save(commit=False)
        bereich.haus = self.object
        bereich.save()
        messages.success(
            request,
            _("Bereich „%(name)s“ wurde angelegt. Orte entstehen darunter.")
            % {"name": bereich.name},
        )
        return redirect(self.get_success_url())


class HausCreateView(CreateView):
    model = Haus
    form_class = HausForm
    template_name = "organisation/haus_form.html"


class HausUpdateView(UpdateView):
    model = Haus
    form_class = HausForm
    template_name = "organisation/haus_form.html"
    context_object_name = "haus"


class HausDeleteView(GeschuetztesLoeschen, DeleteView):
    model = Haus
    template_name = "organisation/haus_delete.html"
    context_object_name = "haus"
    success_url = reverse_lazy("haus_list")
    schutz_meldung = _(
        "„{name}“ lässt sich nicht löschen. Daran hängen noch {woran}. Artikelhistorie "
        "und Inventuren dürfen nicht mit dem Haus verschwinden."
    )


class BereichDetailView(PfadMixin, FormMixin, DetailView):
    """An area with its locations. The form creates a location below it."""

    model = Bereich
    form_class = OrtForm
    template_name = "organisation/bereich_detail.html"
    context_object_name = "bereich"

    def get_queryset(self):
        return Bereich.objects.select_related("haus").prefetch_related(
            Prefetch("orte", queryset=orte_mit_erwartungszahl())
        )

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"haus": self.object.haus}

    def get_initial(self):
        return super().get_initial() | {"bereich": self.object}

    def standard_pfad(self):
        haus = self.object.haus
        return [HAEUSER, Station(haus.name, haus.get_absolute_url())]

    def pfad_kennung(self):
        return f"bereich:{self.object.pk}"

    def get_success_url(self):
        return self.object.get_absolute_url()

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = self.get_form()
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))
        ort = form.save()
        messages.success(
            request,
            _("Ort „%(ort)s“ wurde in %(haus)s angelegt.")
            % {"ort": ort.name, "haus": self.object.name},
        )
        return redirect(self.get_success_url())


class BereichUpdateView(UpdateView):
    model = Bereich
    form_class = BereichForm
    template_name = "organisation/bereich_form.html"
    context_object_name = "bereich"

    def get_queryset(self):
        return Bereich.objects.select_related("haus")

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"haus": self.object.haus}


class BereichDeleteView(GeschuetztesLoeschen, DeleteView):
    """With the area its locations vanish, and with them the expectations.

    That is why the template names beforehand what hangs on it. Count
    positions of a running inventory hold the locations. Then it only works
    after its closing.
    """

    model = Bereich
    schutz_meldung = _(
        "„{name}“ lässt sich nicht löschen. An seinen Orten hängen noch {woran} einer "
        "laufenden Inventur. Nach deren Abschluss geht es."
    )
    template_name = "organisation/bereich_delete.html"
    context_object_name = "bereich"

    def get_queryset(self):
        return Bereich.objects.select_related("haus").prefetch_related(
            Prefetch("orte", queryset=orte_mit_erwartungszahl())
        )

    def get_success_url(self):
        return self.object.haus.get_absolute_url()

    def form_valid(self, form):
        """Its locations locked before PROTECT looks, as the page of a location does.

        A new row of the app locks its location ('api.serializers.counting.ort_sperren'),
        not the area. One created first is then seen and becomes the message,
        one that comes later waits and finds the location gone. Until 10/2026 a
        row created in the same moment landed on a location the area took
        along, and one of the two commits ended in a server error. By number,
        in the order every other path locks locations, 'FOR UPDATE' because
        they are deleted ('django_project.deletion.zum_loeschen_sperren').
        """
        with transaction.atomic():
            orte_der_bereiche_sperren([self.object.pk])
            return super().form_valid(form)


def ort_mit_zaehlliste() -> QuerySet[Ort]:
    """Location including its expectations in counting order.

    The descent to 'hausartikel'/'rezepturartikel' is loaded along, so
    'Artikel.konkret()' in the template triggers no query per row.
    """
    from katalog.models import Erwartung  # noqa: PLC0415

    return Ort.objects.select_related("bereich__haus").prefetch_related(
        Prefetch(
            "erwartungen",
            queryset=Erwartung.objects.select_related(
                "artikel__hausartikel__ware__basiseinheit",
                "artikel__rezepturartikel",
            )
            .prefetch_related("gebinde_vorgaben__gebinde")
            .order_by("sortierung"),
        )
    )


class OrtDetailView(PfadMixin, DetailView):
    """Detail page of a location with the counting list in its order."""

    model = Ort
    template_name = "organisation/ort_detail.html"
    context_object_name = "ort"

    def get_queryset(self):
        return ort_mit_zaehlliste()

    def standard_pfad(self):
        bereich = self.object.bereich
        return [
            HAEUSER,
            Station(bereich.haus.name, bereich.haus.get_absolute_url()),
            Station(bereich.name, bereich.get_absolute_url()),
        ]

    def pfad_kennung(self):
        return f"ort:{self.object.pk}"


class OrtUpdateView(UpdateView):
    """Only the location itself: name and area.

    **Which** articles occur here and in which order they are counted is in
    the counting list. That is work on a long list and needs filters, paging
    and space. In this short form it was only in the way.
    """

    model = Ort
    form_class = OrtForm
    template_name = "organisation/ort_form.html"
    context_object_name = "ort"

    def get_queryset(self):
        return Ort.objects.select_related("bereich__haus")

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"haus": self.object.bereich.haus}


class OrtDeleteView(GeschuetztesLoeschen, DeleteView):
    model = Ort
    template_name = "organisation/ort_delete.html"
    context_object_name = "ort"
    # The app may be creating a row at it just now.
    zuerst_sperren = True
    schutz_meldung = _(
        "„{name}“ lässt sich nicht löschen. Daran hängen noch {woran} einer laufenden "
        "Inventur. Nach deren Abschluss geht es. Zusammenführen geht ebenfalls erst dann."
    )

    def get_queryset(self):
        return Ort.objects.select_related("bereich__haus")

    def get_success_url(self):
        return self.object.bereich.get_absolute_url()
