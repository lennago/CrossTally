/**
 * A choice that hangs on the account and applies at once on the device.
 *
 * Language and appearance work the same way: written via 'PATCH /ich/',
 * read with every response from '/ich/', remembered in the browser under a
 * key of its own so it applies at startup and offline. A choice made
 * without network waits under '<key>_pending' until it can go out, and as
 * long as it waits, the state from the server does not overrule it. Until
 * 10/2026 both stores carried this logic twice.
 *
 * 'clearAll' in 'storage.js' removes the pending keys with the account.
 */

import { guarded, send } from "./api.js";
import { currentMark, remember } from "./storage.js";

function read(key) {
    // In private windows the access itself can throw.
    try {
        return localStorage.getItem(key);
    } catch {
        return null;
    }
}

function write(key, value) {
    try {
        if (value === null) localStorage.removeItem(key);
        else localStorage.setItem(key, value);
    } catch {
        // Then the choice applies only while the app is open.
    }
}

/**
 * The store and its four operations.
 *
 * 'key' is the browser's key, 'field' the account's field in '/ich/',
 * 'isValid' says which values exist, 'fallback' applies for everything
 * else, and 'applied' does what the device has to show for the choice.
 */
export function accountSetting({ key, field, fallback, isValid, applied }) {
    const pendingKey = `${key}_pending`;
    const stored = read(key);
    const state = $state({ choice: isValid(stored) ? stored : fallback });
    // Applied once at the start, from what the browser remembers: before any
    // account answers, the login form too stands in the last choice. Until
    // 10/2026 an English login form was marked as German ('lang').
    applied(state.choice);

    /**
     * Applies the choice on the device and remembers it in the browser.
     * It does not go to the account.
     */
    function apply(choice) {
        if (!isValid(choice)) choice = fallback;
        state.choice = choice;
        applied(choice);
        write(key, choice);
    }

    /** Adopts what is on the account, unless an own choice still waits. */
    function fromAccount(me) {
        if (read(pendingKey)) return;
        if (isValid(me?.[field])) apply(me[field]);
    }

    /** The choice in the menu: apply at once, then to the account. */
    async function choose(choice) {
        apply(choice);
        write(pendingKey, choice);
        // The account it was chosen for: signed out meanwhile, the answer
        // is not remembered for the next one.
        const taken = currentMark();
        try {
            const me = await send("/ich/", { [field]: choice }, "PATCH", taken);
            await guarded(taken, () => remember("me", me));
            // Only clear if nothing newer has queued up in the meantime.
            if (read(pendingKey) === choice) write(pendingKey, null);
            return me;
        } catch {
            return null;
        }
    }

    /** Send out next time what was chosen without network. */
    async function submitPending() {
        const pending = read(pendingKey);
        return pending ? choose(pending) : null;
    }

    return { state, apply, fromAccount, choose, submitPending };
}
