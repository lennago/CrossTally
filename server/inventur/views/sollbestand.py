"""The target stock from the file: messages after reading in and the clarification of open rows."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic.detail import SingleObjectMixin

from django_project.validation import ganzzahl
from django_project.views import meldungen_gekuerzt
from katalog.models import Artikelstatus, Hausartikel

from ..models import (
    Inventur,
    Inventurstatus,
    Sollgrund,
    Sollzeile,
)
from ..services import (
    KLAERUNG_NUR_IN_DER_VORBEREITUNG,
    NichtInDerVorbereitung,
    ohne_soll_ausscheiden,
    sollklaerung,
    sollzeile_aktivieren,
    sollzeile_dazurechnen,
    sollzeile_verwerfen,
    sollzeilen_erneut_zuordnen,
    sollzeilen_verwerfen,
)

#: What a report of the read-in can say besides the main count: the field of
#: the report, the level, the sentence. '%(anzahl)s' is the field's value.
HINWEISE = (
    (
        "ohne_nummern",
        messages.WARNING,
        _(
            "Dieses Haus führt keine Artikelnummern. Eine Datei kann hier nichts zuordnen, "
            "das Soll wird über „Von Hand pflegen“ eingetragen."
        ),
    ),
    (
        "offen",
        messages.WARNING,
        _(
            "%(anzahl)s Zeile(n) brauchen eine Entscheidung: unbekannte Nummern, inaktive oder "
            "eingestellte Artikel."
        ),
    ),
    (
        "nicht_dran",
        messages.INFO,
        _(
            "%(anzahl)s Zeile(n) zu Jahresware gespeichert. Sie ist bei dieser Inventur "
            "nicht dran, ihr Soll gilt nur, wenn sie in der App dazugezählt wird."
        ),
    ),
    (
        "nicht_zaehlbar",
        messages.INFO,
        _("%(anzahl)s Zeile(n) zu nicht zählbaren Nummern übergangen."),
    ),
    (
        "zugerechnet",
        messages.INFO,
        _(
            "%(anzahl)s Zeile(n) zu eingestellten Dubletten dem Artikel zugerechnet, als der "
            "sie zählen."
        ),
    ),
    (
        "von_hand_ersetzt",
        messages.INFO,
        _("%(anzahl)s von Hand eingetragene Sollbestände hat die Datei ersetzt."),
    ),
)


def melde_sollbestand(request, bericht) -> None:
    """The messages after reading in: what was adopted, what is open, what was skipped."""
    if bericht.zugeordnet:
        messages.success(
            request,
            _("%(zugeordnet)s von %(zeilen)s Zeilen als Sollbestand übernommen.")
            % {"zugeordnet": bericht.zugeordnet, "zeilen": bericht.zeilen},
        )
    elif bericht.zeilen:
        messages.warning(
            request, _("Aus dieser Datei wurde keine Zeile als Sollbestand übernommen.")
        )
    else:
        messages.warning(request, _("In der Datei stand keine lesbare Zeile."))
    for feld, stufe, text in HINWEISE:
        if wert := getattr(bericht, feld):
            messages.add_message(request, stufe, text % {"anzahl": wert})
    meldungen_gekuerzt(request, bericht.fehler, _("… und %(anzahl)s weitere unlesbare Zeilen."))


def _nummer(request, feld: str) -> str:
    """The number of a row or article from the form. Anything else finds nothing.

    Only a crafted form sends something else, and that is a 404 like an
    unknown reason ('_alle_verwerfen'), not a server error (until 10/2026).
    """
    wert = request.POST.get(feld, "")
    if ganzzahl(wert) is None:
        raise Http404
    return wert


class SollbestandKlaerungView(SingleObjectMixin, View):
    """What the target stock file left open and what it lacks.

    The file of the inventory management system says what the system carries
    as stock at the cut-off date. If it names a number the house does not
    actively carry, or does not name an active article, something about the
    master data is off, and that belongs clarified before the count, while the
    count positions can still arise and vanish without consequences. Hence
    only in preparation.

    Every answer is a button of its own, no bulk decision: they are individual
    articles, and each has its own history. The exception is discarding, per
    block and for all open rows together: it changes no master data, and
    whoever uploads the file anew anyway does not want to click away thirty
    rows one by one first.
    """

    model = Inventur
    template_name = "inventur/sollbestand.html"

    def get_queryset(self):
        return super().get_queryset().select_related("haus")

    def _nur_vorbereitung(self, request, inventur):
        if inventur.status == Inventurstatus.VORBEREITUNG:
            return None
        return self._zur_inventur(request, inventur)

    @staticmethod
    def _zur_inventur(request, inventur):
        messages.info(request, KLAERUNG_NUR_IN_DER_VORBEREITUNG)
        return redirect(inventur.get_absolute_url())

    def get(self, request, *args, **kwargs):
        inventur = self.get_object()
        if (umleitung := self._nur_vorbereitung(request, inventur)) is not None:
            return umleitung
        return render(
            request,
            self.template_name,
            {
                "inventur": inventur,
                "klaerung": sollklaerung(inventur),
                "anzahl_soll": inventur.sollbestaende_im_umfang().count(),
                "INAKTIV": Artikelstatus.INAKTIV,
                "EINGESTELLT": Artikelstatus.EINGESTELLT,
            },
        )

    def post(self, request, *args, **kwargs):
        inventur = self.get_object()
        if (umleitung := self._nur_vorbereitung(request, inventur)) is not None:
            return umleitung
        aktion = request.POST.get("aktion", "")
        try:
            self._ausfuehren(request, inventur, aktion)
        except NichtInDerVorbereitung as fehler:
            # Counting started after the look above, seen under the lock
            # ('services.clarification'): the answer for a page loaded after it.
            return self._zur_inventur(request, fehler.inventur)
        except ValidationError as fehler:
            messages.error(request, " ".join(fehler.messages))
        return redirect("inventur_sollbestand", pk=inventur.pk)

    def _ausfuehren(self, request, inventur, aktion: str) -> None:
        if aktion == "alle_verwerfen":
            self._alle_verwerfen(request, inventur)
        elif aktion == "erneut":
            self._erneut_zuordnen(request, inventur)
        elif aktion in ("aktivieren", "dazurechnen", "verwerfen"):
            self._zeile_beantworten(request, inventur, aktion)
        elif aktion in ("inaktiv", "einstellen"):
            self._artikel_ausscheiden(request, inventur, aktion)
        else:
            raise ValidationError(_("Unbekannte Aktion."))

    def _alle_verwerfen(self, request, inventur) -> None:
        grund = request.POST.get("grund", "")
        if grund and grund not in Sollgrund.values:
            raise Http404
        anzahl = sollzeilen_verwerfen(inventur, grund)
        messages.info(request, _("%(anzahl)s Zeile(n) verworfen.") % {"anzahl": anzahl})

    def _erneut_zuordnen(self, request, inventur) -> None:
        bericht = sollzeilen_erneut_zuordnen(inventur)
        messages.success(
            request,
            _("%(zugeordnet)s Zeile(n) zugeordnet, %(offen)s bleiben offen.")
            % {"zugeordnet": bericht.zugeordnet, "offen": bericht.offen},
        )

    def _zeile_beantworten(self, request, inventur, aktion: str) -> None:
        zeile = get_object_or_404(
            Sollzeile.objects.select_related("hausartikel"),
            pk=_nummer(request, "zeile"),
            inventur=inventur,
        )
        if aktion == "verwerfen":
            sollzeile_verwerfen(zeile)
            messages.info(
                request,
                _("Zeile %(artikelnummer)s verworfen.") % {"artikelnummer": zeile.artikelnummer},
            )
            return
        if aktion == "dazurechnen":
            ziel = sollzeile_dazurechnen(zeile)
            messages.success(
                request,
                _(
                    "Das Soll von %(artikelnummer)s ist bei %(ziel_nummer)s „%(ziel)s“ "
                    "dazugerechnet. Die alte Nummer zählt ab jetzt als dieser Artikel. Beim "
                    "nächsten Einlesen kommt ihr Bestand von selbst dazu."
                )
                % {
                    "artikelnummer": zeile.artikelnummer,
                    "ziel_nummer": ziel.artikelnummer,
                    "ziel": ziel.name,
                },
            )
            return
        anzahl = sollzeile_aktivieren(zeile)
        messages.success(
            request,
            _(
                "%(artikelnummer)s „%(hausartikel)s“ ist wieder aktiv. Das Soll ist "
                "übernommen, %(anzahl)s Zählposition(en) sind dazugekommen."
            )
            % {
                "artikelnummer": zeile.artikelnummer,
                "hausartikel": zeile.hausartikel.name,
                "anzahl": anzahl,
            },
        )

    def _artikel_ausscheiden(self, request, inventur, aktion: str) -> None:
        artikel = get_object_or_404(Hausartikel, pk=_nummer(request, "artikel"), haus=inventur.haus)
        status = Artikelstatus.INAKTIV if aktion == "inaktiv" else Artikelstatus.EINGESTELLT
        geloescht = ohne_soll_ausscheiden(inventur, artikel, status)
        messages.success(
            request,
            _(
                "%(artikelnummer)s „%(artikel)s“ ist %(beschriftung)s. %(geloescht)s leere "
                "Zählposition(en) sind entfernt."
            )
            % {
                "artikelnummer": artikel.artikelnummer,
                "artikel": artikel.name,
                "beschriftung": Artikelstatus(status).label.lower(),
                "geloescht": geloescht,
            },
        )
