/**
 * How far is counted, per area, location and article.
 *
 * Pure functions over the count positions: they sum up what the interface
 * shows as colour and number. Without database and without network, so
 * they can be checked.
 */

/**
 * A position counts as counted as soon as someone has entered something.
 *
 * For weighed goods that is the first weighing. A zero would be the claim
 * that the shelf is empty, and someone has to write that down explicitly.
 */
export function isCounted(position) {
    if (position.wird_gewogen) return (position.wiegungen ?? []).length > 0;
    return position.eingabemenge !== null && position.eingabemenge !== undefined;
}

export function progress(positions) {
    return progressFrom(positions.length, positions.filter(isCounted).length);
}

/** The same state from two numbers, say those the server brings per inventory. */
export function progressFrom(total, counted) {
    return {
        total,
        counted,
        open: total - counted,
        // Three states because they differ in urgency: nothing started, in the
        // middle, done.
        state: counted === 0 ? "empty" : counted === total ? "done" : "partial",
    };
}

/**
 * How far an inventory is, for the start page.
 *
 * From the storage as soon as its rows lie here: then what was entered in a
 * dead spot and is not out yet counts too. Before that, on the first start,
 * from the numbers the list of inventories brings from the server.
 */
export function inventoryProgress(inventory, positions) {
    if (positions.length) return progress(positions);
    return progressFrom(inventory.anzahl_positionen ?? 0, inventory.anzahl_gezaehlt ?? 0);
}

/** 'empty' names the keys that get a group even without a row. */
function groupBy(positions, keyOf, describe, order, empty = []) {
    const groups = new Map(empty.map((key) => [key, []]));
    for (const position of positions) {
        const key = keyOf(position);
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(position);
    }
    const sequence = order ?? ((a, b) => a.name.localeCompare(b.name, "de"));
    return [...groups.entries()]
        .map(([key, rows]) => ({
            ...describe(key, rows),
            ...progress(rows),
            rows,
        }))
        .sort(sequence);
}

/**
 * The locations nothing is expected at. No row names them, only the master data.
 *
 * Where a location has rows, they say where it lies. The master data is
 * fetched once per inventory and may be older than the rows: a location
 * moved to another area since then would otherwise stand in both.
 */
function withoutRows(positions, places) {
    const used = new Set(positions.map((position) => position.location_id));
    return places.filter((place) => !used.has(place.id));
}

/**
 * The areas of a house: as they occur in the positions, plus those of the
 * locations nothing is expected at ('places', the locations of the master data).
 *
 * An empty location belongs to the round too. What stands there nobody
 * expected, and it can only be added where the way leads to the location.
 */
export function areas(positions, places = []) {
    return groupBy(
        positions,
        (position) => position.ort.bereich,
        (name) => ({ name }),
        undefined,
        withoutRows(positions, places).map((place) => place.bereich),
    );
}

export function locations(positions, area, places = []) {
    const own = positions.filter((position) => position.ort.bereich === area);
    const empty = withoutRows(positions, places).filter((place) => place.bereich === area);
    const names = new Map(empty.map((place) => [place.id, place.name]));
    return groupBy(
        own,
        (position) => position.location_id,
        (id, group) => ({
            id,
            name: group[0]?.ort.name ?? names.get(id),
        }),
        undefined,
        empty.map((place) => place.id),
    );
}

/**
 * The articles at a location, per article all its rows together.
 *
 * In the order of the **counting list**: that is how the goods stand on the
 * shelf, and on the round nobody has to search. With the same position the
 * name decides.
 */
export function articles(positions, locationId) {
    const own = positions.filter((position) => position.location_id === locationId);
    return groupBy(
        own,
        (position) => position.artikel_id,
        (id, group) => ({
            id,
            name: group[0].artikel,
            unit: group[0].einheit,
            sortOrder: Math.min(...group.map((position) => position.sortierung ?? 10000)),
            unexpected: group.every((position) => position.unerwartet),
            // The inventory type it is counted in. A storage from before
            // 10/2026 does not know the field: then it is null, and the
            // article stands in the first tab.
            type: group.find((position) => position.inventurart != null)?.inventurart ?? null,
        }),
        (a, b) => a.sortOrder - b.sortOrder || a.name.localeCompare(b.name, "de"),
    );
}

/**
 * The articles with the tab each stands in ('tab'), the number of a type.
 *
 * 'types' are those of the inventory in their order, as '{id, name}'. An
 * article of another type was counted on top, through the search. It
 * stands in the first tab that has rows here, so it does not open a tab
 * of its own. So does a row whose type is not known yet.
 */
export function inTabs(entries, types) {
    const counted = new Set(types.map((type) => type.id));
    const first = types.find((type) => entries.some((entry) => entry.type === type.id)) ?? types[0];
    return entries.map((entry) => ({
        ...entry,
        tab: counted.has(entry.type) ? entry.type : (first?.id ?? null),
    }));
}

/**
 * The tabs of a location with their count state: one per type with rows here.
 *
 * Separated only here, **after** sorting: the counting list carries every
 * type in one order, and every tab is an excerpt of it. If an article
 * changes type, it stands in the other tab right at its place on the shelf.
 * 'entries' come from 'inTabs'.
 */
export function tabs(entries, types) {
    return types
        .map((type) => {
            const own = entries.filter((entry) => entry.tab === type.id);
            return {
                key: type.id,
                name: type.name,
                entries: own,
                ...progress(own.flatMap((entry) => entry.rows)),
            };
        })
        .filter((tab) => tab.entries.length > 0);
}

/**
 * The article that follows this one in the list, or nothing if it is the last.
 *
 * "Next ›" goes along the round, as the goods stand on the shelf.
 * It stays in the article's tab ('inTabs'): the list someone came from
 * should also be the one they are led through. Whoever has no row here yet
 * (only just fetched through the search) has no neighbour either.
 */
export function next(entries, articleId) {
    return neighbour(entries, articleId, 1);
}

/**
 * The article before, the same sequence as with `next`, only backwards.
 *
 * Whoever notices on the round that they miscounted at the last shelf
 * should not have to go back through the list.
 */
export function previous(entries, articleId) {
    return neighbour(entries, articleId, -1);
}

function neighbour(entries, articleId, step) {
    const here = entries.find((entry) => entry.id === articleId);
    if (!here) return null;
    const sequence = entries.filter((entry) => entry.tab === here.tab);
    return sequence[sequence.indexOf(here) + step] ?? null;
}

/**
 * Searches by **name**, without regard to case.
 *
 * Nobody knows the article number by heart when counting. Having it in the
 * search only brought hits nobody searched for.
 */
export function search(entries, term) {
    const wanted = term.trim().toLocaleLowerCase("de");
    if (!wanted) return entries;
    return entries.filter((entry) => entry.name.toLocaleLowerCase("de").includes(wanted));
}

/**
 * What can still be added at a location, in the order of the search.
 *
 * Everything that has no row here yet, filtered by name. **Inactive**
 * articles stand behind all active ones: they are the exception, and
 * whoever types "Cola" searches first for the cola the house carries. At
 * the very back **discontinued** ones: delisted but found on the shelf. The
 * server names only those whose number and product are still free in the
 * house. Within the groups the order of the master data remains.
 */
export function addable(all, existing, term) {
    const present = new Set(existing);
    const open = search(
        all.filter((article) => !present.has(article.id)),
        term,
    );
    const discontinued = (article) => article.eingestellt === true;
    return [
        ...open.filter((article) => article.aktiv !== false && !discontinued(article)),
        ...open.filter((article) => article.aktiv === false && !discontinued(article)),
        ...open.filter(discontinued),
    ];
}
