/**
 * Submitting under adverse circumstances: a dead spot in the middle of a
 * job, a row the server already knows, an expired sign-in.
 *
 * In storage all of that is the normal case, not the exception, and each of
 * these cases once counted a quantity twice or swallowed it.
 */

import { beforeEach, expect, it } from "vitest";

import {
    rejectedJobs,
    quantityConflicts,
    submit,
    removeInventory,
    removePosition,
    fetchPositions,
    changeWeighing,
    cleanUp,
    pendingJobs,
    createWeighing,
    enterDifference,
    enterNote,
    enterQuantity,
    enterTargetCounts,
    fetchInventories,
    removeWeighing,
    switchLock,
    uuid,
} from "./sync.js";
import { saveDraft, savedDrafts } from "./draft_backup.js";
import { db, remembered, remember } from "./storage.js";
import { respond } from "../test_support.js";
import { apply } from "./language.svelte.js";

const row = (id, extra = {}) => ({
    id,
    inventur_id: 1,
    ort: { id: 7, name: "Theke", bereich: "Theke" },
    artikel_id: 11,
    artikel: "Artikel N",
    gebinde: null,
    wird_gewogen: true,
    wiegungen: [],
    eingabemenge: null,
    aktiv: true,
    zuletzt_geaendert: "2026-01-31T10:00:00+01:00",
    ...extra,
});

beforeEach(async () => {
    await Promise.all([
        db.state.clear(),
        db.inventories.clear(),
        db.positions.clear(),
        db.queue.clear(),
    ]);
    await remember("access_token", "a");
    await remember("refresh_token", "r");
});

it("does not send a weighing again when only the refetch fails on the network", async () => {
    await db.positions.put({ ...row(55), server_id: 55, location_id: 7 });
    await db.queue.add({
        kind: "weighing",
        position: 55,
        payload: { brutto_gramm: "900", behaelter: 3, kennung: "k-1" },
    });
    const posts = [];
    let networkUp = false;
    globalThis.fetch = (url, options = {}) => {
        if (url === "/api/v1/wiegungen/" && options.method === "POST") {
            posts.push(JSON.parse(options.body));
            return respond({ id: posts.length }, 201);
        }
        return networkUp ? respond(row(55)) : Promise.reject(new TypeError("Funkloch"));
    };

    await submit();
    expect(await db.queue.count()).toBe(0);
    networkUp = true;
    await submit();

    expect(posts).toHaveLength(1);
    expect(posts[0].kennung).toBe("k-1");
});

it("gives every weighing a key", async () => {
    globalThis.fetch = () => Promise.reject(new TypeError("kein Netz"));
    await db.positions.put({ ...row(55), server_id: 55, location_id: 7 });

    await createWeighing({ ...row(55), server_id: 55 }, "900", 3, false);

    const [job] = await db.queue.toArray();
    expect(job.payload.kennung).toMatch(/^[0-9a-f-]{20,}$/);
});

it("builds a valid key where the browser offers none", () => {
    const original = crypto.randomUUID;
    crypto.randomUUID = undefined;
    try {
        const keys = [uuid(), uuid()];
        for (const key of keys) {
            expect(key).toMatch(
                /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
            );
        }
        expect(keys[0]).not.toBe(keys[1]);
    } finally {
        crypto.randomUUID = original;
    }
});

it("adopts the server's row when it already knows it", async () => {
    await db.positions.put({ ...row(-1), server_id: null, location_id: 7, eingabemenge: "2" });
    await db.queue.add({
        kind: "position",
        position: -1,
        payload: { inventur: 1, ort: 7, artikel: 11, gebinde: null },
    });
    await db.queue.add({ kind: "quantity", position: -1, payload: { eingabemenge: "2" } });
    const sent = [];
    globalThis.fetch = (url, options = {}) => {
        sent.push(`${options.method ?? "GET"} ${url}`);
        if (url === "/api/v1/zaehlpositionen/" && options.method === "POST") {
            return respond(
                { vorhanden_id: ["55"], detail: ["Diese Zeile gibt es hier schon."] },
                400,
            );
        }
        if (url === "/api/v1/zaehlpositionen/55/") {
            return respond(row(55, { eingabemenge: options.method === "PATCH" ? "2" : null }));
        }
        if (url.startsWith("/api/v1/inventuren/1/")) {
            return respond({ id: 1, zaehlpositionen: [row(55, { eingabemenge: "2" })] });
        }
        return Promise.reject(new TypeError("?"));
    };

    await submit();
    await fetchPositions(1);

    const rows = await db.positions.where("inventur_id").equals(1).toArray();
    expect(rows.map((z) => z.id)).toEqual([55]);
    // The quantity went to the server's row, not to an own number.
    expect(sent).toContain("PATCH /api/v1/zaehlpositionen/55/");
});

it("stops on an expired sign-in instead of discarding the job", async () => {
    await db.positions.put({ ...row(55, { wird_gewogen: false }), server_id: 55, location_id: 7 });
    await db.queue.add({ kind: "quantity", position: 55, payload: { eingabemenge: "4" } });
    globalThis.fetch = (url) =>
        url === "/api/v1/token/erneuern/"
            ? respond({ detail: "Token is invalid or expired" }, 401)
            : respond({ detail: "Authentifizierung fehlgeschlagen" }, 401);

    await submit();

    expect(await db.queue.count()).toBe(1);
    expect(await rejectedJobs()).toHaveLength(0);
});

it("keeps the job and stops on a server error", async () => {
    // Maintenance or an error page as HTML: nothing is wrong with the job,
    // and the one behind it is not even tried, so the order stays.
    await db.positions.bulkPut([
        { ...row(55, { wird_gewogen: false }), server_id: 55, location_id: 7 },
        { ...row(56, { wird_gewogen: false }), server_id: 56, location_id: 7 },
    ]);
    await db.queue.bulkAdd([
        { kind: "quantity", position: 55, payload: { eingabemenge: "4" } },
        { kind: "quantity", position: 56, payload: { eingabemenge: "5" } },
    ]);
    const sent = [];
    globalThis.fetch = (url, options = {}) => {
        sent.push(`${options.method ?? "GET"} ${url}`);
        return Promise.resolve({
            ok: false,
            status: 500,
            text: () => Promise.resolve("<html><body>Server Error</body></html>"),
        });
    };

    await submit();

    expect(sent).toEqual(["PATCH /api/v1/zaehlpositionen/55/"]);
    expect(await db.queue.count()).toBe(2);
    expect(await rejectedJobs()).toEqual([]);
});

