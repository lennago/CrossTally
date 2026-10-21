/**
 * Once an account signed out, nothing of it reaches the storage or the server, in any window.
 *
 * A shared phone: Zoe signs out while a job of hers is on its way, Zara
 * signs in. Until 10/2026 Zoe's answers still landed in the storage when
 * they were refusals or questions, a job of hers went out again with
 * Zara's token after a refresh, and a second window wrote Zoe's tokens back
 * after she had signed out in the first. Zara's sign-in put her tokens next
 * to Zoe's waiting count before it was decided whose the storage is, and a
 * second window sent the count under Zara's name. Each case below once did
 * one of these.
 */

import Dexie from "dexie";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { ForeignEntries, signIn, signInWithPasskey, signOut, thisWindow } from "./api.js";
import { close, counting, loadFromStorage, refresh } from "./counting.svelte.js";
import { db, remember, remembered, unsentCount } from "./storage.js";
import {
    fetchInventories,
    fetchPositions,
    quantityConflicts,
    rejectedJobs,
    submit,
} from "./sync.js";
import { apply } from "./language.svelte.js";
import { clearStorage } from "../test_support.js";

const ZOE = { id: 1, vorname: "Zoe", benutzername: "zoe" };
const ZARA = { id: 2, vorname: "Zara", benutzername: "zara" };

/** A 'fetch' answer, an empty one for 'null'. */
const answer = (data, status = 200) => ({
    ok: status < 400,
    status,
    text: () => Promise.resolve(data === null ? "" : JSON.stringify(data)),
});

/** A gate a fake server waits at until the case lets it through. */
function gate() {
    let open;
    const passed = new Promise((resolve) => (open = resolve));
    return { passed, open };
}

const row = (id, extra = {}) => ({
    id,
    inventur_id: 1,
    ort: { id: 7, name: "Theke", bereich: "Theke" },
    artikel_id: 11,
    artikel: "Artikel C",
    gebinde: null,
    wird_gewogen: false,
    wiegungen: [],
    eingabemenge: null,
    aktiv: true,
    zuletzt_geaendert: "2026-01-31T10:00:00+01:00",
    ...extra,
});

/** Zoe is signed in, and her count of 3 at the counter waits to go out. */
async function zoeWithCount() {
    await db.state.bulkPut([
        { key: "me", value: ZOE },
        { key: "access_token", value: "a-zoe" },
        { key: "refresh_token", value: "r-zoe" },
    ]);
    await db.positions.put({ ...row(100), server_id: 100, location_id: 7, eingabemenge: "3" });
    await db.queue.add({
        kind: "quantity",
        position: 100,
        payload: { eingabemenge: "3", eingabemenge_vorher: null },
    });
}

/**
 * A server. 'quantity' answers Zoe's count, the rest is signing out and in as Zara.
 * Back come the requests, with the token they carried.
 */
function server(quantity) {
    const requests = [];
    globalThis.fetch = async (url, options = {}) => {
        const path = url.replace("/api/v1", "");
        const token = options.headers?.Authorization ?? "";
        requests.push(`${options.method ?? "GET"} ${path} ${token}`.trim());
        if (path === "/zaehlpositionen/100/") return await quantity(token);
        if (path === "/abmelden/") return answer(null, 204);
        if (path === "/token/") return answer({ access: "a-zara", refresh: "r-zara" });
        if (path === "/ich/") return answer(ZARA);
        return answer({ detail: "?" }, 404);
    };
    return requests;
}

/** Nothing of Zoe's lies in the storage: no row, no job, no question, no refusal. */
async function expectNothingOfZoe() {
    expect(await db.positions.count()).toBe(0);
    expect(await db.queue.count()).toBe(0);
    expect(await quantityConflicts()).toEqual([]);
    expect(await rejectedJobs()).toEqual([]);
    expect(await unsentCount()).toBe(0);
}

let opened = [];

beforeEach(async () => {
    await clearStorage();
    thisWindow.reload = vi.fn();
});

afterEach(() => {
    for (const storage of opened) storage.db.close();
    opened = [];
    close();
});

/** The app in a second window: its own modules on the same storage. */
async function secondWindow() {
    vi.resetModules();
    const window = {
        api: await import("./api.js"),
        storage: await import("./storage.js"),
        sync: await import("./sync.js"),
    };
    window.api.thisWindow.reload = vi.fn();
    opened.push(window.storage);
    // Loaded: it knows which clearing it works on, as every window does long
    // before someone signs out in another one.
    await window.storage.markValue(window.storage.currentMark());
    return window;
}

