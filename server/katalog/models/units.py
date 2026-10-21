"""Units and quantities: dimension, id, base unit and computing with grams."""

import functools
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import ROUND_HALF_UP, Context, Decimal

from django.conf import settings
from django.db import models
from django.utils import translation
from django.utils.translation import gettext_lazy as _
from django.utils.translation import npgettext, pgettext, pgettext_lazy

from django_project.decimals import dezimaltrenner
from django_project.labels import Beschriftung

from .base import NachNameManager


def menge_kurz(wert: Decimal) -> str:
    """Quantity without superfluous decimal places, with the language's decimal separator.

    For display, in templates as in messages. Whoever computes on with the
    number takes the Decimal itself, not this text.
    """
    return f"{wert.normalize():f}".replace(".", dezimaltrenner())


def menge_mit_vorzeichen(wert: Decimal) -> str:
    """Like 'menge_kurz', only that the plus is there too.

    For differences. "+12" and "-12" are two different messages, and a bare
    "12" would leave open which of the two is meant. The zero stays without a
    sign, because there is nothing to distinguish there. Expected is the
    already rounded value, otherwise a remainder rounded to zero would still
    get a plus.
    """
    text = menge_kurz(wert)
    return f"+{text}" if wert > 0 else text


#: Set while a PDF is rendered, see 'portion_abgekuerzt'.
_portion_abgekuerzt: ContextVar[bool] = ContextVar("portion_abgekuerzt", default=False)


@contextmanager
def portion_abgekuerzt() -> Iterator[None]:
    """Inside the block the portion is short, like every other unit: "130 Port.".

    For the PDFs ('inventur/pdf.py'). A portrait page has no room for "+3
    Portionen" in four house columns, the screen has. A switch and not a
    parameter, because the texts come from properties the template reads.
    Like 'translation.override' it holds only for the running thread.
    """
    marke = _portion_abgekuerzt.set(True)
    try:
        yield
    finally:
        _portion_abgekuerzt.reset(marke)


def netto_in_basiseinheit(
    brutto: Decimal,
    gramm_je_einheit: Decimal,
    behaelter=None,
) -> Decimal:
    """Gross weight in grams, minus tare, in the unit of the article.

    The scale always delivers grams, but the article is carried in grams,
    kilograms, millilitres, litres or pieces. How many grams one of them
    weighs is said by 'gramm_je_einheit': for mass the unit's own factor (a
    kilogram weighs 1,000 g), for volume the density per unit, for piece
    goods the weight of one piece. The product knows it ('Ware.gramm_je_einheit').

    The tare is in grams as well. A container heavier than its content
    yields 0 instead of a negative quantity.
    """
    tara = behaelter.tara if behaelter else Decimal("0")
    netto = max(brutto - tara, Decimal("0"))
    return netto / gramm_je_einheit


class Dimension(models.TextChoices):
    """What a unit measures. A family sums in one of them, a product converts into the others.

    The portion (since 09/2026) is a dimension of its own: "snacks" sums
    in portions, and a bag of nuts in grams says how many grams one portion
    is. No product is carried in portions. The unit belongs to the families
    ('NUR_FUER_FAMILIEN'), the products only carry their factor into it.
    """

    STUECK = "STUECK", _("Stückzahl")
    MASSE = "MASSE", _("Masse")
    VOLUMEN = "VOLUMEN", _("Volumen")
    PORTION = "PORTION", _("Portionen")


class Einheitenkennung(models.TextChoices):
    """The six units, with their short form per language.

    The context "Basiseinheit" separates "g", "kg", "l" and "ml" from every
    other one-character text in the catalog. Without it they would coincide
    there.
    """

    STUECK = "STUECK", pgettext_lazy("Basiseinheit", "Stk.")
    GRAMM = "GRAMM", pgettext_lazy("Basiseinheit", "g")
    KILOGRAMM = "KILOGRAMM", pgettext_lazy("Basiseinheit", "kg")
    MILLILITER = "MILLILITER", pgettext_lazy("Basiseinheit", "ml")
    LITER = "LITER", pgettext_lazy("Basiseinheit", "l")
    PORTION = "PORTION", pgettext_lazy("Basiseinheit", "Portion")


#: Units no product and no article is carried in. They belong to the product
#: families: a family sums in portions, its products convert into them.
NUR_FUER_FAMILIEN = (Einheitenkennung.PORTION,)

