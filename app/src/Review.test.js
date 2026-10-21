/**
 * The review list on the phone, against a filled storage and without network.
 *
 * Per article the difference stands large, the note only on request: a pen
 * opens it, an × says that there is one. With the difference by hand the
 * difference is a field. Whether it is typed by hand, only a manager switches.
 * "Target counts" likewise: a button for managers where there is a target.
 * A few cases put a server behind it that notes what it is asked for.
 */

import { mount, tick, unmount } from "svelte";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import App from "./App.svelte";
import { counting, loadFromStorage, refresh } from "./services/counting.svelte.js";
import { db, remembered } from "./services/storage.js";
import { go, route, up } from "./services/route.svelte.js";
import { apply } from "./services/language.svelte.js";
import { clearStorage, findButton, respond } from "./test_support.js";

const INVENTORY = 1;

const row = (hausartikel, extra = {}) => ({
    hausartikel,
    nummer: `10100${hausartikel}`,
    name: `Artikel ${hausartikel}`,
    name_im_haus: "",
    einheit: "STUECK",
    ist: "40",
    soll: "30",
    differenz: "10",
    gezaehlt: true,
    aktiv: true,
    notiz: "",
    ...extra,
});

let target;
let app;

/** 'ready' is the text the page shows once it stands: the list, or the login form. */
async function start({
    manager = false,
    manual = false,
    locked = false,
    extra = [],
    ready = "Ware N",
} = {}) {
    await db.state.bulkPut([
        { key: "access_token", value: "a" },
        { key: "refresh_token", value: "abc" },
        { key: "me", value: { vorname: "Zoe", ist_fuehrungskraft: manager } },
    ]);
    await db.inventories.put({
        id: INVENTORY,
        haus: "Kino Ost",
        stichtag: "2026-01-31",
        differenz_von_hand: manual,
        hat_sollbestand: !manual,
        gesperrt: locked,
    });
    await db.masterData.put({ inventory_id: INVENTORY, orte: [], artikel: [], behaelter: [] });
    await db.reviews.put({
        inventory_id: INVENTORY,
        inventur: INVENTORY,
        differenz_von_hand: manual,
        hat_sollbestand: !manual,
        stand: "2026-01-31T10:00:00+01:00",
        zeilen: [
            row(1, { name: "Ware N", differenz: manual ? null : "-3", notiz: "lag hinten" }),
            row(2, {
                name: "Artikel C",
                differenz: manual ? null : "0",
                soll: manual ? null : "40",
            }),
            row(3, { name: "Ware Z", gezaehlt: false, ist: "0", differenz: manual ? null : "-5" }),
            ...extra,
        ],
    });
    go("inventory", INVENTORY, "review");
    app = mount(App, { target });
    await shows(ready);
}

async function shows(text) {
    await vi.waitFor(() => expect(target.textContent).toContain(text), { timeout: 3000 });
}

function rowOf(name) {
    return [...target.querySelectorAll("li.row")].find((entry) =>
        entry.querySelector(".name").textContent.includes(name),
    );
}

beforeEach(async () => {
    apply("de");
    await clearStorage();
    target = document.createElement("div");
    document.body.append(target);
});

afterEach(() => {
    if (app) unmount(app);
    app = null;
    target.remove();
    window.location.hash = "";
    vi.restoreAllMocks();
});

it("shows the difference large and the note only on request", async () => {
    await start();

    expect(rowOf("Ware N").querySelector(".difference").textContent.trim()).toBe("-3");
    expect(rowOf("Artikel C").querySelector(".difference").textContent.trim()).toBe("0");
    expect(target.textContent).not.toContain("lag hinten");
    // The product group is not shown: the API does not even send it.
    expect(target.textContent).toContain("3 Artikel · 2 mit Abweichung · 1 ungezählt");

    // The × only where there is a note.
    expect(rowOf("Ware N").querySelectorAll("button.icon")).toHaveLength(2);
    expect(rowOf("Artikel C").querySelectorAll("button.icon")).toHaveLength(1);

    rowOf("Ware N").querySelector("button.icon").click();
    await tick();
    expect(rowOf("Ware N").querySelector("textarea").value).toBe("lag hinten");
});

