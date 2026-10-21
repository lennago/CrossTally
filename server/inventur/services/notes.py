"""Remarks on articles of an inventory: note, tick and difference by hand."""

import hashlib
import re
from collections.abc import Callable, Collection, Iterable
from decimal import Decimal
from typing import NamedTuple

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import BooleanField, ExpressionWrapper, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from katalog.models import (
    Artikelstatus,
    Hausartikel,
    artikel_sperren,
)

from ..models import (
    Inventur,
    Inventurnotiz,
    Inventurstatus,
)
from .results import betroffene_hausartikel

#: What a row of the check list showed when it was built: note and tick.
Vorher = tuple[str, bool]

#: What a remark holds after a change: note, tick and difference by hand.
Werte = tuple[str, bool, Decimal | None]

#: The first letter of a remarks' state: taken with the notes' text (the check
#: list) or without it (the run, which shows no notes).
MIT_NOTIZEN, OHNE_NOTIZEN = "n", "z"

#: What a remarks' state looks like: its first letter, then 24 hex digits.
_BEMERKUNGSSTAND = re.compile(rf"[{MIT_NOTIZEN}{OHNE_NOTIZEN}][0-9a-f]{{24}}")

#: The longest note, in characters, as it is stored: without the spaces around
#: it. The check list hands every note along whole, in the app and on the
#: website, and the sheet prints it. Until 10/2026 a note of two megabytes was
#: stored and came back with every check list. The column itself has no limit,
#: the writers keep to this one ('vermerk_aendern', 'notizen_speichern', the API).
NOTIZ_HOECHSTLAENGE = 2000

#: What a single longer note hears. In braces: the API fills them in as well.
NOTIZ_ZU_LANG = gettext_lazy("Eine Notiz hat höchstens {max_length} Zeichen.")


class _Unveraendert:
    """Stands for a field nobody sent. 'None' is a value of its own: an emptied difference."""

    def __repr__(self) -> str:
        return "UNVERAENDERT"


UNVERAENDERT = _Unveraendert()

#: What a row of the page sends: note, tick and difference, each perhaps unchanged, and what
#: it showed of a note or a difference it changed.
Gesendet = tuple[
    str | _Unveraendert,
    bool | _Unveraendert,
    Decimal | None | _Unveraendert,
    str | _Unveraendert,
    Decimal | None | _Unveraendert,
]


class InventurAbgeschlossen(ValidationError):
    """The inventory was closed while a write was on its way: a remark, the counters.

    Carries the fresh row.
    """

    def __init__(self, inventur: Inventur):
        super().__init__(_("Diese Inventur ist abgeschlossen."))
        self.inventur = inventur


class VermerkVeraltet(ValidationError):
    """What the device saw of this field is no longer what is stored: someone wrote since.

    Carries which field and what is stored now, so the answer can say it.
    """

    def __init__(self, feld: str, gespeichert: str | Decimal | None):
        super().__init__(_("Inzwischen hat jemand anderes diesen Eintrag geändert."))
        self.feld = feld
        self.gespeichert = gespeichert


class NotizenVeraltet(ValidationError):
    """Rows of the check list left as stored. The rest is saved.

    Someone else wrote in them while the page stood open. Or 'abgewiesen'
    says why a row was refused, per article: its article takes no remark
    here any more ('_ohne_vermerk'), its note is too long. Raised after the
    save, so the other rows stay stored. Carries the house articles, the
    messages name them.
    """

    def __init__(self, hausartikel_ids: list[int], abgewiesen: dict[int, str] | None = None):
        abgewiesen = abgewiesen or {}
        meldungen = []
        if hausartikel_ids:
            namen = Hausartikel.objects.filter(pk__in=hausartikel_ids).values_list(
                "name", flat=True
            )
            meldungen.append(
                _(
                    "Nicht gespeichert, weil inzwischen jemand anderes dort etwas eingetragen "
                    "hat: %(namen)s. Neu laden zeigt, was jetzt dort steht."
                )
                % {"namen": ", ".join(sorted(namen))}
            )
        meldungen.extend(abgewiesen[pk] for pk in sorted(abgewiesen))
        super().__init__(meldungen)
        self.hausartikel_ids = [*hausartikel_ids, *sorted(abgewiesen)]


class Gesichert(NamedTuple):
    """What saving the check list leaves behind ('notizen_speichern')."""

    #: How many remarks the inventory carries in total afterwards.
    anzahl: int
    #: The remarks' state the page carries on ('bemerkungsstand'): after its
    #: own writes the new one, where it had seen the state they were written
    #: into. Otherwise the one it sent, and closing finds that outdated. None
    #: where the page sent none.
    bemerkungen: str | None


