/**
 * Signing out and switching accounts clear the storage. What is not at the
 * server yet must not silently vanish in the process.
 *
 * Tapped "Sign out" in a dead spot, a colleague signs in on the same phone,
 * someone else is signed in on the website: every time what still waited was
 * gone. Now a question comes first, or it stays with the previous account.
 */

import { mount, unmount } from "svelte";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import App from "./App.svelte";
import { ForeignEntries, signIn, fromSession } from "./services/api.js";
import { close, counting } from "./services/counting.svelte.js";
import { saveDraft, savedDrafts } from "./services/draft_backup.js";
import { go, route } from "./services/route.svelte.js";
import { SIGN_OUT_OWED, db, remembered, remember } from "./services/storage.js";
import { clearStorage, findButton, respond } from "./test_support.js";

const ZOE = { id: 1, vorname: "Zoe", benutzername: "zoe" };
const ZARA = { id: 2, vorname: "Zara", benutzername: "zara" };

/** A server at which 'who' signs in. Sent quantities it accepts. */
function server(who, { session = true } = {}) {
    const requests = [];
    globalThis.fetch = (url, options = {}) => {
        const path = url.replace("/api/v1", "");
        requests.push(`${options.method ?? "GET"} ${path}`);
        if (path === "/token/") return respond({ access: `a-${who.id}`, refresh: `r-${who.id}` });
        if (path === "/token/aus-sitzung/") {
            return session
                ? respond({ access: `a-${who.id}`, refresh: `r-${who.id}`, ich: who })
                : respond({ detail: "keine Sitzung" }, 403);
        }
        if (path === "/ich/") return respond(who);
        if (path === "/inventuren/") return respond([]);
        if (path.startsWith("/zaehlpositionen/")) return respond({ id: 100, inventur_id: 1 });
        if (path === "/abmelden/") return respond({});
        return respond({ detail: "?" }, 404);
    };
    return requests;
}

async function asZoeWithPending() {
    await db.state.bulkPut([
        { key: "me", value: ZOE },
        { key: "access_token", value: "a-zoe" },
        { key: "refresh_token", value: "r-zoe" },
    ]);
    await db.positions.put({ id: 100, inventur_id: 1, server_id: 100, eingabemenge: "3" });
    await db.queue.add({
        kind: "quantity",
        position: 100,
        payload: { eingabemenge: "3", eingabemenge_vorher: null },
    });
}

/**
 * Zoe is signed in, and a form of hers was left unsaved: the app died while
 * she typed. Typed, but not in the queue ('draft_backup.js').
 */
async function asZoeWithDraft() {
    await db.state.bulkPut([
        { key: "me", value: ZOE },
        { key: "access_token", value: "a-zoe" },
        { key: "refresh_token", value: "r-zoe" },
    ]);
    await db.inventories.put({ id: 1, haus: "Kino Ost", stichtag: "2026-01-31" });
    await saveDraft(
        { inventoryId: 1, area: "Theke", locationId: 7, articleId: 11, label: "Artikel C" },
        { packs: [{ quantity: "17" }] },
    );
}

let target;

beforeEach(async () => {
    await clearStorage();
    target = document.createElement("div");
    document.body.append(target);
});

afterEach(() => {
    target.remove();
    window.location.hash = "";
    document.cookie = "csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT";
    globalThis.fetch = () => Promise.reject(new TypeError("kein Netz im Test"));
    close();
});

/** The text of the page as a person reads it: without the template's line breaks. */
const text = () => target.textContent.replace(/\s+/g, " ");

const button = (text) => findButton(target, text);

// ---------------------------------------------------------------------------
// Signing out
// ---------------------------------------------------------------------------

async function openMenu() {
    const app = mount(App, { target: target });
    await vi.waitFor(() => expect(target.querySelector("button.account")).toBeTruthy());
    target.querySelector("button.account").click();
    await vi.waitFor(() => expect(button("Abmelden")).toBeTruthy());
    return app;
}

it("asks before signing out when something still waits, and deletes nothing", async () => {
    await asZoeWithPending();
    const app = await openMenu();

    button("Abmelden").click();

    await vi.waitFor(() => expect(text()).toContain("1 Eingabe ist noch nicht beim Server"));
    expect(await db.queue.count()).toBe(1);
    expect(await remembered("access_token")).toBe("a-zoe");

    button("Trotzdem abmelden und löschen").click();

    await vi.waitFor(async () => expect(await db.queue.count()).toBe(0));
    await vi.waitFor(() => expect(target.querySelector("input[type=password]")).toBeTruthy());
    unmount(app);
});

