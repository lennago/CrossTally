"""What arises when counting: the count position and the weighing per vessel."""

from collections.abc import Iterable
from decimal import Decimal
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

from katalog.formats import HOECHSTMENGE
from katalog.models import (
    GEFUEHRT,
    Behaelter,
    Hausartikel,
    Rezepturartikel,
    menge_kurz,
)

from .base import ANS_ENDE, pruefe_im_haus, verteilt_ueber_rezepturen
from .inventur import Inventur

if TYPE_CHECKING:
    # Only for pyright: without the mypy plugin of the django-stubs, reverse
    # relations, '_id' columns and 'get_…_display' are unknown. Nothing of this
    # is loaded at runtime. The dependency direction of the apps stays as it is.
    from django.db.models.fields.related_descriptors import RelatedManager

    from katalog.models import Artikel, Basiseinheit


class Zaehlposition(models.Model):
    """An input row when counting: article, location and what is counted in.

    They are created at the start of the inventory from the expectations: one
    per expected pack, plus one for weighing where the product is weighable.
    The PWA fills them and creates further ones for unexpected things.

    'gebinde' stays empty where counting is not in packs: when weighing a
    product and for every recipe article. Which of those applies is said by
    the article. A marker of its own would be a second truth about it.

    'eingabemenge' is a **number**: packs or pieces. Weighing goes through
    'wiegungen': one row per vessel, each with its own container. The same
    product often lies in several bowls at one location, and each brings its
    own tare. A single input would subtract it only once. A gross weight
    therefore never belongs in this field. It would otherwise have two meanings.

    'sortierung' is the position in the location's counting list, taken over
    from the expectation when creating. A **copy**, and on purpose: the count
    positions arise exactly once, and whoever re-sorts the counting list
    afterwards should not pull the order out from under a running inventory.
    What is added when counting lands at the back ('ANS_ENDE').

    'zuletzt_geaendert' carries the sync with the PWA: when checking back in it
    fetches only what has changed since its last state. 'auto_now' applies on
    'save()' and on 'bulk_create', **not** on 'QuerySet.update()'. Whoever
    writes past it makes the row invisible to the PWA. In the application only
    the API writes, and that goes through 'save()'.
    """

    inventur = models.ForeignKey(
        Inventur,
        on_delete=models.CASCADE,
        related_name="zaehlpositionen",
        verbose_name=_("Inventur"),
    )
    ort = models.ForeignKey(
        "organisation.Ort",
        on_delete=models.PROTECT,
        related_name="zaehlpositionen",
        verbose_name=_("Ort"),
    )
    artikel = models.ForeignKey(
        "katalog.Artikel",
        on_delete=models.PROTECT,
        related_name="zaehlpositionen",
        verbose_name=_("Artikel"),
    )
    gebinde = models.ForeignKey(
        "katalog.Gebinde",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="zaehlpositionen",
        help_text=_("Leer beim Wiegen und bei Rezepturartikeln."),
        verbose_name=pgettext_lazy("Feld", "Gebinde"),
    )
    eingabemenge = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name=_("Anzahl"),
        help_text=_(
            "Anzahl der Gebinde bzw. Stücke, leer heißt ungezählt. Gewogenes steht bei den "
            "Wiegungen."
        ),
    )
    sortierung = models.PositiveIntegerField(
        default=ANS_ENDE,
        help_text=_("Stellung in der Zählliste des Orts, aus der Erwartung übernommen."),
        verbose_name=_("Sortierung"),
    )
    gezaehlt_am = models.DateTimeField(null=True, blank=True, verbose_name=_("gezählt am"))
    zuletzt_geaendert = models.DateTimeField(
        auto_now=True,
        db_index=True,
        help_text=_("Die Zähl-App lädt nur nach, was sich seitdem geändert hat."),
        verbose_name=_("zuletzt geändert"),
    )
    unerwartet = models.BooleanField(
        default=False,
        help_text=_("Beim Zählen dazugekommen, statt aus den Erwartungen zu stammen."),
        verbose_name=_("unerwartet"),
    )
    aktiv = models.BooleanField(
        default=True,
        help_text=_(
            "Gelöscht wird eine Zeile nicht, sie wird als gelöscht markiert. Sonst "
            "erführe die Zähl-App nichts davon."
        ),
        verbose_name=_("aktiv"),
    )

    # Created by Django at runtime. Only named here so pyright knows them.
    inventur_id: int
    ort_id: int
    artikel_id: int
    gebinde_id: int | None
    wiegungen: RelatedManager[Wiegung]

    class Meta:
        verbose_name = _("Zählposition")
        verbose_name_plural = _("Zählpositionen")
        ordering = [
            "ort__bereich__name",
            "ort__name",
            "sortierung",
            "artikel__name",
            "gebinde__sortierung",
        ]
        constraints = [
            # Two partial indexes instead of one: NULL counts as different from
            # everything in a unique index, the weighing and recipe article rows
            # could otherwise be created any number of times.
            models.UniqueConstraint(
                fields=["inventur", "ort", "artikel", "gebinde"],
                condition=models.Q(gebinde__isnull=False),
                name="eine_position_je_gebinde",
                violation_error_message=_(
                    "Für dieses Gebinde gibt es an diesem Ort schon eine Zeile."
                ),
            ),
            models.UniqueConstraint(
                fields=["inventur", "ort", "artikel"],
                condition=models.Q(gebinde__isnull=True),
                name="eine_position_ohne_gebinde",
                violation_error_message=_(
                    "Für diesen Artikel gibt es an diesem Ort schon eine Zeile ohne Gebinde."
                ),
            ),
        ]

    def __str__(self) -> str:
        worin = self.gebinde.anzeigename if self.gebinde_id else _("abgewogen")
        return f"{self.ort.name} - {self.artikel.name} ({worin})"

    def clean(self) -> None:
        """Location, article and pack have to fit inventory and article.

        For the **article** that applies only to house articles: they belong to a
        house, and a row with the article of another house would freeze an
        'Inventurergebnis' onto a foreign house article at closing, i.e. a
        quantity that would be booked at the wrong place in the inventory
        management system. The same rule is on the target stock and on the
        inventory note. A recipe article belongs to no house. If a component is
        missing there, its share lapses when resolving, and that is the intended
        treatment. Counted in a pack or as portions, the quantity has to fit into
        the result ('_menge_pruefen'). What is weighed is checked with the
        weighing ('wiegung_pruefen').
        """
        if self.inventur_id and self.ort_id and self.ort.bereich.haus_id != self.inventur.haus_id:
            raise ValidationError(
                {
                    "ort": _("„%(ort)s“ liegt nicht in %(haus)s.")
                    % {"ort": self.ort, "haus": self.inventur.haus}
                }
            )
        if self.artikel_id is None:
            return
        artikel = self.artikel.konkret()
        if self.inventur_id and isinstance(artikel, Hausartikel):
            pruefe_im_haus(self.inventur, artikel, "artikel")
        if self.gebinde_id is None:
            # Without a pack only the scale remains, or a recipe article counted
            # in its own unit. For everything else there would be nothing to
            # enter here.
            if isinstance(artikel, Hausartikel) and not artikel.ware.abwiegbar:
                raise ValidationError(
                    {
                        "gebinde": _(
                            "„%(name)s“ wird nicht gewogen. Ohne Gebinde lässt sich hier "
                            "nichts zählen."
                        )
                        % {"name": artikel.name}
                    }
                )
            if isinstance(artikel, Rezepturartikel) and not artikel.wird_gewogen:
                self._menge_pruefen(artikel)
            return
        if not isinstance(artikel, Hausartikel):
            raise ValidationError(
                {"gebinde": _("Rezepturartikel werden nicht in Gebinden gezählt.")}
            )
        if self.gebinde.ware_id != artikel.ware_id:
            raise ValidationError({"gebinde": _("Das Gebinde gehört nicht zu diesem Artikel.")})
        self._menge_pruefen(artikel)

    def _menge_pruefen(self, artikel: Artikel) -> None:
        """Number times pack, or the number of portions, has to fit into the result.

        99,999,999 crates of 20 are two billion pieces. The closing would stop
        on it ('Inventur._ergebnisse'), but whoever typed it should hear it
        while counting, not the manager at closing. Without a pack the number
        of a house article is the quantity itself, and its field holds no more
        than the result. A portion reaches the result through its recipe, and
        140 g of nachos each make ten million portions 1.4 billion grams.
        """
        if self.eingabemenge is None:
            return
        self._ergebnis_pruefen(artikel, self.menge_in_basiseinheit(), "eingabemenge")

    def wiegung_pruefen(self, wiegung: Wiegung) -> None:
        """The weighings of this row, with 'wiegung' as it would stand, have to fit into the result.

        One bowl holds less than a billion grams, but the bowls of a row add
        up, a house may count the product in a much smaller unit, and a mix
        reaches the result through its recipe. Until 10/2026 only the closing
        noticed, and the manager heard of a typo nobody could find any more.
        The other weighings are read from the database, not from a prefetch:
        'wiegung' may have been written a moment ago, in the same transaction.
        """
        artikel = self.artikel.konkret()
        andere = self.wiegungen.exclude(pk=wiegung.pk).select_related("behaelter")
        netto = sum(
            (
                artikel.in_basiseinheit(eintrag.brutto_gramm, eintrag.behaelter)
                for eintrag in [*andere, wiegung]
            ),
            Decimal("0"),
        )
        self._ergebnis_pruefen(artikel, netto, "brutto_gramm")

    def _ergebnis_pruefen(self, artikel: Artikel, menge: Decimal | None, feld: str) -> None:
        """What this row alone would freeze has to fit: twelve digits, three after the point.

        Rounded to the places of the unit first, as the closing rounds before
        it compares ('Inventur._ergebnisse'). The message lands on 'feld',
        the number the person typed. Rows of the same article at other places
        add up only at closing, and that is where their sum is checked.
        """
        if menge is None:
            return
        for name, anteil, einheit in self._im_ergebnis(artikel, menge):
            gerundet = einheit.runden(anteil)
            if abs(gerundet) < HOECHSTMENGE:
                continue
            werte = {"menge": einheit.mit_menge(gerundet), "name": name}
            if name is None:
                meldung = _(
                    "Das wären %(menge)s, mehr als ein Ergebnis fasst. Bitte die Zahl prüfen."
                )
            else:
                meldung = _(
                    "Für „%(name)s“ wären das %(menge)s, mehr als ein Ergebnis fasst. Bitte die "
                    "Zahl prüfen."
                )
            raise ValidationError({feld: meldung % werte})

    def _im_ergebnis(
        self, artikel: Artikel, menge: Decimal
    ) -> list[tuple[str | None, Decimal, Basiseinheit]]:
        """Per house article what the closing would freeze from this row: name, quantity, unit.

        A house article freezes the quantity itself, in its house's unit, and
        needs no name: the row says which it is. A recipe article spreads over
        its recipe onto the carried house articles of this house, each in its
        house's unit, the way 'Inventur.mengen_je_hausartikel' resolves it. A
        share without a carried house article lapses there, so it is not
        checked here either. The name is the product's, as the app shows it.
        """
        if isinstance(artikel, Hausartikel):
            return [(None, menge, artikel.basiseinheit)]
        if not isinstance(artikel, Rezepturartikel):
            return []
        nach_ware = verteilt_ueber_rezepturen({artikel.pk: menge})
        zutaten = Hausartikel.objects.filter(
            haus_id=self.inventur.haus_id, status__in=GEFUEHRT, ware_id__in=nach_ware
        ).select_related("basiseinheit", "ware__basiseinheit")
        return [
            (zutat.ware.name, zutat.in_hauseinheit(nach_ware[zutat.ware_id]), zutat.basiseinheit)
            for zutat in zutaten
        ]

    @property
    def wird_gewogen(self) -> bool:
        """Whether the input is a gross weight instead of a number."""
        if self.gebinde_id is not None:
            return False
        artikel = self.artikel.konkret()
        if isinstance(artikel, Rezepturartikel):
            return artikel.wird_gewogen
        return artikel.abwiegbare_ware

    def behaelter_zur_wahl(self):
        """What can be weighed in on this row ('Behaelter.zur_wahl_fuer')."""
        artikel = self.artikel.konkret()
        return Behaelter.zur_wahl_fuer(
            artikel.ware if isinstance(artikel, Hausartikel) else artikel
        )

    def menge_in_basiseinheit(self) -> Decimal | None:
        """What was counted here, in the unit of the counted article, otherwise None.

        Three paths, and the article says which applies: pack times factor, sum
        of the weighings, or the bare piece count of a counted recipe article.
        For recipe articles the result is the quantity in **their** unit. Onto
        the products it is distributed only by 'Rezepturartikel.verteilen'. For
        the house article it is the unit of the **house**: the pack computes in
        the product's unit, and from there it goes one step further where the
        house counts differently.
        """
        if self.gebinde_id is None and self.wird_gewogen:
            return self.netto_der_wiegungen()
        if self.eingabemenge is None:
            return None
        artikel = self.artikel.konkret()
        if self.gebinde_id is not None:
            menge = self.gebinde.in_basiseinheit(self.eingabemenge)
            if isinstance(artikel, Hausartikel):
                return artikel.in_hauseinheit(menge)
            return menge
        if isinstance(artikel, Rezepturartikel):
            return artikel.in_basiseinheit(self.eingabemenge)
        # House articles without pack and without scale are not allowed by 'clean()'.
        # For legacy data the input remains the quantity in the base unit.
        return self.eingabemenge

    def netto_der_wiegungen(self) -> Decimal | None:
        """Sum of the weighings, **per vessel** relieved of its tare.

        That is exactly why they are rows of their own: three bowls of 250 g tare
        each subtract 750 g, not 250 g. Without a weighing nothing is counted
        here, because a zero would be the claim that the shelf is empty.
        """
        wiegungen = list(self.wiegungen.all())
        if not wiegungen:
            return None
        return sum((wiegung.netto() for wiegung in wiegungen), Decimal("0"))

    @property
    def ist_gezaehlt(self) -> bool:
        """Whether someone has entered something here already."""
        return self.menge_in_basiseinheit() is not None


