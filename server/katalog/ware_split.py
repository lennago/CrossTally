"""Split a product: the reverse direction of 'katalog/ware_merge.py'.

Merged is what the import created under two designations. Sometimes the
opposite turns out: behind one product there are really two products,
because one house carries something different from the others. Then the
affected **house articles** migrate to a new product.

The difficult part is again the **packs**. They hang on the product, and
location defaults as well as count positions point to them. A house
article that changes product must not use a pack of the old one afterwards
('ErwartungGebinde.clean' and 'Zaehlposition.clean' say so). That is why
the new product gets **copies of all packs**, and everything hanging on the
migrating house articles points to the copy afterwards. That way every
counted quantity is preserved, including its packaging. What the new
product does not need can be set inactive on its page afterwards.

**What stays with the previous product**: the recipes. They point to the
product, not to a house, and a recipe cannot be halved. If the product is
in one, it is to be checked afterwards whether the recipe article is still
resolvable in the houses of the new product ('Rezepturartikel.aufloesbar_in').
"""

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from .models import (
    ErwartungGebinde,
    Gebinde,
    Hausartikel,
    Ware,
    artikel_sperren,
    zaehlpositionen_sperren,
)


@dataclass
class Aufteilbericht:
    bleibt: Ware
    neu: Ware
    gewandert: int
    gebinde: int
    zaehlpositionen: int


def _gebinde_abbilden(ware: Ware, neu: Ware) -> list[tuple[Gebinde, Gebinde]]:
    """Creates every pack of the product a second time on the new one.

    The inactive ones too, because old counts hang on them. The position
    migrates along: on the new product it is free, and the counting list
    keeps the order everyone knows.
    """
    abbilder = []
    for gebinde in ware.gebinde.order_by("sortierung"):
        abbilder.append(
            (
                gebinde,
                Gebinde.objects.create(
                    ware=neu,
                    name=gebinde.name,
                    ist_einzeln=gebinde.ist_einzeln,
                    aktiv=gebinde.aktiv,
                    faktor_in_basiseinheit=gebinde.faktor_in_basiseinheit,
                    sortierung=gebinde.sortierung,
                ),
            )
        )
    return abbilder


def _verweise_umhaengen(nummern: list[int], abbilder: list[tuple[Gebinde, Gebinde]]) -> int:
    """Turns location defaults and count positions of the migrated rows onto the copies.

    'nummern' are the house articles that changed product. A duplicate
    cannot arise in the process, because the copies have only just been
    created.

    Via the reverse relation of the packs instead of via 'Zaehlposition': the
    catalog does not know the inventory ('organisation' <- 'katalog' <-
    'inventur'). The timestamp has to come along, because 'auto_now' does
    not apply on 'update()', and without it the counting app would keep the
    number of a pack that no longer belongs to this product.
    """
    jetzt = timezone.now()
    positionen = 0
    for gebinde, abbild in abbilder:
        ErwartungGebinde.objects.filter(gebinde=gebinde, erwartung__artikel_id__in=nummern).update(
            gebinde=abbild
        )
        positionen += gebinde.zaehlpositionen.filter(artikel_id__in=nummern).update(
            gebinde=abbild, zuletzt_geaendert=jetzt
        )
    return positionen


@transaction.atomic
def aufteilen(ware: Ware, name: str, neuer_name: str, wandern) -> Aufteilbericht:
    """Creates a second product next to 'ware' and moves the named house articles there.

    'wandern' are house articles or their numbers. Foreign ones are skipped,
    because they do not belong to this product.

    The new product inherits everything that makes up the product: unit,
    family, conversions and the entries about weighing, and the family it
    remembers from before "Not countable". It is the same product until
    someone changes it. Exactly for that it is split. The house articles keep
    their inventory type, it hangs on them, and their remembered places point
    to the copies of the packs.

    The count positions of the migrating house articles in running
    inventories are locked first and touched last: their product and their
    pack change, and the app has to fetch them anew ('zaehlpositionen_sperren').
    So are those of the articles that stay, if 'ware' changes its name: the
    app shows the product's name on every row ('api.master_data.ware_name').
    Until 10/2026 they kept their timestamp, and the app the old name. The
    articles are locked before it is read which of them still belong to
    'ware', in one call, by number. Until 10/2026 that was read first: an
    article someone moved to another product in between was moved back onto
    the new one, with its defaults on the packs of the other product.
    """
    nummern = {getattr(eintrag, "pk", eintrag) for eintrag in wandern}
    umbenannt = name != ware.name
    bisherige = set(ware.hausartikel.values_list("pk", flat=True)) if umbenannt else set()
    gesperrt = artikel_sperren(nummern | bisherige)
    wandernde = list(Hausartikel.objects.filter(ware=ware, pk__in=nummern))
    # Only those locked above: one that came to 'ware' in between would be
    # locked out of the order by number.
    bleibende = Hausartikel.objects.filter(ware=ware, pk__in=gesperrt - nummern)
    zeilen = zaehlpositionen_sperren([*wandernde, *bleibende])

    ware.name = name
    ware.save(update_fields=["name"])
    neu = Ware.objects.create(
        name=neuer_name,
        basiseinheit=ware.basiseinheit,
        warenfamilie=ware.warenfamilie,
        abwiegbar=ware.abwiegbar,
    )
    # Past 'save()': creating counts as a switch of family and would forget it.
    Ware.objects.filter(pk=neu.pk).update(familie_vorher=ware.familie_vorher_id)
    neu.umrechnungen_setzen(ware.faktoren())
    neu.behaelter.set(ware.behaelter.all())
    abbilder = _gebinde_abbilden(ware, neu)

    gehen = [artikel.pk for artikel in wandernde]
    # 'update' instead of 'save' per row: both products carry the same family,
    # so nothing changes about the status of the house articles.
    Hausartikel.objects.filter(pk__in=gehen).update(ware=neu)
    # A duplicate counts as another row of the same product. Where the split
    # separates the pair, the link is dropped: a reference across the product
    # border would fail the form's check ('pruefe_zaehlt_als') at the next save,
    # without anyone knowing why. Until 10/2026 it stayed.
    Hausartikel.objects.filter(pk__in=gehen, zaehlt_als__ware=ware).update(zaehlt_als=None)
    Hausartikel.objects.filter(ware=ware, zaehlt_als__in=gehen).update(zaehlt_als=None)
    positionen = _verweise_umhaengen(gehen, abbilder) if gehen else 0
    # The way back of a discontinued or ignored row finds the copies, not the old packs.
    Hausartikel.plaetze_umschreiben(
        Hausartikel.objects.filter(pk__in=gehen),
        gebinde={gebinde.pk: abbild.pk for gebinde, abbild in abbilder},
    )
    if zeilen is not None:
        # 'auto_now' does not apply on 'update()'.
        zeilen.update(zuletzt_geaendert=timezone.now())

    return Aufteilbericht(
        bleibt=ware,
        neu=neu,
        gewandert=len(gehen),
        gebinde=len(abbilder),
        zaehlpositionen=positionen,
    )
