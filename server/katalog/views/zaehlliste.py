"""The counting list of a location, its article selection, and splitting, merging and copying."""

from typing import Any

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Case, ProtectedError, Value, When
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic import (
    TemplateView,
)

from django_project.navigation import HAEUSER, PfadMixin, Station
from django_project.validation import ganzzahl
from django_project.views import (
    Zeilenaktion,
    zurueck_zu,
)
from organisation.models import Ort

from ..forms import (
    HausartikelFilterForm,
    OrtAufteilenForm,
    OrtKopierenForm,
    OrtZusammenfuehrenForm,
    ZaehllisteFormSet,
)
from ..models import (
    Artikel,
    Erwartung,
    Hausartikel,
    Rezepturartikel,
    nur_die_waage,
    waehlbare_artikel,
)
from ..ort_copy import kopieren as kopieren_ort
from ..ort_copy import vorschau as vorschau_kopie
from ..ort_split_merge import aufteilen as aufteilen_ort
from ..ort_split_merge import geordnet
from ..ort_split_merge import vorschau as vorschau_orte
from ..ort_split_merge import zusammenfuehren as zusammenfuehren_orte
from .sorting import gewuenschte_reihenfolge, ist_verschiebung
from .split_merge import AufteilenView, VorschauView, ZusammenfuehrenView


