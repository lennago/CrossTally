"""Products and their packs: list, detail page, maintenance, splitting, merging."""

from copy import copy
from decimal import Decimal
from http import HTTPStatus

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Min, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext, pgettext_lazy
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    FormView,
    ListView,
    UpdateView,
)

from django_project.deletion import GeschuetztesLoeschen
from django_project.navigation import PfadMixin, liste
from django_project.validation import ganzzahl
from django_project.views import (
    GegliedertOhneSeiten,
    InlineFormsetMixin,
    Zeilenaktion,
)

from ..forms import (
    DublettenForm,
    GebindeAufloesenForm,
    GebindeFormSet,
    HausartikelZusammenfuehrenForm,
    WareAufteilenForm,
    WareFilterForm,
    WareForm,
    WareZusammenfuehrenForm,
)
from ..models import (
    SICHTBAR,
    Artikelstatus,
    Behaelter,
    Dimension,
    Gebinde,
    Hausartikel,
    Inventurart,
    Ware,
    artikel_sperren,
    menge_kurz,
    mit_statuszahlen,
)
from ..ware_merge import Bericht, dubletten, gebindepaare, zusammenfuehren
from ..ware_split import aufteilen as aufteilen_ware
from ..ware_unit_change import FAKTOR_STELLEN, mengen_umrechnen
from .base import SammelstatusView
from .hausartikel import mit_weiteren_artikeln
from .sorting import SortierenView
from .split_merge import VORIGE_WAHL, AufteilenView


class WareListView(GegliedertOhneSeiten, ListView):
    """The entry into the article master, the search lies here.

    The search goes over the product's designation and over its house
    articles, so an article number from the inventory management system leads
    to the target too.
    """

    model = Ware
    template_name = "katalog/ware_list.html"
    context_object_name = "ware"
    paginate_by = 50

    def get_queryset(self):
        self.filterform = WareFilterForm(self.request.GET)
        basis = (
            Ware.objects.select_related("basiseinheit", "warenfamilie")
            .prefetch_related(
                "gebinde", "hausartikel__haus", "hausartikel__inventurart", "behaelter"
            )
            # Sorted by the first type of its articles. A mixed product stands
            # with the first of its types.
            .annotate(
                art_folge=Min(
                    "hausartikel__inventurart__reihenfolge",
                    filter=Q(hausartikel__status__in=SICHTBAR),
                )
            )
        )
        # The numbers count 'distinct': the filters join over the house articles,
        # and a row would otherwise run into the count several times.
        return mit_statuszahlen(self.filterform.anwenden(basis))

    def get_context_data(self, **kwargs):
        kontext = super().get_context_data(**kwargs) | {
            "filterform": self.filterform,
            "spalten": self.filterform.spalten.kopf(),
            # Products without family come last, because empty values sort at the back.
            # Grouped means no paging, 'object_list' is then everything.
            "gruppen": self.filterform.gruppen(self.object_list),
            # Once for all rows: the menu of every row offers them.
            "inventurarten": list(Inventurart.objects.all()),
        }
        return kontext


class WareDetailView(PfadMixin, DetailView):
    model = Ware
    template_name = "katalog/ware_detail.html"
    context_object_name = "ware"

    def standard_pfad(self):
        return [liste(pgettext_lazy("Liste aller Waren", "Waren"), "ware_list")]

    def pfad_kennung(self):
        return f"ware:{self.object.pk}"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related("basiseinheit")
            .prefetch_related(
                Prefetch(
                    "hausartikel",
                    queryset=mit_weiteren_artikeln(
                        Hausartikel.objects.select_related("haus", "warengruppe", "inventurart")
                    ).order_by("haus__name", "artikelnummer"),
                ),
                "verwendet_in__rezepturartikel__basiseinheit",
            )
        )

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs) | {
            "inventurarten": list(Inventurart.objects.all()),
        }


class WareCreateView(InlineFormsetMixin, CreateView):
    model = Ware
    form_class = WareForm
    formset_classes = {"gebinde": GebindeFormSet}
    template_name = "katalog/ware_form.html"


