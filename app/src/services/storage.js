/**
 * The offline storage. Everything the app shows comes from here, never
 * directly from the network.
 *
 * That way counting without reception is no special case but the normal
 * case: the interface writes into the storage and puts a job into the
 * queue, and the sync brings both together at some point.
 *
 * Numbers: what comes from the server carries its number. What arises here
 * gets a **negative** one. That way both can be kept in the same table and
 * told apart by the sign. As soon as the server names its number, it stands
 * next to it as 'server_id'.
 */

import Dexie from "dexie";

export const db = new Dexie("crosstally");

//: The marks per inventory up to when the sync has fetched ('sync/positions.js').
export const SYNC_PREFIX = "synced:";

//: The copies of the forms the app left behind unsaved ('draft_backup.js').
export const DRAFT_PREFIX = "draft:";

const TABLES = {
    // Single values: tokens, time of the last sync, signed-in user.
    state: "key",
    inventories: "id",
    positions: "id, inventur_id, location_id, artikel_id, server_id",
    // Locations, articles and containers of a house. They change rarely.
    masterData: "inventory_id",
    queue: "++number",
};

//: Since 10/2026: the review list per inventory, as last fetched ('sync/review.js').
const TABLES_WITH_REVIEW = { ...TABLES, reviews: "inventory_id" };

db.version(1).stores(TABLES);

// Since 10/2026 a row names the inventory type it is counted in
// ('inventurart'), and the list has a tab per type. Rows stored before name
// the old scope instead. The marks of the sync go, so the next sync fetches
// every row anew. The master data follows by itself: without the types it
// is fetched again ('counting.refresh'). The queue and the own rows stay as
// they are, so nothing counted is lost, and offline everything stays usable.
db.version(2)
    .stores(TABLES)
    .upgrade((tx) => tx.table("state").where("key").startsWith(SYNC_PREFIX).delete());

// Since 10/2026 the review list of an inventory lies here too, so it can be
// read in a dead spot. A table of its own: it is computed by the server over
// every device's counts, and the rows of the walk through the house stay as
// they are. Nothing to carry over, the list comes with the next fetch.
db.version(3).stores(TABLES_WITH_REVIEW);

/**
 * Turns a value into something the storage accepts.
 *
 * What comes from the interface sits in Svelte states, and those are
 * proxies. IndexedDB clones what it stores and fails on that with "could
 * not be cloned". The row then does not arise, and nobody sees why. That is
 * why nothing goes in unfiltered.
 */
export function asPlainData(value) {
    return value === null || value === undefined ? value : JSON.parse(JSON.stringify(value));
}

export async function remember(key, value) {
    await db.state.put({ key, value });
}

export async function remembered(key, fallback = null) {
    const row = await db.state.get(key);
    return row === undefined ? fallback : row.value;
}

export async function forget(key) {
    await db.state.delete(key);
}

/**
 * The next free number for a row that exists only here.
 *
 * Descending from -1, so it never overlaps with a server number.
 */
export async function nextOwnNumber() {
    const lowest = await db.positions.orderBy("id").first();
    return Math.min(0, lowest?.id ?? 0) - 1;
}

export function isOwn(number) {
    return number < 0;
}

//: Where the storage notes its last clearing ('clearAll'). Every window of
//: the app shares the storage, so this is where each learns of it. Until
//: 10/2026 the clearings were counted per window, in memory, and a second
//: window wrote the previous account's tokens back after a sign-out.
const CLEARING = "clearing";

//: Stands for the clearing this window found when it loaded, until that is read.
const AT_LOAD = Symbol("at load");

//: The clearing this window works on. Its state came from that storage, and
//: what it sends or writes belongs to that account. Only this window's own
//: clearing changes it ('clearAll'). Another window's leaves it behind.
let mark = AT_LOAD;

//: What the storage noted when this window loaded. Read right away, before
//: the interface loads anything.
const markAtLoad = db.state.get(CLEARING).then(
    (row) => row?.value ?? null,
    () => null,
);

/**
 * The clearing the next step of this window belongs to.
 *
 * Taken before anything is awaited ('api.js'): taken after the token was
 * read, a sign-out in between let the previous account's answer through.
 */
