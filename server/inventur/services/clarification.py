"""The clarification of the target stock: the rows the file left open."""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from katalog.formats import HOECHSTMENGE
from katalog.models import (
    Artikelstatus,
    Hausartikel,
    menge_kurz,
    zaehlpositionen_sperren,
)

from ..models import (
    Inventur,
    Inventurstatus,
    Sollbestand,
    Sollgrund,
    Sollzeile,
    orte_und_artikel_sperren,
)
from .target_stock import Sollbestandbericht, Zuordner

#: Why the target is clarified in preparation only.
KLAERUNG_NUR_IN_DER_VORBEREITUNG = gettext_lazy(
    "Der Sollbestand wird in der Vorbereitung geklärt. Danach halten die Zähl-Apps "
    "ihre Zeilen, und die Stammdaten sollen sich nicht mehr unter ihnen bewegen."
)


class NichtInDerVorbereitung(ValidationError):
    """Counting started while an answer of the clarification was on its way.

    Carries the fresh row, so the page can say where the inventory stands now.
    """

    def __init__(self, inventur: Inventur):
        super().__init__(KLAERUNG_NUR_IN_DER_VORBEREITUNG)
        self.inventur = inventur


def _vorbereitung_sperren(inventur_id: int) -> Inventur:
    """The inventory, locked without the key until the transaction ends, while in preparation.

    Every answer writes the target, most change master data as well. The
    page looked at the status before, on the inventory as it loaded it, and
    nothing was locked (until 10/2026): two answers to one row both added its
    target, a new file came in between reading and writing, and an answer
    landed after the start of counting. The upload locks the same row
    ('soll_darf_herein'), the start writes it. Whoever comes second waits
    and then sees the first. First of all locks, in the order of every
    writer ('orte_und_artikel_sperren'). Deleted meanwhile, it is a 404.
    """
    gesperrt = get_object_or_404(Inventur.objects.select_for_update(no_key=True), pk=inventur_id)
    if gesperrt.status != Inventurstatus.VORBEREITUNG:
        raise NichtInDerVorbereitung(gesperrt)
    return gesperrt


def _zeile_unter_sperre(zeile: Sollzeile, *, mit_artikeln: bool = False) -> Sollzeile:
    """The row as it stands under the inventory's lock, read anew with its article.

    The page found it before any lock. Answered in another window or
    replaced by a new file since, it is gone, and the answer says so.
    'mit_artikeln' locks the house's locations and articles before the row
    is read, for an answer that changes its article.
    """
    inventur = _vorbereitung_sperren(zeile.inventur_id)
    if mit_artikeln:
        orte_und_artikel_sperren([inventur.haus_id])
    frisch = Sollzeile.objects.select_related("hausartikel").filter(pk=zeile.pk).first()
    if frisch is None:
        raise ValidationError(
            _(
                "Zeile %(artikelnummer)s ist inzwischen beantwortet oder mit einer neuen Datei "
                "ersetzt. Hier steht, was jetzt offen ist."
            )
            % {"artikelnummer": zeile.artikelnummer}
        )
    frisch.inventur = inventur
    return frisch


@dataclass
class Sollklaerung:
    """What the target stock file left open and what it lacks."""

    inaktive: list[Sollzeile] = field(default_factory=list)
    eingestellte: list[Sollzeile] = field(default_factory=list)
    unbekannte: list[Sollzeile] = field(default_factory=list)
    #: Target stocks whose designation reads differently in the file. The
    #: number may have been reassigned.
    abweichende_namen: list[Sollbestand] = field(default_factory=list)
    #: Active and due, but without target, neither from the file nor by hand.
    ohne_soll: list[Hausartikel] = field(default_factory=list)
    #: Per discontinued row (by its pk) the active article the house now
    #: carries its product as. Its target can go there ('sollzeile_dazurechnen').
    nachfolger: dict[int, Hausartikel] = field(default_factory=dict)

    @property
    def eingestellte_mit_nachfolger(self) -> list[tuple[Sollzeile, Hausartikel | None]]:
        """The discontinued rows for the page, each with its successor or 'None'."""
        return [(zeile, self.nachfolger.get(zeile.pk)) for zeile in self.eingestellte]

    @property
    def leer(self) -> bool:
        return not (
            self.inaktive
            or self.eingestellte
            or self.unbekannte
            or self.abweichende_namen
            or self.ohne_soll
        )

    @property
    def offene_zeilen(self) -> int:
        """The rows of the file still waiting for an answer: the first three blocks."""
        return len(self.inaktive) + len(self.eingestellte) + len(self.unbekannte)