class WareUpdateView(InlineFormsetMixin, UpdateView):
    model = Ware
    form_class = WareForm
    formset_classes = {"gebinde": GebindeFormSet}
    template_name = "katalog/ware_form.html"

    def form_valid(self, form):
        """Saved under a lock of the product's articles, as a new row of the app locks its own.

        A pack ticked for deletion is checked against the rows that hang on it
        ('BaseGebindeFormSet.clean'). With the articles locked first
        ('katalog.models.artikel_sperren'), a row the app created a moment ago
        is seen there, and one it creates afterwards waits and finds the pack
        gone. Until 10/2026 such a row landed on the pack being deleted, and
        its commit ended in a server error.
        """
        with transaction.atomic():
            artikel_sperren(self.object.hausartikel.values_list("pk", flat=True))
            return self._speichern(form)

    def _speichern(self, form):
        """If the unit changes, what is in it is converted, or nothing at all is saved.

        **After** the packs: what was on the page and sent along was meant in the
        previous unit, a newly typed pack too. If something cannot be converted
        (a pack would become too small, two would coincide), the transaction
        takes everything back, and the page says what it was.
        """
        if form.umrechnungsfaktor is None:
            vorher = self.object
            try:
                with transaction.atomic():
                    return super().form_valid(form)
            except ValidationError as fehler:
                # Saving asks once more under the rows' lock whether an article
                # is counted. A count that landed after the form's check refuses
                # "Not countable" there, and the page says so.
                self.object = vorher
                for meldung in fehler.messages:
                    form.add_error("warenfamilie", meldung)
                return self.form_invalid(form)
        # The packs sent along were meant in the previous unit: the formset
        # checks them against it ('get_formsets'), the conversion follows.
        self.bisherige_einheit = form.bisherige_einheit
        vorher = self.object
        try:
            with transaction.atomic():
                antwort = super().form_valid(form)
                if antwort.status_code != HTTPStatus.FOUND:
                    # A formset was invalid. The skeleton has already rolled back.
                    return antwort
                ehemals_einzeln = mengen_umrechnen(
                    self.object, form.umrechnungsfaktor, form.bisherige_einheit
                )
        except ValidationError as fehler:
            self.object = vorher
            for meldungen in fehler.message_dict.values():
                for meldung in meldungen:
                    form.add_error("basiseinheit", meldung)
            return self.form_invalid(form)
        self._umrechnung_melden(form, ehemals_einzeln)
        return antwort

    def get_formsets(self, instance=None, **kwargs):
        """During a unit change the packs are checked against the previous unit.

        The skeleton saves the product first, so the formset saw the new unit
        and refused the single unit ("only for goods counted in pieces")
        before the conversion could turn it into a pack: over the page the
        change from pieces to kg failed for every product with a single unit
        (until 10/2026). A copy carries the old unit, the rows are written to
        the same product all the same.
        """
        einheit = getattr(self, "bisherige_einheit", None)
        if instance is not None and einheit is not None and "data" in kwargs:
            instance = copy(instance)
            instance.basiseinheit = einheit
        return super().get_formsets(instance=instance, **kwargs)

    def _umrechnung_melden(self, form, ehemals_einzeln) -> None:
        """Says what the unit change did, and which single units became packs."""
        messages.success(
            self.request,
            _(
                "„%(object)s“ wird jetzt in %(basiseinheit)s geführt. Gebinde, Rezepturen und "
                "die Umrechnung der Häuser sind nachgezogen (1 %(bisherige_einheit)s = "
                "%(umrechnungsfaktor)s %(basiseinheit2)s). Die Häuser zählen weiter in ihrer "
                "Einheit."
            )
            % {
                "object": self.object.name,
                "basiseinheit": self.object.basiseinheit.kurz,
                "bisherige_einheit": form.bisherige_einheit.kurz,
                # From the product's own conversion the factor comes in full
                # precision ('bekannter_faktor'). Shown as the columns hold it.
                "umrechnungsfaktor": menge_kurz(form.umrechnungsfaktor.quantize(FAKTOR_STELLEN)),
                "basiseinheit2": self.object.basiseinheit.kurz,
            },
        )
        for gebinde in ehemals_einzeln:
            messages.info(
                self.request,
                _(
                    "Ein loses Einzelstück gibt es nur bei Stück. Es ist jetzt das Gebinde "
                    "„%(name)s“ mit %(menge)s %(einheit)s."
                )
                % {
                    "name": gebinde.name,
                    "menge": menge_kurz(gebinde.faktor_in_basiseinheit),
                    "einheit": self.object.basiseinheit.kurz,
                },
            )


