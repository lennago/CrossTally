/**
 * The way through the house, against a filled offline storage.
 *
 * Without network and without server: exactly the state the app runs in in
 * storage. Checked is that every level builds from the local state: areas,
 * locations, articles and the entry form including weighing block.
 */

import { mount, unmount } from "svelte";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import App from "./App.svelte";
import { db } from "./services/storage.js";
import { close, refresh } from "./services/counting.svelte.js";
import { go, route } from "./services/route.svelte.js";
import { findButton } from "./test_support.js";

const INVENTORY = 1;
const LOCATION = { id: 7, name: "Thekenfront", bereich: "Theke" };
const MONTH = { id: 1, name: "Monatsinventur" };
const YEAR = { id: 2, name: "Jahresinventur" };
const CUPS = { id: 3, name: "Becherinventur" };

let target;

beforeEach(async () => {
    // The counting store lives in the module: without this the next case sees
    // the inventory of the last one, and a lock set here would not arrive.
    close();
    await Promise.all([
        db.state.clear(),
        db.inventories.clear(),
        db.positions.clear(),
        db.masterData.clear(),
    ]);
    await db.state.bulkPut([
        { key: "access_token", value: "a" },
        { key: "refresh_token", value: "abc" },
        { key: "me", value: { vorname: "Zoe" } },
    ]);
    await db.inventories.put({
        id: INVENTORY,
        haus: "Kino Ost",
        stichtag: "2026-01-31",
    });
    await db.masterData.put({
        inventory_id: INVENTORY,
        orte: [LOCATION],
        behaelter: [{ id: 3, name: "Schale", tara: "250.000" }],
        artikel: [
            {
                id: 11,
                name: "Artikel C 0,33",
                artikelnummer: "101001",
                einheit: "STUECK",
                wird_gewogen: false,
                gebinde: [{ id: 2, name: "Kasten", faktor_in_basiseinheit: "20.000" }],
                behaelter: [],
            },
            {
                id: 12,
                name: "Artikel N lose",
                artikelnummer: "101002",
                einheit: "GRAMM",
                wird_gewogen: true,
                gebinde: [],
                behaelter: [3],
            },
        ],
    });
    await db.positions.bulkPut([
        {
            id: 101,
            inventur_id: INVENTORY,
            ort: LOCATION,
            location_id: LOCATION.id,
            artikel_id: 11,
            artikel: "Artikel C 0,33",
            einheit: "STUECK",
            gebinde: { id: 2, name: "Kasten", faktor_in_basiseinheit: "20.000" },
            wird_gewogen: false,
            wiegungen: [],
            eingabemenge: null,
            menge_basiseinheit: null,
            unerwartet: false,
            zuletzt_geaendert: "2026-01-31T10:00:00Z",
        },
        {
            id: 102,
            inventur_id: INVENTORY,
            ort: LOCATION,
            location_id: LOCATION.id,
            artikel_id: 12,
            artikel: "Artikel N lose",
            einheit: "GRAMM",
            gebinde: null,
            wird_gewogen: true,
            wiegungen: [{ id: 5, brutto_gramm: "1250.000", behaelter: 3, netto: "1000" }],
            eingabemenge: null,
            menge_basiseinheit: "1000.000",
            unerwartet: false,
            zuletzt_geaendert: "2026-01-31T10:00:00Z",
        },
    ]);

    target = document.createElement("div");
    document.body.append(target);
});

afterEach(() => {
    target.remove();
    window.location.hash = "";
});

async function shows(text) {
    await vi.waitFor(() => expect(target.textContent).toContain(text), {
        timeout: 3000,
    });
}

it("leads to the list on an address that names no number", async () => {
    // Typed or mangled: '#/inventory/abc'. Until 10/2026 'Number("abc")' ran
    // into Dexie as NaN, and the device looked offline afterwards.
    const errors = [];
    const onError = (event) => errors.push(event.message ?? String(event.reason));
    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onError);
    const app = mount(App, { target: target });
    go("inventory", "abc", "area", "Theke", "location", "x", "article", "7");

    await shows("Kino Ost");
    await new Promise((done) => setTimeout(done, 50));
    expect(errors).toEqual([]);
    expect(target.querySelector(".sync-state svg.offline")).toBeNull();

    window.removeEventListener("error", onError);
    window.removeEventListener("unhandledrejection", onError);
    unmount(app);
});

