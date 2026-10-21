/**
 * All running inventories lie on the device, not only the opened one.
 *
 * Whoever did not tap a house before the walk into storage should still be
 * able to count there without network. And the start page shows per house
 * how far it is, including what was counted here and not sent yet.
 */

import { mount, unmount } from "svelte";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import App from "../App.svelte";
import { refresh, loadFromStorage, open, close, counting } from "./counting.svelte.js";
import { db, remember } from "./storage.js";
import { forgetDraft, saveDraft } from "./draft_backup.js";
import { enterQuantity, rejectedJobs } from "./sync.js";
import { clearStorage, respond } from "../test_support.js";

const header = (id, haus) => ({
    id,
    haus,
    haus_id: id,
    stichtag: "2026-01-31",
    inventurarten: [{ id: 1, name: "Monatsinventur" }],
    arten_anzeige: "Monatsinventur",
    status: "ZAEHLUNG",
    status_anzeige: "Zählung",
    zaehlbar: true,
    anzahl_positionen: 2,
    anzahl_gezaehlt: 1,
});

const row = (id, inventoryId, eingabemenge = null) => ({
    id,
    inventur_id: inventoryId,
    ort: { id: 7, name: "Thekenfront", bereich: "Theke" },
    artikel_id: 11,
    artikel: "Artikel C",
    gebinde: { id: id, name: `Gebinde ${id}` },
    wird_gewogen: false,
    wiegungen: [],
    eingabemenge,
    aktiv: true,
    zuletzt_geaendert: "2026-01-31T10:00:00Z",
});

/**
 * Two running inventories, two rows each. 'gone' answers with 404 in the detail.
 * 'listed' are those in the count: a test takes one out as the website does.
 * The others answer a quantity like an inventory back in preparation.
 */
function mockServer({ gone = [] } = {}) {
    const inventories = [header(1, "Kino Ost"), header(2, "Kino West")];
    const rows = {
        1: [row(101, 1, "3.000"), row(102, 1)],
        2: [row(201, 2), row(202, 2)],
    };
    //: What the house carries now. A test changes it like maintenance does.
    const server = { network: true, calls: [], places: [], listed: [1, 2] };
    const respond = (data, status = 200) =>
        Promise.resolve({
            ok: status < 400,
            status,
            text: () => Promise.resolve(JSON.stringify(data)),
        });
    const counted = (number) => server.listed.includes(Number(number));
    globalThis.fetch = (url, options = {}) => {
        if (!server.network) return Promise.reject(new TypeError("kein Netz"));
        const path = url.replace("/api/v1", "").split("?")[0];
        server.calls.push(path);
        if (path === "/inventuren/") return respond(inventories.filter(({ id }) => counted(id)));
        if (options.method === "PATCH") {
            // Row 101 belongs to inventory 1: the hundreds carry it.
            const number = Math.floor(Number(path.match(/(\d+)\/$/)?.[1]) / 100);
            if (!counted(number)) {
                return respond(
                    { konflikt: "status", vorlaeufig: true, detail: "Vorbereitung" },
                    409,
                );
            }
        }
        const [, number, rest] = path.match(/^\/inventuren\/(\d+)\/(stammdaten\/)?$/) ?? [];
        if (!number || gone.includes(Number(number)) || !counted(number)) {
            return respond({ detail: "?" }, 404);
        }
        if (rest)
            return respond({
                orte: server.places,
                artikel: [{ id: 11, name: "Artikel C", inventurart: 1 }],
                inventurarten: [{ id: 1, name: "Monatsinventur" }],
                behaelter: [],
            });
        return respond({ ...header(Number(number)), zaehlpositionen: rows[number] });
    };
    return server;
}

let target;

beforeEach(async () => {
    await clearStorage();
    await remember("access_token", "a");
    await remember("refresh_token", "r");
    target = document.createElement("div");
    document.body.append(target);
});

afterEach(() => {
    target.remove();
    window.location.hash = "";
    globalThis.fetch = () => Promise.reject(new TypeError("kein Netz im Test"));
    close();
});

it("fetches rows and master data of every running inventory, the never opened ones too", async () => {
    mockServer();

    await refresh();

    for (const number of [1, 2]) {
        expect(await db.positions.where("inventur_id").equals(number).count()).toBe(2);
        expect(await db.masterData.get(number)).toBeTruthy();
    }
});

