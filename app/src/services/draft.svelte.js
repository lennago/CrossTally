/**
 * The draft of an entry: what someone typed before confirming.
 *
 * On the phone counting happens standing, often with one hand. Not every
 * keystroke may write something already. Only moving on adopts ("Next ›",
 * "Done"), or "Back", which does the same. "Cancel" discards. Until then everything
 * lies here.
 *
 * Saved on adopting is only what **changed**: otherwise every opening of a
 * form would push a pile of jobs out.
 *
 * The rows in the draft ('row', 'scale', 'piece') are the server's rows and
 * keep its field names ('eingabemenge', 'wiegungen', 'gebinde'). What the
 * draft adds itself is English.
 */

import {
    changeWeighing,
    createPosition,
    createWeighing,
    enterQuantity,
    removePosition,
    removeWeighing,
    submit,
} from "./sync.js";
import {
    LIMIT,
    asNumberText,
    formatQuantity,
    hasTooManyDecimals,
    isBelowZero,
    isTooLarge,
} from "./numbers.js";
import { currentMark } from "./storage.js";
import { t } from "./language.svelte.js";

/**
 * Builds the draft from what is already there.
 *
 * 'rows' are the count positions of this article at this location,
 * 'article' comes from the master data. It also knows the packs for which
 * there is no row yet.
 */
export function draftFrom(article, rows) {
    const byPack = new Map(rows.filter((r) => r.gebinde).map((r) => [r.gebinde.id, r]));
    const scale = rows.find((r) => r.wird_gewogen) ?? null;
    const piece = rows.find((r) => !r.gebinde && !r.wird_gewogen) ?? null;

    return {
        // Only what is expected here, because the counting list says what is on
        // the shelf. Everything further 'addPack' fetches on request.
        packs: (article?.gebinde ?? [])
            .filter((pack) => byPack.has(pack.id))
            .map((pack) => ({
                pack,
                row: byPack.get(pack.id),
                quantity: formatQuantity(byPack.get(pack.id).eingabemenge),
            })),
        //: Removed packs that had a row here. The row only goes on adopting.
        //: Until then 'addPack' brings it back including the number that
        //: was already typed.
        removed: [],
        //: The order of the packs on the product. By it they stand at the top as
        //: at the bottom, however often one migrated back and forth.
        order: (article?.gebinde ?? []).map((pack) => pack.id),
        scale,
        // Existing weighings and the newly typed ones in one list. 'removed'
        // remembers what should vanish on adopting.
        weighings: weighingRows(article, scale),
        //: Packs for which there is no row here yet, only on request.
        morePacks: (article?.gebinde ?? []).filter((pack) => !byPack.has(pack.id)),
        piece,
        pieceCount: formatQuantity(piece?.eingabemenge),
    };
}

/**
 * Continue a left-behind draft ('draft_backup.js').
 *
 * It knows the rows as they looked on first opening. Since then an own row
 * may have arrived at the server and carry another number, a weighing may
 * be gone. Every row is therefore matched to the one of now (by pack, scale
 * or piece count). The number in it stays the one that was visible then: it
 * goes out as seen, and if someone else has counted in the meantime, the
 * question comes. A row that no longer exists arises anew on adopting.
 */
export function restoreDraft(saved, article, rows) {
    const fresh = draftFrom(article, rows);
    const byPack = new Map(rows.filter((r) => r.gebinde).map((r) => [r.gebinde.id, r]));
    const ofNow = (then, now) =>
        now ? { ...now, eingabemenge: then?.eingabemenge ?? null } : null;
    const matched = (entry) => ({
        ...entry,
        row: ofNow(entry.row, byPack.get(entry.pack.id)),
    });

    const packs = (saved.packs ?? []).map(matched);
    // By number, or by key for a weighing that has not reached the server
    // yet (as 'sameWeighing' in 'sync/weighings.js' does). By number alone
    // every unsent bowl is 'null', and two of them would end up as the same one.
    const keyOf = (weighing) => weighing?.id ?? weighing?.kennung ?? null;
    const existing = new Map((fresh.scale?.wiegungen ?? []).map((w) => [keyOf(w), w]));
    const weighings = (saved.weighings ?? [])
        .filter((entry) => entry.isNew || existing.has(keyOf(entry.weighing)))
        .map((entry) =>
            entry.isNew ? entry : { ...entry, weighing: existing.get(keyOf(entry.weighing)) },
        );
    return {
        ...fresh,
        packs,
        removed: (saved.removed ?? []).map(matched).filter((entry) => entry.row),
        morePacks: (article?.gebinde ?? []).filter(
            (entry) => !packs.some((present) => present.pack.id === entry.id),
        ),
        weighings: weighings.length ? weighings : fresh.weighings,
        piece: fresh.piece && ofNow(saved.piece, fresh.piece),
        pieceCount: saved.pieceCount ?? "",
    };
}

