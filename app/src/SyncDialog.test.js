/**
 * The sync seen from outside: what waits, what was rejected, what may go.
 *
 * In storage offline is the normal case, so it has to be possible to check
 * whether the own value is already out, and what is settled must not lie
 * around forever and reappear as a choice in the next dead spot.
 */

import { mount, unmount } from "svelte";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import App from "./App.svelte";
import { cleanUp, jobKind, pendingJobs, removeInventory, fetchPositions } from "./services/sync.js";
import { db } from "./services/storage.js";
import { open, close, counting } from "./services/counting.svelte.js";
import { clearStorage, findButton } from "./test_support.js";

let target;

beforeEach(async () => {
    await clearStorage();
    // Signed in, as the sync needs it: a request without its token does not go out.
    await db.state.bulkPut([
        { key: "access_token", value: "a" },
        { key: "refresh_token", value: "r" },
    ]);
    target = document.createElement("div");
    document.body.append(target);
});

afterEach(() => {
    target.remove();
    window.location.hash = "";
});

async function storeInventory(id) {
    await db.inventories.put({ id, haus: "Kino Ost", stichtag: "2026-01-31" });
    await db.masterData.put({ inventory_id: id, orte: [], artikel: [], behaelter: [] });
    await db.state.put({ key: `synced:${id}`, value: "2026-01-31T10:00:00Z" });
    await db.positions.put({
        id: id * 100,
        inventur_id: id,
        location_id: 7,
        ort: { id: 7, name: "Thekenfront", bereich: "Theke" },
        artikel_id: 11,
        artikel: "Artikel C 0,33",
        gebinde: { id: 2, name: "Kasten" },
        wiegungen: [],
    });
}

it("clears away what the server no longer names", async () => {
    await storeInventory(1);
    await storeInventory(2);

    expect(await cleanUp([1])).toEqual([2]);
    expect(await db.inventories.toArray()).toHaveLength(1);
    expect(await db.positions.where("inventur_id").equals(2).count()).toBe(0);
    expect(await db.masterData.get(2)).toBeUndefined();
    expect(await db.state.get("synced:2")).toBeUndefined();
});

/** First what was counted has to go out, otherwise the work would be lost. */
it("leaves an inventory for which something is still pending", async () => {
    await storeInventory(2);
    await db.queue.add({ kind: "quantity", position: 200, payload: { eingabemenge: "3" } });

    expect(await cleanUp([])).toEqual([]);
    expect(await db.inventories.get(2)).toBeTruthy();
});

it("takes an inventory with everything hanging on it", async () => {
    await storeInventory(3);
    await removeInventory(3);

    expect(await db.inventories.get(3)).toBeUndefined();
    expect(await db.positions.where("inventur_id").equals(3).count()).toBe(0);
});

it("describes what waits in the queue", async () => {
    await storeInventory(1);
    await db.queue.add({ kind: "quantity", position: 100, payload: { eingabemenge: "3" } });

    const [job] = await pendingJobs();
    expect(job.kind).toBe("quantity");
    expect(jobKind(job.kind)).toBe("Menge");
    expect(job.label).toBe("Artikel C 0,33 · Kasten · Thekenfront");
});

/**
 * If the inventory is closed on the way, the server answers with 404.
 * Counting on would not work any more anyway, so away with it.
 */
/** The list names only what is running, the detail otherwise answers with 404. */
function server(list, detail = null) {
    return (url) => {
        const text = String(url).endsWith("/inventuren/") ? JSON.stringify(list) : detail;
        return Promise.resolve({
            ok: text !== null,
            status: text === null ? 404 : 200,
            text: () => Promise.resolve(text ?? "{}"),
        });
    };
}

it("clears away an inventory the server no longer names", async () => {
    await storeInventory(1);
    globalThis.fetch = server([]);

    await open(1);
    expect(counting.gone).toBe(true);
    expect(await db.inventories.get(1)).toBeUndefined();
    expect(counting.notice).toEqual({ key: "error.gone", inventoryId: 1 });

    globalThis.fetch = () => Promise.reject(new TypeError("kein Netz im Test"));
    close();
});

