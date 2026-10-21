"""The invented names of the demo stock in English.

Names are **data**, not interface texts. They do not go through gettext,
because in operation the products of the house stand there, and nobody
translates those. For the demo it is different: it is invented, and whoever
tries it in English should not find "Zitronenlimonade" on the shelf.

**In the blueprints German stays.** The names wire everything together
there: the recipe names its products, the counting list its families, the
addition its location. Translation only happens where a name goes into a
field or is compared with one that comes from a field. The key stays
German, the value becomes English. That keeps the tables readable and the
wiring intact.

What is **not** here:

- "Einzeln". The loose single unit carries a switch instead of a name
  ('Gebinde.ist_einzeln'), and the interface makes "Single" of it by itself.
- "Nicht zählbar", "Ohne Behälter" and "Ohne Warengruppe". They belong to
  the application and not to a stock. They carry their display name from
  the catalog and thus follow the language of the account, not that of the
  build.
- The skeleton of the import files. "gekauft in … zu …" is the format of
  the inventory management system, and that is a German program. Only the
  designations become English there, see 'demo/exports/en/'.

A test in 'demo/tests.py' pins down that every name from the blueprints has
an entry here. Without it a forgotten one would silently fall back to German.
"""

#: German -> English, for everything that gets a name in the demo.
ENGLISCH: dict[str, str] = {
    # -- Notes in the check list ------------------------------------------
    "Spülmaschine war noch voll": "Dishwasher was still full",
    "Sirup steckt in der Anlage": "Syrup is still in the dispenser",
    # -- Houses, areas, locations -----------------------------------------
    "Kino Nord": "Cinema North",
    "Kino Ost": "Cinema East",
    "Kino West": "Cinema West",
    "Eismanufaktur Süd": "Ice cream workshop South",
    "Produktion": "Production",
    "Tiefkühlraum": "Cold store",
    "Rohstofflager": "Raw materials store",
    "Theke": "Counter",
    "Lager": "Store room",
    "Kühlung": "Refrigeration",
    "Foyer": "Lobby",
    "Aktionstisch": "Promotion table",
    "Theke links": "Counter left",
    "Theke rechts": "Counter right",
    "Popcornmaschine": "Popcorn machine",
    "Süßwarenregal": "Sweets shelf",
    "Getränkelager": "Beverage store",
    "Trockenlager": "Dry store",
    "Kühlschrank Theke": "Counter fridge",
    "Tiefkühltruhe": "Freezer",
    "Eistruhe": "Ice cream chest",
    "Nachostation": "Nacho station",
    "Lager Getränke": "Store beverages",
    "Lager Ware": "Store goods",
    # -- Product groups ---------------------------------------------------
    # The number carries the sort order and stays. "VK" and "EK" stand for
    # sales and purchasing in the inventory management system.
    "201 VK - Getränke - Zentral": "201 Sales - Beverages - Central",
    "202 VK - Getränke - Lokal": "202 Sales - Beverages - Local",
    "210 VK - Popcorn": "210 Sales - Popcorn",
    "211 VK - Nachos": "211 Sales - Nachos",
    "220 VK - Süßwaren - Zentral": "220 Sales - Confectionery - Central",
    "221 VK - Süßwaren - Lokal": "221 Sales - Confectionery - Local",
    "230 VK - Eis": "230 Sales - Ice cream",
    "240 VK - Heißgetränke": "240 Sales - Hot drinks",
    "250 EK - Verpackung": "250 Purch - Packaging",
    "260 VK - Herzhaftes": "260 Sales - Savoury",
    "290 EK - Sonstiges": "290 Purch - Miscellaneous",
    "299 - Pfand und Gutscheine": "299 - Deposits and vouchers",
    "Fertigware": "Finished goods",
    "Rohstoffe": "Raw materials",
    # -- Product families including description ---------------------------
    "Getränke": "Drinks",
    "Alles, was getrunken wird, kalt und in Flaschen.": "Everything drunk, cold and in bottles.",
    "Snacks": "Snacks",
    "Knabbereien": "Nibbles",
    "Was portionsweise über die Theke geht, ob am Stück oder lose.": (
        "What goes over the counter by the portion, in one piece or loose."
    ),
    "Alles, was neben dem Film gegessen wird.": "Everything eaten alongside the film.",
    "Zutaten": "Ingredients",
    "Rohstoffe der Eismanufaktur.": "Raw materials of the ice cream workshop.",
    "Limonaden": "Soft drinks",
    "Alles, was gezuckert und sprudelnd verkauft wird, ob in Flaschen oder als Sirup.": (
        "Everything sold sweet and fizzy, in bottles or as syrup."
    ),
    "Säfte und Wasser": "Juices and water",
    "Stille Getränke in kleinen Flaschen.": "Still drinks in small bottles.",
    "Bier und Wein": "Beer and wine",
    "Alkoholisches und die alkoholfreien Sorten daneben.": (
        "The alcoholic range and the alcohol-free sorts beside it."
    ),
    "Popcorn": "Popcorn",
    "Was in die Maschine geht und was hinterher darauf kommt.": (
        "What goes into the machine and what goes on top afterwards."
    ),
    "Nachos": "Nachos",
    "Chips und die Saucen dazu.": "Chips and the sauces that go with them.",
    "Süßwaren": "Confectionery",
    "Lose Ware aus der Theke und abgepackte Riegel.": (
        "Loose goods from the counter and pre-packed bars."
    ),
    "Speiseeis": "Ice cream",
    "Alles aus der Tiefkühltruhe.": "Everything from the freezer.",
    "Heißgetränke": "Hot drinks",
    "Kaffee, Kakao und Tee samt Zubehör.": (
        "Coffee, cocoa and tea with everything that goes with them."
    ),
    "Verpackung": "Packaging",
    "Becher, Tüten, Schalen und was sonst mitgeht.": (
        "Cups, bags, trays and whatever else goes out with the order."
    ),
    "Mehrwegbecher": "Reusable cups",
    "Becher mit Pfand, die gespült zurückkommen.": "Cups with a deposit that come back washed.",
    "Herzhaftes": "Savoury",
    "Warme Ware neben dem Popcorn.": "Hot food beside the popcorn.",
    # -- Inventory types --------------------------------------------------
    "Monatsinventur": "Monthly inventory",
    "Jahresinventur": "Annual inventory",
    "Becherinventur": "Cup inventory",
    # -- Containers -------------------------------------------------------
    "Schale klein": "Bowl small",
    "Schale groß": "Bowl large",
    "Eimer weiß": "Bucket white",
    "Schale blau": "Bowl blue",
    "Sahneflasche": "Cream bottle",
    "Saucenkanne": "Sauce jug",
    # -- Packs ------------------------------------------------------------
    "Beutel": "Bag",
    "Display": "Display box",
    "Eimer": "Bucket",
    "Glas": "Jar",
    "Kanister": "Canister",
    "Karton": "Box",
    "Kasten": "Crate",
    "Sack": "Sack",
    # -- Products ---------------------------------------------------------
    "Sahne": "Cream",
    "Zucker": "Sugar",
    "Waffelbruch": "Waffle crumbs",
    "Cola 0,5 l": "Cola 0.5 l",
    "Cola zuckerfrei 0,5 l": "Cola sugar-free 0.5 l",
    "Orangenlimonade 0,5 l": "Orange soda 0.5 l",
    "Zitronenlimonade 0,5 l": "Lemon soda 0.5 l",
    "Bitterlimonade 0,2 l": "Bitter lemon 0.2 l",
    "Colasirup": "Cola syrup",
    "Orangensirup": "Orange syrup",
    "Zitronensirup": "Lemon syrup",
    "Mineralwasser 0,25 l": "Sparkling water 0.25 l",
    "Mineralwasser 0,5 l": "Sparkling water 0.5 l",
    "Apfelsaft 0,2 l": "Apple juice 0.2 l",
    "Orangensaft 0,2 l": "Orange juice 0.2 l",
    "Apfelschorle 0,5 l": "Apple spritzer 0.5 l",
    "Eistee Pfirsich 0,5 l": "Iced tea peach 0.5 l",
    "Tomatensaft 0,2 l": "Tomato juice 0.2 l",
    "Bier 0,33 l": "Beer 0.33 l",
    "Bier alkoholfrei 0,33 l": "Beer alcohol-free 0.33 l",
    "Weißwein 0,2 l": "White wine 0.2 l",
    "Rotwein 0,2 l": "Red wine 0.2 l",
    "Sekt 0,2 l": "Sparkling wine 0.2 l",
    "Puffmais": "Popping corn",
    "Popcornsalz": "Popcorn salt",
    "Popcornzucker": "Popcorn sugar",
    "Kokosfett": "Coconut oil",
    "Karamellmasse": "Caramel mix",
    "Nachochips": "Nacho chips",
    "Käsesauce": "Cheese sauce",
    "Salsasauce": "Salsa sauce",
    "Jalapenos": "Jalapenos",
    "Guacamole": "Guacamole",
    "Fruchtgummi lose": "Fruit gums loose",
    "Schokolinsen lose": "Chocolate beans loose",
    "Lakritz lose": "Liquorice loose",
    "Saure Schnüre lose": "Sour laces loose",
    "Schokoriegel": "Chocolate bar",
    "Keksriegel": "Biscuit bar",
    "Kaugummi": "Chewing gum",
    "Salzbrezeln lose": "Pretzels loose",
    "Eis Vanille": "Ice cream vanilla",
    "Eis Schokolade": "Ice cream chocolate",
    "Eis Erdbeer": "Ice cream strawberry",
    "Eishörnchen": "Ice cream cone",
    "Eissandwich": "Ice cream sandwich",
    "Eisriegel": "Ice cream bar",
    "Kaffeebohnen": "Coffee beans",
    "Kakaopulver": "Cocoa powder",
    "Teebeutel": "Tea bags",
    "Milchpulver": "Milk powder",
    "Zuckersticks": "Sugar sticks",
    "Popcorntüte klein": "Popcorn bag small",
    "Popcorntüte mittel": "Popcorn bag medium",
    "Popcorntüte groß": "Popcorn bag large",
    "Popcorneimer": "Popcorn bucket",
    "Nachoschale": "Nacho tray",
    "Becher 0,3 l": "Cup 0.3 l",
    "Becher 0,4 l": "Cup 0.4 l",
    "Trinkbecher 0,4 l": "Drinking cup 0.4 l",
    "Becher 0,5 l": "Cup 0.5 l",
    "Mehrwegbecher 0,3 l": "Reusable cup 0.3 l",
    "Mehrwegbecher 0,5 l": "Reusable cup 0.5 l",
    "Deckel klein": "Lid small",
    "Deckel groß": "Lid large",
    "Trinkhalm": "Drinking straw",
    "Serviette": "Napkin",
    "Würstchen": "Sausage",
    "Hotdogbrötchen": "Hot dog bun",
    "Röstzwiebeln": "Fried onions",
    "Senf": "Mustard",
    "Tomatenketchup": "Tomato ketchup",
    "Sprühsahne": "Spray cream",
    "Zuckerstreusel": "Sugar sprinkles",
    "Eiswürfel": "Ice cubes",
    "Reinigungstücher": "Cleaning wipes",
    "Einweghandschuhe": "Disposable gloves",
    "Becherpfand": "Cup deposit",
    "Eimerpfand": "Bucket deposit",
    "Wertgutschein": "Gift voucher",
    "Menürabatt": "Meal discount",
    # -- Recipe articles ------------------------------------------------------
    # "Popcorn Tüte groß" is the finished portion, "Popcorntüte groß" the empty
    # bag from the packaging. In English they have to stay further apart,
    # otherwise two articles would carry the same name.
    "Nachos mit Käse": "Nachos with cheese",
    "Popcorn Tüte groß": "Popcorn portion large",
    "Popcorn süß lose": "Popcorn sweet loose",
    "Süßwarenmischung": "Pick and mix sweets",
    "Sirupmischung Hauslimo": "Syrup mix house lemonade",
    "Kaffee im Becher": "Coffee in a cup",
    "Hotdog": "Hot dog",
    # -- Designations under which a house carries its product ---------------
    "Cola Flasche 0,5": "Cola bottle 0.5",
    "Cola zuckerfrei Flasche 0,5": "Cola sugar-free bottle 0.5",
    "Orange Flasche 0,5": "Orange bottle 0.5",
    "Zitrone Flasche 0,5": "Lemon bottle 0.5",
    "Eishörnchen alt": "Ice cream cone old",
    "Mais für Popcorn": "Corn for popcorn",
    "Popcornmais": "Popcorn corn",
    "Postmix Cola": "Postmix cola",
    "Postmix Zitrone": "Postmix lemon",
    "Sirup Cola 10 l": "Syrup cola 10 l",
    "Sirup Orange 10 l": "Syrup orange 10 l",
    "Sirup Zitrone 10 l": "Syrup lemon 10 l",
    # -- Accounts ---------------------------------------------------------
    # The surnames are birds and start like the first name. That stays.
    "Alex": "Alex",
    "Amsel": "Avocet",
    "Leonie": "Leonie",
    "Lerche": "Lark",
    "Zoe": "Zoe",
    "Zeisig": "Siskin",
    "Nele": "Nell",
    "Nachtigall": "Nightingale",
}


