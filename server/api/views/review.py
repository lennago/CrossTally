"""Writing from the check list of the app: remarks on an article, and the switches of managers."""

from collections.abc import Callable
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response

from django_project.labels import Beschriftung
from inventur.models import Inventur, Inventurstatus
from inventur.services import (
    UNVERAENDERT,
    InventurAbgeschlossen,
    VermerkVeraltet,
    ergebniszeile,
    vermerk_aendern,
)

from ..checks import NichtInDerZaehlung, pruefe_in_der_zaehlung
from ..permissions import IstAktiverBenutzer
from ..serializers import PrueflisteZeileSerializer, VermerkSerializer, zahl
from .base import eingabe


def zum_schreiben(request, pk) -> Inventur:
    """The inventory of a write from the app's check list, with the same answers as for counts.

    Found in every state, so a job that waited out a dead spot learns why it
    cannot go any more ('pruefe_in_der_zaehlung'), instead of meeting a 404
    that looks like a deleted inventory. A lock does not stop it: checking
    goes on when counting stops. A closed inventory no longer knows who
    counted ('Inventur.abschliessen'). It is found without that and only says
    that it is closed.
    """
    inventur = Inventur.fuer_zaehler(request.user).filter(pk=pk).first()
    if inventur is None:
        # Found without any assignment, so the answer names nothing: not the
        # house, not the day, not the types. Over the numbers a stranger
        # could otherwise list every closed inventory (until 10/2026).
        inventur = get_object_or_404(Inventur, pk=pk, status=Inventurstatus.ABGESCHLOSSEN)
        raise NichtInDerZaehlung(inventur, mit_namen=False)
    pruefe_in_der_zaehlung(inventur)
    return inventur


@api_view(["PATCH"])
@permission_classes([IstAktiverBenutzer])
def pruefliste_zeile(request, version=None, pk=None, hausartikel=None):
    """Note, difference by hand or "target counts" of one article, from the app's check list.

    One field at a time, the others stay as stored ('vermerk_aendern'). The
    note is for everyone who counts here, the difference as soon as a
    manager switched it on. "Target counts" only for managers, as on the
    website, where only they see the check list: it replaces the count with
    the target at closing, and a counter could hide a shortage that way.
    Setting it needs a target for the article. Back comes the row as it
    stands now, or null where the article dropped out of the list with its
    last remark.

    With 'notiz_vorher' or 'differenz_vorher' the app says what it showed
    when the person typed. If something else is stored by now, someone
    wrote in between, and the answer is a 409 with the stored row: the app
    shows that one and lists the own entry as not accepted.
    """
    inventur = zum_schreiben(request, pk)
    artikel_id = int(str(hausartikel))
    eingabe = VermerkSerializer(data=request.data)
    eingabe.is_valid(raise_exception=True)
    daten = eingabe.validated_data
    if "soll_statt_ist" in daten:
        if not request.user.is_staff:
            raise PermissionDenied(_("Ob das Soll zählt, entscheidet eine Führungskraft."))
        if daten["soll_statt_ist"] and (
            inventur.differenz_von_hand or artikel_id not in inventur.wirksame_sollbestaende()
        ):
            raise ValidationError({"detail": [_("Ohne Sollbestand gibt es nichts zu übernehmen.")]})
    kontext = {"von_hand": inventur.differenz_von_hand}

    def zeile_jetzt():
        zeile = ergebniszeile(inventur, artikel_id)
        return None if zeile is None else PrueflisteZeileSerializer(zeile, context=kontext).data

    vorher = {}
    if "notiz_vorher" in daten:
        vorher["text_vorher"] = daten["notiz_vorher"] or ""
    if "differenz_vorher" in daten:
        vorher["differenz_vorher"] = daten["differenz_vorher"]
    try:
        # The state once more under the inventory's lock: a second tab may
        # have taken it back into preparation since 'zum_schreiben' looked.
        vermerk_aendern(
            inventur,
            artikel_id,
            text=daten.get("notiz", UNVERAENDERT),
            soll_statt_ist=daten.get("soll_statt_ist", UNVERAENDERT),
            differenz=daten.get("differenz", UNVERAENDERT),
            pruefen=pruefe_in_der_zaehlung,
            **vorher,
        )
    except InventurAbgeschlossen as fehler:
        # Closed between the check above and the write: the same answer as
        # for a job that comes too late anyway.
        raise NichtInDerZaehlung(fehler.inventur) from fehler
    except VermerkVeraltet as fehler:
        wert = fehler.gespeichert
        gespeichert = zahl(wert) if isinstance(wert, Decimal) else str(wert or "–")
        return Response(
            {
                "detail": _(
                    "Inzwischen steht dort „%(wert)s“. Deine Eingabe wurde nicht übernommen."
                )
                % {"wert": gespeichert},
                "konflikt": fehler.feld,
                "zeile": zeile_jetzt(),
            },
            status=409,
        )
    except DjangoValidationError as fehler:
        raise ValidationError({"detail": fehler.messages}) from fehler
    return Response({"zeile": zeile_jetzt()})


def _schalten(
    request,
    pk,
    setzen: Callable[[Inventur, bool], None],
    nur_fuehrung: Beschriftung,
    feld: str,
) -> Response:
    """A switch of the inventory from the app: managers only, true or false, the model's rules.

    The difference by hand and the lock both go this way. Until 10/2026 each
    repeated it word for word.
    """
    inventur = zum_schreiben(request, pk)
    if not request.user.is_staff:
        raise PermissionDenied(nur_fuehrung)
    an = eingabe(request).get("an")
    if not isinstance(an, bool):
        raise ValidationError({"an": _("Erwartet wird true oder false.")})
    try:
        setzen(inventur, an)
    except DjangoValidationError as fehler:
        raise ValidationError({"detail": fehler.messages}) from fehler
    return Response({feld: getattr(inventur, feld)})


@api_view(["POST"])
@permission_classes([IstAktiverBenutzer])
def differenz_von_hand(request, version=None, pk=None):
    """Switch the difference by hand on or off, from the app. Only managers.

    The difference comes from the inventory management system, and whether
    it is typed in at all is decided by whoever runs the inventory, on the
    website as here. Counters type it in once it is on.
    """
    return _schalten(
        request,
        pk,
        Inventur.differenz_von_hand_setzen,
        _("Ob die Differenz von Hand eingetragen wird, entscheidet eine Führungskraft."),
        "differenz_von_hand",
    )


@api_view(["POST"])
@permission_classes([IstAktiverBenutzer])
def sperre(request, version=None, pk=None):
    """Lock the count or open it again, from the app. Only managers, as on the website.

    Whoever runs the inventory decides when counting is done. Counters see
    the lock and then only the check list.
    """
    return _schalten(
        request,
        pk,
        Inventur.sperre_setzen,
        _("Sperren und öffnen kann nur eine Führungskraft."),
        "gesperrt",
    )