it("keeps an inventory the server no longer names while numbers wait for it", async () => {
    // Taken back into preparation: counted again later, and the numbers from
    // the dead spot go out then. Until 10/2026 the storage was cleared regardless.
    await storeInventory(1);
    await db.queue.add({ kind: "quantity", position: 100, payload: { eingabemenge: "3" } });
    globalThis.fetch = (url, options = {}) => {
        if (options.method === "PATCH") {
            return Promise.resolve({
                ok: false,
                status: 409,
                text: () =>
                    Promise.resolve(
                        JSON.stringify({
                            konflikt: "status",
                            vorlaeufig: true,
                            detail: "Vorbereitung",
                        }),
                    ),
            });
        }
        return server([])(url);
    };

    await open(1);
    expect(counting.gone).toBe(true);
    expect(counting.notice).toEqual({ key: "error.on_hold", inventoryId: 1 });
    // Its stored status still says "counting": the list says it waits.
    expect((await db.inventories.get(1)).held).toBe(true);
    expect(await db.positions.get(100)).toBeDefined();
    expect(await db.queue.count()).toBe(1);

    globalThis.fetch = () => Promise.reject(new TypeError("kein Netz im Test"));
    close();
});

it("clears it away too when it was closed between list and fetch", async () => {
    await storeInventory(1);
    globalThis.fetch = server([{ id: 1, haus: "Kino Ost", stichtag: "2026-01-31" }]);

    await open(1);
    expect(counting.gone).toBe(true);
    expect(await db.inventories.get(1)).toBeUndefined();

    globalThis.fetch = () => Promise.reject(new TypeError("kein Netz im Test"));
    close();
});

/**
 * Whoever has the app lying open while someone creates an inventory would
 * otherwise never see it, because the background interval had nothing to do on the list.
 */
it("fetches the list of inventories when no inventory is open", async () => {
    await db.state.bulkPut([
        { key: "access_token", value: "a" },
        { key: "refresh_token", value: "abc" },
        { key: "me", value: { vorname: "Zoe" } },
    ]);
    let created = false;
    globalThis.fetch = (url) => {
        const list = created
            ? [{ id: 5, haus: "Kino Ost", stichtag: "2026-01-31", status_anzeige: "Zählung" }]
            : [];
        if (String(url).endsWith("/inventuren/")) {
            return Promise.resolve({
                ok: true,
                status: 200,
                text: () => Promise.resolve(JSON.stringify(list)),
            });
        }
        return Promise.reject(new TypeError("kein Netz im Test"));
    };

    const app = mount(App, { target: target });
    await vi.waitFor(() => expect(target.textContent).toContain("keine Inventur an"));

    // Now someone creates one in the web interface. The button at the very
    // top right fetches it without waiting for the next interval.
    created = true;
    target.querySelector('button[aria-label="Auffrischen"]').click();
    await vi.waitFor(() => expect(target.textContent).toContain("Kino Ost"));

    globalThis.fetch = () => Promise.reject(new TypeError("kein Netz im Test"));
    unmount(app);
});

/**
 * Deleted means "marked as deleted" on the server: the row comes along on
 * sync with 'aktiv: false'. Without that the app would never learn it is gone.
 */
it("clears positions marked as deleted from the storage", async () => {
    await storeInventory(1);
    await db.state.put({ key: "synced:1", value: "2026-01-31T10:00:00Z" });
    const deleted = {
        id: 100,
        inventur_id: 1,
        ort: { id: 7, name: "Thekenfront", bereich: "Theke" },
        artikel_id: 11,
        artikel: "Artikel C 0,33",
        gebinde: { id: 2, name: "Kasten" },
        wiegungen: [],
        aktiv: false,
        zuletzt_geaendert: "2026-01-31T12:00:00Z",
    };
    globalThis.fetch = (url) =>
        Promise.resolve({
            ok: true,
            status: 200,
            text: () =>
                Promise.resolve(
                    JSON.stringify(
                        String(url).includes("/inventuren/1/")
                            ? { id: 1, haus: "Kino Ost", zaehlpositionen: [deleted] }
                            : [],
                    ),
                ),
        });

    expect(await db.positions.get(100)).toBeTruthy();
    await fetchPositions(1);
    expect(await db.positions.get(100)).toBeUndefined();

    globalThis.fetch = () => Promise.reject(new TypeError("kein Netz im Test"));
});

