"""The houses of the demo stock including articles and counting lists.

Two houses are populated, one is empty. The empty one is on purpose: it can
be used to show how a house is set up, from the import through the counting
list to the first inventory.

The articles stand as a table, the counting lists as a rule. Which products
stand at a location is said by their family, because that is how shelves are
stocked too: in the beverage storage the crates, at the counter the single
bottles.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone

from katalog.formats import STANDARD
from katalog.models import (
    QUELLE_EIGEN,
    Artikelstatus,
    Basiseinheit,
    Erwartung,
    ErwartungGebinde,
    Hausartikel,
    Inventurart,
    Rezepturartikel,
    Ware,
    Warengruppe,
)
from organisation.models import Bereich, Haus, Ort

from .catalog import ART_JE_WARE, MONATSINVENTUR
from .names_en import Namen
from .saving import angelegt

#: The same cup twice in Kino Nord, as two products: (stays, is merged in).
DUBLETTE = ("Becher 0,4 l", "Trinkbecher 0,4 l")

NORD = "Kino Nord"
OST = "Kino Ost"
WEST = "Kino West"
#: A house without an inventory management system: it supplies the cinemas
#: with ice cream and carries only own articles, without a number. Its target
#: is maintained by hand.
SUED = "Eismanufaktur Süd"

AKTIV = Artikelstatus.AKTIV
EIGEN = QUELLE_EIGEN
INAKTIV = Artikelstatus.INAKTIV
EINGESTELLT = Artikelstatus.EINGESTELLT

# Product groups come from the inventory management system and are cut
# differently per house. The two houses of the demo carry the same beverages
# and confectionery in "Zentral" and "Lokal". Exactly for that the product
# family exists.
GETRAENKE_ZENTRAL = "201 VK - Getränke - Zentral"
GETRAENKE_LOKAL = "202 VK - Getränke - Lokal"
POPCORN = "210 VK - Popcorn"
NACHOS = "211 VK - Nachos"
SUESSWAREN_ZENTRAL = "220 VK - Süßwaren - Zentral"
SUESSWAREN_LOKAL = "221 VK - Süßwaren - Lokal"
EIS = "230 VK - Eis"
HEISSGETRAENKE = "240 VK - Heißgetränke"
VERPACKUNG = "250 EK - Verpackung"
HERZHAFTES = "260 VK - Herzhaftes"
SONSTIGES = "290 EK - Sonstiges"
PFAND = "299 - Pfand und Gutscheine"
#: The product groups of the workshop. Without an inventory management system
#: there are no numbers and no default there, they are named freely.
FERTIGWARE = "Fertigware"
ROHSTOFFE = "Rohstoffe"


@dataclass(frozen=True)
class Artikelbauplan:
    """An article as a house carries it."""

    nummer: str
    ware: str
    warengruppe: str
    #: Deviating designation in the house. Empty means: it is called like the product.
    bezeichnung: str = ""
    status: str = AKTIV
    #: The unit the house counts in, where it deviates from the product
    #: (name of the base unit). Empty means: like the product.
    einheit: str = ""
    #: Only with a different dimension than the product: how many units of the
    #: product one unit of the house is.
    faktor: str = ""
    #: The system that carries the article. "eigen" has no number.
    quelle: str = STANDARD
    #: The inventory type, where the house counts the product differently from
    #: the others. Empty means: as the product's blueprint says
    #: ('catalog.Warenbauplan.art').
    art: str = ""


@dataclass(frozen=True)
class Ortsbauplan:
    """A location and what is expected there."""

    bereich: str
    name: str
    #: Products of these families stand here.
    familien: tuple[str, ...] = ()
    #: Only these packs are expected here. Empty means: all of the product.
    gebinde: tuple[str, ...] = ()
    #: Whether weighable goods go on the scale at this location.
    wiegen: bool = False
    #: Individual products that additionally stand here.
    zusaetzlich: tuple[str, ...] = ()
    #: Products that have no business here despite their family.
    ohne: tuple[str, ...] = ()
    #: Whether a refill list is maintained for this location. Only then do its
    #: packs carry a suggestion, and even there not every one. In storage stands
    #: what was delivered, no target image of the shelf.
    auffuellliste: bool = False
    #: Prepared portions that are counted at this location.
    rezepturartikel: tuple[str, ...] = ()


#: Usual refill stock per pack, as a suggestion when counting. Only for the
#: packs that are really refilled. A crate or a sack stands in storage, and
#: how many of them stand there no list predicts.
STANDARDMENGEN: dict[str, str] = {
    "Einzeln": "24",
    "Karton": "2",
    "Glas": "2",
    "Beutel": "4",
}


# --------------------------------------------------------------------------- #
# Kino Nord
# --------------------------------------------------------------------------- #

#: Articles of Kino Nord that leave the range only **after** the running
#: count ('inventories.spaeter_ausscheiden'). The app accepts a row only while
#: the article is carried. These three were counted as active, then one was
#: set inactive and two discontinued: that is how counted rows of non-active
#: articles arise. They are built active and take the status of their row
#: below afterwards. Everything else that is not active left half a year ago
#: ('ausgeschieden_am').
SPAETER_AUSGESCHIEDEN = ("101205", "101307", "101410")

#: Of those, the ones on no counting list: carried, counted through the
#: app's search, delisted afterwards. The gum is such a case.
NIE_GELISTET = ("101307",)

#: Number -> the unit the house counted in until the last closed inventory.
#: Kino Ost counted the coffee beans in kg like the product and changed to
#: bags only before the running run ('inventories.einheiten_umstellen'). The
#: closed results keep the kg they were frozen in. A unit of the product's
#: dimension, so the row needs no factor until then.
SPAETER_UMGESTELLT = {"102501": "kg"}

#: New number -> old number: the house's system reassigned the cones after
#: the running count. The old row is built under the product's name, stands
#: on the list and is counted in every run. Afterwards it is discontinued
#: ('SPAETER_AUSGESCHIEDEN'), takes the designation of its row below, and the
#: new row arises with its places on the lists ('nummer_neu_vergeben').
NEU_VERGEBEN = {"101404": "101410"}

ARTIKEL_NORD: list[Artikelbauplan] = [
    # -- Beverages ----------------------------------------------------------
    Artikelbauplan("101001", "Cola 0,5 l", GETRAENKE_ZENTRAL),
    Artikelbauplan("101002", "Cola zuckerfrei 0,5 l", GETRAENKE_ZENTRAL),
    Artikelbauplan("101003", "Orangenlimonade 0,5 l", GETRAENKE_ZENTRAL),
    Artikelbauplan("101004", "Zitronenlimonade 0,5 l", GETRAENKE_ZENTRAL),
    Artikelbauplan("101005", "Bitterlimonade 0,2 l", GETRAENKE_ZENTRAL, status=EINGESTELLT),
    Artikelbauplan("101006", "Colasirup", GETRAENKE_ZENTRAL, "Sirup Cola 10 l"),
    Artikelbauplan("101007", "Orangensirup", GETRAENKE_ZENTRAL, "Sirup Orange 10 l"),
    # The third syrup of the house lemonade. Without it the recipe article
    # could not be resolved here, and its row on the list would be dead weight.
    Artikelbauplan("101020", "Zitronensirup", GETRAENKE_ZENTRAL, "Sirup Zitrone 10 l"),
    Artikelbauplan("101008", "Mineralwasser 0,25 l", GETRAENKE_ZENTRAL),
    Artikelbauplan("101009", "Mineralwasser 0,5 l", GETRAENKE_ZENTRAL),
    Artikelbauplan("101010", "Apfelsaft 0,2 l", GETRAENKE_ZENTRAL),
    Artikelbauplan("101011", "Orangensaft 0,2 l", GETRAENKE_ZENTRAL),
    Artikelbauplan("101012", "Apfelschorle 0,5 l", GETRAENKE_ZENTRAL),
    Artikelbauplan("101013", "Eistee Pfirsich 0,5 l", GETRAENKE_ZENTRAL, status=INAKTIV),
    Artikelbauplan("101014", "Bier 0,33 l", GETRAENKE_ZENTRAL),
    Artikelbauplan("101015", "Bier alkoholfrei 0,33 l", GETRAENKE_ZENTRAL),
    Artikelbauplan("101016", "Weißwein 0,2 l", GETRAENKE_ZENTRAL),
    Artikelbauplan("101017", "Rotwein 0,2 l", GETRAENKE_ZENTRAL),
    Artikelbauplan("101018", "Sekt 0,2 l", GETRAENKE_ZENTRAL),
    # Discontinued, and the inventory management system still lists it, with a
    # stock of 0. The target stock file names it that way. Nobody is asked
    # about it: the 0 is stored silently.
    Artikelbauplan("101019", "Tomatensaft 0,2 l", GETRAENKE_ZENTRAL, status=EINGESTELLT),
    # -- Popcorn ------------------------------------------------------------
    Artikelbauplan("101101", "Puffmais", POPCORN, "Mais für Popcorn"),
    Artikelbauplan("101102", "Popcornsalz", POPCORN),
    Artikelbauplan("101103", "Popcornzucker", POPCORN),
    Artikelbauplan("101104", "Kokosfett", POPCORN),
    Artikelbauplan("101105", "Karamellmasse", POPCORN),
    # -- Nachos -------------------------------------------------------------
    Artikelbauplan("101201", "Nachochips", NACHOS),
    Artikelbauplan("101202", "Käsesauce", NACHOS),
    Artikelbauplan("101203", "Salsasauce", NACHOS),
    Artikelbauplan("101204", "Jalapenos", NACHOS),
    Artikelbauplan("101205", "Guacamole", NACHOS, status=INAKTIV),
    # -- Confectionery ------------------------------------------------------
    Artikelbauplan("101301", "Fruchtgummi lose", SUESSWAREN_ZENTRAL),
    Artikelbauplan("101302", "Schokolinsen lose", SUESSWAREN_ZENTRAL),
    Artikelbauplan("101303", "Lakritz lose", SUESSWAREN_ZENTRAL),
    Artikelbauplan("101304", "Saure Schnüre lose", SUESSWAREN_ZENTRAL),
    Artikelbauplan("101305", "Schokoriegel", SUESSWAREN_ZENTRAL),
    Artikelbauplan("101306", "Keksriegel", SUESSWAREN_ZENTRAL),
    Artikelbauplan("101307", "Kaugummi", SUESSWAREN_ZENTRAL, status=EINGESTELLT),
    Artikelbauplan("101308", "Salzbrezeln lose", SUESSWAREN_ZENTRAL),
    # -- Ice cream ----------------------------------------------------------
    Artikelbauplan("101401", "Eis Vanille", EIS),
    Artikelbauplan("101402", "Eis Schokolade", EIS),
    Artikelbauplan("101403", "Eis Erdbeer", EIS),
    # The cones' new number. It arises only after the running count
    # ('NEU_VERGEBEN'). Until then the house counts them as 101410.
    Artikelbauplan("101404", "Eishörnchen", EIS),
    Artikelbauplan("101405", "Eissandwich", EIS),
    Artikelbauplan("101406", "Eisriegel", EIS),
    # The cones under their old number, discontinued once 101404 takes over.
    # The target file still names the old number: in the prepared run the
    # clarification offers to add its stock to the new row. At closing the
    # tick is locked, and the row says why.
    Artikelbauplan("101410", "Eishörnchen", EIS, "Eishörnchen alt", EINGESTELLT),
    # -- Hot drinks ---------------------------------------------------------
    Artikelbauplan("101501", "Kaffeebohnen", HEISSGETRAENKE),
    Artikelbauplan("101502", "Kakaopulver", HEISSGETRAENKE),
    Artikelbauplan("101503", "Teebeutel", HEISSGETRAENKE),
    Artikelbauplan("101504", "Milchpulver", HEISSGETRAENKE),
    Artikelbauplan("101505", "Zuckersticks", HEISSGETRAENKE),
    # -- Packaging ----------------------------------------------------------
    Artikelbauplan("101601", "Popcorntüte klein", VERPACKUNG),
    Artikelbauplan("101602", "Popcorntüte mittel", VERPACKUNG),
    Artikelbauplan("101603", "Popcorntüte groß", VERPACKUNG),
    Artikelbauplan("101604", "Popcorneimer", VERPACKUNG),
    Artikelbauplan("101605", "Nachoschale", VERPACKUNG),
    Artikelbauplan("101606", "Becher 0,3 l", VERPACKUNG),
    Artikelbauplan("101607", "Becher 0,4 l", VERPACKUNG),
    Artikelbauplan("101608", "Becher 0,5 l", VERPACKUNG),
    Artikelbauplan("101609", "Deckel klein", VERPACKUNG),
    Artikelbauplan("101610", "Deckel groß", VERPACKUNG),
    Artikelbauplan("101611", "Trinkhalm", VERPACKUNG),
    Artikelbauplan("101612", "Serviette", VERPACKUNG),
    # A duplicate: the cup of 101607 once more ('DUBLETTE').
    Artikelbauplan("101613", "Trinkbecher 0,4 l", VERPACKUNG),
    # Only Kino Nord carries reusable cups. They are counted in the cup
    # inventory, and that can therefore be counted on its own only here.
    Artikelbauplan("101614", "Mehrwegbecher 0,3 l", VERPACKUNG),
    Artikelbauplan("101615", "Mehrwegbecher 0,5 l", VERPACKUNG),
    # -- Savoury ------------------------------------------------------------
    Artikelbauplan("101701", "Würstchen", HERZHAFTES),
    Artikelbauplan("101702", "Hotdogbrötchen", HERZHAFTES),
    Artikelbauplan("101703", "Röstzwiebeln", HERZHAFTES),
    Artikelbauplan("101704", "Senf", HERZHAFTES),
    Artikelbauplan("101705", "Tomatenketchup", HERZHAFTES),
    # -- Miscellaneous ------------------------------------------------------
    Artikelbauplan("101801", "Sprühsahne", SONSTIGES),
    Artikelbauplan("101802", "Zuckerstreusel", SONSTIGES),
    Artikelbauplan("101803", "Eiswürfel", SONSTIGES),
    Artikelbauplan("101804", "Reinigungstücher", SONSTIGES),
    Artikelbauplan("101805", "Einweghandschuhe", SONSTIGES),
    # -- Not countable ------------------------------------------------------
    Artikelbauplan("101901", "Becherpfand", PFAND),
    Artikelbauplan("101902", "Eimerpfand", PFAND),
    Artikelbauplan("101903", "Wertgutschein", PFAND),
    Artikelbauplan("101904", "Menürabatt", PFAND),
]

ORTE_NORD: list[Ortsbauplan] = [
    Ortsbauplan(
        "Theke",
        "Theke links",
        familien=("Limonaden", "Säfte und Wasser"),
        gebinde=("Einzeln",),
        auffuellliste=True,
    ),
    Ortsbauplan(
        "Theke",
        "Theke rechts",
        familien=("Verpackung",),
        gebinde=("Karton",),
        rezepturartikel=("Nachos mit Käse", "Kaffee im Becher"),
        auffuellliste=True,
    ),
    Ortsbauplan(
        "Theke",
        "Popcornmaschine",
        familien=("Popcorn",),
        gebinde=("Sack", "Karton", "Eimer"),
        rezepturartikel=("Popcorn süß lose", "Popcorn Tüte groß"),
    ),
    Ortsbauplan(
        "Theke",
        "Süßwarenregal",
        familien=("Süßwaren", "Knabbereien"),
        gebinde=("Einzeln", "Karton"),
        wiegen=True,
        zusaetzlich=("Zuckerstreusel",),
        rezepturartikel=("Süßwarenmischung",),
        auffuellliste=True,
    ),
    # The reusable cups come back here in their boxes. In an inventory with
    # the monthly and the cup inventory the app shows two tabs here.
    Ortsbauplan(
        "Lager",
        "Getränkelager",
        familien=("Limonaden", "Säfte und Wasser", "Bier und Wein", "Mehrwegbecher"),
        gebinde=("Kasten", "Kanister", "Karton"),
        rezepturartikel=("Sirupmischung Hauslimo",),
    ),
    Ortsbauplan(
        "Lager",
        "Trockenlager",
        familien=("Nachos", "Heißgetränke", "Verpackung"),
        gebinde=("Karton", "Eimer"),
        zusaetzlich=("Reinigungstücher", "Einweghandschuhe"),
    ),
    # The hot dog is assembled here: its four ingredients stand in this fridge.
    # Until 10/2026 it stood on no list and was counted nowhere.
    Ortsbauplan(
        "Kühlung",
        "Kühlschrank Theke",
        familien=("Herzhaftes",),
        gebinde=("Glas", "Beutel", "Kanister", "Eimer"),
        wiegen=True,
        zusaetzlich=("Sprühsahne", "Käsesauce"),
        rezepturartikel=("Hotdog",),
    ),
    # The ice cubes come in bags. Until 10/2026 the bag was missing here, and
    # they stood on no list although the blueprint names them.
    Ortsbauplan(
        "Kühlung",
        "Tiefkühltruhe",
        familien=("Speiseeis",),
        gebinde=("Einzeln", "Karton", "Beutel"),
        zusaetzlich=("Eiswürfel",),
    ),
    # Nothing is expected here: what stands on the table changes with the
    # film, and nobody keeps a counting list for it. The counting app shows
    # the location anyway. What is found there is added through its search.
    Ortsbauplan("Foyer", "Aktionstisch"),
]


# --------------------------------------------------------------------------- #
# Kino Ost
# --------------------------------------------------------------------------- #

ARTIKEL_OST: list[Artikelbauplan] = [
    # -- Beverages ----------------------------------------------------------
    Artikelbauplan("102001", "Cola 0,5 l", GETRAENKE_LOKAL, "Cola Flasche 0,5"),
    Artikelbauplan(
        "102002", "Cola zuckerfrei 0,5 l", GETRAENKE_LOKAL, "Cola zuckerfrei Flasche 0,5"
    ),
    Artikelbauplan("102003", "Orangenlimonade 0,5 l", GETRAENKE_LOKAL, "Orange Flasche 0,5"),
    Artikelbauplan("102004", "Zitronenlimonade 0,5 l", GETRAENKE_LOKAL, "Zitrone Flasche 0,5"),
    Artikelbauplan("102006", "Colasirup", GETRAENKE_LOKAL, "Postmix Cola"),
    Artikelbauplan("102007", "Zitronensirup", GETRAENKE_LOKAL, "Postmix Zitrone"),
    Artikelbauplan("102008", "Mineralwasser 0,25 l", GETRAENKE_LOKAL),
    Artikelbauplan("102010", "Apfelsaft 0,2 l", GETRAENKE_LOKAL),
    Artikelbauplan("102011", "Orangensaft 0,2 l", GETRAENKE_LOKAL),
    Artikelbauplan("102012", "Apfelschorle 0,5 l", GETRAENKE_LOKAL),
    Artikelbauplan("102013", "Eistee Pfirsich 0,5 l", GETRAENKE_LOKAL),
    Artikelbauplan("102014", "Bier 0,33 l", GETRAENKE_LOKAL),
    Artikelbauplan("102015", "Bier alkoholfrei 0,33 l", GETRAENKE_LOKAL, status=INAKTIV),
    # Kino Ost counts the white wine every month, Kino Nord only once a year.
    # On the product's page it therefore stands as mixed.
    Artikelbauplan("102016", "Weißwein 0,2 l", GETRAENKE_LOKAL, art=MONATSINVENTUR),
    Artikelbauplan("102017", "Rotwein 0,2 l", GETRAENKE_LOKAL),
    Artikelbauplan("102019", "Tomatensaft 0,2 l", GETRAENKE_LOKAL, status=EINGESTELLT),
    # -- Popcorn ------------------------------------------------------------
    Artikelbauplan("102101", "Puffmais", POPCORN, "Popcornmais"),
    Artikelbauplan("102102", "Popcornsalz", POPCORN),
    Artikelbauplan("102103", "Popcornzucker", POPCORN),
    Artikelbauplan("102104", "Kokosfett", POPCORN),
    # -- Nachos -------------------------------------------------------------
    # Two houses, two units: Kino Ost counts the chips in grams, the product
    # is in kg. The units work that out among themselves.
    Artikelbauplan("102201", "Nachochips", NACHOS, einheit="g"),
    Artikelbauplan("102202", "Käsesauce", NACHOS),
    Artikelbauplan("102203", "Salsasauce", NACHOS),
    Artikelbauplan("102204", "Jalapenos", NACHOS),
    # -- Confectionery ------------------------------------------------------
    Artikelbauplan("102301", "Fruchtgummi lose", SUESSWAREN_LOKAL),
    Artikelbauplan("102302", "Schokolinsen lose", SUESSWAREN_LOKAL),
    Artikelbauplan("102303", "Lakritz lose", SUESSWAREN_LOKAL),
    Artikelbauplan("102305", "Schokoriegel", SUESSWAREN_LOKAL),
    Artikelbauplan("102306", "Keksriegel", SUESSWAREN_LOKAL),
    Artikelbauplan("102308", "Salzbrezeln lose", SUESSWAREN_LOKAL),
    # -- Ice cream ----------------------------------------------------------
    Artikelbauplan("102401", "Eis Vanille", EIS),
    Artikelbauplan("102402", "Eis Schokolade", EIS),
    Artikelbauplan("102404", "Eishörnchen", EIS),
    Artikelbauplan("102405", "Eissandwich", EIS),
    Artikelbauplan("102406", "Eisriegel", EIS),
    # -- Hot drinks ---------------------------------------------------------
    # And the beans in bags, while the product is carried in kg. A bag
    # weighs one kilo: that is the house's conversion to the product.
    Artikelbauplan("102501", "Kaffeebohnen", HEISSGETRAENKE, einheit="Stück(e)", faktor="1"),
    Artikelbauplan("102502", "Kakaopulver", HEISSGETRAENKE),
    Artikelbauplan("102503", "Teebeutel", HEISSGETRAENKE),
    Artikelbauplan("102505", "Zuckersticks", HEISSGETRAENKE),
    # -- Packaging ----------------------------------------------------------
    Artikelbauplan("102601", "Popcorntüte klein", VERPACKUNG),
    Artikelbauplan("102602", "Popcorntüte mittel", VERPACKUNG),
    Artikelbauplan("102603", "Popcorntüte groß", VERPACKUNG),
    Artikelbauplan("102605", "Nachoschale", VERPACKUNG),
    Artikelbauplan("102606", "Becher 0,3 l", VERPACKUNG),
    Artikelbauplan("102607", "Becher 0,4 l", VERPACKUNG),
    Artikelbauplan("102609", "Deckel klein", VERPACKUNG),
    Artikelbauplan("102610", "Deckel groß", VERPACKUNG),
    Artikelbauplan("102611", "Trinkhalm", VERPACKUNG),
    Artikelbauplan("102612", "Serviette", VERPACKUNG),
    # -- Savoury ------------------------------------------------------------
    Artikelbauplan("102701", "Würstchen", HERZHAFTES),
    Artikelbauplan("102702", "Hotdogbrötchen", HERZHAFTES),
    Artikelbauplan("102704", "Senf", HERZHAFTES),
    Artikelbauplan("102705", "Tomatenketchup", HERZHAFTES),
    # -- Miscellaneous ------------------------------------------------------
    Artikelbauplan("102801", "Sprühsahne", SONSTIGES),
    Artikelbauplan("102803", "Eiswürfel", SONSTIGES),
    Artikelbauplan("102805", "Einweghandschuhe", SONSTIGES),
    # -- Not countable ------------------------------------------------------
    Artikelbauplan("102901", "Becherpfand", PFAND),
    Artikelbauplan("102903", "Wertgutschein", PFAND),
]

ORTE_OST: list[Ortsbauplan] = [
    Ortsbauplan(
        "Theke",
        "Theke",
        familien=("Limonaden", "Säfte und Wasser", "Süßwaren", "Knabbereien"),
        gebinde=("Einzeln",),
        wiegen=True,
        auffuellliste=True,
    ),
    Ortsbauplan(
        "Theke",
        "Eistruhe",
        familien=("Speiseeis",),
        gebinde=("Einzeln",),
    ),
    Ortsbauplan(
        "Theke",
        "Nachostation",
        familien=("Nachos", "Popcorn"),
        gebinde=("Karton", "Eimer", "Sack"),
        wiegen=True,
        rezepturartikel=("Nachos mit Käse", "Popcorn Tüte groß"),
    ),
    Ortsbauplan(
        "Lager",
        "Lager Getränke",
        familien=("Limonaden", "Säfte und Wasser", "Bier und Wein"),
        gebinde=("Kasten", "Kanister", "Karton"),
    ),
    Ortsbauplan(
        "Lager",
        "Lager Ware",
        familien=("Verpackung", "Heißgetränke", "Herzhaftes"),
        gebinde=("Karton", "Glas", "Beutel", "Kanister"),
        zusaetzlich=("Sprühsahne", "Einweghandschuhe"),
    ),
]


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Eismanufaktur Süd: own articles without a number
# --------------------------------------------------------------------------- #

#: The ice cream is the same product as in the cinemas, there in pieces, here
#: in kilos: 1 kg is 9.09 portions of 110 g. Only the workshop knows the ingredients.
ARTIKEL_SUED: list[Artikelbauplan] = [
    Artikelbauplan("", "Eis Vanille", FERTIGWARE, einheit="kg", faktor="9.0909", quelle=EIGEN),
    Artikelbauplan("", "Eis Schokolade", FERTIGWARE, einheit="kg", faktor="9.0909", quelle=EIGEN),
    Artikelbauplan("", "Sahne", ROHSTOFFE, quelle=EIGEN),
    Artikelbauplan("", "Zucker", ROHSTOFFE, quelle=EIGEN),
    Artikelbauplan("", "Waffelbruch", ROHSTOFFE, quelle=EIGEN),
]

ORTE_SUED: list[Ortsbauplan] = [
    Ortsbauplan("Produktion", "Tiefkühlraum", familien=("Speiseeis",)),
    Ortsbauplan("Produktion", "Rohstofflager", familien=("Zutaten",)),
]


def haeuser_anlegen(namen: Namen) -> dict[str, Haus]:
    """Four houses. Kino West stays empty and is set up in the demo itself.

    The key stays the German name. The whole build looks it up under that,
    and it stands as a constant in several modules.
    """
    return {name: angelegt(Haus(name=namen(name))) for name in (NORD, OST, WEST, SUED)}


def warengruppen_anlegen(namen: Namen) -> dict[str, Warengruppe]:
    gruppen = {bauplan.warengruppe for bauplan in ARTIKEL_NORD + ARTIKEL_OST + ARTIKEL_SUED}
    return {name: angelegt(Warengruppe(name=namen(name))) for name in sorted(gruppen)}


def artikel_anlegen(
    haus: Haus,
    plaene: list[Artikelbauplan],
    waren: dict[str, Ware],
    warengruppen: dict[str, Warengruppe],
    ausgeschieden_am: datetime,
    namen: Namen,
    arten: dict[str, Inventurart],
) -> dict[str, Hausartikel]:
    """The articles of a house. Back comes the mapping of number to row.

    An own article has no number and stands under the name of its product.
    'gueltig_bis' belongs to the status and has to be set exactly when a row
    is not active. A database constraint watches over that. The inventory
    type comes from the product's blueprint unless the house's row names
    its own. What leaves the range only after the running count
    ('SPAETER_AUSGESCHIEDEN') starts out active, and a number the system
    reassigns only then ('NEU_VERGEBEN') does not exist yet. What changes its
    unit only before the running run ('SPAETER_UMGESTELLT') starts out in the
    one of the closed runs.
    """
    einheiten = {einheit.name: einheit for einheit in Basiseinheit.objects.all()}
    artikel = {}
    for bauplan in plaene:
        if bauplan.nummer in NEU_VERGEBEN:
            continue
        ware = waren[bauplan.ware]
        status = AKTIV if bauplan.nummer in SPAETER_AUSGESCHIEDEN else bauplan.status
        # The old number of a reassignment is called like the product until
        # the new one exists ('nummer_neu_vergeben').
        bezeichnung = "" if bauplan.nummer in NEU_VERGEBEN.values() else bauplan.bezeichnung
        einheit = SPAETER_UMGESTELLT.get(bauplan.nummer, bauplan.einheit)
        faktor = "" if bauplan.nummer in SPAETER_UMGESTELLT else bauplan.faktor
        satz = Hausartikel(
            haus=haus,
            name=namen(bezeichnung or bauplan.ware),
            ware=ware,
            warengruppe=warengruppen[bauplan.warengruppe],
            artikelnummer=bauplan.nummer,
            quelle=bauplan.quelle,
            status=status,
            gueltig_bis=None if status == AKTIV else ausgeschieden_am,
            basiseinheit=einheiten[einheit] if einheit else ware.basiseinheit,
            faktor_zur_ware=Decimal(faktor) if faktor else None,
            inventurart=arten[bauplan.art or ART_JE_WARE[bauplan.ware]],
        )
        # Through the model's check: a row of the blueprint with a foreign
        # dimension and without a factor should break here, not only when counting.
        satz.full_clean()
        satz.save()
        artikel[bauplan.nummer or bauplan.ware] = satz
    return artikel


def orte_anlegen(haus: Haus, plaene: list[Ortsbauplan], namen: Namen) -> dict[str, Ort]:
    """Areas and locations in the order in which counting happens."""
    bereiche: dict[str, Bereich] = {}
    orte = {}
    for bauplan in plaene:
        bereich = bereiche.get(bauplan.bereich)
        if bereich is None:
            bereich = angelegt(Bereich(haus=haus, name=namen(bauplan.bereich)))
            bereiche[bauplan.bereich] = bereich
        orte[bauplan.name] = angelegt(Ort(bereich=bereich, name=namen(bauplan.name)))
    return orte


def zaehllisten_anlegen(
    orte: dict[str, Ort],
    plaene: list[Ortsbauplan],
    artikel: dict[str, Hausartikel],
    rezepturartikel: dict[str, Rezepturartikel],
    namen: Namen,
) -> int:
    """For every location the counting list from its rule.

    Expected is what the house actively carries. A discontinued or not
    countable row belongs in no list, an inactive one does: it comes back.
    """
    gezaehlt = 0
    for bauplan in plaene:
        ort = orte[bauplan.name]
        for satz in _waren_am_ort(bauplan, artikel, namen):
            gezaehlt += _erwartung_anlegen(ort, satz, bauplan, namen)
        for name in bauplan.rezepturartikel:
            angelegt(Erwartung(ort=ort, artikel_id=rezepturartikel[name].pk))
            gezaehlt += 1
    return gezaehlt


def _waren_am_ort(
    bauplan: Ortsbauplan, artikel: dict[str, Hausartikel], namen: Namen
) -> list[Hausartikel]:
    """Which articles of this house belong at this location, in the order of the table.

    The comparison is against stored names. Those are in the language of the
    build, the blueprint next to it always in German. So the blueprint moves
    over, not the stored name back.
    """
    ohne = namen.alle(bauplan.ohne)
    familien = namen.alle(bauplan.familien)
    zusaetzlich = namen.alle(bauplan.zusaetzlich)
    passend = []
    for satz in artikel.values():
        # Carried, but on no list ('NIE_GELISTET'): counted through the app's search.
        if satz.artikelnummer in NIE_GELISTET:
            continue
        if satz.status in (EINGESTELLT, Artikelstatus.IGNORIERT):
            continue
        ware = satz.ware
        if ware.name in ohne:
            continue
        familie = ware.warenfamilie.name if ware.warenfamilie_id else ""
        if familie in familien or ware.name in zusaetzlich:
            passend.append(satz)
    return passend


def _erwartung_anlegen(ort: Ort, satz: Hausartikel, bauplan: Ortsbauplan, namen: Namen) -> int:
    """A row of the counting list including the packs expected here.

    An expectation without packs and without a scale would yield not a single
    count position when creating the inventory. It therefore does not arise at all.
    """
    ware = satz.ware
    gewuenscht = namen.alle(bauplan.gebinde)
    mengen = {namen(name): menge for name, menge in STANDARDMENGEN.items()}
    vorgaben = [
        gebinde
        for gebinde in ware.gebinde.all()
        if not bauplan.gebinde or gebinde.name in gewuenscht
    ]
    wiegen = ware.abwiegbar and bauplan.wiegen
    if not vorgaben and not wiegen:
        return 0
    erwartung = angelegt(Erwartung(ort=ort, artikel_id=satz.pk, wiegen_erwartet=wiegen))
    for gebinde in vorgaben:
        menge = mengen.get(gebinde.name) if bauplan.auffuellliste else None
        angelegt(
            ErwartungGebinde(
                erwartung=erwartung,
                gebinde=gebinde,
                standard_menge=Decimal(menge) if menge else None,
            )
        )
    return 1


def nummer_neu_vergeben(haus: Haus, namen: Namen, arten: dict[str, Inventurart]) -> int:
    """Kino Nord's system reassigns a number: the old row gives way, the new one takes its places.

    The old row is already discontinued ('inventories.spaeter_ausscheiden'):
    two carried rows of one product in one house are not allowed, so the new
    one can arise only now. It is built the way 'artikel_anlegen' builds a
    row and gets the list places the old one had, from the same rule. The old
    row takes the designation of its table row. That it counts as the new one
    ('zaehlt_als') is left to the clarification of the prepared run, where the
    target file still names the old number ('sollzeile_dazurechnen'). Until
    10/2026 it was set here, past every page. Returns how many rows arose.
    """
    einheiten = {einheit.name: einheit for einheit in Basiseinheit.objects.all()}
    warengruppen = {gruppe.name: gruppe for gruppe in Warengruppe.objects.all()}
    plaene = {bauplan.nummer: bauplan for bauplan in ARTIKEL_NORD}
    orte = {ort.name: ort for ort in Ort.objects.filter(bereich__haus=haus)}
    angelegt = 0
    for neu, alt in NEU_VERGEBEN.items():
        bauplan = plaene[neu]
        ware = Ware.objects.get(name=namen(bauplan.ware))
        satz = Hausartikel(
            haus=haus,
            name=namen(bauplan.bezeichnung or bauplan.ware),
            ware=ware,
            warengruppe=warengruppen[namen(bauplan.warengruppe)],
            artikelnummer=neu,
            quelle=bauplan.quelle,
            status=AKTIV,
            basiseinheit=einheiten[bauplan.einheit] if bauplan.einheit else ware.basiseinheit,
            faktor_zur_ware=Decimal(bauplan.faktor) if bauplan.faktor else None,
            inventurart=arten[bauplan.art or ART_JE_WARE[bauplan.ware]],
        )
        satz.full_clean()
        satz.save()
        angelegt += 1
        vorgaenger = Hausartikel.objects.get(haus=haus, artikelnummer=alt)
        vorgaenger.name = namen(plaene[alt].bezeichnung or plaene[alt].ware)
        vorgaenger.full_clean()
        vorgaenger.save()
        for ortsplan in ORTE_NORD:
            if _waren_am_ort(ortsplan, {neu: satz}, namen):
                _erwartung_anlegen(orte[namen(ortsplan.name)], satz, ortsplan, namen)
    return angelegt


def ausgeschieden_am(heute: date) -> datetime:
    """A point in time for everything that is no longer active: half a year before the build's day.

    Relative to '--date', not to the clock (until 10/2026), so a build for a
    fixed day shows the same "discontinued since" every time.
    """
    return timezone.make_aware(datetime.combine(heute, time(hour=12))) - timedelta(days=180)
