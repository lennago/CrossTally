"""Reports over closed inventories, grouped by product family.

Two views of the same frozen rows:

- **One inventory**: what was counted, what the system expected and the
  difference: the sheet that is worked through in the inventory management
  system. Grouped by **product group** and sorted by article number, because
  that is how the system lists its articles ('warengruppen_einer_inventur').
- **Several inventories** over a period and several houses: only the
  **differences**, summed per product. If the same deviation repeats, a
  movement of goods between two houses is usually missing or something was
  miscounted once. Actual and target stocks only appear there if exactly
  one inventory per house is in the selection. Otherwise stocks would be
  added up over time.

The reports over several inventories are grouped by **product family**,
not by product group: that comes from the inventory management system and
is cut differently per house. The families
form a tree ('katalog/family_tree.py'): a family above sums everything
below it, in its own lead unit.
"""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import NamedTuple

from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from django_project.labels import Beschriftung
from katalog.family_tree import OHNE_FAMILIE, Familienknoten, familienbaum
from katalog.models import (
    Artikelstatus,
    Basiseinheit,
    Ware,
    Warenfamilie,
    menge_kurz,
    menge_mit_vorzeichen,
    nummer_sortierschluessel,
)

from .models import Inventur, Inventurergebnis, Inventurstatus
from .services import differenz_klasse, ergebniszeilen

__all__ = ["OHNE_FAMILIE"]

#: From this many houses on a product counts as counted across houses.
IN_MEHREREN_HAEUSERN = 2


@dataclass
class Summe:
    """A sum per dimension and what it means in the usual units.

    Adding happens in the reference unit (grams, millilitres, pieces), because
    a family can carry products in grams **and** kilograms. It is then shown
    in every unit that occurs in the family: "2,500 g = 2.5 kg". Across
    dimensions nothing is added, because pieces and kilograms have no common
    denominator.
    """

    dimension: str
    referenzmenge: Decimal
    einheiten: list[Basiseinheit] = field(default_factory=list)

    def als_text(self, zahl: Callable[[Decimal], str] = menge_kurz) -> str:
        teile = [
            einheit.mit_menge(
                einheit.runden(self.referenzmenge / einheit.faktor_zur_referenz), zahl
            )
            for einheit in sorted(self.einheiten, key=lambda e: e.faktor_zur_referenz)
        ]
        return " = ".join(teile)


#: A product and a quantity in its unit, or none where there was nothing.
Paar = tuple[Ware, Decimal | None]


def summieren(paare: list[Paar]) -> list[Summe]:
    """Sums up quantities per dimension, converted via the reference unit."""
    je_dimension: dict[str, Summe] = {}
    for ware, menge in paare:
        if menge is None:
            continue
        einheit = ware.basiseinheit
        summe = je_dimension.setdefault(einheit.dimension, Summe(einheit.dimension, Decimal("0")))
        summe.referenzmenge += Decimal(menge) * einheit.faktor_zur_referenz
        if einheit not in summe.einheiten:
            summe.einheiten.append(einheit)
    return list(je_dimension.values())


class Zelle(NamedTuple):
    """A number and the colour it carries. A template cannot zip."""

    text: str
    klasse: str


def _in_leiteinheit(
    familie: Warenfamilie | None, paare: list[Paar]
) -> tuple[Decimal, Basiseinheit] | None:
    """Everything in the family's lead unit, already rounded.

    Every product converts for itself ('Ware.faktor_nach'): within the lead
    dimension through its unit, across dimensions through its stored factor.
    None where that does not work out: without a family, without a quantity
    and where a product lacks its factor. Then the sums are shown separately
    instead of adding two things wrongly.
    """
    if familie is None or familie.leiteinheit_id is None:
        return None
    einheit = familie.leiteinheit
    gesamt = Decimal("0")
    gezaehlt = False
    for ware, menge in paare:
        if menge is None:
            continue
        faktor = ware.faktor_nach(einheit.dimension)
        if faktor is None:
            return None
        gesamt += menge * faktor / einheit.faktor_zur_referenz
        gezaehlt = True
    if not gezaehlt:
        return None
    return einheit.runden(gesamt), einheit


