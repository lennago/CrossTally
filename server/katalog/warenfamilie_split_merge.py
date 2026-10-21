"""Split and merge product families, like the locations ('katalog/ort_split_merge.py').

A family is more than a list of products: it carries a **lead unit**, and
it has to fit the products it still has after the process. That is mainly
what this is about. The conversion into the lead unit hangs on every
product ('WareUmrechnung'), so it travels along by itself.

- **Splitting**: both halves inherit the lead unit of the old family and
  its place in the tree. If a half no longer carries the lead unit at all
  and only **one** dimension, it gets the unit of its first product. A sum
  in pieces over nothing but kilogram products would be arithmetically
  right, but nobody would read it that way. A lead unit no product of the
  old family carried was chosen on purpose (litres over bottles in pieces,
  kg over products in g) and stays, as long as the products reach it.
  Families below stay with the old half.
- **Merging**: the remaining family wins with its lead unit. An empty one
  without a unit takes over that of the absorbed family. If an absorbed
  product lacks its factor to that unit, or to the unit of a family above,
  it is needed **before** writing. Otherwise 'Ware.clean' would not accept
  the product there at all, and the report would show gaps. The families
  below the absorbed one move under the remaining one.

**"Not countable" stays out**, in both roles: a product that switches in or
out is ignored or active again in every house ('Ware.pruefe_familienwechsel').
Nobody should trigger that on the side with a click on "Split". For that
there are the buttons on the product.
"""

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction

from .models import Basiseinheit, Ware, Warenfamilie


@dataclass
class Aufteilbericht:
    bleibt: Warenfamilie
    neu: Warenfamilie
    gewandert: int


@dataclass
class Zusammenfuehrbericht:
    familie: Warenfamilie
    neue_waren: int


#: A missing factor: the product and the dimension it cannot reach.
Fehlstelle = tuple[Ware, str]


def _einheit_nachziehen(
    familie: Warenfamilie, leiteinheit: Basiseinheit | None, *, getragen: bool = True
) -> None:
    """Set the lead unit as the family's current products need it.

    'leiteinheit' is the template, when splitting the unit of the old family.
    It stays as long as one of the products carries it or several dimensions
    meet. 'getragen' says whether a product of the old family carried it.
    One that none carried was chosen on purpose, as the family's form allows
    (since 2026-09-24), and stays as long as every product reaches it. Until
    10/2026 splitting and merging turned litres over bottles in pieces into
    pieces. The products of the families below count too: they are summed
    here as well.
    """
    waren = list(
        familie.alle_waren()
        .select_related("basiseinheit")
        .prefetch_related("umrechnungen")
        .order_by("name")
    )
    if not waren:
        leiteinheit = None
    elif leiteinheit is None:
        leiteinheit = waren[0].basiseinheit
    elif not leiteinheit.nur_fuer_familien:
        # A family in portions keeps them: no product carries that unit anyway.
        einheiten = {ware.basiseinheit_id for ware in waren}
        dimensionen = {ware.basiseinheit.dimension for ware in waren}
        erreichbar = all(ware.faktor_nach(leiteinheit.dimension) is not None for ware in waren)
        if (
            leiteinheit.pk not in einheiten
            and len(dimensionen) == 1
            and (getragen or not erreichbar)
        ):
            leiteinheit = waren[0].basiseinheit

    familie.leiteinheit = leiteinheit
    familie.save(update_fields=["leiteinheit"])


@transaction.atomic
def aufteilen(familie: Warenfamilie, name: str, neuer_name: str, wandern) -> Aufteilbericht:
    """Creates a second family next to 'familie' and moves the named products there.

    'wandern' are products or their numbers. Foreign ones are skipped,
    because they do not belong to this family. The new family stands where
    the old one stands, under the same family above.
    """
    nummern = {getattr(eintrag, "pk", eintrag) for eintrag in wandern}
    leiteinheit = familie.leiteinheit
    getragen = familie.alle_waren().filter(basiseinheit=leiteinheit).exists()

    familie.name = name
    familie.save(update_fields=["name"])
    neu = Warenfamilie.objects.create(
        name=neuer_name, beschreibung=familie.beschreibung, uebergeordnet=familie.uebergeordnet
    )

    # 'update' instead of 'save' per product: between two real families nothing
    # changes about the status of the house articles. "Not countable" is excluded here.
    gewandert = Ware.objects.filter(warenfamilie=familie, pk__in=nummern).update(warenfamilie=neu)
    _einheit_nachziehen(familie, leiteinheit, getragen=getragen)
    _einheit_nachziehen(neu, leiteinheit, getragen=getragen)
    return Aufteilbericht(bleibt=familie, neu=neu, gewandert=gewandert)