/**
 * The weighing rows and, where weighing is expected, always at least one.
 *
 * Whoever weighs weighs: having to open the first row first costs a handle
 * nobody in storage wants to make. Empty it stays without consequences:
 * without weight it is skipped on adopting.
 *
 * **Where** weighing is expected is said by the location's counting list:
 * then a weighing row stands here ('scale'). Without it the scale stays
 * closed, because the product lies in the box at this location, and an
 * open field for the weight would be in the way at every article.
 * 'weighingExpected' tells the form whether it shows a button instead. If
 * the article has no pack at all, only the scale remains, and it stands open.
 */
export function weighingExpected(article, scale) {
    if (!article?.wird_gewogen) return false;
    return scale != null || !(article.gebinde ?? []).length;
}

function weighingRows(article, scale) {
    const existing = (scale?.wiegungen ?? []).map((weighing) => ({
        weighing,
        gross: formatQuantity(weighing.brutto_gramm),
        container: weighing.behaelter,
        removed: false,
        isNew: false,
    }));
    if (existing.length || !weighingExpected(article, scale)) return existing;
    return [emptyWeighing()];
}

/**
 * A fresh weighing row, **without** vessel.
 *
 * Nothing is preselected, not even the only one (until 2026-09-19 it was):
 * a preselected vessel one accepted without looking, and a wrong tare goes
 * unnoticed by anyone when counting. Whoever enters a weight chooses the
 * vessel, otherwise it does not go on ('validate').
 */
export function emptyWeighing() {
    return {
        weighing: null,
        gross: "",
        container: null,
        removed: false,
        isNew: true,
    };
}

/** A freshly opened row without weight is no input. */
function emptyAndNew(entry) {
    return entry.isNew && !String(entry.gross ?? "").trim();
}

/** Whether a stored weighing deviates from what is now in the field. */
function weighingChanged(entry) {
    if (!entry.weighing) return false;
    return (
        entry.gross !== formatQuantity(entry.weighing.brutto_gramm) ||
        entry.container !== entry.weighing.behaelter
    );
}

function ordered(draft, list, packOf) {
    const place = (pack) => {
        const where = (draft.order ?? []).indexOf(pack.id);
        return where === -1 ? Infinity : where;
    };
    return [...list].sort((a, b) => place(packOf(a)) - place(packOf(b)));
}

/**
 * Fetch a pack from the selection at the bottom into the draft.
 *
 * If it was there before and was removed, it comes back as it was: with its
 * row and the number in the field. Otherwise it arises as unexpected.
 */
export function addPack(draft, pack) {
    const before = (draft.removed ?? []).find((entry) => entry.pack.id === pack.id);
    draft.removed = (draft.removed ?? []).filter((entry) => entry !== before);
    const entry = before ?? { pack, row: null, quantity: "" };
    draft.packs = ordered(draft, [...draft.packs, entry], (e) => e.pack);
    draft.morePacks = draft.morePacks.filter((entry) => entry.id !== pack.id);
}

/**
 * Take a pack out of the draft. It then stands at the bottom for choice.
 *
 * Whether it was expected or only just fetched plays no role for that:
 * whoever mistyped brings it back with one grip. If it had a row, that is
 * only removed on adopting ('removed').
 */
export function removePack(draft, index) {
    const entry = draft.packs[index];
    if (!entry) return;
    draft.packs = draft.packs.filter((_, i) => i !== index);
    if (entry.row) draft.removed = [...(draft.removed ?? []), entry];
    draft.morePacks = ordered(draft, [...draft.morePacks, entry.pack], (pack) => pack);
}

/**
 * Whether this weighing row holds less than the chosen vessel weighs empty.
 *
 * That cannot be: mistyped or the wrong vessel chosen. The server silently
 * made a zero of it. Only the **0** is allowed, it means "there was nothing
 * to weigh here" and is thus a count, not a tare. Checked is what is new or
 * changed. An old weighing holds nobody in the form.
 */
export function belowTare(entry, containers = []) {
    if (entry.removed || (!entry.isNew && !weighingChanged(entry))) return false;
    const gross = Number(asNumberText(entry.gross));
    if (!gross) return false;
    const tare = containers.find((container) => container.id === entry.container)?.tara;
    return tare !== undefined && gross < Number(tare);
}

