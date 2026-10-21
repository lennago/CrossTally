"""The product group from the inventory management system, on which the house articles hang."""

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from django_project.labels import Beschriftung
from organisation.models import Haus

from .base import OHNE_WARENGRUPPE, OHNE_WARENGRUPPE_ANZEIGE, SICHTBAR, AusHausartikeln
from .inventurart import Inventurart
from .ware import Ware
from .warenfamilie import Warenfamilie

#: As long as a product group's name may be. The import plan checks the file against it.
WARENGRUPPENNAME_LAENGE = 100


class Warengruppe(AusHausartikeln):
    """Categorisation of the house articles: the product group of the inventory management."""

    name = models.CharField(max_length=WARENGRUPPENNAME_LAENGE, unique=True, verbose_name=_("Name"))

    # Attaches the list of product groups so 'haeuser' does not ask per row.
    haeuser_vorgeladen: list[Haus]
    # The same for the type all its articles agree on, None where they differ or are none.
    art_der_gruppe: Inventurart | None
    # And whether they differ: without visible articles the group is not "mixed".
    arten_gemischt: bool

    class Meta:
        verbose_name = _("Warengruppe")
        verbose_name_plural = _("Warengruppen")
        ordering = ["name"]

    def __str__(self) -> str:
        return str(self.anzeigename)

    def get_absolute_url(self):
        return reverse("warengruppe_detail", kwargs={"pk": self.pk})

    @property
    def anzeigename(self) -> Beschriftung:
        """The catch-all group belongs to the application, its name comes from the language.

        Every other product group comes from the inventory management system and
        stays as it is called there.
        """
        return OHNE_WARENGRUPPE_ANZEIGE if self.name == OHNE_WARENGRUPPE else self.name

    @property
    def haeuser(self):
        """Houses that carry articles of this product group.

        In the list the answer hangs prefetched on the row ('haeuser_vorgeladen',
        see 'WarengruppeListView'), otherwise every row would cost a query.
        """
        if (vorgeladen := getattr(self, "haeuser_vorgeladen", None)) is not None:
            return vorgeladen
        return list(
            Haus.objects.filter(
                hausartikel__warengruppe=self, hausartikel__status__in=SICHTBAR
            ).distinct()
        )

    def ware(self):
        """The products behind the house articles of this group. "Not countable" is none."""
        return Ware.objects.echte().filter(hausartikel__warengruppe=self).distinct()

    def arten_verteilung(self) -> list[tuple[Inventurart, int]]:
        """How many of its visible house articles count in which type, in the types' order."""
        zahlen = dict(
            self.hausartikel.filter(status__in=SICHTBAR)
            .order_by()
            .values_list("inventurart")
            .annotate(anzahl=models.Count("pk"))
        )
        return [(art, zahlen[art.pk]) for art in Inventurart.objects.all() if art.pk in zahlen]

    def inventurart_setzen(self, art: Inventurart) -> int:
        """Puts all house articles of this group into this type. Returns how many changed.

        Only those of the group: the group hangs on the house article. Where
        another house carries the same product under another group, it keeps
        its type.
        """
        from .erwartung import neue_art_ans_ende  # noqa: PLC0415  (a later module of the package)

        with neue_art_ans_ende():
            return self.hausartikel.exclude(inventurart=art).update(inventurart=art)

    def ignorieren(self) -> int:
        """Puts all products of this group into "Not countable". Returns their number.

        Not countable is always the **product**. One that another house carries
        under another group is so there too afterwards. Checked beforehand for
        all: if it does not work for one, the whole group stays as it was
        instead of half switched.
        """
        familie = Warenfamilie.nicht_zaehlbar()
        waren = list(self.ware())
        fehler = []
        for ware in waren:
            try:
                ware.pruefe_familienwechsel(familie)
            except ValidationError as ausnahme:
                fehler.extend(
                    _("„%(name)s“: %(text)s") % {"name": ware.name, "text": text}
                    for text in ausnahme.messages
                )
        if fehler:
            raise ValidationError(fehler)
        with transaction.atomic():
            for ware in waren:
                familie.aufnehmen(ware)
        return len(waren)
