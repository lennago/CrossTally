/**
 * The sync run ('refresh') on the ways the other files do not take.
 *
 * The reason an inventory left the count goes once it is counted again. An
 * inventory closed between list and fetch stays while numbers wait for it. A
 * review list asked for while another one is on its way comes right after
 * it. And a sign-in that only the review list finds expired leads to the
 * login like any other.
 */

import { mount, unmount } from "svelte";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import App from "../App.svelte";
import { close, counting, open, refresh } from "./counting.svelte.js";
import { db, remember, remembered } from "./storage.js";
import { t } from "./language.svelte.js";
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
    anzahl_gezaehlt: 0,
});

const INVENTORIES = [header(1, "Kino Ost"), header(2, "Kino West")];

const row = (id, inventoryId) => ({
    id,
    inventur_id: inventoryId,
    ort: { id: 7, name: "Thekenfront", bereich: "Theke" },
    artikel_id: 11,
    artikel: "Artikel C",
    gebinde: { id, name: `Gebinde ${id}` },
    wird_gewogen: false,
    wiegungen: [],
    eingabemenge: null,
    aktiv: true,
    zuletzt_geaendert: "2026-01-31T10:00:00Z",
});

/** The review list of an inventory, with one article named after it. */
const reviewList = (number) =>
    respond({
        inventur: number,
        differenz_von_hand: false,
        hat_sollbestand: true,
        stand: "2026-01-31T11:00:00+01:00",
        zeilen: [{ hausartikel: number * 10, name: `Ware ${number}`, notiz: "" }],
    });

/**
 * Two running inventories with two rows each.
 *
 * 'listed' are those in the count, 'gone' answer their rows with 404, as
 * between list and fetch. 'review' answers a review list, 'quantity' a
 * quantity and 'renew' the refresh of the token: a case changes them as the
 * website or the network would. 'calls' are the paths asked for.
 */
function mockServer() {
    const rows = { 1: [row(101, 1), row(102, 1)], 2: [row(201, 2), row(202, 2)] };
    const server = {
        listed: [1, 2],
        gone: [],
        calls: [],
        review: reviewList,
        quantity: () => respond({ detail: "?" }, 404),
        renew: () => respond({ detail: "Token is invalid or expired" }, 401),
    };
    globalThis.fetch = (url, options = {}) => {
        const path = url.replace("/api/v1", "").split("?")[0];
        server.calls.push(path);
        if (path === "/token/erneuern/") return server.renew();
        if (path === "/inventuren/") {
            return respond(INVENTORIES.filter(({ id }) => server.listed.includes(id)));
        }
        if (options.method === "PATCH") return server.quantity(path);
        const [, text, rest] =
            path.match(/^\/inventuren\/(\d+)\/(stammdaten\/|pruefliste\/)?$/) ?? [];
        const number = Number(text);
        if (!text || !server.listed.includes(number)) return respond({ detail: "?" }, 404);
        if (rest === "pruefliste/") return server.review(number);
        if (rest === "stammdaten/") {
            return respond({
                orte: [],
                artikel: [{ id: 11, name: "Artikel C", inventurart: 1 }],
                inventurarten: [{ id: 1, name: "Monatsinventur" }],
                behaelter: [],
            });
        }
        if (server.gone.includes(number)) return respond({ detail: "Nicht gefunden." }, 404);
        return respond({ ...header(number), zaehlpositionen: rows[number] });
    };
    return server;
}

/** A gate a fake answer waits at until the case lets it through. */
function gate() {
    let open;
    const passed = new Promise((resolve) => (open = resolve));
    return { passed, open };
}

let target;
let app = null;

beforeEach(async () => {
    await clearStorage();
    await remember("access_token", "a");
    await remember("refresh_token", "r");
    target = document.createElement("div");
    document.body.append(target);
});