/**
 * Says what is wrong with an input, or nothing.
 *
 * 'containers' are those available, with their empty weight ('tara').
 */
export function validate(draft, containers = []) {
    const fields = [
        ...draft.packs.map((entry) => entry.quantity),
        draft.pieceCount,
        ...draft.weighings.filter((entry) => !entry.removed).map((entry) => entry.gross),
    ];
    if (fields.some((value) => asNumberText(value) === undefined)) {
        return t("error.numbers_only");
    }
    // The server stores three places and would refuse a fourth, but only
    // once the form is left, with a sentence from its library.
    if (fields.some(hasTooManyDecimals)) return t("error.decimals");
    // Ten places before the comma, or so many crates that the pieces no
    // longer fit into a result: the server refuses both. Until 10/2026 the
    // number went out, came back refused, and stood here as counted.
    const tooMany = draft.packs.some(
        (entry) =>
            Number(asNumberText(entry.quantity)) * Number(entry.pack.faktor_in_basiseinheit) >=
            LIMIT,
    );
    if (fields.some(isTooLarge) || tooMany) return t("error.too_large");
    // The server rejects it anyway, but only after the form is left. Taking
    // off more than stands there is a slip someone wants to see at once.
    if (fields.some(isBelowZero)) return t("error.below_zero");
    // Without vessel the tare could not be subtracted, and none is
    // preselected. As long as a weight without vessel stands there, it goes
    // neither on nor back. An empty row stays without consequences and does not count.
    const withoutContainer = draft.weighings.some(
        (entry) => !entry.removed && String(entry.gross ?? "").trim() && entry.container == null,
    );
    if (withoutContainer) return t("error.no_container");
    const tooLight = draft.weighings.find((entry) => belowTare(entry, containers));
    if (tooLight) {
        const container = containers.find((entry) => entry.id === tooLight.container);
        return t("error.below_tare", {
            gross: tooLight.gross,
            container: container.name,
            tare: formatQuantity(container.tara),
        });
    }
    return "";
}

/**
 * Adopts the draft: create rows, set quantities, write weighings.
 *
 * A row only arises once something is really entered. An empty unexpected
 * row would only be ballast in the check list.
 *
 * Sent is **once at the end**: otherwise moving on waits for a
 * network round trip per changed row, in a dead spot even for every timeout.
 *
 * Every step for the storage the first one found ('taken'): signed out in
 * between, the rest writes nothing into the storage of the next account.
 */
export async function applyDraft(draft, { inventoryId, location, article }) {
    const taken = currentMark();
    for (const entry of draft.removed ?? []) {
        await removePosition(entry.row, false, taken);
    }
    for (const entry of draft.packs) {
        const quantity = asNumberText(entry.quantity);
        if (quantity === quantityValue(entry.row)) continue;
        if (quantity === null && !entry.row) continue;
        const row =
            entry.row ??
            (await createPosition(
                { inventoryId, location, article, pack: entry.pack },
                false,
                taken,
            ));
        // The person saw the row as it stood when opening the form. The draft
        // holds it that way, a background sync changes nothing about that. A
        // new row was empty.
        await enterQuantity(row, quantity, false, entry.row?.eingabemenge ?? null, taken);
    }

    if (draft.pieceCount !== formatQuantity(draft.piece?.eingabemenge)) {
        const quantity = asNumberText(draft.pieceCount);
        if (quantity !== null || draft.piece) {
            const row =
                draft.piece ??
                (await createPosition(
                    { inventoryId, location, article, pack: null },
                    false,
                    taken,
                ));
            await enterQuantity(row, quantity, false, draft.piece?.eingabemenge ?? null, taken);
        }
    }

    let scale = draft.scale;
    for (const entry of draft.weighings) {
        if (entry.removed && entry.weighing) {
            await removeWeighing(scale, entry.weighing, false, taken);
            continue;
        }
        if (!entry.isNew && weighingChanged(entry)) {
            await changeWeighing(
                scale,
                entry.weighing,
                asNumberText(entry.gross),
                entry.container,
                false,
                taken,
            );
            continue;
        }
        if (!entry.isNew || entry.removed || emptyAndNew(entry)) continue;
        scale =
            scale ??
            (await createPosition({ inventoryId, location, article, pack: null }, false, taken));
        await createWeighing(scale, asNumberText(entry.gross), entry.container, false, taken);
    }

    await submit(taken);
}

function quantityValue(row) {
    return row?.eingabemenge ? asNumberText(formatQuantity(row.eingabemenge)) : null;
}
