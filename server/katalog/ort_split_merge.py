"""Split and merge locations, including what is expected there.

Lies in the catalog and not with 'organisation' because it is about the
**expectations**: which product stands at which location, in which order and
with which packs. 'organisation' does not know the catalog, and that should
stay so.

Neither process touches the order, they **adopt** it: when splitting, each
half keeps the sequence it had in the old location, only renumbered without
gaps. When merging, the added articles are appended at the back, in their
previous sequence. Whoever wants to re-sort afterwards does so in the
counting list.

The packs go with their expectation. When splitting that is so by itself:
the defaults hang on the expectation, and that only changes location. When
merging, the same article may stand at both locations. Then the location
that stays wins, and from the other only the packs it did not have yet are
added.
"""

from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Max

from organisation.models import Ort

from .models import AUSGESCHIEDEN, Erwartung, ErwartungGebinde, Hausartikel


@dataclass
class Aufteilbericht:
    bleibt: Ort
    neu: Ort
    gewandert: int


@dataclass
class Vorschauzeile:
    """What happens with an expectation of the vanishing location."""

    erwartung: Erwartung
    #: The expectation of the same article at the remaining location, otherwise None.
    vorhanden: Erwartung | None = None
    #: The packs that newly come to the remaining location in the process.
    neue_gebinde: list = field(default_factory=list)

    @property
    def ist_neu(self) -> bool:
        return self.vorhanden is None


@dataclass
class Zusammenfuehrbericht:
    ort: Ort
    neue_artikel: int
    neue_gebinde: int


def geordnet(ort: Ort):
    return (
        Erwartung.objects.filter(ort=ort)
        .select_related("artikel__hausartikel__ware", "artikel__rezepturartikel")
        .prefetch_related("gebinde_vorgaben__gebinde")
        .order_by("sortierung")
    )


@transaction.atomic
def aufteilen(ort: Ort, name: str, neuer_name: str, wandern) -> Aufteilbericht:
    """Creates a new location next to 'ort' in the same area and moves expectations there.

    'wandern' are the expectations (or their numbers) that go to the new
    location. What is not named there stays. Foreign numbers are skipped,
    because they do not belong to this location.
    """
    nummern = {getattr(eintrag, "pk", eintrag) for eintrag in wandern}
    alle = list(
        Erwartung.objects.filter(ort=ort).order_by("sortierung").values_list("pk", flat=True)
    )
    gehen = [pk for pk in alle if pk in nummern]
    bleiben = [pk for pk in alle if pk not in nummern]

    ort.name = name
    ort.save(update_fields=["name"])
    neu = Ort.objects.create(name=neuer_name, bereich=ort.bereich)

    # 'update' instead of 'save' per row: the positions stay untouched, and at
    # the new location they are unique anyway as a subset of the old ones. Only
    # afterwards is it renumbered without gaps.
    Erwartung.objects.filter(pk__in=gehen).update(ort=neu)
    if bleiben:
        Erwartung.neu_sortieren(ort.pk, bleiben)
    if gehen:
        Erwartung.neu_sortieren(neu.pk, gehen)
    return Aufteilbericht(bleibt=ort, neu=neu, gewandert=len(gehen))


def vorschau(ziel: Ort, quelle: Ort) -> list[Vorschauzeile]:
    """What merging would do with every expectation of 'quelle', without writing."""
    am_ziel = {erwartung.artikel_id: erwartung for erwartung in geordnet(ziel)}
    zeilen = []
    for erwartung in geordnet(quelle):
        vorhanden = am_ziel.get(erwartung.artikel_id)
        if vorhanden is None:
            zeilen.append(Vorschauzeile(erwartung))
            continue
        schon = {vorgabe.gebinde_id for vorgabe in vorhanden.gebinde_vorgaben.all()}
        neue = [v for v in erwartung.gebinde_vorgaben.all() if v.gebinde_id not in schon]
        zeilen.append(Vorschauzeile(erwartung, vorhanden, neue))
    return zeilen


