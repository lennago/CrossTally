"""The product across all houses, its packs and its containers."""

from collections.abc import Callable
from decimal import Decimal
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

from django_project.labels import Beschriftung
from django_project.renaming import umbenennen
from organisation.models import Haus

from .base import (
    GEFUEHRT,
    OHNE_BEHAELTER,
    SICHTBAR,
    Artikelstatus,
    AusHausartikeln,
    NachNameManager,
    Sortiert,
    mit_statuszahlen,
)
from .units import (
    FAKTOR_MINDESTENS,
    FAKTOR_NACHKOMMASTELLEN,
    FAKTOR_STELLEN_GESAMT,
    KEHRWERT_EINHEIT,
    Basiseinheit,
    Dimension,
    Einheitenkennung,
    faktor_beschriftung,
    faktor_zu_eingabe,
    kehrwert,
    menge_kurz,
    netto_in_basiseinheit,
    referenz_kurz,
)
from .warenfamilie import Warenfamilie

if TYPE_CHECKING:
    # Only for pyright, as explained in 'base.py'.
    from django.db.models.fields.related_descriptors import ManyRelatedManager, RelatedManager

    from inventur.models import (
        Wiegung,
        Zaehlposition,
    )

    from .artikel import Hausartikel, Rezepturartikel, Rezepturkomponente
    from .inventurart import Inventurart

#: The loose single unit. It carries no name from the house but one from the
#: language. Which pack is meant is said by 'Gebinde.ist_einzeln'.
EINZELN = pgettext_lazy("Gebinde", "Einzeln")


#: Under this name it is stored. The name stays, because sort order and
#: uniqueness hang on it. It is no longer displayed. 'scripts.normalize'
#: carries it once more without Django, a test keeps both together.
EINZELN_NAME = "Einzeln"


class WareQuerySet(models.QuerySet):
    def echte(self) -> WareQuerySet:
        """All products except those in "Not countable", everywhere a product is meant."""
        return self.exclude(warenfamilie__sammelt_ignorierte=True)

    def mit_statuszahlen(self) -> WareQuerySet:
        return mit_statuszahlen(self)


class WareManager(NachNameManager):
    """Spelled out instead of 'from_queryset': the class Django builds there at
    runtime is not seen by the type checker. 'objects.echte()' would be foreign to it.
    """

    def get_queryset(self) -> WareQuerySet:
        return WareQuerySet(self.model, using=self._db)

    def echte(self) -> WareQuerySet:
        return self.get_queryset().echte()

    def mit_statuszahlen(self) -> WareQuerySet:
        return self.get_queryset().mit_statuszahlen()


#: The message for a product or article that would be carried in portions.
EINHEIT_NUR_FUER_FAMILIEN = _(
    "In dieser Einheit summieren nur Warenfamilien. Eine Ware wird in Stück, Gramm oder "
    "Litern geführt und rechnet in Portionen um."
)