/** A sync saves no form left behind: only deleting is offered for it. */
it("asks before signing out when a form was left unsaved, and deletes nothing", async () => {
    await asZoeWithDraft();
    const app = await openMenu();

    button("Abmelden").click();

    await vi.waitFor(() => expect(text()).toContain("1 Eingabe wurde nicht gespeichert"));
    expect(text()).not.toContain("noch nicht beim Server");
    expect(button("Jetzt abgleichen")).toBeUndefined();
    expect(await savedDrafts()).toHaveLength(1);
    expect(await remembered("access_token")).toBe("a-zoe");

    button("Trotzdem abmelden und löschen").click();

    await vi.waitFor(() => expect(target.querySelector("input[type=password]")).toBeTruthy());
    expect(await savedDrafts()).toEqual([]);
    unmount(app);
});

/**
 * The login form says what still waits from before. Signed out and
 * deleted, nothing waits. Until 10/2026 the counts of the previous sign-in
 * stayed in memory, and the form said one entry still waited.
 */
it("names no entry of before on the login form after signing out anyway", async () => {
    await asZoeWithPending();
    const app = await openMenu();
    await vi.waitFor(() => expect(counting.pending).toBe(1));
    button("Abmelden").click();
    await vi.waitFor(() => expect(button("Trotzdem abmelden und löschen")).toBeTruthy());

    button("Trotzdem abmelden und löschen").click();

    await vi.waitFor(() => expect(target.querySelector("input[type=password]")).toBeTruthy());
    expect(text()).not.toContain("Die Anmeldung ist abgelaufen");
    expect([counting.pending, counting.rejected, counting.quantityConflicts]).toEqual([0, 0, 0]);
    unmount(app);
});

it("syncs first on request, after that the question is moot", async () => {
    await asZoeWithPending();
    const app = await openMenu();
    button("Abmelden").click();
    await vi.waitFor(() => expect(button("Jetzt abgleichen")).toBeTruthy());

    const requests = server(ZOE);
    button("Jetzt abgleichen").click();

    await vi.waitFor(() => expect(button("Jetzt abgleichen")).toBeUndefined());
    expect(requests).toContain("PATCH /zaehlpositionen/100/");
    expect(await db.queue.count()).toBe(0);
    // Still signed in: signed out only with a further tap.
    expect(await remembered("access_token")).toBe("a-zoe");
    unmount(app);
});

it("signs out at once without anything pending", async () => {
    await db.state.bulkPut([
        { key: "me", value: ZOE },
        { key: "access_token", value: "a-zoe" },
        { key: "refresh_token", value: "r-zoe" },
    ]);
    const app = await openMenu();

    button("Abmelden").click();

    await vi.waitFor(() => expect(target.querySelector("input[type=password]")).toBeTruthy());
    expect(text()).not.toContain("noch nicht beim Server");
    unmount(app);
});

/**
 * A shared phone with a poor connection: whoever taps "Sign out" hands the
 * phone on right away, and the request to the server can hang for a long
 * time. Until 10/2026 the app showed and kept the account until it ended.
 */
it("lets go of the account at once and tells the server afterwards", async () => {
    await db.state.bulkPut([
        { key: "me", value: ZOE },
        { key: "access_token", value: "a-zoe" },
        { key: "refresh_token", value: "r-zoe" },
    ]);
    await db.inventories.put({ id: 1, haus: "Kino Ost", stichtag: "2026-01-31" });
    let release;
    const hanging = new Promise((resolve) => (release = resolve));
    const heard = [];
    globalThis.fetch = (url, options = {}) => {
        const path = url.replace("/api/v1", "");
        heard.push(`${options.method ?? "GET"} ${path}`);
        if (path === "/abmelden/") return hanging.then(() => respond(null, 204));
        return Promise.reject(new TypeError("kein Netz"));
    };
    const app = await openMenu();
    await vi.waitFor(() => expect(text()).toContain("Kino Ost"));

    button("Abmelden").click();

    await vi.waitFor(() => expect(target.querySelector("input[type=password]")).toBeTruthy());
    expect(heard).toContain("POST /abmelden/");
    expect(text()).not.toContain("Kino Ost");
    expect(await remembered("refresh_token")).toBeNull();
    expect(await db.inventories.count()).toBe(0);
    // Owed until the server has heard it, in case the app ends before.
    expect(await remembered(SIGN_OUT_OWED)).toMatchObject({ csrf: "" });

    release();

    await vi.waitFor(async () => expect(await remembered(SIGN_OUT_OWED)).toBeNull());
    unmount(app);
});

