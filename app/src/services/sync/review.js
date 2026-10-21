/**
 * The review list: fetched whole, changed one field at a time.
 *
 * Part of the sync ('sync.js'). Notes, differences by hand and "target
 * counts" go through the queue like a count. Switching the difference by
 * hand and the lock go straight to the server.
 */

import { SignedOutMeanwhile, get, guarded, send } from "../api.js";
import { asPlainData, currentMark, db } from "../storage.js";
import { submit } from "./queue.js";

//: The jobs that change a row of the review list: its note, its difference by
//: hand, or whether its target counts.
export const REVIEW_KINDS = new Set(["note", "difference", "target"]);

/**
 * Fetch the review list of an inventory and keep it.
 *
 * The server computes it over the counts of every device, so it cannot be
 * built here. Kept, so it can be read in a dead spot too. What still waits
 * in the queue for it is laid over it again: otherwise a note typed without
 * network would vanish from the list as soon as the list comes anew,
 * although it still goes out.
 */
export async function fetchReview(inventoryId, taken = currentMark()) {
    const data = await get(`/inventuren/${inventoryId}/pruefliste/`, taken);
    return await guarded(taken, async () => {
        const review = {
            ...data,
            inventory_id: inventoryId,
            zeilen: await withPending(inventoryId, data.zeilen ?? []),
        };
        await db.reviews.put(asPlainData(review));
        return review;
    });
}

/** The rows with what still waits in the queue for them, the newest last. */
async function withPending(inventoryId, rows, answered = null) {
    const jobs = (await db.queue.orderBy("number").toArray()).filter(
        (job) =>
            REVIEW_KINDS.has(job.kind) && job.inventory === inventoryId && job.number !== answered,
    );
    if (!jobs.length) return rows;
    const byArticle = new Map(rows.map((row) => [row.hausartikel, row]));
    for (const job of jobs) {
        const row = byArticle.get(job.article);
        if (row) byArticle.set(job.article, overlaid(row, job.payload));
    }
    return [...byArticle.values()];
}

/**
 * A row of the review list with an entry that still waits laid over it.
 *
 * The server's verdict on the difference ('auffaellig') was given for its
 * own number. With a difference typed here it no longer applies, and the
 * page decides by the typed number until the server answers.
 */
function overlaid(row, payload) {
    const changed = { ...row, ...withoutSeen(payload) };
    if ("differenz" in payload) delete changed.auffaellig;
    return changed;
}

//: The field a review job sends, and the one that carries what the device saw of it.
const SEEN = { note: ["notiz", "notiz_vorher"], difference: ["differenz", "differenz_vorher"] };

/** The job's payload without what the device saw: that is for the server, not for the row. */
function withoutSeen(payload) {
    const rest = { ...payload };
    for (const [, seenField] of Object.values(SEEN)) delete rest[seenField];
    return rest;
}

/**
 * What the device showed of this field when the person typed, for the server to compare.
 *
 * From the job that still waits for the same field, if there is one: it
 * carries what was seen before the first local change, and the row by now
 * shows the own entry. Otherwise from the stored row, which then is the
 * server's. "Target counts" is a switch and carries nothing: the server
 * only takes it from managers, and two of them do not race over it.
 */
async function seenValue(inventoryId, articleId, kind) {
    if (!(kind in SEEN)) return {};
    const [field, seenField] = SEEN[kind];
    const waiting = await db.queue
        .filter(
            (job) =>
                job.kind === kind && job.inventory === inventoryId && job.article === articleId,
        )
        .first();
    if (waiting && seenField in (waiting.payload ?? {})) {
        return { [seenField]: waiting.payload[seenField] };
    }
    const review = await db.reviews.get(inventoryId);
    const row = review?.zeilen.find((entry) => entry.hausartikel === articleId);
    return { [seenField]: row?.[field] ?? null };
}

/**
 * A note on an article of the review list: here at once, to the server via the queue.
 *
 * Like a count: the list shows it right away, and without network it waits
 * on the device. Empty removes it. The server takes one field at a time, so
 * a note never carries an outdated difference along.
 */
