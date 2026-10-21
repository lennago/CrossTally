# The demo

*[Deutsche Fassung](DEMO.md)*

One command creates a complete, invented stock: four sites, four accounts, a
product catalogue, count lists and 14 inventories on seven reference dates.
Four runs are closed, two are running right now, and one is still being
prepared (a run is one inventory per site on the same reference date). With it
the whole application can be played through, without anybody first typing in
master data or importing real figures.

> **None of it is real.** Sites, products, article numbers, quantities and names
> are invented, and the password of the accounts stands further down in this
> text. The stock does not belong in a database anybody works with. The command
> therefore refuses as soon as anything in it does not come from the demo, be it
> a single account. On a server without `DEBUG` it builds only with a switch of
> its own (see below).

While the demo is running, the interface says so on every page: a band above the
navigation bar, a badge beside it that stays put while scrolling, the same in
the administration and a line under the header of the counting app. The marker
hangs on a record of its own in the database, not on the data: it stays even if
you delete a site while trying things out, and it disappears
with `--reset`.

## Building it

The demo needs no Docker. The shortest path is the developer route from the
[README](README.en.md) with SQLite:

```bash
cd server
cp .env.example .env         # enter SECRET_KEY and ADMIN_URL
uv sync
uv run manage.py migrate
uv run manage.py demo        # builds the stock and prints an overview
uv run manage.py runserver   # http://localhost:8000
```

> **The demo does not create a database of its own.** It writes into the one
> `DATABASE_URL` points at, during development therefore `server/db.sqlite3`.
> If there is already something in it, the command stops and names what it
> found, with `--reset` as well.

Whoever already works with real data gives the demo a file of its own. Then
their own database stays untouched:

```bash
cd server
export DATABASE_URL="sqlite:///demo.sqlite3"
uv run manage.py migrate
uv run manage.py demo
uv run manage.py runserver
```

The line in the `.env` does not get in the way: what is in the environment takes
precedence, because the `.env` is read without overwriting what is already set.
The path is relative to the directory the command runs from, here
therefore `server/demo.sqlite3`. The file is in the `.gitignore` and can simply
be deleted afterwards.

Back to your own database it goes with `unset DATABASE_URL` or in a new
terminal. Whoever forgets that keeps working in the demo.

For the counting app in the browser, run `npm ci && npm run build` once
from `app/`. Django then serves it under `/app/`.

With Docker the application runs without `DEBUG`, as in operation. There the
command builds only with `--demo-server`, the switch for a server that is meant
to show nothing but the demo. Even then it builds only in a fresh database:
right after the first start, instead of `createsuperuser`. If there is an
account already, it refuses.

```bash
docker compose exec web python manage.py demo --demo-server
```

Whoever reaches such a server and knows this text signs in as `admin`. That is
why, without `DEBUG`, the command builds only when the switch says so
explicitly.

There are five switches:

| Switch | Effect |
| --- | --- |
| `--reset` | Deletes sites, products, articles, count lists, inventories and the four demo accounts before rebuilding. Asks first. Only in a database that already holds the demo. |
| `--date YYYY-MM-DD` | Counts from this day instead of from today. Useful for screenshots that should always look the same. |
| `--language de\|en` | Which language the made-up names and the command's output are created in. |
| `--noinput` | Lets `--reset` run without the question, say from a script. |
| `--demo-server` | Builds without `DEBUG` too, on a server that is meant to show nothing but the demo. |

The switches are English, like those of every Django command, and have short
forms (`-r`, `-d`, `-l`), only `--noinput` and `--demo-server` have none. The
German demo is the default.

### The demo in English

```bash
uv run manage.py demo --language en
```

The sites are then called “Cinema North”, “Cinema East”, “Cinema West” and “Ice cream workshop South”, the
products “Lemon soda 0.5 l” and “Popcorn salt”, the locations “Counter left” and
“Freezer”. The import files are there in English too, and the four accounts are
set to English, so the interface is in that language right after signing in.
The overview the command prints at the end is then in English as well.
Without the switch, `LANGUAGE_CODE` from the `.env` applies: on a server set to
English, the English demo is what you get anyway, and the addresses are English
there too (`/sites/` instead of `/haeuser/`).

Names are **data**, not interface text. In real use they are the products of the
site, and nobody translates those. The demo is made up, so it has a name table
of its own (`server/demo/names_en.py`).

