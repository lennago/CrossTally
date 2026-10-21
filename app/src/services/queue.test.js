/**
 * The queue runs once at a time, and a run that fails does not hold it.
 *
 * Every entry asks for a run, and so do the clock and the network coming
 * back. A second run while the first is on its way would send the same job
 * twice. And a run that ended in an error must not leave the queue
 * believing it still runs: nothing would ever go out again.
 */

import { beforeEach, expect, it, vi } from "vitest";

import { submit } from "./sync.js";
import { db, remember } from "./storage.js";
import { clearStorage, respond } from "../test_support.js";

const row = (extra = {}) => ({
    id: 55,
    inventur_id: 1,
    ort: { id: 7, name: "Theke", bereich: "Theke" },
    artikel_id: 11,
    artikel: "Ware N",
    gebinde: null,
    wird_gewogen: false,
    wiegungen: [],
    eingabemenge: null,
    aktiv: true,
    zuletzt_geaendert: "2026-01-31T10:00:00+01:00",
    ...extra,
});

const QUANTITY = {
    kind: "quantity",
    position: 55,
    payload: { eingabemenge: "4", eingabemenge_vorher: null },
};

/** A gate a fake answer waits at until the case lets it through. */
function gate() {
    let open;
    const passed = new Promise((resolve) => (open = resolve));
    return { passed, open };
}

beforeEach(async () => {
    await clearStorage();
    await remember("access_token", "a");
    await remember("refresh_token", "r");
    await db.positions.put({ ...row(), server_id: 55, location_id: 7 });
    await db.queue.add(QUANTITY);
});

it("runs once at a time: a second start sends nothing and says what waits", async () => {
    const held = gate();
    const sent = [];
    globalThis.fetch = async (url, options = {}) => {
        sent.push(`${options.method ?? "GET"} ${url}`);
        await held.passed;
        return await respond(row({ eingabemenge: "4.000" }));
    };

    const first = submit();
    await vi.waitFor(() => expect(sent).toHaveLength(1));
    expect(await submit()).toEqual({ sent: 0, pending: 1 });
    held.open();

    expect(await first).toEqual({ sent: 1, pending: 0 });
    expect(sent).toEqual(["PATCH /api/v1/zaehlpositionen/55/"]);
});

it("runs again after a run that ended in an error", async () => {
    globalThis.fetch = () => respond(row({ eingabemenge: "4.000" }));
    // Striking the job fails, say because the storage is full.
    const strike = vi.spyOn(db.queue, "delete").mockRejectedValueOnce(new Error("Speicher voll"));

    await expect(submit()).rejects.toThrow("Speicher voll");
    strike.mockRestore();

    expect(await submit()).toEqual({ sent: 1, pending: 0 });
});