export async function enterNote(
    inventoryId,
    articleId,
    text,
    immediately = true,
    taken = currentMark(),
) {
    await changeReview(inventoryId, articleId, "note", { notiz: text }, immediately, taken);
}

/**
 * The difference by hand, as decimal text with a point ('asNumberText'). Null clears it.
 *
 * Only where a manager switched it on. The server checks that again: a job
 * that waited out a dead spot meanwhile may meet an inventory that takes no
 * difference any more, and then it counts as rejected and says why.
 */
export async function enterDifference(
    inventoryId,
    articleId,
    value,
    immediately = true,
    taken = currentMark(),
) {
    const payload = { differenz: value };
    await changeReview(inventoryId, articleId, "difference", payload, immediately, taken);
}

/**
 * "Target counts": at closing the target stands as the result of the article.
 *
 * Only managers, and only where the article has a target. The server checks
 * both again. Through the queue like a note: without network it waits on the
 * device, and the list shows it at once.
 */
export async function enterTargetCounts(
    inventoryId,
    articleId,
    on,
    immediately = true,
    taken = currentMark(),
) {
    const payload = { soll_statt_ist: on };
    await changeReview(inventoryId, articleId, "target", payload, immediately, taken);
}

async function changeReview(inventoryId, articleId, kind, payload, immediately, taken) {
    await guarded(taken, async () => {
        // Read before the row changes: afterwards it shows the own entry.
        const seen = await seenValue(inventoryId, articleId, kind);
        await changeReviewRow(inventoryId, articleId, (row) => overlaid(row, payload));
        // Only the last state of a field counts. An older job for it would go
        // out first and say something nobody means any more.
        await db.queue
            .filter(
                (job) =>
                    job.kind === kind && job.inventory === inventoryId && job.article === articleId,
            )
            .delete();
        await db.queue.add({
            kind,
            inventory: inventoryId,
            article: articleId,
            payload: { ...payload, ...seen },
        });
    });
    if (immediately) await submit(taken);
}

/**
 * The server kept someone else's entry and sent its row: that row stands here now.
 *
 * The own entry is listed under "not accepted" with the server's reason,
 * so nothing vanishes without a word. Nobody is asked, unlike with a
 * quantity: a note is text, and the person reads both and types again.
 */
export async function adoptReviewRow(job, data) {
    const fresh = data?.zeile;
    if (!fresh) return;
    await changeReviewRow(job.inventory, job.article, () => fresh);
}

async function changeReviewRow(inventoryId, articleId, change) {
    const review = await db.reviews.get(inventoryId);
    if (!review) return;
    const rows = review.zeilen.map((row) => (row.hausartikel === articleId ? change(row) : row));
    await db.reviews.put(asPlainData({ ...review, zeilen: rows }));
}

/**
 * Send a note or a difference and take the row as the server answers it.
 *
 * The answer is null where the article dropped out of the list with its
 * last remark. Jobs still waiting for the same row are laid over it again.
 */
export async function sendReviewChange(job, taken) {
    const answer = await send(
        `/inventuren/${job.inventory}/pruefliste/${job.article}/`,
        job.payload,
        "PATCH",
        taken,
    );
    await guarded(taken, async () => {
        const review = await db.reviews.get(job.inventory);
        if (!review) return;
        const fresh = answer?.zeile ?? null;
        const others = review.zeilen.filter((row) => row.hausartikel !== job.article);
        const known = review.zeilen.some((row) => row.hausartikel === job.article);
        const rows = !fresh
            ? others
            : known
              ? review.zeilen.map((row) => (row.hausartikel === job.article ? fresh : row))
              : [...review.zeilen, fresh];
        // The answered job still lies in the queue until 'execute' returns. It
        // must not lay its own payload over what the server made of it.
        await db.reviews.put(
            asPlainData({ ...review, zeilen: await withPending(job.inventory, rows, job.number) }),
        );
    });
}