def bemerkungsstand(inventur: Inventur, *, mit_notizen: bool = True) -> str:
    """An id of what the remarks of an inventory freeze: ticks, differences by hand, notes.

    'zaehlstand' sees only the count positions. A difference by hand, a
    tick for the target ('soll_statt_ist') or a note written in the app
    after the check list was loaded went into the result unseen (until
    10/2026). Closing compares this id as it compares the count state
    ('abschliessen'). Taken from the content and not from the time of the
    change: merging two articles moves remarks without touching it. Whether
    the inventory takes the difference by hand belongs to it, because the
    switch decides which number is frozen. The run shows no notes, so its id
    leaves their text out ('mit_notizen'), and the first letter says which
    of the two an id is. Whether the remark's article lies in "Not countable"
    belongs to it as well: the check list leaves such a remark out
    ('Inventur.vermerke'), and an article brought back between the page's
    load and the closing would otherwise freeze a remark the page never
    showed.
    """
    notizen = Inventurnotiz.objects.filter(inventur=inventur).annotate(
        ignoriert=ExpressionWrapper(
            Q(hausartikel__status=Artikelstatus.IGNORIERT), output_field=BooleanField()
        )
    )
    felder = ["hausartikel_id", "soll_statt_ist", "differenz", "ignoriert"]
    if mit_notizen:
        felder.append("text")
    else:
        # Without its text a remark that holds nothing else is none at all.
        notizen = notizen.filter(Q(soll_statt_ist=True) | Q(differenz__isnull=False))
    inhalt = list(notizen.order_by("hausartikel_id").values_list(*felder))
    pruefsumme = hashlib.sha256(repr((inventur.differenz_von_hand, inhalt)).encode()).hexdigest()
    return (MIT_NOTIZEN if mit_notizen else OHNE_NOTIZEN) + pruefsumme[:24]


def ist_bemerkungsstand(wert: str) -> bool:
    """Whether 'wert' has the form 'bemerkungsstand' gives. Sent by a page, it is from outside."""
    return _BEMERKUNGSSTAND.fullmatch(wert) is not None


def bemerkungsstand_wie(inventur: Inventur, gesehen: str) -> str:
    """The remarks' state now, taken as 'gesehen' was: with the notes' text or without it."""
    return bemerkungsstand(inventur, mit_notizen=not gesehen.startswith(OHNE_NOTIZEN))


def _gesperrt(inventur: Inventur, pruefen: Callable[[Inventur], object] | None = None) -> Inventur:
    """The inventory's row, locked for the write and checked against a closing.

    Two devices on the same article then take turns instead of one
    overwriting the other's field, and a closing in between is seen here
    instead of the remark landing in a closed inventory after its notes were
    frozen. Closing locks the same row ('Inventur.abschliessen'). A no-op
    under SQLite. Once per write: the check list locks once for all rows of
    its page (until 10/2026 once per row, ten queries each).

    Without the key, as the closing and the app lock it. A product saved
    with a new name and moved into "Not countable" touches the inventory's
    rows twice, deletes the article's remarks and checks the rows' keys
    against the inventory at its commit. Under 'FOR UPDATE' that check
    waited for this lock while the remark waited for the row being deleted,
    and one of the two ended in a server error (until 10/2026). An inventory
    deleted while the remark waited is a 404, not a server error.

    'pruefen' is what the caller asks of the locked row on top ('vermerk_aendern').
    """
    gesperrt = get_object_or_404(Inventur.objects.select_for_update(no_key=True), pk=inventur.pk)
    if gesperrt.status == Inventurstatus.ABGESCHLOSSEN:
        raise InventurAbgeschlossen(gesperrt)
    if pruefen is not None:
        pruefen(gesperrt)
    return gesperrt


def _zu_lang(text: str | _Unveraendert) -> bool:
    """Whether a sent note is longer than it may be stored ('NOTIZ_HOECHSTLAENGE')."""
    return isinstance(text, str) and len(text.strip()) > NOTIZ_HOECHSTLAENGE