it("lays a quantity for a vanished row under the rejected ones and goes on", async () => {
    await db.positions.bulkPut([
        { ...row(55, { wird_gewogen: false }), server_id: 55, location_id: 7 },
        { ...row(56, { wird_gewogen: false }), server_id: 56, location_id: 7 },
    ]);
    await db.queue.bulkAdd([
        { kind: "quantity", position: 55, payload: { eingabemenge: "4" } },
        { kind: "quantity", position: 56, payload: { eingabemenge: "5" } },
    ]);
    globalThis.fetch = (url) =>
        url.includes("/55/")
            ? respond({ detail: "Nicht gefunden." }, 404)
            : respond(row(56, { wird_gewogen: false, eingabemenge: "5" }));

    await submit();

    expect(await db.queue.count()).toBe(0);
    const [rejected] = await rejectedJobs();
    expect(rejected.reason).toBe("Nicht gefunden.");
    expect(rejected.value).toBe("4");
});

/**
 * A proxy restarts while the sign-in is renewed, and the browser carries the
 * website's session of someone else. Until 10/2026 the 503 dropped the
 * tokens, and what followed went out without one. The server answered it
 * through the session: that account's inventories landed here, the waiting
 * count met "CSRF token missing" as a refusal, and the number was gone.
 */
it("keeps the count and the sign-in when the renewal meets a proxy restart", async () => {
    await db.positions.put({ ...row(55, { wird_gewogen: false }), server_id: 55, location_id: 7 });
    await db.queue.add({ kind: "quantity", position: 55, payload: { eingabemenge: "4" } });
    const sent = [];
    globalThis.fetch = (url, options = {}) => {
        const token = options.headers?.Authorization ?? "-";
        sent.push(`${options.method ?? "GET"} ${url} ${token}`);
        if (url === "/api/v1/token/erneuern/") {
            return respond({ detail: "Service Unavailable" }, 503);
        }
        if (token !== "-") return respond({ detail: "Token is invalid or expired" }, 401);
        // Without a token the website's session answers, as someone else.
        if (options.method === "PATCH") {
            return respond({ detail: "CSRF Failed: CSRF token missing." }, 403);
        }
        return respond([{ id: 9, haus: "Kino West", stichtag: "2026-01-31" }]);
    };

    for (let round = 0; round < 2; round += 1) {
        await submit();
        await fetchInventories().catch((error) => error);
    }

    const withoutToken = sent.filter((entry) => entry.endsWith(" -"));
    expect(withoutToken.every((entry) => entry.includes("/token/erneuern/"))).toBe(true);
    expect(await db.queue.count()).toBe(1);
    expect(await rejectedJobs()).toEqual([]);
    expect(await db.inventories.count()).toBe(0);
    expect(await remembered("refresh_token")).toBe("r");
});

/**
 * With the token a 403 is the server's verdict on the job: the account may
 * not do this. It counts as refused, as before.
 */
it("still counts a job as refused that the server refuses to the account", async () => {
    await db.queue.add({
        kind: "target",
        inventory: 1,
        article: 11,
        payload: { soll_statt_ist: true },
    });
    globalThis.fetch = (url, options = {}) =>
        options.method === "PATCH"
            ? respond({ detail: "Ob das Soll zählt, entscheidet eine Führungskraft." }, 403)
            : respond({ detail: "Nicht gefunden." }, 404);

    await submit();

    expect(await db.queue.count()).toBe(0);
    const [rejected] = await rejectedJobs();
    expect(rejected.reason).toBe("Ob das Soll zählt, entscheidet eine Führungskraft.");
});

/**
 * A refused quantity does not stay on the row. Until 10/2026 it stood there
 * as counted, and the next entry went out with it as seen, refused once more.
 */
it("takes the server's row back after a refused quantity, and the next one sees it", async () => {
    const crate = { id: 2, name: "Kasten", faktor_in_basiseinheit: "20.000" };
    const onServer = row(55, { wird_gewogen: false, gebinde: crate, eingabemenge: "2.000" });
    await db.positions.put({ ...onServer, server_id: 55, location_id: 7 });
    const sent = [];
    globalThis.fetch = (url, options = {}) => {
        if (options.method === "PATCH") {
            sent.push(JSON.parse(options.body));
            return respond({ eingabemenge: ["mehr, als ein Ergebnis fasst"] }, 400);
        }
        return respond(onServer);
    };

    await enterQuantity(await db.positions.get(55), "60000000");

    expect((await db.positions.get(55)).eingabemenge).toBe("2.000");
    expect(await rejectedJobs()).toHaveLength(1);
    await enterQuantity(await db.positions.get(55), "3");
    expect(sent.at(-1)).toEqual({ eingabemenge: "3", eingabemenge_vorher: "2.000" });
});

it("takes a refused bowl off the row again", async () => {
    await db.positions.put({ ...row(55), server_id: 55, location_id: 7 });
    globalThis.fetch = (url, options = {}) =>
        options.method === "POST"
            ? respond({ behaelter: ["steht nicht zur Wahl"] }, 400)
            : respond(row(55));

    await createWeighing(await db.positions.get(55), "900", 3);

    expect((await db.positions.get(55)).wiegungen).toEqual([]);
    expect(await rejectedJobs()).toHaveLength(1);
});

/**
 * What a result cannot hold, the form does not see for a bowl or a portion:
 * it knows neither the house's unit nor the recipe. The server refuses it on
 * the field, and its sentence is what the sync window names.
 */
it("names the server's sentence for a refused bowl and a refused number", async () => {
    await db.positions.bulkPut([
        { ...row(55), server_id: 55, location_id: 7 },
        { ...row(56, { wird_gewogen: false }), server_id: 56, location_id: 7 },
    ]);
    const bowl = "Das wären 1000000000 g, mehr als ein Ergebnis fasst. Bitte die Zahl prüfen.";
    const portions =
        "Für „Artikel N“ wären das 1400000000 g, mehr als ein Ergebnis fasst. Bitte die Zahl prüfen.";
    globalThis.fetch = (url, options = {}) => {
        if (options.method === "POST") return respond({ brutto_gramm: [bowl] }, 400);
        if (options.method === "PATCH") return respond({ eingabemenge: [portions] }, 400);
        return respond(url.includes("/56/") ? row(56, { wird_gewogen: false }) : row(55));
    };

    await createWeighing(await db.positions.get(55), "400000250", 3);
    await enterQuantity(await db.positions.get(56), "10000000");

    const rejected = await rejectedJobs();
    expect(rejected.map((entry) => [entry.kind, entry.value, entry.reason])).toEqual([
        ["weighing", "400000250", bowl],
        ["quantity", "10000000", portions],
    ]);
});

