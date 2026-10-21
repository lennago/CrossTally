/**
 * Every kind of job, sent: what goes to the server, and what comes back.
 *
 * One case per kind the app puts into the queue, each on its good way
 * through 'submit', the three ways of correcting a bowl included.
 * 'sync.test.js' walks the bad ways. At the end two rows the server cannot
 * give back: one it deleted meanwhile, and an own one without its number.
 */

import { beforeEach, expect, it } from "vitest";

import { rejectedJobs, removeWeighing, submit } from "./sync.js";
import { db, remember } from "./storage.js";
import { clearStorage, respond } from "../test_support.js";

const row = (id, extra = {}) => ({
    id,
    inventur_id: 1,
    ort: { id: 7, name: "Theke", bereich: "Theke" },
    artikel_id: 11,
    artikel: "Ware N",
    gebinde: null,
    wird_gewogen: true,
    wiegungen: [],
    eingabemenge: null,
    aktiv: true,
    zuletzt_geaendert: "2026-01-31T10:00:00+01:00",
    ...extra,
});

/** A row as the storage holds it: an own one has a negative number and no server number yet. */
const stored = (id, extra = {}) => ({
    ...row(id, extra),
    server_id: id > 0 ? id : null,
    location_id: 7,
});

const BOWL = { id: 9, kennung: "k-1", brutto_gramm: "900.000", behaelter: 3, netto: "650.000" };
const CORRECTED = { ...BOWL, brutto_gramm: "950.000", netto: "700.000" };

const reviewRow = (extra = {}) => ({
    hausartikel: 11,
    name: "Ware N",
    notiz: "",
    differenz: "2",
    soll_statt_ist: false,
    ...extra,
});

/**
 * A server that answers by method and path, and notes what it is asked, with the body.
 * What 'answers' does not name it answers with 404.
 */
function serve(answers) {
    const asked = [];
    globalThis.fetch = (url, options = {}) => {
        const request = `${options.method ?? "GET"} ${url.replace("/api/v1", "")}`;
        const body = options.body ? JSON.parse(options.body) : null;
        asked.push([request, body]);
        const answer = answers[request];
        return answer ? answer(body) : respond({ detail: "Nicht gefunden." }, 404);
    };
    return asked;
}

/** The review list of inventory 1 with its one row, as last fetched. */
const withReview = () =>
    db.reviews.put({
        inventory_id: 1,
        inventur: 1,
        differenz_von_hand: true,
        zeilen: [reviewRow()],
    });

const storedReviewRow = async () => (await db.reviews.get(1)).zeilen[0];