export function currentMark() {
    return mark;
}

/** What a mark stands for. Outside any transaction: it may wait for the first read. */
export async function markValue(taken) {
    return taken === AT_LOAD ? await markAtLoad : taken;
}

/**
 * Whether the storage is still the one 'expected' was taken on.
 *
 * Read inside the caller's transaction. A storage never cleared carries no
 * mark yet, and then there is nothing to tell apart.
 */
export async function markHolds(expected) {
    const noted = (await db.state.get(CLEARING))?.value ?? null;
    return noted === null || noted === expected;
}

function freshMark() {
    return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;
}

//: A sign-out the server did not hear ('signOut' in 'api.js'). The website's
//: session it should have ended may still be there, and the refresh token
//: still holds. The next start settles both before it takes anything over
//: ('fromSession'). The note names the token by its id and the CSRF value of
//: the moment, never the token itself. An empty value lets every session of
//: the website alone. One noted before 10/2026 is 'true'.
export const SIGN_OUT_OWED = "sign_out_owed";

/**
 * Clear everything away, on sign-out or on switching the account.
 *
 * Every table of the schema, not a list by hand: a table added later is
 * cleared without anyone thinking of it here. Including what still waits in
 * the browser to go to the account: a theme or language chosen without
 * network ('*_pending' in 'theme.svelte.js' and 'language.svelte.js'). The
 * next account on the device would otherwise inherit the choice, and its
 * own would never apply.
 *
 * The new mark is set before anything is awaited, and noted in the same
 * transaction as the clearing. What this window starts from now on belongs
 * to the cleared storage, what it or another window started before does
 * not ('api.guarded'). No window ever sees the storage empty and unmarked.
 *
 * A sign-out the server has not heard yet ('SIGN_OUT_OWED') is noted in the
 * same step and outlives every clearing: the session it should have ended
 * outlives it too. 'signOutOwed' is the note from now on, 'false' for none.
 * Left out, the note stays as it was. A function builds the note from the
 * storage that goes, in the same step: nothing of another window comes in
 * between what it reads and the clearing.
 */
export async function clearAll({ signOutOwed } = {}) {
    mark = freshMark();
    const noted = mark;
    await db.transaction("rw", db.tables, async () => {
        const owed =
            typeof signOutOwed === "function"
                ? await signOutOwed()
                : (signOutOwed ?? (await remembered(SIGN_OUT_OWED, false)));
        await Promise.all(db.tables.map((table) => table.clear()));
        await db.state.put({ key: CLEARING, value: noted });
        if (owed) await db.state.put({ key: SIGN_OUT_OWED, value: owed });
    });
    try {
        for (const key of Object.keys(localStorage)) {
            if (key.endsWith("_pending")) localStorage.removeItem(key);
        }
    } catch {
        // In private windows the access itself can throw. Then there was nothing to clear.
    }
}

/**
 * How many own numbers are not at the server yet.
 *
 * Jobs in the queue and open questions ('quantity_conflicts'), because a
 * question too holds a counted number. Signing out and a switch of account
 * clear the storage. Before that this has to be zero, or someone has said
 * explicitly that it may go.
 */
export async function unsentCount() {
    const questions = (await remembered("quantity_conflicts", [])) ?? [];
    return (await db.queue.count()) + questions.length;
}

/**
 * How many forms the app left behind unsaved, typed and never counted ('draft_backup.js').
 *
 * They are no jobs, and a sync sends none of them. But signing out and a
 * switch of account would delete what was typed in them, so they are asked
 * about like what waits for the server. Until 10/2026 they went without a
 * question.
 */
export async function unsavedCount() {
    return await db.state.where("key").startsWith(DRAFT_PREFIX).count();
}

/**
 * Ask the browser not to clean up the storage on its own.
 *
 * Without this request it counts as "best effort": if storage runs short,
 * the browser may delete it, including everything not sent yet. Whether it
 * agrees it decides itself. An installed app mostly gets it. A refusal or
 * a browser without the API changes nothing about counting.
 */
export async function requestPersistence() {
    try {
        return (await globalThis.navigator?.storage?.persist?.()) ?? false;
    } catch {
        return false;
    }
}