class WareDeleteView(GeschuetztesLoeschen, DeleteView):
    model = Ware
    template_name = "katalog/ware_delete.html"
    success_url = reverse_lazy("ware_list")
    schutz_meldung = _(
        "„{name}“ lässt sich nicht löschen. Daran hängen noch {woran}. Hausartikel "
        "lassen sich zusammenführen oder einstellen, Rezepturen am Rezepturartikel ändern."
    )


class WareZusammenfuehrenView(FormView):
    """Make two products one.

    The import creates separate products per house for deviating
    designations. Here they are united again.

    Always in **two steps**: first the preview, then its button. The preview
    shows what becomes of the packs. That both carry a "Flasche" is the normal
    case and needs no answer. Where the same name means another quantity or
    the same quantity carries two names, the preview asks. Likewise for a
    house that carries both: the row of the absorbed product is discontinued
    there, and the preview asks which of its expectations go over. Nothing is
    written beforehand.

    The preview names the choice it showed ('gezeigt', as 'VorschauView'
    does). The button writes only for that choice. Until 10/2026 the page
    wrote at once when nothing was contested, so a second enter in the search
    field merged for good, and a choice changed on the second page took the
    ticks of the first one: those belonged to other rows, and the
    expectations of the now discontinued article fell without a question.
    """

    form_class = WareZusammenfuehrenForm
    template_name = "katalog/ware_merge.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        #: From whose page the call comes. Whether it stays is only said by the form.
        self.artikel = self.ware_holen()

    def ware_holen(self) -> Ware:
        return get_object_or_404(Ware.objects.select_related("basiseinheit"), pk=self.kwargs["pk"])

    def weiter(self, ziel: Ware) -> str:
        """Where it goes afterwards: to the product that stays."""
        return ziel.get_absolute_url()

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"ziel": self.artikel}

    def get_context_data(self, **kwargs):
        # 'ziel' is whoever stays, until the choice of this page's product.
        return {"artikel": self.artikel, "ziel": self.artikel} | super().get_context_data(**kwargs)

    @staticmethod
    def signatur(quelle: Ware, ziel: Ware, faktor: Decimal) -> str:
        """What the preview showed, in one word for the hidden field 'gezeigt'.

        Who is absorbed, who stays, and at which factor: anything else that
        the second page could change follows from these three.
        """
        return f"{quelle.pk}:{ziel.pk}:{faktor}"

    def form_valid(self, form):
        """Two steps: first show what is to be decided, then write for exactly that choice.

        The first POST (or one with another choice than the preview showed)
        renders the preview with the questions about contested packs and
        duplicates. Only a POST that confirms the shown signature with its
        answers calls the service. A conversion that fails on the way back
        ends as a form error, nothing is written then.
        """
        quelle, ziel = form.rollen()
        paare = gebindepaare(quelle, ziel, form.faktor)
        strittig = [paar for paar in paare if paar.braucht_entscheidung]
        doppelt = dubletten(quelle, ziel)

        signatur = self.signatur(quelle, ziel, form.faktor)
        gebunden = self._gebunden(signatur)
        daten = self.request.POST if gebunden else None
        aufloesen = GebindeAufloesenForm(daten, paare=paare, ziel_gebinde=list(ziel.gebinde.all()))
        doppelte = DublettenForm(daten, dubletten=doppelt)
        offen = (
            not gebunden
            or (strittig and not aufloesen.is_valid())
            or (doppelt and not doppelte.is_valid())
        )
        if offen:
            return self._vorschau(
                form,
                signatur=signatur,
                aufloesen=aufloesen if strittig else None,
                doppelte=doppelte if doppelt else None,
                paare=paare,
                quelle=quelle,
                ziel=ziel,
            )

        name = quelle.name
        try:
            bericht = zusammenfuehren(
                quelle,
                ziel,
                namen=aufloesen.namen() if strittig else None,
                entscheidungen=aufloesen.entscheidungen() if strittig else None,
                faktor=form.faktor,
                uebernehmen=doppelte.uebernehmen() if doppelt else None,
                aktivieren=aufloesen.aktivieren() if strittig else frozenset(),
            )
        except ValidationError as fehler:
            # A pack that would become too small in the target's unit, or two
            # that would coincide: the conversion says what it was.
            for meldung in fehler.messages:
                form.add_error("umrechnung", meldung)
            return self.form_invalid(form)
        self._melden(name, ziel, bericht)
        return redirect(self.weiter(ziel))

    def _gebunden(self, signatur: str) -> bool:
        """Whether this POST confirms the very choice its preview showed.

        The preview sends 'bestaetigt' along, with the signature of the
        choice it showed. Only that choice is written. For another one, its
        own preview comes first: the answers on the page belong to the old one.
        """
        bestaetigt = self.request.POST.get("bestaetigt") == "1"
        gebunden = bestaetigt and self.request.POST.get("gezeigt") == signatur
        if bestaetigt and not gebunden:
            messages.info(self.request, VORIGE_WAHL)
        return gebunden

    def _vorschau(self, form, **kontext):
        """The first step: the page shows what is to be decided and writes nothing."""
        return self.render_to_response(self.get_context_data(form=form, vorschau=True, **kontext))

    def _melden(self, name: str, ziel: Ware, bericht: Bericht) -> None:
        """What the merge did. 'name' is the absorbed product's, which is gone by now."""
        messages.success(
            self.request,
            _(
                "„%(name)s“ wurde in „%(ziel)s“ aufgelöst: %(hausartikel)s Hausartikel und "
                "%(rezepturzeilen)s Rezepturzeile(n) umgehängt, %(verschmolzen)s Gebinde "
                "verschmolzen, %(umgezogen)s umgezogen."
            )
            % {
                "name": name,
                "ziel": ziel.name,
                "hausartikel": bericht.hausartikel,
                "rezepturzeilen": bericht.rezepturzeilen,
                "verschmolzen": bericht.verschmolzen,
                "umgezogen": bericht.umgezogen,
            },
        )
        for gebinde in bericht.reaktiviert:
            messages.success(
                self.request,
                _("Gebinde „%(satz)s“ ist jetzt %(inaktiv)s.")
                % {"satz": gebinde, "inaktiv": _("aktiv")},
            )
        for gebinde in bericht.inaktiv:
            messages.warning(
                self.request,
                _(
                    "„%(gebinde)s“ bleibt inaktiv. Seine Vorgaben gelten erst, wenn es wieder "
                    "aktiv ist."
                )
                % {"gebinde": gebinde},
            )
        if bericht.eingestellt:
            meldung = ngettext(
                "Ein Haus führte beide. Der Artikel von „%(name)s“ ist dort eingestellt und "
                "zählt als der bleibende. Übernommene Erwartungen: %(erwartungen)s.",
                "%(anzahl)s Häuser führten beide. Die Artikel von „%(name)s“ sind dort "
                "eingestellt und zählen als die bleibenden. Übernommene Erwartungen: "
                "%(erwartungen)s.",
                bericht.eingestellt,
            ) % {"anzahl": bericht.eingestellt, "name": name, "erwartungen": bericht.erwartungen}
            messages.info(self.request, meldung)