One thing stays German on purpose: **the skeleton of the import files.**
“gekauft in Kiste(n) zu 24 Stück(e)” is the format the merchandise system
produces, and that system is German. Only the descriptions beside it become
English.

“Nicht zählbar”, “Ohne Behälter” and “Ohne Warengruppe” belong to the
application rather than to any particular stock. They live in the message
catalog and therefore follow the language **of the account**: on an English
server a German account still reads them in German. Their stored name stays as
it is, nothing is ever renamed. That holds in every database, including one that
is in real use.

## The accounts

Password for the first three: `demo1234`.

| Account | Role | What it can do |
| --- | --- | --- |
| `admin` | administrator | Everything, plus the administration and the backups. May count everywhere without being entered. |
| `leitung` | manager | The whole web interface, but no administration. Is assigned as counting to every open inventory, like `zaehler`. |
| `zaehler` | counts only | Does not get onto the website, only into the counting app under `/app/`. Counts in every open inventory. |
| `eingeladen` | still waiting | Created without a password, the way the user form does it with a mail server as long as “Set the password yourself” stays unticked. Shows as “invited” in the user list. On its page “Send invitation again” sends the mail with the link through which the person sets their password. The mail ends up in the log or in Mailpit (see DEPLOYMENT.en.md). |

That `zaehler` bounces off the website is not a fault but the purpose of the
role. The lockout page explains it.

Every account changes its own password: in the web interface through the
account menu, in the counting app likewise. For `zaehler` the app is the only
way. After that the new password applies to this account, and every other
device signs in again with it. With `--reset` the four accounts come back, the
first three with the password from above.

The demo brings no passkeys: a passkey belongs to a device and only comes
into being there. Every account creates its own through the account menu
(“Passkeys”), on `localhost` even without HTTPS. First the page asks for the
password from above, and so does the counting app, and once more before a key
is removed. Removing a passkey signs the account out on every other device, on
the website at once and in the counting app within the hour at the latest. The
device on which it is removed stays signed in.

Every account has an address under `example.org`. Whoever changes, deactivates
or deletes one sends the person a mail saying what changed and by whom. One
also comes for a new password and for every passkey added or removed. The
mails end up in the log or in Mailpit (see DEPLOYMENT.en.md). Every account
changes its address itself through the account menu, in the counting app too,
or `admin` changes it: a mail with a link first goes to the new address, and
the address applies once someone has confirmed it. One thing to try: change
the address of `zaehler` in the app and open the link from Mailpit.

What the mails say, `admin` changes in the administration
under **Organisation → Mail texts**. The demo ships the supplied texts. One
thing to try: change the text of the invitation, look at the preview at the
bottom of the page, then press “Send invitation again” on the page
of `eingeladen`. Per mail you can also switch off that it names who triggered
it. Without a mail server (`DEBUG` off and no `EMAIL_HOST`) the invitation and
“Forgot your password?” disappear from the pages, and a new account needs a
password right away.

## What is ready

### Sites

| Site | State |
| --- | --- |
| Cinema North | Fully maintained: four areas, nine locations, about 70 articles, count lists with pack defaults. At one location nothing is expected. |
| Cinema East | Fully maintained but cut differently: two areas, five locations, its own article groups and its own names for the same products. |
| Cinema West | Empty. This site is set up in the demo itself, see “Setting up a site”. |
| Ice cream workshop South | A site without a merchandise management system: five own articles without numbers, two locations. The ice cream is the same product as in the cinemas, here in kilograms instead of pieces. |

That the two maintained sites name the same product differently and put it into
different article groups is deliberate. That is exactly what the product is
there for, holding them together across the sites.

### The catalogue

About 80 products in fifteen product families, plus seven prepared items. Every
case the application knows is represented:

- Products in pieces, in kilograms and in litres, with one or two packs each.
  Only the chocolate bar has three: single, in a display box of 20 and in a
  box of 40. Its packs can be dragged into another order. The display box is
  ticked on no counting list and so counts nowhere.
- Weighable products with containers and tare, such as loose sweets and nacho
  chips.
- Prepared items with a recipe: a portion of nachos resolves into chips, sauce
  and bowl on closing. “Popcorn sweet loose” goes onto the scale as a whole and
  spreads over three products. “Pick and mix sweets” and “Syrup mix house lemonade”
  state their recipe as shares in percent, the second one from syrups in
  litres that carry their weight per litre.
- Three inventory types: monthly, annual and cup inventory. The annual
  inventory holds what is counted only once a year, such as wine and cleaning
  agents. Only Cinema North carries reusable cups, and they count in the cup
  inventory.