it("leads from the areas into the entry form", async () => {
    const app = mount(App, { target: target });
    await shows("Kino Ost");

    go("inventory", INVENTORY);
    await shows("Theke");
    // Two rows at the location, one of them already weighed.
    expect(target.textContent).toContain("1/2");

    go("inventory", INVENTORY, "area", "Theke");
    await shows("Thekenfront");

    go("inventory", INVENTORY, "area", "Theke", "location", LOCATION.id);
    await shows("Artikel C 0,33");
    expect(target.querySelector("input[type=search]")).toBeTruthy();

    go("inventory", INVENTORY, "area", "Theke", "location", LOCATION.id, "article", 11);
    await shows("Kasten");
    expect(target.querySelector(".count-row input")).toBeTruthy();

    unmount(app);
});

it("leads to a location nothing is expected at", async () => {
    // Such a location has no rows. It comes from the master data alone.
    const stored = await db.masterData.get(INVENTORY);
    await db.masterData.put({
        ...stored,
        orte: [
            LOCATION,
            { id: 8, name: "Vitrine", bereich: "Theke" },
            { id: 9, name: "Büro", bereich: "Verwaltung" },
        ],
    });

    const app = mount(App, { target: target });
    await shows("Kino Ost");

    go("inventory", INVENTORY);
    await shows("Verwaltung");
    expect(target.querySelector("main").textContent).toContain("nichts erwartet");

    go("inventory", INVENTORY, "area", "Theke");
    await shows("Vitrine");
    const rows = [...target.querySelectorAll("main .row")];
    const empty = rows.find((row) => row.textContent.includes("Vitrine"));
    expect(empty.textContent).toContain("nichts erwartet");
    // No bar where nothing is open. The neighbour keeps its own.
    expect(empty.querySelector(".progress")).toBeNull();
    expect(rows.find((row) => row.textContent.includes("Thekenfront")).textContent).toContain(
        "1/2",
    );

    empty.click();
    await shows("Hier wird nichts erwartet.");
    // From here the search adds what stands there after all.
    expect(target.querySelector("button.add")).toBeTruthy();

    unmount(app);
});

it("does not call a house empty whose locations are not loaded yet", async () => {
    await db.masterData.clear();
    await db.positions.clear();

    const app = mount(App, { target: target });
    await shows("Kino Ost");
    go("inventory", INVENTORY);

    await shows("noch nicht geladen");
    expect(target.textContent).not.toContain("hat noch keine Orte");

    unmount(app);
});

it("says so when the house has no locations", async () => {
    const stored = await db.masterData.get(INVENTORY);
    await db.masterData.put({ ...stored, orte: [] });
    await db.positions.clear();

    const app = mount(App, { target: target });
    await shows("Kino Ost");
    go("inventory", INVENTORY);

    await shows("hat noch keine Orte");

    unmount(app);
});

it("carries a banner as long as the API reports a demo stock", async () => {
    // The information comes from the server ('GET ich/'), not from the app:
    // whoever signs in to a real stock should see no demo banner.
    await db.state.put({ key: "me", value: { vorname: "Zoe", demo: true } });

    const app = mount(App, { target: target });
    await shows("Kino Ost");
    expect(target.textContent).toContain("alle Daten sind erfunden");
    // It sticks below the header. Account menu, search line and article
    // header attach themselves below and reckon with its height for that.
    expect(document.documentElement.style.getPropertyValue("--banner-height")).toBe("0px");

    unmount(app);
});

it("shows no banner without that report", async () => {
    const app = mount(App, { target: target });
    await shows("Kino Ost");
    expect(target.textContent).not.toContain("alle Daten sind erfunden");
    // Without banner no height: then the zero from 'style.css' applies.
    expect(document.documentElement.style.getPropertyValue("--banner-height")).toBe("");

    unmount(app);
});

it("shows an article of a type not counted here in the same list, with its type", async () => {
    // It gets no row by itself in this inventory. Whoever adds it through
    // the search should find it again in the list, and see that it is extra.
    await db.inventories.update(INVENTORY, { inventurarten: [MONTH] });
    await db.masterData.update(INVENTORY, { inventurarten: [MONTH, YEAR] });
    await db.positions.update(101, { inventurart: MONTH.id });
    await db.positions.update(102, { inventurart: YEAR.id, unerwartet: 1 });

    const app = mount(App, { target: target });
    await shows("Kino Ost");
    go("inventory", INVENTORY, "area", "Theke", "location", LOCATION.id);
    await shows("Artikel C 0,33");

    expect(target.querySelectorAll("[role=tab]").length).toBe(0);
    expect(target.querySelector("main").textContent).toContain("Artikel N lose");
    expect(target.querySelector("main").textContent).toContain("· Jahresinventur");

    unmount(app);
});