const CASES = [
    {
        name: "creates a new row and takes the server's number",
        rows: [stored(-1, { wird_gewogen: false })],
        job: {
            kind: "position",
            position: -1,
            payload: { inventur: 1, ort: 7, artikel: 11, gebinde: null },
        },
        answers: () => ({
            "POST /zaehlpositionen/": () => respond(row(56, { wird_gewogen: false }), 201),
        }),
        asked: [["POST /zaehlpositionen/", { inventur: 1, ort: 7, artikel: 11, gebinde: null }]],
        after: async () => {
            const rows = await db.positions.toArray();
            expect(rows.map((entry) => [entry.id, entry.server_id])).toEqual([[56, 56]]);
        },
    },
    {
        name: "removes a row by the number the job carries",
        rows: [],
        job: { kind: "position_removed", position: 55, serverId: 55, inventory: 1 },
        answers: () => ({ "DELETE /zaehlpositionen/55/": () => respond(null, 204) }),
        asked: [["DELETE /zaehlpositionen/55/", null]],
    },
    {
        name: "sends a quantity and takes the row as the server writes it",
        rows: [stored(55, { wird_gewogen: false })],
        job: {
            kind: "quantity",
            position: 55,
            payload: { eingabemenge: "4", eingabemenge_vorher: null },
        },
        answers: () => ({
            "PATCH /zaehlpositionen/55/": () =>
                respond(row(55, { wird_gewogen: false, eingabemenge: "4.000" })),
        }),
        asked: [["PATCH /zaehlpositionen/55/", { eingabemenge: "4", eingabemenge_vorher: null }]],
        after: async () => expect((await db.positions.get(55)).eingabemenge).toBe("4.000"),
    },
    {
        name: "sends a new bowl and fetches its row anew",
        rows: [stored(55)],
        job: {
            kind: "weighing",
            position: 55,
            payload: { brutto_gramm: "900", behaelter: 3, kennung: "k-1" },
        },
        answers: () => ({
            "POST /wiegungen/": () => respond(BOWL, 201),
            "GET /zaehlpositionen/55/": () => respond(row(55, { wiegungen: [BOWL] })),
        }),
        asked: [
            [
                "POST /wiegungen/",
                { brutto_gramm: "900", behaelter: 3, kennung: "k-1", zaehlposition: 55 },
            ],
            ["GET /zaehlpositionen/55/", null],
        ],
        after: async () => expect((await db.positions.get(55)).wiegungen).toEqual([BOWL]),
    },
    {
        name: "corrects a bowl by its number",
        rows: [stored(55, { wiegungen: [BOWL] })],
        job: {
            kind: "weighing_changed",
            position: 55,
            weighing: 9,
            kennung: "k-1",
            payload: { brutto_gramm: "950", behaelter: 3 },
        },
        answers: () => ({
            "PATCH /wiegungen/9/": () => respond(CORRECTED),
            "GET /zaehlpositionen/55/": () => respond(row(55, { wiegungen: [CORRECTED] })),
        }),
        asked: [
            ["PATCH /wiegungen/9/", { brutto_gramm: "950", behaelter: 3 }],
            ["GET /zaehlpositionen/55/", null],
        ],
        after: async () => expect((await db.positions.get(55)).wiegungen).toEqual([CORRECTED]),
    },
    {
        name: "corrects a bowl the device knows by its key, under the number the server names",
        rows: [stored(55, { wiegungen: [{ ...BOWL, id: null }] })],
        job: {
            kind: "weighing_changed",
            position: 55,
            weighing: null,
            kennung: "k-1",
            payload: { brutto_gramm: "950", behaelter: 3 },
        },
        answers: () => {
            let bowl = BOWL;
            return {
                "PATCH /wiegungen/9/": () => respond((bowl = CORRECTED)),
                "GET /zaehlpositionen/55/": () => respond(row(55, { wiegungen: [bowl] })),
            };
        },
        asked: [
            ["GET /zaehlpositionen/55/", null],
            ["PATCH /wiegungen/9/", { brutto_gramm: "950", behaelter: 3 }],
            ["GET /zaehlpositionen/55/", null],
        ],
        after: async () => expect((await db.positions.get(55)).wiegungen).toEqual([CORRECTED]),
    },
    {
        name: "creates a corrected bowl under its key where the server does not have it",
        rows: [stored(55, { wiegungen: [{ ...BOWL, id: null }] })],
        job: {
            kind: "weighing_changed",
            position: 55,
            weighing: null,
            kennung: "k-1",
            payload: { brutto_gramm: "950", behaelter: 3 },
        },
        answers: () => {
            let bowls = [];
            return {
                "POST /wiegungen/": () => respond((bowls = [CORRECTED])[0], 201),
                "GET /zaehlpositionen/55/": () => respond(row(55, { wiegungen: bowls })),
            };
        },
        asked: [
            ["GET /zaehlpositionen/55/", null],
            [
                "POST /wiegungen/",
                { brutto_gramm: "950", behaelter: 3, kennung: "k-1", zaehlposition: 55 },
            ],
            ["GET /zaehlpositionen/55/", null],
        ],
        after: async () => expect((await db.positions.get(55)).wiegungen).toEqual([CORRECTED]),
    },
    {
        name: "removes a bowl and fetches its row anew",
        rows: [stored(55, { wiegungen: [BOWL] })],
        job: { kind: "weighing_removed", position: 55, weighing: 9 },
        answers: () => ({
            "DELETE /wiegungen/9/": () => respond(null, 204),
            "GET /zaehlpositionen/55/": () => respond(row(55)),
        }),
        asked: [
            ["DELETE /wiegungen/9/", null],
            ["GET /zaehlpositionen/55/", null],
        ],
        after: async () => expect((await db.positions.get(55)).wiegungen).toEqual([]),
    },
    {
        name: "sends a note and takes the row of the review list as the server answers it",
        rows: [],
        setup: withReview,
        job: {
            kind: "note",
            inventory: 1,
            article: 11,
            payload: { notiz: "Bruch", notiz_vorher: "" },
        },
        answers: () => ({
            "PATCH /inventuren/1/pruefliste/11/": () =>
                respond({ zeile: reviewRow({ notiz: "Bruch" }) }),
        }),
        asked: [["PATCH /inventuren/1/pruefliste/11/", { notiz: "Bruch", notiz_vorher: "" }]],
        after: async () => expect((await storedReviewRow()).notiz).toBe("Bruch"),
    },
    {
        name: "sends a difference by hand the same way",
        rows: [],
        setup: withReview,
        job: {
            kind: "difference",
            inventory: 1,
            article: 11,
            payload: { differenz: "-2", differenz_vorher: "2" },
        },
        answers: () => ({
            "PATCH /inventuren/1/pruefliste/11/": () =>
                respond({ zeile: reviewRow({ differenz: "-2" }) }),
        }),
        asked: [["PATCH /inventuren/1/pruefliste/11/", { differenz: "-2", differenz_vorher: "2" }]],
        after: async () => expect((await storedReviewRow()).differenz).toBe("-2"),
    },
    {
        name: "sends 'target counts' the same way",
        rows: [],
        setup: withReview,
        job: { kind: "target", inventory: 1, article: 11, payload: { soll_statt_ist: true } },
        answers: () => ({
            "PATCH /inventuren/1/pruefliste/11/": () =>
                respond({ zeile: reviewRow({ soll_statt_ist: true }) }),
        }),
        asked: [["PATCH /inventuren/1/pruefliste/11/", { soll_statt_ist: true }]],
        after: async () => expect((await storedReviewRow()).soll_statt_ist).toBe(true),
    },
];