it("fetches the opened one first", async () => {
    const server = mockServer();

    await open(2);

    expect(server.calls).toEqual([
        "/inventuren/",
        "/inventuren/2/stammdaten/",
        "/inventuren/2/",
        "/inventuren/1/stammdaten/",
        "/inventuren/1/",
    ]);
});

it("fetches master data only once per inventory", async () => {
    const server = mockServer();
    await refresh();
    server.calls = [];

    await refresh();

    expect(server.calls).toEqual(["/inventuren/", "/inventuren/1/", "/inventuren/2/"]);
});

it("fetches the master data anew when asked", async () => {
    const server = mockServer();
    await refresh();
    server.calls = [];

    await refresh({ masterData: true });

    expect(server.calls).toEqual([
        "/inventuren/",
        "/inventuren/1/stammdaten/",
        "/inventuren/1/",
        "/inventuren/2/stammdaten/",
        "/inventuren/2/",
    ]);
});

it("fetches master data from before the inventory types once more", async () => {
    // Stored by an app from before 10/2026: it does not name the types yet.
    const server = mockServer();
    await refresh();
    await db.masterData.update(1, { inventurarten: undefined });
    server.calls = [];

    await refresh();

    expect(server.calls).toEqual([
        "/inventuren/",
        "/inventuren/1/stammdaten/",
        "/inventuren/1/",
        "/inventuren/2/",
    ]);
});

it("fetches the master data anew, once, after the server rejected a new row", async () => {
    const server = mockServer();
    await refresh();
    const serve = globalThis.fetch;
    globalThis.fetch = (url, options = {}) =>
        url.endsWith("/zaehlpositionen/") && options.method === "POST"
            ? respond({ gebinde: ["Dieses Gebinde gibt es nicht mehr."] }, 400)
            : serve(url, options);
    await db.queue.add({
        kind: "position",
        position: -1,
        payload: { inventur: 1, ort: 7, artikel: 11, gebinde: 101 },
    });
    server.calls = [];

    await refresh();

    expect(server.calls).toEqual([
        "/inventuren/",
        "/inventuren/1/stammdaten/",
        "/inventuren/1/",
        "/inventuren/2/",
    ]);
    expect((await rejectedJobs()).map((job) => job.kind)).toEqual(["position"]);
    server.calls = [];

    await refresh();

    expect(server.calls).toEqual(["/inventuren/", "/inventuren/1/", "/inventuren/2/"]);
});

it("brings a location created since the first fetch", async () => {
    const server = mockServer();
    await open(1);
    expect(counting.masterData.orte).toEqual([]);

    server.places = [{ id: 9, name: "Vitrine", bereich: "Theke" }];
    // The background sync leaves the master data alone.
    await refresh();
    expect(counting.masterData.orte).toEqual([]);

    await refresh({ masterData: true });
    expect(counting.masterData.orte).toEqual(server.places);
});

it("keeps the master data when fetching it anew fails", async () => {
    const server = mockServer();
    await open(1);
    server.network = false;

    await refresh({ masterData: true });

    expect(counting.online).toBe(false);
    expect(counting.masterData.artikel).toEqual([{ id: 11, name: "Artikel C", inventurart: 1 }]);
    expect(await db.masterData.get(2)).toBeTruthy();
});

it("asks for the master data through the button in the header, and only there", async () => {
    await remember("me", { vorname: "Zoe" });
    const server = mockServer();

    const app = mount(App, { target: target });
    await vi.waitFor(() => expect(target.querySelectorAll("button.row")).toHaveLength(2));
    await vi.waitFor(() => expect(counting.loading).toBe(false));
    server.calls = [];
    server.places = [{ id: 9, name: "Vitrine", bereich: "Theke" }];

    target.querySelector('button[aria-label="Auffrischen"]').click();

    await vi.waitFor(() => expect(server.calls).toContain("/inventuren/2/stammdaten/"));
    await vi.waitFor(async () => expect((await db.masterData.get(1)).orte).toEqual(server.places));
    unmount(app);
});

it("lets one count without network in a house nobody opened before", async () => {
    const server = mockServer();
    await refresh();
    server.network = false;

    await open(2);

    expect(counting.loaded).toBe(true);
    expect(counting.masterData).toBeTruthy();
    expect(counting.positions.map((position) => position.id)).toEqual([201, 202]);
    expect(counting.gone).toBe(false);
});

