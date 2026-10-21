"""The run: the inventories of a cut-off date across all houses, as one page."""

from dataclasses import dataclass
from datetime import date
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from django.views import View
from django.views.generic import TemplateView

from django_project.mixin_bases import AnsichtBasis

from ..closing import (
    Entscheidungen,
    NeueBemerkungen,
    NeueWerte,
    abschliessen,
    klaeren,
)
from ..evaluation import (
    familien_eines_laufs,
)
from ..models import (
    Inventur,
    Inventurstatus,
    arten_des_laufs,
    stichtag_kurz,
    zeilen_der_inventuren_sperren,
)
from ..pdf import als_pdf
from ..services import bemerkungsstand, bemerkungsstand_wie, zaehlstand
from .base import melde_abschluss, mit_fortschritt


@dataclass
class Lauf:
    """The inventories of a cut-off date, shown as one page.

    No model: the "parent inventory" is discarded (see 'models'). Here stands
    only the bracket for the views, i.e. name, address and the inventories
    including progress, sorted by house like the tabs.
    """

    stichtag: date
    inventuren: list[Inventur]

    @classmethod
    def aus_adresse(cls, stichtag: str) -> Lauf:
        try:
            tag = date.fromisoformat(stichtag)
        except ValueError:
            raise Http404(_("Kein Stichtag.")) from None
        inventuren = list(
            mit_fortschritt(Inventur.objects.filter(stichtag=tag)).order_by("haus__name")
        )
        if not inventuren:
            raise Http404(_("Zu diesem Tag gibt es keine Inventur."))
        return cls(tag, inventuren)

    @classmethod
    def von(cls, inventur: Inventur) -> Lauf:
        return cls.aus_adresse(inventur.stichtag.isoformat())

    @property
    def arten_anzeige(self) -> str:
        """The types of all its houses, each once. Usually all count the same."""
        return arten_des_laufs(self.inventuren)

    @property
    def name(self) -> str:
        return f"{stichtag_kurz(self.stichtag)} {self.arten_anzeige}"

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return self.inventuren[0].lauf_url()

    @property
    def offene(self) -> list[Inventur]:
        return [inventur for inventur in self.inventuren if inventur.laeuft]

    @property
    def offene_haeuser(self) -> str:
        """The names of the open houses, ready for a confirmation in the template.

        A loop cannot be accommodated in a 'blocktranslate', and in an attribute
        even less.
        """
        return ", ".join(inventur.haus.name for inventur in self.offene)

    @property
    def in_vorbereitung(self) -> list[Inventur]:
        return [
            inventur
            for inventur in self.inventuren
            if inventur.status == Inventurstatus.VORBEREITUNG
        ]

    @property
    def kann_abschliessen(self) -> bool:
        """Only from the count, and only if all open ones are that far.

        Leaving out a house in preparation would mean pressing "close all" and
        finding one open afterwards.
        """
        return bool(self.offene) and not self.in_vorbereitung


def lauf_reiter(inventur: Inventur) -> Lauf | None:
    """The tabs above the detail page when other houses count on the same day."""
    lauf = Lauf.von(inventur)
    return lauf if len(lauf.inventuren) > 1 else None


class LaufMixin(AnsichtBasis):
    def get_lauf(self) -> Lauf:
        return Lauf.aus_adresse(self.kwargs["stichtag"])