def familiensumme(
    familie: Warenfamilie | None,
    paare: list[Paar],
    zahl: Callable[[Decimal], str] = menge_kurz,
) -> str:
    """The quantities of a family in its lead unit: the one value under the family.

    The check list shows it already during the count. If it computed itself,
    a different number might stand there than later on the sheet. It writes
    its numbers with a comma and passes 'zahl' in for that.
    """
    gesamt = _in_leiteinheit(familie, paare)
    if gesamt is None:
        return " · ".join(summe.als_text(zahl) for summe in summieren(paare))
    menge, einheit = gesamt
    return einheit.mit_menge(menge, zahl)


def summenklasse(familie: Warenfamilie | None, paare: list[Paar]) -> str:
    """The colour for the sum of the differences, see 'services.differenz_klasse'.

    Empty where there was nothing to sum and where the sum falls apart into
    several dimensions that do not agree. A plus in grams and a minus in
    pieces together make no statement that would carry a colour.
    """
    gesamt = _in_leiteinheit(familie, paare)
    if gesamt is not None:
        return differenz_klasse(gesamt[0])
    klassen = {differenz_klasse(summe.referenzmenge) for summe in summieren(paare)}
    return klassen.pop() if len(klassen) == 1 else ""


@dataclass
class Warenzeile:
    """A product in the report, across one or more houses."""

    ware: Ware
    ist: Decimal | None = None
    soll: Decimal | None = None
    #: Empty where there was no target, on the sheet as in the sum over several
    #: inventories. A zero would look like "fine", and a find of twelve bottles
    #: would go under.
    differenz: Decimal | None = None
    #: In how many houses it was counted in this selection.
    haeuser: int = 0
    #: The article number of the house, only where there is exactly one.
    nummer: str = ""
    #: The house's product group, only on the sheet of one inventory.
    warengruppe: str = ""
    #: What the house calls the product, if it deviates.
    name_im_haus: str = ""
    #: The status of the house article if it is no longer carried: "discontinued".
    vermerk: str = ""
    #: What was noted in the check list, only on the sheet of one inventory.
    #: Across houses there is no note: it belongs to the article of one house.
    notiz: str = ""
    #: Only over several inventories: from how many inventories the product
    #: comes and how many of them had a target for it.
    ergebnisse: int = 0
    mit_differenz: int = 0
    #: Only in the run: the difference per house, in the order of the house columns.
    je_haus: list[Decimal | None] = field(default_factory=list)
    #: Only in the run: whether the column's house lists the product at all.
    im_haus: list[bool] = field(default_factory=list)
    #: Only on the sheet of one inventory: the unit the result was frozen in,
    #: the house's at closing ('Inventurergebnis.einheit'), as on the page of
    #: the inventory. Everywhere else the rows are in the product's unit,
    #: because the sums under the families need the product's factors.
    hauseinheit: Basiseinheit | None = None
    #: Results without a way into the product's unit, left out of every
    #: number of the row ('Inventurergebnis.faktor_zur_ware_wirksam').
    ohne_umrechnung: int = 0

    @property
    def einheit(self) -> Basiseinheit:
        return self.hauseinheit or self.ware.basiseinheit

    def hinzufuegen(
        self,
        *,
        ist: Decimal | None = None,
        soll: Decimal | None = None,
        differenz: Decimal | None = None,
    ) -> None:
        """Adds the numbers of one more result, each only where it has one.

        What had no target contributes nothing, and a sum without any stays
        empty instead of zero (see 'differenz'). Until 10/2026 the report over
        several inventories and the run repeated these lines in six places.
        """
        if ist is not None:
            self.ist = (self.ist or Decimal("0")) + ist
        if soll is not None:
            self.soll = (self.soll or Decimal("0")) + soll
        if differenz is not None:
            self.differenz = (self.differenz or Decimal("0")) + differenz

    @property
    def auffaellig(self) -> bool:
        """By the difference as the row shows it: one that rounds to 0 is none."""
        return self.differenz is not None and bool(self.einheit.runden(self.differenz))

    def _text(self, wert, zahl: Callable[[Decimal], str] = menge_kurz) -> str:
        if wert is None:
            return "-"
        return self.einheit.mit_menge(self.einheit.runden(wert), zahl)

    @property
    def ist_text(self) -> str:
        return self._text(self.ist)

    @property
    def soll_text(self) -> str:
        return self._text(self.soll)

    @property
    def differenz_text(self) -> str:
        return self._text(self.differenz, menge_mit_vorzeichen)

    @property
    def differenz_klasse(self) -> str:
        if self.differenz is None:
            return ""
        return differenz_klasse(self.einheit.runden(self.differenz))

    @property
    def je_haus_zellen(self) -> list[Zelle]:
        """Empty where the house does not carry the product. "-" where it has no target."""
        return [
            Zelle(
                self._text(differenz, menge_mit_vorzeichen) if da else "",
                differenz_klasse(self.einheit.runden(differenz))
                if da and differenz is not None
                else "",
            )
            for differenz, da in zip(self.je_haus, self.im_haus, strict=True)
        ]

    @property
    def gegenlaeufig(self) -> bool:
        """One house is missing what another has in excess: usually a movement of goods.

        By the differences as their cells show them ('je_haus_zellen'). One
        that rounds to 0 stands there as "0" in green and moves nothing.
        Until 10/2026 it counted with its raw value.
        """
        differenzen = [
            self.einheit.runden(differenz) for differenz in self.je_haus if differenz is not None
        ]
        return any(d > 0 for d in differenzen) and any(d < 0 for d in differenzen)

    @property
    def teilsumme_text(self) -> str:
        """A sum that is missing inventories should not look like a whole one."""
        if 0 < self.mit_differenz < self.ergebnisse:
            return _("aus %(mit)s von %(alle)s Inventuren") % {
                "mit": self.mit_differenz,
                "alle": self.ergebnisse,
            }
        return ""

    @property
    def ohne_umrechnung_text(self) -> str:
        """What is left out is said, so the row does not look complete ('ohne_umrechnung')."""
        if not self.ohne_umrechnung:
            return ""
        return ngettext(
            "Ein Ergebnis lässt sich nicht in %(einheit)s umrechnen und fehlt hier.",
            "%(anzahl)s Ergebnisse lassen sich nicht in %(einheit)s umrechnen und fehlen hier.",
            self.ohne_umrechnung,
        ) % {"anzahl": self.ohne_umrechnung, "einheit": self.ware.basiseinheit.kurz}


