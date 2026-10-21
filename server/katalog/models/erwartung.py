"""The expectation: which article stands at which location, with which packs."""

from collections import defaultdict
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

from .artikel import Artikel, Hausartikel, Rezepturartikel, Rezepturkomponente
from .base import GEFUEHRT, Sortiert
from .inventurart import Inventurart, arten_je_artikel

if TYPE_CHECKING:
    # Only for pyright, as explained in 'base.py'.
    from django.db.models.fields.related_descriptors import RelatedManager

    from organisation.models import Ort


class Erwartung(Sortiert):
    """Master data: which article is usually to be found at which location.

    Serves the completeness check and the sorting of the counting list.
    Counts at other locations remain possible and are marked 'unerwartet'
    there.
    """

    gruppenfeld = "ort"

    ort = models.ForeignKey(
        "organisation.Ort",
        on_delete=models.CASCADE,
        related_name="erwartungen",
        verbose_name=_("Ort"),
    )
    artikel = models.ForeignKey(
        Artikel,
        on_delete=models.CASCADE,
        related_name="erwartet_an",
        verbose_name=_("Artikel"),
    )
    #: Whether the product goes on the scale **at this location**. Weighable the
    #: product is everywhere or nowhere ('Ware.abwiegbar'). But it is only
    #: weighed where it lies loose: at the counter in the bowl, in storage in
    #: the box. Without the field every location got a weighing row that stayed
    #: open in storage at every inventory. As with the packs: what is not
    #: expected here can still be counted and then arises as unexpected. For
    #: non-weighable goods and for recipe articles the field says nothing.
    #:
    #: **Off unless someone says otherwise** (since 2026-09-29, before that on):
    #: most locations hold boxes, and whoever puts a counting list together
    #: had to take the tick out row by row. Where the scale is the only way to
    #: count an article, a new expectation starts with it ('nur_die_waage').
    #: Every new place goes through 'anlegen', which sets it only for goods
    #: that go onto the scale.
    wiegen_erwartet = models.BooleanField(
        default=False,
        verbose_name=_("Wiegen erwartet"),
        help_text=_("Ob der Artikel an diesem Ort üblicherweise gewogen wird."),
    )

    # Created by Django at runtime. Only named here so pyright knows them.
    ort_id: int
    artikel_id: int
    gebinde_vorgaben: RelatedManager[ErwartungGebinde]

    class Meta:
        verbose_name = _("Erwartung")
        verbose_name_plural = _("Erwartungen")
        ordering = ["ort", "sortierung"]
        constraints = [
            models.UniqueConstraint(
                fields=["ort", "artikel"],
                name="erwartung_je_ort_eindeutig",
            ),
            models.UniqueConstraint(
                fields=["ort", "sortierung"],
                name="sortierung_je_ort_eindeutig",
                violation_error_message=_("An diesem Ort ist die Position bereits vergeben."),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.ort} - {self.artikel.name}"

    def clean(self) -> None:
        """The article has to fit the location's house."""
        if self.ort_id is None or self.artikel_id is None:
            return
        artikel = self.artikel.konkret()
        haus = self.ort.haus

        if isinstance(artikel, Hausartikel):
            if artikel.haus_id != haus.pk:
                raise ValidationError(
                    {
                        "artikel": _("„%(name)s“ gehört zu %(haus)s, nicht zu %(ziel)s.")
                        % {"name": artikel.name, "haus": artikel.haus, "ziel": haus}
                    }
                )
            if artikel.ist_ausgeschieden:
                raise ValidationError(
                    {
                        "artikel": _("„%(name)s“ ist %(status)s und wird nirgends mehr erwartet.")
                        % {
                            "name": artikel.name,
                            "status": artikel.get_status_display().lower(),
                        }
                    }
                )
        elif isinstance(artikel, Rezepturartikel) and not artikel.aufloesbar_in(haus.pk):
            raise ValidationError(
                {
                    "artikel": _(
                        "„%(name)s“ lässt sich in %(haus)s nicht auflösen. Mindestens eine "
                        "Rezepturkomponente hat dort keinen geführten Hausartikel."
                    )
                    % {"name": artikel.name, "haus": haus}
                }
            )

    @classmethod
    def anlegen(
        cls,
        ort: Ort,
        artikel: Artikel,
        *,
        wiegen: bool = False,
        vorgaben: Iterable[tuple[int, Decimal | None]] = (),
    ) -> Erwartung:
        """A new place in a location's counting list, with its pack defaults.

        Every way that takes an article in comes through here: the counting
        list, creating a house article, the way back of a discontinued or
        ignored one and the closing. Until 10/2026 each had its own rule, and
        the closing expected the scale also of goods that are not weighed: a
        later switch of the product to the scale then brought a weighing row
        to exactly those places. 'wiegen' is what the way wants, kept only
        where the product goes onto the scale ('Artikel.abwiegbare_ware').

        'vorgaben' are pack and default quantity. One default per pack, the
        first named: the database allows no second. The position comes from
        'Sortiert', at the end of the list. Place and defaults arise together
        or not at all: until 10/2026 a failing default left the place behind
        without packs, and the article got no row there.
        """
        with transaction.atomic():
            erwartung = cls.objects.create(
                ort=ort, artikel=artikel, wiegen_erwartet=wiegen and artikel.abwiegbare_ware
            )
            mengen: dict[int, Decimal | None] = {}
            for gebinde_id, menge in vorgaben:
                mengen.setdefault(gebinde_id, menge)
            ErwartungGebinde.objects.bulk_create(
                ErwartungGebinde(erwartung=erwartung, gebinde_id=gebinde_id, standard_menge=menge)
                for gebinde_id, menge in mengen.items()
            )
        return erwartung

    def vorgaben_abgleichen(
        self, gewuenscht: dict[int, Decimal | None], angeboten: set[int]
    ) -> None:
        """The pack defaults as a row of the counting list wants them, tick and quantity in one go.

        'gewuenscht' are the ticked packs with their default quantity,
        'angeboten' all packs the row had to choose from. Removed are only
        defaults for packs that were also available in the row: an inactive
        pack should not lose its default because someone saves the counting
        list.
        """
        vorhanden = {satz.gebinde_id: satz for satz in self.gebinde_vorgaben.all()}

        ErwartungGebinde.objects.filter(
            erwartung=self,
            gebinde_id__in=(set(vorhanden) & angeboten) - set(gewuenscht),
        ).delete()

        for gebinde_id, menge in gewuenscht.items():
            if (satz := vorhanden.get(gebinde_id)) is None:
                ErwartungGebinde.objects.create(
                    erwartung=self, gebinde_id=gebinde_id, standard_menge=menge
                )
            elif satz.standard_menge != menge:
                satz.standard_menge = menge
                satz.save(update_fields=["standard_menge"])


def art_in_der_zaehlliste(haus_id: int, artikel_ids: Iterable[int]) -> dict[int, int]:
    """The section of the counting list an article stands in: the first of its types.

    A house article has one. A recipe article has those of its ingredients
    and stands under the first, as in the app. One without a carried
    ingredient in the house has none and stands under the first type of all,
    so it stays visible and can be taken out.
    """
    ids = set(artikel_ids)
    arten = arten_je_artikel(haus_id, ids)
    erste = Inventurart.erste().pk
    return {pk: (arten.get(pk) or [erste])[0] for pk in ids}


#: Set while 'neue_art_ans_ende' watches. A nested call leaves it to the outer one.
_ART_WIRD_BEOBACHTET: ContextVar[bool] = ContextVar("art_wird_beobachtet", default=False)


@contextmanager
def neue_art_ans_ende() -> Iterator[None]:
    """Whoever changes the type of articles inside this moves them to the end of the new type.

    At every location that expects them (wish of 2026-10-09): the counting
    list is divided by type, and an article that arrives in another section
    has no place there yet. Several at one location keep their order among
    each other.

    **Compared before and after, not told which articles change.** A type
    changes on many paths (article, product, product group, deleting a type,
    closing, merging, import), most of them with 'update()'. And a recipe
    article changes its section with its ingredient. Comparing catches all of
    it and leaves alone what kept its type. Every house is looked at, a few
    queries per house: changing a type is no mass business.

    The order stays one sequence per location ('Sortiert'). The section is
    not stored but follows from the article. Behind the last row of the
    location is therefore behind the last row of the new section.
    """
    if _ART_WIRD_BEOBACHTET.get():
        yield
        return
    marke = _ART_WIRD_BEOBACHTET.set(True)
    try:
        with transaction.atomic():
            vorher = _abschnitte()
            yield
            _gewechselte_ans_ende(vorher, _abschnitte())
    finally:
        _ART_WIRD_BEOBACHTET.reset(marke)


def _abschnitte() -> dict[int, tuple[int, int, int]]:
    """Per expectation its location, its place and its section, in every house."""
    zeilen = list(
        Erwartung.objects.values_list(
            "pk", "ort_id", "ort__bereich__haus_id", "artikel_id", "sortierung"
        )
    )
    artikel_je_haus: dict[int, set[int]] = defaultdict(set)
    for _pk, _ort, haus_id, artikel_id, _stelle in zeilen:
        artikel_je_haus[haus_id].add(artikel_id)
    art = {haus_id: art_in_der_zaehlliste(haus_id, ids) for haus_id, ids in artikel_je_haus.items()}
    return {
        pk: (ort_id, stelle, art[haus_id][artikel_id])
        for pk, ort_id, haus_id, artikel_id, stelle in zeilen
    }


def _gewechselte_ans_ende(
    vorher: dict[int, tuple[int, int, int]], nachher: dict[int, tuple[int, int, int]]
) -> None:
    letzte: dict[int, int] = defaultdict(int)
    for ort_id, stelle, _art in nachher.values():
        letzte[ort_id] = max(letzte[ort_id], stelle)
    gewechselt: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for pk, (ort_id, _stelle, art) in nachher.items():
        if pk in vorher and vorher[pk][2] != art:
            gewechselt[ort_id].append((vorher[pk][1], pk))
    # Behind the last place of the location, so no number is taken twice on the way.
    saetze = [
        Erwartung(pk=pk, sortierung=letzte[ort_id] + schritt)
        for ort_id, liste in gewechselt.items()
        for schritt, (_alt, pk) in enumerate(sorted(liste), start=1)
    ]
    if saetze:
        Erwartung.objects.bulk_update(saetze, ["sortierung"])


def nur_die_waage(artikel) -> bool:
    """Whether the scale is the only way to count this article.

    That is weighable goods without an active pack. An expectation of them
    without the scale would yield no row at all in the next inventory: the
    article would be in the counting list and missing from the app. Whoever
    creates an expectation for someone who has not decided yet asks here.
    """
    konkret = artikel.konkret()
    return (
        isinstance(konkret, Hausartikel)
        and konkret.ware.abwiegbar
        and not konkret.ware.gebinde.filter(aktiv=True).exists()
    )


def waehlbare_artikel(ort) -> models.QuerySet[Artikel]:
    """What can be expected at this location.

    Three things, in *one* query, because joining two querysets with '|'
    fails as soon as one of them carries 'distinct()':

    1. the carried house articles of this house, active and inactive.
       Seasonal goods have their place in the counting list too.
    2. the recipe articles **resolvable** here ('Rezepturartikel.aufloesbar_in'):
       every recipe component has a carried house article in this house.
       Having a recipe is not enough. Otherwise every recipe article would be
       available at every location, even where its ingredients are not
       carried at all, and 'Erwartung.clean()' would reject it when saving.
    3. what is **already expected** here, even if it no longer fulfils the
       first two conditions. Otherwise an entry whose ingredient was just
       discontinued would silently vanish from the list. This way it stays
       visible and can be removed deliberately.
    """
    haus_id = ort.bereich.haus_id

    # Per component: is there **one** house article that belongs at once to
    # this product, to this house and to the carried ones? As 'Exists' with
    # 'OuterRef', not as 'exclude(a=…, b=…)' over the relation: Django turns
    # that into two separate subqueries, and then an active row in
    # **another** house next to an inactive one in this one would suffice.
    gefuehrt_im_haus = Hausartikel.objects.filter(
        ware_id=models.OuterRef("ware_id"),
        haus_id=haus_id,
        status__in=GEFUEHRT,
    )
    unaufloesbar = Rezepturkomponente.objects.filter(
        rezepturartikel_id=models.OuterRef("pk")
    ).filter(~models.Exists(gefuehrt_im_haus))

    return (
        Artikel.objects.filter(
            models.Q(hausartikel__haus_id=haus_id, hausartikel__status__in=GEFUEHRT)
            | (models.Q(rezepturartikel__komponenten__isnull=False) & ~models.Exists(unaufloesbar))
            | models.Q(erwartet_an__ort=ort)
        )
        .distinct()
        .order_by("name")
    )


class ErwartungGebinde(models.Model):
    """Usual refill stocks per pack at a location."""

    erwartung = models.ForeignKey(
        Erwartung,
        on_delete=models.CASCADE,
        related_name="gebinde_vorgaben",
        verbose_name=_("Erwartung"),
    )
    gebinde = models.ForeignKey(
        "katalog.Gebinde",
        on_delete=models.CASCADE,
        related_name="ortsvorgaben",
        verbose_name=pgettext_lazy("Feld", "Gebinde"),
    )
    standard_menge = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text=_("Üblicher Auffüllbestand, dient als Vorschlag. Leer = kein Vorschlag."),
        verbose_name=_("Standardmenge"),
    )

    # Created by Django at runtime. Only named here so pyright knows them.
    erwartung_id: int
    gebinde_id: int

    class Meta:
        verbose_name = _("Gebinde-Vorgabe")
        verbose_name_plural = _("Gebinde-Vorgaben")
        ordering = ["gebinde__sortierung"]
        constraints = [
            models.UniqueConstraint(
                fields=["erwartung", "gebinde"],
                name="gebindevorgabe_je_erwartung_eindeutig",
                violation_error_message=_("Zu jedem Gebinde gibt es hier nur eine Vorgabe."),
            )
        ]

    def __str__(self) -> str:
        return f"{self.gebinde} - {self.erwartung.ort}"

    def clean(self) -> None:
        """The pack has to belong to the product of the expected house article."""
        if self.gebinde_id is None or self.erwartung_id is None:
            return
        artikel = self.erwartung.artikel.konkret()
        if not isinstance(artikel, Hausartikel):
            raise ValidationError(
                {
                    "gebinde": _(
                        "Rezepturartikel haben keine Gebinde. Hier ist keine Vorgabe möglich."
                    )
                }
            )
        if self.gebinde.ware_id != artikel.ware_id:
            raise ValidationError(
                {"gebinde": _("Das Gebinde gehört nicht zum erwarteten Artikel.")}
            )
