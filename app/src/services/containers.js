/**
 * What can be weighed in, in the order in which one reaches for it.
 *
 * At the very top, without heading, what **weighs nothing** (tare 0,
 * "Without container"): it is possible with every product and often needed,
 * and at the top one finds it without searching. Then three groups, and the
 * selection shows them separately:
 *
 * 1. what is assigned to the **article** (the article's 'behaelter' list
 *    from the master data).
 * 2. the containers of **this house**: the bowl standing around here.
 * 3. the containers of **other houses**: borrowed. Whoever reaches there is
 *    asked before closing whether the container belongs to the article or
 *    the house.
 *
 * A container without a house that only hangs on other articles stands
 * nowhere: in the spray cream can there is never anything but spray cream.
 * The server checks the same rule when weighing. What is missing here it
 * would not accept.
 *
 * Pure functions, so they can be checked without interface. If an older
 * master data state does not know 'haus' yet, the rear groups stay empty.
 */

//: The headings stand as keys and are only translated when displaying
//: ('texts.js'). Here nobody knows which language currently applies.
export const GROUPS = {
    //: Without heading: the selection shows these vessels before all groups.
    without_tare: "",
    article: "container.article",
    house: "container.house",
    elsewhere: "container.elsewhere",
};

/** The groups with content, each as { key, title, containers }. */
export function containerGroups(article, allContainers) {
    const assigned = new Set(article?.behaelter ?? []);
    const all = allContainers ?? [];
    const others = all.filter((container) => !assigned.has(container.id));
    // In the order the server names on the article: first the assigned ones,
    // "Without container" last. First should stand what is mostly reached for.
    const byId = new Map(all.map((container) => [container.id, container]));
    const onArticle = (article?.behaelter ?? []).map((id) => byId.get(id)).filter(Boolean);
    const groups = [
        ["article", onArticle],
        // 'dieses' and 'anderes' are the API's words for this house and another one.
        ["house", others.filter((container) => container.haus === "dieses")],
        ["elsewhere", others.filter((container) => container.haus === "anderes")],
    ];
    // What weighs nothing, out of its group to the very top.
    const weightless = (container) => Number(container.tara) === 0;
    const withoutTare = groups.flatMap(([, containers]) => containers.filter(weightless));
    return [
        ["without_tare", withoutTare],
        ...groups.map(([key, containers]) => [key, containers.filter((c) => !weightless(c))]),
    ]
        .filter(([, containers]) => containers.length)
        .map(([key, containers]) => ({ key, title: GROUPS[key], containers }));
}

/** All selectable vessels in a row, for looking up and for preselecting. */
export function containers(article, allContainers) {
    return containerGroups(article, allContainers).flatMap((group) => group.containers);
}