/**
 * The loose single unit is a word of the language, not a name of the catalog:
 * the form says "Single", and so do the waiting job and the question.
 */
it("names a single pack by the app's own word in the sync window", async () => {
    const single = { id: 3, name: "Einzeln", ist_einzeln: true, faktor_in_basiseinheit: "1.000" };
    const onServer = row(55, { wird_gewogen: false, gebinde: single, eingabemenge: "14.000" });
    await db.positions.put({ ...onServer, server_id: 55, location_id: 7 });
    await db.queue.add({
        kind: "quantity",
        position: 55,
        payload: { eingabemenge: "10", eingabemenge_vorher: null },
    });
    apply("en");
    try {
        expect((await pendingJobs())[0].label).toBe("Artikel N · Single · Theke");
        globalThis.fetch = () =>
            respond({ konflikt: "eingabemenge", detail: "…", aktuell: onServer }, 409);
        await submit();
        const [conflict] = await quantityConflicts();
        expect(conflict.pack).toBe("Single");
        expect(conflict.label).toBe("Artikel N · Single · Theke");
    } finally {
        apply("de");
    }
});

it("counts a removal the server no longer finds as done", async () => {
    // The first DELETE got through, its answer was lost. Until 10/2026 the
    // second one stood as rejected: "Weighing removed · Not found."
    await db.positions.put({ ...row(55), server_id: 55, location_id: 7 });
    await db.queue.bulkAdd([
        { kind: "weighing_removed", weighing: 9, position: 55 },
        { kind: "position_removed", serverId: 56, position: 56 },
    ]);
    globalThis.fetch = () => respond({ detail: "Nicht gefunden." }, 404);

    await submit();

    expect(await db.queue.count()).toBe(0);
    expect(await rejectedJobs()).toEqual([]);
});

it("sets the marker back a few seconds", async () => {
    globalThis.fetch = () =>
        respond({
            id: 1,
            zaehlpositionen: [row(55, { zuletzt_geaendert: "2026-01-31T10:00:10.000Z" })],
        });

    await fetchPositions(1);

    expect(await remembered("synced:1")).toBe("2026-01-31T10:00:05.000Z");
});

it("does not let a rejected new row block the queue forever", async () => {
    // Created and counted offline. Meanwhile the article is discontinued, the
    // server rejects the creation. Behind it waits a quantity for an entirely
    // different row. That still has to go out.
    await db.positions.put({ ...row(-1), server_id: null, location_id: 7, wird_gewogen: false });
    await db.positions.put({ ...row(55, { wird_gewogen: false }), server_id: 55, location_id: 7 });
    await db.queue.add({
        kind: "position",
        position: -1,
        payload: { inventur: 1, ort: 7, artikel: 11, gebinde: null },
    });
    await db.queue.add({ kind: "quantity", position: -1, payload: { eingabemenge: "2" } });
    await db.queue.add({ kind: "quantity", position: 55, payload: { eingabemenge: "4" } });
    const sent = [];
    globalThis.fetch = (url, options = {}) => {
        sent.push(`${options.method ?? "GET"} ${url}`);
        if (url === "/api/v1/zaehlpositionen/" && options.method === "POST") {
            return respond(
                { artikel: ["„Artikel N“ ist eingestellt und wird nicht mehr gezählt."] },
                400,
            );
        }
        if (url === "/api/v1/zaehlpositionen/55/") {
            return respond(row(55, { wird_gewogen: false, eingabemenge: "4" }));
        }
        return Promise.reject(new TypeError("?"));
    };

    await submit();

    expect(sent).toContain("PATCH /api/v1/zaehlpositionen/55/");
    expect(await db.queue.count()).toBe(0);
    // Both stand as rejected: the row and the quantity that hung on it.
    expect((await rejectedJobs()).map((k) => k.kind)).toEqual(["position", "quantity"]);
    // And the row that will never exist on the server no longer stands in the list.
    expect(await db.positions.get(-1)).toBeUndefined();
});

it("has the master data fetched anew once the server rejects a new row built from it", async () => {
    // The pack was merged into another one on the website in the meantime.
    // Offered once more, it would be rejected once more.
    const master = { orte: [], artikel: [], inventurarten: [], behaelter: [] };
    await db.masterData.bulkPut([
        { inventory_id: 1, ...master },
        { inventory_id: 2, ...master },
    ]);
    await db.positions.put({ ...row(-1), server_id: null, location_id: 7, wird_gewogen: false });
    await db.queue.add({
        kind: "position",
        position: -1,
        payload: { inventur: 1, ort: 7, artikel: 11, gebinde: 3 },
    });
    const gone = "Dieses Gebinde gibt es nicht mehr, denn die Ware wurde inzwischen geändert.";
    globalThis.fetch = () => respond({ gebinde: [gone] }, 400);

    await submit();

    expect((await rejectedJobs()).map((job) => job.reason)).toEqual([gone]);
    expect((await db.masterData.get(1)).outdated).toBe(true);
    expect((await db.masterData.get(2)).outdated).toBeUndefined();
    // Kept until the new ones come: without network another pack still works.
    expect((await db.masterData.get(1)).artikel).toEqual([]);
});

it("strikes a never sent row with its jobs without asking the server", async () => {
    const own = { ...row(-1), server_id: null, location_id: 7 };
    await db.positions.put(own);
    await db.queue.add({ kind: "position", position: -1, payload: { inventur: 1 } });
    await db.queue.add({ kind: "quantity", position: -1, payload: { eingabemenge: "2" } });
    const sent = [];
    globalThis.fetch = (url) => {
        sent.push(url);
        return Promise.reject(new TypeError("?"));
    };

    await removePosition(own);

    expect(await db.positions.get(-1)).toBeUndefined();
    expect(await db.queue.count()).toBe(0);
    expect(sent).toEqual([]);
});

it("reports a removed row the server knows, even after a dead spot", async () => {
    const known = { ...row(55), server_id: 55, location_id: 7 };
    await db.positions.put(known);
    await db.queue.add({ kind: "quantity", position: 55, payload: { eingabemenge: "2" } });
    let network = false;
    const sent = [];
    globalThis.fetch = (url, options = {}) => {
        if (!network) return Promise.reject(new TypeError("kein Netz"));
        sent.push(`${options.method ?? "GET"} ${url}`);
        return respond({}, 204);
    };

    await removePosition(known);
    // Gone here at once, the waiting quantity void. The job for removing waits.
    expect(await db.positions.get(55)).toBeUndefined();
    expect((await db.queue.toArray()).map((a) => a.kind)).toEqual(["position_removed"]);

    network = true;
    await submit();

    expect(sent).toEqual(["DELETE /api/v1/zaehlpositionen/55/"]);
    expect(await db.queue.count()).toBe(0);
});