/** The list lies on the device: one from another day says which, one from today only when. */
it("names the day of a review list from another day, and only the time of one from today", async () => {
    await start();
    const asOf = () => target.querySelector(".summary .as-of").textContent;
    expect(asOf()).toContain("31.01.");

    await db.reviews.update(INVENTORY, { stand: new Date().toISOString() });
    await loadFromStorage();

    await vi.waitFor(() => expect(asOf()).not.toMatch(/\d\d\.\d\d\./));
    expect(asOf()).toMatch(/\d\d:\d\d/);
});

it("lists by article number, own articles without one last", async () => {
    await start({
        extra: [row(4, { name: "Ware A", nummer: "" }), row(5, { name: "Ware B", nummer: "99" })],
    });

    const names = [...target.querySelectorAll("li.row .name strong")].map(
        (name) => name.textContent,
    );
    expect(names).toEqual(["Ware B", "Ware N", "Artikel C", "Ware Z", "Ware A"]);
});

it("keeps a note typed with the pen, here at once", async () => {
    await start();

    rowOf("Artikel C").querySelector("button.icon").click();
    await tick();
    const field = rowOf("Artikel C").querySelector("textarea");
    field.value = "zwei Kästen hinten";
    field.dispatchEvent(new Event("input", { bubbles: true }));
    findButton(rowOf("Artikel C"), "Übernehmen").click();

    await vi.waitFor(async () => {
        const [job] = await db.queue.toArray();
        expect(job).toMatchObject({
            kind: "note",
            article: 2,
            payload: { notiz: "zwei Kästen hinten" },
        });
    });
    // Now there is one: the × appears beside the pen.
    await vi.waitFor(() =>
        expect(rowOf("Artikel C").querySelectorAll("button.icon")).toHaveLength(2),
    );
});

it("stops a note at the length the server keeps", async () => {
    await start();

    rowOf("Artikel C").querySelector("button.icon").click();
    await tick();

    // The server refuses more ('NOTIZ_HOECHSTLAENGE'). The field says it before.
    expect(rowOf("Artikel C").querySelector("textarea").maxLength).toBe(2000);
});

it("removes a note with the × after asking", async () => {
    await start();
    vi.spyOn(window, "confirm").mockReturnValue(true);

    rowOf("Ware N").querySelectorAll("button.icon")[1].click();

    await vi.waitFor(async () => {
        const [job] = await db.queue.toArray();
        expect(job).toMatchObject({ kind: "note", article: 1, payload: { notiz: "" } });
    });
});

it("filters the deviations and the uncounted", async () => {
    await start();

    findButton(target, "Abweichung").click();
    await vi.waitFor(() => expect(rowOf("Artikel C")).toBeUndefined());
    expect(rowOf("Ware N")).toBeTruthy();

    findButton(target, "Ungezählt").click();
    await vi.waitFor(() => expect(rowOf("Ware N")).toBeUndefined());
    expect(rowOf("Ware Z")).toBeTruthy();
});

/**
 * Whether a row deviates is the server's verdict, on the difference rounded
 * like the unit. A difference by hand of 0.4 pieces is none, as on the
 * website. A row from an older server and one typed here decide by the number.
 */
it("counts a deviation as the server rounds it", async () => {
    await start({
        manual: true,
        extra: [
            row(4, { name: "Ware R", differenz: "0.4", auffaellig: false }),
            row(5, { name: "Ware S", differenz: "-3", auffaellig: true }),
            row(6, { name: "Ware T", differenz: "1" }),
        ],
    });
    expect(target.textContent).toContain("6 Artikel · 2 mit Abweichung");

    findButton(target, "Abweichung").click();
    await vi.waitFor(() => expect(rowOf("Ware R")).toBeUndefined());
    expect(rowOf("Ware S")).toBeTruthy();
    expect(rowOf("Ware T")).toBeTruthy();
    findButton(target, "Alle").click();
    await vi.waitFor(() => expect(rowOf("Ware R")).toBeTruthy());

    // Typed here and not sent yet: the verdict went with the old number.
    const field = rowOf("Ware R").querySelector(".difference input");
    field.value = "-5";
    field.dispatchEvent(new Event("change", { bubbles: true }));
    await shows("6 Artikel · 3 mit Abweichung");
});