class ZaehllisteView(PfadMixin, TemplateView):
    """The counting list of a location, in two tabs.

    **Counting list**: what is expected here, in counting order. Order, packs
    and default quantities stand in *one* form with *one* save. Two separate
    submit buttons on the same table invite data loss. The ✕ takes an
    article out of the list.

    **Select articles**: all articles of the house, with the same filters as
    the house article list, and per row a button. A multiple selection field
    would be unusable with a few hundred articles per house.

    Both tabs are addresses of their own ('?reiter=...'), so filters and
    paging in the second work as everywhere else.
    """

    template_name = "katalog/zaehlliste.html"

    LISTE = "liste"
    AUSWAHL = "auswahl"
    #: This many articles per page in the selection tab.
    JE_SEITE = 50

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.ort = get_object_or_404(
            Ort.objects.select_related("bereich__haus"), pk=kwargs["ort_pk"]
        )
        self.reiter = self.AUSWAHL if request.GET.get("reiter") == self.AUSWAHL else self.LISTE

    def standard_pfad(self):
        bereich = self.ort.bereich
        return [
            HAEUSER,
            Station(bereich.haus.name, bereich.haus.get_absolute_url()),
            Station(bereich.name, bereich.get_absolute_url()),
            Station(self.ort.name, self.ort.get_absolute_url()),
        ]

    def pfad_kennung(self):
        return f"ort:{self.ort.pk}"

    def get_context_data(self, **kwargs):
        kontext = super().get_context_data(**kwargs) | {
            "ort": self.ort,
            "reiter": self.reiter,
            "anzahl_erwartet": Erwartung.objects.filter(ort=self.ort).count(),
        }
        kontext.setdefault("formset", ZaehllisteFormSet(prefix="zeilen", ort=self.ort))
        if self.reiter == self.AUSWAHL:
            kontext |= self.auswahl_kontext()
        return kontext

    def auswahl_kontext(self) -> dict:
        """The second tab: the articles of the house including filters and paging.

        House articles and recipe articles stand separately: the filters (product
        group, status, scope) exist only on the house article, and recipe
        articles are so few that a short list of their own is clearer than a
        common pot with half ineffective filters.

        **The search still applies to both**: whoever types "Popcorn" searches
        for the article and not the construction of the row. For the recipe
        article the search goes by name. It has no number and no EAN, it arises
        here and not in the inventory management system.
        """
        haus = self.ort.haus
        # Without grouping: the tab pages itself, and a group over two pages
        # would be none.
        filterform = HausartikelFilterForm(self.request.GET, haus=haus, gliederbar=False)
        hausartikel = filterform.anwenden(
            Hausartikel.objects.filter(haus=haus).select_related(
                "warengruppe", "ware__basiseinheit", "ware__warenfamilie"
            )
        )

        seiten = Paginator(hausartikel, self.JE_SEITE)
        seite = seiten.get_page(self.request.GET.get("page"))

        # Whoever is already expected here, as a set, so the template only looks
        # up per row instead of querying.
        erwartet = set(Erwartung.objects.filter(ort=self.ort).values_list("artikel_id", flat=True))
        waehlbar = set(waehlbare_artikel(self.ort).values_list("pk", flat=True))

        rezepturartikel = (
            Rezepturartikel.objects.filter(pk__in=waehlbar)
            .select_related("basiseinheit")
            .order_by("name")
        )
        if begriff := filterform.daten.get("q", "").strip():
            # As in the lists: what begins with the term comes first.
            rezepturartikel = rezepturartikel.filter(name__icontains=begriff).order_by(
                Case(When(name__istartswith=begriff, then=Value(0)), default=Value(1)),
                "name",
            )

        return {
            "filterform": filterform,
            "spalten": filterform.spalten.kopf(),
            "page_obj": seite,
            "paginator": seiten,
            "is_paginated": seite.has_other_pages(),
            "auswahl_hausartikel": seite.object_list,
            "auswahl_rezepturartikel": rezepturartikel,
            # So the template can say that the search found nothing here.
            # Otherwise the empty block would look like "there are no recipe articles".
            "rezepturartikel_gesucht": bool(filterform.daten.get("q", "").strip()),
            "erwartet": erwartet,
            "waehlbar": waehlbar,
        }

    def post(self, request, *args, **kwargs):
        """Order and packs belong in the same pass.

        Every row sends its position along as a hidden 'reihenfolge' field, on
        ordinary saving too. The order alone is therefore no good as a
        distinction. Only the buttons of a row say that it is about re-sorting
        and that one wants to stay in the list afterwards.

        **Removing adopts the dragged order of the others**. The ✕ should not
        drop what was dragged before.

        **After releasing, the script saves in the background** (recognised by
        the header 'X-Requested-With: fetch'). The response is then 204 instead
        of the page: a crashing tab should cost nothing more of what was already
        dragged. If the formset is invalid in the process (say a half-typed
        quantity), the order is saved nonetheless. The message for it comes on
        the actual save.

        **A row button is no such saving on the side**, although the script
        sends it with the same header ('instant_actions.js'): it wants the list
        back, freshly rendered, to put it in place of the old one. So it goes
        the ordinary way and gets the redirect.

        **A row button acts even if a row does not hold up.** Until 10/2026 an
        invalid formset showed the page with its errors instead, and the ✕ did
        nothing. Nobody saw why: a row without pack and without scale, as the
        selection tab took articles in then, carried the error somewhere in a
        long list. Now the valid rows are saved, the button does its work, and
        a changed row that did not hold up is reported. Only "Save" itself
        insists on a valid list.

        **A save with 'weiter' is the switch to the other tab** with unsaved
        ticks: the script sends the form the ordinary way with the tab as the
        target. It acts like a row button, not like "Save": an untouched row
        that does not hold up (see above) must not keep anyone from the other
        tab. The valid rows are saved, a changed row that did not hold up is
        reported, and the tab opens.

        **Moved is within the type.** The list stands in one section per
        inventory type, and the order is stored the way it is shown. A button
        stays in its section ('formset.abschnitt_je_erwartung').
        """
        rohwert = request.POST.get("entfernen", "")
        zeilenknopf = bool(rohwert) or ist_verschiebung(request)
        nebenbei = request.headers.get("X-Requested-With") == "fetch" and not zeilenknopf
        reiterwechsel = bool(request.POST.get("weiter"))

        formset = ZaehllisteFormSet(request.POST, prefix="zeilen", ort=self.ort)
        if not formset.is_valid() and not (nebenbei or zeilenknopf or reiterwechsel):
            return self.render_to_response(self.get_context_data(formset=formset))

        aktuell = formset.folge
        entfernen = zahl if (zahl := ganzzahl(rohwert)) in aktuell else None
        neu = gewuenschte_reihenfolge(request, aktuell, formset.abschnitt_je_erwartung)
        self._schreiben(formset, entfernen if not nebenbei else None, neu)

        if nebenbei:
            return HttpResponse(status=204)
        if zeilenknopf:
            self.verworfenes_melden(formset)
            # Removing and row buttons are steps in the middle of work: back into
            # the list, freshly built.
            return redirect("zaehlliste", ort_pk=self.ort.pk)
        if reiterwechsel:
            self.verworfenes_melden(formset)
        messages.success(request, _("Zählliste für %(ort)s gespeichert.") % {"ort": self.ort})
        return redirect(zurueck_zu(request, self.ort.get_absolute_url()))

    def _schreiben(self, formset, entfernen: int | None, neu: list[int] | None) -> None:
        """Rows, removal and order in one transaction.

        Removing adopts the dragged order of the others: the ✕ should not drop
        what was dragged before.
        """
        with transaction.atomic():
            formset.speichern()
            if entfernen is not None:
                Erwartung.objects.filter(ort=self.ort, pk=entfernen).delete()
                Erwartung.neu_sortieren(
                    self.ort.pk, [pk for pk in (neu or formset.folge) if pk != entfernen]
                )
                messages.success(
                    self.request,
                    _(
                        "Aus der Zählliste genommen. Der Artikel selbst bleibt. "
                        "Erwartet wird er hier nur nicht mehr."
                    ),
                )
            elif neu is not None:
                Erwartung.neu_sortieren(self.ort.pk, neu)

    def verworfenes_melden(self, formset) -> None:
        """Says which changed rows did not arrive, and why, each on its own.

        The page is rebuilt from the database after the redirect, so the typed
        values are gone with the message. Short enough that it does not hurt,
        and rare: it takes a changed row that does not hold up.
        """
        for form in formset.verworfene_zeilen():
            eigene = " ".join(text for texte in form.errors.values() for text in texte)
            fehler = eigene or " ".join(formset.non_form_errors())
            messages.warning(
                self.request,
                _("„%(artikel)s“ wurde nicht gespeichert: %(fehler)s")
                % {"artikel": form.erwartung_objekt.artikel.name, "fehler": fehler},
            )


