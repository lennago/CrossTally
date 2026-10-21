/**
 * The review list in the storage: fetched whole, changed one field at a time.
 *
 * A note typed in a dead spot must stay visible and go out later, also when
 * the list comes anew in between. A difference and "target counts" go the
 * same way. Switching the difference by hand goes straight to the server.
 */

import { beforeEach, expect, it } from "vitest";

import {
    enterDifference,
    enterNote,
    enterTargetCounts,
    fetchReview,
    pendingJobs,
    removeInventory,
    submit,
    switchManualDifference,
} from "./sync.js";
import { db, remember } from "./storage.js";
import { clearStorage, respond } from "../test_support.js";

const INVENTORY = 1;

const row = (hausartikel, extra = {}) => ({
    hausartikel,
    nummer: "101001",
    name: "Artikel C 0,33",
    name_im_haus: "Artikel C 0,33 Flasche",
    einheit: "STUECK",
    ist: "40",
    soll: "30",
    differenz: "10",
    gezaehlt: true,
    aktiv: true,
    notiz: "",
    ...extra,
});

const list = (rows, extra = {}) => ({
    inventur: INVENTORY,
    differenz_von_hand: false,
    hat_sollbestand: true,
    stand: "2026-01-31T10:00:00+01:00",
    zeilen: rows,
    ...extra,
});

beforeEach(async () => {
    await clearStorage();
    await remember("access_token", "a");
    await remember("refresh_token", "r");
});

it("keeps the fetched list for the dead spot", async () => {
    globalThis.fetch = () => respond(list([row(11)]));

    await fetchReview(INVENTORY);

    const stored = await db.reviews.get(INVENTORY);
    expect(stored.zeilen.map((entry) => entry.hausartikel)).toEqual([11]);
    expect(stored.hat_sollbestand).toBe(true);
});

it("shows a note at once and sends it once there is network", async () => {
    await db.reviews.put({ inventory_id: INVENTORY, ...list([row(11)]) });
    globalThis.fetch = () => Promise.reject(new TypeError("Funkloch"));

    await enterNote(INVENTORY, 11, "zwei Kästen hinten");

    expect((await db.reviews.get(INVENTORY)).zeilen[0].notiz).toBe("zwei Kästen hinten");
    expect(await db.queue.count()).toBe(1);

    const sent = [];
    globalThis.fetch = (url, options = {}) => {
        sent.push([url, options.method, JSON.parse(options.body)]);
        return respond({ zeile: row(11, { notiz: "zwei Kästen hinten" }) });
    };
    await submit();

    // With what the device showed before: the server compares ('notiz_vorher').
    expect(sent).toEqual([
        [
            "/api/v1/inventuren/1/pruefliste/11/",
            "PATCH",
            { notiz: "zwei Kästen hinten", notiz_vorher: "" },
        ],
    ]);
    expect(await db.queue.count()).toBe(0);
});

it("lays what still waits over a list that comes anew", async () => {
    await db.reviews.put({ inventory_id: INVENTORY, ...list([row(11)]) });
    globalThis.fetch = () => Promise.reject(new TypeError("Funkloch"));
    await enterNote(INVENTORY, 11, "noch nicht gesendet");

    // The list comes, the note is still in the queue: it must not vanish.
    globalThis.fetch = (url) =>
        url.endsWith("/pruefliste/")
            ? respond(list([row(11)]))
            : Promise.reject(new TypeError("Funkloch"));
    await fetchReview(INVENTORY);

    expect((await db.reviews.get(INVENTORY)).zeilen[0].notiz).toBe("noch nicht gesendet");
});

/** The server's verdict on a deviation belongs to its number, not to one typed here. */
it("drops the server's verdict where a typed difference waits, and keeps it for a note", async () => {
    const manual = { differenz_von_hand: true };
    const rows = [row(11, { differenz: "0", auffaellig: false }), row(12, { auffaellig: true })];
    await db.reviews.put({ inventory_id: INVENTORY, ...list(rows, manual) });
    globalThis.fetch = () => Promise.reject(new TypeError("Funkloch"));
    await enterDifference(INVENTORY, 11, "-4");
    await enterNote(INVENTORY, 12, "Bruch");

    let stored = (await db.reviews.get(INVENTORY)).zeilen;
    expect(stored[0]).not.toHaveProperty("auffaellig");
    expect(stored[1].auffaellig).toBe(true);

    // The list comes anew while both still wait.
    globalThis.fetch = (url) =>
        url.endsWith("/pruefliste/")
            ? respond(list(rows, manual))
            : Promise.reject(new TypeError("Funkloch"));
    await fetchReview(INVENTORY);

    stored = (await db.reviews.get(INVENTORY)).zeilen;
    expect(stored[0]).toMatchObject({ differenz: "-4" });
    expect(stored[0]).not.toHaveProperty("auffaellig");
    expect(stored[1]).toMatchObject({ notiz: "Bruch", auffaellig: true });
});

