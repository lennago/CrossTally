/**
 * The entry form, the place where counting really happens.
 *
 * Everything without network: the inputs land in the storage and as a job
 * in the queue. Checked is what stood out when clicking through: the
 * missing field for the recipe article, the pack button, the vessel choice.
 */

import { mount, unmount } from "svelte";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import App from "./App.svelte";
import { draftFrom } from "./services/draft.svelte.js";
import { saveDraft, savedDraft } from "./services/draft_backup.js";
import { db } from "./services/storage.js";
import { go, route } from "./services/route.svelte.js";
import { close, counting, refresh } from "./services/counting.svelte.js";
import { apply } from "./services/language.svelte.js";
import { respond } from "./test_support.js";

const INVENTORY = 1;
const LOCATION = { id: 7, name: "Thekenfront", bereich: "Theke" };
const CRATE = { id: 2, name: "Kasten", faktor_in_basiseinheit: "20.000" };
const SINGLE = { id: 3, name: "Einzeln", ist_einzeln: true, faktor_in_basiseinheit: "1.000" };

let target;
//: The app of the case, taken down in 'afterEach' ('open').
let app = null;

function position(overrides) {
    return {
        inventur_id: INVENTORY,
        ort: LOCATION,
        location_id: LOCATION.id,
        wiegungen: [],
        eingabemenge: null,
        menge_basiseinheit: null,
        erwartete_menge: null,
        unerwartet: false,
        wird_gewogen: false,
        gebinde: null,
        zuletzt_geaendert: "2026-01-31T10:00:00Z",
        ...overrides,
    };
}

beforeEach(async () => {
    // The language lives in the module. Without this the next case would see English.
    apply("de");
    await Promise.all([
        db.state.clear(),
        db.inventories.clear(),
        db.positions.clear(),
        db.masterData.clear(),
        db.queue.clear(),
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
        behaelter: [
            { id: 1, name: "Ohne Behälter", tara: "0.000" },
            { id: 3, name: "Schale", tara: "250.000" },
            { id: 4, name: "Eimer", tara: "300.000" },
        ],
        artikel: [
            {
                id: 11,
                name: "Artikel C 0,33",
                artikelnummer: "101001",
                einheit: "STUECK",
                wird_gewogen: false,
                gebinde: [CRATE, SINGLE],
                behaelter: [],
            },
            {
                id: 12,
                name: "Artikel N lose",
                artikelnummer: "101002",
                einheit: "GRAMM",
                wird_gewogen: true,
                gebinde: [],
                behaelter: [1, 3],
            },
            {
                id: 14,
                name: "Ware P lose",
                artikelnummer: "101004",
                einheit: "GRAMM",
                wird_gewogen: true,
                gebinde: [],
                behaelter: [1, 3, 4],
            },
            {
                id: 15,
                name: "Ware E",
                artikelnummer: "101005",
                einheit: "GRAMM",
                wird_gewogen: true,
                // In storage in the box, at the counter in the bowl: whether
                // weighing happens here is said by the location's weighing row.
                gebinde: [{ id: 5, name: "Karton", faktor_in_basiseinheit: "5000.000" }],
                behaelter: [1, 3],
            },
            {
                id: 13,
                name: "Portion N",
                artikelnummer: "",
                einheit: "STUECK",
                wird_gewogen: false,
                gebinde: [],
                behaelter: [],
            },
        ],
    });
    // The count store lives in the module and outlives the test case.
    close();
    target = document.createElement("div");
    document.body.append(target);
});

afterEach(async () => {
    // Taken down here and not at the end of the case: a case that fails on
    // the way would otherwise leave its app running, and that app would
    // answer the address of the next case too.
    if (app) unmount(app);
    app = null;
    // If a case ends with "Next ›", saving is still running when it tears down
    // the app. Once done, it jumps to the next address, and that would
    // otherwise hit the form of the following case, which then occasionally
    // failed. So let it finish first, then reset the address.
    await new Promise((done) => setTimeout(done, 100));
    target.remove();
    window.location.hash = "";
});

/**
 * Typing like a person: 'input' reports every keystroke, 'change' leaving
 * the field. In between Svelte has to be able to catch up, otherwise the
 * button next to it is still disabled when the test presses it.
 */
async function typeInto(field, value) {
    field.value = value;
    field.dispatchEvent(new Event("input", { bubbles: true }));
    await new Promise((done) => setTimeout(done, 0));
    field.dispatchEvent(new Event("change", { bubbles: true }));
    await new Promise((done) => setTimeout(done, 0));
}

/** Choose a vessel as the selection does: set value, report 'change'. */
async function chooseContainer(selection, id) {
    selection.value = String(id);
    selection.dispatchEvent(new Event("change", { bubbles: true }));
    await new Promise((done) => setTimeout(done, 0));
}

async function open(articleId) {
    app = mount(App, { target: target });
    go("inventory", INVENTORY, "area", "Theke", "location", LOCATION.id, "article", articleId);
    await vi.waitFor(() => expect(target.querySelector("header h1")).toBeTruthy());
}

/** "Next ›" at the bottom: it adopts the draft, like every leaving of the form except "Cancel". */
function confirm() {
    const button = target.querySelector("button.next");
    expect(button, "Knopf „Nächster“").toBeTruthy();
    button.click();
}

it("gives a recipe article without packs a field for the piece count", async () => {
    await open(13);
    await vi.waitFor(() => expect(target.textContent).toContain("Anzahl"));

    await typeInto(target.querySelector(".count-row input"), "12");
    confirm();

    // The row does not exist yet. It only arises with confirming.
    await vi.waitFor(async () => {
        const rows = await db.positions.where("artikel_id").equals(13).toArray();
        expect(rows).toHaveLength(1);
        expect(rows[0].eingabemenge).toBe("12");
    });
});

it("writes only on confirming, not while typing", async () => {
    await db.positions.put(
        position({
            id: 101,
            artikel_id: 11,
            artikel: "Artikel C 0,33",
            einheit: "STUECK",
            gebinde: CRATE,
        }),
    );
    await open(11);
    await vi.waitFor(() => expect(target.textContent).toContain("Kasten"));

    await typeInto(target.querySelector(".count-row input"), "7");
    expect((await db.positions.get(101)).eingabemenge).toBeNull();
    expect(await db.queue.count()).toBe(0);

    confirm();
    await vi.waitFor(async () => {
        expect((await db.positions.get(101)).eingabemenge).toBe("7");
    });
});

it("shows only the expected packs", async () => {
    await db.positions.put(
        position({
            id: 102,
            artikel_id: 11,
            artikel: "Artikel C 0,33",
            einheit: "STUECK",
            gebinde: CRATE,
        }),
    );
    await open(11);
    // Only "Kasten" is expected here. "Einzeln" stands ready as a button.
    await vi.waitFor(() => expect(target.querySelectorAll(".count-row")).toHaveLength(1));
    expect(target.textContent).toContain("Weiteres Gebinde");
    expect(target.textContent).toContain("+ Einzeln");
});

it("shows unit and single piece in the app's language", async () => {
    await db.positions.put(
        position({
            id: 102,
            artikel_id: 11,
            artikel: "Artikel C 0,33",
            einheit: "STUECK",
            gebinde: SINGLE,
        }),
    );
    apply("en");
    await open(11);
    await vi.waitFor(() => expect(target.querySelectorAll(".count-row")).toHaveLength(1));
    expect(target.textContent).toContain("counted in pcs");
    expect(target.textContent).toContain("Single");
    // The stored name no longer counts for the single unit.
    expect(target.textContent).not.toContain("Einzeln");
});

it("adds a further pack only on request", async () => {
    await db.positions.put(
        position({
            id: 103,
            artikel_id: 11,
            artikel: "Artikel C 0,33",
            einheit: "STUECK",
            gebinde: CRATE,
        }),
    );
    await open(11);
    await vi.waitFor(() => expect(target.querySelectorAll(".count-row")).toHaveLength(1));

    [...target.querySelectorAll("button")].find((b) => b.textContent.includes("Einzeln")).click();
    await vi.waitFor(() => expect(target.querySelectorAll(".count-row")).toHaveLength(2));

    await typeInto(target.querySelectorAll(".count-row input")[1], "3");
    confirm();

    await vi.waitFor(async () => {
        const rows = await db.positions.where("artikel_id").equals(11).toArray();
        expect(rows.map((z) => z.gebinde?.name).sort()).toEqual(["Einzeln", "Kasten"]);
    });
});

