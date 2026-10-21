/**
 * The working state of an inventory, as all views see it.
 *
 * Loaded once, everywhere the same source: the views compute their grouping
 * from it themselves ('progress.js'). Displayed is always the state from
 * the storage: the network only refreshes it.
 */

import { SignedOutMeanwhile, guarded } from "./api.js";
import { containerGroups } from "./containers.js";
import { savedDrafts } from "./draft_backup.js";
import { inventoryProgress } from "./progress.js";
import { currentMark, db, remember, remembered } from "./storage.js";
import {
    fetchInventories,
    fetchReview,
    hasPendingJobs,
    fetchMasterData,
    inUse,
    fetchPositions,
    quantityConflicts,
    rejectedJobs,
    removeInventory,
    submit,
} from "./sync.js";
import { t } from "./language.svelte.js";

export const counting = $state({
    inventoryId: null,
    inventory: null,
    //: What is pending: the list of the own inventories. It stands here and not
    //: in the page so the background sync takes it along: whoever has the app
    //: open while someone creates an inventory would otherwise never see it.
    inventories: [],
    //: How far each of them is, per number, for the start page.
    progress: {},
    positions: [],
    masterData: null,
    //: The review list of the open inventory as last fetched, or null.
    review: null,
    pending: 0,
    //: Whether the last sync got through. That stands as an icon in the bar.
    //: A notice banner would shift the content, and in storage offline is the
    //: normal case, not worth a message.
    online: true,
    //: What the server rejected. That does not vanish by itself. It belongs
    //: told to someone, otherwise a counted value is missing in the end.
    rejected: 0,
    //: Rows in which someone else had already counted. Here someone has to
    //: decide what happens with the own number.
    quantityConflicts: 0,
    //: Typed but never adopted because the app died before. At the top stands
    //: a notice that leads back to the form.
    drafts: [],
    //: When was last really synced, the information in the sync window.
    lastSynced: null,
    //: If the inventory left the count on the way (closed, deleted or taken
    //: back into preparation): there is nothing more to do here, and the app
    //: goes back to the list. An open entry form stays first ('outOfCount').
    gone: false,
    //: The server no longer knows the sign-in: token expired or blocked. The
    //: queue waits. The app leads to the login, and nothing that was counted
    //: is lost in the process.
    signedOut: false,
    message: "",
    //: The open inventory left the count while its entry form was open. The
    //: form stays with a notice, so nothing typed is lost ('App.svelte').
    //: Leaving it leads to the list.
    outOfCount: false,
    //: Why the list came up: the open inventory left the count ('markGone').
    //: The key of its text and the inventory. Unlike 'message' it outlives
    //: the next sync (until 10/2026 nobody ever read it). It goes once another
    //: inventory opens or this one is counted again.
    notice: null,
    loading: false,
    //: Whether a sync has run through since the start, however it ended. The
    //: list says "nothing pending" only after that, not before the first answer.
    synced: false,
    //: Whether the state from the storage is there yet. The entry form builds
    //: its draft from it. Before loading an empty one would arise, and on
    //: confirming it would create new rows instead of changing the existing ones.
    loaded: false,
});

/**
 * Forget everything that belongs to the running inventory, on switch or sign-out.
 *
 * The counts of the account too: what waits, what was refused, the questions
 * and the last sync. A run on its way during a sign-out does not count anew
 * ('refresh'). Until 10/2026 they stayed, and the login form said an entry of
 * the previous account still waited, after its storage had been cleared.
 */
export function close() {
    counting.loaded = false;
    counting.online = true;
    counting.gone = false;
    counting.inventoryId = null;
    counting.inventory = null;
    counting.inventories = [];
    counting.progress = {};
    counting.drafts = [];
    counting.positions = [];
    counting.masterData = null;
    counting.review = null;
    counting.pending = 0;
    counting.rejected = 0;
    counting.quantityConflicts = 0;
    counting.lastSynced = null;
    counting.message = "";
    counting.notice = null;
    counting.outOfCount = false;
    counting.synced = false;
}

export async function open(inventoryId) {
    if (counting.inventoryId === inventoryId) return;
    counting.notice = null;
    // Another inventory: its form is not the one left out of the count, and
    // the empty master data below must not lead to the list ('App.svelte').
    counting.outOfCount = false;
    counting.loaded = false;
    counting.inventoryId = inventoryId;
    counting.positions = [];
    counting.masterData = null;
    counting.review = null;
    await loadFromStorage();
    await refresh();
}