class Wiegung(models.Model):
    """A vessel on the scale: gross weight in grams and the container with it.

    There are several per count position, and that is the whole purpose: the
    same product often lies in several bowls at one location, and **each**
    brings its own tare. A single value would subtract it only once and count
    500 grams too much with three 250-gram bowls.

    The container is required. "Without container" exists as a record of its
    own with tare 0. Whoever weighs on the zeroed scale chooses it. An empty
    entry could not be told apart from someone having forgotten the vessel.
    """

    zaehlposition = models.ForeignKey(
        Zaehlposition,
        on_delete=models.CASCADE,
        related_name="wiegungen",
        verbose_name=_("Zählposition"),
    )
    brutto_gramm = models.DecimalField(
        max_digits=12,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0"))],
        verbose_name=_("Brutto in Gramm"),
        help_text=_("Was die Waage anzeigt, samt Behälter."),
    )
    behaelter = models.ForeignKey(
        "katalog.Behaelter",
        on_delete=models.PROTECT,
        related_name="wiegungen",
        verbose_name=pgettext_lazy("Feld", "Behälter"),
    )
    erfasst_am = models.DateTimeField(auto_now_add=True, verbose_name=_("erfasst am"))
    # No default: the device assigns the id, and a row without one is one
    # nobody resubmits. NULL does not collide in the index.
    kennung = models.UUIDField(
        null=True,
        blank=True,
        unique=True,
        # A weighing otherwise has no feature that distinguishes it from the next
        # bowl. Without the id a repeated job would count twice.
        help_text=_("Vergibt das Gerät. Daran wird eine doppelt gesendete Wiegung erkannt."),
        verbose_name=_("Kennung"),
    )

    # Created by Django at runtime. Only named here so pyright knows them.
    zaehlposition_id: int
    behaelter_id: int

    class Meta:
        verbose_name = _("Wiegung")
        verbose_name_plural = _("Wiegungen")
        ordering = ["erfasst_am", "pk"]

    def __str__(self) -> str:
        return f"{menge_kurz(self.brutto_gramm)} g in {self.behaelter.anzeigename}"

    def clean(self) -> None:
        """Weighing happens only where weighing is possible, and only as much as a result holds."""
        if self.zaehlposition_id is None:
            return
        if not self.zaehlposition.wird_gewogen:
            raise ValidationError(
                {
                    "zaehlposition": _("„%(name)s“ wird nicht gewogen.")
                    % {"name": self.zaehlposition.artikel.name}
                }
            )
        erlaubt = self.zaehlposition.behaelter_zur_wahl()
        if self.behaelter_id is not None and not erlaubt.filter(pk=self.behaelter_id).exists():
            raise ValidationError(
                {
                    "behaelter": _(
                        "„%(behaelter)s“ steht für „%(artikel)s“ nicht zur Wahl. Er ist "
                        "weder dem Artikel noch einem Haus zugeordnet."
                    )
                    % {
                        "behaelter": self.behaelter.anzeigename,
                        "artikel": self.zaehlposition.artikel.name,
                    }
                }
            )
        if self.brutto_gramm is not None and self.behaelter_id is not None:
            self.zaehlposition.wiegung_pruefen(self)

    def netto(self) -> Decimal:
        """The weighed amount in the article's unit, minus **this** tare.

        For the house article that is the house's unit. Both kinds of article
        know the way from the scale to there.
        """
        return self.zaehlposition.artikel.konkret().in_basiseinheit(
            self.brutto_gramm, self.behaelter
        )


def zeilen_der_inventuren_sperren(nummern: Iterable[int]) -> None:
    """Every count position of these inventories, locked until the transaction ends.

    For deleting an inventory, once it is locked without the key. A quantity
    or a weighing of the app locks only its own row while it writes
    ('api.serializers.counting.gesperrte_zeile'). One that holds a row now is
    waited for, and Django then collects what it wrote. One that comes later
    finds its row gone. Until 10/2026 only the inventory was locked: a
    weighing written between Django's collecting and its delete ended the
    delete at the weighing's foreign key, a server error.

    The rows of all of them in one query, by number: merging a product locks
    its rows by number too, across inventories ('katalog.models.zaehlpositionen_sperren').
    Locked one inventory after the other, the two could wait for each other.
    Not by 'Meta.ordering': that joins the nullable pack, and PostgreSQL
    refuses to lock the nullable side of an outer join.
    """
    list(
        Zaehlposition.objects.select_for_update()
        .filter(inventur_id__in=sorted(set(nummern)))
        .order_by("pk")
        .values_list("pk", flat=True)
    )
