/**
 * The count positions: fetched since the last mark, stored with what still
 * waits for them, changed here first, and sent.
 *
 * Part of the sync ('sync.js'). A row arises here with an own, negative
 * number and takes the server's number once its creation went out.
 */

import { ApiError, SignedOutMeanwhile, get, guarded, remove, send } from "../api.js";
import { multiplyQuantities } from "../numbers.js";
import {
    SYNC_PREFIX,
    asPlainData,
    currentMark,
    db,
    isOwn,
    nextOwnNumber,
    remember,
    remembered,
} from "../storage.js";
import { t } from "../language.svelte.js";
import { fail } from "./jobs.js";
import { submit } from "./queue.js";
import { sameWeighing } from "./weighings.js";

/** Marker per inventory: up to when was already fetched. */
export const SYNC_MARK = (inventoryId) => `${SYNC_PREFIX}${inventoryId}`;

/**
 * This far the marker is set back, in milliseconds.
 *
 * 'zuletzt_geaendert' arises on the server when saving, **before** the
 * commit. Two requests at once: one carries 10:00:00.0 and finishes later
 * than the other with 10:00:00.1. If a device fetches in between, it
 * remembers 00.1 and afterwards asks only for newer. The first would never
 * arrive. A few seconds of margin fetch it next time. Rows fetched twice do
 * no harm, 'bulkPut' overwrites.
 */
const MARGIN = 5_000;

function setBack(timestamp) {
    const value = new Date(timestamp).getTime();
    return Number.isNaN(value) ? timestamp : new Date(value - MARGIN).toISOString();
}

/**
 * Refresh the count positions of an inventory.
 *
 * The timestamp comes from the data itself, not from the device's clock:
 * that likes to run slow, and then exactly the span by which it is wrong
 * would be dropped.
 *
 * The rows and the mark of the sync are one step: both arrive, or neither.
 * Until 10/2026 a sign-out between them left the mark without its rows, and
 * the next account fetched only what changed after it, an almost empty list.
 */
export async function fetchPositions(inventoryId, taken = currentMark()) {
    const since = await remembered(SYNC_MARK(inventoryId));
    const query = since ? `?geaendert_seit=${encodeURIComponent(since)}` : "";
    const data = await get(`/inventuren/${inventoryId}/${query}`, taken);
    return await guarded(taken, () => storePositions(inventoryId, since, data));
}

async function storePositions(inventoryId, since, data) {
    await db.inventories.put({
        id: data.id,
        bezeichnung: data.bezeichnung,
        haus: data.haus,
        haus_id: data.haus_id,
        inventurarten: data.inventurarten,
        arten_anzeige: data.arten_anzeige,
        stichtag: data.stichtag,
        status: data.status,
        status_anzeige: data.status_anzeige,
        zaehlbar: data.zaehlbar,
        anzahl_positionen: data.anzahl_positionen,
        anzahl_gezaehlt: data.anzahl_gezaehlt,
        differenz_von_hand: data.differenz_von_hand,
        hat_sollbestand: data.hat_sollbestand,
        gesperrt: data.gesperrt === true,
    });

    const positions = data.zaehlpositionen ?? [];
    // A row marked as deleted is deleted: it comes along exactly once so it
    // can vanish here. The own number of a not yet sent row is not
    // affected, because the server does not know it after all.
    const [deleted, remaining] = partition(positions, (position) => position.aktiv === false);
    await storeFromServer(remaining, inventoryId);
    for (const position of deleted) {
        await db.positions.where("server_id").equals(position.id).delete();
        await db.positions.delete(position.id);
    }
    if (!since) {
        // Without a mark the server names every living row and no deleted
        // one. A server row here that it does not name is gone: deleted
        // while the mark was dropped ('storage.js', version 2) or lost. It
        // would otherwise stand in the list until the end. Own rows stay, the
        // server does not know them yet.
        const named = new Set(remaining.map((position) => position.id));
        await db.positions
            .where("inventur_id")
            .equals(inventoryId)
            .filter((position) => !isOwn(position.id) && !named.has(position.id))
            .delete();
    }

    // Compared as points in time, not as text: the server writes the zone
    // sometimes as "+01:00", sometimes as "Z", and as text they would sort wrongly.
    const newest = positions.reduce((latest, position) => {
        const value = position.zuletzt_geaendert;
        return !latest || new Date(value) > new Date(latest) ? value : latest;
    }, since ?? "");
    if (newest && newest !== since) await remember(SYNC_MARK(inventoryId), setBack(newest));
    return positions.length;
}