it("keeps a question that comes after signing out away from the next account", async () => {
    await zoeWithCount();
    const held = gate();
    let asked = false;
    server(async () => {
        asked = true;
        await held.passed;
        return answer({ konflikt: "eingabemenge", aktuell: row(100, { eingabemenge: "5" }) }, 409);
    });

    const sending = submit().catch((error) => error);
    await vi.waitFor(() => expect(asked).toBe(true));
    await signOut();
    held.open();
    await sending;

    await expectNothingOfZoe();
    expect(await signIn("zara", "geheim")).toMatchObject({ id: 2 });
    await expectNothingOfZoe();
});

it("keeps a refusal that comes after signing out off the next account's list", async () => {
    await zoeWithCount();
    const held = gate();
    let asked = false;
    server(async () => {
        asked = true;
        await held.passed;
        return answer({ eingabemenge: ["Das fasst kein Ergebnis."] }, 400);
    });

    const sending = submit().catch((error) => error);
    await vi.waitFor(() => expect(asked).toBe(true));
    await signOut();
    held.open();
    await sending;

    await expectNothingOfZoe();
});

it("does not send a job again with the next account's token after refreshing", async () => {
    await zoeWithCount();
    const held = gate();
    let refreshing = false;
    const requests = server(async (token) =>
        token === "Bearer a-zara"
            ? answer(row(100, { eingabemenge: "3" }))
            : answer({ detail: "abgelaufen" }, 401),
    );
    const answers = globalThis.fetch;
    globalThis.fetch = async (url, options) => {
        if (!url.endsWith("/token/erneuern/")) return await answers(url, options);
        refreshing = true;
        await held.passed;
        // Zoe's token went on the blocklist with her sign-out.
        return answer({ detail: "Token is blacklisted" }, 401);
    };

    const sending = submit().catch((error) => error);
    await vi.waitFor(() => expect(refreshing).toBe(true));
    await signOut();
    await signIn("zara", "geheim");
    held.open();
    await sending;

    expect(requests).not.toContain("PATCH /zaehlpositionen/100/ Bearer a-zara");
    expect(await remembered("refresh_token")).toBe("r-zara");
    await expectNothingOfZoe();
});

/** The sentence can show in a form left open: in the app's language, from its texts. */
it("says in the app's language that the account was signed out meanwhile", async () => {
    await db.state.bulkPut([
        { key: "access_token", value: "a-zoe" },
        { key: "refresh_token", value: "r-zoe" },
    ]);
    globalThis.fetch = async () => answer([]);
    try {
        for (const [choice, sentence] of [
            ["de", "Du wurdest inzwischen abgemeldet."],
            ["en", "You have been signed out in the meantime."],
        ]) {
            apply(choice);
            const asked = fetchInventories().catch((error) => error);
            await signOut(false);

            expect((await asked).data.detail).toBe(sentence);
        }
    } finally {
        apply("de");
    }
});

it("takes the storage a request belongs to before anything is awaited", async () => {
    await db.state.bulkPut([
        { key: "access_token", value: "a-zoe" },
        { key: "refresh_token", value: "r-zoe" },
    ]);
    globalThis.fetch = async () => answer([{ id: 1, haus: "Kino Ost", stichtag: "2026-01-31" }]);

    // Signed out in the same moment: until 10/2026 the request read the
    // token first and only then which storage it belonged to.
    const asked = fetchInventories().catch((error) => error);
    await signOut(false);

    expect((await asked).status).toBe(0);
    expect(await db.inventories.count()).toBe(0);
});

it("leaves nothing of an answer when the sign-out comes while it is written", async () => {
    await db.state.bulkPut([
        { key: "access_token", value: "a-zoe" },
        { key: "refresh_token", value: "r-zoe" },
    ]);
    globalThis.fetch = async () => answer({ id: 1, haus: "Kino Ost", zaehlpositionen: [row(100)] });
    const put = db.inventories.put.bind(db.inventories);
    let signingOut = null;
    vi.spyOn(db.inventories, "put").mockImplementationOnce((...args) => {
        // The tap on "Sign out" between the first write and the rest: a
        // step of its own, outside the one that writes.
        signingOut = Dexie.ignoreTransaction(() => signOut(false));
        return put(...args);
    });

    await fetchPositions(1).catch((error) => error);
    await signingOut;
    vi.restoreAllMocks();

    expect(await db.inventories.count()).toBe(0);
    expect(await db.positions.count()).toBe(0);
    // Until 10/2026 the mark of the sync could stay without its rows.
    expect(await remembered("synced:1")).toBeNull();
});