afterEach(async () => {
    // A run asked for at the end of a case still finishes before the next one.
    await vi.waitFor(() => expect(counting.loading).toBe(false));
    if (app) unmount(app);
    app = null;
    target.remove();
    window.location.hash = "";
    close();
});

/** On the list, the reason goes once the inventory is counted again. */
it("drops the reason an inventory left the count once it is counted again", async () => {
    const server = mockServer();
    await remember("me", { vorname: "Zoe" });
    window.location.hash = "#/inventory/1";
    app = mount(App, { target });
    await vi.waitFor(() => expect(counting.inventoryId).toBe(1));
    await vi.waitFor(() => expect(counting.loading).toBe(false));
    const gone = t("error.gone");

    server.listed = [2];
    await refresh();
    await vi.waitFor(() => expect(target.textContent).toContain(gone));
    await vi.waitFor(() => expect(counting.loading).toBe(false));
    expect(counting.inventoryId).toBeNull();

    server.listed = [1, 2];
    await refresh();

    await vi.waitFor(() => expect(target.textContent).not.toContain(gone));
    expect(counting.notice).toBeNull();
    await vi.waitFor(() => expect(target.querySelectorAll("button.row")).toHaveLength(2));
});

/** Closed between list and fetch, but a number of it still waits: nothing goes. */
it("keeps an inventory closed between list and fetch while numbers wait for it", async () => {
    const server = mockServer();
    await refresh();
    // The number cannot go out right now: the server is in maintenance.
    server.quantity = () => respond({ detail: "Wartung" }, 503);
    await db.queue.add({ kind: "quantity", position: 201, payload: { eingabemenge: "4" } });
    server.gone = [2];

    await refresh();

    expect(server.calls).toContain("/inventuren/2/");
    expect(await db.inventories.get(2)).toBeDefined();
    expect(await db.positions.where("inventur_id").equals(2).count()).toBe(2);
    expect(await db.queue.count()).toBe(1);
    expect(counting.message).toBe("");
});

/**
 * Another inventory opens while the review list of the first is on its way,
 * and its page asks for its own: that one comes with the next run, right
 * after this one. The answer for the first does not take the wish away.
 */
it("fetches a review list asked for while another one was on its way", async () => {
    const server = mockServer();
    await open(1);
    const held = gate();
    let asked = false;
    server.review = async (number) => {
        if (number === 1) {
            asked = true;
            await held.passed;
        }
        return await reviewList(number);
    };

    const first = refresh({ review: true });
    await vi.waitFor(() => expect(asked).toBe(true));
    await open(2);
    await refresh({ review: true });
    held.open();
    await first;

    await vi.waitFor(async () => expect(await db.reviews.get(2)).toBeTruthy());
    await vi.waitFor(() => expect(counting.loading).toBe(false));
    expect((await db.reviews.get(1)).zeilen[0].name).toBe("Ware 1");
    expect(server.calls.filter((path) => path === "/inventuren/2/pruefliste/")).toHaveLength(1);
});

/**
 * The counts went out and the rows came, but the review list finds the
 * sign-in expired, the refresh too. That is no fault of the sync: the app
 * leads to the login, says nothing, and "last synced" stays where it was.
 */
it("leads to the login when only the review list finds the sign-in expired", async () => {
    const server = mockServer();
    await open(1);
    const before = "2026-01-31T09:00:00.000Z";
    await remember("last_synced", before);
    server.review = () => respond({ detail: "Token is invalid or expired" }, 401);
    server.calls = [];

    await refresh({ review: true });
    await new Promise((done) => setTimeout(done, 200));

    expect(counting.signedOut).toBe(true);
    expect(counting.message).toBe("");
    expect(await remembered("last_synced")).toBe(before);
    expect(server.calls.filter((path) => path === "/inventuren/1/pruefliste/")).toHaveLength(1);
    // Nothing counted is lost: the rows of both inventories stay.
    expect(await db.positions.count()).toBe(4);
    counting.signedOut = false;
});