it("takes the difference by hand in a field, with a comma", async () => {
    await start({ manual: true });

    const field = rowOf("Artikel C").querySelector(".difference input");
    expect(field).toBeTruthy();
    // Without a target its column goes.
    expect(rowOf("Artikel C").textContent).not.toContain("Soll");
    field.value = "-3,5";
    field.dispatchEvent(new Event("change", { bubbles: true }));

    await vi.waitFor(async () => {
        const [job] = await db.queue.toArray();
        expect(job).toMatchObject({
            kind: "difference",
            article: 2,
            payload: { differenz: "-3.5" },
        });
    });
});

/** Typed, but the field never left: no 'change' comes. */
async function typeOnly(name, value) {
    const field = rowOf(name).querySelector(".difference input");
    field.value = value;
    field.dispatchEvent(new Event("input", { bubbles: true }));
    return field;
}

async function queuedDifference() {
    const [job] = await db.queue.toArray();
    return job?.payload?.differenz;
}

it("takes over a typed difference when the app goes to the background", async () => {
    await start({ manual: true });
    await typeOnly("Artikel C", "-4");
    expect(await db.queue.count()).toBe(0);

    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
    try {
        document.dispatchEvent(new Event("visibilitychange"));
        await vi.waitFor(async () => expect(await queuedDifference()).toBe("-4"));
    } finally {
        delete document.visibilityState;
    }
});

it("takes over a typed difference when the review list is left", async () => {
    await start({ manual: true });
    await typeOnly("Artikel C", "-6");

    up();

    await vi.waitFor(async () => expect(await queuedDifference()).toBe("-6"));
});

it("turns the sign with ±, the number pad of a phone has no minus", async () => {
    await start({ manual: true });
    const field = await typeOnly("Artikel C", "12");

    findButton(rowOf("Artikel C"), "±").click();

    expect(field.value).toBe("-12");
    await vi.waitFor(async () => expect(await queuedDifference()).toBe("-12"));
    findButton(rowOf("Artikel C"), "±").click();
    await vi.waitFor(async () => expect(await queuedDifference()).toBe("12"));
});

it("a minus before the number waits for it", async () => {
    await start({ manual: true });
    const field = rowOf("Artikel C").querySelector(".difference input");

    findButton(rowOf("Artikel C"), "±").click();

    expect(field.value).toBe("-");
    expect(await db.queue.count()).toBe(0);
    expect(target.textContent).not.toContain("Bitte nur Zahlen eintragen.");
    field.value = "-5";
    field.dispatchEvent(new Event("change", { bubbles: true }));
    await vi.waitFor(async () => expect(await queuedDifference()).toBe("-5"));
});

it("says when a difference is no number and keeps it out", async () => {
    await start({ manual: true });

    const field = rowOf("Artikel C").querySelector(".difference input");
    field.value = "drei";
    field.dispatchEvent(new Event("change", { bubbles: true }));

    await shows("Bitte nur Zahlen eintragen.");
    expect(field.getAttribute("aria-invalid")).toBe("true");
    expect(await db.queue.count()).toBe(0);
});

/** What the server would refuse stays out, as in the entry form, and nothing goes out. */
it("keeps a difference out that the server would refuse", async () => {
    await start({ manual: true });
    const field = rowOf("Artikel C").querySelector(".difference input");

    for (const [value, says] of [
        ["-1,2345", "Höchstens drei Nachkommastellen."],
        ["1000000000", "Das ist mehr, als ein Ergebnis fasst."],
    ]) {
        field.value = value;
        field.dispatchEvent(new Event("change", { bubbles: true }));
        await shows(says);
        expect(field.getAttribute("aria-invalid")).toBe("true");
    }
    expect(await db.queue.count()).toBe(0);
});

it("shows the switch to managers only, and only where it can switch", async () => {
    await start({ manual: true });
    expect(target.querySelector(".manual input[type=checkbox]")).toBeNull();
    unmount(app);
    app = null;

    // A target stock came in: the difference computes itself, nothing to switch.
    await clearStorage();
    await start({ manager: true });
    expect(target.querySelector(".manual input[type=checkbox]")).toBeNull();
    unmount(app);
    app = null;

    await clearStorage();
    await start({ manager: true, manual: true });
    const box = target.querySelector(".manual input[type=checkbox]");
    expect(box.checked).toBe(true);
    expect(box.disabled).toBe(false);
});