- Cinema East counts the white wine every month, Cinema North only once a
  year. On the product's page it therefore stands as “mixed”, with the sites
  per type.
- The same cup twice at Cinema North: “Cup 0.4 l” under 101607 and “Drinking
  cup 0.4 l” under 101613, as two products and expected at the same locations.
- Articles in all four states: active, inactive, discontinued and not countable.
  The last of these lie in the family “Nicht zählbar”, where deposits and
  vouchers belong.

### Inventories

The cut-off dates hang on the day of the build, so the demo does not age. Built
in September 2026 it looks like this:

| Cut-off date | Types | Status |
| --- | --- | --- |
| 12/31/2025 | monthly, annual and cup inventory | closed, both sites |
| 05/31/2026 | monthly inventory | closed, both sites |
| 06/30/2026 | monthly inventory | closed, both sites |
| 07/31/2026 | monthly inventory | closed, both sites |
| 08/24/2026 | cup inventory | only Cinema North, because only there are reusable cups. Counted, without target stock and with the difference by hand, locked, waiting for closing |
| 08/31/2026 | monthly and cup inventory | **running**: Cinema North is fully counted, Cinema East has started |
| 12/31/2026 | monthly, annual and cup inventory | in preparation, both cinemas and the workshop, target stock imported or entered by hand |

Which days they are for you is stated by the command at the end of its output.

## A tour

### The running inventory

The start page is the inventory list. The second run from the top is the open
one, above it stands the one in preparation. The tabs in the header jump between the sites and the tab “Total”, which shows the
difference per product across both sites.

- **Cinema East** is at the beginning. On the detail page you see “x of y counted”
  per location, and exactly one location is finished.
- **Cinema North** is fully counted. The button “Review” leads into the review
  list, with a search, article groups to collapse, the difference per article
  and a note field per row. “Update values” fetches the current state without
  rebuilding the page. As long as something has arrived, closing stays blocked.
  At the cola syrup “Target counts” is ticked, with a note saying why: it is
  still in the dispenser. At closing its target stands as the result.

The tab “Total” shows the difference per product across both sites and beside
it per site. In the **closed** runs, for exactly two products the differences
run against each other: what is missing at one site lies in surplus at the
other. That is the typical case of a goods movement between two sites. In the
running run the target stock is the file from inventory management, and the count
deviates from it in many places. There the number “opposing” says little yet.

### Closing and the clarification

Closing Cinema North is the most interesting part of the demo. After the question
“really close?” comes the clarification, and **every one** of its seven blocks
has something to ask here:

1. **No longer active, but counted.** Four articles, two of them discontinued.
   Three were still active when counted and left the range only afterwards, the
   way such rows arise in the app. For one the tick is blocked: the merchandise
   management system gave the cones a new number, and the new record carries
   the product. The row says which one.
2. **Counted unexpectedly.** What was added in the app. The question is whether
   it should be in the count list of this location in future.
3. **Expected, but nothing entered.** Individual packs that stayed open.
4. **Not counted at all.** Articles without any entry. Here an article can be
   set inactive, discontinued or switched to another inventory type, say the
   annual inventory.
5. **Counted, but without a target stock.** Three articles that the file from
   inventory management does not know: one from the range and two from block 1
   that are no longer active. Without an answer they would freeze without a
   difference.
6. **Counted, but in no type of this inventory.** The white wine that somebody
   counted along in the month, and the two annual products from block 1. For the
   white wine the page asks whether the answer applies to this article only or
   to the one of Cinema East too.
7. **Weighed in a container from elsewhere.** A bowl that belongs to the other
   site and still stood on the scale.

Without a single tick, nothing further happens. Closing goes ahead anyway.
Afterwards there is the PDF, and the review list is over. Whoever wants to see
it again rebuilds the demo with `--reset`.

The **cup inventory** of Cinema North is waiting for closing as well. There the
clarification asks about the type: the large reusable cup stayed uncounted and
can be switched to the monthly or the annual inventory. The disposable “Cup
0.4 l” was counted in the app but belongs to the monthly inventory. The block
“Counted, but in no type of this inventory” offers to take it into the cup
inventory. Because Cinema East carries the same cup, the question comes whether
that applies there too.