class HausartikelZusammenfuehrenView(WareZusammenfuehrenView):
    """The merge of the products, started from a house article.

    The other one is chosen among the articles of the house, and it leads
    back to the article afterwards. The article stays in any case: if its
    product is absorbed, it moves to the other one.
    """

    form_class = HausartikelZusammenfuehrenForm

    def ware_holen(self) -> Ware:
        #: The article from whose page the call comes.
        self.hausartikel = get_object_or_404(
            Hausartikel.objects.select_related("haus", "ware__basiseinheit"), pk=self.kwargs["pk"]
        )
        return self.hausartikel.ware

    def get_form_kwargs(self):
        return super().get_form_kwargs() | {"hausartikel": self.hausartikel}

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs) | {"hausartikel": self.hausartikel}

    def weiter(self, ziel: Ware) -> str:
        return self.hausartikel.get_absolute_url()


class WareAufteilenView(AufteilenView):
    """One product becomes two, with the house articles distributed onto both.

    The reverse direction of merging. Needed where it turns out that a house
    carries something different under the same designation than the others.
    """

    form_class = WareAufteilenForm
    template_name = "katalog/ware_split.html"
    queryset = Ware.objects.select_related("basiseinheit")
    kontextname = "ware"

    def aufteilen(self, name, neuer_name, wandern):
        return aufteilen_ware(self.satz, name, neuer_name, wandern)

    def melden(self, bericht):
        messages.success(
            self.request,
            _(
                "„%(bleibt)s“ ist aufgeteilt: %(gewandert)s Hausartikel gehören jetzt zu "
                "„%(neu)s“. Die neue Ware führt dieselben %(gebinde)s Gebinde. Was sie nicht "
                "braucht, lässt sich dort inaktiv setzen."
            )
            % {
                "bleibt": bericht.bleibt.name,
                "gewandert": bericht.gewandert,
                "neu": bericht.neu.name,
                "gebinde": bericht.gebinde,
            },
        )
        # The recipes stay with the previous product. Whether they still work
        # out in the houses of the new one only a look at the recipe article knows.
        rezepturen = (
            list(bericht.bleibt.verwendet_in.select_related("rezepturartikel"))
            if bericht.gewandert
            else []
        )
        if rezepturen:
            messages.warning(
                self.request,
                _(
                    "Die Rezepturen bleiben bei „%(bleibt)s“: %(namen)s. Bitte nachsehen, ob "
                    "diese Rezepturartikel in den Häusern von „%(neu)s“ noch auflösbar sind."
                )
                % {
                    "bleibt": bericht.bleibt.name,
                    "namen": ", ".join(f"„{zeile.rezepturartikel.name}“" for zeile in rezepturen),
                    "neu": bericht.neu.name,
                },
            )


