/**
 * The inventories on the device: the list, their master data, and clearing away.
 *
 * Part of the sync ('sync.js'). What the server no longer names goes here,
 * unless numbers or a draft still wait for it.
 */

import { get, guarded } from "../api.js";
import { forgetDrafts, savedDrafts } from "../draft_backup.js";
import { currentMark, db, forget } from "../storage.js";
import { SYNC_MARK } from "./positions.js";

export async function fetchInventories(taken = currentMark()) {
    const inventories = await get("/inventuren/", taken);
    await guarded(taken, async () => {
        await db.inventories.bulkPut(inventories);
        await cleanUp(inventories.map((inventory) => inventory.id));
    });
    return inventories;
}

/**
 * What the server no longer names is settled here.
 *
 * Closed and deleted inventories would otherwise stay in the storage
 * forever and reappear as a choice in the next dead spot, with numbers
 * nobody can change any more. Cleared away only **after a successful
 * response**: without network nobody here knows what still applies.
 *
 * An inventory for which jobs still lie in the queue stays: first what was
 * counted has to go out. It is marked as waiting for the count ('held'),
 * because its stored state still says "counting" (until 10/2026 the list
 * showed it so). Listed again, it comes from the server without the mark.
 *
 * One that only a draft keeps ('inUse') stays for its form, but the list of
 * inventories no longer offers it ('draftOnly'): nothing says it will be
 * counted again, it may be closed for good. The notice of the draft leads
 * to it. Its review list goes. Until 10/2026 such an inventory stood in the
 * list as waiting for the count, with its review list, closed or not.
 *
 * Its lock goes too. The app holds back the draft of a locked inventory
 * until the lock opens ('App.svelte'), and the lock of one no longer listed
 * never opens. Until 10/2026 nothing then led to the draft, and the
 * inventory stayed with its rows until someone signed out. Listed again, it
 * comes from the server with its own.
 */
export async function cleanUp(current) {
    const keep = new Set(current);
    const waiting = await inventoriesWithJobs();
    const drafted = new Set((await savedDrafts()).map((draft) => draft.inventoryId));
    const gone = [];
    for (const inventory of await db.inventories.toArray()) {
        if (keep.has(inventory.id)) continue;
        if (waiting.has(inventory.id)) {
            if (!inventory.held || inventory.draftOnly) {
                await db.inventories.update(inventory.id, { held: true, draftOnly: false });
            }
        } else if (drafted.has(inventory.id)) {
            // Also one set aside before its lock went along with it.
            if (!inventory.draftOnly || inventory.gesperrt) {
                const unlisted = { held: false, draftOnly: true, gesperrt: false };
                await db.inventories.update(inventory.id, unlisted);
                await db.reviews.delete(inventory.id);
            }
        } else {
            gone.push(inventory.id);
        }
    }
    for (const id of gone) await dropInventory(id);
    return gone;
}

/** Take an inventory including everything hanging on it out of the storage of 'taken'. */
export async function removeInventory(inventoryId, taken = currentMark()) {
    await guarded(taken, () => dropInventory(inventoryId));
}

async function dropInventory(inventoryId) {
    await forgetDrafts(inventoryId);
    await db.positions.where("inventur_id").equals(inventoryId).delete();
    await db.masterData.delete(inventoryId);
    await db.reviews.delete(inventoryId);
    await db.inventories.delete(inventoryId);
    await forget(SYNC_MARK(inventoryId));
}

/** For which inventories something is still pending. Those are not touched. */
async function inventoriesWithJobs() {
    const ids = new Set();
    for (const job of await db.queue.toArray()) {
        const id = await inventoryOf(job);
        if (id != null) ids.add(id);
    }
    return ids;
}

/** Whether numbers still wait to go out for this inventory. */
export async function hasPendingJobs(inventoryId) {
    return (await inventoriesWithJobs()).has(inventoryId);
}

/**
 * The inventories the storage keeps although the server no longer names
 * them: numbers wait to go out, or a draft waits for its person. A draft
 * goes only when someone saves or discards it. Until 10/2026 an inventory
 * that left the count took the draft of the open form with it.
 */
async function inventoriesInUse() {
    const ids = await inventoriesWithJobs();
    for (const draft of await savedDrafts()) ids.add(draft.inventoryId);
    return ids;
}

/** Whether the storage has to keep this inventory ('inventoriesInUse'). */
export async function inUse(inventoryId) {
    return (await inventoriesInUse()).has(inventoryId);
}

/** Which inventory a job belongs to, by its payload or its row. Null once the row is gone. */
export async function inventoryOf(job) {
    if (job.inventory != null) return job.inventory;
    if (job.payload?.inventur) return job.payload.inventur;
    const row = job.position == null ? null : await db.positions.get(job.position);
    return row?.inventur_id ?? null;
}

export async function fetchMasterData(inventoryId, taken = currentMark()) {
    const data = await get(`/inventuren/${inventoryId}/stammdaten/`, taken);
    await guarded(taken, () => db.masterData.put({ inventory_id: inventoryId, ...data }));
    return data;
}

/**
 * The master data of this inventory come anew with the next sync ('refresh').
 *
 * A new row is built from them: location, article and pack. When the server
 * rejects one, they are usually out of date. A product was merged and its
 * pack is gone, or an article was set inactive, and the search would offer
 * the same once more. Until 10/2026 that went on until someone tapped
 * "Refresh". What lies here stays until the new ones arrive: without network
 * the article can still be counted on another pack.
 */
export async function markMasterDataOutdated(inventoryId) {
    if (inventoryId != null) await db.masterData.update(inventoryId, { outdated: true });
}