The cup inventory has no target stock, the difference comes **by hand**. Its
review list therefore shows a field for the difference from the merchandise
management system instead of target and difference. For the two reusable cups
one is already entered, for the small one with a note. The cup added in the
app is still waiting for its. On the inventory's page and above its review list the
difference by hand can be switched off again. What has been entered stays.

The cup inventory is **locked** too: counting there has stopped, reviewing has
not. The button with the lock on its page therefore reads “Open”. Open, it reads
“Lock”. Closing works either way.

### Preparation and target stock

The annual inventory at the end of the year is still in preparation. Only there
can be seen what disappears later:

- the **upload of the target stock**, as often as you like. The file uploaded last
  counts, and no remainder of the previous one stays behind.
- the **clarification** of what the file leaves open.
- the **maintenance by hand**: the target per article for entering, for sites
  without a file or for the article it does not know. A target that already
  stands is locked until “Edit” releases the row.
- **Change types**: a forgotten inventory type is added with its lines, a
  deselected one takes its empty lines along.

After that, “Start counting” releases the inventory to the app. From then on the
counting apps hold their lines, and the master data should no longer shift
underneath them. That is why the clarification is over then. Back it goes
through “Back to preparation”, and the counted quantities stay in place.

The clarification shows exactly what the file left open:

- an article number that does not exist at the site,
- a number that belongs to an inactive article,
- one that belongs to a discontinued one,
- in Cinema North the cones' old number,
- an article the file names differently from the site.

Not among them is the tomato juice of Cinema North: discontinued, and the file
lists it with a stock of 0. There the merchandise management system and the
site agree, the target is saved silently, and nobody is asked. At Cinema East
the file lists it with a stock of 9, which is why it is the discontinued
article in the clarification there.

The file still lists the cones' old number. The row is discontinued, and the
site now carries the same product under the new number. The row names it, and
“Add to 101404” adds the stock to its target. After that the old number counts
as the new one: whoever uploads the file again finds the stock there right
away, without a question, and the old article's page says which one it counts
as.

Every row has a button of its own. “Match again” picks the clarification back
up without your having to upload the file again.

Cinema East has entered the article the file does not know by hand: under
“Maintain by hand” it stands under “With target” with the note “by hand”. In
Cinema North it was left open and stands in the clarification under “Active, but
without a target”.

The same file also sits in the running inventory of Cinema North. There the
clarification is no longer reachable, but its consequences are: on closing, the
block “Counted, but without a target stock” asks about the article the file does
not know.

### A site without a merchandise management system

The ice cream workshop South supplies the cinemas with ice cream and runs no
merchandise management system. Its five articles have the source “own” and no
number. Two of them are the same product as in the cinema: “Vanilla ice cream”
stands in Cinema North in pieces under a WinTICKET number, in the workshop in
kilograms, with the conversion in between. That is how the sites hang together
through the product without any system knowing both. The workshop takes part
in the annual inventory that is still being prepared: without a file, its
target stock is entered by hand for the four countable articles. On “Maintain by
hand” every row there says “by hand”, and the clarification has nothing to ask.
The fifth, the waffle crumbs, stands under “Not countable” by mistake (see “Undo
a mistake”).

### The counting app

Sign in under `/app/`, preferably as `zaehler`. The app shows the running
inventory, in it the locations, and per location the count list in the order of
the shelf.

What there is to see there:

- Packs side by side. At the counters the usual replenishment stock is shown as
  a reference (“Expected: 24”), in the store room it is not. Only whoever
  replenishes keeps a replenishment list. About a quarter of the rows have such
  a default.
- The scale: one weighing per container with its own tare. Choosing the
  container is mandatory. “No container” stands for the zeroed scale.
- Prepared items that are counted as a portion.
- A button per row to remove it, and the search to add unexpected finds.
- A location at which nothing is expected: the “Promotion table” in the lobby
  of Cinema North. What stands there changes with the film, and nobody keeps a
  count list for it. Area and location appear in the app anyway, with “nothing
  expected” instead of a progress. What stands there after all is added
  through the search.
- “+ add” and “− off” beside a number: a second find is added, a crate
  counted twice is taken off again, without doing sums in your head. It does
  not go below zero.
- The refresh button at the top right also fetches the master data anew.
  Whoever creates a location or an article in the web interface while the app
  has already loaded the inventory sees it after pressing it. The sync in the
  background only brings the numbers.
