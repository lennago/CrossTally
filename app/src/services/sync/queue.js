/**
 * Submitting: the queue, worked through from the front.
 *
 * Part of the sync ('sync.js'). What a job does on the server, the module
 * of its subject says. The table below names it per kind of job.
 */

import { ApiError, guarded } from "../api.js";
import { currentMark, db, remember } from "../storage.js";
import { inventoryOf, markMasterDataOutdated } from "./inventories.js";
import { fail, holdReason, reasonText, recordQuantityConflict } from "./jobs.js";
import {
    ROW_KINDS,
    discardOrphans,
    restoreRow,
    sendPosition,
    sendPositionRemoval,
    sendQuantity,
} from "./positions.js";
import { REVIEW_KINDS, adoptReviewRow, restoreReview, sendReviewChange } from "./review.js";
import { sendWeighing, sendWeighingChange, sendWeighingRemoval } from "./weighings.js";

let running = false;

/**
 * Work through the queue, from the front.
 *
 * Aborts on the first temporary error. Without network there is no point in
 * letting the remaining jobs fail too, and their order should be kept. That
 * includes an expired sign-in: the job is not wrong, only the token is
 * missing. After signing in it goes out. What the server **rejects**, by
 * contrast, is thrown out: a job that still gets the same "400" on the
 * tenth attempt would otherwise block everything behind it.
 */
export async function submit(taken = currentMark()) {
    if (running) return { sent: 0, pending: await db.queue.count() };
    running = true;
    let sent = 0;
    //: Inventories not being counted right now, with the server's words for it.
    const held = new Map();
    //: Broken off on the network: the jobs after it were not seen.
    let cutShort = false;
    //: The review lists this run fetched to put refused rows back, per inventory.
    const reviews = new Map();
    try {
        for (const queued of await db.queue.orderBy("number").toArray()) {
            // Read fresh, not the copy from just now: if the job before creates a
            // row, 'replaceNumber' turns the following ones onto the server's
            // number. The copy in memory would know nothing of it and run into
            // nothing, and the quantity would only go out on the next sync.
            const job = await db.queue.get(queued.number);
            // Struck in the meantime: the row was removed or its creation
            // rejected. Executing the copy from just now anyway would mean
            // sending a job that no longer exists.
            if (!job) continue;
            // Everything of a held inventory waits, in its order. Except the
            // check list while the count is only locked: the server takes
            // notes and differences then, and that is when they are typed.
            const inventoryId = await inventoryOf(job);
            if (holdReason(held.get(inventoryId), job) != null) continue;
            try {
                await execute(job, taken);
                // Gone through, the list on the server changed: a refusal after
                // this one takes it anew. From the copy of before, its row lost
                // what this job just set, the note say.
                if (REVIEW_KINDS.has(job.kind)) reviews.delete(inventoryId);
            } catch (error) {
                // Signed out meanwhile too: what the job brought or would
                // note belongs to the account of before ('SignedOutMeanwhile').
                if (error instanceof ApiError && error.isTemporary) {
                    cutShort = true;
                    break;
                }
                if (error instanceof ApiError && error.isOnHold) {
                    // Taken back into preparation: it is counted again later,
                    // and the numbers from the dead spot wait for that here.
                    // Until 10/2026 the answer was a 400, the job counted as
                    // rejected, and the numbers were gone. Locked: only the
                    // counting waits (until 10/2026 the check list did too).
                    if (inventoryId != null) {
                        held.set(inventoryId, {
                            reason: reasonText(error),
                            countingOnly: Boolean(error.data?.gesperrt),
                        });
                    }
                    continue;
                }
                if (isQuantityConflict(job, error)) {
                    await guarded(taken, () => recordQuantityConflict(job, error.data));
                } else if (isReviewConflict(job, error)) {
                    await guarded(taken, async () => {
                        await adoptReviewRow(job, error.data);
                        await fail(job, error);
                    });
                } else if (!isAlreadyGone(job, error)) {
                    await guarded(taken, async () => {
                        await fail(job, error);
                        if (job.kind === "position") {
                            await discardOrphans(job.position, error);
                            await markMasterDataOutdated(inventoryId);
                        }
                    });
                    if (ROW_KINDS.has(job.kind)) await restoreRow(job, taken);
                    else if (REVIEW_KINDS.has(job.kind)) await restoreReview(job, taken, reviews);
                }
            }
            await guarded(taken, () => db.queue.delete(job.number));
            sent += 1;
        }
    } finally {
        running = false;
    }
    // Noted for the sync dialog. A run cut short by the network keeps the
    // previous notes: it has not seen those inventories. Until 10/2026 the
    // break counted as seen, and the notes of every held inventory went.
    if (!cutShort) await guarded(taken, () => remember("held", Object.fromEntries(held)));
    return { sent, pending: await db.queue.count() };
}

/** A note or difference someone else changed in between: the server kept theirs. */
function isReviewConflict(job, error) {
    return (
        REVIEW_KINDS.has(job.kind) &&
        error instanceof ApiError &&
        error.status === 409 &&
        (error.data?.konflikt === "notiz" || error.data?.konflikt === "differenz")
    );
}

/** A quantity the server asks back about because someone else counted. */
function isQuantityConflict(job, error) {
    return (
        job.kind === "quantity" &&
        error instanceof ApiError &&
        error.status === 409 &&
        error.data?.aktuell != null
    );
}

/**
 * A removal that finds nothing has reached its goal.
 *
 * The first attempt got through, only its answer was lost on the way. The
 * second one meets a 404. Until 10/2026 that counted as a rejection, with a
 * red mark and a notice about an error that did not exist.
 */
function isAlreadyGone(job, error) {
    return (
        error instanceof ApiError &&
        error.status === 404 &&
        (job.kind === "weighing_removed" || job.kind === "position_removed")
    );
}

//: What a job of each kind does on the server. A new weighing is the rest:
//: the chain of questions this table replaced ended with it. A kind it does
//: not know comes from a newer version of the app, when a device loads an
//: older one again. The server refuses it, and the sync window names it.
//: Held back here, it would stop the queue, which sends strictly in order.
//: The modules of the subjects import 'submit' from here in turn. That
//: holds because the table names only functions declared with 'function':
//: they exist as soon as the modules are linked, before any of them runs.
const EXECUTE = new Map([
    ["position", sendPosition],
    ["position_removed", sendPositionRemoval],
    ["quantity", sendQuantity],
    ["weighing", sendWeighing],
    ["weighing_changed", sendWeighingChange],
    ["weighing_removed", sendWeighingRemoval],
    ["note", sendReviewChange],
    ["difference", sendReviewChange],
    ["target", sendReviewChange],
]);

async function execute(job, taken) {
    return await (EXECUTE.get(job.kind) ?? sendWeighing)(job, taken);
}
