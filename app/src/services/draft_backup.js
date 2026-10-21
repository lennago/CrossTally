/**
 * The draft of a form, written along in the storage.
 *
 * Counted is only on leaving the form ('draft.svelte.js'). Until then
 * everything lay only in memory: if the app crashed, the phone went off or
 * the device closed the app in the background, what was typed was gone.
 * That is why a copy lies here, one per inventory, location and article.
 *
 * Whoever leaves the form, whether saved or cancelled, clears it away. If
 * the app dies, it stays, and on the next opening the form asks whether it
 * should continue. Nothing is counted by that, that still happens only on
 * leaving the form.
 */

import { SignedOutMeanwhile, guarded } from "./api.js";
import { DRAFT_PREFIX, asPlainData, currentMark, db } from "./storage.js";

function keyFor({ inventoryId, locationId, articleId }) {
    return `${DRAFT_PREFIX}${inventoryId}/${locationId}/${articleId}`;
}

/**
 * 'target' says where the draft belongs: 'inventoryId', 'area',
 * 'locationId', 'articleId', plus 'label' for display. The area is there
 * because the form's address needs it.
 *
 * Only into the storage this window works on ('taken'): a form left open
 * from the account of before would otherwise leave its draft to the next
 * account, with the notice to continue it.
 */
export async function saveDraft(target, draft, taken = currentMark()) {
    await onlyHere(taken, () =>
        db.state.put({
            key: keyFor(target),
            value: asPlainData({ ...target, draft, at: new Date().toISOString() }),
        }),
    );
}

export async function savedDraft(target) {
    return (await db.state.get(keyFor(target)))?.value ?? null;
}

export async function forgetDraft(target, taken = currentMark()) {
    await onlyHere(taken, () => db.state.delete(keyFor(target)));
}

/**
 * A guarded step that keeps quiet once the storage belongs to someone else.
 *
 * The copy is a help, not a count: there is nothing to tell the person. The
 * form calls it on every change and does not wait.
 */
async function onlyHere(taken, change) {
    try {
        await guarded(taken, change);
    } catch (error) {
        if (!(error instanceof SignedOutMeanwhile)) throw error;
    }
}

/** All left-behind ones, for the notice at the top of the app. */
export async function savedDrafts() {
    const rows = await db.state.where("key").startsWith(DRAFT_PREFIX).toArray();
    return rows.map((row) => row.value);
}

/** An inventory is gone, then everything typed for it too. */
export async function forgetDrafts(inventoryId) {
    await db.state.where("key").startsWith(`${DRAFT_PREFIX}${inventoryId}/`).delete();
}
