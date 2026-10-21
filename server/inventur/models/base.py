"""The workflow of an inventory: prepare, count, close.

An inventory always belongs to **one house**, and a house has at most one
per day. If several houses are inventoried on the same day, several
inventories arise with the same cut-off date, and exactly that *is* their
bracket. There is deliberately no separate "parent inventory": it would
carry only the cut-off date, a field the individual inventory needs anyway,
and two truths about the same day would sooner or later drift apart. The
cross-house view is fetched by 'Inventur.lauf()'.

What an inventory counts are its inventory types ('Inventur.inventurarten'),
one tab each in the app.

Three stages:

1. 'VORBEREITUNG': the count positions are in place (generated from the
   expectations), the target stocks come from the inventory management
   system or by hand.
2. 'ZAEHLUNG': the counters fill the positions through the PWA. Unexpected
   articles and packs come in as additional positions.
3. 'ABGESCHLOSSEN': per house article an 'Inventurergebnis' is frozen,
   then target stocks and count positions vanish. What remains is the result
   including a copy of the target. The PDF and the report are fed from it.

Here stand the types, constants and helpers the other modules share. The
modules import each other in one direction: 'base' → 'inventur' →
'sollbestand' → 'zaehlung' → 'ergebnis'. Where a method body needs a later
model, the import stands there.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import formats
from django.utils.translation import gettext_lazy as _

from katalog.models import Rezepturkomponente


def pruefe_im_haus(inventur, hausartikel, feld: str) -> None:
    """The article has to belong to the inventory's house.

    Otherwise closing would freeze an 'Inventurergebnis' onto a foreign
    house's article: a quantity booked at the wrong place in the inventory
    management system. The same rule on the count position, the target stock
    and the inventory note, each on its own field.
    """
    if hausartikel.haus_id != inventur.haus_id:
        raise ValidationError(
            {
                feld: _("„%(name)s“ gehört nicht zu %(haus)s.")
                % {"name": hausartikel.name, "haus": inventur.haus}
            }
        )


class Inventurstatus(models.TextChoices):
    VORBEREITUNG = "VORBEREITUNG", _("Vorbereitung")
    ZAEHLUNG = "ZAEHLUNG", _("Zählung")
    ABGESCHLOSSEN = "ABGESCHLOSSEN", _("Abgeschlossen")


#: Where unexpected things land in the counting list: at the back. It is a
#: special case on the round anyway, and it has no position.
ANS_ENDE = 10_000


#: What counts at all: a row marked as deleted is deleted and must appear
#: neither in the progress nor in the result. It only stays so the counting
#: app learns of the deletion on the next sync.
IST_DABEI = models.Q(zaehlpositionen__aktiv=True)

#: When a count position counts as counted, for queries over the inventory.
#: A number is there **or** it was weighed at least once. Weighed goods carry
#: their quantity in the weighings, not in 'eingabemenge'.
IST_GEZAEHLT = IST_DABEI & (
    models.Q(zaehlpositionen__eingabemenge__isnull=False)
    | models.Q(zaehlpositionen__wiegungen__isnull=False)
)


def stichtag_kurz(datum) -> str:
    """The date as the chosen language writes it.

    'SHORT_DATE_FORMAT' comes from the language's format. In German it reads
    31.08.2026, in English 08/31/2026.
    """
    return formats.date_format(datum, "SHORT_DATE_FORMAT")


def verteilt_ueber_rezepturen(je_rezepturartikel: dict[int, Decimal]) -> dict[int, Decimal]:
    """Distributes counted recipe articles over their recipes onto the products.

    Takes {recipe article PK: quantity in its unit} and returns {product PK:
    quantity in the base unit of **this** product}, all in one query, no
    matter how many positions are behind it.
    """
    if not je_rezepturartikel:
        return {}

    # Whole rows instead of 'values_list': a share in percent needs the
    # product's unit and its weight per unit ('menge_je_einheit'). One query
    # for the rows, one for the conversions.
    nach_ware: dict[int, Decimal] = {}
    komponenten = (
        Rezepturkomponente.objects.filter(rezepturartikel_id__in=je_rezepturartikel)
        .select_related("rezepturartikel", "ware__basiseinheit")
        .prefetch_related("ware__umrechnungen")
    )
    for komponente in komponenten:
        nach_ware[komponente.ware_id] = (
            nach_ware.get(komponente.ware_id, Decimal("0"))
            + je_rezepturartikel[komponente.rezepturartikel_id] * komponente.menge_je_einheit()
        )
    return nach_ware