class LaufView(LaufMixin, TemplateView):
    """The tab "Total": all houses of the run side by side.

    During the count that is the check view over the run, afterwards the
    report of the day. Computed the same in both cases (see
    'evaluation.familien_eines_laufs'). There are no notes here: they hang on
    the article of a house, but the summary is over the product.
    """

    template_name = "inventur/run.html"

    def get(self, request, *args, **kwargs):
        lauf = self.get_lauf()
        if len(lauf.inventuren) == 1:
            # Left over, say after deleting the others: no run any more.
            return redirect(lauf.inventuren[0].get_absolute_url())
        return self.render_to_response(self.get_context_data(lauf=lauf))

    def get_context_data(self, **kwargs):
        lauf = kwargs["lauf"]
        # The count state per house, for "Close all" ('lauf_fortsetzen'). Taken
        # before the numbers: what arrives in between then counts as unseen and
        # stops the closing, and not the other way round. The remarks' state
        # likewise, without the notes' text: this page shows none of them.
        gesehen = [
            (inventur.pk, zaehlstand(inventur), bemerkungsstand(inventur, mit_notizen=False))
            for inventur in lauf.inventuren
            if inventur.status == Inventurstatus.ZAEHLUNG
        ]
        bloecke = familien_eines_laufs(lauf.inventuren)
        # Every level: the products of a family below a family count too, as
        # on the sheet (until 10/2026 only the top families' own rows did).
        zeilen = [zeile for block in bloecke for zeile in block.alle_zeilen]
        return super().get_context_data(**kwargs) | {
            "bloecke": bloecke,
            "anzahl_waren": len(zeilen),
            "anzahl_gegenlaeufig": sum(1 for zeile in zeilen if zeile.gegenlaeufig),
            "stand": timezone.localtime(),
            "gesehen": gesehen,
        }


class LaufPdfView(LaufMixin, View):
    """The sheet over all houses of the run, only once all are closed.

    For the same reason as with the sheet of one house: printed is only a
    frozen state.
    """

    def get(self, request, *args, **kwargs):
        lauf = self.get_lauf()
        if lauf.offene:
            messages.error(
                request,
                _(
                    "Im Lauf %(lauf)s ist noch nicht jede Inventur abgeschlossen. Gedruckt wird "
                    "der eingefrorene Stand."
                )
                % {"lauf": lauf},
            )
            return redirect(lauf.get_absolute_url())
        bloecke = familien_eines_laufs(lauf.inventuren)
        return als_pdf(
            request,
            "pdf/run.html",
            {
                "lauf": lauf,
                "bloecke": bloecke,
                "anzahl_waren": sum(block.anzahl for block in bloecke),
            },
            f"lauf-{lauf.stichtag}",
        )


def lauf_fortsetzen(request, lauf: Lauf):
    """The joint closing, step by step.

    First what closes without questions is closed. That changes nothing about
    the master data and can ask the other houses no question. Then it goes to
    the first house with questions. After its answers the view comes back
    here and recomputes: an answer about the scope of a product can settle or
    lock questions of other houses. Hence the houses one after another and
    not all questions at once.

    Every house freezes in its own transaction. If someone aborts in between,
    part is closed. That is an allowed state otherwise too, and the tab
    "Total" shows it.

    **Closed is only what was seen**, as on the check list. Every house is
    compared with the count state the tab "Total" showed when "Close all" was
    pressed ('LaufAbschlussView'). If one has moved since, nothing more is
    closed and the run shows the new numbers. A house with questions takes
    its state along to its page ('?stand=') and is compared there once more.
    Until 10/2026 "Close all" froze whatever had arrived in the meantime.
    The same goes for the remarks' state ('bemerkungsstand', '?bemerkungen=').
    A house without both is not closed either: nobody can say what was seen
    there ('_ohne_stand').
    """
    gesehen = _gesehen(request, lauf)
    offen = [inventur for inventur in lauf.inventuren if inventur.status == Inventurstatus.ZAEHLUNG]
    if ohne := [inventur for inventur in offen if inventur.pk not in gesehen]:
        return _ohne_stand(request, lauf, ohne)
    if veraltet := [
        inventur for inventur in offen if gesehen[inventur.pk][0] != zaehlstand(inventur)
    ]:
        return _neue_werte(request, lauf, veraltet)
    if geaendert := [
        inventur
        for inventur in offen
        if gesehen[inventur.pk][1] != bemerkungsstand_wie(inventur, gesehen[inventur.pk][1])
    ]:
        return _neue_werte(request, lauf, geaendert, bemerkungen=True)
    mit_fragen = []
    for inventur in offen:
        stand, bemerkungen = gesehen[inventur.pk]
        if not klaeren(inventur).leer:
            mit_fragen.append((inventur, stand, bemerkungen))
            continue
        try:
            bericht = abschliessen(
                inventur, Entscheidungen(), gesehen=stand, bemerkungen=bemerkungen
            )
        except NeueWerte as neu:
            return _neue_werte(
                request, lauf, [inventur], bemerkungen=isinstance(neu, NeueBemerkungen)
            )
        except ValidationError as fehler:
            messages.error(
                request,
                _("%(inventur)s: %(messages)s")
                % {"inventur": inventur, "messages": " ".join(fehler.messages)},
            )
        else:
            melde_abschluss(request, inventur, bericht)
    if mit_fragen:
        inventur, stand, bemerkungen = mit_fragen[0]
        ziel = reverse("inventur_abschluss", kwargs={"pk": inventur.pk})
        abfrage = {"lauf": 1, "stand": stand, "bemerkungen": bemerkungen}
        return redirect(f"{ziel}?{urlencode(abfrage)}")
    request.session.pop(_gesehen_schluessel(lauf), None)
    return redirect(lauf.get_absolute_url())