it("keeps both bowls weighed in one go without network", async () => {
    // The draft passes the same row in twice, as it looked when opening.
    const scale = { ...row(55), server_id: 55, location_id: 7 };
    await db.positions.put(scale);
    globalThis.fetch = () => Promise.reject(new TypeError("kein Netz"));

    await createWeighing(scale, "900", 3, false);
    await createWeighing(scale, "700", 3, false);

    const stored = await db.positions.get(55);
    expect(stored.wiegungen.map((w) => w.brutto_gramm)).toEqual(["900", "700"]);
    expect(await db.queue.count()).toBe(2);
});

it("no longer sends a weighing that was removed again before sending", async () => {
    const scale = { ...row(55), server_id: 55, location_id: 7 };
    await db.positions.put(scale);
    let network = false;
    const sent = [];
    globalThis.fetch = (url, options = {}) => {
        if (!network) return Promise.reject(new TypeError("kein Netz"));
        sent.push(`${options.method ?? "GET"} ${url}`);
        return respond(row(55));
    };
    await createWeighing(scale, "900", 3, false);
    await createWeighing(scale, "700", 3, false);

    // This is how the draft sees it when the row is opened again without network.
    const [first] = (await db.positions.get(55)).wiegungen;
    await removeWeighing(scale, first, false);

    expect((await db.positions.get(55)).wiegungen.map((w) => w.brutto_gramm)).toEqual(["700"]);
    network = true;
    await submit();
    // Exactly one weighing went out, the removed one did not.
    expect(sent.filter((entry) => entry === "POST /api/v1/wiegungen/")).toHaveLength(1);
});

it("corrects an unsent bowl by its key and sends the corrected weight", async () => {
    // Until 10/2026 the correction hit every unsent bowl of the row, and it
    // went out as a change of the number 'null': 404, the old weight stood.
    const scale = { ...row(55), server_id: 55, location_id: 7 };
    await db.positions.put(scale);
    let network = false;
    const sent = [];
    globalThis.fetch = (url, options = {}) => {
        if (!network) return Promise.reject(new TypeError("kein Netz"));
        const body = options.body ? JSON.parse(options.body) : null;
        sent.push(`${options.method ?? "GET"} ${url} ${body?.brutto_gramm ?? ""}`.trim());
        if (options.method === "POST")
            return respond({ id: 9, brutto_gramm: body.brutto_gramm }, 201);
        return respond(row(55));
    };
    await createWeighing(scale, "900", 3, false);
    await createWeighing(scale, "700", 3, false);

    // Opened again without network, the first bowl stands there as an existing weighing.
    const [first] = (await db.positions.get(55)).wiegungen;
    await changeWeighing(scale, first, "950", 3, false);

    expect((await db.positions.get(55)).wiegungen.map((w) => w.brutto_gramm)).toEqual([
        "950",
        "700",
    ]);
    expect((await db.queue.toArray()).map((job) => job.kind)).toEqual(["weighing", "weighing"]);
    network = true;
    await submit();
    expect(sent.filter((entry) => entry.startsWith("POST"))).toEqual([
        "POST /api/v1/wiegungen/ 950",
        "POST /api/v1/wiegungen/ 700",
    ]);
    expect(await rejectedJobs()).toEqual([]);
});

it("changes a stored weighing and refetches the row", async () => {
    // 1250 read wrongly, 1520 is right, and it was the other vessel. Without
    // network the storage shows it at once, with network the correction goes
    // out as it is, and the row comes back with the server's sum.
    const scale = {
        ...row(55, {
            wiegungen: [{ id: 9, brutto_gramm: "1250.000", behaelter: 3, netto: "1000" }],
        }),
        server_id: 55,
        location_id: 7,
    };
    await db.positions.put(scale);
    let network = false;
    const sent = [];
    globalThis.fetch = (url, options = {}) => {
        if (!network) return Promise.reject(new TypeError("kein Netz"));
        sent.push(`${options.method ?? "GET"} ${url} ${options.body ?? ""}`.trim());
        return respond(
            row(55, {
                wiegungen: [{ id: 9, brutto_gramm: "1520.000", behaelter: 1, netto: "1520" }],
            }),
        );
    };

    await changeWeighing(scale, scale.wiegungen[0], "1520", 1, false);

    const stored = (await db.positions.get(55)).wiegungen[0];
    expect([stored.brutto_gramm, stored.behaelter]).toEqual(["1520", 1]);
    expect((await db.queue.toArray()).map((job) => [job.kind, job.weighing])).toEqual([
        ["weighing_changed", 9],
    ]);
    network = true;
    await submit();
    expect(sent).toEqual([
        'PATCH /api/v1/wiegungen/9/ {"brutto_gramm":"1520","behaelter":1}',
        "GET /api/v1/zaehlpositionen/55/",
    ]);
    expect((await db.positions.get(55)).wiegungen[0].netto).toBe("1520");
    expect(await db.queue.count()).toBe(0);
});

it("corrects a bowl whose job went out but whose number never arrived", async () => {
    // The weighing was sent, the refetch failed on the network: the storage
    // shows it without a number. The server's row names the number.
    const scale = { ...row(55), server_id: 55, location_id: 7 };
    await db.positions.put({
        ...scale,
        wiegungen: [{ id: null, kennung: "k-9", brutto_gramm: "900", behaelter: 3, netto: null }],
    });
    const sent = [];
    globalThis.fetch = (url, options = {}) => {
        const body = options.body ? JSON.parse(options.body) : null;
        sent.push(`${options.method ?? "GET"} ${url} ${body?.brutto_gramm ?? ""}`.trim());
        return respond(
            row(55, { wiegungen: [{ id: 9, kennung: "k-9", brutto_gramm: "900", behaelter: 3 }] }),
        );
    };

    await changeWeighing(scale, { id: null, kennung: "k-9" }, "950", 3);

    expect(sent).toContain("PATCH /api/v1/wiegungen/9/ 950");
    expect(sent.some((entry) => entry.includes("/wiegungen/null/"))).toBe(false);
    expect(await rejectedJobs()).toEqual([]);
});

