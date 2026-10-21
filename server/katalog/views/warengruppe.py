"""Product groups: list, detail page, maintenance and the buttons for status and type."""

from collections import defaultdict

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
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
    Zeilenaktion,
)
from organisation.models import Haus

from ..forms import (
    WarengruppeFilterForm,
    WarengruppeForm,
)
from ..models import (
    SICHTBAR,
    Artikelstatus,
    Hausartikel,
    Inventurart,
    Ware,
    Warengruppe,
    mit_statuszahlen,
)
from .base import SammelstatusView


class WarengruppeListView(GefilterteListe, ListView):
    model = Warengruppe
    template_name = "katalog/warengruppe_list.html"
    context_object_name = "warengruppen"
    filterform_klasse = WarengruppeFilterForm

    def basis(self):
        # The numbers instead of 'sammelstatus' without them: that cost a query
        # per row. Ignored articles do not count, and a group consisting only of
        # them ("Pfand", say) does not even stand there.
        alle = Hausartikel.objects.filter(warengruppe=OuterRef("pk"))
        return (
            mit_statuszahlen(Warengruppe.objects.all())
            .annotate(
                anzahl_artikel=Count(
                    "hausartikel", filter=Q(hausartikel__status__in=SICHTBAR), distinct=True
                ),
            )
            .filter(Exists(alle.filter(status__in=SICHTBAR)) | ~Exists(alle))
        )

    def get_context_data(self, **kwargs):
        """Houses and types per group in one query each. Asked per row it would be two per row."""
        kontext = super().get_context_data(**kwargs)
        gruppen: list[Warengruppe] = list(kontext["object_list"])
        haeuser = {haus.pk: haus for haus in Haus.objects.all()}
        arten = {art.pk: art for art in Inventurart.objects.all()}
        je_gruppe: dict[int, set[int]] = defaultdict(set)
        arten_je_gruppe: dict[int, set[int]] = defaultdict(set)
        sichtbar = Hausartikel.objects.filter(warengruppe__in=gruppen, status__in=SICHTBAR)
        for gruppe_id, haus_id in sichtbar.values_list("warengruppe_id", "haus_id").distinct():
            je_gruppe[gruppe_id].add(haus_id)
        for gruppe_id, art_id in sichtbar.values_list(
            "warengruppe_id", "inventurart_id"
        ).distinct():
            arten_je_gruppe[gruppe_id].add(art_id)
        for gruppe in gruppen:
            gruppe.haeuser_vorgeladen = sorted(
                (haeuser[pk] for pk in je_gruppe[gruppe.pk]), key=lambda haus: haus.name
            )
            eigene = arten_je_gruppe[gruppe.pk]
            gruppe.art_der_gruppe = arten[next(iter(eigene))] if len(eigene) == 1 else None
            gruppe.arten_gemischt = len(eigene) > 1
        return kontext | {"inventurarten": list(arten.values())}


