/**
 * What is not at the server yet, and the request not to clear it away.
 */

import { afterEach, beforeEach, expect, it } from "vitest";

import {
    SIGN_OUT_OWED,
    clearAll,
    db,
    remember,
    remembered,
    requestPersistence,
    unsentCount,
} from "./storage.js";

const real = Object.getOwnPropertyDescriptor(globalThis.navigator, "storage");

beforeEach(async () => {
    await Promise.all([db.state.clear(), db.queue.clear()]);
});

afterEach(() => {
    if (real) Object.defineProperty(globalThis.navigator, "storage", real);
    else delete globalThis.navigator.storage;
});

function setStorage(value) {
    Object.defineProperty(globalThis.navigator, "storage", { value: value, configurable: true });
}

it("counts jobs and open questions, because both hold an own number", async () => {
    expect(await unsentCount()).toBe(0);
    await db.queue.bulkAdd([{ kind: "quantity" }, { kind: "weighing" }]);
    await remember("quantity_conflicts", [{ id: "k1" }]);
    // Rejected things do not count: that already failed at the server.
    await remember("rejected", [{ kind: "quantity" }]);

    expect(await unsentCount()).toBe(3);
});

/**
 * A sign-out the server did not hear stays noted through every clearing: the
 * website's session it should have ended is still there ('fromSession' in
 * 'api.js'), and so is the token's validity. Only a sign-out that decides
 * about it changes the note. What it names stays as it is: until 10/2026
 * the note was a plain 'true'.
 */
it("carries a sign-out the server has not heard over every clearing", async () => {
    const owed = { jti: "101abc", csrf: "abc" };
    await clearAll({ signOutOwed: owed });
    expect(await remembered(SIGN_OUT_OWED)).toEqual(owed);

    await clearAll();
    expect(await remembered(SIGN_OUT_OWED)).toEqual(owed);

    await clearAll({ signOutOwed: false });
    expect(await remembered(SIGN_OUT_OWED)).toBeNull();

    // One noted by the version before stays what it was.
    await remember(SIGN_OUT_OWED, true);
    await clearAll();
    expect(await remembered(SIGN_OUT_OWED)).toBe(true);
});

it("asks the browser for persistent storage", async () => {
    let asked = false;
    setStorage({
        persist: () => {
            asked = true;
            return Promise.resolve(true);
        },
    });

    expect(await requestPersistence()).toBe(true);
    expect(asked).toBe(true);
});

it("copes without the API and with a refusal", async () => {
    setStorage(undefined);
    expect(await requestPersistence()).toBe(false);

    setStorage({ persist: () => Promise.reject(new Error("nicht erlaubt")) });
    expect(await requestPersistence()).toBe(false);
});