/**
 * Signed out without network on a shared phone. The website's session in the
 * same browser lives on, and until 10/2026 the next start took it over: the
 * same account was signed in again for whoever held the phone next.
 */
it("signs nobody in from the website after a sign-out without network", async () => {
    await db.state.bulkPut([
        { key: "me", value: ZOE },
        { key: "access_token", value: "a-zoe" },
        { key: "refresh_token", value: "r-zoe" },
        { key: "from_session", value: true },
    ]);
    document.cookie = "csrftoken=abc";
    globalThis.fetch = () => Promise.reject(new TypeError("kein Netz"));
    const app = await openMenu();
    button("Abmelden").click();
    await vi.waitFor(() => expect(target.querySelector("input[type=password]")).toBeTruthy());
    unmount(app);
    close();

    // The next start, with network, and the session of before still there.
    const requests = server(ZOE);
    const again = mount(App, { target: target });

    await vi.waitFor(() => expect(requests).toContain("POST /abmelden/"));
    await vi.waitFor(() => expect(target.querySelector("input[type=password]")).toBeTruthy());
    expect(requests).not.toContain("POST /token/aus-sitzung/");
    expect(await remembered("me")).toBeNull();
    unmount(again);
});

// ---------------------------------------------------------------------------
// Another account signs in
// ---------------------------------------------------------------------------

it("aborts the switch while entries of the previous account wait", async () => {
    await asZoeWithPending();
    server(ZARA);

    const error = await signIn("zara", "geheim").catch((cause) => cause);

    expect(error).toBeInstanceOf(ForeignEntries);
    expect(error.count).toBe(1);
    expect(error.who).toMatchObject({ vorname: "Zoe" });
    // Everything as before, the tokens too: Zoe's numbers must not go out under Zara.
    expect(await db.queue.count()).toBe(1);
    expect(await remembered("access_token")).toBe("a-zoe");
    expect(await remembered("refresh_token")).toBe("r-zoe");
    expect(await remembered("me")).toMatchObject({ id: 1 });
});

it("aborts the switch while a form of the previous account was left unsaved", async () => {
    await asZoeWithDraft();
    server(ZARA);

    const error = await signIn("zara", "geheim").catch((cause) => cause);

    expect(error).toBeInstanceOf(ForeignEntries);
    expect([error.count, error.unsaved]).toEqual([0, 1]);
    expect(await savedDrafts()).toHaveLength(1);
    expect(await remembered("access_token")).toBe("a-zoe");
});

it("keeps the previous tokens when the connection drops halfway through signing in", async () => {
    await asZoeWithPending();
    server(ZARA);
    const answers = globalThis.fetch;
    globalThis.fetch = (url, options) =>
        url.endsWith("/ich/") ? Promise.reject(new TypeError("Funkloch")) : answers(url, options);

    const error = await signIn("zara", "geheim").catch((cause) => cause);

    expect(error.status).toBe(0);
    // Zara's tokens next to Zoe's numbers would send them out under Zara.
    expect(await remembered("access_token")).toBe("a-zoe");
    expect(await remembered("refresh_token")).toBe("r-zoe");
    expect(await db.queue.count()).toBe(1);
});

it("switches nonetheless with explicit discarding", async () => {
    await asZoeWithPending();
    server(ZARA);

    const me = await signIn("zara", "geheim", { discard: true });

    expect(me).toMatchObject({ id: 2 });
    expect(await db.queue.count()).toBe(0);
    expect(await remembered("access_token")).toBe("a-2");
});

it("keeps what waits when the same account signs in anew", async () => {
    await asZoeWithPending();
    server(ZOE);

    await signIn("zoe", "geheim");

    expect(await db.queue.count()).toBe(1);
});

it("asks in the login form before deleting foreign entries", async () => {
    await asZoeWithPending();
    // Zoe's sign-in has expired: the app shows the form.
    await db.state.delete("access_token");
    await db.state.delete("refresh_token");
    server(ZARA, { session: false });
    const app = mount(App, { target: target });
    await vi.waitFor(() => expect(target.querySelector("input[type=password]")).toBeTruthy());

    const [name, password] = target.querySelectorAll("input");
    name.value = "zara";
    name.dispatchEvent(new Event("input", { bubbles: true }));
    password.value = "geheim";
    password.dispatchEvent(new Event("input", { bubbles: true }));
    target.querySelector("button[type=submit]").click();

    await vi.waitFor(() => expect(text()).toContain("wartet noch 1 Eingabe von Zoe"));
    expect(await db.queue.count()).toBe(1);

    button("Eingaben löschen und als zara anmelden").click();

    await vi.waitFor(() => expect(target.querySelector("input[type=password]")).toBeNull());
    expect(await db.queue.count()).toBe(0);
    expect(await remembered("me")).toMatchObject({ id: 2 });
    unmount(app);
});