#: The order of the dimensions for entering a factor: a portion holds
#: pieces, a piece holds millilitres and grams, a litre weighs grams. A
#: factor is stored as "reference units of the foreign dimension per unit of
#: the product", and that reads the wrong way round whenever the foreign
#: dimension ranks higher: "pieces per kilogram", "portions per gram".
#: Entered and shown is then the other way ("kg je Stk.", "g je Portion"),
#: stored is the reciprocal, so the sums compute like every other.
RANG_JE_DIMENSION: dict[str, int] = {
    Dimension.PORTION: 0,
    Dimension.STUECK: 1,
    Dimension.VOLUMEN: 2,
    Dimension.MASSE: 3,
}

#: For a reciprocal entry: the unit of the higher dimension it is entered
#: against, and how many reference units that is. Mass per volume is
#: entered per litre ("g je l", "kg je l"), not per millilitre.
KEHRWERT_EINHEIT: dict[str, tuple[str, Decimal]] = {
    Dimension.PORTION: (Einheitenkennung.PORTION, Decimal(1)),
    Dimension.STUECK: (Einheitenkennung.STUECK, Decimal(1)),
    Dimension.VOLUMEN: (Einheitenkennung.LITER, Decimal(1000)),
}

#: How precisely a conversion factor is stored and taken in: six places
#: before the decimal point, twelve after it ('WareUmrechnung',
#: 'Hausartikel.faktor_zur_ware' and every field that takes a factor). A
#: factor entered the other way round is stored as its reciprocal. With six
#: places "750 ml je Stk." became 0.001333 pieces per millilitre and came
#: back as 750.188, and the change of such a product from ml into pieces
#: could not even be entered (until 10/2026).
FAKTOR_STELLEN_GESAMT = 18
FAKTOR_NACHKOMMASTELLEN = 12

#: The smallest factor a field takes: one in the last place.
FAKTOR_MINDESTENS = Decimal(1).scaleb(-FAKTOR_NACHKOMMASTELLEN)

#: The first factor a field no longer holds: one more than its six places
#: before the point ('Hausartikel.faktor_zur_ware', 'Inventurergebnis.faktor_zur_ware',
#: 'referenzmenge_je_einheit'). PostgreSQL rejects more, and SQLite stores it.
FAKTOR_HOECHSTENS = Decimal(1).scaleb(FAKTOR_STELLEN_GESAMT - FAKTOR_NACHKOMMASTELLEN)

#: What a factor field says to a zero. Django's own text names the limit, and
#: with twelve places that reads "1E-12".
FAKTOR_ZU_KLEIN = _("Eine Umrechnung muss größer als null sein.")


def faktor_gerundet(wert: Decimal) -> Decimal:
    """A derived factor to the twelve places of its column, if the column can hold it at all.

    Rounded to twelve places, a number needs its digits before the point and
    twelve more, and Decimal computes with 28. From 1e16 on that raised
    'InvalidOperation', an error page, before the check could say that the
    column does not hold the factor (until 10/2026). Factors at both ends of
    the column reach that: 999999 ml per piece over 1e-12 g per piece. What
    the column cannot hold comes back unrounded, and the check refuses it
    with its message ('FAKTOR_HOECHSTENS').
    """
    if abs(wert) >= FAKTOR_HOECHSTENS:
        return wert
    return wert.quantize(FAKTOR_MINDESTENS, rounding=ROUND_HALF_UP)


#: With how many significant digits an entered reciprocal is shown. The
#: stored value carries twelve decimal places, and without rounding 1/3
#: comes back as 3.000000000003, 1030 g per litre as 1029.99999999975.
KEHRWERT_GENAUIGKEIT = Context(prec=6)


def kehrwert(eigene: str, fremde: str) -> bool:
    """Whether the factor from a product in 'eigene' into 'fremde' is entered the other way."""
    return RANG_JE_DIMENSION[fremde] < RANG_JE_DIMENSION[eigene]


def eingabe_zu_faktor(eigene: str, fremde: str, wert: Decimal) -> Decimal:
    """What the form took in, as the model stores it: reference units per unit of the product."""
    if not kehrwert(eigene, fremde):
        return wert
    _einheit, referenzmenge = KEHRWERT_EINHEIT[fremde]
    return referenzmenge / wert


def faktor_zu_eingabe(eigene: str, fremde: str, wert: Decimal) -> Decimal:
    """What the model stores, as the form shows it, without the trailing zeros of the column.

    The column hands a stored 500 back as 500.000000000000, and the form
    would show every one of those zeros.
    """
    if kehrwert(eigene, fremde):
        _einheit, referenzmenge = KEHRWERT_EINHEIT[fremde]
        wert = KEHRWERT_GENAUIGKEIT.divide(referenzmenge, wert)
    # Without an exponent: a form shows "1.03E+3" as it is.
    if wert == wert.to_integral_value():
        return wert.to_integral_value()
    return wert.normalize()