/** The article's name from the stored review list, for the waiting jobs. */
export async function reviewArticleName(job) {
    const review = await db.reviews.get(job.inventory);
    return review?.zeilen.find((row) => row.hausartikel === job.article)?.name ?? "";
}

/**
 * Switch the difference by hand on or off. Only managers, and only with network.
 *
 * Not through the queue: the server decides whether it may (no target
 * stock), and whoever switches wants to know at once. The inventory and its
 * list say it from now on, also before the next fetch.
 */
export async function switchManualDifference(inventoryId, on, taken = currentMark()) {
    const address = `/inventuren/${inventoryId}/differenz-von-hand/`;
    const answer = await send(address, { an: on }, "POST", taken);
    const value = answer?.differenz_von_hand === true;
    await guarded(taken, async () => {
        const inventory = await db.inventories.get(inventoryId);
        if (inventory) await db.inventories.put({ ...inventory, differenz_von_hand: value });
        const review = await db.reviews.get(inventoryId);
        if (review) await db.reviews.put(asPlainData({ ...review, differenz_von_hand: value }));
    });
    return value;
}

/**
 * Lock the count or open it again. Only managers, and only with network.
 *
 * Not through the queue, like the difference by hand: whoever locks wants
 * to know at once whether it holds. The inventory says it from now on, also
 * before the next fetch, and the app then shows only its review list.
 */
export async function switchLock(inventoryId, on, taken = currentMark()) {
    const answer = await send(`/inventuren/${inventoryId}/sperre/`, { an: on }, "POST", taken);
    const value = answer?.gesperrt === true;
    await guarded(taken, async () => {
        const inventory = await db.inventories.get(inventoryId);
        if (inventory) await db.inventories.put({ ...inventory, gesperrt: value });
    });
    return value;
}

/**
 * The row of the review list as the server has it, in place of a refused entry.
 *
 * The entry was laid over its row when it was typed. Left standing, a refused
 * "Target counts" stayed switched on and a refused difference stood, and the
 * next entry went out with it as seen, which the server refused once more
 * (until 10/2026). The server sends its list whole. Taken over are this row
 * and the flags of the list, since the difference by hand may have been
 * switched off. The other rows stay as they were read: the numbers do not
 * move while someone reads them. What still waits for the row is laid over
 * it again, the refused job left out. Without network the row stays as it
 * is: nothing here may throw, or the job would never be struck.
 *
 * The list comes once per run of the queue and inventory ('lists', kept by
 * 'submit'). The server computes it over every device, and until 10/2026 a
 * batch of refusals fetched it once per entry, one after another: every
 * difference, say, after the difference by hand was switched off.
 */
export async function restoreReview(job, taken, lists = new Map()) {
    if (!lists.has(job.inventory)) {
        lists.set(job.inventory, await reviewOrNothing(job.inventory, taken));
    }
    const data = lists.get(job.inventory);
    if (data === null) return;
    await guarded(taken, async () => {
        const review = await db.reviews.get(job.inventory);
        if (!review) return;
        const fresh = (data?.zeilen ?? []).find((row) => row.hausartikel === job.article);
        const rows = review.zeilen.flatMap((row) =>
            row.hausartikel !== job.article ? [row] : fresh ? [fresh] : [],
        );
        await db.reviews.put(
            asPlainData({
                ...review,
                differenz_von_hand: data?.differenz_von_hand ?? review.differenz_von_hand,
                hat_sollbestand: data?.hat_sollbestand ?? review.hat_sollbestand,
                zeilen: await withPending(job.inventory, rows, job.number),
            }),
        );
    });
}

/** The server's review list, or null where it does not come ('restoreReview'). */
async function reviewOrNothing(inventoryId, taken) {
    try {
        return await get(`/inventuren/${inventoryId}/pruefliste/`, taken);
    } catch (error) {
        if (error instanceof SignedOutMeanwhile) throw error;
        return null;
    }
}