class OrtAufteilenView(AufteilenView):
    """One location becomes two, with the articles distributed onto both.

    The order cannot be changed here: each half keeps the sequence it had in
    the old location. Re-sorting happens afterwards in the counting list. Two
    places where the same thing works only invite confusion.
    """

    form_class = OrtAufteilenForm
    template_name = "katalog/ort_split.html"
    queryset = Ort.objects.select_related("bereich__haus")
    kontextname = "ort"

    def aufteilen(self, name, neuer_name, wandern):
        return aufteilen_ort(self.satz, name, neuer_name, wandern)

    def melden(self, bericht):
        messages.success(
            self.request,
            _("„%(bleibt)s“ ist aufgeteilt: %(gewandert)s Artikel stehen jetzt an „%(neu)s“.")
            % {
                "bleibt": bericht.bleibt.name,
                "gewandert": bericht.gewandert,
                "neu": bericht.neu.name,
            },
        )


class OrtZusammenfuehrenView(ZusammenfuehrenView):
    """A second location of the same area is absorbed into this one.

    The preview shows which articles newly come along and which packs for
    already existing ones.
    """

    form_class = OrtZusammenfuehrenForm
    template_name = "katalog/ort_merge.html"
    queryset = Ort.objects.select_related("bereich__haus")
    kontextname = "ort"

    def vorschau(self, quelle):
        zeilen = vorschau_orte(self.ziel, quelle)
        return {
            "vorschau": zeilen,
            "bleibende": list(geordnet(self.ziel)),
            "anzahl_neu": sum(1 for zeile in zeilen if zeile.ist_neu),
        }

    def form_valid(self, form):
        """A count that arrives meanwhile becomes the form's message, not an error page.

        'clean_quelle' rejects a location count positions hang on, but before
        the merge's transaction. If the app counts there just then, deleting
        the location fails at PROTECT and the transaction takes everything
        back. Until 10/2026 that ended as an error page. Asked again, the same
        check names the inventory.
        """
        try:
            return super().form_valid(form)
        except ProtectedError:
            try:
                form.clean_quelle()
            except ValidationError as fehler:
                form.add_error("quelle", fehler)
                return self.form_invalid(form)
            raise

    def zusammenfuehren(self, quelle, form):
        return zusammenfuehren_orte(self.ziel, quelle, form.cleaned_data["name"])

    def melden(self, name_quelle, bericht):
        messages.success(
            self.request,
            _(
                "„%(name_quelle)s“ ist in „%(ort)s“ aufgegangen: %(neue_artikel)s Artikel und "
                "%(neue_gebinde)s Gebinde kamen dazu."
            )
            % {
                "name_quelle": name_quelle,
                "ort": bericht.ort.name,
                "neue_artikel": bericht.neue_artikel,
                "neue_gebinde": bericht.neue_gebinde,
            },
        )