it("reports a server error without discarding anything", async () => {
    // Maintenance or an error page: the numbers wait, the storage stays, and
    // the bar says that the sync failed, not that the device is offline.
    mockServer();
    await refresh();
    await db.queue.add({ kind: "quantity", position: 101, payload: { eingabemenge: "7" } });
    globalThis.fetch = () =>
        Promise.resolve({
            ok: false,
            status: 500,
            text: () => Promise.resolve("<html><body>Server Error</body></html>"),
        });

    await refresh();

    expect(counting.online).toBe(false);
    expect(counting.message).toBe("Der Abgleich ist gescheitert.");
    expect(await db.queue.count()).toBe(1);
    expect(await db.positions.count()).toBe(4);
    expect(await db.inventories.count()).toBe(2);
});

it("quietly clears away what was closed between list and fetch, and leaves the open one", async () => {
    mockServer({ gone: [2] });

    await open(1);

    expect(counting.gone).toBe(false);
    expect(counting.message).toBe("");
    expect(await db.inventories.get(2)).toBeUndefined();
    expect(counting.positions).toHaveLength(2);
});

it("counts on the start page what was counted here and not sent yet", async () => {
    const server = mockServer();
    await refresh();
    expect(counting.progress[2]).toMatchObject({ total: 2, counted: 0, state: "empty" });

    server.network = false;
    await enterQuantity(await db.positions.get(201), "4", false);
    await loadFromStorage();

    expect(counting.progress[2]).toMatchObject({ total: 2, counted: 1, state: "partial" });
    expect(counting.progress[1]).toMatchObject({ total: 2, counted: 1 });
});

it("takes the numbers of the list before the first fetch of the rows", async () => {
    await db.inventories.put({
        ...header(1, "Kino Ost"),
        anzahl_positionen: 90,
        anzahl_gezaehlt: 30,
    });

    await loadFromStorage();

    expect(counting.progress[1]).toMatchObject({ total: 90, counted: 30, state: "partial" });
});

it("shows the progress of every house on the start page", async () => {
    await remember("me", { vorname: "Zoe" });
    mockServer();

    const app = mount(App, { target: target });
    await vi.waitFor(() => expect(target.querySelectorAll("button.row")).toHaveLength(2));
    await vi.waitFor(() =>
        expect([...target.querySelectorAll("button.row .count")].map((e) => e.textContent)).toEqual(
            ["1/2", "0/2"],
        ),
    );
    unmount(app);
});

it("fetches a review list asked for before its inventory was open", async () => {
    // A locked inventory opens on its review list at once, and the list asks
    // before the inventory is open. Until 10/2026 that wish was lost.
    const server = mockServer();
    const others = globalThis.fetch;
    globalThis.fetch = (url, options) => {
        if (!url.includes("/pruefliste/")) return others(url, options);
        server.calls.push(url.replace("/api/v1", ""));
        return respond({ inventur: 1, zeilen: [] });
    };

    const asked = refresh({ review: true });
    await open(1);
    await asked;

    await vi.waitFor(async () => expect(await db.reviews.get(1)).toBeTruthy());
    expect(server.calls).toContain("/inventuren/1/pruefliste/");
});

/**
 * The open inventory leaves the count: the list comes up and says why, also
 * after its own sync. Until 10/2026 that sync took the sentence away at once.
 */
it("keeps saying why an inventory left the count, until another one opens", async () => {
    const server = mockServer();
    await remember("me", { vorname: "Zoe" });
    window.location.hash = "#/inventory/1";
    const app = mount(App, { target });
    await vi.waitFor(() => expect(counting.inventoryId).toBe(1));
    await vi.waitFor(() => expect(counting.loading).toBe(false));
    const gone = "Diese Inventur ist gerade nicht in der Zählung: abgeschlossen";

    server.listed = [2];
    await refresh();
    await vi.waitFor(() => expect(target.textContent).toContain(gone));
    await vi.waitFor(() => expect(counting.loading).toBe(false));
    // The clock: one more round on the list.
    await refresh();
    expect(target.textContent).toContain(gone);

    // Another one opens: the sentence has said what it had to.
    await open(2);
    await vi.waitFor(() => expect(target.textContent).not.toContain(gone));
    unmount(app);
});

/** Back in preparation with a number waiting: the list says it waits, not "Zählung". */
it("marks an inventory that waits for the count, until it is counted again", async () => {
    const server = mockServer();
    await refresh();
    await db.queue.add({ kind: "quantity", position: 101, payload: { eingabemenge: "7" } });
    server.listed = [2];

    await refresh();

    expect(await db.queue.count()).toBe(1);
    expect((await db.inventories.get(1)).held).toBe(true);
    expect((await db.inventories.get(2)).held).toBeUndefined();
    const app = mount(App, { target });
    await vi.waitFor(() => expect(target.textContent).toContain("wartet auf die Zählung"));
    expect(target.textContent).not.toContain("Monatsinventur · Zählung · ");
    unmount(app);

    server.listed = [1, 2];
    await refresh();
    expect(await db.queue.count()).toBe(0);
    expect((await db.inventories.get(1)).held).toBeUndefined();
});