@dataclass
class Familienblock(Familienknoten):
    """A product family with its rows, the families below and the sums.

    Every product converts into the **lead unit** for itself: within its
    dimension through its unit, across dimensions through the factor it
    carries. Without a factor the sum stays separate per dimension instead
    of adding two things that have nothing to do with each other. Such a
    family cannot be created anyway.

    The sum runs over the whole subtree, and it is computed from the
    products, not from the sums of the families below: their unit may
    differ, and pieces do not become litres, a bottle of 500 ml does.
    """

    @property
    def zeilen(self) -> list[Warenzeile]:
        """The rows of this family itself, without the families below."""
        return self.eintraege

    @property
    def alle_zeilen(self) -> list[Warenzeile]:
        return self.alle_eintraege()

    def paare(self, feld: str) -> list[Paar]:
        return [(zeile.ware, getattr(zeile, feld)) for zeile in self.alle_zeilen]

    def summen(self, feld: str) -> list[Summe]:
        return summieren(self.paare(feld))

    def gesamt(self, feld: str, zahl: Callable[[Decimal], str] = menge_kurz) -> str:
        """Everything in the lead unit: the one value under the family."""
        return familiensumme(self.familie, self.paare(feld), zahl)

    @property
    def ist_gesamt(self) -> str:
        return self.gesamt("ist")

    @property
    def differenz_gesamt(self) -> str:
        return self.gesamt("differenz", menge_mit_vorzeichen)

    @property
    def differenz_klasse(self) -> str:
        return summenklasse(self.familie, self.paare("differenz"))

    @property
    def haus_summen(self) -> list[Zelle]:
        """Only in the run: the sum of the differences per house column, in the lead unit."""
        zeilen = self.alle_zeilen
        spalten = len(zeilen[0].je_haus) if zeilen else 0
        zellen = []
        for spalte in range(spalten):
            paare: list[Paar] = [(zeile.ware, zeile.je_haus[spalte]) for zeile in zeilen]
            zellen.append(
                Zelle(
                    familiensumme(self.familie, paare, menge_mit_vorzeichen),
                    summenklasse(self.familie, paare),
                )
            )
        return zellen