it("removes server rows the first fetch no longer names, and keeps own ones", async () => {
    // After the storage upgrade the marks are gone, and a row deleted on the
    // server meanwhile would never come along as deleted: a zombie in the list.
    await db.positions.bulkPut([
        { ...row(5), location_id: 7 },
        { ...row(-1), location_id: 7 },
        { ...row(9, { inventur_id: 2 }), location_id: 7 },
    ]);
    globalThis.fetch = () => respond({ id: 1, zaehlpositionen: [row(6)] });

    await fetchPositions(1);

    const ids = (await db.positions.toArray()).map((position) => position.id).sort();
    expect(ids).toEqual([-1, 6, 9]);
});

it("leaves unnamed rows alone when fetching only what changed", async () => {
    await db.positions.put({ ...row(5), location_id: 7 });
    await remember("synced:1", "2026-01-31T09:00:00Z");
    globalThis.fetch = () => respond({ id: 1, zaehlpositionen: [row(6)] });

    await fetchPositions(1);

    const ids = (await db.positions.toArray()).map((position) => position.id).sort();
    expect(ids).toEqual([5, 6]);
});

// What still waits in the queue stays visible when the server's row comes anew.

/** A bowl as the server sends it. */
const bowl = (id, grams, extra = {}) => ({
    id,
    kennung: `k-${id}`,
    brutto_gramm: grams,
    behaelter: 3,
    netto: null,
    ...extra,
});

it("keeps a bowl whose job hangs on a server error, and weighs it once", async () => {
    // 10/2026: every weighing ran into a 500. The next sync brought the row
    // without the bowl, so it was weighed again, and once the server took
    // both jobs it stood there twice.
    const scale = { ...row(55), server_id: 55, location_id: 7 };
    await db.positions.put(scale);
    let broken = true;
    const posts = [];
    globalThis.fetch = (url, options = {}) => {
        if (url === "/api/v1/wiegungen/" && options.method === "POST") {
            if (broken) return respond({ detail: "Serverfehler" }, 500);
            posts.push(JSON.parse(options.body));
            return respond({ id: 9 }, 201);
        }
        const weighings = posts.map((body) => bowl(9, "900.000", { kennung: body.kennung }));
        if (url === "/api/v1/zaehlpositionen/55/") {
            return respond(row(55, { wiegungen: weighings }));
        }
        return respond({ id: 1, zaehlpositionen: [row(55, { wiegungen: weighings })] });
    };

    await createWeighing(scale, "900", 3);
    await fetchPositions(1);

    const [waiting] = (await db.positions.get(55)).wiegungen;
    expect(waiting).toMatchObject({ id: null, brutto_gramm: "900", behaelter: 3 });
    expect(await db.queue.count()).toBe(1);

    broken = false;
    await submit();
    await fetchPositions(1);

    expect(posts).toHaveLength(1);
    expect((await db.positions.get(55)).wiegungen).toEqual([
        bowl(9, "900.000", { kennung: waiting.kennung }),
    ]);
});

it("keeps a quantity typed without network when the row comes anew", async () => {
    const crate = { ...row(55, { eingabemenge: "2.000" }), server_id: 55, location_id: 7 };
    await db.positions.put(crate);
    globalThis.fetch = (url) =>
        url.startsWith("/api/v1/inventuren/1/")
            ? respond({ id: 1, zaehlpositionen: [row(55, { eingabemenge: "2.000" })] })
            : Promise.reject(new TypeError("kein Netz"));

    await enterQuantity(crate, "5");
    await fetchPositions(1);

    expect((await db.positions.get(55)).eingabemenge).toBe("5");
});

it("does not bring back a row removed here before the removal went out", async () => {
    const position = { ...row(55), server_id: 55, location_id: 7 };
    await db.positions.put(position);
    globalThis.fetch = (url) =>
        url.startsWith("/api/v1/inventuren/1/")
            ? respond({ id: 1, zaehlpositionen: [row(55), row(56)] })
            : Promise.reject(new TypeError("kein Netz"));

    await removePosition(position);
    await fetchPositions(1);

    expect((await db.positions.toArray()).map((entry) => entry.id)).toEqual([56]);
});

it("shows a bowl only once whose answer got lost on the way", async () => {
    await db.positions.put({ ...row(55), server_id: 55, location_id: 7 });
    await db.queue.add({
        kind: "weighing",
        position: 55,
        payload: { brutto_gramm: "900", behaelter: 3, kennung: "k-9" },
    });
    globalThis.fetch = () =>
        respond({ id: 1, zaehlpositionen: [row(55, { wiegungen: [bowl(9, "900.000")] })] });

    await fetchPositions(1);

    expect((await db.positions.get(55)).wiegungen).toEqual([bowl(9, "900.000")]);
});

it("lays removed and corrected bowls over the row", async () => {
    await db.positions.put({ ...row(55), server_id: 55, location_id: 7 });
    await db.queue.bulkAdd([
        { kind: "weighing_removed", weighing: 8, position: 55 },
        {
            kind: "weighing_changed",
            weighing: 9,
            kennung: "k-9",
            position: 55,
            payload: { brutto_gramm: "950", behaelter: 4 },
        },
    ]);
    const weighings = [bowl(8, "500.000"), bowl(9, "900.000")];
    globalThis.fetch = () =>
        respond({ id: 1, zaehlpositionen: [row(55, { wiegungen: weighings })] });

    await fetchPositions(1);

    expect((await db.positions.get(55)).wiegungen).toEqual([bowl(9, "950", { behaelter: 4 })]);
});

it("keeps the bowl of a new row when only the row reached the server", async () => {
    const waiting = { id: null, kennung: "k-1", brutto_gramm: "900", behaelter: 3, netto: null };
    await db.positions.put({ ...row(-1), server_id: null, location_id: 7, wiegungen: [waiting] });
    await db.queue.bulkAdd([
        { kind: "position", position: -1, payload: { inventur: 1, ort: 7, artikel: 11 } },
        {
            kind: "weighing",
            position: -1,
            payload: { brutto_gramm: "900", behaelter: 3, kennung: "k-1" },
        },
    ]);
    globalThis.fetch = (url, options = {}) =>
        url === "/api/v1/zaehlpositionen/" && options.method === "POST"
            ? respond(row(55), 201)
            : respond({ detail: "Serverfehler" }, 500);

    await submit();

    const rows = await db.positions.toArray();
    expect(rows.map((entry) => entry.id)).toEqual([55]);
    expect(rows[0].wiegungen).toEqual([waiting]);
    expect((await db.queue.toArray()).map((job) => [job.kind, job.position])).toEqual([
        ["weighing", 55],
    ]);
});

//: The server's answer for an inventory that is not being counted right now.
const notCounting = (status, vorlaeufig) => ({
    konflikt: "status",
    status,
    vorlaeufig,
    detail: `„Kino Ost“ steht auf ${status === "VORBEREITUNG" ? "Vorbereitung" : "Abgeschlossen"}. Gezählt wird nur in der Zählung.`,
});