it("names the unsaved form of the previous account in the login form", async () => {
    await asZoeWithDraft();
    // Zoe's sign-in has expired: the app shows the form.
    await db.state.bulkDelete(["access_token", "refresh_token"]);
    server(ZARA, { session: false });
    const app = mount(App, { target: target });
    await vi.waitFor(() => expect(target.querySelector("input[type=password]")).toBeTruthy());

    const [name, password] = target.querySelectorAll("input");
    name.value = "zara";
    name.dispatchEvent(new Event("input", { bubbles: true }));
    password.value = "geheim";
    password.dispatchEvent(new Event("input", { bubbles: true }));
    target.querySelector("button[type=submit]").click();

    await vi.waitFor(() =>
        expect(text()).toContain("liegt noch 1 Eingabe von Zoe, die nicht gespeichert wurde"),
    );
    expect(text()).not.toContain("die nicht beim Server ist");
    expect(await savedDrafts()).toHaveLength(1);
    unmount(app);
});

// ---------------------------------------------------------------------------
// The website's sign-in
// ---------------------------------------------------------------------------

it("does not adopt another account of the website while something waits here", async () => {
    await asZoeWithPending();
    document.cookie = "csrftoken=abc";
    server(ZARA);

    expect(await fromSession()).toBeNull();
    expect(await db.queue.count()).toBe(1);
    expect(await remembered("access_token")).toBe("a-zoe");
});

/**
 * A form left behind unsaved is no number yet: it keeps nobody signed in, and
 * it goes with the storage it belongs to. Until 10/2026 it kept the previous
 * account signed in, and the next person worked as that account.
 */
it("adopts another account of the website although a form waits unsaved here", async () => {
    await asZoeWithDraft();
    document.cookie = "csrftoken=abc";
    server(ZARA);

    expect(await fromSession()).toMatchObject({ id: 2 });
    expect(await remembered("access_token")).toBe("a-2");
    expect(await savedDrafts()).toEqual([]);
});

it("signs the app out with the website although a form waits unsaved here", async () => {
    await asZoeWithDraft();
    await remember("from_session", true);
    document.cookie = "csrftoken=abc";
    server(ZOE, { session: false });

    expect(await fromSession()).toBeNull();

    expect(await remembered("access_token")).toBeNull();
    expect(await remembered("refresh_token")).toBeNull();
    expect(await savedDrafts()).toEqual([]);
});

/**
 * A shared tablet. Zoe opened the app from the website and typed into a
 * form, then the tab closed: the form stays behind unsaved. She signed out
 * on the website, and the next person opens the app. Until 10/2026 the form
 * kept Zoe signed in: the next person saw her inventories and counted under
 * her name.
 */
it("shows the next person the login form when only an unsaved form of the previous one waits", async () => {
    await asZoeWithDraft();
    await remember("from_session", true);
    document.cookie = "csrftoken=abc";
    const requests = server(ZOE, { session: false });

    const app = mount(App, { target: target });

    await vi.waitFor(() => expect(target.querySelector("input[type=password]")).toBeTruthy());
    expect(text()).not.toContain("Kino Ost");
    expect(text()).not.toContain("Artikel C");
    expect(requests).not.toContain("GET /inventuren/");
    expect(await remembered("me")).toBeNull();
    unmount(app);
});

/** Zoe's inventory as the storage holds it: a house, a location and a counted row. */
async function asZoeWithInventory() {
    await db.state.bulkPut([
        { key: "me", value: ZOE },
        { key: "access_token", value: "a-zoe" },
        { key: "refresh_token", value: "r-zoe" },
    ]);
    const location = { id: 7, name: "Thekenfront", bereich: "Theke" };
    const pack = { id: 2, name: "Kasten", faktor_in_basiseinheit: "20.000" };
    await db.inventories.put({ id: 1, haus: "Kino Ost", stichtag: "2026-01-31" });
    await db.masterData.put({
        inventory_id: 1,
        orte: [location],
        behaelter: [],
        artikel: [
            {
                id: 11,
                name: "Artikel C 0,33",
                einheit: "STUECK",
                wird_gewogen: false,
                gebinde: [pack],
                behaelter: [],
            },
        ],
    });
    await db.positions.put({
        id: 101,
        inventur_id: 1,
        ort: location,
        location_id: 7,
        artikel_id: 11,
        artikel: "Artikel C 0,33",
        einheit: "STUECK",
        gebinde: pack,
        wird_gewogen: false,
        wiegungen: [],
        eingabemenge: "17",
        unerwartet: false,
    });
}