def sollklaerung(inventur: Inventur) -> Sollklaerung:
    """The open questions about the target stock, computed from the current state.

    "Without target" only exists once some target is there: before that
    everyone lacked it, and the question would be on every article. A target
    by hand counts like one from the file (decision of 2026-09-23): whoever
    confirms has a target, and the question concerns the articles that have none.
    Not among them is the successor of a discontinued row: the answer up there
    gives it the old number's target, and "set inactive" next to it would
    contradict that.
    """
    zeilen = list(
        inventur.sollzeilen.select_related(
            # With unit and product: the page writes the unit after every
            # quantity and the product's name before the house's.
            "hausartikel__basiseinheit",
            "hausartikel__ware",
            "hausartikel__haus",
        )
    )
    klaerung = Sollklaerung(
        inaktive=[z for z in zeilen if z.grund == Sollgrund.INAKTIV],
        eingestellte=[z for z in zeilen if z.grund == Sollgrund.EINGESTELLT],
        unbekannte=[z for z in zeilen if z.grund == Sollgrund.UNBEKANNT],
    )
    klaerung.nachfolger = _nachfolger(inventur.haus_id, klaerung.eingestellte)
    sollbestaende = inventur.sollbestaende_im_umfang().select_related(
        "hausartikel__basiseinheit", "hausartikel__ware"
    )
    klaerung.abweichende_namen = [
        soll
        for soll in sollbestaende
        if soll.name_in_datei and soll.name_in_datei.casefold() != soll.hausartikel.name.casefold()
    ]
    if zeilen or inventur.sollbestaende.exists():
        klaerung.ohne_soll = list(
            Hausartikel.objects.filter(haus=inventur.haus, aktiv=True)
            .filter(inventur.zaehlumfang.hausartikel())
            .exclude(sollbestaende__inventur=inventur)
            .exclude(pk__in=[satz.pk for satz in klaerung.nachfolger.values()])
            .select_related("basiseinheit", "ware", "warengruppe")
            .order_by("artikelnummer")
        )
    return klaerung


def _nachfolger(haus_id: int, zeilen: list[Sollzeile]) -> dict[int, Hausartikel]:
    """Per discontinued row the active article of its product in the house, where there is one.

    The system gave the product a new number and still books on the old one.
    What it carries there stands on the shelf as the active article. At most
    one per product, the partial index sees to that. One query for all rows.
    """
    waren = {zeile.hausartikel.ware_id for zeile in zeilen if zeile.hausartikel is not None}
    if not waren:
        return {}
    aktive = {
        satz.ware_id: satz
        for satz in Hausartikel.objects.filter(haus_id=haus_id, ware_id__in=waren, aktiv=True)
    }
    nachfolger = {}
    for zeile in zeilen:
        if zeile.hausartikel is None:
            continue
        satz = aktive.get(zeile.hausartikel.ware_id)
        # Brought back in the meantime, the row would be its own successor.
        if satz is not None and satz.pk != zeile.hausartikel_id:
            nachfolger[zeile.pk] = satz
    return nachfolger


@transaction.atomic
def sollzeile_aktivieren(zeile: Sollzeile) -> int:
    """Set the article active again, adopt the target, catch up on the count positions.

    Its rows come back too where "Set inactive" marked them as deleted in
    this inventory ('ohne_soll_ausscheiden'): a new row cannot arise beside a
    marked one. Until 10/2026 the article was active again and had no row in
    the app. Returns how many count positions arose or came back. If
    activating does not work (number or product taken in the meantime), the
    row stays and the message comes back as 'ValidationError'.
    """
    # Before the article changes: activating locks it, and the rows that follow
    # lock the house's locations and then its articles. Activated first, two
    # rows of one house could wait for each other (until 10/2026).
    zeile = _zeile_unter_sperre(zeile, mit_artikeln=True)
    artikel = zeile.hausartikel
    if artikel is None:
        raise ValidationError(
            _("Zu dieser Zeile gibt es keinen Artikel, der sich aktivieren ließe.")
        )
    artikel.aktivieren()
    Sollbestand.objects.update_or_create(
        inventur=zeile.inventur,
        hausartikel=artikel,
        defaults={"menge_basiseinheit": zeile.menge_basiseinheit, "name_in_datei": zeile.name},
    )
    zeile.delete()
    return zeile.inventur.zaehlpositionen_anlegen(wiederbeleben_fuer={artikel.pk})