it("holds the numbers of an inventory taken back into preparation and sends the others", async () => {
    // Until 10/2026 the 400 counted as a verdict: the job was rejected without
    // its number, and the next sync cleared the inventory away.
    await db.inventories.bulkPut([
        { id: 1, haus: "Kino Ost" },
        { id: 2, haus: "Kino West" },
    ]);
    await db.positions.bulkPut([
        { ...row(55, { wird_gewogen: false }), server_id: 55, location_id: 7 },
        { ...row(56, { wird_gewogen: false }), server_id: 56, location_id: 7 },
        { ...row(66, { wird_gewogen: false, inventur_id: 2 }), server_id: 66, location_id: 7 },
    ]);
    await db.queue.bulkAdd([
        {
            kind: "quantity",
            position: 55,
            payload: { eingabemenge: "3", eingabemenge_vorher: null },
        },
        {
            kind: "quantity",
            position: 66,
            payload: { eingabemenge: "4", eingabemenge_vorher: null },
        },
        {
            kind: "quantity",
            position: 56,
            payload: { eingabemenge: "5", eingabemenge_vorher: null },
        },
    ]);
    const sent = [];
    globalThis.fetch = (url, options = {}) => {
        sent.push(`${options.method ?? "GET"} ${url}`);
        if (url.includes("/zaehlpositionen/66/")) return respond(row(66, { inventur_id: 2 }));
        return respond(notCounting("VORBEREITUNG", true), 409);
    };

    await submit();

    // The second row of the held inventory was not even tried, its order stays.
    expect(sent).toEqual([
        "PATCH /api/v1/zaehlpositionen/55/",
        "PATCH /api/v1/zaehlpositionen/66/",
    ]);
    expect((await db.queue.toArray()).map((job) => job.position)).toEqual([55, 56]);
    expect(await rejectedJobs()).toEqual([]);
    const [first] = await pendingJobs();
    // The server's sentence alone. Until 10/2026 "status VORBEREITUNG true" stood before it.
    expect(first.held).toBe(notCounting("VORBEREITUNG", true).detail);
    expect(first.value).toBe("3");
    // The list no longer names it, but its rows and numbers stay until they are out.
    expect(await cleanUp([2])).toEqual([]);
    expect(await db.positions.where("inventur_id").equals(1).count()).toBe(2);
});

/**
 * Set aside for its draft before 10/2026, with the lock it had: the notice of
 * its draft stays hidden until the lock opens, and it never opens.
 */
it("takes the lock off an inventory a draft kept from before", async () => {
    await db.inventories.clear();
    await db.inventories.put({ id: 1, haus: "Kino Ost", draftOnly: true, gesperrt: true });
    await saveDraft(
        { inventoryId: 1, area: "Theke", locationId: 7, articleId: 11, label: "Artikel N" },
        { packs: [] },
    );

    expect(await cleanUp([2])).toEqual([]);

    expect(await db.inventories.get(1)).toMatchObject({ draftOnly: true, gesperrt: false });
});

it("holds the numbers of a locked inventory until it opens", async () => {
    await db.inventories.put({ id: 1, haus: "Kino Ost" });
    await db.positions.put({ ...row(55, { wird_gewogen: false }), server_id: 55, location_id: 7 });
    await db.queue.add({
        kind: "quantity",
        position: 55,
        payload: { eingabemenge: "3", eingabemenge_vorher: null },
    });
    const locked = {
        konflikt: "status",
        status: "ZAEHLUNG",
        vorlaeufig: true,
        gesperrt: true,
        detail: "„Kino Ost“ ist gesperrt. Gezählt wird erst wieder, wenn eine Führungskraft sie öffnet.",
    };
    globalThis.fetch = () => respond(locked, 409);

    await submit();

    expect(await db.queue.count()).toBe(1);
    expect(await rejectedJobs()).toEqual([]);
    expect((await pendingJobs())[0].held).toBe(locked.detail);
});

it("keeps the lock of an inventory when its rows come", async () => {
    globalThis.fetch = () =>
        respond({ id: 1, haus: "Kino Ost", gesperrt: true, zaehlpositionen: [] });

    await fetchPositions(1);

    expect((await db.inventories.get(1)).gesperrt).toBe(true);
});

it("locks an inventory and keeps that here", async () => {
    await db.inventories.put({ id: 1, haus: "Kino Ost", gesperrt: false });
    const sent = [];
    globalThis.fetch = (url, options = {}) => {
        sent.push(`${options.method} ${url} ${options.body}`);
        return respond({ gesperrt: true });
    };

    expect(await switchLock(1, true)).toBe(true);

    expect(sent).toEqual(['POST /api/v1/inventuren/1/sperre/ {"an":true}']);
    expect((await db.inventories.get(1)).gesperrt).toBe(true);
});

it("rejects with the number when the inventory is closed for good", async () => {
    await db.positions.put({ ...row(55, { wird_gewogen: false }), server_id: 55, location_id: 7 });
    await db.queue.add({
        kind: "quantity",
        position: 55,
        payload: { eingabemenge: "3", eingabemenge_vorher: null },
    });
    globalThis.fetch = () => respond(notCounting("ABGESCHLOSSEN", false), 409);

    await submit();

    expect(await db.queue.count()).toBe(0);
    const [rejected] = await rejectedJobs();
    expect(rejected.value).toBe("3");
    expect(rejected.reason).toContain("Abgeschlossen");
    expect(rejected.label).toBe("Artikel N · Theke");
});

it("lets the queue continue with the other rows after a question", async () => {
    await db.positions.bulkPut([
        { ...row(55, { wird_gewogen: false }), server_id: 55, location_id: 7, eingabemenge: "3" },
        { ...row(56, { wird_gewogen: false }), server_id: 56, location_id: 7, eingabemenge: "4" },
    ]);
    await db.queue.bulkAdd([
        {
            kind: "quantity",
            position: 55,
            payload: { eingabemenge: "3", eingabemenge_vorher: null },
        },
        {
            kind: "quantity",
            position: 56,
            payload: { eingabemenge: "4", eingabemenge_vorher: null },
        },
    ]);
    const sent = [];
    globalThis.fetch = (url, options = {}) => {
        sent.push(`${options.method ?? "GET"} ${url}`);
        if (url === "/api/v1/zaehlpositionen/55/") {
            return respond(
                { konflikt: "eingabemenge", aktuell: row(55, { eingabemenge: "5.000" }) },
                409,
            );
        }
        return respond(row(56, { eingabemenge: "4.000" }));
    };

    await submit();

    expect(sent).toEqual([
        "PATCH /api/v1/zaehlpositionen/55/",
        "PATCH /api/v1/zaehlpositionen/56/",
    ]);
    expect(await db.queue.count()).toBe(0);
    expect(await quantityConflicts()).toHaveLength(1);
    // A question is no rejection: it does not stand under "Not accepted".
    expect(await rejectedJobs()).toEqual([]);
    expect((await db.positions.get(55)).eingabemenge).toBe("5.000");
});

