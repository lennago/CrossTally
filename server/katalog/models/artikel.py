"""What is counted: the house article of a house and the recipe article with its recipe."""

import operator
import re
from collections.abc import Callable, Iterable
from decimal import Decimal
from functools import reduce
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Value
from django.db.models.functions import Coalesce, NullIf
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

from django_project.renaming import umbenennen

from .base import (
    AUSGESCHIEDEN,
    GEFUEHRT,
    QUELLE_EIGEN,
    Artikelstatus,
    nur_ziffern,
    quellenwahl,
)
from .inventurart import Inventurart, Zaehlumfang
from .units import (
    FAKTOR_HOECHSTENS,
    FAKTOR_MINDESTENS,
    FAKTOR_NACHKOMMASTELLEN,
    FAKTOR_STELLEN_GESAMT,
    Basiseinheit,
    Dimension,
    braucht_faktor,
    faktor_gerundet,
    menge_kurz,
    netto_in_basiseinheit,
    umrechnung,
)
from .ware import EINHEIT_NUR_FUER_FAMILIEN, Behaelter, Ware
from .warengruppe import Warengruppe

if TYPE_CHECKING:
    # Only for pyright, as explained in 'base.py'.
    from django.db.models.fields.related_descriptors import RelatedManager

    from inventur.models import (
        Inventurergebnis,
        Inventurnotiz,
        Sollbestand,
        Sollzeile,
        Zaehlposition,
    )

    from .erwartung import Erwartung
    from .ware import Gebinde


#: A row of an inventory not yet closed: counting, targets and notes still
#: move there. The catalog does not know the inventory ('organisation' <-
#: 'katalog' <- 'inventur'), so the closed status stands here as text, once
#: ('Inventurstatus.ABGESCHLOSSEN').
IN_LAUFENDER_INVENTUR = ~models.Q(inventur__status="ABGESCHLOSSEN")


def ergebnisfaktor_zu_gross(
    name: str, inventur: object, ergebniseinheit: str, menge: Decimal, einheit: str
) -> str:
    """The refusal for a frozen result whose factor would outgrow its column, on every path.

    A change of the product's unit, a merge ('katalog.ware_unit_change') and
    the move of an article to another product ('_faktoren_pruefen') each find
    it. One wording, so the same case reads the same wherever it is found.
    """
    return _(
        "Im Ergebnis von „%(name)s“ der Inventur %(inventur)s wäre 1 "
        "%(ergebniseinheit)s danach %(menge)s %(einheit)s. Eine Umrechnung "
        "fasst höchstens %(grenze)s."
    ) % {
        "name": name,
        "inventur": inventur,
        "ergebniseinheit": ergebniseinheit,
        "menge": menge_kurz(menge),
        "einheit": einheit,
        "grenze": menge_kurz(FAKTOR_HOECHSTENS - FAKTOR_MINDESTENS),
    }


def einheit_gebunden(name: str, inventuren: list[str], einheit: Basiseinheit) -> str:
    """Why the house's unit cannot change now. For the model, its form and the import plan."""
    return _(
        "„%(name)s“ hat in %(inventuren)s ein Soll, eine offene Sollzeile oder eine "
        "Differenz von Hand. Diese Zahlen stehen in %(einheit)s, und bis zum Abschluss "
        "bleibt das Haus bei dieser Einheit."
    ) % {
        "name": name,
        "inventuren": ", ".join(f"„{inventur}“" for inventur in inventuren),
        "einheit": einheit.kurz,
    }


def nummer_sortierschluessel(nummer: str) -> tuple:
    """Article numbers in the order the counting app and the inventory system use.

    Runs of digits count as numbers, the rest as text: "9" before "1012"
    before "101002", and "101003" before "101003-1". Sorted as text, a
    shorter number stood between the longer ones, and whoever works a sheet
    next to the inventory system's screen looked for it in vain (the app
    compares with 'localeCompare' and 'numeric' since 2026-10-12).
    """
    return tuple(
        (0, int(teil)) if teil.isdecimal() else (1, teil.casefold())
        for teil in re.split(r"(\d+)", nummer)
        if teil
    )


def plaetze_abbilden(
    plaetze: list[dict], orte: dict[int, int], gebinde: dict[int, int]
) -> list[dict]:
    """A memory of places ('erwartungen_vorher') with locations and packs renumbered.

    Places that land on one location become one. The place that kept its
    location wins, the other adds only the packs it lacks and the scale, as
    when the locations merge. A place that moves onto another location
    forgets its position and comes back at the end, as the merge appends
    what it brings. A pack named twice adds up its defaults, as
    two duplicates do when their products merge ('ware_merge._vorgaben_addieren').
    """
    zusammen: dict[int, dict] = {}
    for platz in sorted(plaetze, key=lambda platz: platz["ort"] in orte):
        bisheriger: int = platz["ort"]
        ort = orte.get(bisheriger, bisheriger)
        vorgaben = _vorgaben_abbilden(platz["gebinde"], gebinde)
        if (bisher := zusammen.get(ort)) is None:
            eintrag = {**platz, "ort": ort, "gebinde": vorgaben}
            if ort != bisheriger:
                # Its position in the vanished list means nothing in this one.
                eintrag["stelle"] = None
            zusammen[ort] = eintrag
            continue
        vorhanden = {vorgabe["gebinde"] for vorgabe in bisher["gebinde"]}
        bisher["gebinde"] += [v for v in vorgaben if v["gebinde"] not in vorhanden]
        bisher["wiegen"] = bool(bisher["wiegen"] or platz["wiegen"])
    return list(zusammen.values())


def plaetze_vereinen(eigene: list[dict], dazu: list[dict]) -> list[dict]:
    """Two memories of places as one, as two duplicates merge their expectations.

    The own place at a location wins with its position. The other adds its
    packs there, a pack named in both adds up its defaults, and the scale
    counts if either expected it ('ware_merge._vorgaben_addieren'). A place
    at a location of its own comes along whole.
    """
    je_ort: dict[int, dict] = {platz["ort"]: dict(platz) for platz in eigene}
    for platz in dazu:
        if (bisher := je_ort.get(platz["ort"])) is None:
            je_ort[platz["ort"]] = dict(platz)
            continue
        bisher["gebinde"] = _vorgaben_abbilden([*bisher["gebinde"], *platz["gebinde"]], {})
        bisher["wiegen"] = bool(bisher["wiegen"] or platz["wiegen"])
    return list(je_ort.values())


def _vorgaben_abbilden(vorgaben: list[dict], gebinde: dict[int, int]) -> list[dict]:
    je_gebinde: dict[int, dict] = {}
    for vorgabe in vorgaben:
        bisherige: int = vorgabe["gebinde"]
        nummer = gebinde.get(bisherige, bisherige)
        if (bisher := je_gebinde.get(nummer)) is None:
            je_gebinde[nummer] = {**vorgabe, "gebinde": nummer}
        elif vorgabe["standardmenge"] is not None:
            summe = Decimal(bisher["standardmenge"] or "0") + Decimal(vorgabe["standardmenge"])
            bisher["standardmenge"] = str(summe)
    return list(je_gebinde.values())


def artikel_sperren(nummern: Iterable[int]) -> set[int]:
    """Locks these articles until the transaction ends. Back come the numbers that still exist.

    Where a new row of the app meets merging or splitting a product. A row
    that does not exist yet cannot be locked, its article can: the app locks
    it before it reads article and pack and creates the row
    ('api.serializers.counting.gesperrter_artikel'), merging and splitting lock every
    article whose product changes before anything else
    ('zaehlpositionen_sperren'). Whoever comes second waits and then reads
    what the first one left. A row created first is re-hung along, and a row
    that comes after a merge finds its pack gone and says so. Until 10/2026
    such a row landed on the pack the merge was deleting, and its commit
    ended in a server error.

    'FOR NO KEY UPDATE': the foreign keys of other rows go on checking
    against the article meanwhile. By number and without 'Meta.ordering',
    in one order for everyone. A no-op under SQLite.
    """
    return set(
        Artikel.objects.select_for_update(no_key=True)
        .filter(pk__in=list(nummern))
        .order_by("pk")
        .values_list("pk", flat=True)
    )