def _zu_lange_notizen(geaendert: dict[int, Gesendet]) -> dict[int, str]:
    """The rows of the page whose note is too long, each with the sentence that names it."""
    zu_lang = [pk for pk, zeile in geaendert.items() if _zu_lang(zeile[0])]
    if not zu_lang:
        return {}
    return {
        pk: _("Die Notiz zu „%(name)s“ hat mehr als %(anzahl)s Zeichen. Sie ist nicht gespeichert.")
        % {"name": name, "anzahl": NOTIZ_HOECHSTLAENGE}
        for pk, name in Hausartikel.objects.filter(pk__in=zu_lang).values_list("pk", "name")
    }


def _bringt_inhalt(
    text: str | _Unveraendert,
    soll_statt_ist: bool | _Unveraendert,
    differenz: Decimal | None | _Unveraendert,
) -> bool:
    """Whether a write sends something closing would freeze. Emptying a field freezes nothing."""
    return (
        (isinstance(text, str) and bool(text.strip()))
        or soll_statt_ist is True
        or isinstance(differenz, Decimal)
    )


def _ohne_vermerk(
    inventur: Inventur,
    hausartikel_ids: Collection[int],
    gespeicherte: dict[int, Inventurnotiz],
) -> dict[int, str]:
    """Why these articles take no remark in this inventory, per article. The others are not in it.

    A remark freezes a row at closing ('Inventur._ergebnisse'). Not for an
    article in "Not countable": moving its product there deleted its
    remarks on purpose ('katalog.hausartikel_status.aufraeumen'). And not
    for one whose type the inventory does not count and that has no row in
    it, not even through a recipe ('betroffene_hausartikel'): the check list
    does not show it. Until 10/2026 the app and a page loaded before wrote
    into either, and closing froze a row for it. A remark stored already
    stays changeable, the check list shows it.

    Asked under the locks of inventory and article ('artikel_sperren'): a
    product that moves into "Not countable" meanwhile is waited for and seen.
    """
    arten = inventur.zaehlumfang.arten
    gruende: dict[int, str] = {}
    nicht_dran: dict[int, str] = {}
    for pk, name, status, art in Hausartikel.objects.filter(
        pk__in=list(hausartikel_ids)
    ).values_list("pk", "name", "status", "inventurart_id"):
        if status == Artikelstatus.IGNORIERT:
            gruende[pk] = _(
                "„%(name)s“ ist nicht zählbar. Eine Notiz, „Soll zählt“ oder eine Differenz "
                "gibt es dafür nicht."
            ) % {"name": name}
        elif art not in arten and pk not in gespeicherte:
            nicht_dran[pk] = name
    if nicht_dran:
        mit_zeile = betroffene_hausartikel(inventur)
        for pk, name in nicht_dran.items():
            if pk not in mit_zeile:
                gruende[pk] = _(
                    "„%(name)s“ ist bei dieser Inventur nicht dran. Eine Notiz, „Soll zählt“ "
                    "oder eine Differenz gibt es dafür hier nicht."
                ) % {"name": name}
    return gruende


def _gespeicherte(inventur: Inventur, hausartikel_ids: Iterable[int]) -> dict[int, Inventurnotiz]:
    """The stored remarks of these articles, read once after the lock."""
    return {
        notiz.hausartikel_id: notiz
        for notiz in Inventurnotiz.objects.filter(
            inventur=inventur, hausartikel_id__in=list(hausartikel_ids)
        ).order_by()
    }


def _neue_werte(
    inventur: Inventur,
    gespeichert: Inventurnotiz | None,
    *,
    text: str | _Unveraendert = UNVERAENDERT,
    soll_statt_ist: bool | _Unveraendert = UNVERAENDERT,
    differenz: Decimal | None | _Unveraendert = UNVERAENDERT,
    text_vorher: str | _Unveraendert = UNVERAENDERT,
    differenz_vorher: Decimal | None | _Unveraendert = UNVERAENDERT,
) -> Werte:
    """The sent fields new, the others as stored. Checked, not yet written.

    'inventur' is the locked row: whether it takes the difference by hand
    is read where nobody can switch it in between.
    """
    if isinstance(differenz, Decimal) and not inventur.differenz_von_hand:
        raise ValidationError(
            _(
                "Bei dieser Inventur wird die Differenz nicht von Hand eingetragen. Sie ergibt "
                "sich aus dem Sollbestand."
            )
        )
    neuer_text = gespeichert.text if gespeichert else ""
    neuer_haken = gespeichert.soll_statt_ist if gespeichert else False
    neue_differenz = gespeichert.differenz if gespeichert else None
    # Where the sent value already stands, the own job came twice: its answer
    # was lost in a dead spot. That is no conflict, as with a quantity
    # ('gleiche_menge' in the API). Until 10/2026 the second send was listed
    # as not accepted, with the device's own entry as the reason.
    if (
        not isinstance(text_vorher, _Unveraendert)
        and text_vorher.strip() != neuer_text
        and (isinstance(text, _Unveraendert) or text.strip() != neuer_text)
    ):
        raise VermerkVeraltet("notiz", neuer_text)
    if (
        not isinstance(differenz_vorher, _Unveraendert)
        and differenz_vorher != neue_differenz
        and (isinstance(differenz, _Unveraendert) or differenz != neue_differenz)
    ):
        raise VermerkVeraltet("differenz", neue_differenz)
    if not isinstance(text, _Unveraendert):
        neuer_text = text.strip()
    if not isinstance(soll_statt_ist, _Unveraendert):
        neuer_haken = soll_statt_ist
    if not isinstance(differenz, _Unveraendert):
        neue_differenz = differenz
    return neuer_text, neuer_haken, neue_differenz