#: Name from the WinTICKET export to the id. The same mapping is in the
#: migration '0002_stammdaten', which creates the six units.
#: Here it is needed for backups older than the id.
KENNUNG_JE_NAME = {
    "Stück(e)": Einheitenkennung.STUECK,
    "g": Einheitenkennung.GRAMM,
    "kg": Einheitenkennung.KILOGRAMM,
    "Milliliter": Einheitenkennung.MILLILITER,
    "Liter": Einheitenkennung.LITER,
    "Portion": Einheitenkennung.PORTION,
}


#: The dimension per id, without a record behind it. The same mapping as in
#: the migration '0002_stammdaten'. The import plan needs it for rows to
#: which no unit is attached yet.
DIMENSION_JE_KENNUNG: dict[str, str] = {
    Einheitenkennung.STUECK: Dimension.STUECK,
    Einheitenkennung.GRAMM: Dimension.MASSE,
    Einheitenkennung.KILOGRAMM: Dimension.MASSE,
    Einheitenkennung.MILLILITER: Dimension.VOLUMEN,
    Einheitenkennung.LITER: Dimension.VOLUMEN,
    Einheitenkennung.PORTION: Dimension.PORTION,
}


#: The reference unit of every dimension: what a conversion factor counts in.
REFERENZ_JE_DIMENSION: dict[str, str] = {
    Dimension.STUECK: Einheitenkennung.STUECK,
    Dimension.MASSE: Einheitenkennung.GRAMM,
    Dimension.VOLUMEN: Einheitenkennung.MILLILITER,
    Dimension.PORTION: Einheitenkennung.PORTION,
}


def referenz_kurz(dimension: str) -> Beschriftung:
    """The short form of a dimension's reference unit: "g", "ml", "Stk.", "Portion"."""
    return Einheitenkennung(REFERENZ_JE_DIMENSION[dimension]).label


def faktor_beschriftung(eigene: str, fremde: str, einheit: Beschriftung) -> str:
    """What the factor into 'fremde' is called for a product in 'einheit' of 'eigene'.

    "g je l", "ml je Stk.": reference units per unit of the product. Where
    the foreign dimension ranks higher, the other way round: "kg je Stk.",
    "g je Portion", "g je l" ('RANG_JE_DIMENSION').
    """
    if kehrwert(eigene, fremde):
        gegen, _menge = KEHRWERT_EINHEIT[fremde]
        return _("%(einheit)s je %(referenz)s") % {
            "einheit": einheit,
            "referenz": Einheitenkennung(gegen).label,
        }
    return _("%(referenz)s je %(einheit)s") % {
        "referenz": referenz_kurz(fremde),
        "einheit": einheit,
    }


def umrechnung(
    haus: Basiseinheit, ware: Basiseinheit, angabe: Decimal | None = None
) -> Decimal | None:
    """How many units of the product one unit of the house is.

    Within a dimension the units compute themselves (one gram is 0.001 kg),
    an entry is then skipped. Across dimensions only whoever knows the
    product knows: then the entry applies, and without it 'None' comes back.
    A silent 1 would set pieces off against litres.
    """
    if haus.dimension == ware.dimension:
        return haus.faktor_zur_referenz / ware.faktor_zur_referenz
    return angabe if angabe and angabe > 0 else None


def einheit_kurz(kennung: str) -> Beschriftung:
    """The short form for an id, even without a record behind it.

    The import plan carries ids before it is known whether the unit exists.
    An unknown one comes back unchanged so it stays visible.
    """
    try:
        return Einheitenkennung(kennung).label
    except ValueError:
        return kennung


def braucht_faktor(dimension_haus: str, dimension_ware: str) -> bool:
    """Whether counting in one unit and carrying the product in another needs a factor.

    Within a dimension the unit computes itself (a gram is 0.001 kg), and a
    factor could only contradict it. Across dimensions only whoever knows the
    product knows how many of one a piece of the other is. The same rule on
    the house article, the import plan, its adoption and the product family.
    """
    return dimension_haus != dimension_ware


def kurzform(kennung: str) -> str:
    """The short form of a unit in the current language, from a small cache."""
    return _kurzform(kennung, translation.get_language() or settings.LANGUAGE_CODE)