def fehlende_faktoren(ziel: Warenfamilie, quelle: Warenfamilie) -> list[Fehlstelle]:
    """Product and dimension for every sum 'ziel' could not form after merging.

    Checked are the products of 'quelle' and of the families below it,
    against the lead unit of 'ziel' and of every family above it. An empty
    'ziel' takes over the unit of 'quelle' ('zusammenfuehren'), and the
    families above it sum the products just the same. Until 10/2026 nothing
    was checked then, and the family above showed a gap in the evaluation.
    """
    return ziel.fehlende_faktoren(
        leiteinheit=ziel.leiteinheit or quelle.leiteinheit,
        waren=quelle.alle_waren()
        .select_related("basiseinheit")
        .prefetch_related("umrechnungen")
        .order_by("name"),
    )


def waren_ohne_faktor(ziel: Warenfamilie, quelle: Warenfamilie) -> list[Ware]:
    """The products of 'quelle' that 'ziel' could not sum after merging, each once."""
    gefunden: list[Ware] = []
    for ware, _dimension in fehlende_faktoren(ziel, quelle):
        if ware not in gefunden:
            gefunden.append(ware)
    return gefunden


@transaction.atomic
def zusammenfuehren(
    ziel: Warenfamilie,
    quelle: Warenfamilie,
    name: str,
    faktoren: dict[tuple[int, str], Decimal] | None = None,
) -> Zusammenfuehrbericht:
    """Fetches the products of 'quelle' into 'ziel' and deletes 'quelle'.

    'faktoren' are the conversions the absorbed products still lack, per
    product number and dimension ('fehlende_faktoren'). They are written
    first, so every product arrives with what the sums need. The remaining
    family wins. The families below 'quelle' move under 'ziel'. If 'ziel'
    itself stood below 'quelle', directly or deeper, it takes the place of
    'quelle'.
    """
    leiteinheit = quelle.leiteinheit if ziel.leiteinheit_id is None else ziel.leiteinheit
    je_ware: dict[int, dict[str, Decimal]] = {}
    for (nummer, dimension), wert in (faktoren or {}).items():
        je_ware.setdefault(nummer, {})[dimension] = wert
    for ware in Ware.objects.filter(pk__in=je_ware):
        ware.umrechnungen_setzen(ware.faktoren() | je_ware[ware.pk])

    # 'ziel' below 'quelle', directly or deeper: it takes the place of
    # 'quelle'. Otherwise the family on the way down would move under 'ziel'
    # while 'ziel' still hangs below it, and the tree would have a cycle
    # ('update()' does not run 'clean()').
    if any(vorfahre.pk == quelle.pk for vorfahre in ziel.vorfahren()):
        ziel.uebergeordnet = quelle.uebergeordnet
        ziel.save(update_fields=["uebergeordnet"])
    quelle.unterfamilien.exclude(pk=ziel.pk).update(uebergeordnet=ziel)
    neue_waren = Ware.objects.filter(warenfamilie=quelle).update(warenfamilie=ziel)
    # A product in "Not countable" that came from the absorbed family goes back into this one.
    Ware.objects.filter(familie_vorher=quelle).update(familie_vorher=ziel)

    # Delete first, then rename: that way the remaining family may also take
    # the name of the absorbed one.
    quelle.delete()
    ziel.name = name
    ziel.save(update_fields=["name"])
    # The remaining family wins: the form asked for every factor into its unit.
    _einheit_nachziehen(ziel, leiteinheit, getragen=False)
    return Zusammenfuehrbericht(familie=ziel, neue_waren=neue_waren)