class WareStatusView(SammelstatusView):
    """Into "Not countable" and back: ignored is the product, not a single row."""

    model = Ware
    #: No article status of its own: takes the product out of "Not countable".
    WIEDER_AUFNEHMEN = "WIEDER_AUFNEHMEN"

    MELDUNGEN = SammelstatusView.MELDUNGEN | {
        Artikelstatus.IGNORIERT: _("gehört jetzt zu „Nicht zählbar“, in jedem Haus."),
        WIEDER_AUFNEHMEN: _(
            "gehört nicht mehr zu „Nicht zählbar“. Familie und Status der Hausartikel sind "
            "zurück, ihre Plätze an der alten Stelle, soweit sie gemerkt waren und es sie "
            "noch gibt."
        ),
    }

    def umsetzen(self, ware, status: str) -> list[str]:
        if status == Artikelstatus.IGNORIERT:
            ware.ignorieren()
            return []
        if status == self.WIEDER_AUFNEHMEN:
            ware.wieder_aufnehmen()
            return []
        return super().umsetzen(ware, status)


class WareInventurartView(Zeilenaktion):
    """Puts every article of a product into one inventory type, in every house.

    The type hangs on the house article. Here it is set for all of them at
    once, as the product's form does. Meant for the list and the product's
    page, where the decision is due product by product.
    """

    model = Ware

    def ausfuehren(self, satz: Ware) -> None:
        art = Inventurart.objects.filter(pk=self.request.POST.get("inventurart") or None).first()
        if art is None:
            messages.error(self.request, _("Unbekannte Inventurart."))
            return
        # Otherwise nothing to report. The button was already pressed.
        if satz.inventurart_setzen(art):
            messages.success(
                self.request,
                _("„%(satz)s“ zählt jetzt in „%(art)s“, in jedem Haus.")
                % {"satz": satz.name, "art": art.name},
            )