def _schreiben(
    inventur: Inventur, gespeicherte: dict[int, Inventurnotiz], neu: dict[int, Werte]
) -> dict[int, Inventurnotiz]:
    """Writes the remarks in at most three statements, however many there are.

    A remark that keeps nothing goes: neither text nor tick nor difference
    means there was nothing. 'bulk_update' passes 'save()', so the time of
    the change is set here. Back come the remarks that remain.
    """
    jetzt = timezone.now()
    weg: list[int] = []
    anlegen: list[Inventurnotiz] = []
    aendern: list[Inventurnotiz] = []
    bleiben: dict[int, Inventurnotiz] = {}
    for hausartikel_id, (text, haken, differenz) in neu.items():
        notiz = gespeicherte.get(hausartikel_id)
        if not text and not haken and differenz is None:
            if notiz is not None:
                weg.append(notiz.pk)
            continue
        if notiz is None:
            notiz = Inventurnotiz(inventur=inventur, hausartikel_id=hausartikel_id)
            anlegen.append(notiz)
        else:
            aendern.append(notiz)
        notiz.text, notiz.soll_statt_ist, notiz.differenz = text, haken, differenz
        notiz.geaendert_am = jetzt
        bleiben[hausartikel_id] = notiz
    if weg:
        Inventurnotiz.objects.filter(pk__in=weg).delete()
    if anlegen:
        Inventurnotiz.objects.bulk_create(anlegen)
    if aendern:
        Inventurnotiz.objects.bulk_update(
            aendern, ["text", "soll_statt_ist", "differenz", "geaendert_am"]
        )
    return bleiben


@transaction.atomic
def vermerk_aendern(
    inventur: Inventur,
    hausartikel_id: int,
    *,
    text: str | _Unveraendert = UNVERAENDERT,
    soll_statt_ist: bool | _Unveraendert = UNVERAENDERT,
    differenz: Decimal | None | _Unveraendert = UNVERAENDERT,
    text_vorher: str | _Unveraendert = UNVERAENDERT,
    differenz_vorher: Decimal | None | _Unveraendert = UNVERAENDERT,
    pruefen: Callable[[Inventur], object] | None = None,
) -> Inventurnotiz | None:
    """Changes the given fields of one article's remark and keeps the others as stored.

    The counting app sends one field at a time: a note, or a difference. It
    must not empty what someone else wrote into the other one in the
    meantime. A remark that keeps nothing goes, as in 'notizen_speichern'.

    A difference only where the inventory takes it by hand: everywhere else
    it computes itself from the target. Emptying is always allowed. The
    article has to belong to the inventory's house, the number comes from
    outside, and has to be one the check list can show ('_ohne_vermerk').
    Back comes the remark, or None where nothing is left.

    'text_vorher' and 'differenz_vorher' are what the device showed when the
    person typed. Given, and something else is stored by now, someone wrote
    in between, and their entry is not overwritten ('VermerkVeraltet'). What
    is stored may also be the sent value itself: then the same job came
    twice, and it goes through. The website compares the same way
    ('notizen_speichern'). Until 10/2026 a note from a dead spot overwrote a
    younger one without a word.

    'pruefen' is asked of the inventory under its lock, before anything is
    read. The app passes its rule there: it writes remarks only in the
    count, and the answer to a job that came too early or too late is the
    caller's ('api.checks.pruefe_in_der_zaehlung'). Asked before the lock
    only, a remark checked a moment before "Back into preparation" landed in
    the preparation (until 10/2026). The check list of the website writes
    there as well, its remarks are frozen once the count is closed.
    """
    if _zu_lang(text):
        raise ValidationError(str(NOTIZ_ZU_LANG).format(max_length=NOTIZ_HOECHSTLAENGE))
    if not Hausartikel.objects.filter(pk=hausartikel_id, haus_id=inventur.haus_id).exists():
        raise ValidationError(_("Dieser Artikel gehört nicht zum Haus der Inventur."))
    gesperrt = _gesperrt(inventur, pruefen)
    # After the inventory, in the order of every writer ('orte_und_artikel_sperren').
    artikel_sperren([hausartikel_id])
    gespeicherte = _gespeicherte(gesperrt, [hausartikel_id])
    if _bringt_inhalt(text, soll_statt_ist, differenz) and (
        grund := _ohne_vermerk(gesperrt, [hausartikel_id], gespeicherte).get(hausartikel_id)
    ):
        raise ValidationError(grund)
    werte = _neue_werte(
        gesperrt,
        gespeicherte.get(hausartikel_id),
        text=text,
        soll_statt_ist=soll_statt_ist,
        differenz=differenz,
        text_vorher=text_vorher,
        differenz_vorher=differenz_vorher,
    )
    return _schreiben(gesperrt, gespeicherte, {hausartikel_id: werte}).get(hausartikel_id)