class OrtKopierenView(VorschauView):
    """The location arises a second time, in any area and under a name of its own.

    Two steps like merging, for another reason: nothing is lost here, but
    across houses not every article comes along, and that should be seen
    before the copy exists. The own area is preselected, so the page opens
    with the most frequent case.
    """

    form_class = OrtKopierenForm
    template_name = "katalog/ort_copy.html"
    wahlfeld = "bereich"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.ort = get_object_or_404(Ort.objects.select_related("bereich__haus"), pk=kwargs["pk"])

    def namensvorschlag(self, bereich_pk) -> str:
        """The own name where it is free, otherwise marked as a copy."""
        name = self.ort.name
        if Ort.objects.filter(bereich_id=bereich_pk, name=name).exists():
            return _("%(name)s (Kopie)") % {"name": name}
        return name

    def mitgenommener_name(self, name: str, gezeigt: str | None) -> str:
        # The untouched suggestion stays behind: in the new area another one may fit.
        if (zahl := ganzzahl(gezeigt)) is not None and name == self.namensvorschlag(zahl):
            return ""
        return name

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs() | {"ort": self.ort}
        if self.request.method == "GET":
            bereich = self.request.GET.get("bereich", "")
            if ganzzahl(bereich) is None:
                bereich = str(self.ort.bereich_id)
            kwargs["data"] = {
                "bereich": bereich,
                "name": self.request.GET.get("name") or self.namensvorschlag(int(bereich)),
            }
        return kwargs

    def get(self, request, *args, **kwargs):
        form = self.get_form()
        form.is_valid()
        return self.render_to_response(self.get_context_data(form=form))

    def get_context_data(self, **kwargs):
        kontext: dict[str, Any] = super().get_context_data(**kwargs) | {"ort": self.ort}
        form = kontext["form"]
        bereich = form.cleaned_data.get("bereich") if hasattr(form, "cleaned_data") else None
        if bereich is not None:
            zeilen = vorschau_kopie(self.ort, bereich)
            kontext |= {
                "bereich": bereich,
                "vorschau": zeilen,
                "anzahl_kopiert": sum(1 for zeile in zeilen if zeile.wird_kopiert),
                "anzahl_ausgelassen": sum(1 for zeile in zeilen if not zeile.wird_kopiert),
            }
        return kontext

    def form_valid(self, form):
        daten = form.cleaned_data
        bericht = kopieren_ort(self.ort, daten["bereich"], daten["name"])
        angaben = {
            "ort": self.ort.name,
            "neu": bericht.neu.name,
            "bereich": bericht.neu.bereich.name,
            "haus": bericht.neu.haus.name,
            "kopiert": bericht.kopiert,
        }
        messages.success(
            self.request,
            _(
                "„%(ort)s“ ist kopiert: „%(neu)s“ steht in %(haus)s im Bereich %(bereich)s, "
                "mit %(kopiert)s Artikeln."
            )
            % angaben,
        )
        if bericht.ausgelassen:
            messages.warning(
                self.request,
                _("%(haus)s führt diese Artikel nicht, sie fehlen in der Kopie: %(artikel)s.")
                % {"haus": bericht.neu.haus.name, "artikel": ", ".join(bericht.ausgelassen)},
            )
        return redirect(bericht.neu.get_absolute_url())