// -- "Target counts": at closing the target becomes the result ------------------

it("offers managers 'Soll zählt' where a target stands, and queues the switch", async () => {
    await start({
        manager: true,
        extra: [row(4, { name: "Ware O", soll: null, differenz: null })],
    });

    expect(findButton(rowOf("Ware O"), "Soll zählt")).toBeUndefined();
    const button = findButton(rowOf("Ware N"), "Soll zählt");
    expect(button.getAttribute("aria-pressed")).toBe("false");
    expect(button.getAttribute("aria-label")).toBe("Soll zählt bei Ware N");

    button.click();

    await vi.waitFor(async () => {
        const [job] = await db.queue.toArray();
        expect(job).toMatchObject({
            kind: "target",
            article: 1,
            payload: { soll_statt_ist: true },
        });
    });
    await vi.waitFor(() =>
        expect(findButton(rowOf("Ware N"), "Soll zählt").getAttribute("aria-pressed")).toBe("true"),
    );

    findButton(rowOf("Ware N"), "Soll zählt").click();

    // Only the last state of the field waits.
    await vi.waitFor(async () => {
        const jobs = await db.queue.toArray();
        expect(jobs.map((job) => job.payload)).toEqual([{ soll_statt_ist: false }]);
    });
});

it("shows counters that the target counts, without a button", async () => {
    await start({ extra: [row(4, { name: "Ware O", soll_statt_ist: true })] });

    expect(findButton(target, "Soll zählt")).toBeUndefined();
    // Explained above the list, where a phone shows it (until 10/2026 a tooltip).
    expect(target.textContent).toContain(
        "Wo „Soll zählt“ an ist, gilt beim Abschluss der Sollbestand als Ergebnis.",
    );
    expect(rowOf("Ware O").querySelector(".meta").textContent).toContain("Soll zählt");
    expect(rowOf("Ware N").querySelector(".meta").textContent).not.toContain("Soll zählt");
});

it("has no 'Soll zählt' with the difference by hand", async () => {
    await start({ manager: true, manual: true });

    expect(findButton(target, "Soll zählt")).toBeUndefined();
    expect(target.textContent).not.toContain("gilt beim Abschluss der Sollbestand");
});

it("explains 'Soll zählt' to managers as soon as a target stands, not to counters without a row", async () => {
    await start({ manager: true });
    expect(target.textContent).toContain(
        "Wo „Soll zählt“ an ist, gilt beim Abschluss der Sollbestand als Ergebnis.",
    );
    expect(findButton(rowOf("Ware N"), "Soll zählt").getAttribute("title")).toBeNull();
    unmount(app);
    app = null;

    await clearStorage();
    await start();
    expect(target.textContent).not.toContain("gilt beim Abschluss der Sollbestand");
});

it("explains the sign of the difference visibly where it is typed", async () => {
    await start({ manual: true });
    expect(target.textContent).toContain("Ein Minus heißt: Es fehlt etwas.");
    expect(target.querySelector("input[inputmode=decimal]").getAttribute("title")).toBeNull();
    unmount(app);
    app = null;

    await clearStorage();
    await start({ manager: true, manual: true });
    expect(target.textContent).toContain("Ein Minus heißt: Es fehlt etwas.");
});

it("leads back to the areas of the inventory", async () => {
    await start();

    up();

    expect(route.parts).toEqual(["inventory", String(INVENTORY)]);
});

// -- The lock: counting stopped, the check goes on -------------------------

it("says that the count is locked, and the notes go on", async () => {
    await start({ locked: true });

    await shows("Gesperrt: Gezählt wird gerade nicht.");
    // Counters see the lock, switching is for managers.
    expect(findButton(target, "Öffnen")).toBeUndefined();
    rowOf("Artikel C").querySelector("button.icon").click();
    await tick();
    expect(rowOf("Artikel C").querySelector("textarea")).toBeTruthy();
});