/** One list into two: what matches and what does not. */
function partition(list, matches) {
    const yes = [];
    const no = [];
    for (const entry of list) (matches(entry) ? yes : no).push(entry);
    return [yes, no];
}

/** Prepare what the server sends for the storage (location number as index). */
function prepare(position, inventoryId) {
    return {
        ...position,
        inventur_id: position.inventur_id ?? inventoryId,
        location_id: position.ort.id,
        server_id: position.id,
    };
}

//: The jobs that change a row the server already knows. Laid over its rows ('withQueued').
export const ROW_KINDS = new Set(["quantity", "weighing", "weighing_changed", "weighing_removed"]);

/**
 * Store rows from the server with what still waits in the queue for them.
 *
 * The server knows nothing of those jobs yet. Stored as it comes, its row
 * would hide an input that still goes out. Until 10/2026 a weighing whose
 * job hung on a 500 vanished from the list with the next sync, was weighed
 * a second time, and once the server took both, the bowl stood there twice.
 * A row removed here stays away until its removal went out.
 *
 * 'answered' is the job whose answer this is. It still lies in the queue,
 * but the answer already contains it, the way the server writes it
 * ("8.000"). And where the server asked back instead ('quantityConflicts'),
 * its row says what applies, not the own number.
 */
async function storeFromServer(positions, inventoryId, answered = null) {
    const removed = new Set();
    const queued = new Map();
    for (const job of await db.queue.orderBy("number").toArray()) {
        if (job.number === answered) continue;
        if (job.kind === "position_removed") removed.add(job.serverId);
        if (!ROW_KINDS.has(job.kind)) continue;
        queued.set(job.position, [...(queued.get(job.position) ?? []), job]);
    }
    await db.positions.bulkPut(
        positions
            .filter((position) => !removed.has(position.id))
            .map((position) =>
                (queued.get(position.id) ?? []).reduce(withQueued, prepare(position, inventoryId)),
            ),
    );
}

/**
 * A row as it will stand once the job went out. The same steps as the input
 * here ('enterQuantity', 'createWeighing' …), only on the server's row.
 */
function withQueued(position, job) {
    const weighings = position.wiegungen ?? [];
    if (job.kind === "quantity") {
        // The sum in the base unit follows the queued quantity, for the
        // display only: "Saved so far" showed the server's old sum next to the
        // new number (until 10/2026). The server's answer replaces both. Its
        // row names the pack in the house's unit ('gebinde_im_haus').
        const quantity = job.payload.eingabemenge;
        const sum = sumOf(quantity, position.gebinde?.faktor_in_basiseinheit);
        return { ...position, eingabemenge: quantity, menge_basiseinheit: sum };
    }
    if (job.kind === "weighing_removed") {
        return { ...position, wiegungen: weighings.filter((entry) => entry.id !== job.weighing) };
    }
    const key = job.kind === "weighing" ? job.payload.kennung : job.kennung;
    const same = sameWeighing({ id: job.weighing ?? null, kennung: key ?? null });
    if (weighings.some(same)) {
        // A new one the server already has: its answer got lost on the way,
        // and the job goes out once more. The server recognises it by its
        // key and keeps what it has.
        if (job.kind === "weighing") return position;
        const changed = weighings.map((entry) =>
            same(entry) ? { ...entry, ...job.payload } : entry,
        );
        return { ...position, wiegungen: changed };
    }
    // Gone on the server, the correction comes back rejected. Without number
    // and key there is nothing to recognise it by.
    if (job.weighing || !key) return position;
    // New, or a correction known only by its key: that creates it ('correctByKey').
    const weighing = {
        id: null,
        kennung: key,
        brutto_gramm: job.payload.brutto_gramm,
        behaelter: job.payload.behaelter,
        netto: null,
    };
    return { ...position, wiegungen: [...weighings, weighing] };
}

// --------------------------------------------------------------------------
// Changing: always here first, then into the queue
// --------------------------------------------------------------------------

/**
 * 'immediately' determines whether sending happens right away.
 *
 * When adopting a draft several changes arise at once. They go out
 * collected, otherwise moving on waits for a network round trip per row.
 *
 * 'seen' is the quantity the person had in front of them when opening
 * the form. It goes out along, and the server compares: if something else
 * is there by now, someone else counted, and instead of silently
 * overwriting a question comes ('quantityConflicts'). It is fixed in the
 * job, not in the row: after a dead spot what was visible then counts.
 *
 * Like every entry here, it is written only into the storage this window
 * works on ('taken'). A window left over from the account of before writes
 * nothing for the next one.
 */
