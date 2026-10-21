"""What the server checks before the counting app may write.

The same rules as when listing: counting happens only in a running
inventory, and only for what the house still carries.
"""

from http import HTTPStatus

from django.core.exceptions import ValidationError as ModellFehler
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework.exceptions import APIException
from rest_framework.generics import get_object_or_404

from inventur.models import Inventur, Inventurstatus, Zaehlposition
from katalog.models import (
    Artikelstatus,
    Behaelter,
    Hausartikel,
    Rezepturartikel,
)


def pruefen(position: Zaehlposition) -> None:
    """The model's rules apply here too, translated into a response with 400.

    'Zaehlposition.clean()' watches over location and pack fitting the
    inventory and the article. DRF does not call 'full_clean' by itself, and
    Django's 'ValidationError' uncaught would be a server error instead of an
    answer to the PWA.
    """
    try:
        position.full_clean()
    except ModellFehler as fehler:
        raise serializers.ValidationError(fehler.message_dict) from fehler


def pruefe_nicht_aktiv(artikel, inventur: Inventur) -> None:
    """A non-active article only if the app would offer it too.

    For the house article that means: an **inactive** one of a type this
    inventory counts, or a **discontinued** one whose number and product are still free in the
    house. What was delisted is sometimes still on the shelf. If the slot has
    since been reassigned, the count belongs to the new row. Ignored was never
    a product. The app does not offer that, and it must not come in through a
    stale offline state either. A recipe article hangs on its ingredients: it
    works as long as each is carried in the house. If one is only inactive, it
    works like an inactive house article only if one ingredient is due.
    """
    konkret = artikel.konkret()
    if isinstance(konkret, Rezepturartikel):
        if not konkret.aufloesbar_in(inventur.haus_id):
            raise serializers.ValidationError(
                {
                    "artikel": _(
                        "„%(name)s“ lässt sich in %(haus)s nicht auflösen. Eine Zutat hat "
                        "dort keinen geführten Hausartikel."
                    )
                    % {"name": konkret.name, "haus": inventur.haus}
                }
            )
        if not konkret.verfuegbar_in(inventur.haus_id) and not konkret.umfang_passt(
            inventur.zaehlumfang, inventur.haus_id
        ):
            raise serializers.ValidationError(
                {
                    "artikel": _("„%(name)s“ ist bei dieser Inventur nicht dran.")
                    % {"name": konkret.name}
                }
            )
        return
    if not isinstance(konkret, Hausartikel) or konkret.aktiv:
        return
    if konkret.status == Artikelstatus.EINGESTELLT:
        if (belegt := konkret.blockiert_durch()) is not None:
            raise serializers.ValidationError(
                {
                    "artikel": _(
                        "„%(name)s“ ist eingestellt, und %(haus)s führt an seiner Stelle "
                        "%(nummer)s „%(ersatz)s“."
                    )
                    % {
                        "name": konkret.name,
                        "haus": inventur.haus,
                        "nummer": belegt.artikelnummer,
                        "ersatz": belegt.name,
                    }
                }
            )
    elif konkret.status != Artikelstatus.INAKTIV:
        raise serializers.ValidationError(
            {
                "artikel": _("„%(name)s“ ist %(status)s und wird nicht mehr gezählt.")
                % {"name": konkret.name, "status": konkret.get_status_display().lower()}
            }
        )
    if not inventur.zaehlumfang.umfasst(konkret):
        raise serializers.ValidationError(
            {
                "artikel": _("„%(name)s“ ist %(status)s und bei dieser Inventur nicht dran.")
                % {"name": konkret.name, "status": konkret.get_status_display().lower()}
            }
        )


class NichtInDerZaehlung(APIException):
    """Writing outside the count: a matter of the inventory's state, not of the value.

    409 and not 400, because the counting app takes a 400 as the verdict on
    the job and drops it. An inventory taken back into preparation is counted
    again later, and the numbers from a dead spot wait for that on the device
    ('vorlaeufig'). A closed inventory is frozen for good: there the app shows
    the number with the notice, so it can be passed on.

    The answer is 'daten', not 'detail': DRF turns every value of 'detail'
    into text, and the app needs 'vorlaeufig' as a boolean. The handler in
    'api/exceptions.py' sends 'daten' as they are.
    """

    status_code = HTTPStatus.CONFLICT

    def __init__(self, inventur: Inventur, mit_namen: bool = True):
        super().__init__()
        werte = {"inventur": inventur, "status": inventur.get_status_display()}
        # Without the name where the caller was never assigned: a closed
        # inventory is found for everyone ('zum_schreiben'), and its name
        # would tell a stranger house, day and types (until 10/2026).
        detail = (
            _("„%(inventur)s“ steht auf %(status)s. Gezählt wird nur in der Zählung.")
            if mit_namen
            else _("Diese Inventur steht auf %(status)s. Gezählt wird nur in der Zählung.")
        )
        self.daten: dict[str, object] = {
            "konflikt": "status",
            "status": inventur.status,
            "vorlaeufig": inventur.status == Inventurstatus.VORBEREITUNG,
            "detail": detail % werte,
        }


