"""The detail page of an inventory: count positions, status changes, counters and the PDF."""

from itertools import groupby

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import DetailView
from django.views.generic.detail import SingleObjectMixin
from django.views.generic.edit import FormMixin

from django_project.labels import Beschriftung
from django_project.views import Zeilenaktion
from katalog.formats import Lesefehler
from katalog.models import arten_je_artikel, menge_kurz

from ..evaluation import (
    warengruppen_einer_inventur,
)
from ..forms import (
    InventurArtenForm,
    SollbestandImportForm,
    ZaehlendeForm,
)
from ..models import (
    Inventur,
    Inventurstatus,
    Zaehlposition,
)
from ..pdf import als_pdf
from ..services import (
    NUR_IN_DER_VORBEREITUNG,
    OHNE_SOLL_VON_HAND,
    InventurAbgeschlossen,
    ergebniszeilen,
    sollbestand_einlesen,
    sollklaerung,
)
from .base import mit_fortschritt
from .run import lauf_reiter
from .sollbestand import melde_sollbestand


def _worin(position, einheit) -> Beschriftung:
    """What is counted in on this row: pack, scale or bare unit.

    A counted recipe article has neither pack nor scale. A missing pack alone
    therefore does not yet mean "weighed".
    """
    if position.gebinde_id:
        return str(position.gebinde.anzeigename)
    return _("abgewogen") if position.wird_gewogen else einheit.kurz


def reihenfolge_der_app(zeilen: list[dict]):
    """The sort key for the rows of a location, as the counting app shows them."""
    erste = {}
    for zeile in zeilen:
        position = zeile["position"]
        erste[position.artikel_id] = min(
            erste.get(position.artikel_id, position.sortierung), position.sortierung
        )

    def schluessel(zeile: dict):
        position = zeile["position"]
        gebinde = position.gebinde
        return (
            erste[position.artikel_id],
            zeile["artikel"].warenname.casefold(),
            position.artikel_id,
            (0, gebinde.sortierung) if gebinde else (1, 0),
        )

    return schluessel


def positionszeilen(inventur: Inventur) -> list[dict]:
    """The count positions of a running inventory, one prepared row each.

    The unit is not on the position: it is the unit of the counted article,
    for a house article the **house's** unit, not the product's. That is what
    'menge_in_basiseinheit' computes in, and until 2026-09-25 the label said
    the product's unit next to a number in the house's. The descent to the
    child table is prefetched, otherwise a query per row would come in addition.

    **Per location in the order of the counting app**, not by the alphabet:
    whoever looks here for what is still missing goes through the same list
    as the counters in storage. An article stands where its first row in the
    counting list stands ('progress.artikel' of the app), on a tie by product
    name. In it the packs in their order on the product, scale and piece
    count after, as in the form ('nach_ort').

    Every row carries its tab ('reiter'): the type it is counted in, as in
    the app. What no type of this inventory counts stands in the first tab.
    """
    positionen = list(
        # Rows marked as deleted are deleted. They only remain so the counting
        # app learns of the deletion.
        Zaehlposition.objects.filter(inventur=inventur, aktiv=True)
        .select_related(
            "ort__bereich",
            "artikel__hausartikel__basiseinheit",
            "artikel__hausartikel__ware__basiseinheit",
            "artikel__rezepturartikel__basiseinheit",
            "gebinde",
        )
        .prefetch_related("wiegungen__behaelter")
        .order_by("ort__bereich__name", "ort__name", "ort_id")
    )
    umfang = inventur.zaehlumfang
    arten = arten_je_artikel(inventur.haus_id, {position.artikel_id for position in positionen})
    erster = umfang.arten[0] if umfang.arten else None

    zeilen = []
    for position in positionen:
        artikel = position.artikel.konkret()
        einheit = artikel.basiseinheit
        zeilen.append(
            {
                "position": position,
                "artikel": artikel,
                "worin": _worin(position, einheit),
                "einheit": einheit,
                "menge": (menge := position.menge_in_basiseinheit()),
                # As the sheet shows it: the unit's places, no superfluous zeros.
                "menge_text": None if menge is None else menge_kurz(einheit.runden(menge)),
                "reiter": umfang.reiter(arten.get(position.artikel_id, [])) or erster,
            }
        )
    return zeilen