/** A row that does not exist here would otherwise stand as open until closing. */
it("removes a row on request", async () => {
    await db.positions.put(
        position({
            id: 105,
            artikel_id: 11,
            artikel: "Artikel C 0,33",
            einheit: "STUECK",
            gebinde: CRATE,
            server_id: 105,
        }),
    );
    await open(11);
    await vi.waitFor(() => expect(target.textContent).toContain("Kasten"));

    [...target.querySelectorAll("button")]
        .find((b) => b.textContent.trim() === "entfernen")
        .click();
    await new Promise((done) => setTimeout(done, 0));
    confirm();

    // Locally gone at once, and the job for it lies in the queue.
    await vi.waitFor(async () => {
        expect(await db.positions.get(105)).toBeUndefined();
        const job = (await db.queue.toArray()).find((a) => a.kind === "position_removed");
        expect(job.serverId).toBe(105);
    });
});

/** What was never at the server is settled with striking it. */
it("sends no job for a row that never went out", async () => {
    await open(11);
    await vi.waitFor(() => expect(target.textContent).toContain("Weiteres Gebinde"));

    [...target.querySelectorAll("button")].find((b) => b.textContent.includes("Kasten")).click();
    await vi.waitFor(() => expect(target.querySelectorAll(".count-row")).toHaveLength(1));
    [...target.querySelectorAll("button")]
        .find((b) => b.textContent.trim() === "entfernen")
        .click();
    await vi.waitFor(() => expect(target.querySelectorAll(".count-row")).toHaveLength(0));

    confirm();
    await new Promise((done) => setTimeout(done, 0));
    expect(await db.queue.count()).toBe(0);
});

it("lets the vessel be chosen per weighing", async () => {
    await open(12);
    await vi.waitFor(() => expect(target.textContent).toContain("Wiegen"));

    const selection = target.querySelector(".weighing select");
    expect(selection, "Gefäßauswahl je Wiegung").toBeTruthy();
    // "Choose vessel …" and the two vessels of the product.
    expect([...selection.options]).toHaveLength(3);

    await chooseContainer(selection, 3);
    await typeInto(target.querySelector(".weighing input"), "1250");
    confirm();

    await vi.waitFor(async () => {
        const job = (await db.queue.toArray()).find((a) => a.kind === "weighing");
        expect(job.payload).toMatchObject({ brutto_gramm: "1250", behaelter: 3 });
        // The id is assigned by the device. It makes the job recognisable.
        expect(job.payload.kennung).toBeTruthy();
    });
});

it("corrects a stored weighing on confirming", async () => {
    // 1250 read wrongly and the wrong vessel: both change, as a correction of
    // the stored weighing, not as a new bowl.
    await db.positions.put(
        position({
            id: 55,
            artikel_id: 12,
            artikel: "Artikel N lose",
            wird_gewogen: true,
            wiegungen: [{ id: 9, brutto_gramm: "1250.000", behaelter: 3, netto: "1000.000" }],
        }),
    );
    await open(12);
    await vi.waitFor(() => expect(target.querySelector(".weighing input")).toBeTruthy());
    expect(target.querySelector(".weighing input").value).toMatch(/^1\.?250$/);
    expect(target.textContent).toContain("gespeichert:");

    await chooseContainer(target.querySelector(".weighing select"), 1);
    await typeInto(target.querySelector(".weighing input"), "1520");
    confirm();

    await vi.waitFor(async () => {
        const jobs = await db.queue.toArray();
        expect(jobs.map((job) => job.kind)).toEqual(["weighing_changed"]);
        expect(jobs[0]).toMatchObject({
            weighing: 9,
            payload: { brutto_gramm: "1520", behaelter: 1 },
        });
    });
    const stored = (await db.positions.get(55)).wiegungen[0];
    expect([stored.id, stored.brutto_gramm, stored.behaelter]).toEqual([9, "1520", 1]);
});

it("preselects no vessel, not even the one stored on the product", async () => {
    // Artikel N has only the bowl. It is not preselected nonetheless:
    // what is preselected one accepts without looking.
    await open(12);
    await vi.waitFor(() => expect(target.textContent).toContain("Wiegen"));

    const selection = target.querySelector(".weighing select");
    expect(selection.selectedOptions[0].textContent).toContain("Gefäß wählen");
    await typeInto(target.querySelector(".weighing input"), "900");
    confirm();
    await vi.waitFor(() =>
        expect(target.textContent).toContain("Bitte zu jeder Wiegung das Gefäß wählen."),
    );
    expect(await db.queue.count()).toBe(0);

    await chooseContainer(selection, 3);
    confirm();
    await vi.waitFor(async () => {
        const job = (await db.queue.toArray()).find((a) => a.kind === "weighing");
        expect(job.payload).toMatchObject({ brutto_gramm: "900", behaelter: 3 });
    });
});

/** "Decide when weighing": preselecting any would mean guessing the tare. */
it("releases the weight only once the vessel is chosen", async () => {
    const opened = watchPicker();
    await open(14);
    await vi.waitFor(() => expect(target.textContent).toContain("Wiegen"));

    const selection = target.querySelector(".weighing select");
    const weight = target.querySelector(".weighing input");
    expect(selection.selectedOptions[0].textContent).toContain("Gefäß wählen");
    // Reachable but not writable: the iPhone's arrow skips both the
    // selection and a disabled field and would otherwise do nothing.
    expect(weight.readOnly).toBe(true);
    expect(weight.disabled).toBe(false);
    expect(weight.placeholder).toBe("Erst Gefäß wählen");

    // Whoever lands here is led to the vessel choice, and it opens.
    weight.focus();
    expect(document.activeElement).toBe(selection);
    expect(opened.at(-1)).toBe(selection);
    delete HTMLSelectElement.prototype.showPicker;

    // "Without container" is a choice too.
    await chooseContainer(
        selection,
        [...selection.options].find((entry) => entry.textContent.includes("Ohne")).value,
    );

    expect(weight.readOnly).toBe(false);
    expect(weight.placeholder).toBe("Brutto in g");
    // And the focus stands right there where weighing happens now.
    await vi.waitFor(() => expect(document.activeElement).toBe(weight));
    await typeInto(weight, "1500");
    confirm();
    await vi.waitFor(async () => {
        const job = (await db.queue.toArray()).find((a) => a.kind === "weighing");
        expect(job.payload).toMatchObject({ brutto_gramm: "1500", behaelter: 1 });
    });
});

it("leaves the vessel open in every further weighing row too", async () => {
    await open(14);
    await vi.waitFor(() => expect(target.textContent).toContain("Wiegen"));

    [...target.querySelectorAll("button")]
        .find((b) => b.textContent.includes("Weitere Wiegung"))
        .click();
    await vi.waitFor(() => expect(target.querySelectorAll(".weighing select")).toHaveLength(2));
    for (const selection of target.querySelectorAll(".weighing select")) {
        expect(selection.selectedOptions[0].textContent).toContain("Gefäß wählen");
    }
});

it("shows the expected quantity as a hint", async () => {
    await db.positions.put(
        position({
            id: 103,
            artikel_id: 11,
            artikel: "Artikel C 0,33",
            einheit: "STUECK",
            gebinde: CRATE,
            erwartete_menge: "6.000",
        }),
    );
    await open(11);
    // In the empty field stands what is usual here, grey so it does not count.
    await vi.waitFor(() => expect(target.textContent).toContain("üblich 6"));
    expect(target.querySelector(".count-row input").placeholder).toBe("6");
});

/** Whoever weighs weighs: having to open the first row first costs a handle. */
it("has a row from the start when weighing", async () => {
    await open(12);
    await vi.waitFor(() => expect(target.querySelectorAll(".weighing")).toHaveLength(1));

    // Empty it stays without consequences.
    confirm();
    await new Promise((done) => setTimeout(done, 0));
    expect(await db.queue.count()).toBe(0);
});

const BOX = { id: 5, name: "Karton", faktor_in_basiseinheit: "5000.000" };