#: Which families a report is restricted to: their numbers, 'None' among them
#: for the products without a family. Nothing means everything.
Familienauswahl = set[int | None] | None


def _blocke(zeilen: list[Warenzeile], familien: Familienauswahl = None) -> list[Familienblock]:
    """The rows in the tree of their families. "Without product family" goes to the end."""
    erlaubt = None if familien is None else {n for n in familien if n is not None}
    bloecke = familienbaum(
        zeilen,
        lambda zeile: zeile.ware.warenfamilie,
        knoten=Familienblock,
        erlaubt=erlaubt,
        reihenfolge=lambda zeile: zeile.ware.name,
    )
    return [block for block in bloecke if isinstance(block, Familienblock)]


def nach_familien(queryset, familien: Familienauswahl):
    """Only the results whose product lies in one of the chosen families."""
    if familien is None:
        return queryset
    bedingung = Q(hausartikel__ware__warenfamilie_id__in=[n for n in familien if n is not None])
    if None in familien:
        bedingung |= Q(hausartikel__ware__warenfamilie__isnull=True)
    return queryset.filter(bedingung)


@dataclass
class Warengruppenblock:
    """A product group on the sheet of one inventory, with its rows by article number.

    Nothing is summed here: a group mixes units that have no common lead
    unit, and the sheet is for transferring numbers, not for reading a total.
    """

    name: Beschriftung
    zeilen: list[Warenzeile] = field(default_factory=list)


def _zeilen_einer_inventur(inventur: Inventur, *, im_haus: bool = False) -> list[Warenzeile]:
    """The frozen rows of a closed inventory, one per house article.

    Within a house one carried article stands behind every product. A
    discontinued one may stand beside it with rows of its own, a duplicate
    merged during the count. The sheet lists both, each under its number,
    and the sums under the families add them. With 'im_haus' the quantities stay
    as they were frozen, in the unit the house counted in at closing
    ('Inventurergebnis.einheit'). Otherwise they are converted into the
    product's unit, which the sums under the families need.
    """
    # Ignored goods belong in no report, not even retroactively: their
    # product would be called "Not countable".
    ergebnisse = (
        Inventurergebnis.objects.filter(inventur=inventur)
        .exclude(hausartikel__status=Artikelstatus.IGNORIERT)
        .select_related(
            "einheit",
            "hausartikel__basiseinheit",
            "hausartikel__warengruppe",
            "hausartikel__ware__basiseinheit",
            "hausartikel__ware__warenfamilie__leiteinheit",
        )
        # The sum under every family needs the products' factors, see '_in_leiteinheit'.
        .prefetch_related("hausartikel__ware__umrechnungen")
    )
    zeilen = []
    for ergebnis in ergebnisse:
        artikel = ergebnis.hausartikel
        if im_haus:
            ist, soll, differenz = (
                ergebnis.gesamtmenge_basiseinheit,
                ergebnis.sollbestand,
                ergebnis.differenz,
            )
        else:
            ist = ergebnis.in_wareneinheit(ergebnis.gesamtmenge_basiseinheit)
            soll = ergebnis.in_wareneinheit(ergebnis.sollbestand)
            differenz = ergebnis.in_wareneinheit(ergebnis.differenz)
        zeilen.append(
            Warenzeile(
                ware=artikel.ware,
                ist=ist,
                soll=soll,
                differenz=differenz,
                haeuser=1,
                # On the sheet the house's number is searched for. It stands that way
                # in the inventory management system too.
                nummer=artikel.artikelnummer,
                warengruppe=str(artikel.warengruppe.anzeigename),
                name_im_haus=artikel.name_im_haus,
                vermerk=(
                    str(_("eingestellt")) if artikel.status == Artikelstatus.EINGESTELLT else ""
                ),
                notiz=ergebnis.notiz,
                hauseinheit=ergebnis.einheit if im_haus else None,
                ohne_umrechnung=int(not im_haus and ergebnis.faktor_zur_ware_wirksam is None),
            )
        )
    return zeilen