def zaehlpositionen_sperren(
    besitzer: Iterable[Artikel | Gebinde],
) -> models.QuerySet[Zaehlposition] | None:
    """Locks the count positions of these articles and packs until the transaction ends.

    For the paths that hang rows of running inventories onto another product
    or pack: merging and splitting a product. They lock before they change
    anything, by number, as the closing does ('Inventur.zum_abschluss_sperren').
    A write from the app that holds one of the rows has finished by then, and
    one that comes later waits and reads the row as it ends up
    ('api.serializers.counting.gesperrte_zeile'). Whatever timestamp the rows get
    afterwards is later than the app's, so its next sync fetches them. Until
    10/2026 nothing was locked: the app could write the old pack back, and a
    merge that took its timestamp before waiting for a row fell behind the app's.

    The articles come first ('artikel_sperren'): a row the app creates in
    the same moment is then either among the rows found here or waits until
    the product has changed.

    Back come the locked rows, to touch them at the end, or None where there
    are none. Through the reverse relations and not through 'Zaehlposition':
    the catalog does not know the inventory ('organisation' <- 'katalog' <-
    'inventur'). By number and not by 'Meta.ordering', which joins the
    nullable pack: PostgreSQL refuses to lock the nullable side of an outer join.
    """
    besitzer = list(besitzer)
    artikel_sperren(eintrag.pk for eintrag in besitzer if isinstance(eintrag, Artikel))
    zeilen = [eintrag.zaehlpositionen.all() for eintrag in besitzer]
    if not zeilen:
        return None
    alle = reduce(operator.or_, zeilen)
    nummern = list(alle.select_for_update().order_by("pk").values_list("pk", flat=True))
    return alle.model.objects.filter(pk__in=nummern) if nummern else None


class Artikel(models.Model):
    """Common core of house and recipe articles."""

    name = models.CharField(max_length=200, verbose_name=_("Name"))
    erstellt_am = models.DateTimeField(auto_now_add=True, verbose_name=_("erstellt am"))
    geaendert_am = models.DateTimeField(auto_now=True, verbose_name=_("zuletzt geändert am"))

    # Created by Django at runtime. Only named here so pyright knows them.
    erwartet_an: RelatedManager[Erwartung]
    zaehlpositionen: RelatedManager[Zaehlposition]

    class Meta:
        verbose_name = _("Artikel")
        verbose_name_plural = _("Artikel")
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        """A new name reaches the rows that show it ('django_project.renaming')."""
        with umbenennen(self, kwargs.get("update_fields"), lambda: zaehlpositionen_sperren([self])):
            super().save(*args, **kwargs)

    @property
    def haeuser(self):
        """Houses in which the article occurs according to an expectation."""
        return sorted({e.ort.haus for e in self.erwartet_an.all()}, key=lambda h: h.name)

    @property
    def ist_hausartikel(self) -> bool:
        return hasattr(self, "hausartikel")

    def konkret(self) -> Artikel:
        """The concrete form of this article.

        Costs one query per call. Where many articles are processed, load the
        queryset with 'select_related("hausartikel", "rezepturartikel")'.

        If the concrete form is already there, nothing is reloaded: under
        multi-table inheritance a house article also carries the access
        'hausartikel'. It led into a query that fetches the same object once
        more. In a list with a hundred rows that is a hundred queries for nothing.
        """
        if type(self) is not Artikel:
            return self
        for name in ("hausartikel", "rezepturartikel"):
            child = getattr(self, name, None)
            if child is not None:
                return child
        return self

    @property
    def warenname(self) -> str:
        """What the product is called, not what the record is called.

        When counting, the **product** is on the shelf: the product carries the
        maintained name, the house article the one from the inventory management
        system, and that is often abbreviated and hard to read. A recipe article
        belongs to no house and has only its own name.
        """
        konkret = self.konkret()
        if isinstance(konkret, Hausartikel):
            return konkret.ware.name
        return konkret.name

    @property
    def name_im_haus(self) -> str:
        """The house's designation, empty where there is none or it is the same."""
        konkret = self.konkret()
        if isinstance(konkret, Hausartikel) and konkret.name != konkret.ware.name:
            return konkret.name
        return ""

    def zaehlpflichtig_bei(self, umfang: Zaehlumfang, haus_id: int) -> bool:
        """Whether the article has to be counted in an inventory of this scope and at the house."""
        konkret = self.konkret()
        if konkret is self:
            raise NotImplementedError(f"Artikel {self.pk} ist weder Grund- noch Rezepturartikel.")
        return konkret.zaehlpflichtig_bei(umfang, haus_id)

    @property
    def abwiegbare_ware(self) -> bool:
        """Whether a house article stands here whose product goes onto the scale.

        Count positions, counting list and closing asked it in the same words
        (until 10/2026). A recipe article is never one: whether it is weighed
        says its unit ('Rezepturartikel.wird_gewogen').
        """
        konkret = self.konkret()
        return konkret is not self and konkret.abwiegbare_ware


