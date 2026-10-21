/**
 * What the article list of a location remembers: the open tab and the last
 * opened article.
 *
 * An inventory counts one or more inventory types, and each is a tab at the
 * location ('progress.js', 'tabs'). Whoever knows the monthly round finds
 * it unchanged, and what is only counted now does not lengthen it. A tab is
 * known by the number of its type.
 *
 * The state lies here and not in the page: it is torn down on the way into
 * the entry form, and whoever comes back should land in the same tab.
 * Remembered per location, so the next location starts at the front again.
 */

const state = $state({ locationId: null, tab: null });

/**
 * The open tab among those shown, as its number.
 *
 * The first one where nothing was chosen here, or where the chosen one has
 * no rows here (any more). An article of a type this inventory does not
 * count stands in the first tab too.
 */
export function tabOf(locationId, tabs) {
    const chosen = state.locationId === locationId ? state.tab : null;
    return tabs.some((tab) => tab.key === chosen) ? chosen : (tabs[0]?.key ?? null);
}

export function setTab(locationId, tab) {
    state.locationId = locationId;
    state.tab = tab;
}

//: The last opened article per location. The list scrolls there when coming
//: back. No state: nobody has to redraw on it, the list reads it once when building.
let last = { locationId: null, articleId: null };

export function rememberArticle(locationId, articleId) {
    last = { locationId, articleId };
}

export function rememberedArticle(locationId) {
    return last.locationId === locationId ? last.articleId : null;
}