#: What stands only in the import files and in no blueprint: the column
#: headers and the supplier from the format of the inventory management
#: system, plus the articles the second import brings new. Separate from
#: 'ENGLISCH', because "Artikel" and "Bestand" are too general to be in the
#: name table.
NUR_IN_DATEIEN: dict[str, str] = {
    # Column headers and supplier.
    "Artikel": "Article",
    "Lieferant": "Supplier",
    "VKPreis": "Sales price",
    "Lagerwert": "Stock value",
    "Artikelnummer": "Article number",
    "Bestand": "Stock",
    "Bezeichnung": "Description",
    "Zentraleinkauf": "Central purchasing",
    # What the second import brings new. No house carries these articles yet.
    "Energydrink 0,25 l": "Energy drink 0.25 l",
    "Orangenlimo 0,5 l": "Orange pop 0.5 l",
    "Mais lose": "Corn loose",
    "Puffmais lose": "Popping corn loose",
    "Nussmischung lose": "Nut mix loose",
    "Aktionsartikel Eröffnung": "Promotion item opening",
    "Aktionsware Sommer": "Promotion goods summer",
    "Sammelbestellung Kiosk": "Bulk order kiosk",
}


#: What the inventory management system closes a product group with. The
#: word stays, the group name behind it follows along.
ZWISCHENSUMME = "Summe: "


