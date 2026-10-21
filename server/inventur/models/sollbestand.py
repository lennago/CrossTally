"""The target stock of an inventory and the rows of the file that belong to nobody yet."""

from collections.abc import Callable
from typing import TYPE_CHECKING

from django.db import models
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

from katalog.models import (
    Hausartikel,
)

from .inventur import Inventur

if TYPE_CHECKING:
    # Only for pyright: without the mypy plugin of the django-stubs, reverse
    # relations, '_id' columns and 'get_…_display' are unknown. Nothing of this
    # is loaded at runtime. The dependency direction of the apps stays as it is.
    pass
from .base import pruefe_im_haus


class Sollherkunft(models.TextChoices):
    """Where a target comes from. The page shows it, and an upload says what it replaces."""

    DATEI = "DATEI", _("aus der Datei")
    HAND = "HAND", _("von Hand")


class Sollbestand(models.Model):
    """What the inventory management system carries as stock at the cut-off date, or a manual entry.

    Comes in as a file before counting or is maintained by hand in the
    preparation, and vanishes with the closing. What remains of it is the copy
    in the 'Inventurergebnis'. The row needs no date of its own: it applies to
    the inventory it hangs on.
    """

    inventur = models.ForeignKey(
        Inventur, on_delete=models.CASCADE, related_name="sollbestaende", verbose_name=_("Inventur")
    )
    hausartikel = models.ForeignKey(
        Hausartikel,
        on_delete=models.PROTECT,
        related_name="sollbestaende",
        verbose_name=pgettext_lazy("Feld", "Hausartikel"),
    )
    menge_basiseinheit = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        verbose_name=_("Sollbestand"),
        help_text=_("In der Einheit des Hauses, wie die Warenwirtschaft den Artikel führt."),
    )
    herkunft = models.CharField(
        max_length=5,
        choices=Sollherkunft.choices,
        default=Sollherkunft.DATEI,
        verbose_name=_("Herkunft"),
    )
    name_in_datei = models.CharField(
        max_length=200,
        blank=True,
        default="",
        verbose_name=_("Bezeichnung in der Datei"),
        help_text=_(
            "Die Bezeichnung laut Datei. Weicht sie vom Artikel ab, ist die Nummer womöglich neu "
            "belegt."
        ),
    )

    # Created by Django at runtime. Only named here so pyright knows them.
    inventur_id: int
    hausartikel_id: int
    get_herkunft_display: Callable[[], str]

    class Meta:
        verbose_name = _("Sollbestand")
        verbose_name_plural = _("Sollbestände")
        ordering = ["hausartikel__artikelnummer"]
        constraints = [
            models.UniqueConstraint(
                fields=["inventur", "hausartikel"],
                name="ein_sollbestand_je_inventur_und_artikel",
                violation_error_message=_(
                    "Zu diesem Artikel steht in dieser Inventur schon ein Soll."
                ),
            )
        ]

    def __str__(self) -> str:
        return f"{self.hausartikel.artikelnummer}: {self.menge_basiseinheit}"

    def clean(self) -> None:
        """The article has to belong to the inventory's house ('pruefe_im_haus')."""
        if self.inventur_id is None or self.hausartikel_id is None:
            return
        pruefe_im_haus(self.inventur, self.hausartikel, "hausartikel")


class Sollgrund(models.TextChoices):
    UNBEKANNT = "UNBEKANNT", _("Nummer unbekannt")
    INAKTIV = "INAKTIV", _("Artikel inaktiv")
    EINGESTELLT = "EINGESTELLT", _("Artikel eingestellt")


class Sollzeile(models.Model):
    """A row of the target stock file that belongs to nobody yet.

    The file names numbers the house does not actively carry: unknown,
    inactive and discontinued ones. They are not silently skipped, because
    behind each is a question (reactivate, create anew, discard) someone has to
    answer. Until then the row lies here, with everything the file says about
    it. Without this intermediate state the file would have to be uploaded
    again after every creation of an article. The one exception is an inactive
    or discontinued article with a stock of 0: there the file and the house
    agree, and the target is stored without a row here ('Zuordner').

    Working data like the target stock: arises in the preparation and vanishes
    with the next file or with the closing. If the question is answered, a
    'Sollbestand' arises from it, or nothing.
    """

    inventur = models.ForeignKey(
        Inventur, on_delete=models.CASCADE, related_name="sollzeilen", verbose_name=_("Inventur")
    )
    artikelnummer = models.CharField(max_length=20, verbose_name=_("Artikelnummer"))
    name = models.CharField(
        max_length=200,
        blank=True,
        default="",
        verbose_name=_("Bezeichnung in der Datei"),
    )
    menge_basiseinheit = models.DecimalField(
        max_digits=12, decimal_places=3, verbose_name=_("Sollbestand")
    )
    grund = models.CharField(max_length=12, choices=Sollgrund.choices, verbose_name=_("Grund"))
    #: The inactive or discontinued row the row means. Empty for an unknown
    #: number.
    hausartikel = models.ForeignKey(
        Hausartikel,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sollzeilen",
        verbose_name=pgettext_lazy("Feld", "Hausartikel"),
    )

    # Created by Django at runtime. Only named here so pyright knows them.
    inventur_id: int
    hausartikel_id: int | None
    get_grund_display: Callable[[], str]

    class Meta:
        verbose_name = _("Offene Sollzeile")
        verbose_name_plural = _("Offene Sollzeilen")
        ordering = ["grund", "artikelnummer"]
        constraints = [
            models.UniqueConstraint(
                fields=["inventur", "artikelnummer"],
                name="eine_sollzeile_je_inventur_und_nummer",
                violation_error_message=_("Diese Nummer steht in dieser Inventur schon offen."),
            )
        ]

    def __str__(self) -> str:
        return f"{self.artikelnummer}: {self.menge_basiseinheit} ({self.get_grund_display()})"