it("clears an inventory's left-behind drafts away with it", async () => {
    await saveDraft({ inventoryId: 1, locationId: 7, articleId: 11 }, { gebinde: [] });
    await saveDraft({ inventoryId: 2, locationId: 7, articleId: 11 }, { gebinde: [] });

    await removeInventory(1);

    expect((await savedDrafts()).map((e) => e.inventoryId)).toEqual([2]);
});

const locked = {
    konflikt: "status",
    status: "ZAEHLUNG",
    vorlaeufig: true,
    gesperrt: true,
    detail: "ist gesperrt.",
};

async function reviewWithQueuedQuantity() {
    await db.inventories.put({ id: 1, haus: "Kino Ost", gesperrt: true });
    await db.positions.put({ ...row(55, { wird_gewogen: false }), server_id: 55, location_id: 7 });
    await db.reviews.put({
        inventory_id: 1,
        inventur: 1,
        differenz_von_hand: false,
        hat_sollbestand: true,
        zeilen: [{ hausartikel: 11, name: "Artikel C", notiz: "" }],
    });
    // The quantity from before the lock, then the note typed while checking.
    await db.queue.add({
        kind: "quantity",
        position: 55,
        payload: { eingabemenge: "3", eingabemenge_vorher: null },
    });
    await enterNote(1, 11, "Kasten im Keller", false);
}

it("a lock holds the counting, not the check list", async () => {
    // The server takes notes and differences while the count is locked, and
    // that is when they are typed. Until 10/2026 the held quantity in front
    // kept them on the device until the lock opened.
    await reviewWithQueuedQuantity();
    const sent = [];
    globalThis.fetch = (url, options = {}) => {
        sent.push(`${options.method ?? "GET"} ${url}`);
        if (url.includes("/pruefliste/")) {
            return respond({
                zeile: { hausartikel: 11, name: "Artikel C", notiz: "Kasten im Keller" },
            });
        }
        return respond(locked, 409);
    };

    await submit();

    expect(sent).toContain("PATCH /api/v1/inventuren/1/pruefliste/11/");
    const jobs = await pendingJobs();
    expect(jobs.map((job) => [job.kind, job.held != null])).toEqual([["quantity", true]]);
});

it("in preparation everything waits, the check list too", async () => {
    await reviewWithQueuedQuantity();
    const sent = [];
    globalThis.fetch = (url, options = {}) => {
        sent.push(`${options.method ?? "GET"} ${url}`);
        return respond({ ...locked, status: "VORBEREITUNG", gesperrt: false }, 409);
    };

    await submit();

    expect(sent).not.toContain("PATCH /api/v1/inventuren/1/pruefliste/11/");
    const jobs = await pendingJobs();
    expect(jobs.map((job) => job.held != null)).toEqual([true, true]);
});

it("lays the sum in the base unit over the row along with the queued quantity", async () => {
    // "Saved so far" read the server's old sum next to the new number (until 10/2026).
    const stored = {
        wird_gewogen: false,
        gebinde: { id: 2, name: "Kasten", ist_einzeln: false, faktor_in_basiseinheit: "20.000" },
        eingabemenge: "2.000",
        menge_basiseinheit: "40.000",
    };
    await db.inventories.put({ id: 1, haus: "Kino Ost" });
    await db.positions.put({ ...row(55, stored), server_id: 55, location_id: 7 });
    await db.queue.add({
        kind: "quantity",
        position: 55,
        payload: { eingabemenge: "3", eingabemenge_vorher: "2.000" },
    });
    globalThis.fetch = () =>
        respond({ id: 1, haus: "Kino Ost", zaehlpositionen: [row(55, stored)] });

    await fetchPositions(1);

    const seen = await db.positions.get(55);
    expect(seen.eingabemenge).toBe("3");
    expect(seen.menge_basiseinheit).toBe("60.000");
});

/** Without network no answer replaces the sum, so it follows the number at once (10/2026). */
it("keeps the sum in step with a quantity typed without network", async () => {
    const crate = { id: 2, name: "Kasten", faktor_in_basiseinheit: "20.000" };
    const stored = { wird_gewogen: false, gebinde: crate, eingabemenge: "2.000" };
    await db.positions.put({
        ...row(55, { ...stored, menge_basiseinheit: "40.000" }),
        server_id: 55,
        location_id: 7,
    });
    globalThis.fetch = () => Promise.reject(new TypeError("kein Netz"));

    await enterQuantity(await db.positions.get(55), "3");

    expect((await db.positions.get(55)).menge_basiseinheit).toBe("60.000");
});

/**
 * A house in g with the product in kg: the master data names the carton in
 * g. A row stored before 10/2026 still carries the product's 8.
 */
it("sums in the house's unit, also for a row stored with the product's factor", async () => {
    const carton = { id: 42, name: "Karton", faktor_in_basiseinheit: "8.000" };
    const inHouse = { ...carton, faktor_in_basiseinheit: "8000.000" };
    await db.masterData.put({ inventory_id: 1, artikel: [{ id: 11, gebinde: [inHouse] }] });
    await db.positions.put({
        ...row(55, { wird_gewogen: false, gebinde: carton, eingabemenge: "1.000" }),
        server_id: 55,
        location_id: 7,
    });
    globalThis.fetch = () => Promise.reject(new TypeError("kein Netz"));

    try {
        await enterQuantity(await db.positions.get(55), "2");
        expect((await db.positions.get(55)).menge_basiseinheit).toBe("16000.000");
    } finally {
        await db.masterData.clear();
    }
});