class Hausartikel(Artikel):
    """What a house carries: the row whose result lands in its inventory management system.

    Where the row comes from is said by 'quelle': a format from
    'katalog.formats', or "eigen" for an article no system knows. A house
    without an inventory management system (restaurant, workshop) carries only
    own ones. The number follows the source: the format checks and normalises
    it, an own article needs none.
    """

    quelle = models.CharField(
        max_length=20,
        choices=quellenwahl,
        default="winticket",
        verbose_name=_("Quelle"),
        help_text=_(
            "Das System, das den Artikel führt. „Eigen“ heißt: nur in dieser Anwendung, "
            "ohne Nummer."
        ),
    )

    haus = models.ForeignKey(
        "organisation.Haus",
        on_delete=models.PROTECT,
        related_name="hausartikel",
        verbose_name=_("Haus"),
    )
    ware = models.ForeignKey(
        Ware,
        on_delete=models.PROTECT,
        related_name="hausartikel",
        help_text=_("Dieselbe Ware über alle Häuser hinweg. An ihr hängen Einheit und Gebinde."),
        verbose_name=_("Ware"),
    )
    warengruppe = models.ForeignKey(
        Warengruppe,
        on_delete=models.PROTECT,
        related_name="hausartikel",
        verbose_name=_("Warengruppe"),
    )
    # On the house article and not on the product: a product may be counted
    # monthly in one house and annually in another. Where that is not wanted,
    # the product page sets it for all its articles at once.
    inventurart = models.ForeignKey(
        Inventurart,
        on_delete=models.PROTECT,
        related_name="hausartikel",
        verbose_name=_("Inventurart"),
        help_text=_("In welchem Reiter der Inventur der Artikel gezählt wird."),
    )
    # The house's unit. It comes from the inventory management system and is
    # almost always the product's. Where it deviates, 'faktor_zur_ware' converts
    # into the product: the product is the lead unit of the report, the house
    # gets its numbers back in its unit.
    basiseinheit = models.ForeignKey(
        Basiseinheit,
        on_delete=models.PROTECT,
        related_name="hausartikel",
        verbose_name=_("Einheit im Haus"),
        help_text=_("So führt dieses Haus den Artikel. Meist die Einheit der Ware."),
    )
    faktor_zur_ware = models.DecimalField(
        max_digits=FAKTOR_STELLEN_GESAMT,
        decimal_places=FAKTOR_NACHKOMMASTELLEN,
        null=True,
        blank=True,
        validators=[MinValueValidator(FAKTOR_MINDESTENS)],
        verbose_name=_("Umrechnung zur Ware"),
        help_text=_(
            "Nur, wenn das Haus in einer anderen Dimension zählt als die Ware: wie viele "
            "Einheiten der Ware ist eine Einheit dieses Hauses? Beispiel: die Ware wird in g "
            "geführt, das Haus zählt in Stück, und ein Stück wiegt 1 kg. Dann steht hier 1000."
        ),
    )
    artikelnummer = models.CharField(
        max_length=20,
        blank=True,
        help_text=_(
            "Die Nummer, unter der die Warenwirtschaft den Artikel führt. Leer bei einem "
            "eigenen Artikel."
        ),
        verbose_name=_("Artikelnummer"),
    )
    ean = models.CharField(
        max_length=14,
        blank=True,
        validators=[nur_ziffern],
        help_text=_("Optional. Die Suche in den Listen findet den Artikel auch darüber."),
        verbose_name=_("EAN"),
    )
    status = models.CharField(
        max_length=12,
        choices=Artikelstatus.choices,
        default=Artikelstatus.AKTIV,
        verbose_name=_("Status"),
    )
    # Derived, so the many queries for "aktiv" can stay as they are, and none
    # of them ever misses a state that is new.
    aktiv = models.GeneratedField(
        # Django also takes a 'Q' here. The stubs know only 'Expression'.
        expression=models.Q(status=Artikelstatus.AKTIV),  # pyright: ignore[reportArgumentType]
        output_field=models.BooleanField(),
        db_persist=True,
        verbose_name=_("aktiv"),
    )
    gueltig_bis = models.DateTimeField(
        verbose_name=_("nicht mehr aktiv seit"),
        blank=True,
        null=True,
        help_text=_("Wird mit dem Status gesetzt: leer genau dann, wenn der Artikel aktiv ist."),
    )
    #: A duplicate: the same thing under a second number, discontinued in favour
    #: of the row it counts as. The inventory management system may still book
    #: on its number, so its target stock is added to that row when read in.
    #: Only while discontinued or ignored: whoever brings it back clears it
    #: ('_status_angleichen'). Set by merging two products ('ware_merge').
    zaehlt_als = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dubletten",
        verbose_name=_("Zählt als"),
    )
    #: What the row was before its product went into "Not countable", so the
    #: way back restores exactly that ('_status_angleichen'). A product goes
    #: there to stay and comes back only after a wrong click (decision of
    #: 2026-10-14). Empty for a row that was ignored from the start: it comes
    #: back as the duplicate rule decides ('Ware._dubletten_markieren').
    status_vorher = models.CharField(
        max_length=12,
        choices=Artikelstatus.choices,
        blank=True,
        default="",
        editable=False,
        verbose_name=_("Status vor „Nicht zählbar“"),
    )
    #: The places in the counting lists the article had before it was
    #: discontinued or ignored: per location the packs with their defaults,
    #: whether weighing was expected and its position ('stelle', since
    #: 2026-10-18). The way back brings them along, as far as location and
    #: packs still exist ('_plaetze_wiederherstellen', since 2026-10-16).
    #: Empty while the article is carried, and for rows that left before that
    #: date.
    erwartungen_vorher = models.JSONField(
        default=list,
        blank=True,
        editable=False,
        verbose_name=_("Plätze vor dem Ausscheiden"),
    )

    # Created by Django at runtime. Only named here so pyright knows them.
    artikel_ptr: Artikel
    artikel_ptr_id: int
    haus_id: int
    ware_id: int
    warengruppe_id: int
    inventurart_id: int
    basiseinheit_id: int
    zaehlt_als_id: int | None
    dubletten: RelatedManager[Hausartikel]
    get_status_display: Callable[[], str]
    get_quelle_display: Callable[[], str]
    sollbestaende: RelatedManager[Sollbestand]
    sollzeilen: RelatedManager[Sollzeile]
    inventurnotizen: RelatedManager[Inventurnotiz]
    inventurergebnisse: RelatedManager[Inventurergebnis]

    class Meta:
        verbose_name = _("Hausartikel")
        verbose_name_plural = _("Hausartikel")
        # Without a number by the product's name: own articles would otherwise all
        # come first with their empty number (decision of 2026-09-23). Digits sort
        # before letters, so a numbered article stays in front.
        ordering = [
            "warengruppe__name",
            Coalesce(NullIf("artikelnummer", Value("")), "ware__name"),
            "name",
        ]
        constraints = [
            # Only with a number: own articles have none, and a house may share the
            # empty one any number of times. The product distinguishes them.
            models.UniqueConstraint(
                fields=["haus", "artikelnummer"],
                condition=models.Q(status__in=GEFUEHRT) & ~models.Q(artikelnummer=""),
                name="ein_gefuehrter_hausartikel_je_haus_und_nummer",
                violation_error_message=_("In diesem Haus ist die Artikelnummer bereits vergeben."),
            ),
            models.UniqueConstraint(
                fields=["haus", "ware"],
                condition=models.Q(status__in=GEFUEHRT),
                name="ein_gefuehrter_hausartikel_je_haus_und_ware",
                violation_error_message=_("Dieses Haus führt diese Ware bereits."),
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status=Artikelstatus.AKTIV, gueltig_bis__isnull=True)
                    | (~models.Q(status=Artikelstatus.AKTIV) & models.Q(gueltig_bis__isnull=False))
                ),
                name="gueltig_bis_genau_bei_nicht_aktiv",
                violation_error_message=_(
                    "Ein nicht aktiver Hausartikel braucht ein Datum, seit dem er es "
                    "nicht mehr ist. Ein aktiver darf keines haben."
                ),
            ),
            # Ignored too: a product in "Not countable" takes its duplicates
            # along, and back out the duplicate has to know what it counts as.
            # Otherwise two rows of one product came back active in one house.
            models.CheckConstraint(
                condition=(
                    models.Q(zaehlt_als__isnull=True)
                    | models.Q(status__in=(Artikelstatus.EINGESTELLT, Artikelstatus.IGNORIERT))
                ),
                name="zaehlt_als_nur_eingestellt",
                violation_error_message=_(
                    "Als ein anderer Artikel zählt nur ein eingestellter Hausartikel."
                ),
            ),
        ]

    def __str__(self) -> str:
        if not self.artikelnummer:
            return f"{self.haus}: {self.name}"
        return f"{self.haus}: {self.artikelnummer} - {self.name}"

    def save(self, *args, **kwargs):
        """Aligns the status and cleans up what a retired article no longer has.

        Every path that discontinues or ignores an article goes through here: the
        rows in the list, the detail page, the form, the import and the
        clarification before closing. The bulk passes via 'update()' only set to
        inactive, and for that there is nothing to clean up.

        The old status comes from the database, not from the object: a
        remembered value would survive 'refresh_from_db()' and then overlook a
        change.

        If product, unit or factor change, the frozen results follow in their
        conversion into the product ('ergebnisse_nachziehen').
        """
        self._status_angleichen()
        felder = kwargs.get("update_fields")
        if felder is not None:
            kwargs["update_fields"] = {
                *felder,
                "status",
                "gueltig_bis",
                "zaehlt_als",
                "status_vorher",
            }
        with transaction.atomic():
            vorher = (
                Hausartikel.objects.filter(pk=self.pk)
                .values_list("status", "ware_id", "basiseinheit_id", "faktor_zur_ware")
                .first()
                if self.pk
                else None
            ) or (None, None, None, None)
            super().save(*args, **kwargs)
            if self.status != vorher[0]:
                self._ausscheiden_aufraeumen()
                self._plaetze_wiederherstellen(vorher[0])
            ware_geschrieben = felder is None or "ware" in felder
            if ware_geschrieben and vorher[1] is not None and self.ware_id != vorher[1]:
                self._fremde_gebinde_aufraeumen()
            geschrieben = [
                (neu, alt)
                for feld, neu, alt in (
                    ("ware", self.ware_id, vorher[1]),
                    ("basiseinheit", self.basiseinheit_id, vorher[2]),
                    ("faktor_zur_ware", self.faktor_zur_ware, vorher[3]),
                )
                if felder is None or feld in felder
            ]
            if vorher[1] is not None and any(neu != alt for neu, alt in geschrieben):
                self._ergebnisse_nach_aenderung(vorher[1], vorher[2], vorher[3])

    def get_absolute_url(self):
        return reverse("hausartikel_detail", kwargs={"pk": self.pk})

    @property
    def ist_ausgeschieden(self) -> bool:
        return self.status in AUSGESCHIEDEN

    def clean_fields(self, exclude=None) -> None:
        """Without a unit the house counts in its product's, already at validation.

        The field validation runs before 'clean()' and would reject an empty
        required field before anyone could fill it. When saving, the 'pre_save'
        receivers fill it in ('katalog/apps.py'). Here the same happens one step
        earlier. Likewise the inventory type.
        """
        self.einheit_angleichen()
        self.inventurart_angleichen()
        super().clean_fields(exclude)

    def einheit_angleichen(self) -> None:
        """The product's unit where no own one is set."""
        if self.basiseinheit_id is None and self.ware_id is not None:
            self.basiseinheit_id = self.ware.basiseinheit_id

    def inventurart_angleichen(self) -> None:
        """Where no type is set, the one the product's other articles agree on.

        Otherwise the first ('Inventurart.vorschlag_fuer'). The import and
        every path that creates an article without a form go through here.
        """
        if self.inventurart_id is None:
            self.inventurart = Inventurart.vorschlag_fuer(self.ware_id)

    def weitere_gefuehrte(self) -> models.QuerySet[Hausartikel]:
        """The product's other carried articles, in every house: whom a change could concern."""
        return Hausartikel.objects.filter(ware_id=self.ware_id, status__in=GEFUEHRT).exclude(
            pk=self.pk
        )

    def inventurart_setzen(self, art: Inventurart, *, ganze_ware: bool = False) -> int:
        """Puts this article, or every article of its product, into this type.

        Returns how many changed. The type hangs on the house article, so for
        the product means in every house. Whoever changes it at the article
        decides which ('Ware.inventurart_setzen' for all). Without another
        carried article there is nothing to decide: the product's
        discontinued rows follow along, otherwise a dead row would make it
        look mixed.
        """
        from .erwartung import neue_art_ans_ende  # noqa: PLC0415  (a later module of the package)

        with neue_art_ans_ende():
            if ganze_ware or not self.weitere_gefuehrte().exists():
                geaendert = self.ware.inventurart_setzen(art)
            else:
                geaendert = (
                    Hausartikel.objects.filter(pk=self.pk)
                    .exclude(inventurart=art)
                    .update(inventurart=art)
                )
        self.inventurart = art
        return geaendert

    def clean(self) -> None:
        """Aligns the status and checks whether the article may be ignored.

        Aligned **here**, not only when saving: the check of the partial indexes
        runs after 'clean()', and it has to see the status the row is actually
        saved with.
        """
        self._status_angleichen()
        self.pruefe_nummer()
        if self.status == Artikelstatus.IGNORIERT:
            self.pruefe_ignorierbar()
        self.pruefe_eindeutig()
        self.pruefe_einheit()
        self.pruefe_einheitenwechsel()
        self.pruefe_ergebnisfaktoren()
        self.pruefe_zaehlt_als()

    def pruefe_nummer(self) -> None:
        """The number as the source carries it: checked, normalised, or empty for "eigen".

        The format checks the number, provided its system has a scheme (WinTICKET
        has none, there it stays as it is). An own article has no system behind
        it and may stand without a number. A number it does not carry: it would
        mean it existed somewhere after all.
        """
        self.artikelnummer = (self.artikelnummer or "").strip()
        if self.quelle == QUELLE_EIGEN:
            if self.artikelnummer:
                raise ValidationError(
                    {
                        "artikelnummer": _(
                            "Ein eigener Artikel hat keine Nummer. Das Feld bleibt leer."
                        )
                    }
                )
            return
        # The format layer lies above the models and reads files. It is only
        # fetched when a number is to be checked.
        from ..formats import FORMATE  # noqa: PLC0415

        format = FORMATE.get(self.quelle)
        if format is None:
            raise ValidationError({"quelle": _("Diese Quelle kennt die Anwendung nicht.")})
        nummer = format.artikelnummer(self.artikelnummer)
        if nummer is None:
            raise ValidationError({"artikelnummer": format.artikelnummer_hinweis})
        self.artikelnummer = nummer

    # ------------------------------------------------------------------ #
    # The house's unit against the product's
    # ------------------------------------------------------------------ #

    @property
    def braucht_faktor(self) -> bool:
        """Whether house and product count in different dimensions.

        Within a dimension the unit computes itself (g in kg). Across dimensions
        only whoever knows the product knows how much a piece weighs or holds.
        Then 'faktor_zur_ware' is required.
        """
        if not self.basiseinheit_id or not self.ware_id:
            return False
        return self.basiseinheit.dimension != self.ware.basiseinheit.dimension

    @property
    def weicht_ab(self) -> bool:
        """Whether the house counts in a different unit than the product, even just g against kg."""
        return bool(self.basiseinheit_id) and self.basiseinheit_id != (self.ware.basiseinheit_id)

    @property
    def faktor_zur_ware_wirksam(self) -> Decimal:
        """How many units of the product one unit of this house is.

        Computed within a dimension, otherwise the stored factor. If it is
        missing, the row is unusable: 'pruefe_einheit' does not allow that, and
        a silent 1 would set pieces off against litres.
        """
        wareneinheit = self.ware.basiseinheit
        faktor = umrechnung(self.basiseinheit, wareneinheit, self.faktor_zur_ware)
        if faktor is None:
            raise ValueError(
                f"{self.artikelnummer} „{self.name}“: Umrechnung von {self.basiseinheit.kurz} "
                f"in {wareneinheit.kurz} fehlt."
            )
        return faktor

    def in_wareneinheit(self, menge: Decimal) -> Decimal:
        """A quantity of the house in the product's unit, for the report."""
        return menge * self.faktor_zur_ware_wirksam

    def in_hauseinheit(self, menge: Decimal) -> Decimal:
        """A quantity of the product in the house's unit, for count and result."""
        return menge / self.faktor_zur_ware_wirksam

    def in_basiseinheit(self, brutto: Decimal, behaelter=None) -> Decimal:
        """A gross weight in grams into the house's unit.

        The product knows what the scale means in its unit
        ('gramm_je_einheit'). From there it is one step into the house.
        """
        return self.in_hauseinheit(self.ware.in_basiseinheit(brutto, behaelter))

    @property
    def einheit_hinweis(self) -> str:
        """How the house computes against the product, for display. Empty where both count alike."""
        if not self.weicht_ab:
            return ""
        faktor = self.faktor_zur_ware_wirksam
        return _("1 %(haus)s = %(faktor)s %(ware)s") % {
            "haus": self.basiseinheit.kurz,
            "faktor": menge_kurz(faktor),
            "ware": self.ware.basiseinheit.kurz,
        }

    def pruefe_einheit(self) -> None:
        """Across dimensions the factor is needed, within one none may be set.

        The same rule as with the conversion of the product family: a factor
        within a dimension could only contradict the unit's computation.
        """
        if not self.basiseinheit_id or not self.ware_id:
            return
        if self.basiseinheit.nur_fuer_familien:
            raise ValidationError({"basiseinheit": EINHEIT_NUR_FUER_FAMILIEN})
        wareneinheit = self.ware.basiseinheit
        if braucht_faktor(self.basiseinheit.dimension, wareneinheit.dimension):
            if self.faktor_zur_ware is None:
                raise ValidationError(
                    {
                        "faktor_zur_ware": _(
                            "Das Haus zählt in %(haus)s, die Ware wird in %(ware)s geführt. Wie "
                            "viele %(ware)s eine Einheit in %(haus)s ist, weiß nur, wer die Ware "
                            "kennt. Ohne diese Angabe ließe sich nichts zusammenrechnen."
                        )
                        % {"haus": self.basiseinheit.kurz, "ware": wareneinheit.kurz}
                    }
                )
        elif self.faktor_zur_ware is not None:
            raise ValidationError(
                {
                    "faktor_zur_ware": _(
                        "Innerhalb einer Dimension rechnet die Einheit selbst um. Der Faktor "
                        "bleibt leer."
                    )
                }
            )

    def einheit_gebunden_in(self) -> list[str]:
        """The inventories not yet closed that hold numbers of this article in its unit.

        Target, open target stock line and difference by hand are numbers in
        the house's unit. Counted quantities are not: they become one only at
        closing, in the unit the house carries then.
        """
        mit_zahlen = (
            self.sollbestaende.filter(IN_LAUFENDER_INVENTUR),
            self.sollzeilen.filter(IN_LAUFENDER_INVENTUR),
            self.inventurnotizen.filter(IN_LAUFENDER_INVENTUR, differenz__isnull=False),
        )
        inventuren = {
            zeile.inventur
            for zeilen in mit_zahlen
            for zeile in zeilen.select_related("inventur__haus")
        }
        return [str(inventur) for inventur in sorted(inventuren, key=lambda i: (i.stichtag, i.pk))]

    def pruefe_einheitenwechsel(self) -> None:
        """The house's unit stays as long as an inventory not yet closed holds numbers in it.

        A target of 24 pieces from the file would otherwise read as 24 kg the
        moment the house counts in kg (until 10/2026). Closed results keep the
        unit they were frozen in ('Inventurergebnis.einheit').
        """
        if self.pk is None or self.basiseinheit_id is None:
            return
        bisher = (
            Hausartikel.objects.filter(pk=self.pk).values_list("basiseinheit_id", flat=True).first()
        )
        if bisher is None or bisher == self.basiseinheit_id:
            return
        if inventuren := self.einheit_gebunden_in():
            raise ValidationError(
                {
                    "basiseinheit": einheit_gebunden(
                        self.name, inventuren, Basiseinheit.objects.get(pk=bisher)
                    )
                }
            )

    def ergebnisse_nachziehen(
        self, alt: Basiseinheit | None = None, faktor: Decimal | None = None
    ) -> None:
        """The frozen results keep their unit, their conversion into the product follows.

        A result in the unit the house still counts in takes the article's
        factor, also after it changed: what a piece weighs is knowledge about
        the product, as it was before the results had a factor of their own.
        One frozen in another unit converts like the house articles when the
        product changes its unit ('alt' to the one of today, 'faktor' new
        units per old one, see 'hausartikel_nachziehen'). A factor its column
        cannot hold stops the change before anything is written
        ('_faktoren_pruefen').
        """
        andere = [] if alt is None or faktor is None else self._umgerechnet(alt, faktor)
        self._faktoren_pruefen(andere)
        self.inventurergebnisse.filter(einheit_id=self.basiseinheit_id).update(
            faktor_zur_ware=self.faktor_zur_ware
        )
        if andere:
            self.inventurergebnisse.bulk_update(andere, ["faktor_zur_ware"])

    def _umgerechnet(self, alt: Basiseinheit, faktor: Decimal) -> list[Inventurergebnis]:
        """The results in another unit than the house's, converted with the product. Not written."""
        neu = self.ware.basiseinheit
        andere = list(
            self.inventurergebnisse.exclude(einheit_id=self.basiseinheit_id).select_related(
                "einheit"
            )
        )
        for ergebnis in andere:
            bisher = umrechnung(ergebnis.einheit, alt, ergebnis.faktor_zur_ware)
            if bisher is None:
                continue
            ergebnis.faktor_zur_ware = (
                None
                if ergebnis.einheit.dimension == neu.dimension
                else faktor_gerundet(bisher * faktor)
            )
        return andere

    def _ergebnisse_nach_aenderung(
        self, ware_vorher: int, einheit_vorher: int, faktor_vorher: Decimal | None
    ) -> None:
        """After a change of product, unit or factor: the results follow ('ergebnisse_nachziehen').

        A new product in the same dimension as the old one converts its unit
        for the results frozen in another unit. Into another dimension only
        the article knows the way ('_ergebnisse_ueber_den_artikel'). Every new
        factor is checked before anything of it is written ('_faktoren_pruefen').
        """
        geplant = (
            []
            if self.ware_id == ware_vorher
            else self._ergebnisse_geplant(ware_vorher, einheit_vorher, faktor_vorher)
        )
        self._faktoren_pruefen(geplant)
        self.ergebnisse_nachziehen()
        if geplant:
            self.inventurergebnisse.bulk_update(geplant, ["faktor_zur_ware"])

    def _ergebnisse_geplant(
        self, ware_vorher: int, einheit_vorher: int, faktor_vorher: Decimal | None
    ) -> list[Inventurergebnis]:
        """What a change of product makes of the results' factors. Nothing written yet."""
        alt = Basiseinheit.objects.filter(waren__pk=ware_vorher).first()
        # Within a dimension the units compute the way, across it only the article knows it.
        if alt is not None and (faktor := umrechnung(alt, self.ware.basiseinheit)) is not None:
            return self._umgerechnet(alt, faktor)
        return self._ergebnisse_ueber_den_artikel(alt, einheit_vorher, faktor_vorher)

    def _ergebnisse_ueber_den_artikel(
        self, alt: Basiseinheit | None, einheit_vorher: int, faktor_vorher: Decimal | None
    ) -> list[Inventurergebnis]:
        """Into a product of another dimension the results find the new one through the article.

        Only the article knows the way: one unit of it was so many units of
        the old product and is so many of the new one. Over that bridge a
        result's way into the old product becomes its way into the new one.
        500 g of a product in g, counted in pieces of 250 g, are 2 pieces of
        a product in pieces. 1 kg frozen at 4 pieces per kg is 1.2 l where the
        article now counts 1.2 l per kg. Until 10/2026 a result without a
        factor of its own dropped out of every sum, and one with a factor kept
        it although it led into the old product: the 1 kg stood there as 4 l.

        Left as they are: results in the new product's dimension, because the
        units compute there, and a result without a factor where the article
        counts in its dimension, because the article's factor of the day leads
        it ('Inventurergebnis.faktor_zur_ware_wirksam'). Where the article
        leaves its own dimension in the same step, nothing connects the two
        products. A factor into the old one then goes as well: rather no way
        than a wrong number. Back come the results whose factor changes, not
        yet written.
        """
        if alt is None:
            return []
        neu = self.ware.basiseinheit
        vorher = Basiseinheit.objects.get(pk=einheit_vorher)
        bisher = umrechnung(vorher, alt, faktor_vorher)
        weiter = umrechnung(vorher, self.basiseinheit)
        jetzt = umrechnung(self.basiseinheit, neu, self.faktor_zur_ware)
        # Units of the new product per unit of the old one, None without a bridge.
        je_einheit = (
            None if bisher is None or weiter is None or jetzt is None else weiter * jetzt / bisher
        )
        ergebnisse = []
        for ergebnis in self.inventurergebnisse.exclude(
            einheit_id=self.basiseinheit_id
        ).select_related("einheit"):
            eigener = ergebnis.faktor_zur_ware
            # Left as it is: the units compute into the new product, it had no
            # way into the old one, or the article's factor of the day leads it.
            if (
                ergebnis.einheit.dimension == neu.dimension
                or (zur_alten := umrechnung(ergebnis.einheit, alt, eigener)) is None
                or (eigener is None and ergebnis.einheit.dimension == self.basiseinheit.dimension)
            ):
                continue
            ergebnis.faktor_zur_ware = (
                None if je_einheit is None else faktor_gerundet(zur_alten * je_einheit)
            )
            if ergebnis.faktor_zur_ware != eigener:
                ergebnisse.append(ergebnis)
        return ergebnisse

    def pruefe_ergebnisfaktoren(self) -> None:
        """A new product has to leave every frozen result a factor its column holds.

        Asked in the form, so it says so before anything is written. 'save()'
        asks the same on the paths past it ('_ergebnisse_nach_aenderung').
        """
        if self.pk is None or self.ware_id is None or self.basiseinheit_id is None:
            return
        vorher = (
            Hausartikel.objects.filter(pk=self.pk)
            .values_list("ware_id", "basiseinheit_id", "faktor_zur_ware")
            .first()
        )
        if vorher is None or vorher[0] == self.ware_id:
            return
        self._faktoren_pruefen(self._ergebnisse_geplant(*vorher))

    def _faktoren_pruefen(self, ergebnisse: Iterable[Inventurergebnis]) -> None:
        """A factor its column cannot hold stops the change, and the message names the result.

        A derived factor can grow past the six places before the point: a
        pallet of 1000 kg, counted as one piece, is a million g once the
        product is carried in g. Until 10/2026 PostgreSQL refused that with a
        numeric overflow, an error page, and SQLite stored a number it could
        not read back.
        """
        zu_gross = [
            (ergebnis, faktor)
            for ergebnis in ergebnisse
            if (faktor := ergebnis.faktor_zur_ware) is not None and faktor >= FAKTOR_HOECHSTENS
        ]
        if not zu_gross:
            return
        raise ValidationError(
            {
                "ware": [
                    ergebnisfaktor_zu_gross(
                        self.name,
                        ergebnis.inventur,
                        ergebnis.einheit.kurz,
                        faktor,
                        self.ware.basiseinheit.kurz,
                    )
                    for ergebnis, faktor in zu_gross
                ]
            }
        )

    def gleichnamiger(self) -> Hausartikel | None:
        """Another row of this house with the same number **and** the same designation."""
        if self.haus_id is None or not self.artikelnummer or not self.name:
            return None
        return (
            Hausartikel.objects.filter(
                haus_id=self.haus_id, artikelnummer=self.artikelnummer, name=self.name
            )
            .exclude(pk=self.pk)
            .first()
        )

    def pruefe_eindeutig(self) -> None:
        """House, number and designation together exist only once, in every status.

        The partial indexes allow any number of discontinued rows per number: a
        number is reassigned, the old row stays for its results. But those are
        **different** articles, and the designation keeps them apart. The target
        stock import decides exactly about that with several discontinued rows
        of one number. Two rows that agree in all three would be the same
        article twice. If it comes back, the discontinued one is reactivated
        instead of creating a twin.

        No database rule: the designation is in the table of the 'Artikel' core,
        house and number in that of the house article, and a constraint covers
        only columns of one table. The same check therefore stands in the import
        ('BaseImportFormSet', 'services._anlegen').
        """
        if (anderer := self.gleichnamiger()) is None:
            return
        raise ValidationError(
            {
                "name": _(
                    "%(nummer)s „%(name)s“ gibt es in diesem Haus schon (%(status)s). Ist "
                    "es derselbe Artikel, wird der vorhandene Satz weitergeführt oder "
                    "wieder aktiviert. Sonst braucht dieser hier eine eigene Bezeichnung."
                )
                % {
                    "nummer": self.artikelnummer,
                    "name": self.name,
                    "status": anderer.get_status_display().lower(),
                }
            }
        )

    def pruefe_zaehlt_als(self) -> None:
        """A duplicate counts as a row of the same house and the same product.

        Otherwise its target would land in a foreign house, or in a unit that
        does not convert.
        """
        if self.zaehlt_als_id is None:
            return
        ziel = self.zaehlt_als
        passt = (
            ziel is not None
            and ziel.pk != self.pk
            and ziel.haus_id == self.haus_id
            and ziel.ware_id == self.ware_id
        )
        if not passt:
            raise ValidationError(
                {
                    "zaehlt_als": _(
                        "Ein Artikel zählt nur als ein anderer desselben Hauses und derselben Ware."
                    )
                }
            )

    def _status_angleichen(self) -> None:
        """What derives from other fields follows them.

        The status "ignored" hangs on the family "Not countable" of the product,
        in both directions: if the product lies there, the article is ignored.
        On the way in the row remembers what it was ('status_vorher'), and
        whoever takes the product out of there gets exactly that back: the
        way back is a wrong click undone. Until 10/2026 every row came back
        active, and a discontinued old number of the product counted as the
        new one from then on. A row without that memory comes back active,
        because exactly that is the occasion, or discontinued where it counts
        as another row: two active rows of one product in one house are not
        allowed. A database rule cannot do that: the attribute is two tables
        away.

        The date follows the status. On the change between inactive and
        discontinued it stays as it was. It says since when the article is no
        longer active.
        """
        if self.ware_id is not None:
            if self.ware.ist_nicht_zaehlbar:
                if self.status != Artikelstatus.IGNORIERT:
                    self.status_vorher = self.status
                self.status = Artikelstatus.IGNORIERT
            elif self.status == Artikelstatus.IGNORIERT:
                if self.status_vorher:
                    self.status, self.status_vorher = self.status_vorher, ""
                else:
                    self.status = (
                        Artikelstatus.EINGESTELLT if self.zaehlt_als_id else Artikelstatus.AKTIV
                    )
        if self.status == Artikelstatus.AKTIV:
            self.gueltig_bis = None
        elif self.gueltig_bis is None:
            self.gueltig_bis = timezone.now()
        # A duplicate that comes back counts as itself again. Ignored, it keeps
        # the mark: the way out of "Not countable" needs it.
        if self.status not in (Artikelstatus.EINGESTELLT, Artikelstatus.IGNORIERT):
            self.zaehlt_als = None

    def pruefe_ignorierbar(self) -> None:
        """What is counted in a running inventory cannot be hidden.

        The row then belongs to the result. If the article vanished, the
        quantity would be missing without anyone seeing it. Uncounted rows, by
        contrast, silently drop out when ignoring ('_ausscheiden_aufraeumen').
        """
        if self.pk is None:
            return
        gezaehlt = (
            self.zaehlpositionen.filter(aktiv=True)
            .filter(IN_LAUFENDER_INVENTUR)
            .filter(models.Q(eingabemenge__isnull=False) | models.Q(wiegungen__isnull=False))
        )
        if gezaehlt.exists():
            raise ValidationError(
                {
                    "ware": _(
                        "„%(name)s“ ist in einer laufenden Inventur schon gezählt. Bis zu "
                        "deren Abschluss lässt sich seine Ware nicht auf nicht zählbar "
                        "setzen. Eingestellt werden kann er sofort."
                    )
                    % {"name": self.name}
                }
            )

    def _ausscheiden_aufraeumen(self) -> None:
        """What a discontinued or ignored article leaves behind goes ('hausartikel_status')."""
        from .. import hausartikel_status  # noqa: PLC0415  (the module lies above the models)

        hausartikel_status.aufraeumen(self)

    def _plaetze_wiederherstellen(self, status_vorher: str | None) -> None:
        """The way back brings the places along ('hausartikel_status')."""
        from .. import hausartikel_status  # noqa: PLC0415  (the module lies above the models)

        hausartikel_status.plaetze_wiederherstellen(self, status_vorher)

    @classmethod
    def plaetze_umschreiben(
        cls,
        artikel: Iterable[Hausartikel],
        *,
        orte: dict[int, int] | None = None,
        gebinde: dict[int, int] | None = None,
    ) -> None:
        """The remembered places follow when a location or a pack gets another number.

        Merging locations or products and splitting a product renumber what
        'erwartungen_vorher' names. The way back then found neither, although
        both still existed (until 10/2026). 'orte' and 'gebinde' map old
        numbers onto new ones. Two places that fall onto one location become
        one, as when the locations merge: the place that was there already
        wins, the other only adds the packs it lacks and the scale.
        """
        for satz in artikel:
            if not satz.erwartungen_vorher:
                continue
            neu = plaetze_abbilden(satz.erwartungen_vorher, orte or {}, gebinde or {})
            if neu != satz.erwartungen_vorher:
                satz.erwartungen_vorher = neu
                cls.objects.filter(pk=satz.pk).update(erwartungen_vorher=neu)

    def _fremde_gebinde_aufraeumen(self) -> None:
        """After a change of product the packs of the old one go ('hausartikel_status')."""
        from .. import hausartikel_status  # noqa: PLC0415  (the module lies above the models)

        hausartikel_status.fremde_gebinde_aufraeumen(self)

    def status_setzen(self, status: str) -> None:
        """Active, inactive or discontinued, because "ignored" only goes through 'ignorieren()'.

        Checks beforehand whether number and product are free in the house, and
        names who occupies them. An article that is already ignored does not
        come back through the status. For that its product has to leave "Not
        countable".
        """
        if Artikelstatus.IGNORIERT in (status, self.status):
            raise ValidationError(
                _(
                    "Ob ein Artikel nicht zählbar ist, entscheidet die Familie seiner Ware, "
                    "nicht sein Status."
                )
            )
        if status in GEFUEHRT and (blockiert := self.blockiert_durch()) is not None:
            raise ValidationError(
                _(
                    "%(artikelnummer)s „%(self)s“ lässt sich nicht auf %(lower)s setzen: %(haus)s "
                    "führt schon %(artikelnummer2)s „%(blockiert)s“, mit derselben Nummer oder "
                    "derselben Ware."
                )
                % {
                    "artikelnummer": self.artikelnummer,
                    "self": self.name,
                    "lower": Artikelstatus(status).label.lower(),
                    "haus": self.haus,
                    "artikelnummer2": blockiert.artikelnummer,
                    "blockiert": blockiert.name,
                }
            )
        self.status = status
        self.save(update_fields=["status", "gueltig_bis", "geaendert_am"])

    def deaktivieren(self) -> None:
        self.status_setzen(Artikelstatus.INAKTIV)

    def einstellen(self) -> None:
        """Over for good: the row stays only for old results.

        Needed when an article number is reassigned: the old row releases
        number and product, and old inventory results keep pointing to it.
        """
        self.status_setzen(Artikelstatus.EINGESTELLT)

    def aktivieren(self) -> None:
        self.status_setzen(Artikelstatus.AKTIV)

    def ignorieren(self) -> None:
        """Puts its product into "Not countable". That applies in every house that carries it."""
        self.ware.ignorieren()
        self.refresh_from_db()

    def blockiert_durch(self) -> Hausartikel | None:
        """The carried article that contests number or product in the house with this one.

        Without a number only the product counts: the empty number is shared by
        all own articles of the house.
        """
        streitig = models.Q(ware_id=self.ware_id)
        if self.artikelnummer:
            streitig |= models.Q(artikelnummer=self.artikelnummer)
        return (
            Hausartikel.objects.filter(haus_id=self.haus_id, status__in=GEFUEHRT)
            .filter(streitig)
            .exclude(pk=self.pk)
            .first()
        )

    def zaehlpflichtig_bei(self, umfang: Zaehlumfang, haus_id: int) -> bool:
        if not self.aktiv or self.haus_id != haus_id:
            return False
        return umfang.umfasst(self)

    @property
    def abwiegbare_ware(self) -> bool:
        return self.ware.abwiegbar