it("lets a manager open the lock and lock it again", async () => {
    await start({ manager: true, locked: true });
    const sent = [];
    // Only the switch reaches a server. Fetching the list stays without network.
    globalThis.fetch = vi.fn((url, options = {}) => {
        if (options.method !== "POST") return Promise.reject(new TypeError("kein Netz"));
        sent.push(`${options.method} ${url} ${options.body}`);
        return respond({ gesperrt: JSON.parse(options.body).an });
    });

    findButton(target, "Öffnen").click();

    await vi.waitFor(() => expect(findButton(target, "Sperren")).toBeTruthy());
    expect(target.textContent).not.toContain("Gesperrt:");
    expect((await db.inventories.get(INVENTORY)).gesperrt).toBe(false);
    // Opened here, the review list stays: it does not jump to the areas.
    expect(route.parts).toEqual(["inventory", String(INVENTORY), "review"]);

    findButton(target, "Sperren").click();
    await shows("Gesperrt: Gezählt wird gerade nicht.");
    expect(sent).toEqual([
        'POST /api/v1/inventuren/1/sperre/ {"an":false}',
        'POST /api/v1/inventuren/1/sperre/ {"an":true}',
    ]);
});

/** A server error has no 'detail'. The sentence then says so, not "{reason}" (10/2026). */
it("names the reason when the server fails while switching the lock", async () => {
    await start({ manager: true, locked: true });
    globalThis.fetch = vi.fn(() =>
        Promise.resolve({
            ok: false,
            status: 500,
            text: () => Promise.resolve("<html>Fehler</html>"),
        }),
    );

    findButton(target, "Öffnen").click();

    await shows("Das ging nicht: Der Server hat nicht geantwortet.");
    expect(target.textContent).not.toContain("{reason}");
});

/** An inventory as the list and its detail name it, locked for the check. */
const header = (id) => ({
    id,
    haus: `Kino ${id}`,
    stichtag: "2026-01-31",
    inventurarten: [{ id: 1, name: "Monatsinventur" }],
    arten_anzeige: "Monatsinventur",
    status: "ZAEHLUNG",
    status_anzeige: "Zählung",
    zaehlbar: true,
    gesperrt: true,
    hat_sollbestand: true,
    differenz_von_hand: false,
});

/**
 * From the review list of one inventory to that of the next: the list has
 * to come. Until 10/2026 the wish went with the run that started on the
 * previous inventory, and the next list stayed empty until "Refresh".
 */
it("fetches the review list of the next inventory opened here", async () => {
    await start({ locked: true });
    const calls = [];
    globalThis.fetch = vi.fn((url) => {
        const path = String(url).replace("/api/v1", "").split("?")[0];
        calls.push(path);
        if (path === "/inventuren/") return respond([header(1), header(2)]);
        const [, number, rest] =
            path.match(/^\/inventuren\/(\d+)\/(stammdaten\/|pruefliste\/)?$/) ?? [];
        if (!number) return respond({ detail: "?" }, 404);
        if (rest === "stammdaten/") {
            return respond({
                orte: [],
                artikel: [],
                inventurarten: [{ id: 1, name: "Monatsinventur" }],
                behaelter: [],
            });
        }
        if (rest === "pruefliste/") {
            return respond({
                inventur: Number(number),
                differenz_von_hand: false,
                hat_sollbestand: true,
                stand: "2026-01-31T10:00:00+01:00",
                zeilen: [row(Number(number) * 10, { name: `Ware ${number}` })],
            });
        }
        return respond({ ...header(Number(number)), zaehlpositionen: [] });
    });

    go("inventory", 2, "review");

    await shows("Ware 2");
    expect(calls).toContain("/inventuren/2/pruefliste/");
});

/** A fake 'fetch' that answers through 'answer'. Back come the paths it was asked for. */
function noting(answer) {
    const calls = [];
    globalThis.fetch = vi.fn((url) => {
        const path = String(url).replace("/api/v1", "").split("?")[0];
        calls.push(path);
        return answer(path);
    });
    return calls;
}

