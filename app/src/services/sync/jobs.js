/**
 * What is pending and what was rejected, and the questions where someone else counted.
 *
 * Part of the sync ('sync.js'). The sync window shows all three. A refused
 * job is noted here with the server's reason, so nothing vanishes unseen.
 */

import { guarded } from "../api.js";
import { addQuantities } from "../numbers.js";
import { packName } from "../units.js";
import { currentMark, db, forget, remember, remembered } from "../storage.js";
import { t } from "../language.svelte.js";
import { inventoryOf } from "./inventories.js";
import { adopt, enterQuantity } from "./positions.js";
import { REVIEW_KINDS, reviewArticleName } from "./review.js";
import { uuid } from "./weighings.js";

//: Per job kind its text key. Translated when displaying, not when storing:
//: an entry in the storage survives a language switch.
const KINDS = {
    position: "job.position",
    position_removed: "job.position_removed",
    quantity: "job.quantity",
    weighing: "job.weighing",
    weighing_changed: "job.weighing_changed",
    weighing_removed: "job.weighing_removed",
    note: "job.note",
    difference: "job.difference",
    target: "job.target",
};

/** The kind of a job as someone can read it, in the language of now. */
export function jobKind(kind) {
    return kind in KINDS ? t(KINDS[kind]) : kind;
}

/**
 * The queue as someone can read it.
 *
 * "3 jobs" tells nobody anything, "Quantity · Cola 0,33 · Thekenfront"
 * does. By that one recognises whether the own value still waits outside.
 */
export async function pendingJobs() {
    const jobs = await db.queue.orderBy("number").toArray();
    const held = (await remembered("held", {})) ?? {};
    const described = [];
    for (const job of jobs) {
        described.push({
            number: job.number,
            kind: job.kind,
            label: await describe(job),
            // Why it waits although there is network: the inventory is not
            // being counted right now ('submit').
            held: holdReason(held[String(await inventoryOf(job))], job),
            ...valueOf(job),
        });
    }
    return described;
}

/**
 * Why this job waits, or null. A lock holds the counting, not the check list.
 *
 * A note from before 10/2026 is a plain text and holds everything.
 */
export function holdReason(entry, job) {
    if (entry == null) return null;
    if (typeof entry === "string") return entry;
    return entry.countingOnly && REVIEW_KINDS.has(job.kind) ? null : entry.reason;
}

async function describe(job) {
    if (REVIEW_KINDS.has(job.kind)) return await reviewArticleName(job);
    const row = job.position == null ? null : await db.positions.get(job.position);
    if (!row) return "";
    // The loose single unit by the app's own word, as in the form ('packName').
    const parts = [row.artikel, packName(row.gebinde), row.ort?.name];
    return parts.filter(Boolean).join(" · ");
}

/**
 * The number a job carries, so a notice can name it.
 *
 * "Quantity · Artikel C · Theke" without the number means counting again.
 * With it, the number can be typed in anew or passed on to whoever took the
 * inventory back. A weighing carries grams, a cleared quantity 'null'.
 */
function valueOf(job) {
    const payload = job.payload ?? {};
    if ("brutto_gramm" in payload) return { value: payload.brutto_gramm, unit: "g" };
    if ("eingabemenge" in payload) return { value: payload.eingabemenge, unit: null };
    if ("differenz" in payload) return { value: payload.differenz, unit: null };
    if ("notiz" in payload) return { value: payload.notiz, unit: null };
    if ("soll_statt_ist" in payload) {
        return { value: t(payload.soll_statt_ist ? "job.on" : "job.off"), unit: null };
    }
    return {};
}

/** What the server rejected, with reason, so it can be sorted out. */
export async function rejectedJobs() {
    return (await remembered("rejected", [])) ?? [];
}

export async function discardRejected(taken = currentMark()) {
    await guarded(taken, () => forget("rejected"));
}

/**
 * Rows in which someone else had already counted without one knowing.
 *
 * Unlike rejected things this is not simply cleared away: here lies a
 * number, and someone has to say what happens with it ('resolveQuantityConflict').
 */
