"""Copy a location, including what is expected there.

Lies in the catalog for the same reason as 'ort_split_merge.py': it is about
the **expectations**, and 'organisation' does not know the catalog.

The copy may arise in any area, also in one of another house. Houses
resemble each other: whoever has set up the counter of one does not want to
click it together again for the next. An expectation points to an article,
and a house article belongs to one house. So across houses the **product**
decides: the copy expects the house article with which the target house
carries the same product. The packs hang on the product and fit everywhere.

What the target house does not carry is left out and named, not created.
Whether a house carries a product is decided in its article list, not in
passing while copying. Within one house that only meets a recipe article:
discontinuing a house article takes it off every count list
('Hausartikel._ausscheiden_aufraeumen'), but a recipe article whose
ingredient was discontinued stays at the old location. It can no longer be
resolved there, so a new location does not begin with it.
"""

from dataclasses import dataclass, field

from django.db import transaction

from organisation.models import Bereich, Ort

from .models import GEFUEHRT, Artikel, Erwartung, ErwartungGebinde, Hausartikel, Rezepturartikel
from .ort_split_merge import geordnet


@dataclass
class Kopierzeile:
    """What happens with one expectation of the copied location."""

    erwartung: Erwartung
    #: The article the copy expects, None where the target house has none.
    artikel: Artikel | None = None

    @property
    def wird_kopiert(self) -> bool:
        return self.artikel is not None


@dataclass
class Kopierbericht:
    neu: Ort
    kopiert: int
    #: The product names of what the target house does not carry.
    ausgelassen: list[str] = field(default_factory=list)


def vorschau(ort: Ort, bereich: Bereich) -> list[Kopierzeile]:
    """What copying 'ort' into 'bereich' would do with every expectation, without writing."""
    haus_id = bereich.haus_id
    erwartungen = list(geordnet(ort))

    ware_ids = set()
    for erwartung in erwartungen:
        artikel = erwartung.artikel.konkret()
        if isinstance(artikel, Hausartikel):
            ware_ids.add(artikel.ware_id)
    # Unique per house and product among the carried ones, so one row per product.
    im_zielhaus = {
        artikel.ware_id: artikel
        for artikel in Hausartikel.objects.filter(
            haus_id=haus_id, ware_id__in=ware_ids, status__in=GEFUEHRT
        )
    }

    zeilen = []
    for erwartung in erwartungen:
        artikel = erwartung.artikel.konkret()
        if isinstance(artikel, Hausartikel):
            zeilen.append(Kopierzeile(erwartung, im_zielhaus.get(artikel.ware_id)))
        elif isinstance(artikel, Rezepturartikel) and artikel.aufloesbar_in(haus_id):
            zeilen.append(Kopierzeile(erwartung, artikel))
        else:
            zeilen.append(Kopierzeile(erwartung))
    return zeilen


@transaction.atomic
def kopieren(ort: Ort, bereich: Bereich, name: str) -> Kopierbericht:
    """Creates a location 'name' in 'bereich' with the counting list of 'ort'.

    The order is adopted and renumbered without gaps, because what is left out
    would otherwise leave holes. Packs, default quantities and the weighing
    tick come along. 'ort' itself stays untouched, and so do its count
    positions: a copy begins without a count.
    """
    zeilen = vorschau(ort, bereich)
    neu = Ort.objects.create(name=name, bereich=bereich)

    kopiert = 0
    vorgaben = []
    for zeile in zeilen:
        if zeile.artikel is None:
            continue
        kopiert += 1
        erwartung = Erwartung.objects.create(
            ort=neu,
            artikel_id=zeile.artikel.pk,
            sortierung=kopiert,
            wiegen_erwartet=zeile.erwartung.wiegen_erwartet,
        )
        vorgaben += [
            ErwartungGebinde(
                erwartung=erwartung,
                gebinde_id=vorgabe.gebinde_id,
                standard_menge=vorgabe.standard_menge,
            )
            for vorgabe in zeile.erwartung.gebinde_vorgaben.all()
        ]
    ErwartungGebinde.objects.bulk_create(vorgaben)

    return Kopierbericht(
        neu=neu,
        kopiert=kopiert,
        ausgelassen=[
            zeile.erwartung.artikel.warenname for zeile in zeilen if not zeile.wird_kopiert
        ],
    )