/** Inventory 1 on the server. 'review' answers its review list, so a case can let it fail. */
function serving(review) {
    return noting((path) => {
        if (path === "/ich/") return respond({ id: 1, vorname: "Zoe" });
        if (path === "/inventuren/") return respond([header(INVENTORY)]);
        if (path === `/inventuren/${INVENTORY}/stammdaten/`) {
            return respond({
                orte: [],
                artikel: [],
                inventurarten: [{ id: 1, name: "Monatsinventur" }],
                behaelter: [],
            });
        }
        if (path === `/inventuren/${INVENTORY}/pruefliste/`) return review();
        return respond({ ...header(INVENTORY), zaehlpositionen: [] });
    });
}

/** The review list as the server sends it, with one article of this name. */
const reviewList = (name) =>
    respond({
        inventur: INVENTORY,
        differenz_von_hand: false,
        hat_sollbestand: true,
        stand: "2026-01-31T11:00:00+01:00",
        zeilen: [row(9, { name })],
    });

const serverError = () =>
    Promise.resolve({ ok: false, status: 500, text: () => Promise.resolve("<html>Fehler</html>") });

const asked = (calls, path) => calls.filter((entry) => entry === path).length;

const pause = (ms) => new Promise((done) => setTimeout(done, ms));

/** Until no sync runs any more and nothing follows by itself. */
async function settled() {
    await vi.waitFor(() => expect(counting.loading).toBe(false));
    await pause(300);
}

/**
 * Without network the review list waits for the next sync and is not asked
 * for again at once. Until 10/2026 a failed one was asked for as fast as the
 * network refused, on every page of the inventory, and it never stopped.
 */
it("asks without network once per sync, and the list comes with the network", async () => {
    const calls = noting(() => Promise.reject(new TypeError("kein Netz")));
    await start();
    await settled();
    const before = calls.length;
    await pause(300);
    expect(calls.length).toBe(before);

    // The clock: one round more, and nothing after it.
    const lists = asked(calls, "/inventuren/");
    await refresh();
    await settled();
    expect(asked(calls, "/inventuren/")).toBe(lists + 1);

    // Back online the wish is still there.
    const online = serving(() => reviewList("Ware Q"));
    window.dispatchEvent(new Event("online"));
    await shows("Ware Q");
    expect(asked(online, `/inventuren/${INVENTORY}/pruefliste/`)).toBe(1);
});

it("asks for a review list the server fails on once per sync, not again at once", async () => {
    let failing = true;
    const calls = serving(() => (failing ? serverError() : reviewList("Ware Q")));
    const review = `/inventuren/${INVENTORY}/pruefliste/`;
    await start();
    await settled();
    expect(asked(calls, review)).toBe(1);

    await refresh();
    await settled();
    expect(asked(calls, review)).toBe(2);

    failing = false;
    await refresh();
    await shows("Ware Q");
    await settled();
    expect(asked(calls, review)).toBe(3);
});

/**
 * The counts went out and the rows came: the sync stands, whatever the list
 * does. Until 10/2026 a list the server kept failing on made every sync
 * count as failed, the icon said "offline", and "last synced" stood still.
 */
it("keeps the sync good when only the review list fails", async () => {
    const calls = serving(serverError);
    await start();
    await settled();

    expect(counting.online).toBe(true);
    expect(counting.message).toBe("");
    expect(await remembered("last_synced")).toBeTruthy();
    // The list waits for the next round, and none follows at once.
    expect(asked(calls, `/inventuren/${INVENTORY}/pruefliste/`)).toBe(1);
});

it("asks nothing more once the sign-in expired on the review list", async () => {
    const calls = noting(() => respond({ detail: "Token is invalid or expired" }, 401));
    await start({ ready: "Anmelden" });
    await settled();
    const before = calls.length;
    await pause(300);

    expect(calls.length).toBe(before);
    expect(asked(calls, "/inventuren/")).toBeLessThanOrEqual(2);
});

it("keeps the lock without network and says why", async () => {
    await start({ manager: true, locked: true });
    globalThis.fetch = vi.fn(() => Promise.reject(new TypeError("kein Netz")));

    findButton(target, "Öffnen").click();

    await shows("Umschalten geht nur mit Verbindung.");
    expect((await db.inventories.get(INVENTORY)).gesperrt).toBe(true);
    expect(findButton(target, "Öffnen")).toBeTruthy();
});
