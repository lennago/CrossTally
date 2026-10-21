"""What remains from checking and closing: note, frozen result, type decision."""

from decimal import Decimal
from typing import TYPE_CHECKING

from django.db import models
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

from katalog.models import (
    FAKTOR_NACHKOMMASTELLEN,
    FAKTOR_STELLEN_GESAMT,
    Basiseinheit,
    Hausartikel,
    Inventurart,
    Ware,
    umrechnung,
)

from .inventur import Inventur

if TYPE_CHECKING:
    # Only for pyright: without the mypy plugin of the django-stubs, reverse
    # relations, '_id' columns and 'get_…_display' are unknown. Nothing of this
    # is loaded at runtime. The dependency direction of the apps stays as it is.
    pass
from .base import pruefe_im_haus


class Inventurnotiz(models.Model):
    """What was noted about an article while checking, as long as the inventory runs.

    Three things stand here: the **note**, the mark that at closing the
    **target stock** should apply instead of the counted value, and the
    **difference by hand** where the inventory has no target
    ('Inventur.differenz_von_hand'). They share the same row because they are
    the same thing: a remark about exactly one article of this inventory,
    made while checking and settled at closing. A second table with the same
    key would be duplication.

    Working data like target stock and count position: it arises during the
    inventory, is copied into 'Inventurergebnis.notiz' at closing and then
    cleared away with the rest of the working state. Deliberately **no**
    preliminary 'Inventurergebnis': that is the frozen state, and a table with
    two meanings would sooner or later lead to two truths. Actual, target and
    difference would moreover be stale in it, because they move as long as
    counting goes on and are computed fresh on every call anyway.

    The article has to belong to the inventory's house. That is checked as
    with the target stock in 'clean()' and additionally when saving through
    the form, which accepts only articles of the house.
    """

    inventur = models.ForeignKey(
        Inventur, on_delete=models.CASCADE, related_name="notizen", verbose_name=_("Inventur")
    )
    hausartikel = models.ForeignKey(
        Hausartikel,
        on_delete=models.PROTECT,
        related_name="inventurnotizen",
        verbose_name=pgettext_lazy("Feld", "Hausartikel"),
    )
    text = models.TextField(verbose_name=_("Notiz"), blank=True)
    soll_statt_ist = models.BooleanField(
        default=False,
        # The name of the tick in the review list, the app and the help (until
        # 10/2026 the administration and a screen reader said "use target stock").
        verbose_name=_("Soll zählt"),
        help_text=_(
            "Beim Abschluss gilt der Sollbestand als Ergebnis. Die Zählung bleibt unberührt."
        ),
    )
    #: In the house's unit, like the actual it stands next to. Counts only
    #: while the inventory takes the difference by hand.
    differenz = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name=_("Differenz von Hand"),
        help_text=_("In der Einheit des Hauses, wie das Ist. Ein Minus heißt: Es fehlt etwas."),
    )
    geaendert_am = models.DateTimeField(auto_now=True, verbose_name=_("geändert am"))

    # Created by Django at runtime. Only named here so pyright knows them.
    inventur_id: int
    hausartikel_id: int

    class Meta:
        verbose_name = _("Inventurnotiz")
        verbose_name_plural = _("Inventurnotizen")
        ordering = ["hausartikel__artikelnummer"]
        constraints = [
            models.UniqueConstraint(
                fields=["inventur", "hausartikel"],
                name="eine_notiz_je_inventur_und_artikel",
                violation_error_message=_(
                    "Zu diesem Artikel steht in dieser Inventur schon eine Notiz."
                ),
            )
        ]

    def __str__(self) -> str:
        return f"{self.hausartikel.artikelnummer}: {self.text[:40]}"

    def clean(self) -> None:
        """The article has to belong to the inventory's house ('pruefe_im_haus')."""
        if self.inventur_id is None or self.hausartikel_id is None:
            return
        pruefe_im_haus(self.inventur, self.hausartikel, "hausartikel")


