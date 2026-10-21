"""Creating a run: per chosen house an inventory including its count positions."""

from datetime import date

from django.db import transaction

from ..models import (
    Inventur,
    orte_und_artikel_sperren,
)


@transaction.atomic
def inventuren_anlegen(
    haeuser, arten, stichtag: date, angelegt_von=None, zaehlende=()
) -> list[Inventur]:
    """One inventory per house, each right away with its count positions.

    The positions arise exactly here and not afterwards: if something comes
    later, the counters create it in the PWA as an unexpected position. How
    many there were per inventory hangs as 'anzahl_positionen' on the returned
    instance. The message names it, the model needs no field for it.

    **Whoever creates the inventory counts along** ('angelegt_von'): they are
    there on the day anyway and would otherwise have to enter themselves
    first. All other counters are assigned explicitly: here for every house
    at once ('zaehlende'), or afterwards per inventory, where whoever is
    assigned to the house is suggested. Administrators stay out: they may
    count everywhere anyway, an entry would say nothing about it.

    Every house gets the same types ('arten'), also one that carries no
    article of one of them: the app then shows only the tabs with rows. That
    way a run counts the same in every house.

    The locations and articles of all houses are locked first, in one go
    ('orte_und_artikel_sperren'). House by house, the articles of a product
    that the run carries in several houses would be locked in another order
    than merging or editing the product locks them (until 10/2026).
    """
    haeuser = list(haeuser)
    orte_und_artikel_sperren([haus.pk for haus in haeuser])
    arten = list(arten)
    eingetragen = [person for person in zaehlende if not person.is_superuser]
    if angelegt_von is not None and not angelegt_von.is_superuser:
        eingetragen.append(angelegt_von)
    angelegt = []
    for haus in haeuser:
        inventur = Inventur.objects.create(haus=haus, stichtag=stichtag)
        inventur.inventurarten.set(arten)
        inventur.anzahl_positionen = inventur.zaehlpositionen_anlegen()
        inventur.zaehlende.add(*eingetragen)
        angelegt.append(inventur)
    return angelegt
