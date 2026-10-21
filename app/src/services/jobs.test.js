/**
 * What the sync window lists: the jobs that wait, and the questions.
 *
 * A hold noted by an app from before 10/2026 is plain text and holds every
 * job. A switched-off "target counts" names its value too. And a question
 * decided twice, by two taps or in two windows, sends one decision.
 */

import { beforeEach, expect, it } from "vitest";

import { pendingJobs, quantityConflicts, resolveQuantityConflict } from "./sync.js";
import { db, remember } from "./storage.js";
import { t } from "./language.svelte.js";
import { clearStorage } from "../test_support.js";

beforeEach(async () => {
    await clearStorage();
    await remember("access_token", "a");
    await remember("refresh_token", "r");
    globalThis.fetch = () => Promise.reject(new TypeError("kein Netz"));
    await db.positions.put({
        id: 55,
        server_id: 55,
        inventur_id: 1,
        location_id: 7,
        ort: { id: 7, name: "Theke", bereich: "Theke" },
        artikel_id: 11,
        artikel: "Ware N",
        gebinde: null,
        wird_gewogen: false,
        wiegungen: [],
        eingabemenge: "5.000",
        aktiv: true,
        zuletzt_geaendert: "2026-01-31T10:00:00+01:00",
    });
});

it("names a hold noted as plain text before 10/2026 for every job, the check list too", async () => {
    await db.queue.bulkAdd([
        { kind: "quantity", position: 55, payload: { eingabemenge: "4" } },
        { kind: "note", inventory: 1, article: 11, payload: { notiz: "Bruch" } },
    ]);
    await remember("held", { 1: "Die Inventur ist in Vorbereitung." });

    const jobs = await pendingJobs();

    expect(jobs.map((job) => [job.kind, job.held])).toEqual([
        ["quantity", "Die Inventur ist in Vorbereitung."],
        ["note", "Die Inventur ist in Vorbereitung."],
    ]);
});

it("names the value of a switched-off 'target counts'", async () => {
    await db.queue.add({
        kind: "target",
        inventory: 1,
        article: 11,
        payload: { soll_statt_ist: false },
    });

    const [job] = await pendingJobs();

    expect([job.kind, job.value]).toEqual(["target", t("job.off")]);
});

it("sends one decision when a question is decided twice", async () => {
    await remember("quantity_conflicts", [
        {
            id: "q-1",
            position: 55,
            inventoryId: 1,
            label: "Ware N · Theke",
            mine: "3",
            theirs: "5.000",
            pack: null,
            at: "2026-01-31T10:00:00.000Z",
        },
    ]);

    await resolveQuantityConflict("q-1", "add");
    await resolveQuantityConflict("q-1", "add");

    expect(await quantityConflicts()).toEqual([]);
    const jobs = await db.queue.toArray();
    expect(jobs.map((job) => [job.kind, job.payload])).toEqual([
        ["quantity", { eingabemenge: "8", eingabemenge_vorher: "5.000" }],
    ]);
});
