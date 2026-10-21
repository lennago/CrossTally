"""The house-independent part of the demo stock: products, packs, recipe articles.

Everything here is invented. The products carry generic names, so the demo
shows what it is about without depicting a real stock.

The stock stands as a table and is built separately from it. That way one
sees first, when reading, **what** the demo contains, and only then how it
arises. Whoever adds a product writes one row and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from katalog.models import (
    Basiseinheit,
    Behaelter,
    Dimension,
    Gebinde,
    Inventurart,
    Rezepturartikel,
    Rezepturkomponente,
    Ware,
    Warenfamilie,
)
from organisation.models import Haus

from .names_en import Namen
from .saving import angelegt

STUECK = "Stück(e)"
GRAMM = "g"
KILO = "kg"
LITER = "Liter"
PORTION = "Portion"

#: The dimensions, as keys of the conversions per product.
STUECKZAHL = Dimension.STUECK
MASSE = Dimension.MASSE
VOLUMEN = Dimension.VOLUMEN
PORTIONEN = Dimension.PORTION

#: The inventory types of the demo, in their order: the tabs of the app.
#: Cups that come back are counted on their own, and only one house carries
#: them ('houses.ARTIKEL_NORD').
MONATSINVENTUR = "Monatsinventur"
JAHRESINVENTUR = "Jahresinventur"
BECHERINVENTUR = "Becherinventur"
INVENTURARTEN = (MONATSINVENTUR, JAHRESINVENTUR, BECHERINVENTUR)

#: Shorter in the table: what is only counted once a year.
NUR_JAHR = JAHRESINVENTUR


@dataclass(frozen=True)
class Familienbauplan:
    """A product family and the unit its sum stands in.

    'uebergeordnet' names the family above it. The family above sums the
    products of the ones below in its own unit, so it has to stand earlier
    in the table.
    """

    name: str
    leiteinheit: str
    beschreibung: str
    uebergeordnet: str = ""


@dataclass(frozen=True)
class Warenbauplan:
    name: str
    einheit: str
    familie: str = ""
    #: Pack name and number of units in it.
    gebinde: tuple[tuple[str, str], ...] = ()
    abwiegbar: bool = False
    #: Dimension -> what one unit of the product is in its reference unit:
    #: grams per piece or litre, millilitres per bottle, portions per
    #: kilogram. As the model stores it, not as the form shows it (that one
    #: turns some round, "kg je Portion"). The scale needs the grams, a
    #: family that sums in another dimension the way there. Mass products
    #: carry no grams, there the unit computes itself.
    umrechnungen: dict[str, str] = field(default_factory=dict)
    #: The inventory type its articles get in every house, unless a house
    #: says otherwise ('houses.Artikelbauplan.art').
    art: str = MONATSINVENTUR
    behaelter: tuple[str, ...] = ()


@dataclass(frozen=True)
class Rezepturartikelbauplan:
    """A prepared portion that exists only for counting."""

    name: str
    einheit: str
    #: Product -> quantity in its base unit, per one unit of the recipe article.
    #: With 'anteilig' instead the share of the weight in percent, 100 in all.
    rezeptur: dict[str, str]
    behaelter: tuple[str, ...] = ()
    anteilig: bool = False


# --------------------------------------------------------------------------- #
# Containers
# --------------------------------------------------------------------------- #

#: Name, tare in grams, houses. A container without a house hangs only on its
#: product. "Schale blau" deliberately belongs to only one house: so that at
#: the closing of the other inventory the question about the foreign
#: container comes up.
BEHAELTER: list[tuple[str, str, tuple[str, ...]]] = [
    ("Schale klein", "245", ("Kino Nord", "Kino Ost")),
    ("Schale groß", "480", ("Kino Nord", "Kino Ost")),
    ("Eimer weiß", "1150", ("Kino Nord",)),
    ("Schale blau", "310", ("Kino Ost",)),
    ("Sahneflasche", "180", ()),
    ("Saucenkanne", "640", ("Kino Nord",)),
]

#: Containers that hang only on their product instead of standing around in the house.
BEHAELTER_JE_WARE: dict[str, tuple[str, ...]] = {
    "Sprühsahne": ("Sahneflasche",),
    "Käsesauce": ("Saucenkanne",),
}


# --------------------------------------------------------------------------- #
# Product families
# --------------------------------------------------------------------------- #

#: Two families stand above others: "Getränke" sums its three families in
#: litres, "Snacks" its five in kilograms. Every bottle already knows its
#: millilitres, every bar its grams, so nothing is missing for the sums above.
FAMILIEN: list[Familienbauplan] = [
    Familienbauplan("Getränke", LITER, "Alles, was getrunken wird, kalt und in Flaschen."),
    Familienbauplan("Snacks", KILO, "Alles, was neben dem Film gegessen wird."),
    Familienbauplan(
        "Limonaden",
        LITER,
        "Alles, was gezuckert und sprudelnd verkauft wird, ob in Flaschen oder als Sirup.",
        uebergeordnet="Getränke",
    ),
    Familienbauplan(
        "Säfte und Wasser",
        LITER,
        "Stille Getränke in kleinen Flaschen.",
        uebergeordnet="Getränke",
    ),
    Familienbauplan(
        "Bier und Wein",
        LITER,
        "Alkoholisches und die alkoholfreien Sorten daneben.",
        uebergeordnet="Getränke",
    ),
    Familienbauplan(
        "Popcorn",
        KILO,
        "Was in die Maschine geht und was hinterher darauf kommt.",
        uebergeordnet="Snacks",
    ),
    Familienbauplan(
        "Nachos",
        KILO,
        "Chips und die Saucen dazu.",
        uebergeordnet="Snacks",
    ),
    Familienbauplan(
        "Süßwaren",
        KILO,
        "Lose Ware aus der Theke und abgepackte Riegel.",
        uebergeordnet="Snacks",
    ),
    Familienbauplan("Speiseeis", STUECK, "Alles aus der Tiefkühltruhe."),
    Familienbauplan(
        "Zutaten",
        KILO,
        "Rohstoffe der Eismanufaktur.",
    ),
    Familienbauplan(
        "Heißgetränke",
        KILO,
        "Kaffee, Kakao und Tee samt Zubehör.",
    ),
    Familienbauplan("Verpackung", STUECK, "Becher, Tüten, Schalen und was sonst mitgeht."),
    Familienbauplan(
        "Mehrwegbecher",
        STUECK,
        "Becher mit Pfand, die gespült zurückkommen.",
        uebergeordnet="Verpackung",
    ),
    Familienbauplan(
        "Herzhaftes",
        STUECK,
        "Warme Ware neben dem Popcorn.",
        uebergeordnet="Snacks",
    ),
    # Sums in portions: the unit only a family carries. The bar is one
    # portion, the loose pretzels twenty portions per kilogram.
    Familienbauplan(
        "Knabbereien",
        PORTION,
        "Was portionsweise über die Theke geht, ob am Stück oder lose.",
        uebergeordnet="Snacks",
    ),
]


# --------------------------------------------------------------------------- #
# Products
# --------------------------------------------------------------------------- #

EINZELN = ("Einzeln", "1")

WAREN: list[Warenbauplan] = [
    # -- Lemonades ----------------------------------------------------------
    Warenbauplan(
        "Cola 0,5 l",
        STUECK,
        "Limonaden",
        (EINZELN, ("Kasten", "24")),
        umrechnungen={VOLUMEN: "500", MASSE: "520"},
    ),
    Warenbauplan(
        "Cola zuckerfrei 0,5 l",
        STUECK,
        "Limonaden",
        (EINZELN, ("Kasten", "24")),
        umrechnungen={VOLUMEN: "500", MASSE: "520"},
    ),
    Warenbauplan(
        "Orangenlimonade 0,5 l",
        STUECK,
        "Limonaden",
        (EINZELN, ("Kasten", "24")),
        umrechnungen={VOLUMEN: "500", MASSE: "520"},
    ),
    Warenbauplan(
        "Zitronenlimonade 0,5 l",
        STUECK,
        "Limonaden",
        (EINZELN, ("Kasten", "24")),
        umrechnungen={VOLUMEN: "500", MASSE: "520"},
    ),
    Warenbauplan(
        "Bitterlimonade 0,2 l",
        STUECK,
        "Limonaden",
        (EINZELN, ("Kasten", "12")),
        umrechnungen={VOLUMEN: "200", MASSE: "215"},
    ),
    Warenbauplan(
        "Colasirup", LITER, "Limonaden", (("Kanister", "10"),), umrechnungen={MASSE: "1240"}
    ),
    Warenbauplan(
        "Orangensirup", LITER, "Limonaden", (("Kanister", "10"),), umrechnungen={MASSE: "1240"}
    ),
    Warenbauplan(
        "Zitronensirup", LITER, "Limonaden", (("Kanister", "10"),), umrechnungen={MASSE: "1240"}
    ),
    # -- Juices and water ---------------------------------------------------
    Warenbauplan(
        "Mineralwasser 0,25 l",
        STUECK,
        "Säfte und Wasser",
        (EINZELN, ("Kasten", "24")),
        umrechnungen={VOLUMEN: "250", MASSE: "265"},
    ),
    Warenbauplan(
        "Mineralwasser 0,5 l",
        STUECK,
        "Säfte und Wasser",
        (EINZELN, ("Kasten", "12")),
        umrechnungen={VOLUMEN: "500", MASSE: "520"},
    ),
    Warenbauplan(
        "Apfelsaft 0,2 l",
        STUECK,
        "Säfte und Wasser",
        (EINZELN, ("Kasten", "24")),
        umrechnungen={VOLUMEN: "200", MASSE: "215"},
    ),
    Warenbauplan(
        "Orangensaft 0,2 l",
        STUECK,
        "Säfte und Wasser",
        (EINZELN, ("Kasten", "24")),
        umrechnungen={VOLUMEN: "200", MASSE: "215"},
    ),
    Warenbauplan(
        "Apfelschorle 0,5 l",
        STUECK,
        "Säfte und Wasser",
        (EINZELN, ("Kasten", "12")),
        umrechnungen={VOLUMEN: "500", MASSE: "520"},
    ),
    Warenbauplan(
        "Eistee Pfirsich 0,5 l",
        STUECK,
        "Säfte und Wasser",
        (EINZELN, ("Kasten", "12")),
        umrechnungen={VOLUMEN: "500", MASSE: "520"},
    ),
    Warenbauplan(
        "Tomatensaft 0,2 l",
        STUECK,
        "Säfte und Wasser",
        (EINZELN,),
        umrechnungen={VOLUMEN: "200", MASSE: "215"},
    ),
    # -- Beer and wine ------------------------------------------------------
    Warenbauplan(
        "Bier 0,33 l",
        STUECK,
        "Bier und Wein",
        (EINZELN, ("Kasten", "20")),
        umrechnungen={VOLUMEN: "330", MASSE: "345"},
    ),
    Warenbauplan(
        "Bier alkoholfrei 0,33 l",
        STUECK,
        "Bier und Wein",
        (EINZELN, ("Kasten", "20")),
        umrechnungen={VOLUMEN: "330", MASSE: "345"},
    ),
    Warenbauplan(
        "Weißwein 0,2 l",
        STUECK,
        "Bier und Wein",
        (EINZELN, ("Karton", "12")),
        umrechnungen={VOLUMEN: "200", MASSE: "215"},
        art=NUR_JAHR,
    ),
    Warenbauplan(
        "Rotwein 0,2 l",
        STUECK,
        "Bier und Wein",
        (EINZELN, ("Karton", "12")),
        umrechnungen={VOLUMEN: "200", MASSE: "215"},
        art=NUR_JAHR,
    ),
    Warenbauplan(
        "Sekt 0,2 l",
        STUECK,
        "Bier und Wein",
        (EINZELN, ("Karton", "12")),
        umrechnungen={VOLUMEN: "200", MASSE: "215"},
        art=NUR_JAHR,
    ),
    # -- Popcorn ------------------------------------------------------------
    Warenbauplan("Puffmais", KILO, "Popcorn", (("Sack", "22.5"),)),
    Warenbauplan("Popcornsalz", KILO, "Popcorn", (("Karton", "10"),)),
    Warenbauplan("Popcornzucker", KILO, "Popcorn", (("Sack", "25"),)),
    Warenbauplan("Kokosfett", KILO, "Popcorn", (("Karton", "12.5"),)),
    Warenbauplan("Karamellmasse", KILO, "Popcorn", (("Eimer", "6"),), art=NUR_JAHR),
    # -- Nachos -------------------------------------------------------------
    Warenbauplan(
        "Nachochips", KILO, "Nachos", (("Karton", "8"),), abwiegbar=True, behaelter=("Schale groß",)
    ),
    Warenbauplan("Käsesauce", LITER, "Nachos", (("Karton", "6"),), umrechnungen={MASSE: "1080"}),
    Warenbauplan("Salsasauce", LITER, "Nachos", (("Karton", "6"),), umrechnungen={MASSE: "1050"}),
    Warenbauplan(
        "Jalapenos",
        KILO,
        "Nachos",
        (("Eimer", "2.9"),),
        abwiegbar=True,
        behaelter=("Schale klein",),
    ),
    Warenbauplan(
        "Guacamole",
        LITER,
        "Nachos",
        (("Karton", "4"),),
        umrechnungen={MASSE: "1020"},
        art=NUR_JAHR,
    ),
    # -- Confectionery ------------------------------------------------------
    Warenbauplan(
        "Fruchtgummi lose",
        KILO,
        "Süßwaren",
        (("Karton", "3"),),
        abwiegbar=True,
        behaelter=("Schale klein", "Schale groß"),
    ),
    Warenbauplan(
        "Schokolinsen lose",
        KILO,
        "Süßwaren",
        (("Karton", "3"),),
        abwiegbar=True,
        behaelter=("Schale klein", "Schale groß"),
    ),
    Warenbauplan(
        "Lakritz lose",
        KILO,
        "Süßwaren",
        (("Karton", "3"),),
        abwiegbar=True,
        behaelter=("Schale klein",),
    ),
    Warenbauplan(
        "Saure Schnüre lose",
        KILO,
        "Süßwaren",
        (("Karton", "3"),),
        abwiegbar=True,
        behaelter=("Schale klein",),
    ),
    # The only product with three packs, so the order of packs has something
    # to sort: loose, in a counter display of 20 and in a carton of two
    # displays. Every location names the packs it expects, none the display.
    # It is ticked in no counting list, so no run counts it, and no number of
    # the demo moves with it.
    Warenbauplan(
        "Schokoriegel",
        STUECK,
        "Knabbereien",
        (EINZELN, ("Display", "20"), ("Karton", "40")),
        umrechnungen={MASSE: "45", PORTIONEN: "1"},
    ),
    Warenbauplan(
        "Keksriegel", STUECK, "Süßwaren", (EINZELN, ("Karton", "40")), umrechnungen={MASSE: "40"}
    ),
    Warenbauplan(
        "Kaugummi",
        STUECK,
        "Süßwaren",
        (EINZELN, ("Karton", "24")),
        umrechnungen={MASSE: "30"},
        art=NUR_JAHR,
    ),
    Warenbauplan(
        "Salzbrezeln lose",
        KILO,
        "Knabbereien",
        (("Karton", "5"),),
        abwiegbar=True,
        behaelter=("Schale groß",),
        # Stored as portions per kilogram. The form shows "kg je Portion": 0,05.
        umrechnungen={PORTIONEN: "20"},
    ),
    # -- Ingredients of the ice cream workshop ------------------------------
    Warenbauplan("Sahne", LITER, "Zutaten", (("Kanister", "10"),), umrechnungen={MASSE: "1000"}),
    Warenbauplan("Zucker", KILO, "Zutaten", (("Sack", "25"),)),
    # Put into "Not countable" by mistake ('VERSEHENTLICH_NICHT_ZAEHLBAR').
    Warenbauplan("Waffelbruch", KILO, "Zutaten", (("Karton", "5"),)),
    # -- Ice cream ----------------------------------------------------------
    Warenbauplan(
        "Eis Vanille", STUECK, "Speiseeis", (EINZELN, ("Karton", "20")), umrechnungen={MASSE: "110"}
    ),
    Warenbauplan(
        "Eis Schokolade",
        STUECK,
        "Speiseeis",
        (EINZELN, ("Karton", "20")),
        umrechnungen={MASSE: "110"},
    ),
    Warenbauplan(
        "Eis Erdbeer", STUECK, "Speiseeis", (EINZELN, ("Karton", "20")), umrechnungen={MASSE: "110"}
    ),
    Warenbauplan(
        "Eishörnchen", STUECK, "Speiseeis", (EINZELN, ("Karton", "24")), umrechnungen={MASSE: "125"}
    ),
    Warenbauplan(
        "Eissandwich", STUECK, "Speiseeis", (EINZELN, ("Karton", "24")), umrechnungen={MASSE: "95"}
    ),
    Warenbauplan(
        "Eisriegel",
        STUECK,
        "Speiseeis",
        (EINZELN, ("Karton", "24")),
        umrechnungen={MASSE: "85"},
        art=NUR_JAHR,
    ),
    # -- Hot drinks ---------------------------------------------------------
    Warenbauplan("Kaffeebohnen", KILO, "Heißgetränke", (("Karton", "6"),)),
    Warenbauplan("Kakaopulver", KILO, "Heißgetränke", (("Karton", "10"),)),
    Warenbauplan(
        "Teebeutel", STUECK, "Heißgetränke", (("Karton", "200"),), umrechnungen={MASSE: "5"}
    ),
    Warenbauplan("Milchpulver", KILO, "Heißgetränke", (("Karton", "10"),)),
    Warenbauplan(
        "Zuckersticks", STUECK, "Heißgetränke", (("Karton", "1000"),), umrechnungen={MASSE: "4"}
    ),
    # -- Packaging ----------------------------------------------------------
    Warenbauplan(
        "Popcorntüte klein", STUECK, "Verpackung", (("Karton", "500"),), umrechnungen={MASSE: "12"}
    ),
    Warenbauplan(
        "Popcorntüte mittel", STUECK, "Verpackung", (("Karton", "500"),), umrechnungen={MASSE: "16"}
    ),
    Warenbauplan(
        "Popcorntüte groß", STUECK, "Verpackung", (("Karton", "300"),), umrechnungen={MASSE: "22"}
    ),
    Warenbauplan(
        "Popcorneimer", STUECK, "Verpackung", (("Karton", "200"),), umrechnungen={MASSE: "48"}
    ),
    Warenbauplan(
        "Nachoschale", STUECK, "Verpackung", (("Karton", "400"),), umrechnungen={MASSE: "18"}
    ),
    Warenbauplan(
        "Becher 0,3 l", STUECK, "Verpackung", (("Karton", "1000"),), umrechnungen={MASSE: "14"}
    ),
    Warenbauplan(
        "Becher 0,4 l", STUECK, "Verpackung", (("Karton", "1000"),), umrechnungen={MASSE: "16"}
    ),
    # The same cup again, imported under a second number and name in Kino Nord
    # ('houses.DUBLETTE'). Merging it into "Becher 0,4 l" shows the duplicates.
    Warenbauplan(
        "Trinkbecher 0,4 l",
        STUECK,
        "Verpackung",
        (("Karton", "1000"),),
        umrechnungen={MASSE: "16"},
    ),
    Warenbauplan(
        "Becher 0,5 l", STUECK, "Verpackung", (("Karton", "800"),), umrechnungen={MASSE: "19"}
    ),
    # Counted in their own inventory type, and only Kino Nord carries them.
    Warenbauplan(
        "Mehrwegbecher 0,3 l",
        STUECK,
        "Mehrwegbecher",
        (("Karton", "50"),),
        umrechnungen={MASSE: "40"},
        art=BECHERINVENTUR,
    ),
    Warenbauplan(
        "Mehrwegbecher 0,5 l",
        STUECK,
        "Mehrwegbecher",
        (("Karton", "50"),),
        umrechnungen={MASSE: "55"},
        art=BECHERINVENTUR,
    ),
    Warenbauplan(
        "Deckel klein", STUECK, "Verpackung", (("Karton", "1000"),), umrechnungen={MASSE: "6"}
    ),
    Warenbauplan(
        "Deckel groß", STUECK, "Verpackung", (("Karton", "1000"),), umrechnungen={MASSE: "7"}
    ),
    Warenbauplan(
        "Trinkhalm", STUECK, "Verpackung", (("Karton", "2500"),), umrechnungen={MASSE: "2"}
    ),
    Warenbauplan(
        "Serviette", STUECK, "Verpackung", (("Karton", "5000"),), umrechnungen={MASSE: "1"}
    ),
    # -- Savoury ------------------------------------------------------------
    Warenbauplan("Würstchen", STUECK, "Herzhaftes", (("Glas", "40"),), umrechnungen={MASSE: "55"}),
    Warenbauplan(
        "Hotdogbrötchen", STUECK, "Herzhaftes", (("Beutel", "50"),), umrechnungen={MASSE: "60"}
    ),
    Warenbauplan(
        "Röstzwiebeln", KILO, "Herzhaftes", (("Eimer", "2.5"),), umrechnungen={STUECKZAHL: "20"}
    ),
    Warenbauplan(
        "Senf",
        LITER,
        "Herzhaftes",
        (("Kanister", "5"),),
        umrechnungen={STUECKZAHL: "20", MASSE: "1100"},
    ),
    Warenbauplan(
        "Tomatenketchup",
        LITER,
        "Herzhaftes",
        (("Kanister", "5"),),
        umrechnungen={STUECKZAHL: "20", MASSE: "1140"},
    ),
    # -- Without product family ---------------------------------------------
    Warenbauplan(
        "Sprühsahne", GRAMM, "", (("Karton", "12000"),), abwiegbar=True, behaelter=("Sahneflasche",)
    ),
    Warenbauplan(
        "Zuckerstreusel", KILO, "", (("Karton", "4"),), abwiegbar=True, behaelter=("Schale klein",)
    ),
    Warenbauplan("Eiswürfel", KILO, "", (("Beutel", "5"),), art=NUR_JAHR),
    Warenbauplan(
        "Reinigungstücher",
        STUECK,
        "",
        (("Karton", "600"),),
        umrechnungen={MASSE: "8"},
        art=NUR_JAHR,
    ),
    Warenbauplan(
        "Einweghandschuhe",
        STUECK,
        "",
        (("Karton", "1000"),),
        umrechnungen={MASSE: "3"},
        art=NUR_JAHR,
    ),
    # -- Not countable ------------------------------------------------------
    Warenbauplan("Becherpfand", STUECK, "Nicht zählbar", (EINZELN,)),
    Warenbauplan("Eimerpfand", STUECK, "Nicht zählbar", (EINZELN,)),
    Warenbauplan("Wertgutschein", STUECK, "Nicht zählbar", (EINZELN,)),
    Warenbauplan("Menürabatt", STUECK, "Nicht zählbar", (EINZELN,)),
]


# --------------------------------------------------------------------------- #
# Recipe articles
# --------------------------------------------------------------------------- #

REZEPTURARTIKEL: list[Rezepturartikelbauplan] = [
    Rezepturartikelbauplan(
        "Nachos mit Käse",
        STUECK,
        {"Nachochips": "0.1", "Käsesauce": "0.06", "Nachoschale": "1"},
    ),
    Rezepturartikelbauplan(
        "Popcorn Tüte groß",
        STUECK,
        {
            "Puffmais": "0.08",
            "Kokosfett": "0.02",
            "Popcornsalz": "0.002",
            "Popcorntüte groß": "1",
        },
    ),
    Rezepturartikelbauplan(
        "Popcorn süß lose",
        GRAMM,
        {"Puffmais": "0.00006", "Popcornzucker": "0.00003", "Kokosfett": "0.000015"},
        behaelter=("Schale groß", "Eimer weiß"),
    ),
    # As shares of the weight: the products are in kg, the app converts.
    Rezepturartikelbauplan(
        "Süßwarenmischung",
        GRAMM,
        {"Fruchtgummi lose": "50", "Schokolinsen lose": "30", "Saure Schnüre lose": "20"},
        behaelter=("Schale klein", "Schale groß"),
        anteilig=True,
    ),
    # Syrups in litres, weighed as a mix: the share goes through the grams
    # per litre that every syrup carries.
    Rezepturartikelbauplan(
        "Sirupmischung Hauslimo",
        GRAMM,
        {"Colasirup": "40", "Orangensirup": "35", "Zitronensirup": "25"},
        behaelter=("Saucenkanne",),
        anteilig=True,
    ),
    Rezepturartikelbauplan(
        "Kaffee im Becher",
        STUECK,
        {"Kaffeebohnen": "0.008", "Becher 0,3 l": "1", "Deckel klein": "1"},
    ),
    Rezepturartikelbauplan(
        "Hotdog",
        STUECK,
        {"Würstchen": "1", "Hotdogbrötchen": "1", "Senf": "0.01", "Röstzwiebeln": "0.005"},
    ),
]


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #


def familien_anlegen(namen: Namen) -> dict[str, Warenfamilie]:
    """The families, before the first product comes in.

    The lead unit is set here and not inherited from the first product via
    'Warenfamilie.aufnehmen'. In the demo it should be fixed, whatever order
    the products arise in.
    """
    einheiten = {einheit.name: einheit for einheit in Basiseinheit.objects.all()}
    familien = {}
    for bauplan in FAMILIEN:
        familie = angelegt(
            Warenfamilie(
                name=namen(bauplan.name),
                beschreibung=namen(bauplan.beschreibung),
                leiteinheit=einheiten[bauplan.leiteinheit],
                uebergeordnet=familien[bauplan.uebergeordnet] if bauplan.uebergeordnet else None,
            )
        )
        # The key stays German, because the blueprints look it up under that.
        familien[bauplan.name] = familie
    familien["Nicht zählbar"] = Warenfamilie.nicht_zaehlbar()
    return familien


def behaelter_anlegen(haeuser: dict[str, Haus], namen: Namen) -> dict[str, Behaelter]:
    """The vessels weighing happens in. "Without container" is already there."""
    behaelter = {satz.name: satz for satz in Behaelter.objects.filter(immer_erlaubt=True)}
    for name, tara, hausnamen in BEHAELTER:
        satz = angelegt(Behaelter(name=namen(name), tara=Decimal(tara)))
        satz.haeuser.set([haeuser[hausname] for hausname in hausnamen])
        behaelter[name] = satz
    return behaelter


def waren_anlegen(
    familien: dict[str, Warenfamilie], behaelter: dict[str, Behaelter], namen: Namen
) -> dict[str, Ware]:
    """Products including packs and the containers that hang on a single product."""
    einheiten = {einheit.name: einheit for einheit in Basiseinheit.objects.all()}
    waren = {}
    for bauplan in WAREN:
        ware = Ware.objects.create(
            name=namen(bauplan.name),
            basiseinheit=einheiten[bauplan.einheit],
            warenfamilie=familien[bauplan.familie] if bauplan.familie else None,
            abwiegbar=bauplan.abwiegbar,
        )
        ware.umrechnungen_setzen(
            {dimension: Decimal(wert) for dimension, wert in bauplan.umrechnungen.items()}
        )
        # The check comes after the factors: a family that sums in another
        # dimension asks for them, and they hang on the saved product.
        ware.full_clean()
        if bauplan.behaelter:
            ware.behaelter.set([behaelter[name] for name in bauplan.behaelter])
        for name, faktor in bauplan.gebinde:
            angelegt(
                Gebinde(
                    ware=ware,
                    # The loose single unit keeps its stored name. It is called
                    # differently in every language, and the switch says so.
                    name=name if (name, faktor) == EINZELN else namen(name),
                    ist_einzeln=(name, faktor) == EINZELN,
                    faktor_in_basiseinheit=Decimal(faktor),
                )
            )
        waren[bauplan.name] = ware
    return waren


#: Someone clicked "Not countable" by mistake, after every run was created.
#: The product remembers its family, its article the status and the place
#: in the counting list. "Wieder aufnehmen" on the product's page brings
#: them back ('Ware.wieder_aufnehmen'). Only the ice cream workshop carries
#: it, and its preparation counts nothing yet.
VERSEHENTLICH_NICHT_ZAEHLBAR = ("Waffelbruch",)


def versehentlich_ignorieren(namen: Namen) -> None:
    """The wrong click, through the service of the button on the product's page."""
    for name in VERSEHENTLICH_NICHT_ZAEHLBAR:
        Ware.objects.get(name=namen(name)).ignorieren()