it("allows the scale where the location expects only the pack", async () => {
    await db.positions.put(
        position({ id: 301, artikel_id: 15, artikel: "Ware E", einheit: "GRAMM", gebinde: BOX }),
    );
    await open(15);
    await vi.waitFor(() => expect(target.querySelector(".count-row input")).toBeTruthy());

    expect(target.querySelector(".weighing")).toBeNull();
    const button = [...target.querySelectorAll("button")].find(
        (b) => b.textContent.trim() === "+ Wiegen",
    );
    expect(button, "Knopf fürs Wiegen").toBeTruthy();

    // On request it works nonetheless. The row then arises like a pack
    // nobody expected here.
    button.click();
    await vi.waitFor(() => expect(target.querySelector(".weighing input")).toBeTruthy());
    expect(target.textContent).toContain("hier nicht erwartet");
    await chooseContainer(target.querySelector(".weighing select"), 1);
    await typeInto(target.querySelector(".weighing input"), "800");
    confirm();

    await vi.waitFor(async () => {
        const rows = await db.positions.where("artikel_id").equals(15).toArray();
        const scale = rows.find((row) => !row.gebinde);
        expect(scale, "Wiegezeile").toBeTruthy();
        expect(scale.unerwartet).toBe(true);
        const jobs = await db.queue.toArray();
        expect(jobs.filter((job) => job.kind === "weighing")).toHaveLength(1);
    });
});

it("opens the scale where the location is to be weighed", async () => {
    await db.positions.bulkPut([
        position({ id: 302, artikel_id: 15, artikel: "Ware E", einheit: "GRAMM", gebinde: BOX }),
        position({
            id: 303,
            artikel_id: 15,
            artikel: "Ware E",
            einheit: "GRAMM",
            wird_gewogen: true,
        }),
    ]);
    await open(15);
    await vi.waitFor(() => expect(target.querySelector(".weighing input")).toBeTruthy());
    expect(target.textContent).not.toContain("hier nicht erwartet");
});

it("collects several weighings and sends them on confirming", async () => {
    await open(12);
    await vi.waitFor(() => expect(target.textContent).toContain("Wiegen"));

    const addButton = [...target.querySelectorAll("button")].find((b) =>
        b.textContent.includes("Weitere Wiegung"),
    );
    expect(addButton, "Knopf für eine weitere Wiegung").toBeTruthy();
    await chooseContainer(target.querySelector(".weighing select"), 3);
    await typeInto(target.querySelector(".weighing input"), "1250");

    addButton.click();
    await new Promise((done) => setTimeout(done, 0));
    await chooseContainer(target.querySelectorAll(".weighing select")[1], 1);
    await typeInto(target.querySelectorAll(".weighing input")[1], "900");

    // Up to here nothing has gone out.
    expect(await db.queue.count()).toBe(0);
    confirm();

    await vi.waitFor(async () => {
        const jobs = await db.queue.toArray();
        const weighings = jobs.filter((job) => job.kind === "weighing");
        expect(weighings.map((job) => job.payload.brutto_gramm)).toEqual(["1250", "900"]);
    });
});

const selection = () =>
    [...target.querySelectorAll(".extras button")].map((b) => b.textContent.trim());

/** Whoever mistyped brings the pack back with one grip. */
it("puts a just fetched pack back into the selection on removing", async () => {
    await open(11);
    await vi.waitFor(() => expect(selection()).toEqual(["+ Kasten", "+ Einzeln"]));

    [...target.querySelectorAll("button")].find((b) => b.textContent.includes("Einzeln")).click();
    await vi.waitFor(() => expect(selection()).toEqual(["+ Kasten"]));
    [...target.querySelectorAll("button")]
        .find((b) => b.textContent.trim() === "entfernen")
        .click();

    // Back, and in its place, not appended at the back.
    await vi.waitFor(() => expect(selection()).toEqual(["+ Kasten", "+ Einzeln"]));
});

it("puts an expected pack into the selection too and fetches it back with its number", async () => {
    await db.positions.put(
        position({
            id: 107,
            artikel_id: 11,
            artikel: "Artikel C 0,33",
            einheit: "STUECK",
            gebinde: CRATE,
            server_id: 107,
        }),
    );
    await open(11);
    await vi.waitFor(() => expect(target.querySelectorAll(".count-row")).toHaveLength(1));
    await typeInto(target.querySelector(".count-row input"), "4");

    [...target.querySelectorAll("button")]
        .find((b) => b.textContent.trim() === "entfernen")
        .click();
    await vi.waitFor(() => expect(target.querySelectorAll(".count-row")).toHaveLength(0));
    expect(selection()).toEqual(["+ Kasten", "+ Einzeln"]);

    [...target.querySelectorAll("button")].find((b) => b.textContent.includes("Kasten")).click();
    await vi.waitFor(() => expect(target.querySelectorAll(".count-row")).toHaveLength(1));
    // The same row as before, not a new unexpected one.
    expect(target.querySelector(".count-row").textContent).not.toContain("unerwartet");
    expect(target.querySelector(".count-row input").value).toBe("4");

    confirm();
    await vi.waitFor(async () => expect((await db.positions.get(107)).eingabemenge).toBe("4"));
    const kinds = (await db.queue.toArray()).map((job) => job.kind);
    expect(kinds).toEqual(["quantity"]);
});

it("discards the draft when nobody confirms", async () => {
    await db.positions.put(
        position({
            id: 104,
            artikel_id: 11,
            artikel: "Artikel C 0,33",
            einheit: "STUECK",
            gebinde: CRATE,
        }),
    );
    await open(11);
    await vi.waitFor(() => expect(target.querySelector(".count-row input")).toBeTruthy());

    await typeInto(target.querySelector(".count-row input"), "9");
    // Cancel instead of confirm: the note goes into the bin.
    [...target.querySelectorAll("button")].find((b) => b.textContent.includes("Abbrechen")).click();

    await new Promise((done) => setTimeout(done, 300));
    expect((await db.positions.get(104)).eingabemenge).toBeNull();
    expect(await db.queue.count()).toBe(0);
});

const LOCATION_PAGE = "#/inventory/1/area/Theke/location/7";

it("adopts the draft via “Back” in the bar too", async () => {
    await db.positions.put(
        position({
            id: 106,
            artikel_id: 11,
            artikel: "Artikel C 0,33",
            einheit: "STUECK",
            gebinde: CRATE,
        }),
    );
    await open(11);
    await vi.waitFor(() => expect(target.querySelector(".count-row input")).toBeTruthy());

    await typeInto(target.querySelector(".count-row input"), "4");
    target.querySelector("button.back").click();

    await vi.waitFor(async () => {
        expect((await db.positions.get(106)).eingabemenge).toBe("4");
    });
    await vi.waitFor(() => expect(window.location.hash).toBe(LOCATION_PAGE));
});

it("adopts the draft via the device's back button too", async () => {
    await db.positions.put(
        position({
            id: 107,
            artikel_id: 11,
            artikel: "Artikel C 0,33",
            einheit: "STUECK",
            gebinde: CRATE,
        }),
    );
    await open(11);
    await vi.waitFor(() => expect(target.querySelector(".count-row input")).toBeTruthy());

    await typeInto(target.querySelector(".count-row input"), "6");
    // Past the router, as the browser does when going back: the address
    // changes, and the app learns of it only through 'hashchange'.
    window.location.hash = LOCATION_PAGE;

    await vi.waitFor(async () => {
        expect((await db.positions.get(107)).eingabemenge).toBe("6");
    });
    // The form is closed, and the address stays where the device wanted to go.
    await vi.waitFor(() => expect(target.querySelector(".footer-bar")).toBeNull());
    expect(window.location.hash).toBe(LOCATION_PAGE);
});

/** Artikel C before Artikel N, as on the shelf, and nothing behind it. */
async function twoArticlesAtLocation() {
    await db.positions.bulkPut([
        position({
            id: 201,
            artikel_id: 11,
            artikel: "Artikel C 0,33",
            einheit: "STUECK",
            gebinde: CRATE,
            sortierung: 1,
        }),
        position({
            id: 202,
            artikel_id: 12,
            artikel: "Artikel N lose",
            einheit: "GRAMM",
            wird_gewogen: true,
            sortierung: 2,
        }),
    ]);
}

it("saves and opens the next article of the list right away", async () => {
    await twoArticlesAtLocation();
    await open(11);
    await vi.waitFor(() => expect(target.querySelector(".count-row input")).toBeTruthy());
    expect(target.querySelector(".article-header").textContent).toContain("Danach: Artikel N lose");
    expect(target.querySelector(".next").textContent.trim()).toBe("Nächster ›");

    await typeInto(target.querySelector(".count-row input"), "5");
    const historyLength = window.history.length;
    confirm();

    await vi.waitFor(() =>
        expect(target.querySelector("header h1").textContent).toBe("Artikel N lose"),
    );
    expect((await db.positions.get(201)).eingabemenge).toBe("5");
    expect(window.location.hash).toBe(`${LOCATION_PAGE}/article/12`);
    // In place of the old form, not behind it: the device's back button
    // should lead to the list and not back form by form.
    expect(window.history.length).toBe(historyLength);
    // A fresh form: nothing of Artikel C has remained.
    expect(target.querySelector(".count-row")).toBeNull();
    expect(target.querySelector(".weighing input").value).toBe("");
});

