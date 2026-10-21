"""Check and close: the count state, the check list and the freezing."""

from dataclasses import dataclass
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import DetailView
from django.views.generic.detail import SingleObjectMixin

from django_project.form_fields import Dezimalfeld
from django_project.labels import Beschriftung
from django_project.middleware import im_hintergrund
from django_project.validation import ganzzahl
from katalog.family_tree import Familienknoten
from katalog.models import Hausartikel

from ..closing import (
    DEM_ARTIKEL,
    DEM_HAUS,
    EINSTELLEN,
    GANZE_WARE,
    INAKTIV_SETZEN,
    NEUE_BEMERKUNGEN,
    NEUE_WERTE,
    NUR_DIESER,
    SOLL_EIGENER_WERT,
    SOLL_NULL,
    WECHSEL_PRAEFIX,
    Entscheidungen,
    NeueWerte,
    abschliessen,
    klaeren,
)
from ..evaluation import (
    Paar,
    familiensumme,
    summenklasse,
)
from ..forms import (
    PrueflisteFilterForm,
)
from ..models import (
    Inventur,
    Inventurstatus,
)
from ..services import (
    NOTIZ_HOECHSTLAENGE,
    InventurAbgeschlossen,
    NotizenVeraltet,
    bemerkungsstand,
    bemerkungsstand_wie,
    ergebniszeilen,
    ist_bemerkungsstand,
    menge_text_mit_vorzeichen,
    notizen_speichern,
    zaehlstand,
)
from .base import melde_abschluss, mit_fortschritt
from .run import Lauf, lauf_weiter

#: The header in which a background save of the check list hands back the
#: remarks' state the page carries on ('notizen_speichern', 'notes.js').
BEMERKUNGSSTAND_KOPF = "X-Bemerkungsstand"

#: The header in which a 400 that stored the other rows names the fields it
#: left as they were, separated by spaces ('notes.js' moves the rest along).
NICHT_GESPEICHERT_KOPF = "X-Nicht-Gespeichert"

#: What a background save answers with a sentence. It names articles, and as
#: HTML a name would be markup ('notes.js' shows it as text anyway).
NUR_TEXT = "text/plain; charset=utf-8"

#: Why a request to close was sent back: it did not say what the page showed,
#: the count state or the remarks' state is missing ('_abschliessen').
OHNE_STAND = _(
    "Nicht abgeschlossen: Es fehlt, welche Werte du zuletzt gesehen hast. Bitte prüfen und "
    "dann abschließen."
)


@dataclass
class Pruefblock(Familienknoten):
    """A family of the check list, with the sum of its differences by the current state.

    The sum runs over the whole subtree, in the family's lead unit, as
    later on the sheet. The rows stand in the house's unit. The sum wants
    the product's, and each row's difference as closing freezes it.
    """

    @property
    def wert(self) -> str:
        """Under it "Refresh values" finds the sum again."""
        return f"familiensumme-{self.familie.pk if self.familie is not None else 'ohne'}"

    @property
    def paare(self) -> list[Paar]:
        return [
            (zeile.hausartikel.ware, zeile.in_wareneinheit(zeile.differenz_gerundet))
            for zeile in self.alle_eintraege()
        ]

    @property
    def differenz(self) -> str:
        return familiensumme(self.familie, self.paare, menge_text_mit_vorzeichen)

    @property
    def differenz_klasse(self) -> str:
        return summenklasse(self.familie, self.paare)

    @property
    def anzahl_auffaellig(self) -> int:
        return sum(1 for zeile in self.alle_eintraege() if zeile.auffaellig)


def ungesehen(
    inventur: Inventur, gesehen: str | None, bemerkungen: str | None
) -> Beschriftung | None:
    """Why a page with these states may not close, None while they still apply.

    'gesehen' is the count state the page showed, 'bemerkungen' its remarks'
    state. Both are needed: without one nobody can say what was seen. Until
    10/2026 a request without them closed without comparing anything: the
    page of the questions reached without its query, or the check list
    without its remarks' state. Pages from before the states existed are long
    gone. Asked early, for the message. Final is the comparison under the
    lock ('abschliessen').
    """
    if gesehen is None or bemerkungen is None:
        return OHNE_STAND
    if gesehen != zaehlstand(inventur):
        return NEUE_WERTE
    if bemerkungen != bemerkungsstand_wie(inventur, bemerkungen):
        return NEUE_BEMERKUNGEN
    return None