def rezepturartikel_anlegen(
    waren: dict[str, Ware], behaelter: dict[str, Behaelter], namen: Namen
) -> dict[str, Rezepturartikel]:
    """The prepared portions including recipe."""
    einheiten = {einheit.name: einheit for einheit in Basiseinheit.objects.all()}
    rezepturartikel = {}
    for bauplan in REZEPTURARTIKEL:
        artikel = angelegt(
            Rezepturartikel(
                name=namen(bauplan.name),
                basiseinheit=einheiten[bauplan.einheit],
                anteilig=bauplan.anteilig,
            )
        )
        if bauplan.behaelter:
            artikel.behaelter.set([behaelter[name] for name in bauplan.behaelter])
        for warenname, menge in bauplan.rezeptur.items():
            komponente = Rezepturkomponente(
                rezepturartikel=artikel,
                ware=waren[warenname],
                menge_in_basiseinheit=Decimal(menge),
            )
            # The same checks as the form: a share needs the product's weight.
            komponente.full_clean()
            komponente.save()
        if bauplan.anteilig:
            Rezepturartikel.anteile_pruefen(Decimal(menge) for menge in bauplan.rezeptur.values())
        rezepturartikel[bauplan.name] = artikel
    return rezepturartikel


#: The type per product, by its German name: the articles of the houses take it over.
ART_JE_WARE = {bauplan.name: bauplan.art for bauplan in WAREN}


def inventurarten_anlegen(namen: Namen) -> dict[str, Inventurart]:
    """The inventory types in their order, keyed by their German name like everything here.

    The one the migration created goes first: no article counts in it yet,
    and the demo creates its own, in the language of the build.
    """
    Inventurart.objects.all().delete()
    return {
        name: angelegt(Inventurart(name=namen(name), reihenfolge=stelle))
        for stelle, name in enumerate(INVENTURARTEN, start=1)
    }


def behaelter_je_ware_zuordnen(waren: dict[str, Ware], behaelter: dict[str, Behaelter]) -> None:
    """Vessels that belong to exactly one product and stand around in no house."""
    for warenname, namen in BEHAELTER_JE_WARE.items():
        waren[warenname].behaelter.add(*[behaelter[name] for name in namen])