it("leads back to the list after the last article", async () => {
    await twoArticlesAtLocation();
    await open(12);
    await vi.waitFor(() => expect(target.textContent).toContain("Wiegen"));
    expect(target.querySelector(".article-header").textContent).not.toContain("Danach");

    await chooseContainer(target.querySelector(".weighing select"), 3);
    await typeInto(target.querySelector(".weighing input"), "1200");
    expect(target.querySelector(".next").textContent.trim()).toBe("Fertig");
    confirm();

    await vi.waitFor(() => expect(window.location.hash).toBe(LOCATION_PAGE));
});

it("stays in the form when the input is wrong before moving on", async () => {
    await twoArticlesAtLocation();
    await open(11);
    await vi.waitFor(() => expect(target.querySelector(".count-row input")).toBeTruthy());

    await typeInto(target.querySelector(".count-row input"), "viele");
    confirm();

    await vi.waitFor(() => expect(target.textContent).toContain("Bitte nur Zahlen"));
    expect(target.querySelector("header h1").textContent).toBe("Artikel C 0,33");
});

it("shows after going back at which article was counted last", async () => {
    await twoArticlesAtLocation();
    const scrollSpy = vi.spyOn(Element.prototype, "scrollIntoView");
    await open(11);
    await vi.waitFor(() => expect(target.querySelector(".count-row input")).toBeTruthy());

    // On to Artikel N and back from there: remembered is the article where
    // someone last was, not the one they tapped in the list.
    confirm();
    await vi.waitFor(() =>
        expect(target.querySelector("header h1").textContent).toBe("Artikel N lose"),
    );
    target.querySelector("button.back").click();

    await vi.waitFor(() => expect(target.querySelector(".row.marked")).toBeTruthy());
    expect(target.querySelector(".row.marked").textContent).toContain("Artikel N lose");
    expect(scrollSpy.mock.instances.at(-1).dataset.key).toBe("12");
    scrollSpy.mockRestore();
});

it("stays in the form on “Back” when the input is wrong", async () => {
    await open(14);
    await vi.waitFor(() => expect(target.textContent).toContain("Wiegen"));
    const formAddress = window.location.hash;

    await typeInto(target.querySelector(".weighing input"), "1500");
    target.querySelector("button.back").click();
    await vi.waitFor(() =>
        expect(target.textContent).toContain("Bitte zu jeder Wiegung das Gefäß wählen."),
    );
    expect(window.location.hash).toBe(formAddress);

    // The same through the device: the address returns to the form so it
    // fits the view.
    window.location.hash = LOCATION_PAGE;
    await vi.waitFor(() => expect(window.location.hash).toBe(formAddress));
    expect(target.querySelector(".footer-bar")).toBeTruthy();
    expect(await db.queue.count()).toBe(0);
});

it("takes a comma as decimal separator", async () => {
    await db.positions.put(
        position({
            id: 105,
            artikel_id: 11,
            artikel: "Artikel C 0,33",
            einheit: "STUECK",
            gebinde: CRATE,
        }),
    );
    await open(11);
    await vi.waitFor(() => expect(target.querySelector(".count-row input")).toBeTruthy());

    await typeInto(target.querySelector(".count-row input"), "1,5");
    confirm();

    await vi.waitFor(async () => {
        expect((await db.positions.get(105)).eingabemenge).toBe("1.5");
    });
});

/** The row of the crate with a number that is already there. */
function countedCrate(id, quantity) {
    return position({
        id,
        artikel_id: 11,
        artikel: "Artikel C 0,33",
        einheit: "STUECK",
        gebinde: CRATE,
        eingabemenge: quantity,
        server_id: id,
    });
}

/** Tap "+ add" of the first count row and wait for the emptied field. */
async function tapAdd() {
    const button = [...target.querySelectorAll(".count-row button")].find(
        (candidate) => candidate.textContent.trim() === "+ dazu",
    );
    expect(button, "Knopf „+ dazu“").toBeTruthy();
    button.click();
    return vi.waitFor(() => {
        const field = target.querySelector(".count-row input");
        expect(field.value).toBe("");
        expect(document.activeElement).toBe(field);
        return field;
    });
}

/** Leave the field as a finger tapping elsewhere would. */
async function leave(field) {
    field.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
    await new Promise((done) => setTimeout(done, 0));
}

it("adds a further find to the number on request instead of overwriting", async () => {
    await db.positions.put(countedCrate(120, "12.000"));
    await open(11);
    await vi.waitFor(() => expect(target.querySelector(".count-row input").value).toBe("12"));

    const field = await tapAdd();
    expect(target.textContent).toContain("bisher 12 +");
    await typeInto(field, "3");
    expect(target.textContent).toContain("bisher 12 + 3 = 15");

    // Leaving the field shows the sum, and the draft already held it.
    await leave(field);
    await vi.waitFor(() => expect(target.querySelector(".count-row input").value).toBe("15"));
    expect(target.textContent).not.toContain("bisher");

    confirm();
    await vi.waitFor(async () => {
        expect((await db.positions.get(120)).eingabemenge).toBe("15");
    });
    // The server learns what stood there: someone else counting in between
    // still leads to the question.
    const [job] = await db.queue.toArray();
    expect(job.payload).toEqual({ eingabemenge: "15", eingabemenge_vorher: "12.000" });
});

/** Tap "− off" of the first count row and wait for the emptied field. */
async function tapSubtract() {
    const button = [...target.querySelectorAll(".count-row button")].find(
        (candidate) => candidate.textContent.trim() === "− ab",
    );
    expect(button, "Knopf „− ab“").toBeTruthy();
    button.click();
    return vi.waitFor(() => {
        const field = target.querySelector(".count-row input");
        expect(field.value).toBe("");
        expect(document.activeElement).toBe(field);
        return field;
    });
}

it("takes a quantity off the number on request", async () => {
    await db.positions.put(countedCrate(130, "12.000"));
    await open(11);
    await vi.waitFor(() => expect(target.querySelector(".count-row input").value).toBe("12"));

    const field = await tapSubtract();
    expect(target.textContent).toContain("bisher 12 −");
    await typeInto(field, "3");
    expect(target.textContent).toContain("bisher 12 − 3 = 9");

    await leave(field);
    await vi.waitFor(() => expect(target.querySelector(".count-row input").value).toBe("9"));
    expect(target.textContent).not.toContain("bisher");

    confirm();
    await vi.waitFor(async () => {
        expect((await db.positions.get(130)).eingabemenge).toBe("9");
    });
    const [job] = await db.queue.toArray();
    expect(job.payload).toEqual({ eingabemenge: "9", eingabemenge_vorher: "12.000" });
});

it("takes off with a comma, down to nothing", async () => {
    await db.positions.put(countedCrate(131, "1.500"));
    await open(11);
    await vi.waitFor(() => expect(target.querySelector(".count-row input").value).toBe("1,5"));

    const field = await tapSubtract();
    await typeInto(field, "1,5");
    expect(target.textContent).toContain("bisher 1,5 − 1,5 = 0");
    await leave(field);
    await vi.waitFor(() => expect(target.querySelector(".count-row input").value).toBe("0"));

    // A zero is a count: the shelf is empty, and someone has said so.
    confirm();
    await vi.waitFor(async () => {
        expect((await db.positions.get(131)).eingabemenge).toBe("0");
    });
});

it("does not go below zero", async () => {
    await db.positions.put(countedCrate(132, "12.000"));
    await open(11);
    await vi.waitFor(() => expect(target.querySelector(".count-row input").value).toBe("12"));

    const field = await tapSubtract();
    await typeInto(field, "15");
    expect(target.textContent).toContain("bisher 12 − 15 = -3");
    await leave(field);

    confirm();
    await vi.waitFor(() =>
        expect(target.querySelector(".error")?.textContent).toContain("nicht unter null"),
    );
    // Nothing went out, and the row stands as it stood.
    expect(await db.queue.count()).toBe(0);
    expect((await db.positions.get(132)).eingabemenge).toBe("12.000");
});

it("offers taking off only where something stands to take from", async () => {
    await db.positions.put(countedCrate(133, "0.000"));
    await open(11);
    await vi.waitFor(() => expect(target.querySelector(".count-row input").value).toBe("0"));
    // To a zero one can add. There is nothing to take from it.
    expect(target.textContent).toContain("+ dazu");
    expect(target.textContent).not.toContain("− ab");

    await typeInto(target.querySelector(".count-row input"), "4");
    await vi.waitFor(() => expect(target.textContent).toContain("− ab"));
});

