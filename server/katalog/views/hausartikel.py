"""The house articles of a house: list, detail page, maintenance and the status button."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext_lazy as _
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
)

from django_project.deletion import GeschuetztesLoeschen
from django_project.labels import Beschriftung
from django_project.mixin_bases import ModellformularAnsichtBasis
from django_project.navigation import HAEUSER, PfadMixin, Station
from django_project.views import (
    GegliedertOhneSeiten,
    Zeilenaktion,
    sicheres_ziel,
)
from organisation.models import Bereich, Haus

from ..forms import (
    WIEDER_AKTIVIEREN,
    HausartikelFilterForm,
    HausartikelForm,
    NeueGebindeFormSet,
    OrtErwartungFormSet,
    WareEinstellungenForm,
)
from ..models import (
    GANZE_WARE,
    GEFUEHRT,
    GELTUNG_FEHLT,
    Artikelstatus,
    Behaelter,
    Erwartung,
    Gebinde,
    Hausartikel,
    Inventurart,
    Ware,
    geltung_fehlt,
    menge_kurz,
)


def mit_weiteren_artikeln(queryset):
    """Per row how many further carried articles its product has, in every house.

    The type menu of the row asks for those, as the form does: for this
    article only or for the whole product. A subquery instead of a join,
    which would multiply the rows.
    """
    weitere = (
        Hausartikel.objects.filter(ware=OuterRef("ware"), status__in=GEFUEHRT)
        .exclude(pk=OuterRef("pk"))
        .order_by()
        .values("ware")
        .annotate(anzahl=Count("pk"))
        .values("anzahl")
    )
    return queryset.annotate(
        weitere_artikel=Coalesce(Subquery(weitere, output_field=IntegerField()), 0)
    )


class HausartikelListView(GegliedertOhneSeiten, ListView):
    """What a single house carries.

    Deliberately per house: a cross-house list of house articles would be
    full of duplicates of the same product. Whoever searches across the
    houses takes the product list.
    """

    model = Hausartikel
    template_name = "katalog/hausartikel_list.html"
    context_object_name = "hausartikel"
    paginate_by = 50

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.haus = get_object_or_404(Haus, pk=kwargs["haus_pk"])

    def get_queryset(self):
        self.filterform = HausartikelFilterForm(self.request.GET, haus=self.haus)
        basis = (
            Hausartikel.objects.filter(haus=self.haus)
            .select_related(
                "basiseinheit",
                "warengruppe",
                "inventurart",
                "ware__basiseinheit",
                "ware__warenfamilie",
            )
            # The weighing column asks for the product's containers.
            .prefetch_related("ware__behaelter")
        )
        return mit_weiteren_artikeln(self.filterform.anwenden(basis))

    def get_context_data(self, **kwargs):
        kontext = super().get_context_data(**kwargs) | {
            "haus": self.haus,
            "filterform": self.filterform,
            "spalten": self.filterform.spalten.kopf(),
            "gruppen": self.filterform.gruppen(self.object_list),
            # Once for all rows: the menu of every row offers them.
            "inventurarten": list(Inventurart.objects.all()),
        }
        return kontext


class HausartikelDetailView(PfadMixin, DetailView):
    model = Hausartikel
    template_name = "katalog/hausartikel_detail.html"
    context_object_name = "hausartikel"

    def standard_pfad(self):
        haus = self.object.haus
        return [HAEUSER, Station(haus.name, haus.get_absolute_url())]

    def pfad_kennung(self):
        return f"hausartikel:{self.object.pk}"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related(
                "haus",
                "basiseinheit",
                "warengruppe",
                "inventurart",
                "ware__basiseinheit",
                "zaehlt_als",
            )
            .prefetch_related(
                "ware__gebinde",
                "ware__behaelter",
                "erwartet_an__ort",
                "dubletten",
            )
        )

    def get_context_data(self, **kwargs):
        """The containers that can be assigned to the product, including the current choice.

        "Without container" is available when counting anyway and therefore does
        not belong in the box list.
        """
        ware = self.object.ware
        gewaehlt = {behaelter.pk for behaelter in ware.behaelter.all()}
        return super().get_context_data(**kwargs) | {
            "behaelterwahl": [
                (behaelter, behaelter.pk in gewaehlt) for behaelter in Behaelter.zuweisbar()
            ],
            "ortswahl": self.ortswahl(),
            "inventurarten": list(Inventurart.objects.all()),
            "weitere_artikel": self.object.weitere_gefuehrte().count(),
        }

    def ortswahl(self):
        """The locations of the house, per area, with the position in the counting list.

        The other side of the selection tab of the counting list: there one
        chooses the articles for a location, here the locations for an article.
        Both switch the same expectation, only from the other direction. That is
        why the existing toggle endpoint suffices.
        """
        stellung = {
            erwartung.ort_id: erwartung
            for erwartung in Erwartung.objects.filter(artikel=self.object.artikel_ptr)
        }
        bereiche = (
            Bereich.objects.filter(haus=self.object.haus_id)
            .prefetch_related("orte")
            .order_by("name")
        )
        return [
            (bereich, [(ort, stellung.get(ort.pk)) for ort in bereich.orte.all()])
            for bereich in bereiche
        ]


def gewaehlte_ware(form) -> Ware | None:
    """The product chosen in the form, also where the form refused it for another reason.

    A rejected product (one chosen and a new one described) drops out of
    'cleaned_data'. The rows below then judged as for a new product: a
    weighable one asked for a pack at every ticked location, and the ticks
    on its packs became unknown choices (until 10/2026). What does not even
    name a product of the list counts as a new one.
    """
    if "ware" in form.cleaned_data:
        return form.cleaned_data["ware"]
    try:
        return form.fields["ware"].clean(form["ware"].data)
    except ValidationError:
        return None


class NeueGebindeMixin(ModellformularAnsichtBasis):
    """Packs that arise next to the house article.

    They hang on the product and thus apply to all houses. That is why the
    same list is shown when creating as when editing.

    A row that lands on an **inactive** pack of the product asks first
    ('NeuesGebindeForm', decided 2026-10-19), and until it is answered nothing
    is saved. Until 10/2026 the row silently took the inactive pack: a location
    that named it got no row in new inventories, and when editing the typed
    pack simply stayed inactive.
    """

    def gebinde_formset(self, daten=None, ware: Ware | None = None):
        return NeueGebindeFormSet(daten, prefix="gebinde", ware=ware)

    @staticmethod
    def ware_der_gebinde(form) -> Ware | None:
        """The product whose packs a typed row can hit, as far as packs arise here at all.

        None for a new product, which has none yet, and for one in "Not
        countable": its article gets no packs from this page ('form_valid').
        """
        ware = gewaehlte_ware(form)
        if ware is None or form.status_danach({"ware": ware}) == Artikelstatus.IGNORIERT:
            return None
        return ware

    def frage_melden(self, gebinde_formset) -> None:
        """Says at the top that a pack row further down waits for an answer.

        The page is long and comes back looking as before. Without the note
        one would not see why nothing was saved.
        """
        if gebinde_formset.fragen_offen:
            messages.warning(
                self.request,
                _(
                    "Noch nicht gespeichert: Unter „Gebinde“ wartet eine Frage zu einem "
                    "inaktiven Gebinde."
                ),
            )

    def gebinde_anlegen(
        self, ware: Ware, zeilen, entscheidungen: dict[int, str] | None = None
    ) -> tuple[dict[str, Gebinde], int]:
        """Creates the new packs. Returned are every name with its pack and how many arose.

        An existing name stays as it is. Under another quantity that is
        reported: until 10/2026 the new pack vanished silently, and its
        locations hung on the old one. An already taken quantity is not
        created a second time (that would be the same crate under a second
        name) and is reported too. A location that named it gets the pack
        that holds the quantity. Until 10/2026 it got none and stood in the
        counting list without a row.

        An inactive pack is switched on or left as it is, as its row answered
        ('entscheidungen', per pack). Either way a message says so, once per
        pack: two rows can lead to the same one, by name and by quantity.
        """
        gebinde = {satz.name: satz for satz in ware.gebinde.all()}
        beantwortet: set[int] = set()
        neu = 0
        for name, faktor in zeilen:
            vorhanden = Gebinde.vorhandenes(ware, name, faktor)
            if vorhanden is None:
                gebinde[name] = Gebinde.objects.create(
                    ware=ware, name=name, faktor_in_basiseinheit=faktor
                )
                neu += 1
                continue
            if vorhanden.name != name:
                messages.warning(
                    self.request,
                    _(
                        "„%(belegt)s“ steht bei „%(ware)s“ schon für diese Menge. "
                        "„%(name)s“ wurde nicht angelegt."
                    )
                    % {"belegt": vorhanden.name, "ware": ware.name, "name": name},
                )
            elif vorhanden.faktor_in_basiseinheit != faktor:
                messages.warning(
                    self.request,
                    _(
                        "„%(name)s“ steht bei „%(ware)s“ schon für %(menge)s %(einheit)s. "
                        "Ein zweites Gebinde mit diesem Namen wurde nicht angelegt."
                    )
                    % {
                        "name": name,
                        "ware": ware.name,
                        "menge": menge_kurz(vorhanden.faktor_in_basiseinheit),
                        "einheit": ware.basiseinheit.kurz,
                    },
                )
            if not vorhanden.aktiv and vorhanden.pk not in beantwortet:
                self._inaktives_beantworten(vorhanden, (entscheidungen or {}).get(vorhanden.pk))
                beantwortet.add(vorhanden.pk)
            gebinde[name] = vorhanden
        return gebinde, neu

    def _inaktives_beantworten(self, gebinde: Gebinde, antwort: str | None) -> None:
        """Brings the pack back the way the button on the product's page does, or says it stays.

        Without an answer it stays. That is only the case if the pack went
        inactive between the check and the saving. The note then says it too.
        """
        if antwort == WIEDER_AKTIVIEREN:
            gebinde.aktiv_setzen(True)
            messages.success(
                self.request,
                _("Gebinde „%(satz)s“ ist jetzt %(inaktiv)s.")
                % {"satz": gebinde.anzeigename, "inaktiv": _("aktiv")},
            )
            return
        messages.warning(
            self.request,
            _("„%(gebinde)s“ bleibt inaktiv. Seine Vorgaben gelten erst, wenn es wieder aktiv ist.")
            % {"gebinde": gebinde.anzeigename},
        )


class HausartikelCreateView(NeueGebindeMixin, CreateView):
    """Always created from a house: the house comes from the URL.

    On the same page, if needed, the product, its packs and the expectations
    at the locations of this house arise too. Packs and locations are linked
    via the pack *name*, not via primary keys: when filling in, the packs do
    not exist yet.
    """

    model = Hausartikel
    form_class = HausartikelForm
    template_name = "katalog/hausartikel_form.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.haus = get_object_or_404(Haus, pk=kwargs["haus_pk"])

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"haus": self.haus}

    def get_initial(self):
        """Number and designation from the address. The target stock clarification sends them along.

        There stands a number the house does not know. Whoever creates it should
        not have to type it.
        """
        vorbelegt = super().get_initial()
        for feld in ("artikelnummer", "name"):
            if wert := self.request.GET.get(feld, "").strip():
                vorbelegt[feld] = wert[:200]
        return vorbelegt

    def get_context_data(self, **kwargs):
        kontext = super().get_context_data(**kwargs) | {"haus": self.haus, "weiter": self.weiter()}
        kontext.setdefault("gebinde_formset", self.gebinde_formset())
        kontext.setdefault("orte_formset", self._orte_formset())
        return kontext

    def weiter(self) -> str:
        """Where the link came from: the target stock clarification sends its own address.

        After creating, the way leads back there to "Re-assign" (until 10/2026 it
        ended on the page of the new article). The form carries it along.
        """
        ziel = self.request.POST.get("weiter") or self.request.GET.get("weiter") or ""
        return sicheres_ziel(self.request, ziel)

    def get_success_url(self):
        return self.weiter() or super().get_success_url()

    def _orte_formset(
        self,
        daten=None,
        gebindenamen=(),
        *,
        braucht_gebinde=False,
        inaktive=(),
        inaktiv_bleibend=(),
    ):
        return OrtErwartungFormSet(
            daten,
            prefix="orte",
            haus=self.haus,
            gebindenamen=gebindenamen,
            braucht_gebinde=braucht_gebinde,
            inaktive=inaktive,
            inaktiv_bleibend=inaktiv_bleibend,
        )

    def _gesendete_formsets(self, form):
        """The pack rows and the location rows as sent, both validated.

        The selection of the location rows is only fixed once the pack rows
        are read. Only then is it known which names there will be.
        """
        gebinde_formset = self.gebinde_formset(self.request.POST, self.ware_der_gebinde(form))
        gebinde_formset.is_valid()
        orte_formset = self._orte_formset(
            self.request.POST,
            self._gebindenamen(form, gebinde_formset),
            braucht_gebinde=self._braucht_gebinde(form),
            inaktive=gebinde_formset.inaktive_namen,
            inaktiv_bleibend=gebinde_formset.inaktiv_bleibende_namen,
        )
        orte_formset.is_valid()
        return gebinde_formset, orte_formset

    def _zurueck_zur_seite(self, form, gebinde_formset, orte_formset):
        self.frage_melden(gebinde_formset)
        return self.render_to_response(
            self.get_context_data(
                form=form, gebinde_formset=gebinde_formset, orte_formset=orte_formset
            )
        )

    def form_invalid(self, form):
        """The rows further down keep what was typed and ticked.

        Until 10/2026 a rejected main form (a missing name, say) showed them
        empty again: the typed packs, the ticks at the locations and the
        answer about an inactive pack had to be entered anew.
        """
        return self._zurueck_zur_seite(form, *self._gesendete_formsets(form))

    def form_valid(self, form):
        gebinde_formset, orte_formset = self._gesendete_formsets(form)
        if not (gebinde_formset.is_valid() and orte_formset.is_valid()):
            return self._zurueck_zur_seite(form, gebinde_formset, orte_formset)

        with transaction.atomic():
            artikel: Hausartikel = form.save()
            self.object = artikel
            # "Not countable" has no packs and is expected nowhere.
            neue, anzahl = 0, 0
            if artikel.status != Artikelstatus.IGNORIERT:
                gebinde, neue = self.gebinde_anlegen(
                    artikel.ware, gebinde_formset.gebinde, gebinde_formset.entscheidungen
                )
                anzahl = self._erwartungen_anlegen(
                    artikel,
                    orte_formset.erwartungen,
                    gebinde,
                    inaktiv_bleibend=gebinde_formset.inaktiv_bleibende_namen,
                )

        # Only the packs that arose here. Until 10/2026 the existing ones were
        # counted along, and a pack that was not created seemed to be there.
        messages.success(
            self.request,
            _(
                "„%(artikel)s“ angelegt: %(gebinde)s neue Gebinde an der Ware „%(ware)s“, "
                "erwartet an %(anzahl)s Ort(en)."
            )
            % {
                "artikel": artikel.name,
                "gebinde": neue,
                "ware": artikel.ware.name,
                "anzahl": anzahl,
            },
        )
        return redirect(self.get_success_url())

    @staticmethod
    def _braucht_gebinde(form) -> bool:
        """Whether a ticked location needs a pack, see 'OrtErwartungForm.clean'.

        A new product is not weighable. A weighable one goes on the scale where
        no pack is ticked. "Not countable" is expected nowhere, its ticks fall away.
        """
        ware = gewaehlte_ware(form)
        return ware is None or not (ware.abwiegbar or ware.ist_nicht_zaehlbar)

    @staticmethod
    def _gebindenamen(form, gebinde_formset) -> list[str]:
        """All packs available to the location selection: the product's active ones, the typed ones.

        The typed ones also while the pack rows do not hold up yet, say while
        a row asks about an inactive pack. A tick on them then survives the way
        back to the page. Until 10/2026 it was lost, and the location reported
        an unknown choice.
        """
        vorhanden = []
        if ware := gewaehlte_ware(form):
            vorhanden = list(ware.gebinde.filter(aktiv=True).values_list("name", flat=True))
        neue = [name for name, _ in gebinde_formset.gebinde]
        return list(dict.fromkeys([*vorhanden, *neue]))

    @staticmethod
    def _erwartungen_anlegen(
        artikel: Hausartikel, erwartungen, gebinde: dict[str, Gebinde], *, inaktiv_bleibend=()
    ) -> int:
        for ort, namen in erwartungen:
            # A location ticked without a pack: for weighable goods that
            # leaves the scale, otherwise no row would arise there. The form
            # rejects it for all others ('OrtErwartungForm.clean'). A pack
            # that stays inactive gives no row either, its default waits
            # ('Inventur.zaehlpositionen_anlegen'). Two names can lead to the
            # same pack: a taken quantity gets the pack that holds it
            # ('gebinde_anlegen'). 'anlegen' keeps one default.
            zaehlbar = [name for name in namen if name not in inaktiv_bleibend]
            Erwartung.anlegen(
                ort,
                artikel,
                wiegen=not zaehlbar,
                vorgaben=[(gebinde[name].pk, None) for name in namen if name in gebinde],
            )
        return len(erwartungen)


class _EinstellungenUngueltig(Exception):
    def __init__(self, einstellungen: WareEinstellungenForm):
        self.einstellungen = einstellungen
        super().__init__("Einstellungen der Ware ungültig")


class HausartikelUpdateView(NeueGebindeMixin, UpdateView):
    """The house article, and on the same page the settings of its product.

    The settings ('WareEinstellungenForm') belong to the product the article
    has **when the page opens**. They count only if that product stays, if
    the page sent them along and if something in them changed:

    - Whoever chooses another product means that one. The settings shown
      belonged to the previous one, and the script hides them.
    - A call past the page brings no settings. An absent tick would otherwise
      switch off the weighing.
    - A product that does not meet its own rules today (weighing switched on
      through the button, the weight per unit still missing) must not stand in
      the way of a correction to the house article.
    """

    model = Hausartikel
    form_class = HausartikelForm
    template_name = "katalog/hausartikel_form.html"

    #: The prefix of the product's settings in the form.
    EINSTELLUNGEN = "einstellungen"

    #: The product as stored, remembered by 'get_object'.
    bisherige_ware_id: int

    def get_queryset(self):
        return super().get_queryset().select_related("haus", "ware")

    def get_object(self, queryset=None):
        """Remembers the stored product. The form hangs the chosen one onto the article."""
        artikel = super().get_object(queryset)
        self.bisherige_ware_id = artikel.ware_id
        return artikel

    def einstellungen(self, daten=None) -> WareEinstellungenForm:
        """Always on a freshly read product, not on the one hanging on the article.

        The form of the house article has touched that one while validating.
        """
        ware = Ware.objects.select_related("basiseinheit", "warenfamilie").get(
            pk=self.bisherige_ware_id
        )
        return WareEinstellungenForm(daten, instance=ware, prefix=self.EINSTELLUNGEN)

    def einstellungen_gelten(self, form) -> bool:
        """The product stays, and the page has sent its settings along.

        The name is a text field: the browser sends it even when it is empty.
        """
        gewaehlt = form.cleaned_data.get("ware") if hasattr(form, "cleaned_data") else None
        return (
            gewaehlt is not None
            and gewaehlt.pk == self.bisherige_ware_id
            and f"{self.EINSTELLUNGEN}-name" in self.request.POST
        )

    def get_context_data(self, **kwargs):
        kontext = super().get_context_data(**kwargs) | {"haus": self.object.haus}
        kontext.setdefault("gebinde_formset", self.gebinde_formset())
        if "einstellungen" not in kontext:
            # After a rejected attempt what was typed stays in the fields.
            gelten = self.einstellungen_gelten(kontext["form"])
            kontext["einstellungen"] = self.einstellungen(self.request.POST if gelten else None)
        return kontext | {"andere_haeuser": self.andere_haeuser()}

    def andere_haeuser(self) -> list[str]:
        """Who else carries the product: there a change to its settings acts too."""
        return list(
            Haus.objects.filter(
                hausartikel__ware=self.bisherige_ware_id, hausartikel__status__in=GEFUEHRT
            )
            .exclude(pk=self.object.haus_id)
            .distinct()
            .order_by("name")
            .values_list("name", flat=True)
        )

    def _zurueck_mit_gebinde(self, form, gebinde_formset):
        self.frage_melden(gebinde_formset)
        return self.render_to_response(
            self.get_context_data(form=form, gebinde_formset=gebinde_formset)
        )

    def form_invalid(self, form):
        """The typed pack rows stay, as on the page for creating ('HausartikelCreateView')."""
        gebinde_formset = self.gebinde_formset(self.request.POST, self.ware_der_gebinde(form))
        gebinde_formset.is_valid()
        return self._zurueck_mit_gebinde(form, gebinde_formset)

    def form_valid(self, form):
        """New packs land on the product the article has afterwards.

        Save first, then create: if the product is changed here at the same
        time, the new packs belong to the new one.

        The settings of the product are validated only **after** the house
        article is saved: the product's rules look at its house articles
        (into "Not countable" and back), and this one has just changed. If they
        do not hold, the transaction takes the house article back.

        A typed pack that the product carries inactive asks first, as when
        creating. Until it is answered the article stays as it was too.
        """
        gebinde_formset = self.gebinde_formset(self.request.POST, self.ware_der_gebinde(form))
        if not gebinde_formset.is_valid():
            return self._zurueck_mit_gebinde(form, gebinde_formset)

        gelten = self.einstellungen_gelten(form)
        try:
            with transaction.atomic():
                self.object = form.save()
                if gelten:
                    self.einstellungen_speichern()
                if self.object.status != Artikelstatus.IGNORIERT:
                    self.gebinde_anlegen(
                        self.object.ware, gebinde_formset.gebinde, gebinde_formset.entscheidungen
                    )
        except _EinstellungenUngueltig as fehler:
            return self.render_to_response(
                self.get_context_data(
                    form=form,
                    gebinde_formset=gebinde_formset,
                    einstellungen=fehler.einstellungen,
                )
            )
        except ValidationError as fehler:
            # What only 'save()' can see: a product that arises only now
            # ("new product") and leaves a frozen result a factor its column
            # cannot hold. The transaction took everything back, the new
            # product too. Until 10/2026 this ended on the error page. A field
            # the form does not carry speaks above it.
            if hasattr(fehler, "error_dict"):
                for feld, meldungen in fehler.message_dict.items():
                    form.add_error(feld if feld in form.fields else None, meldungen)
            else:
                form.add_error(None, fehler)
            return self._zurueck_mit_gebinde(form, gebinde_formset)
        return redirect(self.get_success_url())

    def einstellungen_speichern(self) -> None:
        einstellungen = self.einstellungen(self.request.POST)
        if not einstellungen.has_changed():
            return
        if not einstellungen.is_valid():
            raise _EinstellungenUngueltig(einstellungen)
        ware = einstellungen.save()
        # The family may have changed, and with it the status of this article.
        self.object.refresh_from_db()
        messages.success(
            self.request,
            _("Die Einstellungen der Ware „%(ware)s“ sind geändert. Sie gelten in jedem Haus.")
            % {"ware": ware.name},
        )


class HausartikelInventurartView(Zeilenaktion):
    """Puts a house article into another inventory type, alone or with its whole product.

    The menu asks the same question as the form: "only this article" or
    "all articles of the product". Without the answer nothing changes where
    the product has further articles. Either way would decide unasked.
    """

    model = Hausartikel

    def ausfuehren(self, satz: Hausartikel) -> None:
        art = Inventurart.objects.filter(pk=self.request.POST.get("inventurart") or None).first()
        if art is None:
            messages.error(self.request, _("Unbekannte Inventurart."))
            return
        geltung = self.request.POST.get("geltung", "")
        weitere = satz.weitere_gefuehrte().exists()
        if geltung_fehlt(weitere, geltung):
            messages.error(self.request, GELTUNG_FEHLT)
            return
        ganze_ware = weitere and geltung == GANZE_WARE
        if not satz.inventurart_setzen(art, ganze_ware=ganze_ware):
            return
        if ganze_ware:
            text = _("Alle Artikel der Ware „%(ware)s“ zählen jetzt in „%(art)s“, in jedem Haus.")
        else:
            text = _("„%(satz)s“ zählt jetzt in „%(art)s“.")
        messages.success(
            self.request, text % {"satz": satz.name, "ware": satz.ware.name, "art": art.name}
        )


class HausartikelStatusView(Zeilenaktion):
    """Sets a house article to active, inactive or discontinued, or ignores it.

    One endpoint for all four, because they stand side by side in the list
    as one button row: whoever goes through the inactive articles of a house
    decides per row between them, not between two pages. What does not work
    (a number another row occupies) comes back as a message instead of an
    error page.
    """

    model = Hausartikel

    MELDUNGEN: dict[str, Beschriftung] = {
        Artikelstatus.AKTIV: _("ist aktiv."),
        Artikelstatus.INAKTIV: _(
            "ist inaktiv. Er bleibt in den Zähllisten und lässt sich in der App weiter zählen."
        ),
        Artikelstatus.EINGESTELLT: _(
            "ist eingestellt. Nummer und Ware sind im Haus frei. Alte Inventurergebnisse "
            "zeigen weiter auf diesen Satz."
        ),
        Artikelstatus.IGNORIERT: _(
            "ist nicht zählbar: seine Ware gehört jetzt zu „Nicht zählbar“, in jedem Haus."
        ),
    }

    def ausfuehren(self, satz: Hausartikel) -> None:
        status = self.request.POST.get("status", "")
        if status not in Artikelstatus.values:
            messages.error(self.request, _("Unbekannter Status."))
            return
        if status == satz.status:
            return

        if status == Artikelstatus.IGNORIERT:
            satz.ignorieren()
        else:
            satz.status_setzen(status)
        messages.success(
            self.request,
            _("%(artikelnummer)s „%(satz)s“ %(status)s")
            % {
                "artikelnummer": satz.artikelnummer,
                "satz": satz.name,
                "status": self.MELDUNGEN[status],
            },
        )


class HausartikelDeleteView(GeschuetztesLoeschen, DeleteView):
    """What occurred in an inventory stays: results, target stocks and rows point to it."""

    model = Hausartikel
    template_name = "katalog/hausartikel_delete.html"
    # The app may be creating a row for it just now.
    zuerst_sperren = True
    schutz_meldung = _(
        "„{name}“ lässt sich nicht löschen. Daran hängen noch {woran}. Stattdessen "
        "einstellen: der Satz bleibt für alte Ergebnisse stehen, Nummer und Ware werden frei."
    )

    def get_queryset(self):
        return super().get_queryset().select_related("haus")

    def get_success_url(self):
        return self.object.haus.get_absolute_url()