class Ware(AusHausartikeln):
    """Collects identical house articles across several houses.

    Here also hangs whether the product is **weighed**, including the usual
    containers. A pack of its own for that would contribute nothing: there
    would be only one of it per product, without own quantity and without a
    name.

    If the product lies in the family **"Not countable"**, it is none: its
    house articles count as ignored in every house. If it switches in or
    out, 'save()' updates the house articles.
    """

    name = models.CharField(max_length=200, unique=True, verbose_name=_("Name"))
    basiseinheit = models.ForeignKey(
        Basiseinheit,
        on_delete=models.PROTECT,
        related_name="waren",
        verbose_name=_("Basiseinheit"),
    )
    warenfamilie = models.ForeignKey(
        Warenfamilie,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="waren",
        help_text=_("Womit diese Ware in den Auswertungen zusammensteht."),
        verbose_name=_("Warenfamilie"),
    )
    #: The family the product had before "Not countable", so a wrong click can
    #: be undone whole (since 2026-10-16). Kept on the way in, forgotten on the
    #: way out. A family deleted in between is forgotten with it.
    familie_vorher = models.ForeignKey(
        Warenfamilie,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        editable=False,
        related_name="+",
        verbose_name=_("Warenfamilie vor „Nicht zählbar“"),
    )
    abwiegbar = models.BooleanField(
        default=False,
        verbose_name=_("wird abgewogen"),
        help_text=_(
            "Die Ware kommt lose auf die Waage. Beim Zählen entsteht dafür neben "
            "den Gebinden eine eigene Eingabe."
        ),
    )
    behaelter = models.ManyToManyField(
        "katalog.Behaelter",
        blank=True,
        related_name="waren",
        verbose_name=_("Übliche Behälter"),
        help_text=_("Werden beim Wiegen zur Auswahl gestellt. Ihre Tara geht ab."),
        db_table="behaelter_ware",
    )

    objects = WareManager()

    # Created by Django at runtime. Only named here so pyright knows them.
    basiseinheit_id: int
    warenfamilie_id: int | None
    familie_vorher_id: int | None
    gebinde: RelatedManager[Gebinde]
    umrechnungen: RelatedManager[WareUmrechnung]
    verwendet_in: RelatedManager[Rezepturkomponente]

    #: Set by a form before validation: the factors it is about to write.
    #: 'clean()' then judges by these instead of the stored rows, which
    #: would still show the old state (or nothing at all for a new product).
    geplante_faktoren: dict[str, Decimal] | None = None

    class Meta:
        verbose_name = _("Ware")
        verbose_name_plural = _("Waren")
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} (in {self.basiseinheit.kurz})"

    def save(self, *args, **kwargs):
        """If the product switches into or out of "Not countable", its house articles follow.

        Every path goes through here: the product's form, the family's,
        'Warenfamilie.aufnehmen' and the buttons in the lists. Whether the switch
        works is checked beforehand ('pruefe_familienwechsel'). Here it is only
        carried out.

        The old state comes from the database, not from the object: a
        remembered value would survive 'refresh_from_db()' and would then see a
        switch where there is none, or overlook one.
        """
        felder = kwargs.get("update_fields")
        pruefen = felder is None or "warenfamilie" in felder
        with transaction.atomic():
            vorher = self._gespeicherte_familie() if pruefen else self.warenfamilie_id
            if self.warenfamilie_id != vorher:
                self._familie_merken(vorher)
                if felder is not None:
                    kwargs["update_fields"] = {*felder, "familie_vorher"}
            # Its name stands on the rows of all its articles ('django_project.renaming').
            with umbenennen(
                self, kwargs.get("update_fields"), lambda: _zeilen_der_artikel_sperren(self)
            ):
                super().save(*args, **kwargs)
            if self.warenfamilie_id != vorher:
                # Every article of the product, then all their rows, before the
                # first one is written. Article by article, a location renamed
                # in the same moment, which locks its rows by number, could hold
                # a row of the second article while waiting for one of the first.
                _zeilen_der_artikel_sperren(self)
                self.hausartikel_angleichen()

    def _familie_merken(self, vorher: int | None) -> None:
        """On the way into "Not countable" the family is kept, on the way out forgotten.

        Whatever path leads in: the buttons, the product's form or the
        family's. The way back ('wieder_aufnehmen') takes the product home.
        """
        war_ignoriert = vorher is not None and self._war_ignoriert()
        if self.ist_nicht_zaehlbar and not war_ignoriert:
            self.familie_vorher_id = vorher
        elif war_ignoriert and not self.ist_nicht_zaehlbar:
            self.familie_vorher_id = None

    def get_absolute_url(self) -> str:
        return reverse("ware_detail", kwargs={"pk": self.pk})

    def natural_key(self) -> tuple[str]:
        return (self.name,)

    def arten_der_artikel(self) -> list[tuple[Inventurart, list[Haus]]]:
        """The inventory types of its visible house articles, each with the houses in it.

        One entry if they agree, which is the normal case. In a list a
        prefetch with 'hausartikel__inventurart' and 'hausartikel__haus' saves
        the queries per product. A single product fetches both in one.
        """
        artikel_der_ware = self.hausartikel.all()
        if "hausartikel" not in getattr(self, "_prefetched_objects_cache", {}):
            artikel_der_ware = artikel_der_ware.select_related("inventurart", "haus")
        je_art: dict[int, tuple[Inventurart, list[Haus]]] = {}
        for artikel in artikel_der_ware:
            if artikel.status not in SICHTBAR:
                continue
            art = artikel.inventurart
            je_art.setdefault(art.pk, (art, []))[1].append(artikel.haus)
        return sorted(
            (
                (art, sorted(haeuser, key=lambda haus: haus.name))
                for art, haeuser in je_art.values()
            ),
            key=lambda eintrag: (eintrag[0].reihenfolge, eintrag[0].name),
        )

    @property
    def inventurart(self) -> Inventurart | None:
        """The type all its visible house articles agree on. None where they differ or are none."""
        arten = self.arten_der_artikel()
        return arten[0][0] if len(arten) == 1 else None

    @property
    def arten_gemischt(self) -> bool:
        """Whether its house articles are in different types."""
        return len(self.arten_der_artikel()) > 1

    def inventurart_setzen(self, art: Inventurart) -> int:
        """Puts every house article of the product into this type, in every house.

        Every one, also the discontinued and ignored ones: if one comes back,
        it should not bring an old type along. Returns how many changed.
        """
        from .erwartung import neue_art_ans_ende  # noqa: PLC0415  (a later module of the package)

        with neue_art_ans_ende():
            return self.hausartikel.exclude(inventurart=art).update(inventurart=art)

    @property
    def anzeigename(self) -> str:
        """The own name, as maintenance assigned it.

        The property exists because the shared page for splitting shows
        locations, product families and products with the same row
        ('components/split_page.html'). For the family the translation of "Not
        countable" hangs on it, here it is simply the name.
        """
        return self.name

    def _gespeicherte_familie(self) -> int | None:
        if self.pk is None:
            return None
        return Ware.objects.filter(pk=self.pk).values_list("warenfamilie_id", flat=True).first()

    @property
    def ist_nicht_zaehlbar(self) -> bool:
        familie = self.warenfamilie
        return familie is not None and familie.sammelt_ignorierte

    @property
    def warenstatus_anzeige(self) -> Beschriftung:
        """Like 'sammelstatus_anzeige', with "not countable" for the ignored product."""
        return Artikelstatus(self.warenstatus).label

    @property
    def warenstatus(self) -> str:
        """Like 'sammelstatus', only that a product from "Not countable" is ignored.

        Whatever its rows say: being in the family is the statement.
        """
        if self.ist_nicht_zaehlbar:
            return Artikelstatus.IGNORIERT
        return self.sammelstatus

    def hausartikel_angleichen(self) -> None:
        """Updates the status of the house articles after the family has changed.

        Back out of "Not countable" every row gets back what it was before
        ('status_vorher'), and at most one row per house may be carried
        again ('ein_gefuehrter_hausartikel_je_haus_und_ware'). A row without
        that memory (ignored from the start, or from before 10/2026) comes
        back active, or discontinued where it counts as another row
        ('_status_angleichen'). Where several of those meet in a house, the
        one carried last stays active and the others count as it from now
        on, and where a row with a memory is carried again, the ones without
        count as that row ('_dubletten_markieren').
        """
        artikel = list(self.hausartikel.order_by("haus_id", "pk"))
        if not self.ist_nicht_zaehlbar:
            self._dubletten_markieren(artikel)
        for satz in artikel:
            satz.ware = self
            vorher = (satz.status, satz.zaehlt_als_id, satz.status_vorher)
            satz._status_angleichen()
            if (satz.status, satz.zaehlt_als_id, satz.status_vorher) != vorher:
                satz.save(
                    update_fields=[
                        "status",
                        "gueltig_bis",
                        "zaehlt_als",
                        "status_vorher",
                        "geaendert_am",
                    ]
                )

    @staticmethod
    def _dubletten_markieren(artikel: list[Hausartikel]) -> None:
        """Of several ignored rows of one house, only one may come back carried.

        Rows that remember what they were restore themselves. Of those that
        remember a carried status there is one per house, except where two
        products merged while in "Not countable", or a row changed its
        product there. Then the active one stays, else the one carried last,
        and the others come back discontinued, counting as it. Until 10/2026
        both came back carried and ran into the unique index.

        The rows without a memory are sorted the same way: the way back
        makes them active. Where one with a memory is carried again, they
        count as that one. Among themselves the one that left the carried
        state last (the youngest 'gueltig_bis') was the one carried before.
        The others get it as what they count as.
        """
        je_haus: dict[int, list[Hausartikel]] = {}
        for satz in artikel:
            if satz.status == Artikelstatus.IGNORIERT and satz.zaehlt_als_id is None:
                je_haus.setdefault(satz.haus_id, []).append(satz)
        for zeilen in je_haus.values():
            ohne = [satz for satz in zeilen if not satz.status_vorher]
            gefuehrte = [satz for satz in zeilen if satz.status_vorher in GEFUEHRT]
            if gefuehrte:
                gefuehrte.sort(
                    key=lambda satz: (
                        satz.status_vorher == Artikelstatus.AKTIV,
                        satz.gueltig_bis or timezone.now(),
                        satz.pk,
                    ),
                    reverse=True,
                )
                bleibt, *weitere = gefuehrte
                for satz in weitere:
                    satz.status_vorher = Artikelstatus.EINGESTELLT
                dubletten = [*weitere, *ohne]
            elif len(ohne) > 1:
                ohne.sort(
                    key=lambda satz: (satz.gueltig_bis or timezone.now(), satz.pk), reverse=True
                )
                bleibt, *dubletten = ohne
            else:
                continue
            for satz in dubletten:
                satz.zaehlt_als = bleibt

    def pruefe_familienwechsel(self, familie: Warenfamilie | None) -> None:
        """Whether the product can switch into this family, as far as "Not countable" is concerned.

        In does not work as long as a house article is counted in a running
        inventory or the product is in a recipe. Out, every house article gets
        its old status back ('status_vorher'), and a row that comes back
        carried needs number and product free in the house.
        """
        wird_ignoriert = familie is not None and familie.sammelt_ignorierte
        if self.pk is None or wird_ignoriert == self._war_ignoriert():
            return
        gefuehrt_zurueck = set() if wird_ignoriert else self._kommen_gefuehrt_zurueck()
        fehler = []
        if wird_ignoriert and (
            rezepturen := list(self.verwendet_in.select_related("rezepturartikel"))
        ):
            namen = ", ".join(f"„{zeile.rezepturartikel.name}“" for zeile in rezepturen)
            fehler.append(
                _(
                    "steckt in der Rezeptur von %(namen)s. Erst dort herausnehmen, sonst "
                    "ließe sich der Rezepturartikel nirgends mehr auflösen."
                )
                % {"namen": namen}
            )
        for artikel in self.hausartikel.select_related("haus"):
            try:
                if wird_ignoriert:
                    artikel.pruefe_ignorierbar()
                elif artikel.pk not in gefuehrt_zurueck:
                    # Comes back discontinued and needs neither number nor
                    # product. Until 10/2026 a reassigned old number of the
                    # product blocked the whole way back.
                    continue
                elif (blockiert := artikel.blockiert_durch()) is not None:
                    fehler.append(
                        _(
                            "%(haus)s: %(nummer)s „%(name)s“ käme nicht zurück. Dort führt "
                            "%(nummer2)s „%(name2)s“ Nummer oder Ware."
                        )
                        % {
                            "haus": artikel.haus,
                            "nummer": artikel.artikelnummer,
                            "name": artikel.name,
                            "nummer2": blockiert.artikelnummer,
                            "name2": blockiert.name,
                        }
                    )
            except ValidationError as ausnahme:
                fehler.extend(
                    _("%(haus)s: %(text)s") % {"haus": artikel.haus, "text": text}
                    for text in ausnahme.messages
                )
        if fehler:
            raise ValidationError({"warenfamilie": fehler})

    def _kommen_gefuehrt_zurueck(self) -> set[int]:
        """The rows the way back makes carried, decided as 'hausartikel_angleichen' decides it.

        On rows of their own, so the check changes nothing. A row with a
        memory comes back as it was, one without comes back active unless it
        counts as another ('_dubletten_markieren'). Only those need number and
        product free in the house. Until 10/2026 every row without a memory
        was checked, also one that comes back as a duplicate.
        """
        artikel = list(self.hausartikel.order_by("haus_id", "pk"))
        self._dubletten_markieren(artikel)
        return {
            satz.pk
            for satz in artikel
            if satz.status_vorher in GEFUEHRT
            or (not satz.status_vorher and satz.zaehlt_als_id is None)
        }

    def _war_ignoriert(self) -> bool:
        gespeichert = self._gespeicherte_familie()
        return (
            gespeichert is not None
            and Warenfamilie.objects.filter(pk=gespeichert, sammelt_ignorierte=True).exists()
        )

    def ignorieren(self) -> None:
        """Into "Not countable", in every house."""
        familie = Warenfamilie.nicht_zaehlbar()
        self.pruefe_familienwechsel(familie)
        familie.aufnehmen(self)

    def wieder_aufnehmen(self) -> None:
        """Out of "Not countable", back into the family it had. The house articles follow.

        Every house article gets its old status back, and with it the places
        in the counting lists ('Hausartikel._plaetze_wiederherstellen'). A
        family deleted in the meantime is forgotten: the product then stands
        without one. Only for a product that lies there: a stale page (a
        second tab, the back button) sends the button again, and until
        10/2026 the product then silently lost its family.

        The family has to be able to sum it, as on every other way in
        ('clean'), and the first product of a family brings its lead unit
        ('Warenfamilie.aufnehmen'). Until 10/2026 the way back skipped both:
        a family merged meanwhile into one in another dimension took the
        product without its factor, and the family's sum went empty.
        """
        if not self._war_ignoriert():
            raise ValidationError(
                _("„%(name)s“ liegt nicht in „Nicht zählbar“.") % {"name": self.name}
            )
        zurueck = self.familie_vorher
        if zurueck is not None and zurueck.sammelt_ignorierte:
            zurueck = None
        self.pruefe_familienwechsel(zurueck)
        if (stufe := self.nicht_summierbar_in(zurueck)) is not None:
            raise ValidationError(self._nicht_summierbar(stufe))
        if zurueck is None:
            self.warenfamilie = None
            self.save(update_fields=["warenfamilie"])
        else:
            zurueck.aufnehmen(self)

    def clean(self) -> None:
        """Two rules on this product.

        **The product family has to be able to sum this product**: if its
        dimension deviates from the lead unit, the product needs its factor to
        that dimension ('WareUmrechnung'). Otherwise a family would arise whose
        sum nobody can form. That would only show up in the report, and then
        as an empty cell. A form that writes the factors in the same step
        announces them via 'geplante_faktoren'.

        The containers are checked by the form: a multiple assignment is only
        saved after the model, here one would still see the old state.
        """
        if self.warenfamilie_id != self._gespeicherte_familie():
            self.pruefe_familienwechsel(self.warenfamilie)

        if self.basiseinheit_id and self.basiseinheit.nur_fuer_familien:
            raise ValidationError({"basiseinheit": EINHEIT_NUR_FUER_FAMILIEN})

        familie = self.warenfamilie
        stufe = self.nicht_summierbar_in(familie) if self.basiseinheit_id else None
        if stufe is not None:
            raise ValidationError({"warenfamilie": self._nicht_summierbar(stufe)})

    def _nicht_summierbar(self, stufe: Warenfamilie) -> str:
        """Why the family cannot sum the product: the factor its lead unit needs is missing."""
        leiteinheit = stufe.leiteinheit
        assert leiteinheit is not None  # 'nicht_summierbar_in' skips a family without one.
        return _(
            "„%(familie)s“ rechnet in %(einheit)s. Ohne „%(faktor)s“ lässt sich die Ware dort "
            "nicht summieren."
        ) % {
            "familie": stufe,
            "einheit": leiteinheit.kurz,
            "faktor": self.faktor_beschriftung(leiteinheit.dimension),
        }

    @property
    def art_rueckfrage(self) -> str:
        """What is at stake when switching the inventory type for the product.

        A click in one row acts in every house that carries this product. That
        should be said before it acts.
        """
        # From 'mit_statuszahlen' where the number exists. In a list the
        # question would otherwise cost a query per row.
        haeuser = getattr(self, "anzahl_aktiv", None)
        if haeuser is None:
            haeuser = self.hausartikel.filter(aktiv=True).count()
        if haeuser <= 1:
            return ""
        return _(
            "„%(name)s“ wird in %(haeuser)s Häusern geführt. Die Inventurart gilt dann für alle "
            "ihre Artikel. Fortfahren?"
        ) % {"name": self.name, "haeuser": haeuser}

    @property
    def braucht_wiegefaktor(self) -> bool:
        """Whether the scale needs an entry: for mass a unit weighs what it is called."""
        return bool(self.basiseinheit_id) and self.basiseinheit.dimension != Dimension.MASSE

    def faktoren(self) -> dict[str, Decimal]:
        """Per foreign dimension: how many of its reference units one unit of this product is.

        What a form is about to write counts before what is stored, see
        'geplante_faktoren'. A new product has nothing stored yet.
        """
        if self.geplante_faktoren is not None:
            return dict(self.geplante_faktoren)
        if self.pk is None:
            return {}
        return {satz.dimension: satz.referenzmenge_je_einheit for satz in self.umrechnungen.all()}

    def faktor_nach(self, dimension: str) -> Decimal | None:
        """Reference units of 'dimension' per unit of this product, or None if unknown.

        Within the own dimension the unit computes itself: a kilogram is
        1,000 g. Across dimensions only the stored factor knows. A silent 1
        would set pieces off against litres.
        """
        if self.basiseinheit.dimension == dimension:
            return self.basiseinheit.faktor_zur_referenz
        return self.faktoren().get(dimension)

    def faktor_beschriftung(self, dimension: str) -> str:
        """What the factor to 'dimension' is called for this product: "g je l", "kg je Stk."."""
        if not self.basiseinheit_id:
            return _("%(referenz)s je %(einheit)s") % {
                "referenz": referenz_kurz(dimension),
                "einheit": _("Einheit"),
            }
        return faktor_beschriftung(self.basiseinheit.dimension, dimension, self.basiseinheit.kurz)

    def faktor_eingaben(self) -> dict[str, Decimal]:
        """The factors as a form shows them, some the other way round ('kehrwert')."""
        eigene = self.basiseinheit.dimension
        return {
            dimension: faktor_zu_eingabe(eigene, dimension, wert)
            for dimension, wert in self.faktoren().items()
        }

    @property
    def gramm_je_einheit(self) -> Decimal | None:
        """What one unit weighs, for the scale. None where the entry is missing."""
        return self.faktor_nach(Dimension.MASSE)

    def nicht_summierbar_in(self, familie: Warenfamilie | None) -> Warenfamilie | None:
        """The first family up the chain whose sum this product cannot join, or none."""
        if familie is None or familie.sammelt_ignorierte:
            return None
        for stufe in [familie, *familie.vorfahren()]:
            einheit = stufe.leiteinheit
            if einheit is not None and self.faktor_nach(einheit.dimension) is None:
                return stufe
        return None

    def umrechnungen_setzen(self, faktoren: dict[str, Decimal]) -> None:
        """Writes the factors as rows: what is not in 'faktoren' any more goes.

        The one path for every writer (form, unit change, merge, demo), so
        the rows never keep a dimension the product itself carries.
        """
        faktoren = {
            dimension: wert
            for dimension, wert in faktoren.items()
            if dimension != self.basiseinheit.dimension
        }
        self.umrechnungen.exclude(dimension__in=faktoren).delete()
        for dimension, wert in faktoren.items():
            WareUmrechnung.objects.update_or_create(
                ware=self, dimension=dimension, defaults={"referenzmenge_je_einheit": wert}
            )
        self.geplante_faktoren = None
        # The prefetched rows would otherwise still show the old state.
        self._prefetched_objects_cache = {}  # pyright: ignore[reportAttributeAccessIssue]

    def waehlbare_behaelter(self):
        """The assigned containers plus "without container"."""
        return Behaelter.objects.filter(
            models.Q(waren=self) | models.Q(immer_erlaubt=True)
        ).distinct()

    def in_basiseinheit(self, brutto: Decimal, behaelter=None) -> Decimal:
        """Converts a gross weight in grams into the base unit of this product.

        Without the entry the scale computes one gram per reference unit
        (per millilitre, per piece), as it always did while the default was
        1. The toggle for weighing warns about it, the form insists on the
        value.
        """
        gramm = self.gramm_je_einheit or self.basiseinheit.faktor_zur_referenz
        return netto_in_basiseinheit(brutto, gramm, behaelter)