it("keeps another window's refresh out of the storage once signed out here", async () => {
    await db.state.bulkPut([
        { key: "me", value: ZOE },
        { key: "access_token", value: "a-zoe" },
        { key: "refresh_token", value: "r-zoe" },
    ]);
    const other = await secondWindow();
    const held = gate();
    let refreshing = false;
    globalThis.fetch = async (url) => {
        if (url.endsWith("/token/erneuern/")) {
            refreshing = true;
            await held.passed;
            return answer({ access: "a-zoe-2", refresh: "r-zoe-2" });
        }
        if (url.endsWith("/abmelden/")) return answer(null, 204);
        return answer({ detail: "abgelaufen" }, 401);
    };

    const there = other.api.get("/inventuren/").catch((error) => error);
    await vi.waitFor(() => expect(refreshing).toBe(true));
    await signOut();
    held.open();

    const error = await there;
    expect(await remembered("access_token")).toBeNull();
    expect(await remembered("refresh_token")).toBeNull();
    expect(error.status).toBe(0);
    // The other window's state belongs to Zoe: it loads anew.
    expect(other.api.thisWindow.reload).toHaveBeenCalled();
});

it("lets a window left from before neither write nor send, and loads it anew", async () => {
    await zoeWithCount();
    const other = await secondWindow();
    const requests = server(async () => answer(row(100, { eingabemenge: "4" })));
    await signOut();
    requests.length = 0;

    // There a number typed after the sign-out here, and its next sync.
    const typed = await other.sync
        .enterQuantity({ id: 100, inventur_id: 1, eingabemenge: "3" }, "4")
        .catch((error) => error);
    const synced = await other.sync.fetchInventories().catch((error) => error);

    expect(requests).toEqual([]);
    await expectNothingOfZoe();
    expect([typed.status, synced.status]).toEqual([0, 0]);
    expect(other.api.thisWindow.reload).toHaveBeenCalled();
    expect(thisWindow.reload).not.toHaveBeenCalled();
});

it("leaves the state of the next sign-in alone when signed out during a sync", async () => {
    await zoeWithCount();
    const held = gate();
    let asked = false;
    server(async () => {
        asked = true;
        await held.passed;
        return answer({ detail: "Serverfehler" }, 500);
    });

    const running = refresh();
    await vi.waitFor(() => expect(asked).toBe(true));
    await signOut();
    close();
    held.open();
    await running;

    // Until 10/2026 the run set "offline" and "synced" for the next account.
    expect(counting.online).toBe(true);
    expect(counting.synced).toBe(false);
    expect(counting.message).toBe("");
    await expectNothingOfZoe();
});

/**
 * What waits, what was refused and when the last sync was, counted for the
 * header and the login form. A run during the sign-out does not count anew
 * ('refresh'). Until 10/2026 the next account saw Zoe's counts.
 */
it("leaves no count of the previous account in memory after signing out during a sync", async () => {
    await zoeWithCount();
    await remember("rejected", [{ kind: "quantity", label: "Artikel C", reason: "Nein." }]);
    await remember("quantity_conflicts", [{ id: "k1", position: 100 }]);
    await remember("last_synced", "2026-01-31T10:00:00.000Z");
    await loadFromStorage();
    expect([counting.pending, counting.rejected, counting.quantityConflicts]).toEqual([1, 1, 1]);
    const held = gate();
    let asked = false;
    server(async () => {
        asked = true;
        await held.passed;
        return answer({ detail: "Serverfehler" }, 500);
    });

    const running = refresh();
    await vi.waitFor(() => expect(asked).toBe(true));
    await signOut();
    close();
    held.open();
    await running;

    expect([counting.pending, counting.rejected, counting.quantityConflicts]).toEqual([0, 0, 0]);
    expect(counting.lastSynced).toBeNull();
});

/** Every value the storage takes for the access token while 'during' runs. */
async function accessTokensWritten(during) {
    const written = [];
    const creating = (key, entry) => {
        if (key === "access_token") written.push(entry.value);
    };
    const updating = (changes, key) => {
        if (key === "access_token" && "value" in changes) written.push(changes.value);
    };
    db.state.hook("creating", creating);
    db.state.hook("updating", updating);
    try {
        await during();
    } finally {
        db.state.hook("creating").unsubscribe(creating);
        db.state.hook("updating").unsubscribe(updating);
    }
    return written;
}