class InventurZaehlstandView(View):
    """The id of the stored count state, for the check view.

    It regularly asks here instead of fetching the whole page, and highlights
    "Refresh values" as soon as the id differs from its own
    ('services.zaehlstand', 'static/js/refresh.js').
    """

    def get(self, request, pk):
        return JsonResponse({"stand": zaehlstand(get_object_or_404(Inventur, pk=pk))})


class InventurPruefenView(DetailView):
    """Check and close: per house article target, actual, difference and a note.

    The last look before freezing, and therefore the **only** place where an
    inventory can be closed: whoever closes should have seen beforehand what
    is being frozen. Listed is the whole scope, including articles for which
    there is a count position but where nothing has arrived yet.

    The notes lie next to it as 'Inventurnotiz' until then and migrate into
    the frozen row at closing. Counting does not happen here either. The
    quantities come from the PWA, the button "Refresh values" fetches them.
    """

    model = Inventur
    template_name = "inventur/inventur_review.html"
    context_object_name = "inventur"

    #: Prefix of the note fields in the form: 'notiz_<house article pk>'.
    NOTIZFELD = "notiz_"
    #: The same for the tick "Target counts".
    UEBERNAHMEFELD = "sollstattist_"
    #: The difference by hand, where the inventory takes it: 'differenz_<pk>'.
    DIFFERENZFELD = "differenz_"
    #: What the row showed when the page was built, per field: 'notiz_vorher_<pk>'.
    #: Against it 'notizen_speichern' decides what was changed on this page.
    VORHER = "vorher_"

    def get_queryset(self):
        return mit_fortschritt(super().get_queryset())

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if not self.object.laeuft:
            messages.info(
                request,
                _("Diese Inventur ist abgeschlossen. Das eingefrorene Ergebnis steht hier."),
            )
            return redirect(self.object.get_absolute_url())
        if (umleitung := self._ansicht(request)) is not None:
            return umleitung
        return self.render_to_response(self.get_context_data())

    def _ansicht(self, request):
        """The check list comes back as one last had it.

        Unlike the other lists, which remember their view only per tab and for
        the breadcrumbs: here work goes on over days, often in several attempts
        and via the "Check" button instead of the breadcrumbs. Remembered in the
        session, per inventory: filter, sort and grouping, i.e. the query. Scroll
        position and expanded groups are remembered by the browser
        ('data-ansicht-dauerhaft').

        Without a query it goes to the remembered one. With a query it is
        remembered, an empty one too, when the search emptied everything while
        filtering (it queries in the background). "Reset filter" forgets it
        ('?zuruecksetzen=1').
        """
        schluessel = f"pruefansicht:{self.object.pk}"
        if "zuruecksetzen" in request.GET:
            request.session.pop(schluessel, None)
            return redirect(request.path)
        abfrage = request.META.get("QUERY_STRING", "")
        if abfrage or request.headers.get("X-Requested-With") == "fetch":
            request.session[schluessel] = abfrage
            return None
        if gemerkt := request.session.get(schluessel):
            return redirect(f"{request.path}?{gemerkt}")
        return None

    def get_context_data(self, **kwargs):
        # The count state before the numbers: what arrives in between then
        # counts as unseen and stops the closing, and not the other way round
        # (until 10/2026 it was taken after them). The remarks' state likewise.
        gesehen = zaehlstand(self.object)
        bemerkungen = bemerkungsstand(self.object)
        alle = ergebniszeilen(self.object)
        filterform = PrueflisteFilterForm(self.request.GET)
        gezeigt = filterform.anwenden(alle)
        return super().get_context_data(**kwargs) | {
            "filterform": filterform,
            "spalten": filterform.spalten.kopf(),
            "zeilen": gezeigt,
            "gruppen": self.gruppen(filterform, gezeigt) if filterform.gegliedert else None,
            # All rows, not only the filtered ones: the snapshot from which the
            # script reinserts the numbers after filtering ('refresh.js').
            "alle": alle,
            "anzahl_gesamt": len(alle),
            "anzahl_auffaellig": sum(1 for zeile in alle if zeile.auffaellig),
            "anzahl_offen": sum(1 for zeile in alle if not zeile.gezaehlt),
            "anzahl_notizen": sum(1 for zeile in alle if zeile.notiz),
            "stand": timezone.localtime(),
            "zaehlstand": gesehen,
            "bemerkungsstand": bemerkungen,
            "notiz_hoechstlaenge": NOTIZ_HOECHSTLAENGE,
            "kann_abschliessen": self.object.status == Inventurstatus.ZAEHLUNG,
            # The switch above the list, as in the counting app: only where the
            # difference does not compute itself ('differenz_von_hand_setzen').
            "von_hand_umschaltbar": (
                self.object.differenz_von_hand or not self.object.hat_sollbestand
            ),
        }

    @staticmethod
    def gruppen(filterform: PrueflisteFilterForm, zeilen) -> list:
        """The rows bundled, one expander per group.

        By product family the groups form the tree of the families
        ('Pruefblock'), and every family carries what it is missing or has
        in excess in total by the current state, in its lead unit, as later
        on the sheet. Over a product group nothing is summed: it mixes units
        that have no common lead unit.
        """
        if filterform.gliederung is not None and filterform.gliederung[0] == "familie":
            return filterform.familienbaum(zeilen, Pruefblock)
        bloecke = filterform.gruppen(zeilen) or []
        for block in bloecke:
            block["anzahl_auffaellig"] = sum(1 for zeile in block["eintraege"] if zeile.auffaellig)
        return bloecke

    def post(self, request, *args, **kwargs):
        """Save notes and close in the same go on request.

        Both in one form, so a note just typed does not get lost because someone
        presses "Close" right away.
        """
        self.object = self.get_object()
        nebenbei = self._nebenbei(request)
        if not self.object.laeuft:
            return self._abgeschlossen(request, nebenbei=nebenbei)

        differenzen, keine_zahl = self._differenzen(request)
        fehler = []
        # Without it the remarks are saved all the same, but nothing closes
        # ('_abschliessen').
        bemerkungen = request.POST.get("bemerkungsstand") or None
        getragen = None
        # The fields the save left as they were. None where nothing was
        # stored at all, the save being one transaction.
        liegen_geblieben: list[str] | None = None
        try:
            getragen = notizen_speichern(
                self.object,
                self._eingaben(request),
                self._uebernahmen(request),
                self._vorher(request),
                differenzen,
                bemerkungen=bemerkungen,
            ).bemerkungen
            liegen_geblieben = []
        except InventurAbgeschlossen:
            return self._abgeschlossen(request, nebenbei=nebenbei)
        except NotizenVeraltet as veraltet:
            # Raised once the other rows are written ('notizen_speichern'). A row
            # someone else wrote in stays whole, its tick goes with its note.
            fehler.extend(veraltet.messages)
            liegen_geblieben = [
                f"{feld}{pk}"
                for pk in veraltet.hausartikel_ids
                for feld in (self.NOTIZFELD, self.DIFFERENZFELD)
            ]
        except ValidationError as abgewiesen:
            fehler.extend(abgewiesen.messages)
        # A page from before someone switched the difference by hand off: the
        # notes are saved, its differences are not ('notizen_speichern').
        verworfen = [
            pk
            for pk, (eingetragen, gezeigt) in differenzen.items()
            if not self.object.differenz_von_hand and eingetragen != gezeigt
        ]
        if verworfen:
            fehler.append(
                str(
                    _(
                        "Bei dieser Inventur wird die Differenz nicht mehr von Hand eingetragen. "
                        "Die Notizen sind gespeichert, die eingetragenen Differenzen nicht."
                    )
                )
            )
        if keine_zahl:
            fehler.append(self._keine_zahl(keine_zahl))
        if liegen_geblieben is not None:
            # Of these rows only the difference stays as it was.
            liegen_geblieben += [f"{self.DIFFERENZFELD}{pk}" for pk in [*verworfen, *keine_zahl]]

        if nebenbei:
            antwort = (
                HttpResponse(" ".join(fehler), status=400, content_type=NUR_TEXT)
                if fehler
                else HttpResponse(status=204)
            )
            # Also with a 400 for a difference that is no number: the other
            # rows are written, and the page carries them on as its own.
            # Where nothing was written, the state is the one the page sent,
            # so only one of the form the server gives. With a line break in
            # it the answer ended in a server error (until 10/2026).
            if getragen and ist_bemerkungsstand(getragen):
                antwort[BEMERKUNGSSTAND_KOPF] = getragen
            # Told the fields left as they were, the page moves along what
            # the stored ones show. Until 10/2026 every field kept what it
            # showed at loading after a 400, and changing a stored one again
            # heard that someone else had written there. Without the header
            # nothing was stored, and nothing moves.
            if fehler and liegen_geblieben is not None:
                antwort[NICHT_GESPEICHERT_KOPF] = " ".join(dict.fromkeys(liegen_geblieben))
            return antwort

        # A difference that is no number is not stored. Closing without it would
        # freeze the row as if nobody had typed anything.
        if fehler:
            messages.error(request, " ".join(fehler))
            return redirect(request.get_full_path())

        if "abschliessen" not in request.POST:
            messages.success(request, _("Notizen gespeichert."))
            return redirect(request.get_full_path())

        return self._abschliessen(request, getragen)

    @staticmethod
    def _nebenbei(request) -> bool:
        """Whether the script saves in the background, when a field or the page is left.

        It only needs the confirmation of that. Sending the whole page back
        would be several hundred kilobytes for a "yes, arrived" with two
        hundred rows. When leaving the page it goes out via 'sendBeacon',
        which cannot set headers. Hence the field in addition.
        """
        return im_hintergrund(request) or bool(request.POST.get("nebenbei"))

    def _abgeschlossen(self, request, *, nebenbei: bool):
        """Closed meanwhile, seen before saving or under the lock of the notes.

        The script hears a status with the sentence it shows. Until 10/2026
        it got the redirect, 'fetch' followed it to the inventory's page, and
        the check list said "Notes saved" although nothing was stored. The
        message meant for that page was used up on the way. A form sent the
        ordinary way goes to the frozen result.
        """
        if nebenbei:
            return HttpResponse(
                _("Notizen nicht gesichert, denn die Inventur ist inzwischen abgeschlossen."),
                status=409,
                content_type=NUR_TEXT,
            )
        messages.error(request, _("Diese Inventur ist abgeschlossen."))
        return redirect(self.object.get_absolute_url())

    def _abschliessen(self, request, bemerkungen: str | None):
        """First the questions, then the freezing. If there are none, the step is skipped.

        **Closed is only what was seen.** The form carries the count state with
        which the numbers on the page were fetched. If something came in from the
        counting app in the meantime, the inventory stays open, and the page
        shows the new numbers. The notes are saved by then. The script disables
        the button in this case anyway. Here it applies even without the script.
        The same goes for the remarks someone else changed ('bemerkungen', as
        'notizen_speichern' left it after the page's own).

        Both states are needed ('ungesehen'). A request without one does not
        say what was seen and goes back to look again.
        """
        if self.object.status != Inventurstatus.ZAEHLUNG:
            messages.error(
                request, _("Abgeschlossen wird aus der Zählung heraus. Sie läuft noch nicht.")
            )
            return redirect(request.get_full_path())
        gesehen = request.POST.get("zaehlstand") or None
        if (warum := ungesehen(self.object, gesehen, bemerkungen)) is not None:
            messages.warning(request, warum)
            return redirect(request.get_full_path())
        if not klaeren(self.object).leer:
            # The seen states go along, they are checked once more when freezing.
            ziel = reverse("inventur_abschluss", args=[self.object.pk])
            abfrage = {"stand": gesehen, "bemerkungen": bemerkungen}
            return redirect(f"{ziel}?{urlencode(abfrage)}")
        try:
            bericht = abschliessen(
                self.object, Entscheidungen(), gesehen=gesehen, bemerkungen=bemerkungen
            )
        except NeueWerte as neu:
            messages.warning(request, " ".join(neu.messages))
            return redirect(request.get_full_path())
        except ValidationError as fehler:
            # Two almost simultaneous clicks: the second finds the inventory
            # already closed. A message, not an error page.
            messages.error(request, " ".join(fehler.messages))
        else:
            melde_abschluss(request, self.object, bericht)
        return redirect(self.object.get_absolute_url())

    def _eingaben(self, request) -> dict[int, str]:
        """The note fields from the POST, mapped to house article numbers.

        What does not look like 'notiz_<number>' is thrown out here. Whether the
        number belongs to the house is checked by 'notizen_speichern'.
        """
        eingaben = {}
        for name, wert in request.POST.items():
            pk = ganzzahl(name.removeprefix(self.NOTIZFELD))
            if name.startswith(self.NOTIZFELD) and pk is not None:
                eingaben[pk] = wert
        return eingaben

    def _uebernahmen(self, request) -> set[int]:
        """Where the tick is set, the target stock should apply at closing.

        An unticked box sends nothing at all. Therefore only what arrives
        counts, and everything else counts as deselected. That is right: the
        rows in the form are exactly those someone is seeing right now.
        """
        gesetzt = set()
        for name in request.POST:
            pk = ganzzahl(name.removeprefix(self.UEBERNAHMEFELD))
            if name.startswith(self.UEBERNAHMEFELD) and pk is not None:
                gesetzt.add(pk)
        return gesetzt

    def _differenzen(self, request) -> tuple[dict, list[int]]:
        """The differences by hand as (typed, shown), and the rows whose input is no number.

        With a comma or a point, as in every number field ('Dezimalfeld'). The
        shown value is read the same way: the page wrote it in the account's
        language.
        """
        feld = Dezimalfeld(max_digits=12, decimal_places=3, required=False)
        praefix_vorher = self.DIFFERENZFELD + self.VORHER
        differenzen = {}
        keine_zahl = []
        for name, wert in request.POST.items():
            nummer = name[len(self.DIFFERENZFELD) :]
            if not name.startswith(self.DIFFERENZFELD) or ganzzahl(nummer) is None:
                continue
            try:
                eingetragen = feld.clean(wert)
                gezeigt = feld.clean(request.POST.get(f"{praefix_vorher}{nummer}", ""))
            except ValidationError:
                keine_zahl.append(int(nummer))
                continue
            differenzen[int(nummer)] = (eingetragen, gezeigt)
        return differenzen, keine_zahl

    def _keine_zahl(self, nummern: list[int]) -> str:
        """Which differences stayed unsaved, by the article's name."""
        namen = Hausartikel.objects.filter(pk__in=nummern, haus_id=self.object.haus_id).values_list(
            "name", flat=True
        )
        return _(
            "Nicht gespeichert, weil die Differenz keine Zahl ist (höchstens drei "
            "Nachkommastellen): %(namen)s."
        ) % {"namen": ", ".join(sorted(namen))}

    def _vorher(self, request) -> dict[int, tuple[str, bool]]:
        """What each row showed when its page was built: note and tick.

        Only rows that say so: a page from before carries no such field, and
        its rows are written as they stand. The tick's field exists only where
        the row has a box. Without it the row showed none.
        """
        praefix = self.NOTIZFELD + self.VORHER
        vorher = {}
        for name, wert in request.POST.items():
            pk = ganzzahl(name.removeprefix(praefix))
            if name.startswith(praefix) and pk is not None:
                haken = request.POST.get(f"{self.UEBERNAHMEFELD}{self.VORHER}{pk}") == "1"
                vorher[pk] = (wert, haken)
        return vorher