#: The whole, in percent: the shares of a weighed mix add up to it.
GANZES = Decimal("100")

ANTEILE_NUR_BEIM_WIEGEN = _(
    "In %(einheit)s wird abgezählt, nicht gewogen. Anteile in Prozent gibt es nur beim Wiegen."
)


class Rezepturartikel(Artikel):
    """Composite article that exists only for counting.

    Maintained exclusively in this application. It never stands in the result
    itself, only its ingredients.

    Two constructions: counted portions (unit pieces, say assembled nachos)
    and goods that go on the scale as a whole and are only distributed over
    several house articles through the recipe (unit g). For the second case
    the containers whose tare is subtracted stand here. A recipe article has
    no packs, those hang on the product.

    A weighed mix may state its recipe as **shares of the weight** in percent
    ('anteilig'). Then the way from the gram into the product's unit goes
    through what the product says a unit of it weighs, the same entry the
    scale uses. Without the flag the number is a quantity per unit of the
    recipe article, as for the counted portion.
    """

    basiseinheit = models.ForeignKey(
        Basiseinheit,
        on_delete=models.PROTECT,
        related_name="rezepturartikel",
        verbose_name=_("Einheit"),
        help_text=_("Stück(e) für abgezählte Portionen, g für alles, was auf die Waage kommt."),
    )
    behaelter = models.ManyToManyField(
        "katalog.Behaelter",
        blank=True,
        related_name="rezepturartikel",
        verbose_name=pgettext_lazy("Feld", "Behälter"),
        help_text=_("Behälter, in denen dieser Artikel gewogen wird."),
        db_table="behaelter_rezepturartikel",
    )
    anteilig = models.BooleanField(
        default=False,
        verbose_name=_("Anteile in Prozent"),
        help_text=_(
            "Die Rezeptur nennt je Ware ihren Anteil am Gewicht in Prozent, zusammen 100. "
            "Nur beim Wiegen."
        ),
    )

    # Created by Django at runtime. Only named here so pyright knows them.
    artikel_ptr: Artikel
    artikel_ptr_id: int
    basiseinheit_id: int
    komponenten: RelatedManager[Rezepturkomponente]

    class Meta:
        verbose_name = _("Rezepturartikel")
        verbose_name_plural = _("Rezepturartikel")
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name}"

    def get_absolute_url(self):
        return reverse("rezepturartikel_detail", kwargs={"pk": self.pk})

    def clean(self) -> None:
        """The name is the key of this product. Twice it would only exist confused.

        No database constraint: 'name' is in the article table, which recipe
        articles share with the house articles, and their names certainly repeat
        across the houses.
        """
        if Rezepturartikel.objects.filter(name=self.name).exclude(pk=self.pk).exists():
            raise ValidationError(
                {"name": _("„%(name)s“ gibt es als Rezepturartikel bereits.") % {"name": self.name}}
            )

        if self.basiseinheit_id is None:
            return

        if self.anteilig and not self.wird_gewogen:
            raise ValidationError(
                {"anteilig": ANTEILE_NUR_BEIM_WIEGEN % {"einheit": self.basiseinheit.kurz}}
            )

        if not self.waehlbare_einheiten().filter(pk=self.basiseinheit_id).exists():
            erlaubt = ", ".join(einheit.kurz for einheit in self.waehlbare_einheiten())
            raise ValidationError(
                {
                    "basiseinheit": _(
                        "Ein Rezepturartikel wird in %(einheiten)s geführt, sonst nirgends."
                    )
                    % {"einheiten": erlaubt}
                }
            )

        # Containers belong to the scale. The assignment is in a table of its
        # own and exists only after the first save. Before that there is nothing
        # to check, which is why the form takes over the case "new".
        if self.pk and not self.wird_gewogen and self.behaelter.exists():
            raise ValidationError(
                {
                    "basiseinheit": _(
                        "In %(einheit)s wird gezählt, nicht gewogen. Dann dürfen keine "
                        "Behälter zugeordnet sein."
                    )
                    % {"einheit": self.basiseinheit.kurz}
                }
            )

    @staticmethod
    def waehlbare_einheiten():
        """Pieces for counting, g for weighing. A recipe article needs no more."""
        return Basiseinheit.objects.filter(
            models.Q(dimension=Dimension.STUECK)
            | models.Q(dimension=Dimension.MASSE, faktor_zur_referenz=Decimal("1"))
        )

    @property
    def wird_gewogen(self) -> bool:
        """Whether the quantity comes via the scale instead of piece counts."""
        return self.basiseinheit.dimension == Dimension.MASSE

    @property
    def abwiegbare_ware(self) -> bool:
        """Never: what goes onto the scale here is the recipe article itself."""
        return False

    def waehlbare_behaelter(self):
        """The assigned containers plus "without container"."""
        return Behaelter.objects.filter(
            models.Q(rezepturartikel=self) | models.Q(immer_erlaubt=True)
        ).distinct()

    def in_basiseinheit(self, eingabemenge: Decimal, behaelter=None) -> Decimal:
        """Converts a count input into the unit of this recipe article.

        When weighing, 'eingabemenge' is the gross weight in grams, otherwise the
        counted piece count, which stays as it is.
        """
        if not self.wird_gewogen:
            return eingabemenge
        return netto_in_basiseinheit(eingabemenge, self.basiseinheit.faktor_zur_referenz, behaelter)

    def mengen_je_ware(self, eingabemenge: Decimal, behaelter=None) -> dict[Ware, Decimal]:
        """How a count input distributes over the recipe onto the products."""
        return self.verteilen(self.in_basiseinheit(eingabemenge, behaelter))

    def verteilen(self, menge: Decimal) -> dict[Ware, Decimal]:
        """The same for a quantity that is already in the unit of this article.

        Needed since a weighed recipe article can consist of **several**
        weighings: there the tare per vessel has long been subtracted, and a
        gross weight no longer exists at all, only the sum.
        """
        return {
            komponente.ware: menge * komponente.menge_je_einheit()
            for komponente in self.komponenten.select_related(
                "ware__basiseinheit"
            ).prefetch_related("ware__umrechnungen")
        }

    @staticmethod
    def anteile_pruefen(anteile: Iterable[Decimal]) -> None:
        """Shares of the weight have to make up the whole. Raises otherwise.

        Checked where all rows are together (the formset, the demo), not on
        the single row: it does not know its neighbours. Water in a mix has
        no place in this, because water is not a product.
        """
        summe = sum(anteile, Decimal("0"))
        if summe != GANZES:
            raise ValidationError(
                _("Die Anteile ergeben %(summe)s %%, nicht 100 %%.") % {"summe": menge_kurz(summe)}
            )

    def _aufgeloest_in(self, haus_id: int, status: tuple[str, ...]) -> bool:
        ware_ids = set(self.komponenten.values_list("ware_id", flat=True))
        if not ware_ids:
            return False
        vorhanden = (
            Hausartikel.objects.filter(haus_id=haus_id, status__in=status, ware_id__in=ware_ids)
            .values_list("ware_id", flat=True)
            .distinct()
        )
        return ware_ids == set(vorhanden)

    @staticmethod
    def status_je_artikel() -> dict[int, str]:
        """How every recipe article stands, across all houses, for list and filter.

        It has no status of its own (§ "Rezepturartikel-Verfügbarkeit"). It stands
        like the house in which it is doing best:

        - **active** as soon as it is available somewhere (all ingredients active).
        - **inactive** if it is available nowhere but resolvable somewhere. An
          ingredient is inactive there, it can still be added.
        - **discontinued** if it can be resolved nowhere: an ingredient is
          discontinued or missing everywhere, or there is no recipe.

        Collected in two queries instead of 'verfuegbar_in' per article and house.
        """
        zutaten: dict[int, set[int]] = {
            pk: set() for pk in Rezepturartikel.objects.values_list("pk", flat=True)
        }
        for rezepturartikel_id, ware_id in Rezepturkomponente.objects.values_list(
            "rezepturartikel_id", "ware_id"
        ):
            zutaten[rezepturartikel_id].add(ware_id)

        aktiv: dict[int, set[int]] = {}
        gefuehrt: dict[int, set[int]] = {}
        alle_waren = set().union(*zutaten.values()) if zutaten else set()
        for haus_id, ware_id, status in Hausartikel.objects.filter(
            ware_id__in=alle_waren, status__in=GEFUEHRT
        ).values_list("haus_id", "ware_id", "status"):
            gefuehrt.setdefault(haus_id, set()).add(ware_id)
            if status == Artikelstatus.AKTIV:
                aktiv.setdefault(haus_id, set()).add(ware_id)

        def steht(waren: set[int]) -> str:
            if waren and any(waren <= vorrat for vorrat in aktiv.values()):
                return Artikelstatus.AKTIV
            if waren and any(waren <= vorrat for vorrat in gefuehrt.values()):
                return Artikelstatus.INAKTIV
            return Artikelstatus.EINGESTELLT

        return {pk: steht(waren) for pk, waren in zutaten.items()}

    def verfuegbar_in(self, haus_id: int) -> bool:
        """Whether every component has an **active** house article in this house.

        The normal case: that is how the recipe article stands in the counting
        list and gets its row when an inventory is created.
        """
        return self._aufgeloest_in(haus_id, (Artikelstatus.AKTIV,))

    def verfuegbar_in_haeusern(self) -> set[int]:
        """The houses for which 'verfuegbar_in' holds, in two queries instead of two per house.

        For the detail page, which names every house with its answer.
        """
        return self._haeuser_mit((Artikelstatus.AKTIV,))

    def aufloesbar_in_haeusern(self) -> set[int]:
        """The houses for which 'aufloesbar_in' holds, the same way."""
        return self._haeuser_mit(GEFUEHRT)

    def _haeuser_mit(self, status: tuple[str, ...]) -> set[int]:
        ware_ids = set(self.komponenten.values_list("ware_id", flat=True))
        if not ware_ids:
            return set()
        je_haus: dict[int, set[int]] = {}
        for haus_id, ware_id in Hausartikel.objects.filter(
            ware_id__in=ware_ids, status__in=status
        ).values_list("haus_id", "ware_id"):
            je_haus.setdefault(haus_id, set()).add(ware_id)
        return {haus_id for haus_id, waren in je_haus.items() if waren == ware_ids}

    def aufloesbar_in(self, haus_id: int) -> bool:
        """Whether every component has a **carried** house article in this house.

        Wider than 'verfuegbar_in': an ingredient may be inactive. Then the
        recipe article is like an inactive house article. It gets no row by
        itself but can be added in the app, and its share lands on the inactive
        row when resolving, which can be reactivated before closing.
        Discontinued or ignored does not count: there the share could no longer
        be booked anywhere.
        """
        return self._aufgeloest_in(haus_id, GEFUEHRT)

    def umfang_passt(self, umfang: Zaehlumfang, haus_id: int) -> bool:
        """Whether an inventory of these types means the recipe article in this house at all.

        As soon as **one** component is due ('Zaehlumfang.rezepturartikel').
        Otherwise exactly the nachos that are in the prepared portions would be
        missing from the monthly inventory.
        """
        return (
            Rezepturartikel.objects.filter(pk=self.pk)
            .filter(umfang.rezepturartikel(haus_id))
            .exists()
        )

    def zaehlpflichtig_bei(self, umfang: Zaehlumfang, haus_id: int) -> bool:
        """Due for counting if it is available here and one ingredient is due."""
        return self.verfuegbar_in(haus_id) and self.umfang_passt(umfang, haus_id)