def in_datei(feld: str) -> str:
    """A field of an import file in English. Unknown stays as it is.

    The skeleton of the row stays German: "gekauft in Kiste(n) zu 24 Stück(e)"
    is the format of the inventory management system, and 'scripts/transform.py'
    reads it by the German word "zu". Only the designations next to it become
    English.
    """
    gestrafft = feld.strip()
    # "Summe: 201 VK - Getränke - Zentral" closes a product group. The
    # import skips the line, it is read nonetheless.
    if gestrafft.startswith(ZWISCHENSUMME):
        gruppe = gestrafft.removeprefix(ZWISCHENSUMME)
        return feld.replace(gruppe, ENGLISCH.get(gruppe, gruppe))
    ersatz = ENGLISCH.get(gestrafft) or NUR_IN_DATEIEN.get(gestrafft)
    return feld.replace(gestrafft, ersatz) if ersatz else feld


def zeile_in_datei(zeile: str) -> str:
    """A whole row, field by field."""
    return ";".join(in_datei(feld) for feld in zeile.split(";"))


class Namen:
    """Returns a name of the blueprint in the language of the build.

    It is called like a function, because in the build it stands everywhere
    the name itself used to: 'namen(bauplan.name)'. In German the name comes
    back unchanged, and the whole build behaves as before.
    """

    def __init__(self, sprache: str = "de"):
        self.sprache = sprache
        self._tabelle = ENGLISCH if sprache == "en" else {}

    def __call__(self, name: str) -> str:
        return self._tabelle.get(name, name)

    def alle(self, namen) -> tuple[str, ...]:
        """Several at once, for the comparisons against stored names."""
        return tuple(self(name) for name in namen)
