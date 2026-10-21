"""What still has to be clarified before closing, and the closing with the answers.

While counting it shows where the master data no longer matches the shelf.
The count positions vanish with the closing, and with them the information
about that. That is why, between "really close?" and freezing, one question
round happens, in seven blocks:

1. **Inactive or discontinued, but counted**: reactivate?
2. **Counted unexpectedly**: add to the location's counting list?
3. **Expected, but nothing entered**: take the pack out of the counting list?
   Plus the scale where weighing was expected and nothing was weighed, as
   long as the article also has a pack there.
4. **Not counted at all**, without "Target counts": set inactive,
   discontinue, or switch it into another type?
5. **Counted, but without target** (only if a target stock exists at all,
   from the file or by hand): set the target to 0, enter an own value or
   leave it as it is?
6. **Counted, but in no type of this inventory**: take it into one of them?
7. **Weighed in a foreign container**: assign to the article or the house?

In the first block are **inactive** articles (including those counted only
through a recipe article whose ingredient they are) and **discontinued**
ones someone found on the shelf and counted. An inactive one still holds
number and product, reactivating it always works. For the discontinued one
another row may have taken the slot by now: then it stands there with the
reason and cannot be ticked. Its quantity goes into the result nonetheless.
A discontinued one is not offered for the counting list. It is only
expected again once it is active, and then the next inventory asks about it.

Without an answer nothing changes: every change to the master data is a
tick someone sets. If there is nothing to ask, the step is skipped.

**Frozen first, then applied**, both in one transaction. If an inactive
article became active again before freezing, shares from recipe article
recipes would suddenly land on it, and the result would deviate from what
was visible in the check list.

**The exception is the fifth block**: a target set by hand *is* part of the
result and therefore has to be in place before freezing. It changes no
master data, only the row of this inventory. Why the question at all: if an
article is not in the file of the inventory management system, it carries
it with stock 0 or not at all. Without a target the row would freeze
without a difference, and a find of twelve bottles would look like "fine".
Asked only if a target stock exists, from the file or by hand. Otherwise no
article has a target, and the question would be the same for every one.

The sixth block is the counterpart of the switch in the fourth: an article
of another type gets no row in this inventory but can be added in the app.
Whoever does that evidently has it in view after all. Then the question
suggests itself whether its type is still right. Asked only about articles
that were counted **themselves**: a collection cup that rides along as an
ingredient of a monthly counted portion is therefore not yet monthly goods,
and the question would otherwise reappear every month.

**A switch of the type asks for whom it applies**, as on the article's page:
this article only, or every article of its product, in every house. Asked
only where the product has further articles.

The seventh block comes from the selection of the counting app: it offers
first the containers of the article, then those of the house, finally those
of **other houses**: the bowl one borrowed. Whoever chooses from the last
group has shown that the assignment is wrong: the container evidently
belongs to this article or also stands in this house. An answer moves it to
the front next time. "Without container" belongs everywhere and never
appears here.

**Asked is by the state at submission**, not by that of the page: the
inventory runs until the last moment, and the app may have submitted
something in the meantime. An answer to a question that has resolved itself
is skipped.

**In the run the first answer for a whole product applies.** If another
house of the same run switched a product into a type for all its articles,
that stands on the question ('festgelegt'), and another switch for the
whole product is locked. Otherwise the second house would silently reverse
the first one's answer. For its own article the house still decides. This is
recorded in 'Artentscheidung'.
"""

from katalog.models import GANZE_WARE, NUR_DIESER

from .decisions import (
    DEM_ARTIKEL,
    DEM_HAUS,
    EINSTELLEN,
    INAKTIV_SETZEN,
    NEUE_BEMERKUNGEN,
    NEUE_WERTE,
    SOLL_EIGENER_WERT,
    SOLL_NULL,
    WECHSEL_PRAEFIX,
    Abschlussbericht,
    Entscheidungen,
    NeueBemerkungen,
    NeueWerte,
    abschliessen,
)
from .questions import (
    Artwechsel,
    FremderBehaelter,
    GezaehltAusserhalb,
    GezaehltOhneSoll,
    Klaerung,
    NeueErwartung,
    UngezaehlterArtikel,
    UngezaehltesGebinde,
    Wiederaufnahme,
    festgelegte_arten,
    klaeren,
)

__all__ = [
    "DEM_ARTIKEL",
    "DEM_HAUS",
    "EINSTELLEN",
    "GANZE_WARE",
    "INAKTIV_SETZEN",
    "NEUE_BEMERKUNGEN",
    "NEUE_WERTE",
    "NUR_DIESER",
    "SOLL_EIGENER_WERT",
    "SOLL_NULL",
    "WECHSEL_PRAEFIX",
    "Abschlussbericht",
    "Artwechsel",
    "Entscheidungen",
    "FremderBehaelter",
    "GezaehltAusserhalb",
    "GezaehltOhneSoll",
    "Klaerung",
    "NeueErwartung",
    "NeueBemerkungen",
    "NeueWerte",
    "UngezaehlterArtikel",
    "UngezaehltesGebinde",
    "Wiederaufnahme",
    "abschliessen",
    "festgelegte_arten",
    "klaeren",
]
