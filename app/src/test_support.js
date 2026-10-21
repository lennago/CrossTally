/**
 * What the Vitest files kept building themselves, six to eight times each.
 *
 * A server answer as 'fetch' returns it, the same as a spy, a button found
 * by its text, and an empty storage. 'vitest.setup.js' resets 'fetch' after
 * every case, so no file has to.
 */

import { vi } from "vitest";

import { close } from "./services/counting.svelte.js";
import { db } from "./services/storage.js";

/** A resolved 'fetch' answer with this JSON body. */
export const respond = (data, status = 200) =>
    Promise.resolve({
        ok: status < 400,
        status,
        text: () => Promise.resolve(JSON.stringify(data)),
    });

/** The same as a spy, to look at the calls afterwards. */
export function responds(data, status = 200) {
    return vi.fn(() => respond(data, status));
}

/** The first button under 'root' whose text contains 'text'. */
export const findButton = (root, text) =>
    [...root.querySelectorAll("button")].find((entry) => entry.textContent.includes(text));

/** Every table empty and the counting store closed, as before a case. */
export async function clearStorage() {
    await Promise.all(db.tables.map((table) => table.clear()));
    close();
}