it("shows a tab per type of the inventory that has rows here", async () => {
    await db.inventories.update(INVENTORY, { inventurarten: [MONTH, YEAR, CUPS] });
    await db.positions.update(101, { inventurart: MONTH.id });
    await db.positions.update(102, { inventurart: YEAR.id });

    const app = mount(App, { target: target });
    await shows("Kino Ost");
    go("inventory", INVENTORY, "area", "Theke", "location", LOCATION.id);
    await shows("Artikel C 0,33");

    const tabs = () => [...target.querySelectorAll("[role=tab]")];
    // Nothing of the cups stands here, so no tab for them.
    expect(tabs().map((button) => button.textContent.replace(/\s+/g, " ").trim())).toEqual([
        "Monatsinventur 0/1",
        "Jahresinventur 1/1",
    ]);
    // In front stands the monthly round, the annual goods only in the second tab.
    expect(target.textContent).not.toContain("Artikel N lose");

    tabs()[1].click();
    await shows("Artikel N lose");
    expect(target.querySelector("main").textContent).not.toContain("Artikel C 0,33");

    // Back from the entry form the tab stays.
    go("inventory", INVENTORY, "area", "Theke", "location", LOCATION.id, "article", 12);
    await shows("Schale");
    go("inventory", INVENTORY, "area", "Theke", "location", LOCATION.id);
    // "Artikel N lose" stands in the entry form too. Waited for is the tab.
    await vi.waitFor(() => expect(tabs()[1]?.getAttribute("aria-selected")).toBe("true"));
    expect(target.querySelector("main").textContent).toContain("Artikel N lose");

    unmount(app);
});

it("shows no tabs where the inventory counts one type", async () => {
    await db.inventories.update(INVENTORY, { inventurarten: [MONTH] });
    await db.positions.update(101, { inventurart: MONTH.id });
    await db.positions.update(102, { inventurart: MONTH.id });

    const app = mount(App, { target: target });
    await shows("Kino Ost");
    go("inventory", INVENTORY, "area", "Theke", "location", LOCATION.id);
    await shows("Artikel C 0,33");
    expect(target.querySelector("[role=tab]")).toBeNull();
    unmount(app);
});

it("shows the weighings with their vessel for a weighed article", async () => {
    const app = mount(App, { target: target });
    go("inventory", INVENTORY, "area", "Theke", "location", LOCATION.id, "article", 12);

    await shows("Wiegen");
    // Existing weighings stand as fields. They can be changed.
    await vi.waitFor(() => expect(target.querySelector(".weighing input")?.value).toBe("1250"));
    // The tare belongs to the individual weighing, not to the row.
    expect(target.textContent).toContain("Schale");
    expect(target.textContent).toContain("Jedes Gefäß einzeln");
    unmount(app);
});

it("enters a count and puts it into the queue", async () => {
    const app = mount(App, { target: target });
    go("inventory", INVENTORY, "area", "Theke", "location", LOCATION.id, "article", 11);
    await shows("Kasten");

    const field = target.querySelector(".count-row input");
    field.value = "3";
    field.dispatchEvent(new Event("input", { bubbles: true }));
    await new Promise((done) => setTimeout(done, 0));
    target.querySelector("button.next").click();

    // Without network the job stays. The input is in the storage at once
    // nonetheless.
    await vi.waitFor(async () => {
        expect((await db.positions.get(101)).eingabemenge).toBe("3");
        expect(await db.queue.count()).toBe(1);
    });
    unmount(app);
});

// -- Locked: counting stopped, only the review list is open --------------

it("opens a locked inventory on its review list, and back leads to the list", async () => {
    await db.inventories.update(INVENTORY, { gesperrt: true });
    const app = mount(App, { target: target });

    go("inventory", INVENTORY, "area", "Theke", "location", LOCATION.id);

    await vi.waitFor(() => expect(route.parts).toEqual(["inventory", String(INVENTORY), "review"]));
    await shows("Gesperrt: Gezählt wird gerade nicht.");
    // Nothing to count from here: no search for the unexpected, no locations.
    expect(target.querySelector("button.add")).toBeNull();
    expect(target.textContent).not.toContain("Thekenfront");

    findButton(target, "Zurück").click();
    await vi.waitFor(() => expect(route.parts).toEqual([]));
    // In the list it says so before anyone opens it.
    await shows("31.01.2026 · Gesperrt");
    unmount(app);
});