export async function quantityConflicts() {
    return (await remembered("quantity_conflicts", [])) ?? [];
}

/**
 * Resolve the conflict of a row.
 *
 * - "add": both numbers together, say when two different crates
 *   were counted.
 * - "overwrite": the own number applies.
 * - "discard": the number already there applies. Then nothing goes out.
 *
 * Whoever decides here has seen the foreign number. It therefore goes out
 * along as 'seen', and the server accepts the decision. If someone else
 * has counted in the meantime, the next conflict comes, with the new number.
 */
export async function resolveQuantityConflict(
    id,
    choice,
    immediately = true,
    taken = currentMark(),
) {
    const settled = await guarded(taken, async () => {
        const all = await quantityConflicts();
        const conflict = all.find((entry) => entry.id === id);
        if (!conflict) return null;
        await remember(
            "quantity_conflicts",
            all.filter((entry) => entry !== conflict),
        );
        if (choice === "discard") return null;
        const position = (await db.positions.get(conflict.position)) ?? { id: conflict.position };
        return { conflict, position };
    });
    if (!settled) return;
    const { conflict, position } = settled;
    const quantity =
        choice === "add" ? addQuantities(conflict.theirs, conflict.mine) : conflict.mine;
    await enterQuantity(position, quantity, immediately, conflict.theirs, taken);
}

/**
 * The server rejected the quantity because someone else counted.
 *
 * Its row is stored so what really applies stands here. The own number
 * migrates into the question. If further own quantities for the same row
 * still lie in the queue, they build on the own number and not on the
 * foreign one: they would run into the same conflict. The last of them is
 * the number that is meant, and the others are absorbed into the question.
 */
export async function recordQuantityConflict(job, data) {
    const later = await db.queue
        .filter(
            (entry) =>
                entry.kind === "quantity" &&
                entry.position === job.position &&
                entry.number > job.number,
        )
        .toArray();
    const mine = (later.at(-1) ?? job).payload.eingabemenge;
    await Promise.all(later.map((entry) => db.queue.delete(entry.number)));

    const current = data.aktuell;
    await adopt(job.position, current, job.number);
    const conflict = {
        id: uuid(),
        position: current.id,
        inventoryId: current.inventur_id,
        label: [current.artikel, packName(current.gebinde), current.ort?.name]
            .filter(Boolean)
            .join(" · "),
        mine,
        theirs: current.eingabemenge ?? null,
        // Named next to both numbers: the question is decided under pressure.
        pack: packName(current.gebinde) || null,
        at: new Date().toISOString(),
    };
    // Per row only one question, the most recent: it carries the number that
    // was meant last, and the one standing there now.
    const others = (await quantityConflicts()).filter(
        (entry) => entry.position !== conflict.position,
    );
    await remember("quantity_conflicts", [...others, conflict]);
}

/**
 * A rejected job is noted instead of vanishing silently.
 *
 * Whoever counted should learn that their value did not arrive, and why.
 */
export async function fail(job, error) {
    const others = (await remembered("rejected", [])) ?? [];
    await remember("rejected", [
        ...others,
        {
            kind: job.kind,
            // Describe now, not when displaying: the row may be cleared away by
            // then, and then only a number would stand there.
            label: await describe(job),
            reason: reasonText(error),
            at: new Date().toISOString(),
            ...valueOf(job),
        },
    ]);
}

/**
 * Turn the server's response into a sentence someone can read.
 *
 * Where it names its reason ('detail'), that is the sentence. The rest of
 * such an answer is meant for the app: "status ZAEHLUNG true" stood before
 * the reason until 10/2026.
 */
export function reasonText(error) {
    const data = error?.data;
    if (!data) return String(error?.message ?? error);
    if (typeof data === "string") return data;
    const said = data.detail ?? Object.values(data);
    return [said]
        .flat(2)
        .map((entry) => String(entry))
        .join(" ");
}