- One tab per inventory type, as far as the location has lines for it: in the
  beverage store of Cinema North “Monthly inventory” and “Cup inventory”.
  Cinema East carries no cups and sees no second tab there. What belongs to a
  type the inventory does not count gets no line. Whoever wants to count it
  anyway fetches it through the search, which adds its type. It then stands in
  the first tab like any other unexpected find. In the demo that is the white
  wine at Cinema North.
- The **review list** above the areas of an inventory: per article the
  difference large in its colour, below it actual and target, with search and
  the filters “Deviating” and “Uncounted”. The pen opens the note, and the ×
  beside it only stands where there is one. In the cup inventory of Cinema
  North the difference is a field there, for `zaehler` too. `leitung`
  and `admin` can switch it on and off. They also set “Target counts” there, a
  button on every row with a target. At the cola syrup it is already pressed,
  and `zaehler` only sees the notice there.
- The locked cup inventory: in the list it carries a lock, and opened it shows
  the review list right away, without areas and search. `leitung` and `admin`
  see the button there that releases counting again.

The app works offline and syncs as soon as it has a network again. Whoever wants
to see that switches the browser to “offline”, keeps counting and switches back.

### Report, PDF and history

The four closed runs are available as frozen results. With them you can look at:

- the result list per inventory, with actual, target stock and difference,
- the PDF per site and across the whole run. The one across the run starts with
  the product families alone, laid out like the blocks behind them, “Nibbles”
  among them in portions (“port.” on paper), and names the page with their
  products,
- the report across several periods and sites,
- how the same product develops over the months, because the differences travel
  from run to run.

The annual inventory from December counts all three types, so also the products
that are not counted monthly. Comparing it with a monthly inventory shows the
difference.

### Browsing the catalogue

Under “Master data” are the lists that carry the master data:

- **Products** with unit, family, packs and the sites that carry them. Two
  products can be merged if it turns out they are the same. And a product can
  be split if it turns out there are two in it. The articles of the sites
  concerned then move to a new product, together with their packs.
- **Prepared items** with their recipe.
- **Containers** with tare, assigned to a site or to a single product.
- **Inventory types** with their order, which is also the order of the tabs,
  and the active articles per site. The cup inventory has some at Cinema North
  only. The number of articles leads to the product list, filtered by the type.
  The white wine stands there under the monthly and the annual inventory, and
  the filter “mixed” finds it alone.
- **Product families** across several dimensions. “Soft drinks”, for instance,
  brings bottles and syrup together and totals in litres. What a bottle holds
  and weighs stands as a conversion on the product: “Cola 0.5 l” with 500 ml
  and 520 g per piece. The scale and the family's total compute with it.
  Two families stand above others: “Drinks” groups soft drinks, juices and
  water, beer and wine and totals in litres, “Snacks” the five families from
  popcorn to savoury in kilograms. Review list and report with its PDF show
  them as a tree: collapsed only the top level, every family with the total
  over everything below it. The sheet of a single inventory, by contrast,
  groups by article group and article number, the way the merchandise
  management system lists its articles. “Nibbles” below it totals in **portions**, the
  unit only a family carries: the chocolate bar is one portion, the loose
  pretzels are 50 g per portion.
- **Articles per site**, with a search over number, name, product and article
  group.
  The hits are ordered by how well they match. Whoever types “popcorn” at
  Cinema North sees “Popcorn salt” and “Popcorn sugar” first and only behind
  them the “Coconut oil”, which merely sits in the same article group. A click
  on a column header sorts by that column instead.
  Two articles at Cinema East count in a different unit than their
  product: the nacho chips in grams instead of kilograms, the coffee beans in
  bags of one kilo each. The site gets target stock and result in its unit, the
  evaluation converts into the unit of the product. A closed result keeps the unit
  it was counted in. Cinema East has counted the coffee beans in bags only since the
  running inventory, the closed ones show them in kg.
- At the location the **count list** with two tabs: the order to drag and the
  article selection. The default quantities sit there per pack and are
  maintained at the counters, empty in the store room. Whoever changes the order
  sees it saved immediately.
  A button above the column selects all packs of the list or deselects them again.
  A newly added article comes with all its active packs ticked, but without
  the tick “Weighing”, unless there is nothing but the scale for it. “Save”
  does not accept a row without any pack, because the article would get no
  line there in the inventory. The list is divided by inventory type, in the
  beverage store of Cinema North into monthly, annual and cup inventory.
  Dragging stays within a section. Whoever puts the white wine there (this
  article only) into the monthly inventory finds it at the end of that section
  afterwards.

Ten maintenance paths that can be tried out with the demo data:

- **Merge two products.** After the first import (see below) Cinema West carries
  “Syrup cola 10 l” as a product of its own, although it is the “Cola syrup” of
  the other sites. On the product's page: “Merge”, choose the other one, decide
  which remains. “Preview” shows what happens to the packs, “Merge for good”
  carries it out. The articles of Cinema West then hang on the remaining
  product.
- **Combine articles created twice.** At Cinema North open the article 101607
  “Cup 0.4 l”, then “Merge”, and choose 101613 “Drinking cup 0.4 l” among the
  articles of the site. The same works on the page of the product. Because
  Cinema North carries both, the preview asks for every expectation of the
  drinking cup whether it is taken over. Afterwards 101613 is discontinued and
  counts as 101607. Nothing changes about the cup itself. Both stood at the same
  locations, so the default quantities there are added up. Whoever then imports
  the target stock again in the prepared annual inventory of Cinema North sees
  the stock of 101613 added to the cup. Whoever first switches the box of
  “Cup 0.4 l” to inactive on the product's page sees it highlighted in the
  preview, because the box of the drinking cup would be absorbed into it.
  Nothing is written before “Activate again” or “Leave inactive”.
- **Create an inventory type.** Under Master data → Inventory types create a
  new type, say “Quarterly count”. On the page of an article group the selection
  above puts all its articles into the type, on the page of a product all
  articles of the product. On a single article the selection asks whether it
  applies to that article only or to the whole product. “New inventory” then
  offers the type, and only the sites with active articles in it can be chosen.
- **Split a product.** “Ice cream cone” is carried by both sites. On the
  product's page: “Split”, move the articles of Cinema East to the other side,
  name the new product. Both halves keep packs and count lines.
- **Change the unit of a product.** “Spray cream” is carried in grams. On the
  product's page “Edit”, set the unit to kg: packs and recipes are converted.
  The sites keep counting in their unit, their results stay. Whoever switches
  to pieces instead has to give the factor. Not for the “Cola 0.5 l”: it knows
  its piece (500 ml), and the change to pieces uses that.
- **Change the product from the article.** At Cinema North open the article
  “Cola 0.5 l”, then “Edit”. Under “Settings of the product” stand its name,
  its family, the weighing and the conversions. The note
  above names Cinema East, because the details belong to the product and apply
  there just the same. The unit stays on the product's page.
  On the page of the article itself the order of the packs can be dragged,
  as on the product's page. It too applies at every site.
- **Set a pack inactive.** On the page of the product “Cola 0.5 l” switch the
  crate to inactive. In the count list of the beverage store at Cinema North its
  default then stands in grey with “inactive”, and a new inventory gives it no
  line. “Save” now asks for another pack there, otherwise the cola would get no
  line in the store room at all. Whoever then edits the article “Cola 0.5 l” of
  Cinema North and enters a crate of 24 under “Packs” gets the row back marked.
  Nothing is saved until “Activate again” or “Leave inactive”. A new article for
  the cola asks the same, say at Cinema West before the import. Cinema West's
  import, on the other hand, brings the crate back, because its file carries it.
  The plan says so beforehand at the cola, and whoever does not want that takes
  the crate's line out of the field there.
- **Split or merge a location.** At Cinema North “Counter left” stands beside
  “Counter right”. On the location's page both can be merged. The count list is
  appended. “Split” goes the other way. The same exists for product families,
  for instance to separate “Confectionery” into loose goods and bars.
- **Copy a location.** On the page of “Counter left” at Cinema North: “Copy”.
  The copy gets the same count list, in any area and under a name of its own.
  Whoever chooses an area of Cinema East sees in the preview which articles are
  not carried there. They are missing from the copy.
- **Undo a mistake.** The waffle crumbs of the ice cream workshop stand under
  “Not countable” because someone clicked the wrong button. On the product's
  page “Bring back” takes them out again: into the family “Ingredients”, and
  their article is active again and stands in the raw materials store again.
  Family, status and place were remembered on the way in.

### Setting up a site

Cinema West is empty. This is how it becomes a countable site, and that is at the
same time the path a real new site takes:

1. **Import articles.** Master data → Sites and locations → Cinema West → “Import”, then
   upload `server/demo/exports/artikel_kino_west.csv`. The plan shows every
   line as new. For most of them it suggests the matching product from the other
   sites, because it already exists there. The import only writes after you
   apply it.