it("sends what the device saw with a note, and a second edit keeps the first seen value", async () => {
    await db.inventories.put({ id: 1, haus: "Kino Ost" });
    await db.reviews.put({
        inventory_id: 1,
        inventur: 1,
        differenz_von_hand: false,
        hat_sollbestand: false,
        zeilen: [{ hausartikel: 11, name: "Artikel C", notiz: "vom Server", differenz: null }],
    });
    const sent = [];
    globalThis.fetch = (url, options = {}) => {
        sent.push(JSON.parse(options.body));
        return respond({ zeile: { hausartikel: 11, name: "Artikel C", notiz: sent.at(-1).notiz } });
    };

    // Two edits in a dead spot: only the last goes out, with the first's seen value.
    await enterNote(1, 11, "erster Versuch", false);
    await enterNote(1, 11, "zweiter Versuch", false);
    const [job] = await db.queue.toArray();
    expect(job.payload).toEqual({ notiz: "zweiter Versuch", notiz_vorher: "vom Server" });
    // The row shows the own entry, not the seen value.
    const review = await db.reviews.get(1);
    expect(review.zeilen[0]).toEqual({
        hausartikel: 11,
        name: "Artikel C",
        notiz: "zweiter Versuch",
        differenz: null,
    });

    await submit();

    expect(sent).toEqual([{ notiz: "zweiter Versuch", notiz_vorher: "vom Server" }]);
});

it("does not overwrite a note someone else changed in between", async () => {
    await db.inventories.put({ id: 1, haus: "Kino Ost" });
    await db.reviews.put({
        inventory_id: 1,
        inventur: 1,
        differenz_von_hand: false,
        hat_sollbestand: false,
        zeilen: [{ hausartikel: 11, name: "Artikel C", notiz: "", differenz: null }],
    });
    globalThis.fetch = () =>
        respond(
            {
                detail: "Inzwischen steht dort „zwei Kästen hinten“.",
                konflikt: "notiz",
                zeile: { hausartikel: 11, name: "Artikel C", notiz: "zwei Kästen hinten" },
            },
            409,
        );

    await enterNote(1, 11, "aus dem Funkloch");

    // The server's row stands, the own note is listed with the reason, the queue is clear.
    const review = await db.reviews.get(1);
    expect(review.zeilen[0].notiz).toBe("zwei Kästen hinten");
    expect(await db.queue.count()).toBe(0);
    const [rejected] = await rejectedJobs();
    expect(rejected.kind).toBe("note");
    expect(rejected.value).toBe("aus dem Funkloch");
    expect(rejected.reason).toContain("zwei Kästen hinten");
});

it("takes the server's version of a difference, not its own job's", async () => {
    await db.inventories.put({ id: 1, haus: "Kino Ost" });
    await db.reviews.put({
        inventory_id: 1,
        inventur: 1,
        differenz_von_hand: true,
        hat_sollbestand: false,
        zeilen: [{ hausartikel: 11, name: "Artikel C", notiz: "", differenz: null }],
    });
    globalThis.fetch = (url) =>
        url.includes("/pruefliste/")
            ? respond({
                  zeile: { hausartikel: 11, name: "Artikel C", notiz: "", differenz: "5.000" },
              })
            : respond({});

    await enterDifference(1, 11, "5.0");

    const review = await db.reviews.get(1);
    expect(review.zeilen[0].differenz).toBe("5.000");
});

/**
 * A refused entry of the review list gives the row back as the server has
 * it. Until 10/2026 a refused "Target counts" stayed switched on, a refused
 * difference stood, and the next entry went out with it as seen.
 */
it("gives a row of the review list back as the server has it after a refusal", async () => {
    await db.inventories.put({ id: 1, haus: "Kino Ost" });
    const server = (extra) => ({ hausartikel: 11, name: "Artikel C", notiz: "", ...extra });
    await db.reviews.put({
        inventory_id: 1,
        inventur: 1,
        differenz_von_hand: true,
        hat_sollbestand: false,
        zeilen: [server({ differenz: "-3" }), server({ hausartikel: 12, name: "Ware Z" })],
    });
    const sent = [];
    globalThis.fetch = (url, options = {}) => {
        if (options.method === "PATCH") {
            sent.push(JSON.parse(options.body));
            return respond(
                { detail: ["Eine Differenz von Hand ist hier nicht eingeschaltet."] },
                400,
            );
        }
        // Switched off meanwhile: the server computes the difference again.
        return respond({
            differenz_von_hand: false,
            hat_sollbestand: true,
            zeilen: [server({ differenz: "-2" }), server({ hausartikel: 12, notiz: "neu" })],
        });
    };

    await enterDifference(1, 11, "5");

    const review = await db.reviews.get(1);
    expect(review.zeilen[0].differenz).toBe("-2");
    expect(review.differenz_von_hand).toBe(false);
    // The other rows stay as they were read: the numbers do not move while someone reads.
    expect(review.zeilen[1].notiz).toBe("");
    expect((await rejectedJobs())[0].value).toBe("5");

    // Typed again, the entry carries what the server has, not the refused one.
    globalThis.fetch = (url, options = {}) => {
        sent.push(JSON.parse(options.body));
        return respond({ zeile: server({ differenz: "4.000" }) });
    };
    await enterDifference(1, 11, "4");
    expect(sent.at(-1)).toEqual({ differenz: "4", differenz_vorher: "-2" });
});

it("leaves a refused row of the review list as it is without network", async () => {
    await db.inventories.put({ id: 1, haus: "Kino Ost" });
    await db.reviews.put({
        inventory_id: 1,
        inventur: 1,
        differenz_von_hand: false,
        hat_sollbestand: true,
        zeilen: [{ hausartikel: 11, name: "Artikel C", notiz: "", soll_statt_ist: false }],
    });
    globalThis.fetch = (url, options = {}) =>
        options.method === "PATCH"
            ? respond({ detail: ["Ohne Sollbestand gibt es nichts zu übernehmen."] }, 400)
            : Promise.reject(new TypeError("Funkloch"));

    await enterTargetCounts(1, 11, true);

    expect(await db.queue.count()).toBe(0);
    expect((await db.reviews.get(1)).zeilen[0].soll_statt_ist).toBe(true);
});

/**
 * Broken off by the network, a run has not seen the inventories behind the
 * break. Why one of them waits stays. Until 10/2026 the sync window lost the
 * reason with every dead spot.
 */
it("keeps why an inventory waits when the network breaks off the next run", async () => {
    const why = { reason: "„Kino Ost“ steht auf Vorbereitung.", countingOnly: false };
    await remember("held", { 1: why });
    await db.positions.put({ ...row(55, { wird_gewogen: false }), server_id: 55, location_id: 7 });
    await db.queue.add({
        kind: "quantity",
        position: 55,
        payload: { eingabemenge: "3", eingabemenge_vorher: null },
    });
    globalThis.fetch = () => Promise.reject(new TypeError("Funkloch"));

    await submit();

    expect(await remembered("held")).toEqual({ 1: why });
    expect((await pendingJobs())[0].held).toBe(why.reason);
});