@functools.lru_cache(maxsize=64)
def _kurzform(kennung: str, sprache: str) -> str:
    return str(Einheitenkennung(kennung).label)


class Basiseinheit(models.Model):
    """Unit in which a house article is carried and exported.

    Three things that want to be kept apart:

    - 'name' is the value under which WinTICKET exports the unit
      ("Stück(e)", "g", "kg", "Milliliter", "Liter"). It is the natural key
      by which a backup finds the unit again, and is **not** translated and
      **not** displayed.
    - 'kennung' is the language-independent id. The import matches against
      it, and the lists sort by it.
    - 'kurz' is the short form in the chosen language. It stands in the
      interface, also behind a number ("0,14 kg").
    """

    name = models.CharField(max_length=32, unique=True, verbose_name=_("Name"))
    kennung = models.CharField(
        max_length=16, unique=True, choices=Einheitenkennung.choices, verbose_name=_("Kennung")
    )
    nachkommastellen = models.PositiveSmallIntegerField(
        default=0,
        help_text=_("0 bei Stück. So genau stehen die Mengen im Ergebnis und auf dem Blatt."),
        verbose_name=_("Nachkommastellen"),
    )
    dimension = models.CharField(
        max_length=10,
        choices=Dimension.choices,
        default=Dimension.STUECK,
        verbose_name=_("Dimension"),
    )
    faktor_zur_referenz = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        default=Decimal("1"),
        help_text=_(
            "Gramm bzw. Milliliter je Einheit: Gramm 1, Kilogramm 1000, Milliliter 1, Liter 1000."
        ),
        verbose_name=_("Faktor zur Referenzeinheit"),
    )

    objects = NachNameManager()

    # Created by Django at runtime. Only named here so pyright knows it.
    get_kennung_display: Callable[[], str]

    class Meta:
        verbose_name = _("Basiseinheit")
        verbose_name_plural = _("Basiseinheiten")
        ordering = ["kennung"]

    def __str__(self) -> str:
        return self.kurz

    def natural_key(self) -> tuple[str]:
        return (self.name,)

    @classmethod
    def fuer_artikel(cls):
        """The units a product or an article can be carried in. The portion is none."""
        return cls.objects.exclude(kennung__in=NUR_FUER_FAMILIEN)

    @property
    def nur_fuer_familien(self) -> bool:
        """Whether only a family may sum in it. No product is carried in portions."""
        return self.kennung in NUR_FUER_FAMILIEN

    @property
    def kurz(self) -> str:
        """The short form in the chosen language.

        Every display place runs at display time, hence no lazy here: the text
        is fixed as soon as someone reads it. Cached per language: Django's
        'get_kennung_display' builds the dict of all choices on every call and
        sends every label through gettext. The import plan asked it half a
        million times and took twelve seconds (measured 2026-10-09).
        """
        return kurzform(self.kennung)

    def mit_menge(self, wert: Decimal, zahl: Callable[[Decimal], str] = menge_kurz) -> str:
        """A quantity in this unit, for display: "130 Portionen", but "1 Portion".

        "Stk.", "g" and "l" fit behind every number. The portion is a whole
        word, and "130 Portion" reads wrong. In a PDF it is "Port." behind
        every number ('portion_abgekuerzt'). 'zahl' writes the number, with
        or without its sign.
        """
        text = zahl(wert)
        if self.kennung != Einheitenkennung.PORTION:
            return f"{text} {self.kurz}"
        if _portion_abgekuerzt.get():
            return f"{text} {pgettext('Basiseinheit', 'Port.')}"
        betrag = abs(wert)
        # gettext picks the form by a whole number. A fraction takes the
        # plural in German and English ("1,5 Portionen"), so it counts as two.
        anzahl = int(betrag) if betrag == betrag.to_integral_value() else 2
        vorlage = npgettext("Basiseinheit", "%(menge)s Portion", "%(menge)s Portionen", anzahl)
        return vorlage % {"menge": text}

    def runden(self, wert: Decimal) -> Decimal:
        """Rounds a value to the precision intended for this unit, as people round.

        Half up, like the unit change and the merge: 12.5 pieces are 13 on
        the sheet. Python's default rounds half to even, and until 10/2026 the
        sheet, the check list and the app said 12 while the unit change said 13.
        A difference that rounds to nothing is 0, not "-0": it would read like
        a shortage ('menge_mit_vorzeichen').
        """
        gerundet = wert.quantize(Decimal(1).scaleb(-self.nachkommastellen), rounding=ROUND_HALF_UP)
        return gerundet if gerundet else abs(gerundet)