def _gesehen(request, lauf: Lauf) -> dict[int, tuple[str, str]]:
    """Per house the count state and the remarks' state "Close all" was pressed with.

    Only houses with both: one that is missing counts as unseen
    ('lauf_fortsetzen'). So does a house of a session from before the
    remarks' state, which carries only the count state.
    """
    gesehen = {}
    for nummer, wert in request.session.get(_gesehen_schluessel(lauf), {}).items():
        stand, bemerkungen = (wert, "") if isinstance(wert, str) else wert
        if stand and bemerkungen:
            gesehen[int(nummer)] = (stand, bemerkungen)
    return gesehen


def _gesehen_schluessel(lauf: Lauf) -> str:
    """Where the session keeps the count states "Close all" was pressed with, per run.

    In the session, because the houses with questions come one after
    another, each in a request of its own.
    """
    return f"lauf_gesehen:{lauf.stichtag.isoformat()}"


def _ohne_stand(request, lauf: Lauf, inventuren: list[Inventur]):
    """Back to the run: for these houses it is not known what the tab "Total" showed."""
    messages.warning(
        request,
        _(
            "Nicht abgeschlossen: Für %(haeuser)s fehlt, welche Werte du gesehen hast. Bitte "
            "prüfen und dann noch einmal „Alle abschließen“."
        )
        % {"haeuser": ", ".join(inventur.haus.name for inventur in inventuren)},
    )
    return redirect(lauf.get_absolute_url())


def _neue_werte(request, lauf: Lauf, inventuren: list[Inventur], *, bemerkungen: bool = False):
    """Back to the run, where the new numbers stand now. Nothing more is closed."""
    if bemerkungen:
        text = _(
            "Nicht abgeschlossen: In %(haeuser)s wurde seit dem Laden der Seite „Soll zählt“ "
            "oder eine Differenz von Hand geändert. Es steht jetzt da. Bitte prüfen und dann "
            "noch einmal „Alle abschließen“."
        )
    else:
        text = _(
            "Nicht abgeschlossen: In %(haeuser)s kamen seit dem Laden der Seite neue Werte aus "
            "der Zähl-App. Sie stehen jetzt da. Bitte prüfen und dann noch einmal „Alle "
            "abschließen“."
        )
    messages.warning(
        request, text % {"haeuser": ", ".join(inventur.haus.name for inventur in inventuren)}
    )
    return redirect(lauf.get_absolute_url())