class InventurAbschlussView(SingleObjectMixin, View):
    """The last step before freezing: what the count shows about the master data.

    The page is reached only via "Close inventory" in the check list, after
    the confirmation, or via "Close all" in the run ('?lauf=1'). Nothing is
    frozen here yet. That only happens with the button at the bottom,
    together with the answers and in one transaction. What is listed and what
    it causes is in 'inventur/closing/'. In the run it goes on to the next
    house afterwards ('lauf_weiter').
    """

    model = Inventur
    template_name = "inventur/inventur_closing.html"

    def get_queryset(self):
        return super().get_queryset().select_related("haus")

    def _im_lauf(self) -> bool:
        return self.request.GET.get("lauf") == "1"

    def _zurueck(self, request, inventur):
        """Away from an inventory that is not in the count, without closing anything.

        In the run back to the run: whoever comes from a second window or
        from the history finds the houses there and "Close all". Until 10/2026
        a GET with '?lauf=1' went on with the run and closed every other house
        without questions, without a button, a confirmation or a house in
        preparation stopping it.
        """
        if self._im_lauf() and inventur.status == Inventurstatus.ABGESCHLOSSEN:
            messages.info(
                request,
                _(
                    "%(inventur)s ist schon abgeschlossen. Die übrigen Häuser schließt "
                    "„Alle abschließen“ hier im Lauf ab."
                )
                % {"inventur": inventur},
            )
        else:
            messages.error(
                request,
                _("%(inventur)s steht nicht in der Zählung. Abgeschlossen wird nur von dort.")
                % {"inventur": inventur},
            )
        if self._im_lauf():
            return redirect(Lauf.von(inventur).get_absolute_url())
        return redirect(inventur.get_absolute_url())

    def get(self, request, *args, **kwargs):
        """Shows the questions. A GET never closes, also not in the run."""
        inventur = self.get_object()
        if inventur.status != Inventurstatus.ZAEHLUNG:
            return self._zurueck(request, inventur)
        return self._zeigen(inventur, Entscheidungen())

    def _weiter(self, request, inventur):
        """After the button: in the run on to the next house, otherwise to the inventory.

        Also when a second window closed this house meanwhile. In the run that
        goes on only as far as "Close all" would ('lauf_weiter').
        """
        if self._im_lauf():
            return lauf_weiter(request, Lauf.von(inventur))
        return redirect(inventur.get_absolute_url())

    def post(self, request, *args, **kwargs):
        inventur = self.get_object()
        if inventur.status != Inventurstatus.ZAEHLUNG:
            if self._im_lauf():
                return self._weiter(request, inventur)
            return self._zurueck(request, inventur)
        # From the check list the states came along whose numbers and remarks
        # were visible there. If either moved since, or one is missing, it
        # goes back there ('_abschliessen').
        gesehen = request.POST.get("zaehlstand") or None
        bemerkungen = request.POST.get("bemerkungsstand") or None
        if (warum := ungesehen(inventur, gesehen, bemerkungen)) is not None:
            messages.warning(request, warum)
            return redirect("inventur_pruefen", pk=inventur.pk)
        entscheidungen = Entscheidungen.aus_formular(request.POST)
        try:
            bericht = abschliessen(
                inventur, entscheidungen, gesehen=gesehen, bemerkungen=bemerkungen
            )
        except NeueWerte as neu:
            messages.warning(request, " ".join(neu.messages))
            return redirect("inventur_pruefen", pk=inventur.pk)
        except ValidationError as fehler:
            return self._zeigen(inventur, entscheidungen, fehler.messages)
        melde_abschluss(request, inventur, bericht)
        return self._weiter(request, inventur)

    def _zeigen(self, inventur, entscheidungen, fehler=()):
        klaerung = klaeren(inventur)
        # The template cannot read a dictionary through a variable key.
        # The previous answer therefore stands right on the row.
        for eintrag in klaerung.ungezaehlte_artikel:
            eintrag.antwort = entscheidungen.ungezaehlt.get(eintrag.schluessel, "")
            eintrag.geltung = entscheidungen.geltung.get(eintrag.schluessel, "")
        for eintrag in klaerung.ausserhalb:
            eintrag.antwort = entscheidungen.in_art.get(eintrag.schluessel, "")
            eintrag.geltung = entscheidungen.geltung.get(eintrag.schluessel, "")
        for eintrag in klaerung.ohne_soll:
            eintrag.antwort = entscheidungen.soll.get(eintrag.schluessel, "")
            eintrag.wert = entscheidungen.sollwerte.get(eintrag.schluessel, "")
        for eintrag in klaerung.fremde_behaelter:
            eintrag.antwort = entscheidungen.behaelter.get(eintrag.schluessel, "")
        return render(
            self.request,
            self.template_name,
            {
                "inventur": inventur,
                "klaerung": klaerung,
                "entscheidungen": entscheidungen,
                "fehler": fehler,
                "lauf": (Lauf.von(inventur) if self._im_lauf() else None),
                "zaehlstand": self.request.GET.get("stand")
                or self.request.POST.get("zaehlstand", ""),
                "bemerkungsstand": self.request.GET.get("bemerkungen")
                or self.request.POST.get("bemerkungsstand", ""),
                "INAKTIV_SETZEN": INAKTIV_SETZEN,
                "EINSTELLEN": EINSTELLEN,
                "WECHSEL_PRAEFIX": WECHSEL_PRAEFIX,
                "NUR_DIESER": NUR_DIESER,
                "GANZE_WARE": GANZE_WARE,
                "SOLL_NULL": SOLL_NULL,
                "SOLL_EIGENER_WERT": SOLL_EIGENER_WERT,
                "DEM_ARTIKEL": DEM_ARTIKEL,
                "DEM_HAUS": DEM_HAUS,
            },
        )
