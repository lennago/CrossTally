/**
 * Two people count the same row, once with network, once without.
 *
 * Normally that does not happen, and precisely therefore it must not happen
 * silently: whoever wrote last won, and the first number was gone. Now the
 * app sends along what the person had in front of them when opening the
 * form. If the server deviates from that, it asks back, and the second
 * person decides: add, discard the own number or overwrite.
 *
 * The device here is the second person. The first counts directly on the
 * mock server ('server.counts'), which has the same rules as the real one
 * (see 'api/tests.py', 'Mengenkonflikte'). Input goes through the draft,
 * exactly as in the form.
 */

import { beforeEach, expect, it } from "vitest";

import { draftFrom, addPack, applyDraft } from "./draft.svelte.js";
import { db, remembered, remember } from "./storage.js";
import {
    rejectedJobs,
    quantityConflicts,
    resolveQuantityConflict,
    submit,
    fetchPositions,
} from "./sync.js";

const LOCATION = { id: 7, name: "Thekenfront", bereich: "Theke" };
const CRATE = { id: 2, name: "Kasten", faktor_in_basiseinheit: "20.000" };
const BOTTLE = { id: 3, name: "Flasche", faktor_in_basiseinheit: "1.000" };
const ARTICLE = {
    id: 11,
    name: "Artikel C",
    einheit: "Stück(e)",
    wird_gewogen: false,
    inventurart: 1,
    gebinde: [CRATE, BOTTLE],
};

/** Quantities as the server writes them: with all decimal places. */
const asServerNumber = (value) => (value == null ? null : Number(value).toFixed(3));
const equal = (links, rechts) =>
    links == null || rechts == null
        ? links == null && rechts == null
        : Number(links) === Number(rechts);

function mockServer() {
    const rows = new Map();
    let nextId = 55;
    let clock = Date.parse("2026-01-31T10:00:00Z");

    const server = {
        rows,
        network: true,
        //: The change arrives, the response does not: a dead spot at the wrong moment.
        loseResponse: false,
        patches: [],

        create(pack, eingabemenge = null) {
            const row = {
                id: nextId++,
                inventur_id: 1,
                ort: LOCATION,
                artikel_id: ARTICLE.id,
                artikel: ARTICLE.name,
                gebinde: pack,
                wird_gewogen: false,
                wiegungen: [],
                eingabemenge: asServerNumber(eingabemenge),
                aktiv: true,
                zuletzt_geaendert: new Date((clock += 1000)).toISOString(),
            };
            rows.set(row.id, row);
            return row;
        },

        /** The other person counts, on their own device. */
        counts(id, quantity) {
            const row = rows.get(id);
            row.eingabemenge = asServerNumber(quantity);
            row.zuletzt_geaendert = new Date((clock += 1000)).toISOString();
        },

        quantity(id) {
            return rows.get(id).eingabemenge;
        },
    };

    const respond = (data, status = 200) =>
        Promise.resolve({
            ok: status < 400,
            status,
            text: () => Promise.resolve(JSON.stringify(data)),
        });

    globalThis.fetch = async (url, options = {}) => {
        if (!server.network) throw new TypeError("kein Netz");
        const method = options.method ?? "GET";
        const body = options.body ? JSON.parse(options.body) : null;
        const path = url.replace("/api/v1", "").split("?")[0];

        if (path === "/inventuren/1/") {
            return respond({ id: 1, status: "ZAEHLUNG", zaehlpositionen: [...rows.values()] });
        }
        if (path === "/zaehlpositionen/" && method === "POST") {
            const existing = [...rows.values()].find(
                (row) =>
                    row.ort.id === body.ort &&
                    row.artikel_id === body.artikel &&
                    (row.gebinde?.id ?? null) === body.gebinde,
            );
            if (existing) {
                return respond(
                    {
                        vorhanden_id: [String(existing.id)],
                        detail: ["Diese Zeile gibt es hier schon."],
                    },
                    400,
                );
            }
            const pack = ARTICLE.gebinde.find((entry) => entry.id === body.gebinde) ?? null;
            return respond(server.create(pack), 201);
        }
        const number = Number(path.match(/^\/zaehlpositionen\/(\d+)\/$/)?.[1]);
        const row = rows.get(number);
        if (row && method === "GET") return respond(row);
        if (row && method === "PATCH") {
            server.patches.push(body);
            if (
                "eingabemenge_vorher" in body &&
                !equal(row.eingabemenge, body.eingabemenge_vorher) &&
                !equal(row.eingabemenge, body.eingabemenge)
            ) {
                return respond({ conflict: "eingabemenge", aktuell: row }, 409);
            }
            server.counts(number, body.eingabemenge);
            if (server.loseResponse) {
                server.loseResponse = false;
                throw new TypeError("Verbindung abgerissen");
            }
            return respond(row);
        }
        return respond({ detail: "?" }, 404);
    };
    return server;
}

/** The rows of the article as the form finds them when opening. */
async function openForm() {
    const rows = await db.positions.where("inventur_id").equals(1).toArray();
    return draftFrom(
        ARTICLE,
        rows.filter((row) => row.artikel_id === ARTICLE.id),
    );
}

async function enter(draft, packId, quantity) {
    draft.packs.find((entry) => entry.pack.id === packId).quantity = quantity;
    await applyDraft(draft, { inventoryId: 1, location: LOCATION, article: ARTICLE });
}

async function here(packId) {
    const rows = await db.positions.where("inventur_id").equals(1).toArray();
    return rows.filter((row) => row.gebinde?.id === packId);
}

let server;
let crate;