def notizen_speichern(
    inventur: Inventur,
    eingaben: dict[int, str],
    uebernahmen: set[int] | None = None,
    vorher: dict[int, Vorher] | None = None,
    differenzen: dict[int, tuple[Decimal | None, Decimal | None]] | None = None,
    *,
    bemerkungen: str | None = None,
) -> Gesichert:
    """Writes the remarks that were in the form, and only those.

    Only those: the check list can be searched and filtered, so a submit often
    carries only an excerpt. What did not come along stays untouched instead
    of being deleted.

    **Only what was changed on the page counts** where the page says what it
    showed ('vorher'). A form can be older than what is stored: a second tab,
    a page the browser brought back from its history with the fields as they
    were. Its empty fields would otherwise delete every note written
    elsewhere in the meantime. On 2026-10-12 a closing lost every note of an
    inventory that way. Note, tick and difference are compared separately, so
    ticking a box does not carry an outdated text along. A row without
    'vorher' comes from a page built before that and is written as it stands.

    **And only where nobody wrote since**, as for the counting app
    ('vermerk_aendern'). A note or a difference changed here whose stored
    value is no longer what the row showed was written elsewhere meanwhile:
    in the app, in another tab. Such a row is left as stored, the others are
    saved, and 'NotizenVeraltet' names it afterwards. Until 10/2026 the page
    overwrote it without a word. The same goes for a row whose article takes
    no remark any more, as in the app ('_ohne_vermerk'), and for a row whose
    note is too long ('NOTIZ_HOECHSTLAENGE'), before any lock.

    'differenzen' are the differences by hand, per article as (typed, shown).
    The field exists only on pages built since, so it always says what it
    showed.

    A remark without content vanishes. Neither text nor tick nor difference
    means: there was nothing. Otherwise closing would freeze nothing but empty
    rows only because something once stood there. Foreign article numbers are
    discarded: the keys come from the form and thus from outside.

    'uebernahmen' are the articles for which the target stock should apply at
    closing. That is only evaluated for rows that were in the form too.

    **The page's own writes are no news to it.** It sends the remarks' state
    it carries ('bemerkungen', 'bemerkungsstand'). Where that still is the
    state under the lock, the page gets the state after its writes back and
    carries that on. Otherwise someone else wrote in between, it keeps the
    one it had, and closing stops ('abschliessen').

    Back come how many remarks the inventory carries in total afterwards and
    the remarks' state for the page ('Gesichert').
    """
    uebernahmen = uebernahmen or set()
    vorher = vorher or {}
    # Differences from a page built before someone switched the difference by
    # hand off are stale. Taken along, the first of them would throw the
    # whole save back, notes of the other rows included (the save is one
    # transaction). The view says that they were left out.
    differenzen = (differenzen or {}) if inventur.differenz_von_hand else {}
    erlaubt = set(
        Hausartikel.objects.filter(
            haus_id=inventur.haus_id, pk__in=set(eingaben) | set(differenzen)
        ).values_list("pk", flat=True)
    )
    geaendert = {
        hausartikel_id: zeile
        for hausartikel_id in erlaubt
        if (zeile := _geaendert(hausartikel_id, eingaben, uebernahmen, vorher, differenzen))
    }
    zu_lang = _zu_lange_notizen(geaendert)
    veraltet, ohne_vermerk, getragen = _geaendertes_schreiben(
        inventur,
        {pk: zeile for pk, zeile in geaendert.items() if pk not in zu_lang},
        bemerkungen,
    )
    if veraltet or zu_lang or ohne_vermerk:
        raise NotizenVeraltet(veraltet, zu_lang | ohne_vermerk)
    return Gesichert(Inventurnotiz.objects.filter(inventur=inventur).count(), getragen)