it("adds and takes off one after the other", async () => {
    await db.positions.put(countedCrate(134, "12.000"));
    await open(11);
    await vi.waitFor(() => expect(target.querySelector(".count-row input").value).toBe("12"));

    await typeInto(await tapAdd(), "5");
    await leave(target.querySelector(".count-row input"));
    await vi.waitFor(() => expect(target.querySelector(".count-row input").value).toBe("17"));

    const field = await tapSubtract();
    expect(target.textContent).toContain("bisher 17 −");
    await typeInto(field, "2");
    await leave(field);
    await vi.waitFor(() => expect(target.querySelector(".count-row input").value).toBe("15"));
});

it("takes off the piece count of a recipe article too", async () => {
    await db.positions.put(
        position({
            id: 135,
            artikel_id: 13,
            artikel: "Portion N",
            einheit: "STUECK",
            eingabemenge: "4.000",
            server_id: 135,
        }),
    );
    await open(13);
    await vi.waitFor(() => expect(target.querySelector(".count-row input").value).toBe("4"));

    const field = await tapSubtract();
    await typeInto(field, "1");
    await leave(field);
    await vi.waitFor(() => expect(target.querySelector(".count-row input").value).toBe("3"));
});

it("offers adding only where a number already stands", async () => {
    await db.positions.put(countedCrate(121, null));
    await open(11);
    await vi.waitFor(() => expect(target.querySelector(".count-row input")).toBeTruthy());
    expect(target.textContent).not.toContain("+ dazu");

    await typeInto(target.querySelector(".count-row input"), "4");
    await vi.waitFor(() => expect(target.textContent).toContain("+ dazu"));
});

it("adds a second find on top of the first", async () => {
    await db.positions.put(countedCrate(122, "12.000"));
    await open(11);
    await vi.waitFor(() => expect(target.querySelector(".count-row input").value).toBe("12"));

    await typeInto(await tapAdd(), "3");
    await leave(target.querySelector(".count-row input"));
    await vi.waitFor(() => expect(target.querySelector(".count-row input").value).toBe("15"));

    // The base is what the field shows now, not the row from the server.
    const field = await tapAdd();
    expect(target.textContent).toContain("bisher 15 +");
    await typeInto(field, "2");
    await leave(field);
    await vi.waitFor(() => expect(target.querySelector(".count-row input").value).toBe("17"));
});

it("leaves the number as it was when nothing is added", async () => {
    await db.positions.put(countedCrate(123, "12.000"));
    await open(11);
    await vi.waitFor(() => expect(target.querySelector(".count-row input").value).toBe("12"));

    await leave(await tapAdd());
    await vi.waitFor(() => expect(target.querySelector(".count-row input").value).toBe("12"));

    confirm();
    await vi.waitFor(() => expect(target.querySelector("header h1")).toBeFalsy());
    expect(await db.queue.count()).toBe(0);
});

it("adds with a comma and writes the sum with a point", async () => {
    await db.positions.put(countedCrate(124, "12.000"));
    await open(11);
    await vi.waitFor(() => expect(target.querySelector(".count-row input").value).toBe("12"));

    const field = await tapAdd();
    await typeInto(field, "1,5");
    expect(target.textContent).toContain("bisher 12 + 1,5 = 13,5");
    await leave(field);
    await vi.waitFor(() => expect(target.querySelector(".count-row input").value).toBe("13,5"));

    confirm();
    await vi.waitFor(async () => {
        expect((await db.positions.get(124)).eingabemenge).toBe("13.5");
    });
});

it("holds the sum in the draft that is written along", async () => {
    await db.positions.put(countedCrate(125, "12.000"));
    await open(11);
    await vi.waitFor(() => expect(target.querySelector(".count-row input").value).toBe("12"));

    // Still in the field, nothing left yet: if the app died now, the copy
    // must not carry the find alone.
    await typeInto(await tapAdd(), "3");
    await vi.waitFor(async () => {
        const saved = await savedDraft({
            inventoryId: INVENTORY,
            locationId: LOCATION.id,
            articleId: 11,
        });
        expect(saved?.draft.packs[0].quantity).toBe("15");
    });
});

it("adds to the piece count of a recipe article too", async () => {
    await db.positions.put(
        position({
            id: 126,
            artikel_id: 13,
            artikel: "Portion N",
            einheit: "STUECK",
            eingabemenge: "4.000",
            server_id: 126,
        }),
    );
    await open(13);
    await vi.waitFor(() => expect(target.querySelector(".count-row input").value).toBe("4"));

    const field = await tapAdd();
    await typeInto(field, "2");
    await leave(field);
    await vi.waitFor(() => expect(target.querySelector(".count-row input").value).toBe("6"));

    confirm();
    await vi.waitFor(async () => {
        expect((await db.positions.get(126)).eingabemenge).toBe("6");
    });
});

it("saves and opens the article before with “Previous”", async () => {
    await twoArticlesAtLocation();
    await open(12);
    await vi.waitFor(() => expect(target.textContent).toContain("Wiegen"));

    await typeInto(target.querySelector(".weighing input"), "1200");
    // Without a vessel it does not go back either.
    target.querySelector("button.previous").click();
    await vi.waitFor(() =>
        expect(target.textContent).toContain("Bitte zu jeder Wiegung das Gefäß wählen."),
    );
    expect(target.querySelector("header h1").textContent).toBe("Artikel N lose");

    await chooseContainer(target.querySelector(".weighing select"), 3);
    target.querySelector("button.previous").click();

    await vi.waitFor(() =>
        expect(target.querySelector("header h1").textContent).toBe("Artikel C 0,33"),
    );
    expect(window.location.hash).toBe(`${LOCATION_PAGE}/article/11`);
    const weighings = (await db.positions.get(202)).wiegungen;
    expect(weighings).toHaveLength(1);
    // Before the first article there is none. The button stays, but disabled.
    expect(target.querySelector("button.previous").disabled).toBe(true);
});

/** The enter key as the on-screen keyboard reports it. */
function pressEnter(field) {
    field.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
}

it("jumps into the next field with the enter key and on after the last one", async () => {
    await twoArticlesAtLocation();
    await db.positions.put(
        position({
            id: 203,
            artikel_id: 11,
            artikel: "Artikel C 0,33",
            einheit: "STUECK",
            gebinde: SINGLE,
            sortierung: 1,
        }),
    );
    await open(11);
    await vi.waitFor(() => expect(target.querySelectorAll(".count-row input")).toHaveLength(2));
    const [first, second] = target.querySelectorAll(".count-row input");
    expect(first.getAttribute("enterkeyhint")).toBe("next");

    first.focus();
    await typeInto(first, "2");
    pressEnter(first);
    expect(document.activeElement).toBe(second);
    // Jumping on alone saves nothing.
    expect((await db.positions.get(201)).eingabemenge).toBeNull();

    await typeInto(second, "7");
    pressEnter(second);
    await vi.waitFor(() =>
        expect(target.querySelector("header h1").textContent).toBe("Artikel N lose"),
    );
    expect((await db.positions.get(201)).eingabemenge).toBe("2");
    expect((await db.positions.get(203)).eingabemenge).toBe("7");
});

it("raises the footer bar above the keyboard, even when the page is scrolled", async () => {
    // The keyboard only shrinks the visible viewport, not the page.
    const viewport = Object.assign(new EventTarget(), {
        height: 800,
        offsetTop: 0,
        pageTop: 0,
        scale: 1,
    });
    window.visualViewport = viewport;
    window.innerHeight = 800;
    try {
        await twoArticlesAtLocation();
        await open(11);
        await vi.waitFor(() => expect(target.querySelector(".footer-bar")).toBeTruthy());
        const bar = target.querySelector(".footer-bar");
        expect(bar.style.top).toBe("");

        viewport.height = 450;
        viewport.dispatchEvent(new Event("resize"));
        await vi.waitFor(() => expect(bar.style.top).toBe("450px"));
        expect(bar.classList.contains("above-keyboard")).toBe(true);

        // Scrolled: iOS sometimes reports that only on the window, not on the viewport.
        viewport.pageTop = 300;
        window.dispatchEvent(new Event("scroll"));
        await vi.waitFor(() => expect(bar.style.top).toBe("750px"));

        // Zoomed in the viewport is smaller too, but no keyboard there.
        Object.assign(viewport, { height: 400, pageTop: 0, scale: 2 });
        viewport.dispatchEvent(new Event("resize"));
        await vi.waitFor(() => expect(bar.style.top).toBe(""));
        expect(bar.classList.contains("above-keyboard")).toBe(false);
    } finally {
        delete window.visualViewport;
        window.innerHeight = 768;
    }
});