class WarengruppeDetailView(PfadMixin, DetailView):
    model = Warengruppe
    template_name = "katalog/warengruppe_detail.html"
    context_object_name = "warengruppe"

    #: Products per page. The largest group of the development copy rendered
    #: three megabytes at once (until 10/2026), a form per row with a token
    #: and a menu. Forty products keep a page around a hundred kilobytes.
    waren_je_seite = 40

    def get_context_data(self, **kwargs):
        verteilung = self.object.arten_verteilung()
        seite = Paginator(self.waren(), self.waren_je_seite).get_page(self.request.GET.get("page"))
        return super().get_context_data(**kwargs) | {
            "arten_verteilung": verteilung,
            # The menu shows the type all rows agree on, otherwise "mixed".
            "art_der_gruppe": verteilung[0][0] if len(verteilung) == 1 else None,
            # Without any visible article the group is not "mixed": the menu shows a dash.
            "arten_gemischt": len(verteilung) > 1,
            "inventurarten": list(Inventurart.objects.all()),
            "waren": seite.object_list,
            "page_obj": seite,
            "is_paginated": seite.has_other_pages(),
        }

    def waren(self):
        """The products of this group, each with its house articles from this group.

        Grouped like a house into areas: the status of the product belongs to
        the product, the individual rows to the houses. The type of a row is
        the one of its product, as in the product list.
        'pk__in' instead of a join on the product group, because the join
        multiplied the annotated numbers.

        The product's numbers count over **all** its house articles, not only
        those of this group. Exactly on those its status buttons act.
        """
        return (
            # 'warenfamilie' for the product status, the numbers for the
            # confirmation of the type. Both otherwise asked per product.
            Ware.objects.mit_statuszahlen()
            .filter(pk__in=self.object.ware().values("pk"))
            # Fixed for the pages: a tie must not wander between two of them.
            .order_by("name", "pk")
            .select_related("basiseinheit", "warenfamilie")
            .prefetch_related(
                Prefetch(
                    "hausartikel",
                    # 'ware' belongs with it: the row shows the name of the
                    # product, and without it a query per article would be added.
                    queryset=Hausartikel.objects.filter(
                        warengruppe=self.object, status__in=SICHTBAR
                    ).select_related("haus", "ware"),
                    to_attr="artikel_der_gruppe",
                ),
                # For the type menu: the types of all its articles, in every house.
                Prefetch(
                    "hausartikel",
                    queryset=Hausartikel.objects.select_related("inventurart", "haus"),
                ),
            )
        )

    def standard_pfad(self):
        return [liste(_("Warengruppen"), "warengruppe_list")]

    def pfad_kennung(self):
        return f"warengruppe:{self.object.pk}"

    def get_queryset(self):
        # For the header row ("N house articles", the group's status). The
        # grouping comes from 'waren'.
        return mit_statuszahlen(super().get_queryset()).annotate(
            anzahl_artikel=Count(
                "hausartikel", filter=Q(hausartikel__status__in=SICHTBAR), distinct=True
            )
        )


class WarengruppeStatusView(SammelstatusView):
    """Into "Not countable" for all products of the group. Back it goes per product."""

    model = Warengruppe

    MELDUNGEN = SammelstatusView.MELDUNGEN | {
        Artikelstatus.IGNORIERT: _(
            "ist nicht zählbar: alle ihre Waren gehören jetzt zu „Nicht zählbar“, in jedem "
            "Haus. Die Gruppe steht damit nicht mehr in der Liste. Die Waren finden sich "
            "unter der Warenfamilie „Nicht zählbar“."
        ),
    }

    def umsetzen(self, gruppe, status: str) -> list[str]:
        if status == Artikelstatus.IGNORIERT:
            gruppe.ignorieren()
            return []
        return super().umsetzen(gruppe, status)


class WarengruppeInventurartView(Zeilenaktion):
    """Puts all house articles of a product group into one inventory type.

    Only those of the group: the group hangs on the house article, as the
    type does. Where another house carries the same product under another
    product group, it keeps its type there.
    """

    model = Warengruppe

    def ausfuehren(self, satz: Warengruppe) -> None:
        art = Inventurart.objects.filter(pk=self.request.POST.get("inventurart") or None).first()
        if art is None:
            messages.error(self.request, _("Unbekannte Inventurart."))
            return
        geaendert = satz.inventurart_setzen(art)
        if not geaendert:
            messages.info(
                self.request,
                _("In „%(satz)s“ zählte schon alles in „%(art)s“.")
                % {"satz": satz.anzeigename, "art": art.name},
            )
            return
        messages.success(
            self.request,
            ngettext(
                "Ein Artikel aus „%(satz)s“ zählt jetzt in „%(art)s“.",
                "%(anzahl)s Artikel aus „%(satz)s“ zählen jetzt in „%(art)s“.",
                geaendert,
            )
            % {"anzahl": geaendert, "satz": satz.anzeigename, "art": art.name},
        )


class WarengruppeCreateView(CreateView):
    model = Warengruppe
    form_class = WarengruppeForm
    template_name = "katalog/warengruppe_form.html"


class WarengruppeUpdateView(UpdateView):
    model = Warengruppe
    form_class = WarengruppeForm
    template_name = "katalog/warengruppe_form.html"


class WarengruppeDeleteView(GeschuetztesLoeschen, DeleteView):
    model = Warengruppe
    template_name = "katalog/warengruppe_delete.html"
    success_url = reverse_lazy("warengruppe_list")
    schutz_meldung = _(
        "„{name}“ lässt sich nicht löschen. Daran hängen noch {woran}. Erst die Artikel "
        "einer anderen Gruppe zuordnen, in ihrem Formular oder beim nächsten Import über "
        "„Ändern“."
    )
