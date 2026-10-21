"""The product family: what the report sums together, and in which unit."""

from collections.abc import Iterable
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from django_project.labels import Beschriftung

from .base import NICHT_ZAEHLBAR, NICHT_ZAEHLBAR_BESCHREIBUNG, NachNameManager

if TYPE_CHECKING:
    # Only for pyright, as explained in 'base.py'.
    from django.db.models.fields.related_descriptors import RelatedManager

    from .ware import Ware


#: For 'summendimensionen' and its callers: the stored parent counts. 'None'
#: there means "no parent", which a form clearing the field has to be able to say.
UNVERAENDERT = object()


class Warenfamilie(models.Model):
    """Products that belong together, say "all lemonades" or "all ice cream flavours".

    The **product group** comes from the inventory management system and
    hangs on the house article. It has grown historically there and is cut
    differently per house. The family hangs one level higher on the
    **product** and is maintained here: the reports are grouped by it,
    because only that way the same products stand together, whatever way the
    houses sorted them.

    Optional: assigning around 900 products by hand would be an imposition,
    and what has no family gathers in the reports under "Without product family".

    One family is none: **"Not countable"** collects the products the
    inventory management system carries without them being any: deposit,
    vouchers. Whoever belongs to it counts as ignored in every house (see
    'Hausartikel._status_angleichen'). It arises in the data migration, like
    "without container", sums nothing and cannot be deleted.

    Families nest (since 09/2026, 'uebergeordnet'): "bottled drinks" holds
    "beers" and "soft drinks", and every product belongs to the family above
    through the chain. The report sums a family over the whole subtree, and
    each family in its own lead unit. So a product needs its factor into the
    dimension of every family above it, not only of its own. The sum of the
    family above is computed from the products, not from the sums below: a
    piece count cannot become litres, a bottle of 500 ml can.
    """

    name = models.CharField(max_length=100, unique=True, verbose_name=_("Name"))
    sammelt_ignorierte = models.BooleanField(
        default=False,
        editable=False,
        verbose_name=_("sammelt ignorierte Waren"),
        # Carried by exactly one record, from the data migration. Not settable
        # through forms, the uniqueness is secured by a partial index.
        help_text=_("Kennzeichnet die Warenfamilie „Nicht zählbar“. Sie gehört zur Anwendung."),
    )
    beschreibung = models.TextField(
        blank=True,
        help_text=_("Wofür die Familie steht. Das hilft beim Zuordnen neuer Waren."),
        verbose_name=_("Beschreibung"),
    )
    leiteinheit = models.ForeignKey(
        "Basiseinheit",
        on_delete=models.PROTECT,
        related_name="warenfamilien",
        null=True,
        blank=True,
        help_text=_(
            "Darin steht später die Summe. Eine Ware in einer anderen Dimension braucht die "
            "Umrechnung dorthin."
        ),
        verbose_name=_("Leiteinheit"),
    )
    uebergeordnet = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        related_name="unterfamilien",
        null=True,
        blank=True,
        help_text=_(
            "Die Familie, zu der diese gehört. Ihre Waren zählen dort mit, in deren Einheit."
        ),
        verbose_name=_("Gehört zu"),
    )

    objects = NachNameManager()

    # Created by Django at runtime. Only named here so pyright knows them.
    leiteinheit_id: int | None
    uebergeordnet_id: int | None
    waren: RelatedManager[Ware]
    unterfamilien: RelatedManager[Warenfamilie]

    class Meta:
        verbose_name = _("Warenfamilie")
        verbose_name_plural = _("Warenfamilien")
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["sammelt_ignorierte"],
                condition=models.Q(sammelt_ignorierte=True),
                name="nur_eine_warenfamilie_fuer_ignorierte",
                violation_error_message=_("„Nicht zählbar“ gibt es nur einmal."),
            )
        ]

    def __str__(self) -> str:
        return str(self.anzeigename)

    def get_absolute_url(self):
        return reverse("warenfamilie_detail", kwargs={"pk": self.pk})

    @classmethod
    def echte(cls):
        """All but "Not countable". Whoever switches in or out of it changes their status.

        Pages and forms asked this the same way in two places (until 10/2026).
        """
        return cls.objects.exclude(sammelt_ignorierte=True)

    def natural_key(self) -> tuple[str]:
        return (self.name,)

    @property
    def anzeigename(self) -> Beschriftung:
        """The family "Not countable" belongs to the application. Every other one is maintained."""
        return NICHT_ZAEHLBAR if self.sammelt_ignorierte else self.name

    @property
    def anzeigebeschreibung(self) -> Beschriftung:
        return NICHT_ZAEHLBAR_BESCHREIBUNG if self.sammelt_ignorierte else self.beschreibung

    def clean(self) -> None:
        """A family stands nowhere below itself, and "Not countable" stands alone.

        A cycle would make every walk up the chain endless. "Not countable"
        sums nothing, so nothing belongs below it, and it belongs nowhere.
        """
        oben = self.uebergeordnet
        if oben is None:
            return
        if self.sammelt_ignorierte or oben.sammelt_ignorierte:
            raise ValidationError(
                {
                    "uebergeordnet": _(
                        "„Nicht zählbar“ steht für sich, ohne Ober- und Unterfamilien."
                    )
                }
            )
        if oben.pk == self.pk or (self.pk is not None and oben.pk in self.nachkommen_nummern()):
            raise ValidationError(
                {
                    "uebergeordnet": _(
                        "Eine Familie kann nicht unter sich selbst oder einer ihrer "
                        "Unterfamilien stehen."
                    )
                }
            )

    def delete(self, *args, **kwargs):
        """The family "Not countable" stays. Its products would otherwise be products again at once.

        Its house articles would notice nothing of it and stay ignored. The
        backstop for every path past the delete page.

        The families below move up one level, as the products end up
        without a family: what was grouped stays grouped, only one level
        less deep.
        """
        if self.sammelt_ignorierte:
            raise ValidationError(
                _("„Nicht zählbar“ gehört zum Datenmodell und lässt sich nicht löschen.")
            )
        self.unterfamilien.update(uebergeordnet=self.uebergeordnet)
        return super().delete(*args, **kwargs)

    @classmethod
    def nicht_zaehlbar(cls) -> Warenfamilie:
        """The family of the products that are none."""
        return cls.objects.get(sammelt_ignorierte=True)

    @property
    def anzahl_waren(self) -> int:
        return self.waren.count()

    # -- The chain ---------------------------------------------------------

    @staticmethod
    def _eltern() -> dict[int, int | None]:
        """Every family's parent, in one query. The chain is walked in memory."""
        return dict(Warenfamilie.objects.values_list("pk", "uebergeordnet_id"))

    @classmethod
    def mit_nachkommen(cls, nummern: Iterable[int]) -> set[int]:
        """The given families and everything below them, as numbers.

        Whoever chooses "bottled drinks" in a filter means the beers too.
        """
        eltern = cls._eltern()
        gewaehlt = set(nummern)
        gefunden = set(gewaehlt)
        for nummer in eltern:
            kette = []
            aktuell: int | None = nummer
            while aktuell is not None and aktuell not in gefunden and aktuell not in kette:
                kette.append(aktuell)
                aktuell = eltern.get(aktuell)
            if aktuell is not None and aktuell in gefunden:
                gefunden.update(kette)
        return gefunden

    def nachkommen_nummern(self) -> set[int]:
        return self.mit_nachkommen([self.pk]) - {self.pk}

    def vorfahren(self) -> list[Warenfamilie]:
        """From the family directly above up to the top. Empty at the top."""
        kette: list[Warenfamilie] = []
        aktuell = self.uebergeordnet
        while aktuell is not None and aktuell.pk != self.pk and aktuell not in kette:
            kette.append(aktuell)
            aktuell = aktuell.uebergeordnet
        return kette

    @property
    def pfad(self) -> list[Warenfamilie]:
        """From the top down to this family, for a breadcrumb."""
        return [*reversed(self.vorfahren()), self]

    @property
    def anzeigepfad(self) -> str:
        return " › ".join(str(familie.anzeigename) for familie in self.pfad)

    @property
    def tiefe(self) -> int:
        return len(self.vorfahren())

    def alle_waren(self):
        """The products of this family and of every family below it."""
        # Import here: 'ware.py' imports this module.
        from .ware import Ware  # noqa: PLC0415

        return Ware.objects.filter(warenfamilie_id__in=self.mit_nachkommen([self.pk]))

    @property
    def anzahl_aller_waren(self) -> int:
        return self.alle_waren().count()

    # -- The sum -----------------------------------------------------------

    def dimensionen(self) -> set[str]:
        """Which dimensions its products bring: piece count, mass, volume."""
        return set(self.alle_waren().values_list("basiseinheit__dimension", flat=True).distinct())

    def summendimensionen(self, leiteinheit=None, uebergeordnet=UNVERAENDERT) -> list[str]:
        """The dimensions a product of this family has to reach, top first.

        Its own lead unit and that of every family above: each of them sums
        this family's products. The form passes what it is about to set,
        before anything is stored. "Not countable" sums nothing.
        """
        if self.sammelt_ignorierte:
            return []
        einheit = self.leiteinheit if leiteinheit is None else leiteinheit
        oben: Warenfamilie | None
        if uebergeordnet is UNVERAENDERT:
            oben = self.uebergeordnet
        else:
            assert uebergeordnet is None or isinstance(uebergeordnet, Warenfamilie)
            oben = uebergeordnet
        kette = [einheit]
        gesehen = {self.pk}
        # A cycle is refused by 'clean()'. Guarded here anyway, so a walk
        # never runs endlessly on data written past the form.
        while oben is not None and oben.pk not in gesehen:
            gesehen.add(oben.pk)
            kette.append(oben.leiteinheit)
            oben = oben.uebergeordnet
        dimensionen: list[str] = []
        for einheit in reversed(kette):
            if einheit is not None and einheit.dimension not in dimensionen:
                dimensionen.append(einheit.dimension)
        return dimensionen

    def fehlende_faktoren(
        self, leiteinheit=None, waren=None, uebergeordnet=UNVERAENDERT
    ) -> list[tuple[Ware, str]]:
        """Product and dimension for every sum this family could not form.

        A product whose dimension deviates from a lead unit above it needs its
        factor there ('WareUmrechnung'). Without that entry every sum would be
        a claim. Checked are the products of the whole subtree, because the
        family sums them all. The family's form passes the unit, the parent
        and the products it is about to set, before anything is stored.
        """
        dimensionen = self.summendimensionen(leiteinheit, uebergeordnet)
        if not dimensionen:
            return []
        if waren is None:
            waren = (
                self.alle_waren()
                .select_related("basiseinheit")
                .prefetch_related("umrechnungen")
                .order_by("name")
            )
        return [
            (ware, dimension)
            for ware in waren
            for dimension in dimensionen
            if ware.faktor_nach(dimension) is None
        ]

    def waren_ohne_faktor(
        self, leiteinheit=None, waren=None, uebergeordnet=UNVERAENDERT
    ) -> list[Ware]:
        """The products that could not be summed somewhere up the chain, each once."""
        gefunden: list[Ware] = []
        for ware, _dimension in self.fehlende_faktoren(leiteinheit, waren, uebergeordnet):
            if ware not in gefunden:
                gefunden.append(ware)
        return gefunden

    def aufnehmen(self, ware) -> None:
        """A product added, and for the first one its unit counts as the lead unit."""
        if self.leiteinheit_id is None and not self.sammelt_ignorierte:
            self.leiteinheit = ware.basiseinheit
            self.save(update_fields=["leiteinheit"])
        ware.warenfamilie = self
        ware.save(update_fields=["warenfamilie"])