// ---------------------------------------------------------------------------
// The draft survives a crash
// ---------------------------------------------------------------------------

const TARGET = { inventoryId: INVENTORY, bereich: "Theke", locationId: LOCATION.id, articleId: 11 };
const CRATE_ROW = position({
    id: 108,
    artikel_id: 11,
    artikel: "Artikel C 0,33",
    einheit: "STUECK",
    gebinde: CRATE,
    server_id: 108,
});

it("writes along what is typed and clears it away on moving on", async () => {
    await db.positions.put(CRATE_ROW);
    await open(11);
    await vi.waitFor(() => expect(target.querySelector(".count-row input")).toBeTruthy());

    await typeInto(target.querySelector(".count-row input"), "6");

    await vi.waitFor(async () =>
        expect((await savedDraft(TARGET))?.draft.packs[0].quantity).toBe("6"),
    );
    confirm();
    await vi.waitFor(async () => expect(await savedDraft(TARGET)).toBeNull());
});

it("clears the written-along draft away on cancelling too", async () => {
    await db.positions.put(CRATE_ROW);
    await open(11);
    await vi.waitFor(() => expect(target.querySelector(".count-row input")).toBeTruthy());
    await typeInto(target.querySelector(".count-row input"), "6");
    await vi.waitFor(async () => expect(await savedDraft(TARGET)).not.toBeNull());

    [...target.querySelectorAll("button")].find((b) => b.textContent.includes("Abbrechen")).click();

    await vi.waitFor(async () => expect(await savedDraft(TARGET)).toBeNull());
});

/** Back in preparation: the list no longer names the inventory, and a count waits. */
function outOfCount() {
    globalThis.fetch = (url, options = {}) => {
        const path = String(url).replace("/api/v1", "").split("?")[0];
        if (path === "/inventuren/") return respond([]);
        if (path === "/ich/") return respond({ id: 1, vorname: "Zoe" });
        if (options.method) {
            return respond({ konflikt: "status", vorlaeufig: true, detail: "Vorbereitung" }, 409);
        }
        return respond({ detail: "Nicht gefunden." }, 404);
    };
}

const STAYS = "bis du es speicherst oder abbrichst";
const WHY = "Diese Inventur ist gerade nicht in der Zählung";

/**
 * The inventory leaves the count while someone types: the form stays with a
 * notice, and the draft stays on the device until its person cancels it.
 * Until 10/2026 the app went to the list and took the draft with it.
 */
it("keeps the form and its draft when the inventory leaves the count, until cancelled", async () => {
    await db.positions.put(CRATE_ROW);
    await open(11);
    await vi.waitFor(() => expect(target.querySelector(".count-row input")).toBeTruthy());
    await typeInto(target.querySelector(".count-row input"), "6");
    await vi.waitFor(async () =>
        expect((await savedDraft(TARGET))?.draft.packs[0].quantity).toBe("6"),
    );

    outOfCount();
    await refresh();

    await vi.waitFor(() => expect(target.textContent).toContain(STAYS));
    expect(target.querySelector(".count-row input").value).toBe("6");
    expect((await savedDraft(TARGET))?.draft.packs[0].quantity).toBe("6");
    expect(await db.inventories.get(INVENTORY)).toBeDefined();

    [...target.querySelectorAll("button")].find((b) => b.textContent.includes("Abbrechen")).click();
    await vi.waitFor(() => expect(route.parts).toEqual([]));
    await vi.waitFor(async () => expect(await savedDraft(TARGET)).toBeNull());
    expect(target.textContent).toContain(WHY);
});

/** Saved out of the count, the number waits, and the list comes up instead of the next article. */
it("saves what was typed out of the count, and the round ends with this form", async () => {
    const portion = position({ id: 109, artikel_id: 13, artikel: "Portion N", server_id: 109 });
    await db.positions.bulkPut([CRATE_ROW, portion]);
    await open(11);
    await vi.waitFor(() => expect(target.querySelector(".count-row input")).toBeTruthy());
    await typeInto(target.querySelector(".count-row input"), "6");
    outOfCount();
    await refresh();
    await vi.waitFor(() => expect(target.textContent).toContain(STAYS));
    expect(target.querySelector("button.next").textContent).toContain("Nächster");

    confirm();

    await vi.waitFor(() => expect(route.parts).toEqual([]));
    const [job] = await db.queue.toArray();
    expect(job).toMatchObject({ kind: "quantity", position: 108, payload: { eingabemenge: "6" } });
    await vi.waitFor(async () => expect(await savedDraft(TARGET)).toBeNull());
    expect(await db.inventories.get(INVENTORY)).toBeDefined();
});

/**
 * The count of what waits belongs to the account, not to the inventory: the
 * list that comes up shows it at once, before its sync answers.
 */
it("keeps the count of what waits when the list comes up out of the count", async () => {
    const portion = position({ id: 109, artikel_id: 13, artikel: "Portion N", server_id: 109 });
    await db.positions.bulkPut([CRATE_ROW, portion]);
    await open(11);
    await vi.waitFor(() => expect(target.querySelector(".count-row input")).toBeTruthy());
    await typeInto(target.querySelector(".count-row input"), "6");
    outOfCount();
    await refresh();
    await vi.waitFor(() => expect(target.textContent).toContain(STAYS));
    // The list's sync waits from here on: only the storage can name the count.
    let release;
    const held = new Promise((resolve) => (release = resolve));
    const answers = globalThis.fetch;
    globalThis.fetch = async (url, options = {}) => {
        if (String(url).endsWith("/inventuren/")) await held;
        return await answers(url, options);
    };

    confirm();

    await vi.waitFor(() => expect(route.parts).toEqual([]));
    await vi.waitFor(() => expect(counting.pending).toBe(1));
    // The sync runs out, so the next case does not find it still running.
    release();
    await vi.waitFor(() => expect(counting.loading).toBe(false));
});

/** Nothing typed, nothing to keep: the list comes up at once, not "does not belong here". */
it("goes to the list at once when nothing was typed before the inventory left the count", async () => {
    await db.positions.put(CRATE_ROW);
    await open(11);
    await vi.waitFor(() => expect(target.querySelector(".count-row input")).toBeTruthy());

    outOfCount();
    await refresh();

    await vi.waitFor(() => expect(route.parts).toEqual([]));
    expect(target.textContent).toContain(WHY);
    expect(target.textContent).not.toContain("gehört nicht zu diesem Haus");
    expect(await db.inventories.get(INVENTORY)).toBeUndefined();
});

/** Left behind: this is what the storage looks like when the app died in the middle of typing. */
async function leaveBehind(quantity) {
    const article = (await db.masterData.get(INVENTORY)).artikel.find((a) => a.id === 11);
    const draft = draftFrom(article, [CRATE_ROW]);
    draft.packs[0].quantity = quantity;
    await saveDraft({ ...TARGET, label: "Artikel C 0,33 · Thekenfront" }, draft);
}

it("offers to continue after a crash and counts it only on moving on", async () => {
    await db.positions.put(CRATE_ROW);
    await leaveBehind("4");
    await open(11);

    await vi.waitFor(() => expect(target.textContent).toContain("nicht gespeichert wurde"));
    expect(target.querySelector(".count-row input").value).toBe("");
    expect(await db.queue.count()).toBe(0);

    [...target.querySelectorAll("button")].find((b) => b.textContent === "Fortsetzen").click();

    await vi.waitFor(() => expect(target.querySelector(".count-row input").value).toBe("4"));
    expect(target.textContent).not.toContain("nicht gespeichert wurde");
    confirm();
    await vi.waitFor(async () => {
        const [job] = await db.queue.toArray();
        expect(job).toMatchObject({
            kind: "quantity",
            position: 108,
            payload: { eingabemenge: "4", eingabemenge_vorher: null },
        });
    });
    await vi.waitFor(async () => expect(await savedDraft(TARGET)).toBeNull());
});

it("discards the left-behind draft on request", async () => {
    await db.positions.put(CRATE_ROW);
    await leaveBehind("4");
    await open(11);
    await vi.waitFor(() => expect(target.textContent).toContain("nicht gespeichert wurde"));

    [...target.querySelectorAll("button")].find((b) => b.textContent === "Verwerfen").click();

    await vi.waitFor(() => expect(target.textContent).not.toContain("nicht gespeichert wurde"));
    expect(target.querySelector(".count-row input").value).toBe("");
    expect(await savedDraft(TARGET)).toBeNull();
});

