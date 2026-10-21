"""The inventory types: the tabs an inventory counts, and what is due in an inventory."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models, transaction
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext_lazy as _

from .base import GEFUEHRT, SICHTBAR, NachNameManager

if TYPE_CHECKING:
    # Only for pyright, as explained in 'base.py'.
    from django.db.models.fields.related_descriptors import ManyRelatedManager, RelatedManager

    from inventur.models import Inventur

    from .artikel import Hausartikel

#: The type every installation starts with. Its name is data after that and may change.
MONATSINVENTUR = _("Monatsinventur")

#: For whom a switch of the type applies: the article alone, or every article
#: of its product. The same values on the article form, in the menu of the
#: lists and in the fourth block of the closing. Until 10/2026 they were
#: defined twice and matched by chance.
NUR_DIESER = "artikel"
GANZE_WARE = "ware"

#: What a form or page says when the answer is missing.
GELTUNG_FEHLT = _(
    "Gilt die neue Inventurart nur für diesen Artikel oder für alle Artikel der Ware?"
)


def geltung_fehlt(weitere: int, geltung: str | None) -> bool:
    """Whether a switch of the type still needs the answer for whom it applies.

    Only where the product has further carried articles ('weitere', their
    number or whether there are any): a default would decide silently, "only
    this one" splits the product across the tabs, "all" changes other houses
    unasked.
    """
    return bool(weitere) and geltung not in (NUR_DIESER, GANZE_WARE)


class Inventurart(models.Model):
    """A tab of the inventory, say the monthly, the annual or the cup inventory.

    Every house article belongs to exactly one ('Hausartikel.inventurart').
    An inventory counts the types chosen when it is created, and the app
    shows a tab for each. There is no rule between the types: an annual
    inventory is one that counts all of them. That is why there always is at
    least one type. Without it a house article would have none.

    'reihenfolge' is the order of the tabs, of the choices and of the name of
    an inventory that counts several.
    """

    name = models.CharField(max_length=100, unique=True, verbose_name=_("Name"))
    reihenfolge = models.PositiveIntegerField(default=0, verbose_name=_("Reihenfolge"))

    objects = NachNameManager()

    # Created by Django at runtime. Only named here so pyright knows them.
    hausartikel: RelatedManager[Hausartikel]
    inventuren: ManyRelatedManager[Inventur, models.Model]
    # Only on rows from the list, which counts them in the same query.
    anzahl_artikel: int

    class Meta:
        verbose_name = _("Inventurart")
        verbose_name_plural = _("Inventurarten")
        ordering = ["reihenfolge", "name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        """A new type comes last. The list moves it from there."""
        if self._state.adding and not self.reihenfolge:
            letzte = Inventurart.objects.aggregate(models.Max("reihenfolge"))["reihenfolge__max"]
            self.reihenfolge = (letzte or 0) + 1
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("inventurart_edit", kwargs={"pk": self.pk})

    def natural_key(self) -> tuple[str]:
        return (self.name,)

    def wird_benutzt(self) -> bool:
        """Whether an inventory counts this type. Its name is part of theirs, so the type stays."""
        return self.inventuren.exists()

    @classmethod
    def erste(cls) -> Inventurart:
        """The first type by order: preselected wherever nothing else says which one.

        An emptied database has none. Then the monthly inventory arises again,
        in the server's language, as after setting up.
        """
        art = cls.objects.first()
        if art is None:
            with translation.override(settings.LANGUAGE_CODE):
                art = cls.objects.create(name=str(MONATSINVENTUR), reihenfolge=1)
        return art

    @classmethod
    def vorschlag_fuer(cls, ware_id: int | None) -> Inventurart:
        """The type for a new house article of this product.

        The one its other articles agree on. A new house then counts the
        product in the same tab as the others. If they differ or there are
        none, the first type.
        """
        from .artikel import Hausartikel  # noqa: PLC0415  (a later module of the package)

        if ware_id is not None:
            arten = set(
                Hausartikel.objects.filter(ware_id=ware_id, status__in=SICHTBAR).values_list(
                    "inventurart_id", flat=True
                )
            )
            if len(arten) == 1:
                return cls.objects.get(pk=arten.pop())
        return cls.erste()

    def verschieben(self, schritt: int) -> None:
        """One place forward (-1) or back (+1). Renumbers all from 1.

        Renumbered instead of swapping two numbers: types from before the
        order or from a backup may share a number, and then a swap would
        change nothing.

        A recipe article stands in the counting list under the first type of
        its ingredients. Swapping two types can move it into the other
        section, and there it goes to the end like every article that changes.
        """
        from .erwartung import neue_art_ans_ende  # noqa: PLC0415  (a later module of the package)

        arten = list(Inventurart.objects.all())
        stelle = next(i for i, art in enumerate(arten) if art.pk == self.pk)
        ziel = stelle + schritt
        if not 0 <= ziel < len(arten):
            return
        arten[stelle], arten[ziel] = arten[ziel], arten[stelle]
        for position, art in enumerate(arten, start=1):
            art.reihenfolge = position
        with neue_art_ans_ende():
            Inventurart.objects.bulk_update(arten, ["reihenfolge"])
        self.reihenfolge = ziel + 1

    def loeschen(self, nachfolger: Inventurart) -> int:
        """Deletes the type. Its house articles move to 'nachfolger'. Returns how many.

        Whether it may go at all ('wird_benutzt', the last one) is checked by
        the page beforehand. The database refuses a type an inventory counts
        anyway ('PROTECT'). In the counting lists the articles go behind those
        of the successor.
        """
        from .erwartung import neue_art_ans_ende  # noqa: PLC0415  (a later module of the package)

        with transaction.atomic(), neue_art_ans_ende():
            umgezogen = self.hausartikel.update(inventurart=nachfolger)
            self.delete()
        return umgezogen


def arten_je_haus() -> dict[int, set[int]]:
    """Per house the types it carries an active article in, in one query.

    A house can count a type only then: its inventory would otherwise have
    nothing to count in it. Creating an inventory and changing its types
    offer a house by this.
    """
    from .artikel import Hausartikel  # noqa: PLC0415  (a later module of the package)

    arten: dict[int, set[int]] = {}
    for haus_id, art_id in (
        Hausartikel.objects.filter(aktiv=True).values_list("haus_id", "inventurart_id").distinct()
    ):
        arten.setdefault(haus_id, set()).add(art_id)
    return arten


def arten_je_artikel(haus_id: int, artikel_ids: Iterable[int]) -> dict[int, list[int]]:
    """The inventory types of these articles in this house, in the order of the types.

    A house article has exactly one. A recipe article has those of its
    ingredients, each through the article the house carries for it. The
    counting list marks by this, and the app sorts its tabs by it. Three
    queries for all.
    """
    from .artikel import Hausartikel, Rezepturkomponente  # noqa: PLC0415  (a later module)

    ids = set(artikel_ids)
    arten: dict[int, set[int]] = {
        pk: {art}
        for pk, art in Hausartikel.objects.filter(pk__in=ids).values_list("pk", "inventurart_id")
    }
    zutaten = Rezepturkomponente.objects.filter(
        rezepturartikel_id__in=ids,
        ware__hausartikel__haus_id=haus_id,
        ware__hausartikel__status__in=GEFUEHRT,
    ).values_list("rezepturartikel_id", "ware__hausartikel__inventurart_id")
    for pk, art in zutaten:
        arten.setdefault(pk, set()).add(art)
    if not arten:
        return {}
    folge = {
        pk: stelle for stelle, pk in enumerate(Inventurart.objects.values_list("pk", flat=True))
    }
    return {pk: sorted(eigene, key=lambda art: folge.get(art, 0)) for pk, eigene in arten.items()}


@dataclass(frozen=True)
class Zaehlumfang:
    """What is due in an inventory: the house articles of its types and the recipes they feed.

    The catalog does not know the inventory ('katalog' <- 'inventur'). Hence
    the types come in as ids, in their order, and 'Inventur.zaehlumfang'
    builds this.
    """

    arten: tuple[int, ...]

    def hausartikel(self, pfad: str = "") -> models.Q:
        """The house articles due, as a filter. 'pfad' leads to the house article, or is empty."""
        feld = f"{pfad}__inventurart_id__in" if pfad else "inventurart_id__in"
        return models.Q(**{feld: self.arten})

    def umfasst(self, hausartikel: Hausartikel) -> bool:
        """Whether this house article is due."""
        return hausartikel.inventurart_id in self.arten

    def rezepturartikel(self, haus_id: int, artikel: str = "") -> models.Q:
        """The recipe articles due in this house: as soon as **one** ingredient is.

        The ingredient's type is that of the article the house carries for
        it. Otherwise exactly the nachos in the prepared portions would be
        missing from the monthly inventory. 'artikel' is the path to the
        recipe article, empty for the recipe article itself.
        """
        from .artikel import Rezepturkomponente  # noqa: PLC0415  (a later module of the package)

        zutaten = Rezepturkomponente.objects.filter(
            rezepturartikel_id=models.OuterRef(artikel or "pk"),
            ware__hausartikel__haus_id=haus_id,
            ware__hausartikel__status__in=GEFUEHRT,
            ware__hausartikel__inventurart_id__in=self.arten,
        )
        return models.Q(models.Exists(zutaten))

    def reiter(self, arten: Iterable[int]) -> int | None:
        """The tab an article with these types is counted in: the first type of this inventory.

        None if none of them is counted here. The app then shows the row in
        its first tab, as something counted on top.
        """
        eigene = set(arten)
        return next((art for art in self.arten if art in eigene), None)
