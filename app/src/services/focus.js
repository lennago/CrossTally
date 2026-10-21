/**
 * Where the focus goes in the entry form.
 *
 * Typing happens at once: what is in a field is selected as soon as it gets
 * the focus, and the enter key leads from field to field like the arrow on
 * the iPhone. The helpers know the fields of the form ('content'), not its
 * draft. Keeping the keyboard open from one form to the next is
 * 'keyboard.js'.
 */

import { tick } from "svelte";

import { belowTare } from "./draft.svelte.js";

/**
 * Where the focus wanders in turn: the fields and the **empty** vessel
 * selections. An already chosen vessel is skipped, on it goes into its
 * weight. That way the enter key runs like the arrow on the iPhone:
 * pack, vessel, weight.
 */
export function targets(content) {
    if (!content) return [];
    return [...content.querySelectorAll("input:not(:disabled), select:not(:disabled)")].filter(
        (field) => !(field instanceof HTMLSelectElement) || "empty" in field.dataset,
    );
}

/** Set the focus into 'field', open a vessel selection right away. */
export function jumpTo(field) {
    if (!field) return;
    field.focus();
    if (field instanceof HTMLSelectElement) {
        try {
            // Only whoever just tapped may open. Otherwise it throws, and
            // the selection just has the focus.
            field.showPicker?.();
        } catch {
            // No tap, no list. The focus sits nonetheless.
        }
    }
}

/** Where it sticks: a missing vessel, otherwise a weight below the tare. */
export function problemField(content, weighings, containers) {
    const index = weighings.findIndex((entry) => belowTare(entry, containers));
    const tooLight =
        index < 0 ? null : content?.querySelectorAll(".weighing")[index]?.querySelector("input");
    return missingContainer(content) ?? tooLight ?? null;
}

/** The first empty vessel selection of a weighing that already has a weight. */
function missingContainer(content) {
    return (
        [...(content?.querySelectorAll(".weighing") ?? [])]
            .filter((row) => row.querySelector("input:not(:disabled)")?.value.trim())
            .map((row) => row.querySelector("select[data-empty]:not(:disabled)"))
            .find(Boolean) ?? null
    );
}

/**
 * After choosing the vessel, weighing follows: the weight of the same row is next.
 *
 * Only after 'tick()': until then the field is still disabled, and a
 * disabled field takes no focus.
 */
export async function containerChosen(event) {
    const row = event.currentTarget.closest(".weighing");
    await tick();
    jumpTo(row?.querySelector("input:not(:disabled)"));
}

/**
 * Select what is in a field on the focus, and once more on the tap that brought it.
 *
 * One per form: the three handlers share which field just got the focus.
 */
export function selectOnFocus() {
    //: The field that just got the focus. The finger tapping it would set the
    //: caret right afterwards and undo the selection again.
    let fresh = null;

    return {
        /** Select what is in the field so the first digit replaces it. */
        selectAll(event) {
            const field = event.target;
            if (!(field instanceof HTMLInputElement) || field.type !== "text") return;
            if (containerFirst(field)) return;
            fresh = field;
            field.setSelectionRange(0, field.value.length);
        },

        /** The tap that first focused the field selects it once more. */
        tapAfterFocus(event) {
            if (event.target !== fresh) return;
            fresh = null;
            event.target.setSelectionRange(0, event.target.value.length);
        },

        /** Whoever already types or sets the caret deliberately means it. */
        released() {
            fresh = null;
        },
    };
}

/**
 * Whoever lands in the weight without having chosen a vessel is led to the selection.
 *
 * Without a vessel the tare does not come off, and nothing can be typed
 * here ('readonly'): a missing vessel should not even occur. Whoever gets
 * here is led into the selection, which opens in the process. After the
 * choice the weight is free and next ('containerChosen').
 *
 * On the iPhone the form assistant skips a read-only field, and selection
 * lists it skips anyway. Its arrow therefore leads nowhere in a weighing
 * row, there the field is tapped. That is the price for nothing being
 * enterable without a vessel.
 */
function containerFirst(field) {
    if (!field.readOnly) return false;
    const selection = field.closest(".weighing")?.querySelector("select[data-empty]");
    if (!selection) return false;
    jumpTo(selection);
    return true;
}

/**
 * The enter key leads into the next field and after the last one on like
 * "Next ›" ('onLast'). Without that it would close the keyboard on Android,
 * and the next grip would go across the screen.
 */
export function toNextField(event, content, onLast) {
    if (event.key !== "Enter" || event.isComposing) return;
    event.preventDefault();
    const fields = targets(content);
    const following = fields[fields.indexOf(event.currentTarget) + 1];
    if (!following) return void onLast();
    jumpTo(following);
    // Centred, not to the edge: at the bottom now stands the footer bar.
    following.scrollIntoView({ block: "center" });
}
