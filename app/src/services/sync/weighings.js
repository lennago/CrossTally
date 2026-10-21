/**
 * The weighings of a row: here first, then into the queue, and sent one by one.
 *
 * Part of the sync ('sync.js'). Every bowl carries a key the device gives
 * it, so the server recognises a weighing that arrives twice.
 */

import { get, guarded, remove, send } from "../api.js";
import { asPlainData, currentMark, db } from "../storage.js";
import { refreshPosition, serverNumber } from "./positions.js";
import { submit } from "./queue.js";

/**
 * The weighings of a row as they stand in the storage **now**.
 *
 * Not from the row the caller passes in: the draft holds it as it looked
 * on opening and adopts several weighings in one go. Built from the copy,
 * the second bowl would overwrite the first. Without network only the last
 * would stand there until the next sync, and with it a wrong progress. The
 * queue did hold both, counted nothing was lost.
 */
async function currentWeighings(position) {
    const stored = await db.positions.get(position.id);
    return stored?.wiegungen ?? position.wiegungen ?? [];
}

export async function createWeighing(
    position,
    grossGrams,
    containerId,
    immediately = true,
    taken = currentMark(),
) {
    // The id is assigned by the device: if the same job arrives once more
    // after a dead spot, the server recognises the weighing by it instead
    // of counting a second bowl. It also stands on the weighing in the
    // storage. By it 'removeWeighing' finds the job of a not yet sent one.
    const ownKey = uuid();
    await guarded(taken, async () => {
        const weighings = asPlainData([
            ...(await currentWeighings(position)),
            {
                id: null,
                kennung: ownKey,
                brutto_gramm: grossGrams,
                behaelter: containerId,
                netto: null,
            },
        ]);
        await db.positions.update(position.id, { wiegungen: weighings });
        await db.queue.add({
            kind: "weighing",
            position: position.id,
            payload: { brutto_gramm: grossGrams, behaelter: containerId, kennung: ownKey },
        });
    });
    if (immediately) await submit(taken);
}

/**
 * 'randomUUID' only exists in a secure context. Without it the id is built
 * by hand, in the same shape: the server accepts nothing but a UUID and
 * would reject every weighing.
 */
export function uuid() {
    if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
    return [
        hex.slice(0, 8),
        hex.slice(8, 12),
        hex.slice(12, 16),
        hex.slice(16, 20),
        hex.slice(20),
    ].join("-");
}

/**
 * Whether a stored weighing is the one meant.
 *
 * Compared by number or key, not by object: the list comes fresh from the
 * storage, the same object as in the draft is not in it. A weighing that
 * has not reached the server yet has no number, only its key. Compared by
 * number alone, 'null' would be every unsent bowl of the row at once.
 * Without either nothing is meant: every bowl gets its key where it arises
 * ('createWeighing'), and one from the server has its number.
 */
export function sameWeighing(weighing) {
    return (entry) =>
        weighing.id
            ? entry.id === weighing.id
            : Boolean(weighing.kennung) && entry.kennung === weighing.kennung;
}

/** Correct a stored weighing, misread or wrong vessel. */
export async function changeWeighing(
    position,
    weighing,
    grossGrams,
    containerId,
    immediately = true,
    taken = currentMark(),
) {
    const same = sameWeighing(weighing);
    const payload = { brutto_gramm: grossGrams, behaelter: containerId };
    await guarded(taken, async () => {
        const weighings = asPlainData(
            (await currentWeighings(position)).map((entry) =>
                same(entry) ? { ...entry, ...payload } : entry,
            ),
        );
        await db.positions.update(position.id, { wiegungen: weighings });

        if (!weighing.id && weighing.kennung) {
            // Not at the server yet: its job is still waiting and goes out with
            // the corrected weight. Until 10/2026 a change went out as a
            // correction of the number 'null', the server answered 404, and
            // the old weight stood.
            const waiting = await db.queue
                .filter(
                    (job) => job.kind === "weighing" && job.payload?.kennung === weighing.kennung,
                )
                .modify((job) => Object.assign(job.payload, payload));
            if (waiting) return;
            // The job has gone out, only the refetch afterwards failed on the
            // network. The server knows the number, the row names it ('correctByKey').
        }
        await db.queue.add({
            kind: "weighing_changed",
            position: position.id,
            weighing: weighing.id,
            kennung: weighing.kennung ?? null,
            payload,
        });
    });
    if (immediately) await submit(taken);
}

export async function removeWeighing(
    position,
    weighing,
    immediately = true,
    taken = currentMark(),
) {
    const same = sameWeighing(weighing);
    const queued = await guarded(taken, async () => {
        const current = await currentWeighings(position);
        await db.positions.update(position.id, {
            wiegungen: asPlainData(current.filter((entry) => !same(entry))),
        });

        if (!weighing.id) {
            // Never been at the server: then its job must not go out any more
            // either, otherwise the next sync would create the bowl just removed.
            if (weighing.kennung) {
                await db.queue
                    .filter(
                        (job) =>
                            job.kind === "weighing" && job.payload?.kennung === weighing.kennung,
                    )
                    .delete();
            }
            return false;
        }
        await db.queue.add({
            kind: "weighing_removed",
            weighing: weighing.id,
            position: position.id,
        });
        return true;
    });
    if (queued && immediately) await submit(taken);
}

// --------------------------------------------------------------------------
// Sending
// --------------------------------------------------------------------------

/** A new bowl. */
export async function sendWeighing(job, taken) {
    const number = await serverNumber(job.position);
    await send("/wiegungen/", { ...job.payload, zaehlposition: number }, "POST", taken);
    // The response is the weighing, not the row. But the server recomputes
    // its quantity. Without fetching it, the sum would stay where it was.
    await refreshPosition(job.position, job.number, taken);
}

/** A corrected bowl, by its number, or by its key where the device has none ('correctByKey'). */
export async function sendWeighingChange(job, taken) {
    if (job.weighing) await send(`/wiegungen/${job.weighing}/`, job.payload, "PATCH", taken);
    else await correctByKey(job, taken);
    return await refreshPosition(job.position, job.number, taken);
}

/** A removed bowl. Its row comes anew: the server recomputes the quantity. */
export async function sendWeighingRemoval(job, taken) {
    await remove(`/wiegungen/${job.weighing}/`, taken);
    return await refreshPosition(job.position, job.number, taken);
}

/**
 * Corrects a weighing the device knows only by its key.
 *
 * Its job went out, the refetch afterwards failed on the network, and the
 * bowl was corrected before the next sync: in the storage it still stands
 * without a number. The row on the server names the number. If the weighing
 * is not there after all, the correction is the weighing, under its key.
 */
async function correctByKey(job, taken) {
    const number = await serverNumber(job.position);
    const fresh = await get(`/zaehlpositionen/${number}/`, taken);
    const known = (fresh.wiegungen ?? []).find((entry) => entry.kennung === job.kennung);
    if (known) {
        await send(`/wiegungen/${known.id}/`, job.payload, "PATCH", taken);
        return;
    }
    const payload = { ...job.payload, kennung: job.kennung, zaehlposition: number };
    await send("/wiegungen/", payload, "POST", taken);
}