2. **Create areas and locations**, such as “Counter” with “Counter left” and
   “Store room” with “Beverage store”.
3. **Fill the count lists.** At the location, select in the “Articles” tab what
   sits there, and check the packs per row. The active ones are already ticked.
4. **Create an inventory** for Cinema West, choosing type and cut-off date. With
   the box “Set the counters right here” `zaehler` comes along at once, without it
   the counters are assigned on the inventory afterwards.
5. **Upload the target stock** during preparation:
   `server/demo/exports/sollbestand_kino_west.xlsx`, the count sheet as
   WinTICKET produces it, or the same figures
   as `sollbestand_kino_west.csv`. One number in it is unknown
   to the site and becomes a question. The report counts the deposit articles and
   skips them.
6. **Start counting.** Only then does the inventory appear in the app.

### A second import

`server/demo/exports/artikel_kino_ost.csv` is the case that occurs more often
in operation: a site is maintained, and inventory management delivers a new
file. Only what needs a decision makes it into the plan. All kinds are
included:

| Kind | What the file holds |
| --- | --- |
| new | Two numbers the site does not know |
| discontinued | An article that could be brought back |
| renamed | The same number under a different name |
| conflict | A different article group, and twice a quantity per pack that the product does not carry |
| missing | Two numbers the site carries and the file no longer knows |

As long as a conflict is open, the button stays blocked. With the choice the row
moves into the block “Resolved conflicts”. The rows the site carries exactly like
this are not in the plan but only in the message above it.

The export leaves out one article group entirely (“290 Purch - Miscellaneous”). That is
exactly how inventory management works: you pick the article groups, and they
come in full. The three articles in it therefore stay untouched, and the message
above the plan says so. The block “Missing from the file” only shows the two
numbers from article groups that really were exported.

Every block is structured by article group. Both levels expand and collapse, and
in the head of an article group one button sets all of its rows at once.

### Your own logo

Top left there is the supplied logo, the blue one. It belongs to the
version **Default** under **Organisation → Appearance and operator**, together with the
name “CrossTally”, the empty operator, the empty legal notice and the supplied privacy text. The version cannot be changed,
only copied, and the demo ships it just as a fresh business finds it.

A logo is ready for trying it out: `server/demo/images/logo_demo.png`, the
same tally mark in teal. The way: tick the row “Default”, **Create a copy**,
open the copy, give it a name of its own under “Name of the application” (say
“Cinema stocktake”), upload the image (`#0f766e` fits as its background) and
save, then tick the copy in the list and **Choose as the active version**. After
that, name and logo appear top left, in the browser tab and on the icon of the
counting app.
Back to the blue logo means: choose Default. In the same copy you can enter a
legal notice, say an invented address with one line per item. Then every page
footer and the account menu of the counting app link to it. You can also enter
an address for feedback there, say `rueckmeldung@example.org`. The domain is
reserved for examples, so a mail to it reaches nobody. Then the account menu
of the website and of the counting app offers “Report a problem or
suggestion”, and the mail program opens with version, page, account, time and
browser in the text. `manage.py demo --reset` takes versions of your own away as well.

One file is enough. The favicon, the icon for iOS and the four sizes for the
home screen are derived from it. Only a square PNG of at least 512 x 512 pixels
is accepted, and what is checked is the content of the file, not its name.
Whoever uploads a renamed text file is told so.

### Administration and backups

As `admin` there is the administration at the address from `ADMIN_URL`, and in
it the buttons for backups. A backup of the demo is a good way to record the
state before trying something that cannot be taken back. Restoring, however,
works only while nothing is being counted. The demo has three running counts:
send each “Back to preparation” first, or close it. The band comes back with
the state, a state from a real operation takes it away. The logo lives in the
database and is part of it. After a restore everyone is signed out, `admin` as
well: on the website at once, in the counting app within the hour at the
latest. If a `BACKUP_KEY` is set in the `server/.env`, the
backup is written encrypted. During development the key is optional, in
operation it is required. With a key set, the administration restores only
encrypted states. An older one without encryption is encrypted first
with `manage.py backup store` (see DEPLOYMENT.en.md, “Restoring”).

Under `/admin/` there is no admin but a decoy with the same form. Sign-in attempts
land in the administration under “Sign-in attempts at the fake admin”.

## The files

They live in `server/demo/exports/` and are invented exports in the format of
inventory management. The same files with English descriptions are beside them
in `server/demo/exports/en/`.