export async function enterQuantity(
    position,
    quantity,
    immediately = true,
    seen = position.eingabemenge ?? null,
    taken = currentMark(),
) {
    await guarded(taken, async () => {
        // The sum follows at once, for "Saved so far": without network no
        // answer replaces it, and until 10/2026 the old sum stood next to the
        // new number.
        const sum = sumOf(quantity, await packFactor(position));
        await db.positions.update(position.id, { eingabemenge: quantity, menge_basiseinheit: sum });
        await db.queue.add({
            kind: "quantity",
            position: position.id,
            payload: { eingabemenge: quantity, eingabemenge_vorher: seen },
        });
    });
    if (immediately) await submit(taken);
}

/** What a quantity of packs makes in the house's unit, for the display only. */
function sumOf(quantity, factor) {
    if (quantity == null || quantity === "") return null;
    return factor == null ? quantity : multiplyQuantities(quantity, factor);
}

/**
 * The pack's quantity in the house's unit, from the master data of the row.
 *
 * Its own pack says the same since 10/2026. A row stored before carries the
 * product's factor, and a house counting in g read a carton of 8 kg as 8.
 */
async function packFactor(position) {
    const pack = position.gebinde;
    if (!pack) return null;
    const master = await db.masterData.get(position.inventur_id);
    const article = master?.artikel?.find((entry) => entry.id === position.artikel_id);
    const known = article?.gebinde?.find((entry) => entry.id === pack.id);
    return known?.faktor_in_basiseinheit ?? pack.faktor_in_basiseinheit ?? null;
}

/**
 * Get rid of a row again, created by accident or not here after all.
 *
 * On the server it is marked as deleted, not deleted. Here it vanishes at
 * once. A row that does not even exist there yet is settled with striking
 * its job.
 */
export async function removePosition(position, immediately = true, taken = currentMark()) {
    const queued = await guarded(taken, async () => {
        await db.positions.delete(position.id);

        // What is still pending for this row is void, because it is gone.
        await db.queue.filter((job) => job.position === position.id).delete();

        // The number migrates into the job: the row no longer stands in the
        // storage, there it could not be looked up later.
        const serverId = isOwn(position.id) ? position.server_id : position.id;
        if (!serverId) return false; // Never been at the server, settled with striking.

        // With the inventory: the row is gone from the storage, and a held
        // inventory could not be told from the job otherwise ('inventoryOf').
        await db.queue.add({
            kind: "position_removed",
            position: position.id,
            serverId,
            inventory: position.inventur_id ?? null,
        });
        return true;
    });
    if (queued && immediately) await submit(taken);
}

/**
 * A row nobody expected: a further pack or an article that does not
 * actually occur here.
 */
export async function createPosition(
    { inventoryId, location, article, pack },
    immediately = true,
    taken = currentMark(),
) {
    const position = await guarded(taken, async () => {
        const id = await nextOwnNumber();
        // Shaped like a row from the server, so the views need no second case.
        const row = asPlainData({
            id,
            server_id: null,
            inventur_id: inventoryId,
            ort: location,
            location_id: location.id,
            artikel_id: article.id,
            artikel: article.name,
            einheit: article.einheit,
            erwartete_menge: null,
            gebinde: pack ?? null,
            wird_gewogen: article.wird_gewogen && !pack,
            wiegungen: [],
            eingabemenge: null,
            menge_basiseinheit: null,
            unerwartet: true,
            // From the master data until the server confirms the row: the
            // list sorts it into the tab of its type.
            inventurart: article.inventurart ?? null,
            zuletzt_geaendert: new Date().toISOString(),
        });
        await db.positions.put(row);
        await db.queue.add({
            kind: "position",
            position: id,
            payload: {
                inventur: inventoryId,
                ort: location.id,
                artikel: article.id,
                gebinde: pack?.id ?? null,
            },
        });
        return row;
    });
    if (immediately) await submit(taken);
    return position;
}

// --------------------------------------------------------------------------
// Sending: what the jobs of a row do on the server
// --------------------------------------------------------------------------

/** A quantity. The server answers with the row, and that is what stands here then. */
export async function sendQuantity(job, taken) {
    const number = await serverNumber(job.position);
    const fresh = await send(`/zaehlpositionen/${number}/`, job.payload, "PATCH", taken);
    return await guarded(taken, () => adopt(job.position, fresh, job.number));
}