it("sends only the last state of a field", async () => {
    await db.reviews.put({
        inventory_id: INVENTORY,
        ...list([row(11)], { differenz_von_hand: true }),
    });
    globalThis.fetch = () => Promise.reject(new TypeError("Funkloch"));

    await enterDifference(INVENTORY, 11, "-2");
    await enterDifference(INVENTORY, 11, "-3");
    await enterNote(INVENTORY, 11, "Bruch");

    const jobs = await db.queue.toArray();
    expect(jobs.map((job) => [job.kind, job.payload])).toEqual([
        ["difference", { differenz: "-3", differenz_vorher: "10" }],
        ["note", { notiz: "Bruch", notiz_vorher: "" }],
    ]);
});

it("takes the row as the server answers it, and drops one that left the list", async () => {
    await db.reviews.put({
        inventory_id: INVENTORY,
        ...list([row(11), row(12, { name: "Ware N", gezaehlt: false, notiz: "lag hinten" })]),
    });
    globalThis.fetch = (url) =>
        respond({ zeile: url.includes("/11/") ? row(11, { ist: "60", notiz: "neu" }) : null });

    await enterNote(INVENTORY, 11, "neu");
    await enterNote(INVENTORY, 12, "");

    const rows = (await db.reviews.get(INVENTORY)).zeilen;
    expect(rows.map((entry) => [entry.hausartikel, entry.ist, entry.notiz])).toEqual([
        [11, "60", "neu"],
    ]);
});

it("names the waiting job by its article", async () => {
    await db.reviews.put({ inventory_id: INVENTORY, ...list([row(11)]) });
    globalThis.fetch = () => Promise.reject(new TypeError("Funkloch"));

    await enterNote(INVENTORY, 11, "später");

    const [job] = await pendingJobs();
    expect(job.label).toBe("Artikel C 0,33");
    expect(job.value).toBe("später");
});

it("marks that the target counts, here at once and in the queue like a note", async () => {
    await db.reviews.put({ inventory_id: INVENTORY, ...list([row(11)]) });
    globalThis.fetch = () => Promise.reject(new TypeError("Funkloch"));

    await enterTargetCounts(INVENTORY, 11, true);
    await enterTargetCounts(INVENTORY, 11, false);
    await enterTargetCounts(INVENTORY, 11, true);

    expect((await db.reviews.get(INVENTORY)).zeilen[0].soll_statt_ist).toBe(true);
    const jobs = await db.queue.toArray();
    expect(jobs.map((job) => [job.kind, job.payload])).toEqual([
        ["target", { soll_statt_ist: true }],
    ]);
    const [job] = await pendingJobs();
    expect(job.label).toBe("Artikel C 0,33");
    expect(job.value).toBe("an");

    const sent = [];
    globalThis.fetch = (url, options = {}) => {
        sent.push([url, options.method, JSON.parse(options.body)]);
        return respond({ zeile: row(11, { soll_statt_ist: true }) });
    };
    await submit();

    expect(sent).toEqual([
        ["/api/v1/inventuren/1/pruefliste/11/", "PATCH", { soll_statt_ist: true }],
    ]);
    expect(await db.queue.count()).toBe(0);
});

it("waits while the inventory is back in preparation", async () => {
    await db.reviews.put({ inventory_id: INVENTORY, ...list([row(11)]) });
    globalThis.fetch = () =>
        respond({ konflikt: "status", status: "VORBEREITUNG", vorlaeufig: true, detail: "…" }, 409);

    await enterNote(INVENTORY, 11, "später");

    expect(await db.queue.count()).toBe(1);
});