| File | What for |
| --- | --- |
| `artikel_kino_west.csv` | First import, sets up the empty site |
| `artikel_kino_ost.csv` | Second import into a maintained site, with all kinds |
| `sollbestand_kino_nord.csv` | Target stock of the running inventory, already imported |
| `sollbestand_kino_ost.csv` | The same for the second site |
| `sollbestand_kino_west.csv` | For the inventory that comes into being after the import |
| `sollbestand_kino_west.xlsx` | The same as the count sheet WinTICKET produces |

Next to them, `server/demo/images/` holds a logo to upload (`logo_demo.png`)
and the SVG file it was rendered from. It is the tally mark of
the placeholder in different colours.

The article files carry the structure that inventory management outputs: an
intermediate line per article group, below it one line per article, and below
that an info line “gekauft in … zu …”, in the German wording of the merchandise
system, also in the English files. From that the import reads the pack size.

They also carry the spellings that inventory management produces: “Kiste(n)”,
“Sack(Säcke)”, “Karnister”, “ltr”, “gramm”. The import turns those into the
site's own terms (“Kasten”, “Sack”, “Kanister”) and into the five permitted
units. Whatever cannot be recognised as a unit, such as “Becher”, is counted,
so it is kept in pieces. For products counted in pieces the pack field always
carries “Einzeln = 1” as well, because what stands on the shelf is an opened
pack.

The target stock files are plain: `article number;stock;name`. The name is
optional. If it differs from the one at the site, the clarification says so.
The count sheet (`sollbestand_kino_west.xlsx`) carries the same figures the way
WinTICKET prints them: a header row per page, a warehouse row, and behind the
system stock the empty columns to fill in. It is built from the CSV
(`uv run python -m demo.count_sheet`), and a test keeps the two together.

## Starting over

```bash
uv run manage.py demo --reset
```

That deletes everything the demo creates and builds it anew. Beforehand the
command names what is in this database and asks. **Every site, every product and
every inventory is deleted in the process, including ones that only came in
while trying things out.** That works only in a database that holds the demo,
recognisable by its marker. In any other the command refuses with `--reset`
too, as soon as anything is in it. Without `DEBUG`, `--demo-server` belongs to
it. The master data of the application stay in place: the base units, the
container “Ohne Behälter” and the product family “Nicht zählbar” come from a
migration and do not belong to the demo.

## For developers

The demo lies in the repository as an app and not as a finished database. The
reason is maintenance: a dump ages silently as soon as a model changes.

| File | Contents |
| --- | --- |
| `server/demo/catalog.py` | Product families, containers, products, packs, prepared items |
| `server/demo/houses.py` | Sites, areas, locations, articles per site, count lists |
| `server/demo/inventories.py` | The runs, the counting and the deliberate cases for the clarification |
| `server/demo/users.py` | The four accounts |
| `server/demo/branding.py` | The version to try out: logo, legal notice, address for feedback |
| `server/demo/count_sheet.py` | Builds the count sheet from the CSV |
| `server/demo/files.py` | The files and the way to them |
| `server/demo/saving.py` | Every record goes through the model's check |
| `server/demo/management/commands/demo.py` | The command `manage.py demo` with its switches |
| `server/demo/names_en.py` | The same names in English, with the table for the import files |
| `server/demo/builder.py` | Puts it together, clears it away again and recognises a stock that does not come from the demo |
| `server/demo/models.py`, `state.py` | The marker by which the interface recognises the demo |
| `server/demo/tests.py` | The contract, see below |

The build goes through the services of the application, not past them. The
inventories come into being with `inventuren_anlegen`, the target stock comes
through `sollbestand_einlesen` from a file, and closing happens
with `Inventur.abschliessen`. If something changes there, the build breaks.

`server/demo/tests.py` ties the demo to the application. The tests do not check
whether particular numbers come out, but whether the demo shows what it
promises: that every block of the clarification has a question, that the book
stock clarification knows its cases, that the closed runs carry results and that
the supplied files can be imported. They run along in the CI.

The pass through the counting app in a real browser (`app/e2e/`) builds on the
demo too: it signs in as `zaehler` and counts in the running inventory, takes
over the sign-in of `leitung` from the website and weighs at the sweets shelf of
the counter of Kino Nord. If any of that drops out of the demo, it breaks.

**Whoever adds a feature adds it to the demo.** A new field belongs in one of the
blueprints, a new case in the tables of `inventories.py`, and the test for it
says that it really does occur in the demo.