def familien_einer_inventur(inventur: Inventur) -> list[Familienblock]:
    """The frozen rows of a closed inventory, in the tree of their families."""
    return _blocke(_zeilen_einer_inventur(inventur))


def warengruppen_einer_inventur(inventur: Inventur) -> list[Warengruppenblock]:
    """The frozen rows of a closed inventory, by product group and article number.

    The sheet that is worked through in the inventory management system:
    that lists by its own groups and numbers, and whoever transfers wants
    to find every row where the system shows it. The quantities stay in the
    unit the house counted in at closing, as on the page of the inventory:
    that system books in it, and a converted number would have to be
    converted back (until 10/2026 the sheet showed the product's unit). Own
    articles without a number stand last in their group, by product name.
    """
    zeilen = sorted(
        _zeilen_einer_inventur(inventur, im_haus=True),
        key=lambda zeile: (
            zeile.warengruppe,
            zeile.nummer == "",
            nummer_sortierschluessel(zeile.nummer),
            zeile.ware.name,
        ),
    )
    bloecke: list[Warengruppenblock] = []
    for zeile in zeilen:
        if not bloecke or bloecke[-1].name != zeile.warengruppe:
            bloecke.append(Warengruppenblock(zeile.warengruppe))
        bloecke[-1].zeilen.append(zeile)
    return bloecke


def familien_ueber_inventuren(
    inventuren, *, nur_mehrere_haeuser: bool = False, familien: Familienauswahl = None
) -> tuple[list[Familienblock], bool]:
    """The differences of several inventories, summed per product.

    Returns the blocks and the information whether actual and target are
    meaningful. They are only if **one** inventory per house is in the
    selection. Otherwise stocks from different months would stand there added up.

    'familien' restricts the report to these families. A family above one
    of them only appears if it was chosen too.
    """
    inventuren = list(inventuren)
    haeuser = [inventur.haus_id for inventur in inventuren]
    bestaende_sinnvoll = len(haeuser) == len(set(haeuser))

    # Fetched per relation and not joined to every row: over several
    # inventories the same articles come again and again. Joined, every result
    # built its article, its product, the family and three units anew. For a
    # year of three houses that was eight objects per row and two thirds of
    # the page's time (measured on 2026-09-28). This way each exists once and
    # the results share it. The price is seven queries, however many rows.
    # Within one inventory every article comes once, there joining stays
    # cheaper ('_eingefrorene_zeilen').
    ergebnisse = nach_familien(
        Inventurergebnis.objects.filter(inventur__in=inventuren).exclude(
            hausartikel__status=Artikelstatus.IGNORIERT
        ),
        familien,
    ).prefetch_related(
        "einheit",
        "hausartikel__basiseinheit",
        "hausartikel__ware__basiseinheit",
        "hausartikel__ware__warenfamilie__leiteinheit",
        "hausartikel__ware__umrechnungen",
        "inventur",
    )

    je_ware: dict[int, Warenzeile] = {}
    haeuser_je_ware: dict[int, set[int]] = defaultdict(set)
    # Counted per inventory, not per result: a discontinued article of the
    # product can stand beside the carried one in the same inventory.
    inventuren_je_ware: dict[int, set[int]] = defaultdict(set)
    mit_differenz_je_ware: dict[int, set[int]] = defaultdict(set)
    for ergebnis in ergebnisse:
        artikel = ergebnis.hausartikel
        ware = artikel.ware
        zeile = je_ware.setdefault(ware.pk, Warenzeile(ware=ware))
        # Summed over several inventories. What had no target contributes nothing.
        # If none had a target, the difference stays empty instead of zero.
        inventuren_je_ware[ware.pk].add(ergebnis.inventur_id)
        haeuser_je_ware[ware.pk].add(ergebnis.inventur.haus_id)
        if ergebnis.faktor_zur_ware_wirksam is None:
            zeile.ohne_umrechnung += 1
            continue
        if (differenz := ergebnis.in_wareneinheit(ergebnis.differenz)) is not None:
            mit_differenz_je_ware[ware.pk].add(ergebnis.inventur_id)
        if bestaende_sinnvoll:
            zeile.hinzufuegen(
                ist=ergebnis.in_wareneinheit(ergebnis.gesamtmenge_basiseinheit),
                soll=ergebnis.in_wareneinheit(ergebnis.sollbestand),
                differenz=differenz,
            )
        else:
            zeile.hinzufuegen(differenz=differenz)

    zeilen = []
    for nummer, zeile in je_ware.items():
        zeile.haeuser = len(haeuser_je_ware[nummer])
        zeile.ergebnisse = len(inventuren_je_ware[nummer])
        zeile.mit_differenz = len(mit_differenz_je_ware[nummer])
        if nur_mehrere_haeuser and zeile.haeuser < IN_MEHREREN_HAEUSERN:
            continue
        zeilen.append(zeile)
    return _blocke(zeilen, familien), bestaende_sinnvoll