beforeEach(async () => {
    await Promise.all([
        db.state.clear(),
        db.positions.clear(),
        db.queue.clear(),
        db.inventories.clear(),
    ]);
    await remember("access_token", "a");
    await remember("refresh_token", "r");
    server = mockServer();
    // Expected: the crate. It has been in the list since the inventory was created.
    crate = server.create(CRATE);
    await fetchPositions(1);
});

it("asks back when someone else counted online while the form was empty", async () => {
    const draft = await openForm();
    server.counts(crate.id, "5");

    await enter(draft, CRATE.id, "3");

    expect(server.quantity(crate.id)).toBe("5.000");
    const [conflict] = await quantityConflicts();
    expect(conflict).toMatchObject({ position: crate.id, mine: "3", theirs: "5.000" });
    expect(conflict.label).toBe("Artikel C · Kasten · Thekenfront");
    // Here stands what really applies until decided.
    expect((await here(CRATE.id))[0].eingabemenge).toBe("5.000");
    expect(await db.queue.count()).toBe(0);
    expect(await rejectedJobs()).toEqual([]);

    await resolveQuantityConflict(conflict.id, "add");

    expect(server.quantity(crate.id)).toBe("8.000");
    expect(server.patches.at(-1)).toEqual({ eingabemenge: "8", eingabemenge_vorher: "5.000" });
    expect((await here(CRATE.id))[0].eingabemenge).toBe("8.000");
    expect(await quantityConflicts()).toEqual([]);
});

it("overwrites without question when the foreign number already stood there on opening", async () => {
    server.counts(crate.id, "5");
    await fetchPositions(1);

    const draft = await openForm();
    expect(draft.packs[0].quantity).toBe("5");
    await enter(draft, CRATE.id, "3");

    expect(server.quantity(crate.id)).toBe("3.000");
    expect(await quantityConflicts()).toEqual([]);
});

it("asks back too when the foreign number only arrived while the form was already open", async () => {
    const draft = await openForm();
    server.counts(crate.id, "5");
    // The background sync fetches the 5. The form keeps showing what it saw when opening.
    await fetchPositions(1);

    await enter(draft, CRATE.id, "3");

    expect(server.quantity(crate.id)).toBe("5.000");
    expect(await quantityConflicts()).toHaveLength(1);
});

it("asks back when both created the same unexpected row offline", async () => {
    server.network = false;
    const draft = await openForm();
    addPack(draft, BOTTLE);
    await enter(draft, BOTTLE.id, "3");
    // The other person was faster on the network.
    const bottle = server.create(BOTTLE, "5");

    server.network = true;
    await submit();

    const [conflict] = await quantityConflicts();
    expect(conflict).toMatchObject({ position: bottle.id, mine: "3", theirs: "5.000" });
    // The own row was absorbed into the server's, not left next to it.
    expect((await here(BOTTLE.id)).map((row) => row.id)).toEqual([bottle.id]);
    expect(await rejectedJobs()).toEqual([]);

    await resolveQuantityConflict(conflict.id, "discard");

    expect(server.quantity(bottle.id)).toBe("5.000");
    expect(server.patches).toHaveLength(1);
    expect(await quantityConflicts()).toEqual([]);
    expect(await db.queue.count()).toBe(0);
});

it("merges several own numbers of the same row from a dead spot into one question", async () => {
    server.network = false;
    await enter(await openForm(), CRATE.id, "3");
    // Opened once more: now the own 3 stands there, and it becomes a 4.
    await enter(await openForm(), CRATE.id, "4");
    server.counts(crate.id, "5");

    server.network = true;
    await submit();

    const conflicts = await quantityConflicts();
    expect(conflicts).toHaveLength(1);
    expect(conflicts[0]).toMatchObject({ mine: "4", theirs: "5.000" });
    // The second own number did not run against the foreign one once more.
    expect(server.patches).toHaveLength(1);

    await resolveQuantityConflict(conflicts[0].id, "overwrite");
    expect(server.quantity(crate.id)).toBe("4.000");
});

it("does not take its own doubly arrived send for a conflict", async () => {
    server.loseResponse = true;
    await enter(await openForm(), CRATE.id, "3");
    // It arrived, the app just does not know it and sends once more next time.
    expect(server.quantity(crate.id)).toBe("3.000");
    expect(await db.queue.count()).toBe(1);

    await submit();

    expect(await db.queue.count()).toBe(0);
    expect(await quantityConflicts()).toEqual([]);
    expect(server.quantity(crate.id)).toBe("3.000");
});

it("leaves different rows of two people side by side without question", async () => {
    const bottle = server.create(BOTTLE);
    await fetchPositions(1);
    server.network = false;
    await enter(await openForm(), CRATE.id, "3");
    server.counts(bottle.id, "12");

    server.network = true;
    await submit();
    await fetchPositions(1);

    expect(server.quantity(crate.id)).toBe("3.000");
    expect(server.quantity(bottle.id)).toBe("12.000");
    expect((await here(BOTTLE.id))[0].eingabemenge).toBe("12.000");
    expect(await quantityConflicts()).toEqual([]);
});

it("asks only once per row, with the most recent number", async () => {
    const draft = await openForm();
    server.counts(crate.id, "5");
    await enter(draft, CRATE.id, "3");
    // Once more without deciding, again seen with an empty form.
    const again = draftFrom(ARTICLE, [{ ...crate, eingabemenge: null }]);
    server.counts(crate.id, "6");
    await enter(again, CRATE.id, "2");

    const conflicts = await quantityConflicts();
    expect(conflicts).toHaveLength(1);
    expect(conflicts[0]).toMatchObject({ mine: "2", theirs: "6.000" });
    expect(await remembered("rejected", [])).toEqual([]);
});
