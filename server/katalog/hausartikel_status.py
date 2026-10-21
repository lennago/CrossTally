"""What a house article's new status or product does to counting lists and running inventories.

The model saves the article and hands over here ('Hausartikel.save'). A
retired article leaves the counting lists, and an ignored one the running
inventories as well. The way back brings its places along, and a new
product takes the packs of the old one away. All of it reaches into the
tables of the counting list and of the inventory. Kept apart from the
model, that knowledge stands in one place.
"""

from decimal import Decimal

from django.utils import timezone

from organisation.models import Ort

from .models import (
    AUSGESCHIEDEN,
    GEFUEHRT,
    IN_LAUFENDER_INVENTUR,
    Artikelstatus,
    Erwartung,
    ErwartungGebinde,
    Hausartikel,
    zaehlpositionen_sperren,
)


def aufraeumen(artikel: Hausartikel) -> None:
    """A discontinued or ignored article is expected nowhere any more.

    An ignored one moreover vanishes from running inventories as far as
    nothing is counted there: the row is marked as deleted instead of
    deleted so the app learns of it on the next sync, and a read-in target
    stock and a note drop away. Otherwise closing would freeze a row for
    something that is not a product. A discontinued one stays there: it may
    still lie on the shelf, and counted is counted.

    For an ignored one the article and its rows are locked first
    ('zaehlpositionen_sperren'), and only then is it decided. A count or a
    weighing the app is writing at this moment holds its row
    ('api.serializers.counting.gesperrte_zeile') and is waited for. Whether
    something is counted is asked once more under the lock: the check
    before saving ('Ware.pruefe_familienwechsel') may have been a moment too
    early, and a count that landed since refuses the ignore with the same
    words. Until 10/2026 the rows were marked in one update without a lock.
    That update chose its rows before it waited for the one the app held,
    so a row counted a moment ago ended marked as deleted with its count on
    it, and the closing froze nothing for it. A count that landed between
    the check and the update stayed active on an ignored article.
    """
    if artikel.status not in AUSGESCHIEDEN:
        return
    if artikel.status == Artikelstatus.IGNORIERT:
        zaehlpositionen_sperren([artikel])
        artikel.pruefe_ignorierbar()
    plaetze_merken(artikel)
    artikel.erwartet_an.all().delete()
    if artikel.status == Artikelstatus.IGNORIERT:
        artikel.zaehlpositionen.filter(
            IN_LAUFENDER_INVENTUR, aktiv=True, eingabemenge__isnull=True, wiegungen__isnull=True
        ).update(aktiv=False, zuletzt_geaendert=timezone.now())
        artikel.sollbestaende.filter(IN_LAUFENDER_INVENTUR).delete()
        artikel.inventurnotizen.filter(IN_LAUFENDER_INVENTUR).delete()


def plaetze_merken(artikel: Hausartikel) -> None:
    """Keeps the places before they go, for the way back ('erwartungen_vorher').

    Written past 'save()': the caller may be saving a few fields only,
    and the memory has to land whatever the list says. Nothing to keep
    leaves the memory as it is, so a row discontinued and then ignored
    does not forget on the second step.
    """
    plaetze = [
        {
            "ort": erwartung.ort_id,
            "stelle": erwartung.sortierung,
            "wiegen": erwartung.wiegen_erwartet,
            "gebinde": [
                {
                    "gebinde": vorgabe.gebinde_id,
                    "standardmenge": (
                        None if vorgabe.standard_menge is None else str(vorgabe.standard_menge)
                    ),
                }
                for vorgabe in erwartung.gebinde_vorgaben.all()
            ],
        }
        for erwartung in artikel.erwartet_an.prefetch_related("gebinde_vorgaben")
    ]
    if not plaetze:
        return
    artikel.erwartungen_vorher = plaetze
    Hausartikel.objects.filter(pk=artikel.pk).update(erwartungen_vorher=plaetze)