/** What is in the storage works without reception too. */
export async function loadFromStorage() {
    counting.pending = await db.queue.count();
    counting.rejected = (await rejectedJobs()).length;
    counting.quantityConflicts = (await quantityConflicts()).length;
    counting.drafts = await savedDrafts();
    counting.lastSynced = await remembered("last_synced");
    counting.inventories = await db.inventories.toArray();
    const progress = {};
    for (const inventory of counting.inventories) {
        const rows = await db.positions.where("inventur_id").equals(inventory.id).toArray();
        progress[inventory.id] = inventoryProgress(inventory, rows);
    }
    counting.progress = progress;

    const id = counting.inventoryId;
    if (id === null) return;
    counting.inventory = (await db.inventories.get(id)) ?? null;
    counting.positions = await db.positions.where("inventur_id").equals(id).toArray();
    counting.masterData = (await db.masterData.get(id)) ?? null;
    counting.review = (await db.reviews.get(id)) ?? null;
    counting.loaded = true;
}

//: Whose review list was asked for and has not come yet ('refresh'): an
//: inventory's number, or 'NEXT_OPEN' when it was asked before any was open.
//: A locked inventory opens on its review list, and the list may ask first.
//: That wish waits for the inventory that opens. Bound to the number, a
//: wish fetches no other inventory's list, and it goes once another is open.
const NEXT_OPEN = "next";
let reviewWantedFor = null;

/** Whether the waiting wish is for this inventory. */
function reviewWantedHere(id) {
    return id !== null && (reviewWantedFor === id || reviewWantedFor === NEXT_OPEN);
}

//: A sync was asked for while one was running: once more right after it.
//: Otherwise a language switch or a newly opened inventory would wait for
//: the next round, up to twenty seconds, with a list in two languages. Not
//: for the clock and the browser ('background'): their next round comes
//: anyway. Until 10/2026 every tick during a slow run asked for one more, and
//: on a slow connection one sync followed the other without a pause.
let againWanted = false;

/**
 * Submit what lies here and fetch what changed, for **all** running
 * inventories, not only the open one.
 *
 * Whoever did not tap a house before the walk into storage should still be
 * able to count there, without network. That is why count positions and
 * master data of every inventory one is assigned to lie here. Normally that
 * is one house, at worst all. That is a few hundred kilobytes the first
 * time, afterwards only what changed. The open one comes first, so its view
 * does not wait for the others. On the side the start page thus has the
 * progress of every house, including what is not sent yet here.
 *
 * The list comes along every time: an inventory created while the app lies
 * open would otherwise never appear.
 *
 * **The master data comes once per inventory**, and anew only where someone
 * asks for it ('masterData', the button in the header bar) or the server
 * rejected a new row built from it ('outdated', 'sync/inventories.js'). It changes
 * rarely, and fetching it every twenty seconds would cost more than it
 * brings. But a location or an article created after the first fetch would
 * otherwise only arrive once the inventory was cleared away. If the fetch
 * fails, what lies here stays.
 *
 * **The review list comes only on request** ('review'): when its page opens
 * and with the button in the header. The server computes it over every
 * device, which is too much for every twenty seconds, and as on the website
 * the numbers should not move while someone reads them.
 *
 * **A run belongs to the storage it started on** ('taken'). Signed out
 * meanwhile, it writes nothing more, and it leaves the state of the next
 * sign-in alone. Until 10/2026 such a run set "offline" and "synced" for it.
 *
 * **The review list does not decide whether the sync got through.** When
 * the counts went out and the rows came, the sync stands, even if the list
 * fails. The list waits for the next round. Until 10/2026 a list the server
 * kept failing on made every sync count as failed: "offline" and no new
 * "last synced", although nothing else was wrong.
 */