def nach_ort(zeilen: list[dict]) -> list[dict]:
    """The rows per location, in them in the order of the counting app."""
    gegliedert = []
    for _ort, block in groupby(zeilen, key=lambda zeile: zeile["position"].ort_id):
        gruppe = list(block)
        gruppe.sort(key=reihenfolge_der_app(gruppe))
        gegliedert.append(
            {
                "ort": gruppe[0]["position"].ort,
                "zeilen": gruppe,
                "gezaehlt": sum(1 for zeile in gruppe if zeile["menge"] is not None),
            }
        )
    return gegliedert


def arten_reiter(inventur: Inventur, zeilen: list[dict], gewaehlt: str) -> list[dict]:
    """The tabs of the inventory, one per type with rows, each with its own state.

    As in the app: in the order of the types, and only where something is
    to be counted. 'gewaehlt' is the type from the address. Without it, or
    with one that has no tab, the first is open.
    """
    reiter = []
    for art in inventur.arten():
        eigene = [zeile for zeile in zeilen if zeile["reiter"] == art.pk]
        if not eigene:
            continue
        reiter.append(
            {
                "wert": str(art.pk),
                "name": art.name,
                "aktiv": False,
                "gesamt": len(eigene),
                "gezaehlt": sum(1 for zeile in eigene if zeile["menge"] is not None),
            }
        )
    if reiter:
        offen = next((r for r in reiter if r["wert"] == gewaehlt), reiter[0])
        offen["aktiv"] = True
    return reiter


class InventurDetailView(FormMixin, DetailView):
    """Header, progress and (depending on status) upload, positions or result.

    The embedded form reads in the target stock, only in **preparation**. The
    file has a say in which articles still exist (see 'services.sollklaerung'),
    and that belongs clarified before the counting apps fetch their rows.
    Whoever has to submit a file during the count goes back into preparation.
    The counted quantities stay in the process.
    """

    model = Inventur
    form_class = SollbestandImportForm
    template_name = "inventur/inventur_detail.html"
    context_object_name = "inventur"

    def get_queryset(self):
        return mit_fortschritt(super().get_queryset())

    def get_success_url(self):
        return self.object.get_absolute_url()

    def get_context_data(self, **kwargs):
        inventur = self.object
        kontext = super().get_context_data(**kwargs) | {
            "lauf": lauf_reiter(inventur),
            "anzahl_soll": inventur.sollbestaende_im_umfang().count(),
            # Only without one the difference may be typed in by hand.
            "hat_sollbestand": inventur.laeuft and inventur.hat_sollbestand,
        }
        if inventur.laeuft:
            zeilen = positionszeilen(inventur)
            # Real addresses instead of switching in the browser, as in the
            # counting list. One tab alone needs no bar.
            reiter = arten_reiter(inventur, zeilen, self.request.GET.get("reiter", ""))
            if len(reiter) > 1:
                offen = next(r for r in reiter if r["aktiv"])
                kontext["arten_reiter"] = reiter
                zeilen = [z for z in zeilen if str(z["reiter"]) == offen["wert"]]
            kontext["orte"] = nach_ort(zeilen)
            klaerung = sollklaerung(inventur)
            kontext["offene_sollfragen"] = (
                len(klaerung.inaktive)
                + len(klaerung.eingestellte)
                + len(klaerung.unbekannte)
                + len(klaerung.abweichende_namen)
                + len(klaerung.ohne_soll)
            )
        else:
            # Only the frozen state. The running differences are in the check
            # view. That is where they belong, with search and notes, and the
            # detail page stays the overview instead of a second result table.
            zeilen = ergebniszeilen(inventur)
            kontext |= {
                "zeilen": zeilen,
                "anzahl_auffaellig": sum(1 for zeile in zeilen if zeile.auffaellig),
            }
        return kontext

    def post(self, request, *args, **kwargs):
        """The target stock upload. The status changes have addresses of their own."""
        self.object = self.get_object()
        if (warum := self._kein_upload()) is not None:
            messages.error(request, warum)
            return redirect(self.get_success_url())

        form = self.get_form()
        if not form.is_valid():
            return self.render_to_response(self.get_context_data(form=form))

        try:
            bericht = sollbestand_einlesen(
                form.cleaned_data["datei"], self.object, form.cleaned_data["format"]
            )
        except Lesefehler as fehler:
            # A damaged or wrong workbook: the sentence at the field, as in the
            # article import. Until 10/2026 it was a server error here.
            form.add_error("datei", str(fehler))
            return self.render_to_response(self.get_context_data(form=form))
        except ValidationError as fehler:
            # Someone switched the difference by hand on since the check above,
            # or started counting.
            messages.error(request, " ".join(fehler.messages))
            return redirect(self.get_success_url())
        melde_sollbestand(request, bericht)
        # What the file left open is asked right away, not at some point.
        if not sollklaerung(self.object).leer:
            return redirect("inventur_sollbestand", pk=self.object.pk)
        return redirect(self.get_success_url())

    def _kein_upload(self) -> Beschriftung | None:
        """Why no file comes in now, by the inventory as the page loaded it.

        Checked once more under the lock ('soll_darf_herein').
        """
        if self.object.differenz_von_hand:
            return OHNE_SOLL_VON_HAND
        if self.object.status != Inventurstatus.VORBEREITUNG:
            return NUR_IN_DER_VORBEREITUNG
        return None