def _geaendert(
    hausartikel_id: int,
    eingaben: dict[int, str],
    uebernahmen: set[int],
    vorher: dict[int, Vorher],
    differenzen: dict[int, tuple[Decimal | None, Decimal | None]],
) -> Gesendet | None:
    """What a row of the page changed, with what it showed of it. None where it changed nothing."""
    neuer_text: str | _Unveraendert = UNVERAENDERT
    text_vorher: str | _Unveraendert = UNVERAENDERT
    neuer_haken: bool | _Unveraendert = UNVERAENDERT
    neue_differenz: Decimal | None | _Unveraendert = UNVERAENDERT
    differenz_vorher: Decimal | None | _Unveraendert = UNVERAENDERT
    if hausartikel_id in eingaben:
        text = eingaben[hausartikel_id].strip()
        uebernehmen = hausartikel_id in uebernahmen
        if hausartikel_id not in vorher:
            neuer_text, neuer_haken = text, uebernehmen
        else:
            gezeigt, uebernehmen_vorher = vorher[hausartikel_id]
            if text != gezeigt.strip():
                neuer_text, text_vorher = text, gezeigt
            if uebernehmen != uebernehmen_vorher:
                neuer_haken = uebernehmen
    if hausartikel_id in differenzen:
        eingetragen, gezeigt_als = differenzen[hausartikel_id]
        if eingetragen != gezeigt_als:
            neue_differenz, differenz_vorher = eingetragen, gezeigt_als
    if all(isinstance(wert, _Unveraendert) for wert in (neuer_text, neuer_haken, neue_differenz)):
        return None
    return neuer_text, neuer_haken, neue_differenz, text_vorher, differenz_vorher


@transaction.atomic
def _geaendertes_schreiben(
    inventur: Inventur, geaendert: dict[int, Gesendet], bemerkungen: str | None
) -> tuple[list[int], dict[int, str], str | None]:
    """Writes what the rows changed. Back come the rows it left, and why.

    Those someone else wrote in since, and those whose article takes no
    remark here ('_ohne_vermerk'). One lock, one read and at most three
    writes for the whole page. Only where something changed, as before: a
    page that changed nothing does not wait for a closing and does not hear
    that it happened. With them comes the remarks' state the page carries
    on ('Gesichert.bemerkungen'), compared and taken anew under the same lock.
    """
    if not geaendert:
        return [], {}, bemerkungen
    gesperrt = _gesperrt(inventur)
    # After the inventory, in the order of every writer ('orte_und_artikel_sperren').
    artikel_sperren(geaendert)
    gesehen = bemerkungen is not None and bemerkungen == bemerkungsstand_wie(gesperrt, bemerkungen)
    gespeicherte = _gespeicherte(gesperrt, geaendert)
    ohne_vermerk = _ohne_vermerk(
        gesperrt,
        [pk for pk, zeile in geaendert.items() if _bringt_inhalt(*zeile[:3])],
        gespeicherte,
    )
    neu: dict[int, Werte] = {}
    veraltet: list[int] = []
    for hausartikel_id, zeile in geaendert.items():
        if hausartikel_id in ohne_vermerk:
            continue
        text, haken, differenz, text_vorher, differenz_vorher = zeile
        try:
            neu[hausartikel_id] = _neue_werte(
                gesperrt,
                gespeicherte.get(hausartikel_id),
                text=text,
                soll_statt_ist=haken,
                differenz=differenz,
                text_vorher=text_vorher,
                differenz_vorher=differenz_vorher,
            )
        except VermerkVeraltet:
            veraltet.append(hausartikel_id)
    _schreiben(gesperrt, gespeicherte, neu)
    if gesehen and bemerkungen is not None:
        bemerkungen = bemerkungsstand_wie(gesperrt, bemerkungen)
    return veraltet, ohne_vermerk, bemerkungen