#: What a button of the selection says it wants ('aktion').
AUFNEHMEN = "aufnehmen"
ENTFERNEN = "entfernen"


class ErwartungUmschaltenView(Zeilenaktion):
    """Takes an article into the counting list of a location or out of it.

    Meant for the selection tab, where the decision is due row by row. New
    expectations attach themselves at the back. Where they stand in the
    counting order is determined in the first tab.

    **The button says what it wants** ('aktion'), and only that happens.
    Until 10/2026 the same button only switched: pressed once more from a
    stale page (a second tab, a send repeated after the network failed), it
    took the article out again, with its packs and default quantities. Now a
    click that comes too late changes nothing and says how things stand.
    """

    model = Ort

    def get_queryset(self):
        return Ort.objects.select_related("bereich__haus")

    def standardziel(self, satz: Ort) -> str:
        return reverse("zaehlliste", kwargs={"ort_pk": satz.pk})

    def ausfuehren(self, satz: Ort) -> None:
        request, ort = self.request, satz
        nummer = ganzzahl(request.POST.get("artikel"))
        aktion = request.POST.get("aktion", "")
        if nummer is None:
            messages.error(request, _("Unbekannter Artikel."))
        elif aktion == AUFNEHMEN:
            self.aufnehmen(ort, nummer)
        elif aktion == ENTFERNEN:
            self.entfernen(ort, nummer)
        else:
            messages.error(request, _("Unbekannte Aktion."))

    def entfernen(self, ort: Ort, nummer: int) -> None:
        request = self.request
        vorhanden = Erwartung.objects.filter(ort=ort, artikel_id=nummer)
        if vorhanden.exists():
            vorhanden.delete()
            messages.success(
                request, _("Aus der Zählliste von %(ort)s genommen.") % {"ort": ort.name}
            )
            return
        if (artikel := Artikel.objects.filter(pk=nummer).first()) is None:
            messages.error(request, _("Unbekannter Artikel."))
            return
        messages.info(
            request,
            _("„%(artikel)s“ steht nicht mehr in der Zählliste von %(ort)s.")
            % {"artikel": artikel.name, "ort": ort.name},
        )

    @transaction.atomic
    def aufnehmen(self, ort: Ort, nummer: int) -> None:
        """Takes the article in, unless it stands there already.

        The location is locked before the look, as a new row of the app
        locks it: two clicks at the same moment come one after the other,
        and the second finds the first. Until 10/2026 both found nothing, and
        the second ended at the unique index on the error page. 'FOR NO KEY
        UPDATE', by number, without 'Meta.ordering', which joins area and house.
        """
        list(
            Ort.objects.select_for_update(no_key=True)
            .filter(pk=ort.pk)
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        request = self.request
        vorhanden = Erwartung.objects.filter(ort=ort, artikel_id=nummer).select_related("artikel")
        if (schon := vorhanden.first()) is not None:
            messages.info(
                request,
                _("„%(artikel)s“ steht schon in der Zählliste von %(ort)s.")
                % {"artikel": schon.artikel.name, "ort": ort.name},
            )
            return

        # Only what can occur here at all. A recipe article without its
        # ingredients could not be resolved later.
        artikel = waehlbare_artikel(ort).filter(pk=nummer).first()
        if artikel is None:
            messages.error(request, _("Dieser Artikel lässt sich an diesem Ort nicht erwarten."))
            return

        # Without the scale, unless it is the only way to count the article.
        # The active packs come ticked: without one the article would get no
        # row here. Until 10/2026 it stood there without, and nothing said so.
        # Whatever is not counted here is unticked in the counting list.
        aktive = (
            konkret.ware.gebinde.filter(aktiv=True)
            if isinstance(konkret := artikel.konkret(), Hausartikel)
            else []
        )
        Erwartung.anlegen(
            ort,
            artikel,
            wiegen=nur_die_waage(artikel),
            vorgaben=[(gebinde.pk, None) for gebinde in aktive],
        )
        messages.success(
            request,
            _("„%(artikel)s“ wird jetzt an %(ort)s erwartet.")
            % {"artikel": artikel.name, "ort": ort.name},
        )