class StatuswechselView(Zeilenaktion):
    """The status buttons of the inventory. 'wechseln' delivers the success message.

    From which state a step is allowed is known by the model. What does not
    work is turned into a message by 'Zeilenaktion'.
    """

    model = Inventur

    def wechseln(self, inventur: Inventur) -> Beschriftung:
        raise NotImplementedError

    def ausfuehren(self, satz: Inventur) -> None:
        messages.success(self.request, self.wechseln(satz))


class ZaehlungStartenView(StatuswechselView):
    def wechseln(self, inventur: Inventur) -> Beschriftung:
        inventur.zaehlung_starten()
        return _("%(inventur)s läuft. Die Zählenden können in der Zähl-App loslegen.") % {
            "inventur": inventur
        }


class ZurueckInVorbereitungView(StatuswechselView):
    """Back into preparation. Counted values stay."""

    def wechseln(self, inventur: Inventur) -> Beschriftung:
        inventur.zurueck_in_vorbereitung()
        return _(
            "%(inventur)s steht wieder in der Vorbereitung. Die bereits gezählten Mengen "
            "bleiben erhalten."
        ) % {"inventur": inventur}


class DifferenzVonHandView(StatuswechselView):
    """Switch the difference by hand on ('an=1') or off. The model says when it may."""

    def wechseln(self, inventur: Inventur) -> Beschriftung:
        an = self.request.POST.get("an") == "1"
        inventur.differenz_von_hand_setzen(an)
        if an:
            return _(
                "%(inventur)s: Die Differenz wird jetzt je Artikel von Hand eingetragen, in der "
                "Prüfliste oder in der Zähl-App."
            ) % {"inventur": inventur}
        return _(
            "%(inventur)s: Die Differenz wird nicht mehr von Hand eingetragen. Was schon "
            "eingetragen ist, bleibt gespeichert und gilt wieder, wenn es wieder an ist."
        ) % {"inventur": inventur}


class SperreView(StatuswechselView):
    """Lock the count ('an=1') or open it again. Closing works either way."""

    def wechseln(self, inventur: Inventur) -> Beschriftung:
        an = self.request.POST.get("an") == "1"
        inventur.sperre_setzen(an)
        if an:
            return _(
                "%(inventur)s ist gesperrt. In der Zähl-App ist nur noch die Prüfliste offen, "
                "gezählt wird erst wieder nach dem Öffnen."
            ) % {"inventur": inventur}
        return _("%(inventur)s ist wieder offen. Es kann weiter gezählt werden.") % {
            "inventur": inventur
        }


class InventurPdfView(DetailView):
    """The sheet for working through in the inventory management system.

    Only for closed inventories: printed is the **frozen** state, and a sheet
    whose numbers still move afterwards would be worthless. Grouped by product
    family, with a sum per family.
    """

    model = Inventur

    def get(self, request, *args, **kwargs):
        inventur = self.get_object()
        if inventur.status != Inventurstatus.ABGESCHLOSSEN:
            messages.error(
                request,
                _(
                    "%(inventur)s ist noch nicht abgeschlossen. Gedruckt wird der eingefrorene "
                    "Stand."
                )
                % {"inventur": inventur},
            )
            return redirect(inventur.get_absolute_url())

        # By product group and article number, as the inventory management
        # system lists them: the sheet is for transferring the numbers there.
        bloecke = warengruppen_einer_inventur(inventur)
        return als_pdf(
            request,
            "pdf/inventur.html",
            {
                "inventur": inventur,
                "bloecke": bloecke,
                "anzahl_waren": sum(len(block.zeilen) for block in bloecke),
            },
            f"inventur-{inventur.haus.name}-{inventur.stichtag}",
        )