it("leaves a draft nobody has decided about yet", async () => {
    await db.positions.put(CRATE_ROW);
    await leaveBehind("4");
    await open(11);
    await vi.waitFor(() => expect(target.textContent).toContain("nicht gespeichert wurde"));

    [...target.querySelectorAll("button")].find((b) => b.textContent.includes("Abbrechen")).click();
    await new Promise((done) => setTimeout(done, 100));

    expect((await savedDraft(TARGET))?.draft.packs[0].quantity).toBe("4");
});

it("leads from the start page back to a left-behind draft", async () => {
    await db.positions.put(CRATE_ROW);
    await leaveBehind("4");
    app = mount(App, { target: target });

    const notice = () =>
        [...target.querySelectorAll("button")].find((b) =>
            b.textContent.includes("Nicht gespeichert"),
        );
    await vi.waitFor(() => expect(notice()).toBeTruthy());
    expect(notice().textContent).toContain("Artikel C 0,33 · Thekenfront");

    notice().click();

    await vi.waitFor(() => expect(target.textContent).toContain("nicht gespeichert wurde"));
    // In the form itself the form asks, at the top it is not repeated.
    expect(notice()).toBeUndefined();
});

/** What is selected in the field, as text. That way the first digit replaces exactly that. */
function selectedText(field) {
    return field.value.slice(field.selectionStart, field.selectionEnd);
}

/** Two pack rows, the second already counted. */
async function twoPacks() {
    await db.positions.bulkPut([
        CRATE_ROW,
        position({
            id: 109,
            artikel_id: 11,
            artikel: "Artikel C 0,33",
            einheit: "STUECK",
            gebinde: SINGLE,
            eingabemenge: "12.000",
            erwartete_menge: "6.000",
        }),
    ]);
    await open(11);
    await vi.waitFor(() => expect(target.querySelectorAll(".count-row input")).toHaveLength(2));
}

it("puts the focus into the first field as soon as the form stands", async () => {
    await twoPacks();
    const [first] = target.querySelectorAll(".count-row input");
    await vi.waitFor(() => expect(document.activeElement).toBe(first));
});

it("selects what stands in the next field when jumping with the enter key", async () => {
    await twoPacks();
    const [first, second] = target.querySelectorAll(".count-row input");
    await vi.waitFor(() => expect(document.activeElement).toBe(first));

    pressEnter(first);

    expect(document.activeElement).toBe(second);
    expect(second.value).toBe("12");
    expect(selectedText(second)).toBe("12");
});

it("keeps the selection when the finger taps the field, and only the second tap sets the caret", async () => {
    await twoPacks();
    const second = target.querySelectorAll(".count-row input")[1];

    second.focus();
    // This is how the browser puts the caret after the tap where the finger was.
    second.setSelectionRange(1, 1);
    second.click();
    expect(selectedText(second)).toBe("12");

    second.setSelectionRange(1, 1);
    second.click();
    expect([second.selectionStart, second.selectionEnd]).toEqual([1, 1]);
});

it("leaves the suggestion grey in the empty field and the counted value as value", async () => {
    await db.positions.put(
        position({ ...CRATE_ROW, erwartete_menge: "6.000", eingabemenge: null }),
    );
    await open(11);
    const field = await vi.waitFor(() => {
        const found = target.querySelector(".count-row input");
        expect(document.activeElement).toBe(found);
        return found;
    });

    // The suggestion is no value: nothing to select, nothing that counts.
    expect(field.value).toBe("");
    expect(field.placeholder).toBe("6");
    expect(selectedText(field)).toBe("");

    confirm();
    await vi.waitFor(() => expect(target.querySelector("header h1")).toBeFalsy());
    expect(await db.queue.count()).toBe(0);
});

it("focuses only once a left-behind draft is decided about", async () => {
    await db.positions.put(CRATE_ROW);
    await leaveBehind("4");
    await open(11);
    await vi.waitFor(() => expect(target.textContent).toContain("nicht gespeichert wurde"));
    expect(document.activeElement?.tagName).not.toBe("INPUT");

    [...target.querySelectorAll("button")].find((b) => b.textContent === "Fortsetzen").click();

    await vi.waitFor(() => {
        const field = target.querySelector(".count-row input");
        expect(document.activeElement).toBe(field);
        expect(selectedText(field)).toBe("4");
    });
});

/** Ware E at this location: counted in the box and weighed. 'weighing' adds a stored one. */
async function boxAndScale(weighing = null) {
    await db.positions.bulkPut([
        position({ id: 301, artikel_id: 15, artikel: "Ware E", einheit: "GRAMM", gebinde: BOX }),
        position({
            id: 302,
            artikel_id: 15,
            artikel: "Ware E",
            einheit: "GRAMM",
            wird_gewogen: true,
            wiegungen: weighing ? [weighing] : [],
        }),
    ]);
    await open(15);
    await vi.waitFor(() => expect(target.querySelector(".weighing select")).toBeTruthy());
}

/** 'showPicker' jsdom does not know. Here it is only counted whether the list should open. */
function watchPicker() {
    const opened = [];
    HTMLSelectElement.prototype.showPicker = function () {
        opened.push(this);
    };
    return opened;
}

