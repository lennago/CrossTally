"""The target stock typed by hand, where no file names it."""

from dataclasses import dataclass, field
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

from katalog.models import (
    Hausartikel,
)

from ..models import (
    Inventur,
    Sollbestand,
    Sollherkunft,
)
from .target_stock import soll_darf_herein


@dataclass
class Pflegezeile:
    """An article that is due in this inventory, and its target if there is one."""

    hausartikel: Hausartikel
    soll: Sollbestand | None = None


@dataclass
class Sollpflege:
    """All articles that could have a target: first those without, then those with.

    Those without come first because they are the work. Those with are locked
    as long as nobody releases them specifically (see 'SollPflegeForm').
    """

    ohne: list[Pflegezeile] = field(default_factory=list)
    mit: list[Pflegezeile] = field(default_factory=list)

    @property
    def zeilen(self) -> list[Pflegezeile]:
        return self.ohne + self.mit


def sollpflege(inventur: Inventur) -> Sollpflege:
    """What the house actively carries and what this inventory's types count, with its target.

    The same set of articles as "Active, but without target" in the
    clarification, only complete: those that already have their target too.
    Sorted like the house articles everywhere, by product group and number.
    """
    soll_je_artikel = {
        soll.hausartikel_id: soll for soll in inventur.sollbestaende.select_related("hausartikel")
    }
    pflege = Sollpflege()
    artikel = (
        Hausartikel.objects.filter(haus=inventur.haus, aktiv=True)
        .filter(inventur.zaehlumfang.hausartikel())
        .select_related("basiseinheit", "ware", "warengruppe")
        .order_by("warengruppe__name", "artikelnummer")
    )
    for satz in artikel:
        zeile = Pflegezeile(satz, soll_je_artikel.get(satz.pk))
        (pflege.mit if zeile.soll is not None else pflege.ohne).append(zeile)
    return pflege


@dataclass
class Sollpflegebericht:
    eingetragen: int = 0
    geaendert: int = 0
    entfernt: int = 0


@transaction.atomic
def soll_von_hand_speichern(
    inventur: Inventur, eingaben: dict[int, Decimal | None]
) -> Sollpflegebericht:
    """Writes the inputs of the maintenance page. The key is the house article.

    A value for an article without target creates one. A value for one with
    target changes it, 'None' removes it. What the form does not send at all,
    because the row was locked, is not in 'eingaben' and stays. Every entry
    that gets through here is "by hand" afterwards, even if the number once
    came from the file: someone has just confirmed or replaced it.

    Written only to articles that are active and due in this kind of
    inventory, as the page shows them. A form from a second window can be
    older than the article's status; otherwise an article that has become
    inactive in the meantime would get a target and stand in the result with
    actual 0. Negative values get through: an inventory management system
    reports them when more was booked than delivered, and the page should be
    able to say the same.

    Refused while the difference is typed in by hand and outside the
    preparation, checked under the lock ('soll_darf_herein').
    """
    bericht = Sollpflegebericht()
    if not eingaben:
        return bericht
    soll_darf_herein(inventur)
    vorhanden = {
        soll.hausartikel_id: soll
        for soll in inventur.sollbestaende.filter(hausartikel_id__in=eingaben)
    }
    im_haus = {
        artikel.pk: artikel
        for artikel in Hausartikel.objects.filter(haus=inventur.haus, pk__in=eingaben)
    }
    umfang = inventur.zaehlumfang
    for hausartikel_id, menge in eingaben.items():
        artikel = im_haus.get(hausartikel_id)
        if artikel is None:
            raise ValidationError(_("Ein Artikel gehört nicht zu diesem Haus."))
        # The form shows only what is active and due. A second window can be
        # older than the article's status.
        if not artikel.aktiv or not umfang.umfasst(artikel):
            raise ValidationError(
                _(
                    "„%(name)s“ ist inzwischen nicht mehr aktiv oder bei dieser Inventur "
                    "nicht dran. Bitte die Seite neu laden."
                )
                % {"name": artikel.name}
            )
        soll = vorhanden.get(hausartikel_id)
        if menge is None:
            if soll is not None:
                soll.delete()
                bericht.entfernt += 1
            continue
        if soll is None:
            Sollbestand.objects.create(
                inventur=inventur,
                hausartikel_id=hausartikel_id,
                menge_basiseinheit=menge,
                herkunft=Sollherkunft.HAND,
            )
            bericht.eingetragen += 1
        elif soll.menge_basiseinheit != menge or soll.herkunft != Sollherkunft.HAND:
            soll.menge_basiseinheit = menge
            soll.herkunft = Sollherkunft.HAND
            soll.save(update_fields=["menge_basiseinheit", "herkunft"])
            bericht.geaendert += 1
    return bericht