def lauf_weiter(request, lauf: Lauf):
    """The joint closing goes on after a house, but only as far as "Close all" itself would.

    A house taken back into preparation in the meantime stops it, as it stops
    the button ('Lauf.kann_abschliessen'). Until 10/2026 the way on closed
    every other house without questions regardless.
    """
    if lauf.in_vorbereitung:
        messages.warning(
            request,
            _("Abgeschlossen wird aus der Zählung heraus.")
            + " "
            + _("Noch in der Vorbereitung: %(haeuser)s.")
            % {"haeuser": ", ".join(inventur.haus.name for inventur in lauf.in_vorbereitung)},
        )
        return redirect(lauf.get_absolute_url())
    return lauf_fortsetzen(request, lauf)


class LaufAbschlussView(LaufMixin, View):
    """The button "Close all" in the tab "Total". Only via POST, like every status change."""

    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        lauf = self.get_lauf()
        if not lauf.kann_abschliessen:
            if lauf.in_vorbereitung:
                dazu = _("Noch in der Vorbereitung: %(haeuser)s.") % {
                    "haeuser": ", ".join(inventur.haus.name for inventur in lauf.in_vorbereitung)
                }
            else:
                dazu = _("Offen ist keine Inventur mehr.")
            messages.error(
                request,
                _("Abgeschlossen wird aus der Zählung heraus.") + " " + str(dazu),
            )
            return redirect(lauf.get_absolute_url())
        # What the tab "Total" showed, per house. A house without it in the
        # form counts as unseen and stops the closing ('lauf_fortsetzen').
        request.session[_gesehen_schluessel(lauf)] = {
            str(inventur.pk): [
                request.POST.get(f"zaehlstand_{inventur.pk}", ""),
                request.POST.get(f"bemerkungsstand_{inventur.pk}", ""),
            ]
            for inventur in lauf.inventuren
            if inventur.status == Inventurstatus.ZAEHLUNG
        }
        return lauf_fortsetzen(request, lauf)


class LaufDeleteView(LaufMixin, TemplateView):
    """Delete the open inventories of the run at once, with confirmation.

    Closed ones stay, for the same reason as with a single inventory: the
    frozen results hang on them. Deleted in one transaction, either all open
    ones or none.
    """

    template_name = "inventur/run_delete.html"

    def get(self, request, *args, **kwargs):
        lauf = self.get_lauf()
        return self.render_to_response(
            self.get_context_data(lauf=lauf, object=lauf, offene=lauf.offene)
        )

    def post(self, request, *args, **kwargs):
        lauf = self.get_lauf()
        with transaction.atomic():
            # Read under the lock, not as the page loaded it: a house closed in
            # the meantime is no longer open and keeps its frozen results. The
            # closing locks the same rows (until 10/2026 it went along). By
            # number, because 'Meta.ordering' would lock the houses as well.
            # Without the key, as when deleting a single inventory
            # ('InventurDeleteView.form_valid'): a merge of products would
            # otherwise wait here for the check of its keys while the delete
            # waits for its rows.
            offene = list(
                Inventur.objects.select_for_update(no_key=True)
                .filter(pk__in=[inventur.pk for inventur in lauf.inventuren])
                .exclude(status=Inventurstatus.ABGESCHLOSSEN)
                .order_by("pk")
            )
            # Then their rows, those of all of them in one query
            # ('zeilen_der_inventuren_sperren').
            zeilen_der_inventuren_sperren(inventur.pk for inventur in offene)
            Inventur.objects.filter(pk__in=[inventur.pk for inventur in offene]).delete()
        if not offene:
            messages.info(
                request, _("Im Lauf %(lauf)s ist keine Inventur mehr offen.") % {"lauf": lauf}
            )
            return redirect(lauf.get_absolute_url())
        messages.success(
            request,
            ngettext(
                "Eine Inventur gelöscht: %(haeuser)s.",
                "%(anzahl)s Inventuren gelöscht: %(haeuser)s.",
                len(offene),
            )
            % {
                "anzahl": len(offene),
                "haeuser": ", ".join(inventur.haus.name for inventur in offene),
            },
        )
        if len(offene) == len(lauf.inventuren):
            return redirect("inventur_list")
        return redirect(lauf.get_absolute_url())