it("keeps an open form when the lock comes, and says what happens to it", async () => {
    // Leaving the form would discard what was typed in it.
    await db.inventories.update(INVENTORY, { gesperrt: true });
    const app = mount(App, { target: target });

    go("inventory", INVENTORY, "area", "Theke", "location", LOCATION.id, "article", 11);

    await shows("Kasten");
    await shows("Die Inventur ist gesperrt.");
    expect(route.parts.at(-2)).toBe("article");
    unmount(app);
});

it("offers the draft of a locked inventory only once it is open again", async () => {
    const draft = {
        inventoryId: INVENTORY,
        area: "Theke",
        locationId: LOCATION.id,
        articleId: 11,
        label: "Artikel C 0,33 · Thekenfront",
        draft: {},
    };
    await db.state.put({ key: `draft:${INVENTORY}/${LOCATION.id}/11`, value: draft });
    await db.inventories.update(INVENTORY, { gesperrt: true });
    const app = mount(App, { target: target });
    await shows("Kino Ost");
    expect(target.textContent).not.toContain("Nicht gespeichert");
    unmount(app);

    await db.inventories.update(INVENTORY, { gesperrt: false });
    const again = mount(App, { target: target });
    await shows("Nicht gespeichert: Artikel C 0,33 · Thekenfront");
    unmount(again);
});

/** A server that no longer lists the inventory: closed, deleted or back in preparation. */
function unlisted() {
    globalThis.fetch = (url) => {
        const path = url.replace("/api/v1", "").split("?")[0];
        const answer = path === "/inventuren/" ? [] : path === "/ich/" ? { vorname: "Zoe" } : null;
        return Promise.resolve({
            ok: answer !== null,
            status: answer === null ? 404 : 200,
            text: () => Promise.resolve(JSON.stringify(answer ?? { detail: "Nicht gefunden." })),
        });
    };
}

/**
 * Locked at its last listing, then no longer listed: closed, say, while a
 * form of it was left unsaved. The device keeps it for the draft. Until
 * 10/2026 its old lock hid the notice for good, nothing led to the draft,
 * and its rows and master data stayed until someone signed out.
 */
it("leads to the draft of an inventory that left the count while locked, and then lets it go", async () => {
    const draft = {
        inventoryId: INVENTORY,
        area: "Theke",
        locationId: LOCATION.id,
        articleId: 11,
        label: "Artikel C 0,33 · Thekenfront",
        draft: {},
    };
    await db.state.put({ key: `draft:${INVENTORY}/${LOCATION.id}/11`, value: draft });
    await db.inventories.update(INVENTORY, { gesperrt: true });
    unlisted();
    const app = mount(App, { target: target });

    await shows("Nicht gespeichert: Artikel C 0,33 · Thekenfront");
    findButton(target, "Nicht gespeichert").click();
    await shows("Hier war noch eine Eingabe offen");
    findButton(target, "Verwerfen").click();
    findButton(target, "Abbrechen").click();
    await vi.waitFor(() => expect(route.parts).toEqual([]));

    await refresh();
    expect(await db.inventories.get(INVENTORY)).toBeUndefined();
    expect(await db.positions.count()).toBe(0);
    expect(await db.masterData.count()).toBe(0);
    unmount(app);
});

/**
 * Out of the count, the open form stays so nothing typed in it is lost.
 * Another article of the inventory, asked for by the address, is no such
 * form. Until 10/2026 it opened as if the count went on.
 */
it("leads to the list when another article of an inventory out of the count is asked for", async () => {
    const draft = {
        inventoryId: INVENTORY,
        area: "Theke",
        locationId: LOCATION.id,
        articleId: 11,
        label: "Artikel C 0,33 · Thekenfront",
        draft: {},
    };
    await db.state.put({ key: `draft:${INVENTORY}/${LOCATION.id}/11`, value: draft });
    unlisted();
    const app = mount(App, { target: target });
    go("inventory", INVENTORY, "area", "Theke", "location", LOCATION.id, "article", 11);
    await shows("Was du hier eingibst, bleibt auf dem Gerät");

    go("inventory", INVENTORY, "area", "Theke", "location", LOCATION.id, "article", 12);

    await vi.waitFor(() => expect(route.parts).toEqual([]));
    await shows("Diese Inventur ist gerade nicht in der Zählung");
    expect(target.textContent).not.toContain("Schale");
    // The form of before keeps its draft, and the notice leads back to it.
    await shows("Nicht gespeichert: Artikel C 0,33 · Thekenfront");
    unmount(app);
});