beforeEach(async () => {
    await clearStorage();
    await remember("access_token", "a");
    await remember("refresh_token", "r");
});

it.each(CASES)("$name", async ({ rows, setup, job, answers, asked, after }) => {
    await db.positions.bulkPut(rows);
    await setup?.();
    await db.queue.add(job);
    const requests = serve(answers());

    expect(await submit()).toEqual({ sent: 1, pending: 0 });

    expect(requests).toEqual(asked);
    expect(await rejectedJobs()).toEqual([]);
    await after?.();
});

/** Removed in the form: the job carries the bowl's number, the row loses it at once. */
it("puts the removal of a stored bowl into the queue, and sends it", async () => {
    await db.positions.put(stored(55, { wiegungen: [BOWL] }));
    globalThis.fetch = () => Promise.reject(new TypeError("kein Netz"));

    await removeWeighing(await db.positions.get(55), BOWL);

    expect((await db.positions.get(55)).wiegungen).toEqual([]);
    const jobs = await db.queue.toArray();
    expect(jobs.map((entry) => [entry.kind, entry.position, entry.weighing])).toEqual([
        ["weighing_removed", 55, 9],
    ]);

    const requests = serve({
        "DELETE /wiegungen/9/": () => respond(null, 204),
        "GET /zaehlpositionen/55/": () => respond(row(55)),
    });
    await submit();
    expect(requests.map(([request]) => request)).toEqual([
        "DELETE /wiegungen/9/",
        "GET /zaehlpositionen/55/",
    ]);
    expect(await db.queue.count()).toBe(0);
});

/** Deleted on the server in the meantime: the row goes here too, the bowl counts as sent. */
it("drops a row the server names as deleted when it comes anew after a bowl", async () => {
    await db.positions.put(stored(55));
    await db.queue.add({
        kind: "weighing",
        position: 55,
        payload: { brutto_gramm: "900", behaelter: 3, kennung: "k-1" },
    });
    serve({
        "POST /wiegungen/": () => respond(BOWL, 201),
        "GET /zaehlpositionen/55/": () => respond(row(55, { aktiv: false })),
    });

    await submit();

    expect(await db.positions.get(55)).toBeUndefined();
    expect(await db.queue.count()).toBe(0);
    expect(await rejectedJobs()).toEqual([]);
});

/**
 * An own row whose creation is not in the queue any more has no server
 * number to send to. That counts as "again later": nothing goes out, the
 * order stays, and nothing is noted as refused.
 */
it("stops at an own row that has no server number yet, and keeps the order", async () => {
    await db.positions.bulkPut([stored(-3, { wird_gewogen: false }), stored(56)]);
    await db.queue.bulkAdd([
        { kind: "quantity", position: -3, payload: { eingabemenge: "2" } },
        { kind: "quantity", position: 56, payload: { eingabemenge: "5" } },
    ]);
    const requests = serve({});

    expect(await submit()).toEqual({ sent: 0, pending: 2 });

    expect(requests).toEqual([]);
    expect(await rejectedJobs()).toEqual([]);
});

/**
 * A kind this version does not write, say from a newer one before a step
 * back. It goes out the way every kind the code does not name goes, as a
 * new bowl. The server refuses it, the sync window names it with the
 * server's reason, and the queue goes on behind it.
 */
it("sends a kind it does not know as a new bowl, and notes the refusal", async () => {
    await db.positions.bulkPut([stored(55), stored(56, { wird_gewogen: false })]);
    await db.queue.bulkAdd([
        { kind: "bowl_split", position: 55, payload: { teile: 2 } },
        {
            kind: "quantity",
            position: 56,
            payload: { eingabemenge: "4", eingabemenge_vorher: null },
        },
    ]);
    const requests = serve({
        "POST /wiegungen/": () => respond({ detail: "Das ist keine Wiegung." }, 400),
        "PATCH /zaehlpositionen/56/": () =>
            respond(row(56, { wird_gewogen: false, eingabemenge: "4.000" })),
    });

    expect(await submit()).toEqual({ sent: 2, pending: 0 });

    expect(requests).toEqual([
        ["POST /wiegungen/", { teile: 2, zaehlposition: 55 }],
        ["PATCH /zaehlpositionen/56/", { eingabemenge: "4", eingabemenge_vorher: null }],
    ]);
    const [refused] = await rejectedJobs();
    expect([refused.kind, refused.reason]).toEqual(["bowl_split", "Das ist keine Wiegung."]);
});
