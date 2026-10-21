/**
 * The way through the app, read from the address: '#/inventory/3/location/7'.
 *
 * Hash instead of path, so Django has to redirect nothing for subpages. The
 * app lies under '/app/', and everything behind it the browser handles
 * itself. A router package of its own would be too much for five views.
 */

function readParts() {
    const raw = window.location.hash.replace(/^#\/?/, "");
    return raw ? raw.split("/").map(decodeURIComponent) : [];
}

function addressOf(parts) {
    return `#/${parts.map(encodeURIComponent).join("/")}`;
}

export const route = $state({ parts: readParts() });

//: Whoever currently holds something unsaved places themselves in the way back here.
let backGuard = null;
let asking = false;

/**
 * Guard the way back: 'check' runs before "Back" leaves the view.
 *
 * It tidies up (the entry form saves) and says with 'true' that it may go
 * on. With 'false' the view stays, say because an input is wrong and the
 * person has to see that. Returns the unsubscribe.
 */
export function guardBack(check) {
    backGuard = check;
    return () => {
        if (backGuard === check) backGuard = null;
    };
}

async function mayProceed() {
    if (!backGuard) return true;
    // Back twice in a row should not save twice.
    if (asking) return false;
    asking = true;
    try {
        return await backGuard();
    } finally {
        asking = false;
    }
}

window.addEventListener("hashchange", async () => {
    const next = readParts();
    // 'go' has already set the path itself. Then there is nothing to do here.
    if (addressOf(next) === addressOf(route.parts)) return;
    // From here it was the device: the back button or the swipe gesture. It
    // should do the same as "Back" in the bar, so ask the guard first.
    const here = route.parts;
    if (await mayProceed()) {
        route.parts = readParts();
    } else if (addressOf(readParts()) !== addressOf(here)) {
        // The view stays, so the address has to fit it again too.
        // 'pushState' triggers no 'hashchange'.
        window.history.pushState(null, "", addressOf(here));
    }
});

/** Without question, for paths the code goes itself, and for "Cancel". */
export function go(...parts) {
    window.location.hash = addressOf(parts);
    // Follow up right away: that way 'hashchange' above recognises which
    // change came from the device and which from here.
    route.parts = readParts();
}

/**
 * Like 'go', but **in place of** the current address instead of behind it.
 *
 * For "Next ›": otherwise after twenty articles a pile of entry
 * forms would lie in the history, and the device's back button would go
 * back through it form by form, instead of to the list like "Back" in the
 * bar. 'replaceState' triggers no 'hashchange'.
 */
export function replace(...parts) {
    window.history.replaceState(null, "", addressOf(parts));
    route.parts = readParts();
}

/**
 * One level up, past the guard.
 *
 * Always one level up instead of 'history.back()': whoever came in through
 * a search hit should still see the level above and not the last page they
 * happened to be on. A level is a pair of name and number ('location/7'),
 * except for a page without a number of its own ('review'): that goes alone.
 */
export function up() {
    if (route.parts.length <= 1) return go();
    go(...route.parts.slice(0, route.parts.length % 2 === 1 ? -1 : -2));
}

/** "Back" in the bar: first the guard, then one level up. */
export async function back() {
    if (await mayProceed()) up();
}