class Inventurergebnis(models.Model):
    """The frozen result per house article.

    Arises at closing and does not change afterwards. Target and difference
    are stored as a **copy**: the target stocks are deleted at closing, and a
    value corrected later must not retroactively change a closed result.

    So is the **unit** the house counted in ('einheit'), with its factor to
    the product. The numbers are in it. Until 10/2026 they were read in the
    article's unit of today, and a house that changed its unit later found
    "10 kg" on the sheet where it had counted 10 pieces. The factor follows
    what the product learns afterwards ('Hausartikel.ergebnisse_nachziehen'):
    it says how many units of the product one unit of the result is.
    """

    inventur = models.ForeignKey(
        Inventur, on_delete=models.CASCADE, related_name="ergebnisse", verbose_name=_("Inventur")
    )
    hausartikel = models.ForeignKey(
        Hausartikel,
        on_delete=models.PROTECT,
        related_name="inventurergebnisse",
        verbose_name=pgettext_lazy("Feld", "Hausartikel"),
    )
    gesamtmenge_basiseinheit = models.DecimalField(
        max_digits=12, decimal_places=3, verbose_name=_("Ist")
    )
    sollbestand = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True, verbose_name=_("Sollbestand")
    )
    differenz = models.DecimalField(
        max_digits=12, decimal_places=3, null=True, blank=True, verbose_name=_("Differenz")
    )
    notiz = models.TextField(blank=True, verbose_name=_("Notiz"))
    eingefroren_am = models.DateTimeField(verbose_name=_("eingefroren am"))
    #: The house's unit at closing. Actual, target and difference are in it.
    einheit = models.ForeignKey(
        Basiseinheit,
        on_delete=models.PROTECT,
        related_name="+",
        verbose_name=_("Einheit im Haus"),
        help_text=_(
            "So hat das Haus beim Abschluss gezählt. Die Einheit bleibt, auch wenn das Haus "
            "später anders zählt."
        ),
    )
    #: How many units of the product one unit of 'einheit' is. Only across
    #: dimensions, as on the house article. Within one the units compute.
    faktor_zur_ware = models.DecimalField(
        max_digits=FAKTOR_STELLEN_GESAMT,
        decimal_places=FAKTOR_NACHKOMMASTELLEN,
        null=True,
        blank=True,
        verbose_name=_("Umrechnung zur Ware"),
    )

    # Created by Django at runtime. Only named here so pyright knows them.
    inventur_id: int
    hausartikel_id: int
    einheit_id: int

    class Meta:
        verbose_name = _("Inventurergebnis")
        verbose_name_plural = _("Inventurergebnisse")
        ordering = ["hausartikel__artikelnummer"]
        constraints = [
            models.UniqueConstraint(
                fields=["inventur", "hausartikel"],
                name="ein_ergebnis_je_inventur_und_artikel",
                violation_error_message=_(
                    "Zu diesem Artikel gibt es in dieser Inventur schon ein Ergebnis."
                ),
            )
        ]

    def __str__(self) -> str:
        return f"{self.hausartikel.artikelnummer}: {self.gesamtmenge_basiseinheit}"

    @property
    def faktor_zur_ware_wirksam(self) -> Decimal | None:
        """How many units of the product one unit of the result is, today. None where unknown.

        Within a dimension the units compute, also after the product changed
        its unit. Across dimensions the frozen factor applies. A result
        without one was frozen against a product of its own dimension, and
        its article moved to a product of another one since. The article's
        factor then helps only where the result is in the article's
        dimension: 500 g of an article now in kg at 4 pieces per kg are 2
        pieces. In any other unit it says nothing about the result. Until
        10/2026 it was applied anyway, and 500 g stood in the report as 500
        or 2000 pieces. Without a way into the product the result stays out
        of every sum, and the report says so ('Warenzeile.ohne_umrechnung').
        """
        artikel = self.hausartikel
        faktor = umrechnung(self.einheit, artikel.ware.basiseinheit, self.faktor_zur_ware)
        if faktor is not None:
            return faktor
        zum_artikel = umrechnung(self.einheit, artikel.basiseinheit)
        return None if zum_artikel is None else zum_artikel * artikel.faktor_zur_ware_wirksam

    def in_wareneinheit(self, menge: Decimal | None) -> Decimal | None:
        """A frozen quantity in the product's unit, for sums across houses.

        Empty stays empty, and so does a quantity without a way into the
        product ('faktor_zur_ware_wirksam').
        """
        faktor = self.faktor_zur_ware_wirksam
        return None if menge is None or faktor is None else menge * faktor


class Artentscheidung(models.Model):
    """That at the closing of this inventory a product was put into a type, for all its articles.

    Answered for the whole product, it applies in every house. If several
    houses of a run close one after another, the second could silently
    reverse the first one's answer: Kino Nord puts a product into the annual
    inventory, Kino Ost right after back into the monthly one. The row records
    who decided. 'closing.klaeren' shows it in the other houses of the run and
    locks the opposite answer for the whole product there. For the article of
    the own house the choice stays open: that is a decision of the house.

    Stored instead of only remembered for the duration of a closing: the lock
    should also apply when someone aborts the joint closing and the next house
    closes individually later.
    """

    inventur = models.ForeignKey(
        Inventur,
        on_delete=models.CASCADE,
        related_name="artentscheidungen",
        verbose_name=_("Inventur"),
    )
    ware = models.ForeignKey(
        Ware, on_delete=models.CASCADE, related_name="+", verbose_name=_("Ware")
    )
    inventurart = models.ForeignKey(
        Inventurart, on_delete=models.CASCADE, related_name="+", verbose_name=_("Inventurart")
    )

    # Created by Django at runtime. Only named here so pyright knows them.
    inventur_id: int
    ware_id: int
    inventurart_id: int

    class Meta:
        verbose_name = _("Artentscheidung")
        verbose_name_plural = _("Artentscheidungen")
        constraints = [
            models.UniqueConstraint(
                fields=["inventur", "ware"],
                name="eine_artentscheidung_je_inventur_und_ware",
                violation_error_message=_(
                    "Zu dieser Ware steht in dieser Inventur schon eine Entscheidung."
                ),
            )
        ]

    def __str__(self) -> str:
        return f"{self.inventur}: {self.ware.name} → {self.inventurart}"