def plaetze_wiederherstellen(artikel: Hausartikel, status_vorher: str | None) -> None:
    """The way back brings the places along, as far as location and packs still exist.

    For every return from discontinued or ignored into a carried status:
    "Activate again", and the way out of "Not countable" with a memory.
    Each place goes back to its old position ('_an_die_stelle'). A
    memory from before 2026-10-18 knows none, there it comes at the end,
    like everything taken in later ('Sortiert'). A pack that is gone or
    inactive is left out, the scale only where the product still goes on
    it, and a place that would be empty is not created at all. Until
    10/2026 the places were simply gone, and the next inventory had no
    row for the article.
    """
    if (
        status_vorher not in AUSGESCHIEDEN
        or artikel.status not in GEFUEHRT
        or not artikel.erwartungen_vorher
    ):
        return
    orte = {
        ort.pk: ort
        for ort in Ort.objects.filter(
            bereich__haus_id=artikel.haus_id,
            pk__in=[platz["ort"] for platz in artikel.erwartungen_vorher],
        )
    }
    gebinde = set(artikel.ware.gebinde.filter(aktiv=True).values_list("pk", flat=True))
    for platz in artikel.erwartungen_vorher:
        if (ort := orte.get(platz["ort"])) is None:
            continue
        vorgaben = [
            (
                vorgabe["gebinde"],
                None if vorgabe["standardmenge"] is None else Decimal(vorgabe["standardmenge"]),
            )
            for vorgabe in platz["gebinde"]
            if vorgabe["gebinde"] in gebinde
        ]
        wiegen = bool(platz["wiegen"]) and artikel.abwiegbare_ware
        if not vorgaben and not wiegen:
            continue
        if Erwartung.objects.filter(ort=ort, artikel_id=artikel.pk).exists():
            continue
        erwartung = Erwartung.anlegen(ort, artikel, wiegen=wiegen, vorgaben=vorgaben)
        if (stelle := platz.get("stelle")) is not None:
            _an_die_stelle(erwartung, stelle)
    artikel.erwartungen_vorher = []
    Hausartikel.objects.filter(pk=artikel.pk).update(erwartungen_vorher=[])


def _an_die_stelle(erwartung: Erwartung, stelle: int) -> None:
    """Puts a place back where it stood, among the rows that are there today.

    Counted is how many rows stand before the old position. Dragging may
    have numbered the list anew since, but the order of the others stays
    the same by that. Until 10/2026 a place came back at the end, and
    whoever walks the shelf in that order found it in the wrong spot.
    """
    folge = list(
        Erwartung.objects.filter(ort_id=erwartung.ort_id)
        .exclude(pk=erwartung.pk)
        .order_by("sortierung")
        .values_list("pk", "sortierung")
    )
    davor = sum(1 for _pk, sortierung in folge if sortierung < stelle)
    reihenfolge = [pk for pk, _sortierung in folge]
    reihenfolge.insert(davor, erwartung.pk)
    Erwartung.neu_sortieren(erwartung.ort_id, reihenfolge)


def fremde_gebinde_aufraeumen(artikel: Hausartikel) -> None:
    """After a change of product: what still points to packs of the old one goes.

    Packs hang on the product. A default "Kasten" of the old product would
    be wrong on the new one ('ErwartungGebinde.clean()' would reject it),
    and the next inventory would create a count position with a foreign
    factor from it. Uncounted rows of running inventories are marked as
    deleted, as when ignoring. Counted ones stay, because counted is
    counted, and the check list shows them. The rows are locked before
    they are marked, as when ignoring ('aufraeumen'): a count the app is
    writing at this moment is waited for, and its row stays. Merging and
    splitting a product hold them already.
    """
    zaehlpositionen_sperren([artikel])
    ErwartungGebinde.objects.filter(erwartung__artikel_id=artikel.pk).exclude(
        gebinde__ware_id=artikel.ware_id
    ).delete()
    artikel.zaehlpositionen.filter(
        IN_LAUFENDER_INVENTUR,
        aktiv=True,
        gebinde__isnull=False,
        eingabemenge__isnull=True,
    ).exclude(gebinde__ware_id=artikel.ware_id).update(
        aktiv=False, zuletzt_geaendert=timezone.now()
    )