/** Another inventory opens: the form of the previous one is no longer out of the count. */
it("ends the out-of-count state of the previous inventory when another opens", async () => {
    mockServer();
    await refresh();
    await open(1);
    counting.outOfCount = true;

    await open(2);

    expect(counting.outOfCount).toBe(false);
    expect(counting.inventoryId).toBe(2);
});

/** "Nothing pending" only once the list from the storage stands, not a moment before it. */
it("says that the sync ran only once the list from the storage stands", async () => {
    mockServer();
    const seen = [];
    const read = db.inventories.toArray.bind(db.inventories);
    vi.spyOn(db.inventories, "toArray").mockImplementation(() => {
        seen.push({ synced: counting.synced, listed: counting.inventories.length });
        return read();
    });

    await refresh();
    vi.restoreAllMocks();

    // Until 10/2026 the list read "synced" while it was still empty.
    expect(seen.filter((entry) => entry.synced && entry.listed === 0)).toEqual([]);
    expect([counting.synced, counting.inventories.length]).toEqual([true, 2]);
});

/**
 * The clock and the browser ask in the background: during a run that adds
 * no second one right after it. Until 10/2026 every tick during a slow run
 * asked for one more, and syncs followed each other without a pause. A
 * request someone made still comes right after, a language switch say.
 */
it("drops what the clock and the browser ask for during a run", async () => {
    const server = mockServer();
    await remember("me", { vorname: "Zoe" });
    const answers = globalThis.fetch;
    let release = null;
    globalThis.fetch = async (url, options) => {
        if (url.endsWith("/inventuren/") && release === null) {
            await new Promise((resolve) => (release = resolve));
        }
        return await answers(url, options);
    };
    const app = mount(App, { target });
    await vi.waitFor(() => expect(release).not.toBeNull());
    const lists = () => server.calls.filter((path) => path === "/inventuren/").length;

    // The browser is back online, the app comes back into view.
    window.dispatchEvent(new Event("online"));
    document.dispatchEvent(new Event("visibilitychange"));
    await refresh({ background: true });
    release();
    await vi.waitFor(() => expect(counting.loading).toBe(false));
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(lists()).toBe(1);

    // Asked for by someone: once more right after the run.
    release = null;
    const first = refresh();
    await vi.waitFor(() => expect(release).not.toBeNull());
    await refresh();
    release();
    await first;
    await vi.waitFor(() => expect(lists()).toBe(3));
    unmount(app);
});

/**
 * Out of the list, with only a draft left: the inventory stays for its form,
 * but the list does not offer it as waiting for the count. It may be closed
 * for good. Until 10/2026 it stood there as "waits for the count".
 */
it("keeps an inventory only a draft holds for its form, without offering it", async () => {
    const server = mockServer();
    await remember("me", { vorname: "Zoe" });
    await refresh();
    await saveDraft(
        { inventoryId: 1, area: "Theke", locationId: 7, articleId: 11, label: "Artikel C" },
        { packs: [] },
    );
    await db.reviews.put({ inventory_id: 1, inventur: 1, zeilen: [] });
    server.listed = [2];

    await refresh();

    const kept = await db.inventories.get(1);
    expect([kept.held, kept.draftOnly]).toEqual([false, true]);
    expect(await db.positions.where("inventur_id").equals(1).count()).toBe(2);
    expect(await db.reviews.get(1)).toBeUndefined();
    const app = mount(App, { target });
    await vi.waitFor(() => expect(target.textContent).toContain("Nicht gespeichert: Artikel C"));
    await vi.waitFor(() => expect(counting.loading).toBe(false));
    // Only the inventory still in the count, the other one's notice above.
    expect(target.querySelectorAll("button.row")).toHaveLength(1);
    expect(target.textContent).not.toContain("wartet auf die Zählung");
    unmount(app);

    // The draft goes: so does the inventory, with the next sync.
    await forgetDraft({ inventoryId: 1, locationId: 7, articleId: 11 });
    await refresh();
    expect(await db.inventories.get(1)).toBeUndefined();
});