def familien_eines_laufs(inventuren: list[Inventur]) -> list[Familienblock]:
    """The houses of a run side by side: per product the sum and the difference per house.

    Unlike 'familien_ueber_inventuren' also during the count. Computed via
    'ergebniszeilen', which delivers the frozen or the running state per
    inventory. That way the overview is right even when only part of the
    houses is closed. Actual and target are always a statement here: a run has
    exactly one inventory per house.

    Listed for a running inventory is only what closing would freeze: counted,
    with target, with a note or with a difference by hand. The rows without
    any entry are shown by the house's check list. Here they would only bloat.

    The columns per house are there because opposite differences cancel out in
    the sum. But exactly those are the hint at a movement of goods.
    """
    spalten = len(inventuren)
    je_ware: dict[int, Warenzeile] = {}
    for index, inventur in enumerate(inventuren):
        for ergebnis in ergebniszeilen(inventur):
            artikel = ergebnis.hausartikel
            if artikel.status == Artikelstatus.IGNORIERT:
                continue
            if not (
                ergebnis.gezaehlt
                or ergebnis.sollbestand is not None
                or ergebnis.differenz is not None
                or ergebnis.notiz
            ):
                continue
            ware = artikel.ware
            zeile = je_ware.get(ware.pk)
            if zeile is None:
                zeile = je_ware[ware.pk] = Warenzeile(
                    ware=ware, je_haus=[None] * spalten, im_haus=[False] * spalten
                )
            # Per house, not per article: a discontinued article of the product
            # can stand beside the carried one with rows of its own, a duplicate
            # merged during the count. Its house column adds both (until 10/2026
            # the second overwrote the first).
            if not zeile.im_haus[index]:
                zeile.im_haus[index] = True
                zeile.ergebnisse += 1
                zeile.haeuser += 1
            # The house's row is in its unit, a closed one in the unit it was
            # frozen in. Summing happens in the product's unit, because that is
            # the one across all houses. A difference by hand as closing will
            # freeze it, not as it was typed ('differenz_gerundet').
            if ergebnis.faktor_zur_ware_wirksam is None:
                zeile.ohne_umrechnung += 1
                continue
            differenz = ergebnis.in_wareneinheit(ergebnis.differenz_gerundet)
            zeile.hinzufuegen(
                ist=ergebnis.in_wareneinheit(ergebnis.gesamtmenge_basiseinheit),
                soll=ergebnis.in_wareneinheit(ergebnis.sollbestand),
                differenz=differenz,
            )
            if differenz is not None:
                bisher = zeile.je_haus[index]
                if bisher is None:
                    zeile.mit_differenz += 1
                    zeile.je_haus[index] = differenz
                else:
                    zeile.je_haus[index] = bisher + differenz
    return _blocke(list(je_ware.values()))


def abgeschlossene(haeuser=None, von=None, bis=None):
    """The inventories that are reported over, always only closed ones.

    With their types: the page names every inventory, and 'Inventur.__str__'
    reads the types. Without the prefetch that was one query per inventory,
    linear with the months reported over (measured 2026-10-09: 38 of 56).
    """
    queryset = (
        Inventur.objects.filter(status=Inventurstatus.ABGESCHLOSSEN)
        .select_related("haus")
        .prefetch_related(*Inventur.NAME_BRAUCHT)
    )
    if haeuser:
        queryset = queryset.filter(haus__in=haeuser)
    if von:
        queryset = queryset.filter(stichtag__gte=von)
    if bis:
        queryset = queryset.filter(stichtag__lte=bis)
    return queryset.order_by("stichtag", "haus__name")