class WareAbwiegenView(Zeilenaktion):
    """Switches weighing on or off for a product.

    Possible in every unit: the scale delivers grams, the rest is done by the
    product's conversion into grams. If that is still missing for volume or
    piece goods, the message says it has to be set. It is not blocked
    because of that, otherwise one would first have to fill in a form for a
    tick.

    When switching off, the assigned containers drop away. They are only
    available for weighing, and 'WareForm' rejects them without the tick. If
    they were left, the product could no longer be saved through its own form.
    """

    model = Ware

    def ausfuehren(self, satz: Ware) -> None:
        request, ware = self.request, satz
        gewuenscht = request.POST.get("abwiegbar") == "ja"
        if gewuenscht == ware.abwiegbar:
            return

        ware.abwiegbar = gewuenscht
        ware.save(update_fields=["abwiegbar"])

        if gewuenscht:
            meldung = _("„%(ware)s“ wird abgewogen.") % {"ware": ware.name}
            if ware.braucht_wiegefaktor and ware.gramm_je_einheit is None:
                messages.warning(
                    request,
                    meldung
                    + " "
                    + _(
                        "Auf ihrer Seite fehlt noch „%(faktor)s“. Ohne den Wert rechnet "
                        "die Waage 1:1."
                    )
                    % {"faktor": ware.faktor_beschriftung(Dimension.MASSE)},
                )
            else:
                messages.success(
                    request,
                    meldung + " " + _("Welche Behälter üblich sind, steht auf ihrer Seite."),
                )
        else:
            geloest = ware.behaelter.count()
            ware.behaelter.clear()
            meldung = _("„%(ware)s“ wird nicht mehr abgewogen.") % {"ware": ware.name}
            if geloest:
                meldung += " " + _("%(anzahl)s Behälterzuordnung(en) sind damit entfallen.") % {
                    "anzahl": geloest
                }
            messages.success(request, meldung)


class WareBehaelterView(Zeilenaktion):
    """Sets the usual containers of a product.

    Reachable from a house article too: there one sits while maintaining a
    house, and which vessels are used stands out exactly then. Stored is
    nonetheless on the product, because it applies to all houses.
    """

    model = Ware

    def ausfuehren(self, satz: Ware) -> None:
        request, ware = self.request, satz
        if not ware.abwiegbar:
            messages.error(
                request,
                _("„%(ware)s“ wird nicht abgewogen. Behälter haben dort keinen Zweck.")
                % {"ware": ware.name},
            )
            return

        # 'getlist' delivers text from the form. Anything that is not a number
        # would otherwise trip up the query.
        nummern = [ganzzahl(wert) for wert in request.POST.getlist("behaelter")]
        gewaehlt = list(Behaelter.zuweisbar().filter(pk__in=[n for n in nummern if n is not None]))
        ware.behaelter.set(gewaehlt)

        namen = ", ".join(str(behaelter.anzeigename) for behaelter in gewaehlt)
        messages.success(
            request,
            _("Behälter für „%(ware)s“ gespeichert: %(namen)s.")
            % {"ware": ware.name, "namen": namen or _("keine")},
        )


class GebindeUmschaltenView(Zeilenaktion):
    """Switches a pack active/inactive.

    An inactive pack is no longer available when counting: the app does not
    offer it, and new inventories give it no row ('Inventur.zaehlpositionen_anlegen').
    It stays on the defaults and in old counts. Hence no deleting.

    **The button says which state it wants** ('aktiv'), and only that is set.
    Until 10/2026 it only switched: sent once more from a stale tab, or
    again after the network failed, it switched the pack back. Now a click
    that comes too late changes nothing and says how things stand, as the
    counting list does ('ErwartungUmschaltenView').
    """

    model = Gebinde

    def standardziel(self, satz: Gebinde) -> str:
        return satz.ware.get_absolute_url()

    def ausfuehren(self, satz: Gebinde) -> None:
        aktiv = {"1": True, "0": False}.get(self.request.POST.get("aktiv", ""))
        if aktiv is None:
            messages.error(self.request, _("Unbekannter Status."))
            return
        werte = {"satz": satz.anzeigename, "inaktiv": _("aktiv") if aktiv else _("inaktiv")}
        if satz.aktiv == aktiv:
            messages.info(self.request, _("Gebinde „%(satz)s“ ist schon %(inaktiv)s.") % werte)
            return
        satz.aktiv_setzen(aktiv)
        messages.success(self.request, _("Gebinde „%(satz)s“ ist jetzt %(inaktiv)s.") % werte)


class GebindeSortierenView(SortierenView):
    """Order of the packs of a product, applies the same at every location."""

    model = Gebinde
    gruppenmodell = Ware