export async function refresh({ masterData = false, review = false, background = false } = {}) {
    // Asked for while a sync runs: it comes right after, not with the next button.
    if (review) reviewWantedFor = counting.inventoryId ?? NEXT_OPEN;
    if (counting.loading) {
        if (!background) againWanted = true;
        return;
    }
    counting.loading = true;
    const taken = currentMark();
    const id = counting.inventoryId;
    // Only for the inventory that is open now. Until 10/2026 a run that
    // started on the previous one took the wish with it, and the next review
    // list stayed empty until someone tapped "Refresh". The page asks again
    // as soon as another inventory opens ('Review.svelte').
    const withReview = reviewWantedHere(id);
    //: Whether the run failed. Then nothing is repeated at once.
    let failed = false;
    try {
        await submit(taken);
        const ids = (await fetchInventories(taken)).map((inventory) => inventory.id);
        // Counted again: what the notice says no longer holds.
        if (ids.includes(counting.notice?.inventoryId)) counting.notice = null;
        if (id !== null && !ids.includes(id)) {
            reviewWantedFor = null;
            await markGone(id, taken);
            return;
        }
        ids.sort((left, right) => Number(right === id) - Number(left === id));
        for (const inventoryId of ids) {
            try {
                // Master data once per inventory, it changes rarely. Anew
                // only on request, after a rejected new row, or where it is
                // from before 10/2026 and does not name the inventory types yet.
                const stored = await db.masterData.get(inventoryId);
                if (masterData || stored?.outdated || !stored?.inventurarten) {
                    await fetchMasterData(inventoryId, taken);
                }
                await fetchPositions(inventoryId, taken);
            } catch (error) {
                // Closed between list and fetch: then away with it, unless
                // numbers or a draft still wait for it ('inUse').
                if (error.status !== 404) throw error;
                if (inventoryId === id) {
                    reviewWantedFor = null;
                    await markGone(id, taken);
                    return;
                }
                if (!(await inUse(inventoryId))) await removeInventory(inventoryId, taken);
            }
        }
        if (withReview && counting.inventoryId === id) {
            try {
                await fetchReview(id, taken);
                if (reviewWantedHere(id)) reviewWantedFor = null;
            } catch (error) {
                if (error instanceof SignedOutMeanwhile || error.isSignedOut) throw error;
                // The wish stays for the next round, and none follows at once.
                failed = true;
            }
        }
        counting.online = true;
        counting.message = "";
        await guarded(taken, () => remember("last_synced", new Date().toISOString()));
    } catch (error) {
        failed = true;
        // Signed out during the run: nothing of it is this window's business
        // any more. Another window's sign-out reloads this one ('api.js').
        if (error instanceof SignedOutMeanwhile) return;
        if (error.isSignedOut) {
            // Do not show as a fault: only the sign-in is missing, and whoever
            // reads the message fetches it.
            counting.signedOut = true;
            counting.message = "";
            return;
        }
        counting.online = false;
        // Only real errors are worth a message. No network shows the icon.
        counting.message = error.status === 0 ? "" : t("error.sync");
    } finally {
        counting.loading = false;
        if (taken !== currentMark()) {
            // This window signed out during the run. What stands here now
            // belongs to the next sign-in, and this run has nothing to say.
            againWanted = false;
        } else {
            // Only once the list from the storage stands: "nothing pending"
            // showed for a moment before it (until 10/2026).
            await loadFromStorage();
            counting.synced = true;
            // The wish of an inventory that is no longer open goes with it.
            if (reviewWantedFor !== NEXT_OPEN && reviewWantedFor !== counting.inventoryId) {
                reviewWantedFor = null;
            }
            // Asked for during this run, after it had decided: once more, now
            // with it. But only after a run that got through. After a failure
            // the wish waits for the next sync: the clock, "online", coming
            // back. Until 10/2026 a review list that failed was asked for again
            // at once, as fast as the network refused, on every page of the
            // inventory and on the login form.
            const again = !failed && (againWanted || reviewWantedHere(counting.inventoryId));
            againWanted = false;
            if (again) void refresh();
        }
    }
}

/**
 * The open inventory is no longer listed: closed, deleted, or taken back
 * into preparation. No way leads on in it, so to the list. Why, the list
 * says on top ('notice'), also after the next sync.
 *
 * With numbers still waiting for it, everything stays in the storage: they
 * go out once the count runs again ('submit' holds them until then). Until
 * 10/2026 the storage was cleared regardless, and the numbers with it. A
 * draft keeps it too, until its person saves or discards it ('inUse').
 */
async function markGone(id, taken) {
    const waiting = await hasPendingJobs(id);
    if (!(await inUse(id))) await removeInventory(id, taken);
    counting.online = true;
    counting.gone = true;
    counting.notice = { key: waiting ? "error.on_hold" : "error.gone", inventoryId: id };
}

/** The rows of an article at a location. */
export function rowsOf(locationId, articleId) {
    return counting.positions.filter(
        (position) => position.location_id === locationId && position.artikel_id === articleId,
    );
}

export function articleFromMasterData(articleId) {
    return (counting.masterData?.artikel ?? []).find((article) => article.id === articleId) ?? null;
}

export function locationFromMasterData(locationId) {
    return (counting.masterData?.orte ?? []).find((location) => location.id === locationId) ?? null;
}

/** What this article can be weighed in, grouped, see 'containers.js'. */
export function containerGroupsOf(article) {
    return containerGroups(article, counting.masterData?.behaelter);
}