class ZaehlendeView(SingleObjectMixin, View):
    """Who may count in this inventory.

    A page of its own instead of a field in the creation form: there several
    houses are available, and who counts in which house differs per
    inventory. Suggested is whoever is assigned to the house. Entered is
    explicit.
    """

    model = Inventur
    template_name = "inventur/inventur_counters.html"

    def get_queryset(self):
        return super().get_queryset().select_related("haus")

    @staticmethod
    def _abgeschlossen(request, inventur: Inventur):
        """Closing cleared the assignment ('Inventur.abschliessen'), and nobody is added again."""
        messages.error(request, _("Eine abgeschlossene Inventur wird nicht mehr umbesetzt."))
        return redirect(inventur.get_absolute_url())

    def get(self, request, *args, **kwargs):
        inventur = self.get_object()
        if not inventur.laeuft:
            return self._abgeschlossen(request, inventur)
        return self._zeigen(
            inventur, ZaehlendeForm(inventur=inventur, initial=self._stand(inventur))
        )

    def post(self, request, *args, **kwargs):
        inventur = self.get_object()
        if not inventur.laeuft:
            return self._abgeschlossen(request, inventur)

        form = ZaehlendeForm(request.POST, inventur=inventur)
        if not form.is_valid():
            return self._zeigen(inventur, form)

        try:
            anzahl = form.speichern()
        except InventurAbgeschlossen:
            return self._abgeschlossen(request, inventur)
        # In preparation the app does not list the inventory yet
        # ('Inventur.zaehlbare_fuer'). Until 10/2026 the page said "from now on".
        if inventur.status == Inventurstatus.VORBEREITUNG:
            text = _(
                "%(anzahl)s Zählende für %(inventur)s eingetragen. Sie sehen die Inventur in "
                "der Zähl-App, sobald die Zählung beginnt."
            )
        else:
            text = _(
                "%(anzahl)s Zählende für %(inventur)s eingetragen. Sie sehen die Inventur ab "
                "sofort in der Zähl-App."
            )
        messages.success(request, text % {"anzahl": anzahl, "inventur": inventur})
        return redirect(inventur.get_absolute_url())

    @staticmethod
    def _stand(inventur) -> dict:
        return {"zaehlende": inventur.zaehlende.all()}

    def _zeigen(self, inventur, form):
        return render(self.request, self.template_name, {"inventur": inventur, "form": form})


class InventurArtenView(SingleObjectMixin, View):
    """Which types an inventory counts, changed in preparation.

    A forgotten type comes along here instead of deleting and creating anew:
    counters and target stock stay. During the count the apps already hold
    their tabs, so there it is refused.
    """

    model = Inventur
    template_name = "inventur/inventur_types.html"

    def get_queryset(self):
        return super().get_queryset().select_related("haus")

    def get(self, request, *args, **kwargs):
        inventur = self.get_object()
        return self._zeigen(inventur, InventurArtenForm(inventur=inventur))

    def post(self, request, *args, **kwargs):
        inventur = self.get_object()
        if inventur.status != Inventurstatus.VORBEREITUNG:
            messages.error(request, _("Die Arten lassen sich nur in der Vorbereitung ändern."))
            return redirect(inventur.get_absolute_url())
        form = InventurArtenForm(request.POST, inventur=inventur)
        if not form.is_valid():
            return self._zeigen(inventur, form)
        dazu, entfernt = inventur.arten_aendern(form.cleaned_data["inventurarten"])
        messages.success(
            request,
            _(
                "%(inventur)s zählt jetzt %(arten)s: %(dazu)s Zählpositionen dazu, "
                "%(entfernt)s entfernt."
            )
            % {
                "inventur": inventur,
                "arten": inventur.arten_anzeige,
                "dazu": dazu,
                "entfernt": entfernt,
            },
        )
        return redirect(inventur.get_absolute_url())

    def _zeigen(self, inventur, form):
        return render(self.request, self.template_name, {"inventur": inventur, "form": form})