class Rezepturkomponente(models.Model):
    """Mapping of recipe article to product (e.g. assembled small nachos).

    The stored number means one of two things, and the recipe article says
    which ('anteilig'): a quantity in the product's unit per unit of the
    recipe article, or a share of the weight in percent. 'menge_je_einheit'
    turns both into the first, so nobody else has to know the difference.
    """

    rezepturartikel = models.ForeignKey(
        Rezepturartikel,
        on_delete=models.CASCADE,
        related_name="komponenten",
        verbose_name=_("Rezepturartikel"),
    )
    ware = models.ForeignKey(
        Ware,
        on_delete=models.PROTECT,
        related_name="verwendet_in",
        verbose_name=_("Ware"),
    )
    menge_in_basiseinheit = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        validators=[MinValueValidator(Decimal("0.000001"))],
        help_text=_(
            "Je Einheit des Rezepturartikels, in der Basiseinheit der Ware, z. B. 0,14 kg Nachos "
            "je Portion. Mit „Anteile in Prozent“ stattdessen der Anteil am Gewicht, z. B. 40."
        ),
        verbose_name=_("Menge in Basiseinheit"),
    )

    # Created by Django at runtime. Only named here so pyright knows them.
    rezepturartikel_id: int
    ware_id: int

    class Meta:
        verbose_name = _("Rezepturkomponente")
        verbose_name_plural = _("Rezepturkomponenten")
        ordering = ["ware__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["rezepturartikel", "ware"],
                name="komponente_je_rezepturartikel_eindeutig",
                violation_error_message=_(
                    "Jede Ware darf nur einmal in der Rezeptur stehen. "
                    "Die Mengen gehören in eine Zeile."
                ),
            )
        ]

    def __str__(self) -> str:
        menge = menge_kurz(self.menge_in_basiseinheit)
        if self.rezepturartikel.anteilig:
            return f"{menge} % {self.ware.name}"
        einheit = self.ware.basiseinheit
        if einheit.dimension == Dimension.STUECK:
            return f"{menge} × {self.ware.name}"
        return f"{menge} {einheit.kurz} {self.ware.name}"

    def menge_je_einheit(self) -> Decimal:
        """What one unit of the recipe article holds of this product, in the product's unit.

        For a share, one gram of the mix holds that percentage of a gram of
        this product. The gram then goes the way the scale takes into the
        product's unit ('Ware.in_basiseinheit'): for kg through the unit
        itself, for litres or pieces through what the product says a unit
        weighs. 'clean' insists on that entry, so the fallback in there does
        not come into play.
        """
        if not self.rezepturartikel.anteilig:
            return self.menge_in_basiseinheit
        return self.ware.in_basiseinheit(self.menge_in_basiseinheit / GANZES)

    def ohne_gewicht(self) -> bool:
        """Whether a share could not be converted: the product does not know its weight."""
        if not self.rezepturartikel.anteilig or self.ware_id is None:
            return False
        return (
            self.ware.basiseinheit.dimension != Dimension.MASSE
            and self.ware.gramm_je_einheit is None
        )

    def clean(self) -> None:
        if self.ware_id is not None and self.ware.ist_nicht_zaehlbar:
            raise ValidationError(
                {
                    "ware": _("„%(name)s“ gehört zu „Nicht zählbar“ und damit in keine Rezeptur.")
                    % {"name": self.ware.name}
                }
            )
        if self.rezepturartikel_id is None:
            return
        if self.ohne_gewicht():
            raise ValidationError(
                {
                    "ware": _(
                        "„%(name)s“ wird in %(einheit)s geführt. Trag an der Ware ein, wie viel "
                        "Gramm eine Einheit wiegt, sonst lässt sich der Anteil nicht umrechnen."
                    )
                    % {"name": self.ware.name, "einheit": self.ware.basiseinheit.kurz}
                }
            )
        if (
            self.rezepturartikel.anteilig
            and self.menge_in_basiseinheit is not None
            and self.menge_in_basiseinheit > GANZES
        ):
            raise ValidationError(
                {"menge_in_basiseinheit": _("Ein Anteil liegt zwischen 0 und 100 Prozent.")}
            )