class WareUmrechnung(models.Model):
    """What one unit of the product is in a foreign dimension.

    "One bottle holds 500 ml", "one litre weighs 1,030 g", "one kilogram is
    eight portions". Two things hang on it: the scale, which delivers grams
    and has to arrive in the product's unit, and the sum of the family, when
    it stands in another dimension than the product. Within the own
    dimension nothing is stored, there the unit computes itself.

    The factor hangs on the product and not on the family (until 09/2026 it
    did): a family "all lemonades" carries bottles of 0.5 l next to cans of
    0.33 l, and one factor for all pieces could only be wrong for one of them.
    """

    ware = models.ForeignKey(
        Ware,
        on_delete=models.CASCADE,
        related_name="umrechnungen",
        verbose_name=_("Ware"),
    )
    dimension = models.CharField(
        max_length=10, choices=Dimension.choices, verbose_name=_("Dimension")
    )
    referenzmenge_je_einheit = models.DecimalField(
        max_digits=FAKTOR_STELLEN_GESAMT,
        decimal_places=FAKTOR_NACHKOMMASTELLEN,
        validators=[MinValueValidator(FAKTOR_MINDESTENS)],
        help_text=_("Gramm, Milliliter bzw. Stück je Einheit der Ware."),
        verbose_name=_("Referenzmenge je Einheit"),
    )

    # Created by Django at runtime. Only named here so pyright knows them.
    ware_id: int
    get_dimension_display: Callable[[], str]

    class Meta:
        verbose_name = _("Umrechnung der Ware")
        verbose_name_plural = _("Umrechnungen der Ware")
        ordering = ["dimension"]
        constraints = [
            models.UniqueConstraint(
                fields=["ware", "dimension"],
                name="eine_umrechnung_je_ware_und_dimension",
                violation_error_message=_("Für diese Dimension steht hier schon ein Faktor."),
            )
        ]

    def __str__(self) -> str:
        return f"{self.ware.name}: {self.beschriftung}"

    @property
    def beschriftung(self) -> str:
        return self.ware.faktor_beschriftung(self.dimension)

    @property
    def referenz(self) -> Beschriftung:
        return referenz_kurz(self.dimension)

    @property
    def anzeigetext(self) -> str:
        """The row as an equation: "1 Stk. = 500 ml", turned round "1 Portion = 100 g"."""
        eigene = self.ware.basiseinheit.dimension
        einheit = self.ware.basiseinheit.kurz
        if kehrwert(eigene, self.dimension):
            gegen, _menge = KEHRWERT_EINHEIT[self.dimension]
            menge = faktor_zu_eingabe(eigene, self.dimension, self.referenzmenge_je_einheit)
            return f"1 {Einheitenkennung(gegen).label} = {menge_kurz(menge)} {einheit}"
        return f"1 {einheit} = {menge_kurz(self.referenzmenge_je_einheit)} {self.referenz}"

    def clean(self) -> None:
        if self.ware_id and self.dimension == self.ware.basiseinheit.dimension:
            raise ValidationError(
                {
                    "dimension": _(
                        "Innerhalb der eigenen Dimension rechnet die Einheit selbst um. "
                        "Hier gehören nur fremde Dimensionen hinein."
                    )
                }
            )