/** The button top right: a look at what still waits outside. */
it("shows pending and rejected things in the sync window", async () => {
    await storeInventory(1);
    await db.state.bulkPut([
        { key: "access_token", value: "a" },
        { key: "refresh_token", value: "abc" },
        { key: "me", value: { vorname: "Zoe" } },
        {
            key: "rejected",
            value: [
                {
                    kind: "quantity",
                    was: "Menge",
                    label: "Artikel C 0,33 · Thekenfront",
                    reason: "Diese Inventur steht auf Abgeschlossen.",
                    at: "2026-01-31T12:00:00Z",
                },
            ],
        },
    ]);
    await db.queue.add({ kind: "quantity", position: 100, payload: { eingabemenge: "3" } });

    const app = mount(App, { target: target });
    window.location.hash = "#/inventory/1";
    await vi.waitFor(() => expect(target.querySelector(".sync-state")).toBeTruthy());
    // The exclamation mark on the icon says: something lies there that nobody accepted.
    await vi.waitFor(() => expect(target.querySelector(".sync-state .warning")).toBeTruthy());

    target.querySelector(".sync-state").click();
    await vi.waitFor(() => expect(target.textContent).toContain("Wartet auf den Server (1)"));
    expect(target.textContent).toContain("Artikel C 0,33 · Kasten · Thekenfront");
    // The window loads the rejected ones after the waiting ones.
    await vi.waitFor(() => expect(target.textContent).toContain("Nicht angenommen (1)"));
    expect(target.textContent).toContain("Diese Inventur steht auf Abgeschlossen.");

    [...target.querySelectorAll("button")]
        .find((button) => button.textContent.includes("Hinweise verwerfen"))
        .click();
    await vi.waitFor(() => expect(target.textContent).not.toContain("Nicht angenommen"));
    unmount(app);
});

async function withConflict(theirs, mine) {
    await storeInventory(1);
    await db.state.bulkPut([
        { key: "access_token", value: "a" },
        { key: "refresh_token", value: "abc" },
        { key: "me", value: { vorname: "Zoe" } },
        {
            key: "quantity_conflicts",
            value: [
                {
                    id: "k1",
                    position: 100,
                    inventoryId: 1,
                    label: "Artikel C 0,33 · Kasten · Thekenfront",
                    mine,
                    theirs,
                    at: "2026-01-31T12:00:00Z",
                },
            ],
        },
    ]);
    const app = mount(App, { target: target });
    window.location.hash = "#/inventory/1";
    return app;
}

const button = (text) => findButton(target, text);

/** Someone else had already counted: here the second person decides. */
it("leads via the bar at the top to the question and accepts the decision", async () => {
    const app = await withConflict("5.000", "3");

    // Not only the exclamation mark: a bar one does not overlook.
    await vi.waitFor(() => expect(button("Hier hat schon jemand anderes gezählt")).toBeTruthy());
    expect(target.querySelector(".sync-state .warning")).toBeTruthy();
    button("Hier hat schon jemand anderes gezählt").click();

    await vi.waitFor(() => expect(target.textContent).toContain("Schon gezählt (1)"));
    expect(target.textContent).toContain("Artikel C 0,33 · Kasten · Thekenfront");
    expect(target.textContent).toContain("Dort steht inzwischen: 5.");
    expect(target.textContent).toContain("Deine Zahl: 3.");
    expect(button("Meine verwerfen: 5 bleibt")).toBeTruthy();
    expect(button("Überschreiben: 3")).toBeTruthy();

    button("Addieren: 8").click();

    await vi.waitFor(() => expect(target.textContent).not.toContain("Schon gezählt"));
    // Without network the decision waits in the queue, with the 5 as seen.
    const [job] = await db.queue.toArray();
    expect(job).toMatchObject({
        kind: "quantity",
        position: 100,
        payload: { eingabemenge: "8", eingabemenge_vorher: "5.000" },
    });
    await vi.waitFor(() => expect(button("Hier hat schon jemand anderes gezählt")).toBeUndefined());
    unmount(app);
});

it("offers no adding when nothing stands there any more", async () => {
    const app = await withConflict(null, "3");
    await vi.waitFor(() => expect(button("Hier hat schon jemand anderes gezählt")).toBeTruthy());
    button("Hier hat schon jemand anderes gezählt").click();

    await vi.waitFor(() => expect(target.textContent).toContain("Schon gezählt (1)"));
    expect(target.textContent).toContain("Dort steht inzwischen nichts mehr.");
    expect(button("Addieren")).toBeUndefined();
    expect(button("Meine verwerfen: bleibt leer")).toBeTruthy();

    button("Meine verwerfen").click();

    await vi.waitFor(() => expect(target.textContent).not.toContain("Schon gezählt"));
    expect(await db.queue.count()).toBe(0);
    unmount(app);
});
