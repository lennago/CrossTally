"""The inventory types: list, order and maintenance."""

from django.contrib import messages
from django.db.models import Count, ProtectedError, Q
from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.views.generic import (
    CreateView,
    DeleteView,
    ListView,
    UpdateView,
)

from django_project.deletion import GeschuetztesLoeschen, woran_es_haengt
from django_project.views import Zeilenaktion

from ..forms import InventurartForm, InventurartLoeschenForm
from ..models import SICHTBAR, Hausartikel, Inventurart


class InventurartListView(ListView):
    model = Inventurart
    template_name = "katalog/inventurart_list.html"
    context_object_name = "inventurarten"

    def get_queryset(self):
        # Both numbers in the same query. 'distinct', because the two joins
        # multiply each other. The order explicitly: a counting query ignores
        # the model's own.
        return Inventurart.objects.order_by("reihenfolge", "name").annotate(
            anzahl_artikel=Count(
                "hausartikel", filter=Q(hausartikel__status__in=SICHTBAR), distinct=True
            ),
            anzahl_inventuren=Count("inventuren", distinct=True),
        )

    def get_context_data(self, **kwargs):
        """Per type the houses with their active articles in it, in one query.

        Which houses a type can be counted in is decided by exactly that when
        creating an inventory.
        """
        kontext = super().get_context_data(**kwargs)
        haeuser: dict[int, list[tuple[str, int]]] = {}
        for art_id, haus, anzahl in (
            Hausartikel.objects.filter(aktiv=True)
            .values_list("inventurart_id", "haus__name")
            .annotate(anzahl=Count("pk"))
            .order_by("haus__name")
        ):
            haeuser.setdefault(art_id, []).append((haus, anzahl))
        for art in kontext["inventurarten"]:
            art.haeuser_mit_artikeln = haeuser.get(art.pk, [])
        return kontext


class InventurartCreateView(CreateView):
    model = Inventurart
    form_class = InventurartForm
    template_name = "katalog/inventurart_form.html"
    success_url = reverse_lazy("inventurart_list")


class InventurartUpdateView(UpdateView):
    model = Inventurart
    form_class = InventurartForm
    template_name = "katalog/inventurart_form.html"
    success_url = reverse_lazy("inventurart_list")


class InventurartVerschiebenView(Zeilenaktion):
    """One place forward or back: the order of the tabs in the app and of the choices."""

    model = Inventurart

    def standardziel(self, satz) -> str:
        return reverse("inventurart_list")

    def ausfuehren(self, satz: Inventurart) -> None:
        richtung = self.request.POST.get("richtung")
        if richtung in ("vor", "zurueck"):
            satz.verschieben(-1 if richtung == "vor" else 1)


class InventurartDeleteView(GeschuetztesLoeschen, DeleteView):
    """Its inventories hold it ('PROTECT'): their name comes from it.

    Its house articles move into another type, chosen on the page: every
    house article has one. The last type stays for the same reason.
    """

    model = Inventurart
    form_class = InventurartLoeschenForm
    template_name = "katalog/inventurart_delete.html"
    success_url = reverse_lazy("inventurart_list")
    schutz_meldung = _(
        "„{name}“ lässt sich nicht löschen. Daran hängen noch {woran}, und die tragen ihren Namen."
    )

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"art": self.object}

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs) | {
            "hindernis": self.hindernis(),
            "anzahl_artikel": self.object.hausartikel.filter(status__in=SICHTBAR).count(),
        }

    def hindernis(self) -> str:
        """Why the type cannot go, empty if it can."""
        if self.object.wird_benutzt():
            return str(
                _(
                    "Es gibt Inventuren dieser Art. Das Löschen wird abgelehnt, denn sie tragen "
                    "ihren Namen."
                )
            )
        if not Inventurart.objects.exclude(pk=self.object.pk).exists():
            return str(
                _(
                    "Das ist die letzte Inventurart. Jeder Hausartikel gehört zu einer, deshalb "
                    "bleibt sie. Umbenennen geht."
                )
            )
        return ""

    def form_valid(self, form):
        if grund := self.hindernis():
            messages.error(self.request, grund)
            return redirect(self.schutz_ziel())
        nachfolger = form.cleaned_data["nachfolger"]
        name = self.object.name
        try:
            umgezogen = self.object.loeschen(nachfolger)
        except ProtectedError as fehler:
            messages.error(
                self.request, self.schutz_meldung.format(name=name, woran=woran_es_haengt(fehler))
            )
            return redirect(self.schutz_ziel())
        messages.success(
            self.request,
            _("„%(name)s“ ist gelöscht. %(anzahl)s Artikel zählen jetzt in „%(nachfolger)s“.")
            % {"name": name, "anzahl": umgezogen, "nachfolger": nachfolger.name},
        )
        return redirect(self.get_success_url())