/**
 * A tab restored on Zoe's entry form, and Zara signed in on the website of
 * the same browser. The start takes Zara over and clears Zoe's storage.
 * Until 10/2026 the interface kept what it had loaded from there before:
 * Zara saw Zoe's house, location and number in an open form, until the next
 * sync up to twenty seconds later.
 */
it("shows nothing of the previous account when the website's account takes over at the start", async () => {
    await asZoeWithInventory();
    document.cookie = "csrftoken=abc";
    // Zara counts nowhere: her list is empty.
    server(ZARA);
    go("inventory", 1, "area", "Theke", "location", 7, "article", 11);

    const app = mount(App, { target: target });

    // Taken over: the storage is Zara's.
    await vi.waitFor(async () => expect(await remembered("me")).toMatchObject({ id: 2 }));
    await new Promise((done) => setTimeout(done, 50));
    expect(text()).not.toContain("Kino Ost");
    expect(text()).not.toContain("Thekenfront");
    expect(text()).not.toContain("Artikel C");
    expect(target.querySelector(".count-row input")).toBeNull();
    expect(counting.inventoryId).toBeNull();
    // The address was Zoe's: the way starts at the list.
    expect(route.parts).toEqual([]);
    expect(target.querySelector("button.account").textContent).toContain("Zara");
    unmount(app);
});

it("adopts it when nothing waits", async () => {
    await db.state.put({ key: "me", value: ZOE });
    document.cookie = "csrftoken=abc";
    server(ZARA);

    expect(await fromSession()).toMatchObject({ id: 2 });
    expect(await remembered("access_token")).toBe("a-2");
});

it("does not sign the app out with the website while something waits here", async () => {
    await asZoeWithPending();
    await remember("from_session", true);
    document.cookie = "csrftoken=abc";
    server(ZOE, { session: false });

    await fromSession();

    expect(await db.queue.count()).toBe(1);
    expect(await remembered("access_token")).toBe("a-zoe");

    // Once everything is out, the app follows the website as before.
    await db.queue.clear();
    await fromSession();
    expect(await remembered("access_token")).toBeNull();
});

/**
 * The sign-in expired, and another account signs in on the form: what the
 * previous one had in memory goes at once, as on signing out. Until 10/2026
 * it stood there until the first sync came back.
 */
it("drops the previous account's state at once when another one signs in", async () => {
    await db.state.bulkPut([
        { key: "me", value: ZOE },
        { key: "access_token", value: "a-zoe" },
        { key: "refresh_token", value: "r-zoe" },
    ]);
    await db.inventories.put({ id: 1, haus: "Kino Ost", stichtag: "2026-01-31" });
    // Zoe's sign-in has expired: every answer is 401, the refresh too.
    globalThis.fetch = () => respond({ detail: "abgelaufen" }, 401);
    const app = mount(App, { target: target });
    await vi.waitFor(() => expect(target.querySelector("input[type=password]")).toBeTruthy());
    await vi.waitFor(() => expect(counting.inventories).toHaveLength(1));

    // Zara signs in. Her list never comes, so only the memory can show anything.
    server(ZARA);
    const answers = globalThis.fetch;
    globalThis.fetch = (url, options) =>
        url.endsWith("/inventuren/") ? new Promise(() => {}) : answers(url, options);
    const [name, password] = target.querySelectorAll("input");
    name.value = "zara";
    name.dispatchEvent(new Event("input", { bubbles: true }));
    password.value = "geheim";
    password.dispatchEvent(new Event("input", { bubbles: true }));
    target.querySelector("button[type=submit]").click();

    await vi.waitFor(() => expect(target.querySelector("input[type=password]")).toBeNull());
    await vi.waitFor(() => expect(target.textContent).toContain("Einen Augenblick"));
    expect(target.textContent).not.toContain("Kino Ost");
    expect(counting.inventories).toEqual([]);
    unmount(app);
});