def _zeilen_der_artikel_sperren(ware: Ware):
    """For a new name of the product: its articles, then their rows ('zaehlpositionen_sperren').

    A new row of the app locks its article before it reads article and
    product ('api.serializers.counting.gesperrter_artikel'). It then waits
    for the rename and reads the new name.
    """
    from .artikel import zaehlpositionen_sperren  # noqa: PLC0415  (artikel.py imports this module)

    return zaehlpositionen_sperren(list(ware.hausartikel.all()))


def _zeilen_des_gebindes_sperren(gebinde: Gebinde):
    """For a new name of the pack: the articles of its product, then the rows counted in it.

    The articles, because a new row of the app locks its article before it
    reads its pack ('api.serializers.counting.aktuelles_gebinde'). Of the
    product as stored: merging moves a pack onto the product that stays and
    renames it in one save, and the articles of the one that goes are
    locked already. Those of the other one would come after the rows the
    merge locked, out of the order every path keeps.
    """
    from .artikel import Hausartikel, artikel_sperren, zaehlpositionen_sperren  # noqa: PLC0415  (artikel.py imports this module)

    gespeichert = Gebinde._base_manager.filter(pk=gebinde.pk).values("ware_id")
    artikel_sperren(
        Hausartikel.objects.filter(ware_id__in=gespeichert).values_list("pk", flat=True)
    )
    return zaehlpositionen_sperren([gebinde])