class Gesperrt(APIException):
    """The count is locked: checking goes on, counting waits ('Inventur.sperre_setzen').

    Answered like an inventory in preparation, so the app keeps the job and
    sends it once the lock opens: it reads 'konflikt' and 'vorlaeufig', and
    'gesperrt' tells it that the check list goes on. Rejected, a count from a
    dead spot would be gone, although someone only has to open the lock for
    it. An app built before 2026-10-10 knows no held jobs at all and takes
    the 409 as a rejection, so a device loads the new version first.
    """

    status_code = HTTPStatus.CONFLICT

    def __init__(self, inventur: Inventur):
        super().__init__()
        self.daten: dict[str, object] = {
            "konflikt": "status",
            "status": inventur.status,
            "vorlaeufig": True,
            "gesperrt": True,
            "detail": _(
                "„%(inventur)s“ ist gesperrt. Gezählt wird erst wieder, wenn eine "
                "Führungskraft sie öffnet."
            )
            % {"inventur": inventur},
        }


def pruefe_in_der_zaehlung(inventur: Inventur) -> None:
    """Writing happens only during the count. Before it nothing is released, after it frozen.

    That is all the check list asks: its notes and differences go on when the
    count is locked, because that is when checking begins.
    """
    if inventur.status != Inventurstatus.ZAEHLUNG:
        raise NichtInDerZaehlung(inventur)


def pruefe_zaehlbar(inventur: Inventur) -> None:
    """Counting happens only during the count, and only while it is not locked."""
    pruefe_in_der_zaehlung(inventur)
    if inventur.gesperrt:
        raise Gesperrt(inventur)


def pruefe_zugeordnet(inventur: Inventur, benutzer, unbekannt: dict) -> None:
    """Only who counts here writes here, and a stranger learns nothing about the inventory.

    Asked before the state, because the answer about the state names the
    inventory. Until 10/2026 an account assigned to nothing learned house, day
    and types of every inventory in preparation or locked over its number. A
    stranger now hears what a number that does not exist gets ('unbekannt').
    Closing clears who counted, so a closed inventory says only that it is
    closed, without its name, as on the paths of the check list
    ('views.review.zum_schreiben'). A device that still holds a number for it learns
    that waiting makes no sense.
    """
    if inventur.darf_zaehlen(benutzer):
        return
    if inventur.status == Inventurstatus.ABGESCHLOSSEN:
        raise NichtInDerZaehlung(inventur, mit_namen=False)
    raise serializers.ValidationError(unbekannt)


def zaehlbar_unter_sperre(inventur: Inventur) -> Inventur:
    """The inventory read fresh under a lock, and only then checked as countable.

    For every write that creates, revives or removes a row. The check in
    'validate' ran before any lock, and a closing may commit in between:
    it locks the inventory's row and holds it until the frozen result is
    written ('Inventur.abschliessen'). A write that arrives meanwhile waits
    here and then sees the closed state, instead of landing in an inventory
    whose rows are gone. Until 10/2026 a new row from the app got a 201
    during the closing and was missing from the result. A quantity or a
    weighing needs no such lock: it locks its row, and the closing locks
    every row before it reads ('Inventur.zum_abschluss_sperren'). Called
    inside 'transaction.atomic()'. The lock is a no-op under SQLite, as in
    'vermerk_aendern'.

    'FOR NO KEY UPDATE' and not 'FOR UPDATE': it keeps every other writer of
    the inventory waiting all the same, but not the check of a foreign key
    that points to it. A merge or a split that touches a row twice checks
    every key of that row at its commit, the inventory's too. Under
    'FOR UPDATE' a new row that waited for the merge's article
    ('katalog.models.artikel_sperren') held the inventory against it, and
    the two waited for each other until PostgreSQL broke one off (until
    10/2026, a server error in the app).
    """
    frisch = get_object_or_404(Inventur.objects.select_for_update(no_key=True), pk=inventur.pk)
    pruefe_zaehlbar(frisch)
    return frisch


def behaelter_erlaubt(position: Zaehlposition, behaelter: Behaelter) -> bool:
    """Whether weighing in this container is allowed on this row, by the model's rule."""
    return position.behaelter_zur_wahl().filter(pk=behaelter.pk).exists()
