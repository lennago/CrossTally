"""A new name that rows of running inventories show, and how it reaches the counting app.

The app shows on every row the names of what the row hangs on: the
product ('artikel'), the house's designation ('artikel_im_haus'), the pack
('gebinde.name'), the location and its area ('ort.name', 'ort.bereich',
'api.serializers.counting.ZaehlpositionSerializer'). It fetches only the
rows that changed since its last sync ('zuletzt_geaendert'), and it groups
its areas by their name. Until 10/2026 a name changed on a page, in the
import or in the administration reached it only with a full sync, and a
renamed area showed twice until then.

So whoever saves one of these models with a new name locks the rows that
show it and touches them at the end, as splitting a product does. In the
project package because the catalog and the organisation both need it and
do not know each other's models.
"""

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager

from django.db import models, transaction
from django.utils import timezone


@contextmanager
def umbenennen(
    satz: models.Model,
    felder: Iterable[str] | None,
    sperren: Callable[[], models.QuerySet | None],
) -> Iterator[None]:
    """Around the save of 'satz': if its name changes, the rows that show it are locked first.

    'sperren' locks what a new row of the app locks first, its article or
    its location, and then the rows, each by number, in the order every path
    locks in (locations, articles, rows). It returns the rows, or None where
    there are none. A new row of the app then waits and reads the new name,
    one that came first is among the rows. They are touched after the save,
    in its transaction. Only where the name changes: the stored one is read
    from the database, a remembered value would survive 'refresh_from_db()'.
    'felder' are the 'update_fields' of the save, without a name nothing is
    read.
    """
    if satz._state.adding or (felder is not None and "name" not in felder):
        yield
        return
    modell = type(satz)
    # The table that holds the name: for a house article that of 'Artikel'.
    wurzel = ([modell, *modell._meta.get_parent_list()])[-1]
    with transaction.atomic(savepoint=False):
        gespeichert = wurzel._base_manager.filter(pk=satz.pk).values_list("name", flat=True).first()
        neu = gespeichert is not None and gespeichert != getattr(satz, "name", gespeichert)
        zeilen = sperren() if neu else None
        yield
        if zeilen is not None:
            # 'auto_now' does not apply on 'update()'.
            zeilen.update(zuletzt_geaendert=timezone.now())