/** A row removed here, by the number its job carries. */
export async function sendPositionRemoval(job, taken) {
    // The row is already gone here. 'serverNumber' therefore no longer
    // reads its number from the storage but is given it.
    return await remove(`/zaehlpositionen/${job.serverId}/`, taken);
}

/**
 * Fetches a row fresh from the server and lays it over the own one.
 *
 * If that fails on the network, the job before is settled nonetheless. It
 * must not go out again, otherwise the weighing would stand there twice.
 * The row then comes with the next sync: the server has just touched it,
 * '?geaendert_seit=' brings it along.
 */
export async function refreshPosition(position, answered, taken) {
    const number = await serverNumber(position);
    let fresh;
    try {
        fresh = await get(`/zaehlpositionen/${number}/`, taken);
    } catch (error) {
        if (error instanceof SignedOutMeanwhile) throw error;
        if (error instanceof ApiError && error.isTemporary) return;
        throw error;
    }
    await guarded(taken, async () => {
        if (fresh.aktiv === false) {
            await db.positions.delete(position);
            return;
        }
        await storeFromServer([fresh], fresh.inventur_id, answered);
    });
}

export async function sendPosition(job, taken) {
    try {
        const fresh = await send("/zaehlpositionen/", job.payload, "POST", taken);
        await guarded(taken, () => adopt(job.position, fresh, job.number));
    } catch (error) {
        // The row already exists, the normal case on the second attempt after
        // an aborted connection. The server names its number so nothing is
        // counted twice here: the own row is absorbed into its, as after a
        // successful creation. If it stayed, the next sync would bring the
        // server row next to it, and the article would stand twice in the list.
        const existing = error?.data?.vorhanden_id?.[0];
        if (!existing) throw error;
        const fresh = await get(`/zaehlpositionen/${Number(existing)}/`, taken);
        await guarded(taken, () => adopt(job.position, fresh, job.number));
    }
}

/**
 * Take over the state from the server and replace the own number in the process.
 *
 * The number first: what still waits for the own row is then found under
 * the server's number and laid over its row.
 */
export async function adopt(own, fresh, answered = null) {
    if (isOwn(own)) {
        await db.positions.delete(own);
        await replaceNumber(own, fresh.id);
    }
    await storeFromServer([fresh], fresh.inventur_id, answered);
}

/** Turn jobs that still point to the own number onto the server number. */
async function replaceNumber(own, serverId) {
    const affected = await db.queue.filter((entry) => entry.position === own).toArray();
    await Promise.all(
        affected.map((entry) => db.queue.update(entry.number, { position: serverId })),
    );
}

export async function serverNumber(position) {
    if (!isOwn(position)) return position;
    const row = await db.positions.get(position);
    if (row?.server_id) return row.server_id;
    throw new ApiError(0, { detail: t("error.no_row") });
}

/**
 * The server's row in place of a refused entry.
 *
 * The entry was laid over the row here when it was typed. Left standing, it
 * looked counted, and the next entry went out with it as seen, which the
 * server refused once more (until 10/2026). The refused job is left out:
 * it still lies in the queue until it is struck. Without network or with
 * any other refusal the row stays as it is: nothing here may throw, or the
 * job would never be struck.
 */
export async function restoreRow(job, taken) {
    try {
        await refreshPosition(job.position, job.number, taken);
    } catch (error) {
        if (error instanceof SignedOutMeanwhile) throw error;
        if (error instanceof ApiError && error.status === 404) {
            await guarded(taken, () => db.positions.delete(job.position));
        }
    }
}

/**
 * A row whose creation the server rejects will never exist there.
 *
 * What still hangs on it (the quantity, the weighings) would therefore
 * never find its server number: 'serverNumber' reports that as temporary,
 * and the queue would abort at this point on **every** sync. Everything
 * behind it, quantities of entirely different rows too, would never go out.
 * The jobs therefore count as rejected too, and the row vanishes from the
 * storage, because it would otherwise keep showing a quantity that never arrives.
 */
export async function discardOrphans(own, error) {
    const orphans = await db.queue
        .filter((entry) => entry.position === own && entry.kind !== "position")
        .toArray();
    for (const entry of orphans) {
        await fail(entry, error);
        await db.queue.delete(entry.number);
    }
    await db.positions.delete(own);
}
