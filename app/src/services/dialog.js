/**
 * What a dialog owes the keyboard and the screen reader.
 *
 * Until 10/2026 the three dialogs of the app opened without moving the
 * focus, did not close on Escape and let Tab wander into the page behind
 * them. Whoever counts with a keyboard or a screen reader did not notice
 * that a dialog was open at all.
 *
 * A Svelte action: 'use:modal={close}' on the dialog's element. On mount
 * the first field (or else the first button) inside gets the focus, Escape
 * calls 'close', Tab stays inside, and on destroy the focus returns to
 * where it was.
 */

const FOCUSABLE =
    'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function focusable(node) {
    return [...node.querySelectorAll(FOCUSABLE)].filter((element) => !element.hidden);
}

/** The first field, if there is one: that is where the work of a dialog starts. */
function entry(node) {
    const elements = focusable(node);
    return elements.find((element) => element.matches("input, select, textarea")) ?? elements[0];
}

export function modal(node, close) {
    const opener = document.activeElement;
    (entry(node) ?? node).focus();

    function onKeydown(event) {
        if (event.key === "Escape") {
            event.preventDefault();
            close();
            return;
        }
        if (event.key !== "Tab") return;
        const elements = focusable(node);
        if (!elements.length) {
            event.preventDefault();
            return;
        }
        const start = elements[0];
        const end = elements.at(-1);
        if (
            event.shiftKey &&
            (document.activeElement === start || document.activeElement === node)
        ) {
            event.preventDefault();
            end.focus();
        } else if (!event.shiftKey && document.activeElement === end) {
            event.preventDefault();
            start.focus();
        }
    }

    node.addEventListener("keydown", onKeydown);
    return {
        destroy() {
            node.removeEventListener("keydown", onKeydown);
            if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
        },
    };
}