it("jumps with the enter key from the pack into the empty vessel, opens it and after the choice into the weight", async () => {
    const opened = watchPicker();
    await boxAndScale();
    const box = target.querySelector(".count-row input");
    const selection = target.querySelector(".weighing select");
    const weight = target.querySelector(".weighing input");
    await vi.waitFor(() => expect(document.activeElement).toBe(box));
    // The vessel stands before the weight: choose first, then weigh.
    expect(
        selection.compareDocumentPosition(weight) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    await typeInto(box, "2");
    pressEnter(box);
    expect(document.activeElement).toBe(selection);
    expect(opened).toEqual([selection]);

    await chooseContainer(selection, 3);
    expect(document.activeElement).toBe(weight);
    delete HTMLSelectElement.prototype.showPicker;
});

it("skips an already chosen vessel and goes straight into the weight", async () => {
    const opened = watchPicker();
    await boxAndScale({
        id: 9,
        brutto_gramm: "1250.000",
        behaelter: 3,
        netto: "1000",
    });
    const box = target.querySelector(".count-row input");
    await vi.waitFor(() => expect(document.activeElement).toBe(box));

    pressEnter(box);

    expect(document.activeElement).toBe(target.querySelector(".weighing input"));
    expect(opened).toEqual([]);
    delete HTMLSelectElement.prototype.showPicker;
});

it("starts with the vessel for goods that are only weighed", async () => {
    await open(12);
    await vi.waitFor(() =>
        expect(document.activeElement).toBe(target.querySelector(".weighing select")),
    );
});

it("keeps the keyboard open when paging, in the tap itself, and hands over to the next form", async () => {
    await twoArticlesAtLocation();
    await open(11);
    await vi.waitFor(() => expect(document.activeElement?.matches(".count-row input")).toBe(true));

    confirm();
    // Still in the click: the holder has the focus so iOS leaves the keyboard open.
    expect(document.activeElement?.classList.contains("keyboard-holder")).toBe(true);

    await vi.waitFor(() =>
        expect(target.querySelector("header h1").textContent).toBe("Artikel N lose"),
    );
    await vi.waitFor(() =>
        expect(document.activeElement).toBe(target.querySelector(".weighing select")),
    );
});

it("leads to the missing vessel when moving on fails on it", async () => {
    await twoArticlesAtLocation();
    await open(12);
    const selection = target.querySelector(".weighing select");
    await typeInto(target.querySelector(".weighing input"), "900");

    target.querySelector("button.previous").click();
    expect(document.activeElement?.classList.contains("keyboard-holder")).toBe(true);

    await vi.waitFor(() => expect(target.textContent).toContain("das Gefäß wählen"));
    await vi.waitFor(() => expect(document.activeElement).toBe(selection));
});

it("keeps the keyboard open when opening from the article list too", async () => {
    await twoArticlesAtLocation();
    app = mount(App, { target: target });
    go("inventory", INVENTORY, "area", "Theke", "location", LOCATION.id);
    await vi.waitFor(() => expect(target.querySelectorAll("button.row").length).toBeGreaterThan(0));

    target.querySelector("button.row").click();
    expect(document.activeElement?.classList.contains("keyboard-holder")).toBe(true);

    await vi.waitFor(() =>
        expect(document.activeElement?.matches(".content input, .content select")).toBe(true),
    );
});

it("blocks a weight below the empty vessel, jumps there and lets the 0 through", async () => {
    await open(12);
    await vi.waitFor(() => expect(target.querySelector(".weighing select")).toBeTruthy());
    const weight = target.querySelector(".weighing input");
    await chooseContainer(target.querySelector(".weighing select"), 3);
    await typeInto(weight, "100");

    confirm();
    await vi.waitFor(() =>
        expect(target.textContent).toContain("leichter als das leere Gefäß „Schale“ (250 g)"),
    );
    expect(document.activeElement).toBe(weight);
    expect(weight.classList.contains("too-light")).toBe(true);
    expect(await db.queue.count()).toBe(0);

    // Nothing to weigh: the 0 is a count and goes out.
    await typeInto(weight, "0");
    expect(weight.classList.contains("too-light")).toBe(false);
    confirm();
    await vi.waitFor(async () => {
        const job = (await db.queue.toArray()).find((a) => a.kind === "weighing");
        expect(job.payload).toMatchObject({ brutto_gramm: "0", behaelter: 3 });
    });
});

it("puts the focus into a fetched pack", async () => {
    await db.positions.put(CRATE_ROW);
    await open(11);
    await vi.waitFor(() => expect(document.activeElement?.matches(".count-row input")).toBe(true));

    [...target.querySelectorAll("button")].find((b) => b.textContent.includes("Einzeln")).click();

    await vi.waitFor(() =>
        expect(document.activeElement).toBe(
            target.querySelector(`.count-row[data-pack="${SINGLE.id}"] input`),
        ),
    );
});

it("puts the focus into the vessel of a further weighing and opens it", async () => {
    const opened = watchPicker();
    await open(12);
    await vi.waitFor(() => expect(target.querySelector(".weighing select")).toBeTruthy());

    [...target.querySelectorAll("button")]
        .find((b) => b.textContent.includes("Weitere Wiegung"))
        .click();
    // Still in the tap the holder keeps the keyboard.
    expect(document.activeElement?.classList.contains("keyboard-holder")).toBe(true);

    await vi.waitFor(() => expect(target.querySelectorAll(".weighing select")).toHaveLength(2));
    const added = target.querySelectorAll(".weighing select")[1];
    await vi.waitFor(() => expect(document.activeElement).toBe(added));
    expect(opened.at(-1)).toBe(added);
    delete HTMLSelectElement.prototype.showPicker;
});

it("puts the vessel without tare at the very top of the selection, without heading", async () => {
    await open(12);
    const selection = await vi.waitFor(() => {
        const found = target.querySelector(".weighing select");
        expect(found).toBeTruthy();
        return found;
    });
    const [placeholder, first] = selection.options;
    expect(placeholder.textContent).toContain("Gefäß wählen");
    expect(first.textContent).toContain("Ohne Behälter");
    expect(first.parentElement).toBe(selection);
    expect(
        [...selection.querySelectorAll("optgroup option")].map((o) => o.textContent),
    ).not.toEqual(expect.arrayContaining([expect.stringContaining("Ohne Behälter")]));
});

// ---------------------------------------------------------------------------
// Removing a bowl, a typo while adding, a storage that refuses
// ---------------------------------------------------------------------------

const BOWL = { id: 9, kennung: "k-9", brutto_gramm: "1250.000", behaelter: 3, netto: "1000.000" };

/** The remove button of the weighing row 'index'. */
const weighingButton = (index = 0) => target.querySelectorAll(".weighing button")[index];

it("removes a stored bowl only on moving on, and keeps it on second thought", async () => {
    await db.positions.put(
        position({
            id: 56,
            artikel_id: 12,
            artikel: "Artikel N lose",
            wird_gewogen: true,
            server_id: 56,
            wiegungen: [BOWL],
        }),
    );
    await open(12);
    await vi.waitFor(() => expect(target.querySelector(".weighing input")).toBeTruthy());
    expect(weighingButton().textContent.trim()).toBe("entfernen");

    weighingButton().click();
    await vi.waitFor(() => expect(target.querySelector(".weighing.removed")).toBeTruthy());
    // Struck through, its fields closed, and the button takes it back.
    expect(weighingButton().textContent.trim()).toBe("behalten");
    expect(target.querySelector(".weighing input").disabled).toBe(true);
    expect(target.querySelector(".weighing select").disabled).toBe(true);

    weighingButton().click();
    await vi.waitFor(() => expect(target.querySelector(".weighing.removed")).toBeNull());
    expect(weighingButton().textContent.trim()).toBe("entfernen");

    weighingButton().click();
    await vi.waitFor(() => expect(target.querySelector(".weighing.removed")).toBeTruthy());
    expect(await db.queue.count()).toBe(0);
    confirm();

    await vi.waitFor(async () => {
        const jobs = await db.queue.toArray();
        expect(jobs.map((job) => [job.kind, job.weighing])).toEqual([["weighing_removed", 9]]);
    });
    expect((await db.positions.get(56)).wiegungen).toEqual([]);
});

it("lets a new weighing row go at once on removing", async () => {
    await open(12);
    await vi.waitFor(() => expect(target.querySelectorAll(".weighing")).toHaveLength(1));
    [...target.querySelectorAll("button")]
        .find((b) => b.textContent.includes("Weitere Wiegung"))
        .click();
    await vi.waitFor(() => expect(target.querySelectorAll(".weighing")).toHaveLength(2));

    weighingButton(1).click();

    await vi.waitFor(() => expect(target.querySelectorAll(".weighing")).toHaveLength(1));
    expect(target.querySelector(".weighing.removed")).toBeNull();
    confirm();
    await new Promise((done) => setTimeout(done, 0));
    expect(await db.queue.count()).toBe(0);
});

it("keeps a typo typed while adding, and moving on refuses it", async () => {
    await db.positions.put(countedCrate(127, "12.000"));
    await open(11);
    await vi.waitFor(() => expect(target.querySelector(".count-row input").value).toBe("12"));

    const field = await tapAdd();
    await typeInto(field, "3x");
    // No sum: the draft holds what was typed, so the check sees it.
    expect(target.textContent).toContain("bisher 12 + 3x = 3x");
    await leave(field);
    await vi.waitFor(() => expect(target.querySelector(".count-row input").value).toBe("3x"));

    confirm();
    await vi.waitFor(() => expect(target.textContent).toContain("Bitte nur Zahlen"));
    expect(target.querySelector("header h1").textContent).toBe("Artikel C 0,33");
    expect(await db.queue.count()).toBe(0);
    expect((await db.positions.get(127)).eingabemenge).toBe("12.000");
});

/** The storage refuses, say because it is full: the form says so and stays. */
it("says what went wrong when the storage refuses, and stays in the form", async () => {
    await db.positions.put(CRATE_ROW);
    await open(11);
    await vi.waitFor(() => expect(target.querySelector(".count-row input")).toBeTruthy());
    await typeInto(target.querySelector(".count-row input"), "6");
    const add = vi.spyOn(db.queue, "add").mockRejectedValueOnce(new Error("Speicher voll"));

    confirm();

    await vi.waitFor(() =>
        expect(target.querySelector(".error")?.textContent).toBe("Das ging nicht: Speicher voll"),
    );
    add.mockRestore();
    expect(target.querySelector("header h1").textContent).toBe("Artikel C 0,33");
    expect(target.querySelector("button.next").disabled).toBe(false);
    expect(document.activeElement).toBe(target.querySelector(".count-row input"));
    expect((await db.positions.get(108)).eingabemenge).toBeNull();
    expect(await db.queue.count()).toBe(0);
});

/** The weight of the last weighing is the last field: the enter key goes on like "Done". */
it("goes on from the weight with the enter key, like the button after the last field", async () => {
    await twoArticlesAtLocation();
    await open(12);
    const selection = target.querySelector(".weighing select");
    await vi.waitFor(() => expect(document.activeElement).toBe(selection));
    await chooseContainer(selection, 3);
    const weight = target.querySelector(".weighing input");
    await vi.waitFor(() => expect(document.activeElement).toBe(weight));
    await typeInto(weight, "1200");

    pressEnter(weight);

    await vi.waitFor(() => expect(window.location.hash).toBe(LOCATION_PAGE));
    const jobs = await db.queue.toArray();
    expect(jobs.map((job) => [job.kind, job.payload.brutto_gramm, job.payload.behaelter])).toEqual([
        ["weighing", "1200", 3],
    ]);
});