it("sends no count of the previous account under the new one while a sign-in is decided", async () => {
    await zoeWithCount();
    // Zoe's sign-in has expired: the form shows, and her count waits.
    await db.state.bulkDelete(["access_token", "refresh_token"]);
    const other = await secondWindow();
    const held = gate();
    let asked = false;
    const requests = server(async (token) =>
        token ? answer(row(100, { eingabemenge: "3" })) : answer({ detail: "Wer bist du?" }, 401),
    );
    const answers = globalThis.fetch;
    globalThis.fetch = async (url, options) => {
        if (url.endsWith("/ich/")) {
            asked = true;
            await held.passed;
        }
        return await answers(url, options);
    };

    const signingIn = signIn("zara", "geheim").catch((error) => error);
    await vi.waitFor(() => expect(asked).toBe(true));
    // The other window's sync in the background, at this very moment.
    await other.sync.submit().catch((error) => error);
    held.open();

    expect(await signingIn).toBeInstanceOf(ForeignEntries);
    expect(requests).not.toContain("PATCH /zaehlpositionen/100/ Bearer a-zara");
    expect(await db.queue.count()).toBe(1);
});

/**
 * Zara signs in in one window while Zoe signs in in another one and counts
 * without network. Until 10/2026 the switch was decided on who the storage
 * held before Zara's requests went out: nobody. Her pair went in next to
 * Zoe's waiting count, and the next sync sent it under Zara's name.
 */
it("decides a switch on the account the storage holds when the sign-in comes back", async () => {
    const other = await secondWindow();
    const held = gate();
    let asked = false;
    globalThis.fetch = async (url, options = {}) => {
        const path = url.replace("/api/v1", "");
        if (path === "/token/") {
            const { username } = JSON.parse(options.body);
            return answer({ access: `a-${username}`, refresh: `r-${username}` });
        }
        if (path === "/ich/" && options.headers.Authorization === "Bearer a-zoe") {
            return answer(ZOE);
        }
        if (path === "/ich/") {
            asked = true;
            await held.passed;
            return answer(ZARA);
        }
        return answer({ detail: "?" }, 404);
    };

    const signingIn = signIn("zara", "geheim").catch((error) => error);
    await vi.waitFor(() => expect(asked).toBe(true));
    await other.api.signIn("zoe", "geheim");
    await other.sync.enterQuantity({ id: 100, inventur_id: 1, eingabemenge: null }, "3", false);
    held.open();

    const error = await signingIn;
    expect(error).toBeInstanceOf(ForeignEntries);
    expect([error.who.id, error.count]).toEqual([1, 1]);
    expect(await remembered("access_token")).toBe("a-zoe");
    expect(await remembered("me")).toMatchObject({ id: 1 });
    expect(await db.queue.count()).toBe(1);
});

/** The question comes before the new account's pair is in the storage, with a passkey too. */
it("writes no token of the new account before the switch is decided", async () => {
    const bytes = (...values) => Uint8Array.from(values).buffer;
    globalThis.navigator.credentials = {
        get: () =>
            Promise.resolve({
                id: "AQID",
                rawId: bytes(1, 2, 3),
                type: "public-key",
                response: {
                    clientDataJSON: bytes(7),
                    authenticatorData: bytes(8),
                    signature: bytes(9),
                    userHandle: bytes(10),
                },
            }),
    };
    for (const way of ["password", "passkey"]) {
        await clearStorage();
        await zoeWithCount();
        server(async () => answer(row(100)));
        const answers = globalThis.fetch;
        globalThis.fetch = async (url, options) => {
            if (url.includes("/passkey/anmelden/optionen/")) {
                return answer({ options: { challenge: "AQID" }, challenge: "signiert" });
            }
            if (url.includes("/passkey/anmelden/")) {
                return answer({ access: "a-zara", refresh: "r-zara", ich: ZARA });
            }
            return await answers(url, options);
        };

        const written = await accessTokensWritten(async () => {
            const asked = way === "password" ? signIn("zara", "geheim") : signInWithPasskey();
            expect(await asked.catch((error) => error), way).toBeInstanceOf(ForeignEntries);
        });

        expect(written, way).toEqual([]);
        expect(await remembered("access_token"), way).toBe("a-zoe");
    }
    delete globalThis.navigator.credentials;
});
