"""What all models of the catalog share: status, sort order and fixed names."""

from typing import TYPE_CHECKING, cast

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from django_project.labels import Beschriftung

if TYPE_CHECKING:
    # Only for pyright: without the mypy plugin of the django-stubs, reverse
    # relations, '_id' columns and 'get_…_display' are unknown. Nothing of this
    # is loaded at runtime. The dependency direction of the apps stays as it is.
    from django.db.models.fields.related_descriptors import RelatedManager

    from .artikel import Hausartikel

nur_ziffern = RegexValidator(r"^\d+$", _("Nur Ziffern erlaubt."))


#: The source of a house article that exists only in this application: no
#: system carries it, and it needs no number. The other sources are the ids
#: from 'katalog.formats'.
QUELLE_EIGEN = "eigen"


def quellenwahl() -> list[tuple[str, Beschriftung]]:
    """The choice of source: every known format, plus "eigen".

    As a function, so the model needs the format layer only when someone sees
    the choice. That lies above the models and reads files.
    """
    from ..formats import formatwahl  # noqa: PLC0415

    return [*formatwahl(), (QUELLE_EIGEN, _("eigen, nur in dieser Anwendung"))]


class Sortiert(models.Model):
    """A position that is unique within its group."""

    #: Name of the foreign key within which counting happens.
    gruppenfeld = ""

    sortierung = models.PositiveIntegerField(
        blank=True,
        help_text=_(
            "Reihenfolge innerhalb der Gruppe. Bleibt das Feld leer, wird hinten angehängt."
        ),
        verbose_name=_("Sortierung"),
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.sortierung is None or (self._state.adding and self._position_belegt()):
            self.sortierung = self._naechste_sortierung()
        super().save(*args, **kwargs)

    def clean_fields(self, exclude=None) -> None:
        """Assigns the position before the field validation complains it is empty."""
        if self.sortierung is None and self._gruppen_id is not None:
            self.sortierung = self._naechste_sortierung()
        super().clean_fields(exclude)

    @property
    def _gruppen_id(self):
        return getattr(self, f"{self.gruppenfeld}_id", None)

    @classmethod
    def _sortiermodell(cls) -> type[Sortiert]:
        """The model in whose table 'sortierung' stands."""
        return cast("type[Sortiert]", cls._meta.get_field("sortierung").model)

    @classmethod
    def _geschwister(cls, gruppen_id):
        return cls._sortiermodell()._default_manager.filter(**{f"{cls.gruppenfeld}_id": gruppen_id})

    def _position_belegt(self) -> bool:
        return self._geschwister(self._gruppen_id).filter(sortierung=self.sortierung).exists()

    def _naechste_sortierung(self) -> int:
        """Position after the so far last of the group."""
        hoechste = self._geschwister(self._gruppen_id).aggregate(models.Max("sortierung"))[
            "sortierung__max"
        ]
        return (hoechste or 0) + 1

    @classmethod
    def neu_sortieren(cls, gruppen_id: int, reihenfolge: list[int]) -> None:
        """Renumbers a group according to 'reihenfolge' (primary keys)."""
        modell = cls._sortiermodell()
        saetze = {satz.pk: satz for satz in cls._geschwister(gruppen_id)}
        if set(reihenfolge) != set(saetze):
            raise ValueError("Die Reihenfolge muss genau die Sätze dieser Gruppe enthalten.")

        hoechste = max((satz.sortierung for satz in saetze.values()), default=0)
        parkplatz = max(hoechste, len(reihenfolge))

        with transaction.atomic():
            for versatz, pk in enumerate(reihenfolge, start=1):
                saetze[pk].sortierung = parkplatz + versatz
            modell._default_manager.bulk_update(saetze.values(), ["sortierung"])

            for position, pk in enumerate(reihenfolge, start=1):
                saetze[pk].sortierung = position
            modell._default_manager.bulk_update(saetze.values(), ["sortierung"])


def mit_statuszahlen[Q: models.QuerySet](queryset: Q) -> Q:
    """Per row, how many of its house articles are active, inactive and discontinued.

    For 'AusHausartikeln.sammelstatus' in lists. Without the numbers every row
    would cost a query. 'distinct', because filters can join over the same
    house articles and would otherwise multiply the count.
    """

    def zahl(status):
        return models.Count(
            "hausartikel", filter=models.Q(hausartikel__status=status), distinct=True
        )

    return queryset.annotate(
        anzahl_aktiv=zahl(Artikelstatus.AKTIV),
        anzahl_inaktiv=zahl(Artikelstatus.INAKTIV),
        anzahl_eingestellt=zahl(Artikelstatus.EINGESTELLT),
    )


class AusHausartikeln(models.Model):
    """A status derived from the assigned house articles, for product and group.

    Neither product group nor product has one of its own: they stand like
    their liveliest house article, and their status is set by all their
    house articles getting it.
    """

    # Created by Django at runtime. Only named here so pyright knows them.
    hausartikel: RelatedManager[Hausartikel]
    # Only on rows from 'mit_statuszahlen'. 'sammelstatus' asks with 'hasattr'.
    anzahl_aktiv: int
    anzahl_inaktiv: int
    anzahl_eingestellt: int

    class Meta:
        abstract = True

    @property
    def aktiv(self) -> bool:
        """Whether at least one house article is active.

        Where many rows are displayed at once, annotate
        'Count("hausartikel", filter=Q(hausartikel__aktiv=True))' instead of this
        property, otherwise every row costs a query.
        """
        return self.hausartikel.filter(aktiv=True).exists()

    @property
    def sammelstatus_anzeige(self) -> Beschriftung:
        """The status as a word in the account's language. The raw value happens to be German."""
        return Artikelstatus(self.sammelstatus).label

    @property
    def sammelstatus(self) -> str:
        """Active as soon as one house article is active (or there is none yet).
        Discontinued if all are discontinued. Otherwise inactive.

        Reads the numbers from 'mit_statuszahlen' where they exist. Ignored
        house articles do not count.
        """
        if hasattr(self, "anzahl_eingestellt"):
            aktiv, inaktiv, eingestellt = (
                self.anzahl_aktiv,
                self.anzahl_inaktiv,
                self.anzahl_eingestellt,
            )
        else:
            zahlen = dict(
                self.hausartikel.order_by()
                .values_list("status")
                .annotate(anzahl=models.Count("pk"))
            )
            aktiv, inaktiv, eingestellt = (
                zahlen.get(Artikelstatus.AKTIV, 0),
                zahlen.get(Artikelstatus.INAKTIV, 0),
                zahlen.get(Artikelstatus.EINGESTELLT, 0),
            )
        if aktiv or not (inaktiv or eingestellt):
            return Artikelstatus.AKTIV
        return Artikelstatus.INAKTIV if inaktiv else Artikelstatus.EINGESTELLT

    def deaktivieren(self, zeitpunkt=None) -> int:
        """Sets all active house articles to inactive and returns their number.

        Via 'update()': inactive keeps expectations and slot, so there is
        nothing to clean up that would require 'save()'.
        """
        jetzt = zeitpunkt or timezone.now()
        return self.hausartikel.filter(status=Artikelstatus.AKTIV).update(
            status=Artikelstatus.INAKTIV, gueltig_bis=jetzt, geaendert_am=jetzt
        )

    def einstellen(self) -> int:
        """Discontinues all carried house articles one by one, so they leave the counting lists."""
        betroffen = list(self.hausartikel.filter(status__in=GEFUEHRT))
        for artikel in betroffen:
            artikel.einstellen()
        return len(betroffen)

    def status_setzen(self, status: str) -> list[str]:
        """Set all house articles active, inactive or discontinued.

        Inactive affects only the active ones, discontinued all carried ones.
        Active first brings back the inactive ones and then the discontinued,
        as far as their slot in the house is free. Whoever cannot come back is
        in the return value.
        """
        if status == Artikelstatus.INAKTIV:
            self.deaktivieren()
            return []
        if status == Artikelstatus.EINGESTELLT:
            self.einstellen()
            return []
        if status != Artikelstatus.AKTIV:
            raise ValidationError(
                _("Ob ein Artikel nicht zählbar ist, entscheidet die Familie seiner Ware.")
            )
        nicht_zurueck = []
        for alter in (Artikelstatus.INAKTIV, Artikelstatus.EINGESTELLT):
            # The one retired last first: under one number several discontinued
            # rows gather over the years, and the slot goes to whoever had it
            # last, not to whoever the sort happens to put in front.
            saetze = self.hausartikel.filter(status=alter).order_by("-gueltig_bis", "-pk")
            for artikel in saetze.select_related("haus"):
                try:
                    artikel.aktivieren()
                except ValidationError as fehler:
                    nicht_zurueck.extend(fehler.messages)
        return nicht_zurueck


class NachNameManager(models.Manager):
    """Finds a row by its name instead of its primary key.

    For 'dumpdata --natural-primary/--natural-foreign': base units and the
    container "without container" arise in a data migration and already
    exist in every freshly set up database, only possibly under another
    number. A dump that addresses them by name finds them again instead of
    failing on the uniqueness of the name.
    """

    def get_by_natural_key(self, name: str):
        return self.get(name=name)


#: The catch-all group for articles for which the file names no product
#: group. The name stands like this in the database, because the import
#: finds it by it.
OHNE_WARENGRUPPE = "Ohne Warengruppe"


#: Two rows are created by 'katalog/0002_stammdaten', plus the catch-all
#: group on the first import. They belong to the application and to no house,
#: and therefore their names are language and not data: shown is
#: 'anzeigename', the stored name stays as it is. Nothing is renamed, not even
#: on a change of the server language, because the choice hangs on the
#: account here as everywhere.
NICHT_ZAEHLBAR = _("Nicht zählbar")
NICHT_ZAEHLBAR_BESCHREIBUNG = _(
    "Was die Warenwirtschaft als Artikel führt, ohne dass es eine Ware ist: Pfand, "
    "Gutscheine, Rabatte, Menü-Buttons. Waren dieser Familie sind in jedem Haus "
    "nicht zählbar."
)
OHNE_BEHAELTER = _("Ohne Behälter (Waage genullt)")
OHNE_WARENGRUPPE_ANZEIGE = _("Ohne Warengruppe")


class Artikelstatus(models.TextChoices):
    """How a house article is doing.

    Four states instead of one switch, because "not active" meant three things:

    - **Inactive**: currently not in the range, but not over. Seasonal goods
      come back. They keep their number and their place in the counting list
      and can be counted in the app if they are on the shelf after all.
    - **Discontinued**: over. Overwritten by a reassigned number or delisted.
      Stays only for old results and releases number and product in the house.
    - **Ignored**: never was an article, say deposit, vouchers and whatever
      else the inventory management system carries as an article. Its product
      lies in the product family "Not countable", and only through it is it
      visible.
    """

    AKTIV = "AKTIV", _("Aktiv")
    INAKTIV = "INAKTIV", _("Inaktiv")
    EINGESTELLT = "EINGESTELLT", _("Eingestellt")
    IGNORIERT = "IGNORIERT", _("Nicht zählbar")


#: What a house carries, today or again some time. These rows occupy number and product.
GEFUEHRT = (Artikelstatus.AKTIV, Artikelstatus.INAKTIV)


#: What is expected nowhere any more.
AUSGESCHIEDEN = (Artikelstatus.EINGESTELLT, Artikelstatus.IGNORIERT)


#: What appears in lists and counts at all: everything except "Not countable".
SICHTBAR = (Artikelstatus.AKTIV, Artikelstatus.INAKTIV, Artikelstatus.EINGESTELLT)
