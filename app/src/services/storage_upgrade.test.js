/**
 * An app from before 10/2026 holds rows that name the old scope instead of
 * their inventory type. After the update they come anew, and nothing
 * counted is lost.
 */

import Dexie from "dexie";
import { expect, it } from "vitest";

import { SYNC_PREFIX, db } from "./storage.js";

const BEFORE = {
    state: "key",
    inventories: "id",
    positions: "id, inventur_id, location_id, artikel_id, server_id",
    masterData: "inventory_id",
    queue: "++number",
};

it("fetches every row anew after the update and keeps what is not sent", async () => {
    db.close();
    await Dexie.delete("crosstally");
    const before = new Dexie("crosstally");
    before.version(1).stores(BEFORE);
    await before.table("state").bulkPut([
        { key: `${SYNC_PREFIX}1`, value: "2026-09-30T10:00:00Z" },
        { key: "last_synced", value: "2026-09-30T10:00:00Z" },
    ]);
    await before.table("positions").put({ id: -1, inventur_id: 1, nur_jahr: true });
    await before.table("queue").add({ kind: "position", position: -1 });
    await before.table("masterData").put({ inventory_id: 1, artikel: [] });
    before.close();

    await db.open();

    expect(await db.state.get(`${SYNC_PREFIX}1`)).toBeUndefined();
    expect((await db.state.get("last_synced")).value).toBe("2026-09-30T10:00:00Z");
    expect(await db.positions.get(-1)).toBeTruthy();
    expect(await db.queue.count()).toBe(1);
    // Offline the old master data still serves. Online it comes anew ('refresh').
    expect(await db.masterData.get(1)).toBeTruthy();
});