#: As long as a pack's name may be. The pack field of the import checks it too.
GEBINDENAME_LAENGE = 100

#: What the column of a pack holds: ten digits, three of them after the point
#: ('Gebinde.faktor_in_basiseinheit'). PostgreSQL rejects more, and SQLite
#: would store it and fail when reading. The import, the merge and the change
#: of unit check against it.
GEBINDE_HOECHSTMENGE = Decimal("1e7")


class Gebinde(Sortiert):
    """A counted packaging of a product: single, crate, box, bag.

    The number is multiplied by a fixed factor. Weighing is no pack any
    more: it hangs directly on the product as 'abwiegbar' including
    containers. There was only one of it per product anyway, it carried no
    quantity of its own, and as a pack it demanded a name nobody needed.
    """

    gruppenfeld = "ware"

    ware = models.ForeignKey(
        Ware,
        on_delete=models.CASCADE,
        related_name="gebinde",
        verbose_name=_("Ware"),
    )
    name = models.CharField(
        max_length=GEBINDENAME_LAENGE,
        help_text=_("z. B. Einzeln, Kasten, Karton, Tüte …"),
        verbose_name=_("Name"),
    )
    ist_einzeln = models.BooleanField(
        default=False,
        verbose_name=_("Das lose Einzelstück"),
        help_text=_("Es heißt in jeder Sprache anders. Der Name im Feld daneben zählt dann nicht."),
    )
    aktiv = models.BooleanField(default=True, verbose_name=_("aktiv"))
    faktor_in_basiseinheit = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0.001"))],
        verbose_name=_("Menge je Gebinde"),
        help_text=_("Menge in Basiseinheit je Gebinde, z. B. Kasten = 20."),
    )

    # Created by Django at runtime. Only named here so pyright knows them.
    ware_id: int
    zaehlpositionen: RelatedManager[Zaehlposition]

    class Meta:
        verbose_name = _("Gebinde")
        verbose_name_plural = _("Gebinde")
        ordering = ["ware", "sortierung"]
        constraints = [
            models.UniqueConstraint(
                fields=["ware", "name"],
                name="gebinde_name_je_ware_eindeutig",
            ),
            models.UniqueConstraint(
                fields=["ware", "sortierung"],
                name="gebindesortierung_je_ware_eindeutig",
                violation_error_message=_("An dieser Ware ist die Position bereits vergeben."),
            ),
            models.UniqueConstraint(
                fields=["ware", "faktor_in_basiseinheit"],
                name="gebindemenge_je_ware_eindeutig",
                violation_error_message=_(
                    "Diese Menge führt die Ware schon unter einem anderen Namen. "
                    "Beim Zählen ließen sich die beiden nicht unterscheiden."
                ),
            ),
            # That it moreover only affects piece goods is in 'clean()'. The unit
            # hangs on the product, and a check sees only its own table.
            models.CheckConstraint(
                condition=models.Q(ist_einzeln=False) | models.Q(faktor_in_basiseinheit=1),
                name="einzeln_ist_genau_eines",
                violation_error_message=_("Das lose Einzelstück ist immer genau eines."),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.ware.name} - {self.anzeigename}"

    def save(self, *args, **kwargs):
        """A new name reaches the rows counted in the pack ('django_project.renaming')."""
        with umbenennen(
            self, kwargs.get("update_fields"), lambda: _zeilen_des_gebindes_sperren(self)
        ):
            super().save(*args, **kwargs)

    @property
    def anzeigename(self) -> Beschriftung:
        """The loose single unit carries no name from the house but one from the language."""
        return EINZELN if self.ist_einzeln else self.name

    def clean(self) -> None:
        """The pack's rules at the model's door. The formset and the API check them too.

        The loose single unit is always exactly one and exists only for products
        counted in pieces. Two packs of one product must not mean the same quantity:
        when counting they could not be told apart. 'gleiche_menge' finds the twin.
        """
        if self.ware_id is None or self.faktor_in_basiseinheit is None:
            return
        if self.ist_einzeln and self.faktor_in_basiseinheit != Decimal(1):
            raise ValidationError(
                {"faktor_in_basiseinheit": _("Das lose Einzelstück ist immer genau eines.")}
            )
        if self.ist_einzeln and self.ware.basiseinheit.kennung != Einheitenkennung.STUECK:
            raise ValidationError(
                {
                    "ist_einzeln": _(
                        "Ein loses Einzelstück gibt es nur bei Ware, die in Stück gezählt wird. "
                        "„%(name)s“ wird in %(einheit)s geführt."
                    )
                    % {
                        "name": self.ware.name,
                        "einheit": self.ware.basiseinheit.kurz,
                    }
                }
            )
        if (doppelt := self.gleiche_menge()) is not None:
            raise ValidationError(
                {
                    "faktor_in_basiseinheit": _(
                        "„%(name)s“ steht bei dieser Ware schon für %(menge)s %(einheit)s. "
                        "Zwei Namen für dieselbe Menge lassen sich beim Zählen nicht "
                        "unterscheiden."
                    )
                    % {
                        "name": doppelt.anzeigename,
                        "menge": menge_kurz(self.faktor_in_basiseinheit),
                        "einheit": self.ware.basiseinheit.kurz,
                    }
                }
            )

    @classmethod
    def einzelstuecke_nachtragen(cls) -> int:
        """Sets the switch where the name means the loose single unit.

        Needed after restoring a backup older than the switch
        ('django_project/backup.py'). There the single unit still stands as a
        name, and on an English interface it would stay German. The rule: only
        piece goods, only the quantity 1. A litre in a bottle is a pack.
        """
        gefunden = cls.objects.filter(
            ware__basiseinheit__kennung=Einheitenkennung.STUECK,
            name__in=[EINZELN_NAME, "Stück(e)"],
            faktor_in_basiseinheit=1,
        )
        return gefunden.update(name=EINZELN_NAME, ist_einzeln=True)

    def gleiche_menge(self) -> Gebinde | None:
        """Another pack of the same product with the same quantity.

        The database enforces this itself since the inheritance was dropped,
        because both columns are in one table. This check stays nonetheless: it
        names in the form the name that already occupies the quantity instead
        of only reporting "already exists". For the case that two **new** rows
        collide in one go, the formset additionally watches, because those do
        not see each other in the database yet.
        """
        return (
            Gebinde.objects.filter(
                ware_id=self.ware_id,
                faktor_in_basiseinheit=self.faktor_in_basiseinheit,
            )
            .exclude(pk=self.pk)
            .first()
        )

    @classmethod
    def vorhandenes(cls, ware: Ware, name: str, faktor: Decimal) -> Gebinde | None:
        """The pack a typed new row lands on: the one with its name, else the one with its quantity.

        Both are unique per product, so the row cannot become a second pack
        under either. The house article form takes the pack that is there
        ('NeueGebindeMixin.gebinde_anlegen'), and before that it asks with the
        same lookup whether that pack is inactive ('NeuesGebindeForm'). The
        name comes first, as it always did.
        """
        return (
            cls.objects.filter(ware=ware, name=name).first()
            or cls.objects.filter(ware=ware, faktor_in_basiseinheit=faktor).first()
        )

    def aktiv_setzen(self, aktiv: bool) -> None:
        """Switches the pack on or off: one way for the product's page and the house article form.

        Whoever reactivates a typed pack while creating or editing an article
        gets the same as with the button on the product's page. An inactive
        pack gets no row in new inventories ('Inventur.zaehlpositionen_anlegen'),
        and its defaults stay until it is active again.
        """
        self.aktiv = aktiv
        self.save(update_fields=["aktiv"])

    def in_basiseinheit(self, eingabemenge: Decimal, behaelter=None) -> Decimal:
        """Number times factor. 'behaelter' exists only for weighing, see 'Ware'."""
        return eingabemenge * self.faktor_in_basiseinheit


class Behaelter(models.Model):
    name = models.CharField(max_length=100, unique=True, verbose_name=_("Name"))
    tara = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        validators=[MinValueValidator(Decimal("0"))],
        help_text=_("Leergewicht in Gramm, wird abgezogen."),
        verbose_name=_("Tara"),
    )
    immer_erlaubt = models.BooleanField(
        default=False,
        editable=False,
        verbose_name=_("steht überall zur Auswahl"),
        # Carried by exactly one record, from the data migration. Not settable
        # through forms, the uniqueness is secured by a partial index.
        help_text=_("Kennzeichnet „Ohne Behälter“. Er steht bei jeder Wiegung zur Wahl."),
    )

    haeuser = models.ManyToManyField(
        Haus,
        blank=True,
        related_name="behaelter",
        verbose_name=_("Häuser"),
        # Two kinds of containers: the spray cream can only ever holds spray
        # cream and hangs on its product alone. The bowl stands around in the
        # house, and in it goes whatever is being weighed. Only the second kind
        # gets a house and is then available for every article.
        help_text=_(
            "Wo der Behälter im Haus benutzt wird. Ohne Haus steht er nur bei seinen Artikeln."
        ),
        db_table="behaelter_haus",
    )

    objects = NachNameManager()

    # Created by Django at runtime. Only named here so pyright knows them.
    waren: ManyRelatedManager[Ware, models.Model]
    rezepturartikel: ManyRelatedManager[Rezepturartikel, models.Model]
    wiegungen: RelatedManager[Wiegung]

    class Meta:
        verbose_name = _("Behälter")
        verbose_name_plural = _("Behälter")
        ordering = ["-immer_erlaubt", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["immer_erlaubt"],
                condition=models.Q(immer_erlaubt=True),
                name="nur_ein_behaelter_immer_erlaubt",
                violation_error_message=_("„Ohne Behälter“ gibt es nur einmal."),
            )
        ]

    def __str__(self) -> str:
        return str(self.anzeigename)

    def get_absolute_url(self) -> str:
        return reverse("behaelter_edit", kwargs={"pk": self.pk})

    def natural_key(self) -> tuple[str]:
        return (self.name,)

    @property
    def anzeigename(self) -> Beschriftung:
        """The row "Without container" belongs to the application. Every other one is in a house."""
        return OHNE_BEHAELTER if self.immer_erlaubt else self.name

    def delete(self, *args, **kwargs):
        """The row "Without container" stays. Without it nothing could be weighed on a zeroed scale.

        The backstop for every path past the delete page. That one says it more
        kindly beforehand. A 'QuerySet.delete()' does not pass here. Only the
        data migration needs that on its way back.
        """
        if self.immer_erlaubt:
            raise ValidationError(
                _("„Ohne Behälter“ gehört zum Datenmodell und lässt sich nicht löschen.")
            )
        return super().delete(*args, **kwargs)

    @classmethod
    def ohne_behaelter(cls) -> Behaelter:
        """The record for "scale zeroed / no container" (tare 0)."""
        return cls.objects.get(immer_erlaubt=True)

    @classmethod
    def zuweisbar(cls):
        """The containers that can be assigned to a product.

        "Without container" is not among them: it is available at every weighing
        anyway, because 'waehlbare_behaelter()' always adds it. Ticking it would
        be a decision that does not exist, and an unticked box would suggest it
        was then not available.
        """
        return cls.objects.exclude(immer_erlaubt=True)

    @classmethod
    def zur_wahl_fuer(cls, artikel) -> models.QuerySet[Behaelter]:
        """What this article can be weighed in: the one rule for app and server.

        Three things: what is **assigned** to the article, what belongs to
        **any house** (the bowl standing around, in the own house as borrowed
        from another), and "Without container". A container without a house that
        only hangs on other articles is not among them: in the spray cream can
        there is never anything but spray cream. The order in which the app
        offers them is in 'api/master_data.py'.

        'artikel' is whoever carries the containers: the product or the recipe
        article.
        """
        zugeordnet = (
            models.Q(waren=artikel)
            if isinstance(artikel, Ware)
            else models.Q(rezepturartikel=artikel)
        )
        return cls.objects.filter(
            zugeordnet | models.Q(immer_erlaubt=True) | models.Q(haeuser__isnull=False)
        ).distinct()

    def wird_benutzt(self) -> bool:
        """Whether anything is or was weighed in this container.

        A weighing counts too: it points to it via PROTECT, and without this
        question deleting would run into a server error.
        """
        return self.waren.exists() or self.rezepturartikel.exists() or self.wiegungen.exists()