@transaction.atomic
def zusammenfuehren(ziel: Ort, quelle: Ort, name: str) -> Zusammenfuehrbericht:
    """Fetches the expectations of 'quelle' to 'ziel' and deletes 'quelle'.

    New articles are appended at the back, in their previous order. If an
    article is already at the target, its expectation including position and
    default quantities stays. Only the packs it did not have yet are added.

    Assumed is that no count position hangs on 'quelle'. Those point to the
    location via PROTECT, and the form checks it beforehand.

    Both locations are locked before anything else, the way a new row of
    the app locks its location ('api.serializers.counting.ort_sperren'). A row the app
    created first stops the merge at PROTECT, and the page names its
    inventory ('OrtZusammenfuehrenView.form_valid'). One that comes later
    waits and finds the location gone. Until 10/2026 it was written onto the
    location being deleted, and its commit ended in a server error. 'FOR
    UPDATE', as every path that deletes the row it locks. Both by number, in
    one order with creating count positions, which locks every location of
    the house ('inventur.models.orte_und_artikel_sperren'). Until 10/2026
    only 'quelle' was locked first and 'ziel' when renamed at the end, and
    with 'ziel' the lower number the two waited for each other. Without
    'Meta.ordering', which would lock area and house along.
    """
    list(
        Ort.objects.select_for_update()
        .filter(pk__in=[ziel.pk, quelle.pk])
        .order_by("pk")
        .values_list("pk", flat=True)
    )
    zeilen = vorschau(ziel, quelle)
    reihenfolge = list(
        Erwartung.objects.filter(ort=ziel).order_by("sortierung").values_list("pk", flat=True)
    )

    neue_artikel = [zeile.erwartung.pk for zeile in zeilen if zeile.ist_neu]
    neue_gebinde = 0
    for zeile in zeilen:
        vorhanden = zeile.vorhanden
        if vorhanden is None:
            continue
        # Re-attach instead of copy: the default including default quantity
        # changes expectation. The source vanishes right away anyway.
        ErwartungGebinde.objects.filter(pk__in=[v.pk for v in zeile.neue_gebinde]).update(
            erwartung=vorhanden
        )
        neue_gebinde += len(zeile.neue_gebinde)
        # As with the packs: what was counted at one of the two locations keeps
        # being counted at the joint one, on the scale too.
        if zeile.erwartung.wiegen_erwartet and not vorhanden.wiegen_erwartet:
            vorhanden.wiegen_erwartet = True
            vorhanden.save(update_fields=["wiegen_erwartet"])

    # Append at the back: first onto positions behind everything at the target,
    # so none coincides with an existing one, then renumber without gaps.
    # The real maximum, not the count, because the positions may have gaps.
    hoechste = (
        Erwartung.objects.filter(ort=ziel).aggregate(Max("sortierung"))["sortierung__max"] or 0
    )
    for versatz, pk in enumerate(neue_artikel, start=1):
        Erwartung.objects.filter(pk=pk).update(ort=ziel, sortierung=hoechste + versatz)
    Erwartung.neu_sortieren(ziel.pk, reihenfolge + neue_artikel)
    # A discontinued or ignored row that remembers a place at the vanishing
    # location finds it at the remaining one on its way back.
    Hausartikel.plaetze_umschreiben(
        Hausartikel.objects.filter(haus_id=ziel.bereich.haus_id, status__in=AUSGESCHIEDEN),
        orte={quelle.pk: ziel.pk},
    )

    # Delete first, then rename: that way the remaining location may also take
    # the name of the vanishing one.
    quelle.delete()
    ziel.name = name
    ziel.save(update_fields=["name"])
    return Zusammenfuehrbericht(ort=ziel, neue_artikel=len(neue_artikel), neue_gebinde=neue_gebinde)