@transaction.atomic
def sollzeile_dazurechnen(zeile: Sollzeile) -> Hausartikel:
    """The target of a discontinued number onto the active article of its product.

    The system still books on the old number, the house counts the product as
    the new article. Counted is one thing, so the target is the sum, in the
    unit of the active one. From now on the old row counts as it, the way a
    merged duplicate does ('Hausartikel.zaehlt_als'): the next upload adds its
    stock without asking, and the article import no longer offers it for
    bringing back. Without that the question came back with every file.

    Returns the article that took the target. If there is none (the product
    has no other active article in the house), the row stays and the message
    comes back as 'ValidationError'.

    Read under the locks, as the articles change: a second window that
    answered the same row first leaves it gone, and its target was not added
    twice (until 10/2026).
    """
    zeile = _zeile_unter_sperre(zeile, mit_artikeln=True)
    alter = zeile.hausartikel
    ziel = _nachfolger(alter.haus_id, [zeile]).get(zeile.pk) if alter is not None else None
    if alter is None or ziel is None:
        raise ValidationError(
            _("Diese Ware führt das Haus unter keiner anderen Nummer aktiv. Das Soll bleibt offen.")
        )
    dazu = ziel.in_hauseinheit(alter.in_wareneinheit(zeile.menge_basiseinheit))
    soll, _angelegt = Sollbestand.objects.get_or_create(
        inventur_id=zeile.inventur_id,
        hausartikel=ziel,
        # Without a line of its own in the file the target names what stood
        # there, as when reading in ('Zuordner.schreiben').
        defaults={"menge_basiseinheit": Decimal("0"), "name_in_datei": zeile.name},
    )
    # Converted from another unit, the sum may carry more places than the column.
    summe = (soll.menge_basiseinheit + dazu).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    if abs(summe) >= HOECHSTMENGE:
        # Rolled back with the transaction: the row stays open, the target as it was.
        raise ValidationError(
            _(
                "Zusammen wären es %(menge)s %(einheit)s, mehr als ein Sollbestand fasst. "
                "Das Soll bleibt offen."
            )
            % {"menge": menge_kurz(summe), "einheit": ziel.basiseinheit.kurz}
        )
    soll.menge_basiseinheit = summe
    soll.save(update_fields=["menge_basiseinheit"])
    # Whoever counted as the old row counts as the active one now, as after a merge.
    Hausartikel.objects.filter(zaehlt_als=alter).update(zaehlt_als=ziel)
    alter.zaehlt_als = ziel
    alter.save(update_fields=["zaehlt_als", "geaendert_am"])
    zeile.delete()
    return ziel


@transaction.atomic
def sollzeilen_erneut_zuordnen(inventur: Inventur) -> Sollbestandbericht:
    """Hold the open rows against the master data once more.

    After creating or activating an article through another page: the file
    does not have to be uploaded again for that. An article that takes its
    own row now gets back the rows "Set inactive" marked as deleted, as in
    'sollzeile_aktivieren'.
    """
    bericht = Sollbestandbericht()
    # The rows that may follow lock the house's locations and articles. Locked
    # here already, nothing written before them comes first.
    _vorbereitung_sperren(inventur.pk)
    orte_und_artikel_sperren([inventur.haus_id])
    zuordner = Zuordner(inventur)
    offene = list(inventur.sollzeilen.all())
    inventur.sollzeilen.all().delete()
    for zeile in offene:
        bericht.zeilen += 1
        zuordner.zuordnen(zeile.artikelnummer, zeile.menge_basiseinheit, zeile.name, bericht)
    # Read before 'schreiben': then only the articles that took their own row
    # are in it, not those a duplicate added to.
    getroffen = set(zuordner.sollbestaende)
    zuordner.schreiben(bericht)
    if bericht.zugeordnet:
        inventur.zaehlpositionen_anlegen(wiederbeleben_fuer=getroffen)
    return bericht


@transaction.atomic
def sollzeile_verwerfen(zeile: Sollzeile) -> None:
    """Drop one open row. Answered or replaced meanwhile, the answer says so."""
    _zeile_unter_sperre(zeile).delete()


@transaction.atomic
def sollzeilen_verwerfen(inventur: Inventur, grund: str = "") -> int:
    """Drop the open rows, all or those of one reason. Says how many.

    The only bulk answer on the clarification page: discarding changes
    nothing about the master data, it only drops a target that is assigned to
    nobody anyway. Everything else stays row by row.
    """
    _vorbereitung_sperren(inventur.pk)
    zeilen = inventur.sollzeilen.all()
    if grund:
        zeilen = zeilen.filter(grund=grund)
    geloescht, _je_modell = zeilen.delete()
    return geloescht


@transaction.atomic
def ohne_soll_ausscheiden(inventur: Inventur, artikel: Hausartikel, status: str) -> int:
    """An article the file does not know is probably none any more: set inactive or discontinue.

    Its still empty count positions of this inventory are marked as deleted,
    not deleted: after "Back to preparation" a device may already hold them,
    and only a marked row reaches it on the next sync (until 10/2026 they
    were deleted, and a quantity typed into one met a 404). Counted ones
    stay: counted is counted.

    The article and its rows are locked by number after the inventory and
    before anything changes ('zaehlpositionen_sperren'), as by every writer
    of rows. Until 10/2026 the rows were marked without a lock. The article
    is read anew under its lock.
    """
    if status not in (Artikelstatus.INAKTIV, Artikelstatus.EINGESTELLT):
        raise ValidationError(_("Hier geht nur inaktiv oder eingestellt."))
    _vorbereitung_sperren(inventur.pk)
    zaehlpositionen_sperren([artikel])
    artikel.refresh_from_db()
    if status == Artikelstatus.INAKTIV:
        artikel.deaktivieren()
    else:
        artikel.einstellen()
    return inventur.zaehlpositionen.filter(
        artikel_id=artikel.pk, aktiv=True, eingabemenge__isnull=True, wiegungen__isnull=True
    ).update(aktiv=False, zuletzt_geaendert=timezone.now())