it("switches the difference by hand and says so everywhere", async () => {
    await db.inventories.put({ id: INVENTORY, haus: "Kino Ost", differenz_von_hand: false });
    await db.reviews.put({
        inventory_id: INVENTORY,
        ...list([row(11)], { hat_sollbestand: false }),
    });
    const sent = [];
    globalThis.fetch = (url, options = {}) => {
        sent.push([url, JSON.parse(options.body)]);
        return respond({ differenz_von_hand: true });
    };

    expect(await switchManualDifference(INVENTORY, true)).toBe(true);

    expect(sent).toEqual([["/api/v1/inventuren/1/differenz-von-hand/", { an: true }]]);
    expect((await db.inventories.get(INVENTORY)).differenz_von_hand).toBe(true);
    expect((await db.reviews.get(INVENTORY)).differenz_von_hand).toBe(true);
});

it("clears the list away with its inventory", async () => {
    await db.reviews.put({ inventory_id: INVENTORY, ...list([row(11)]) });

    await removeInventory(INVENTORY);

    expect(await db.reviews.get(INVENTORY)).toBeUndefined();
});

/**
 * The stored list may lack the row by the time the answer comes, fetched
 * anew while the note waited. The answer's row then joins the list.
 */
it("takes a row from the answer that the stored list does not have", async () => {
    await db.reviews.put({ inventory_id: INVENTORY, ...list([row(11)]) });
    globalThis.fetch = () => respond({ zeile: row(12, { name: "Ware N", notiz: "neu" }) });

    await enterNote(INVENTORY, 12, "neu");

    const rows = (await db.reviews.get(INVENTORY)).zeilen;
    expect(rows.map((entry) => [entry.hausartikel, entry.notiz])).toEqual([
        [11, ""],
        [12, "neu"],
    ]);
});

/**
 * A batch of refused entries, say every difference after the difference by
 * hand was switched off: the server computes the list over every device, and
 * one run of the queue fetches it once for all of them.
 */
it("fetches the review list once for every refused entry of a run", async () => {
    const rows = [11, 12, 13, 14].map((id) => row(id));
    await db.reviews.put({ inventory_id: INVENTORY, ...list(rows, { differenz_von_hand: true }) });
    globalThis.fetch = () => Promise.reject(new TypeError("Funkloch"));
    for (const entry of rows) await enterDifference(INVENTORY, entry.hausartikel, "3", false);

    const asked = [];
    globalThis.fetch = (url, options = {}) => {
        asked.push(`${options.method ?? "GET"} ${url}`);
        if (options.method === "PATCH") {
            return respond({ differenz: ["Die Differenz von Hand ist ausgeschaltet."] }, 400);
        }
        return respond(list(rows.map((entry) => ({ ...entry, differenz: "-1" }))));
    };
    await submit();

    expect(asked.filter((entry) => entry.startsWith("GET"))).toEqual([
        "GET /api/v1/inventuren/1/pruefliste/",
    ]);
    expect(asked.filter((entry) => entry.startsWith("PATCH"))).toHaveLength(4);
    const stored = (await db.reviews.get(INVENTORY)).zeilen;
    expect(stored.map((entry) => entry.differenz)).toEqual(["-1", "-1", "-1", "-1"]);
});

/**
 * An entry that goes through changes the list on the server. A refusal after
 * it in the same run takes the list anew: from the copy of before, the row
 * would lose the note just sent, and the next entry would name an outdated
 * one as seen.
 */
it("fetches the review list anew after an entry of the run went through", async () => {
    await db.reviews.put({
        inventory_id: INVENTORY,
        ...list([row(11), row(12)], { differenz_von_hand: true }),
    });
    globalThis.fetch = () => Promise.reject(new TypeError("Funkloch"));
    await enterDifference(INVENTORY, 11, "3", false);
    await enterNote(INVENTORY, 12, "hinten", false);
    await enterDifference(INVENTORY, 12, "4", false);

    let note = "";
    const seen = [];
    globalThis.fetch = (url, options = {}) => {
        if (options.method === "PATCH") {
            const body = JSON.parse(options.body);
            if (!("notiz" in body)) {
                return respond({ differenz: ["Die Differenz von Hand ist ausgeschaltet."] }, 400);
            }
            note = body.notiz;
            return respond({ zeile: row(12, { notiz: note }) });
        }
        seen.push(note);
        return respond(list([row(11), row(12, { notiz: note })]));
    };
    await submit();

    // Once before the note went out, once after it.
    expect(seen).toEqual(["", "hinten"]);
    expect((await db.reviews.get(INVENTORY)).zeilen[1].notiz).toBe("hinten");
});
