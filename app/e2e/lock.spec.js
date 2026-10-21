/**
 * A number counted in a dead spot meets a lock, and goes out once it opens.
 *
 * The manager locks the count for the check while a counter still holds a
 * number on a device without reception. The number must not get lost and
 * must not go in behind the lock: it waits, the app says so, and once the
 * manager opens the count again it arrives. The lock itself goes through the
 * API as the website would set it.
 */

import { expect, test } from "@playwright/test";

import { articleHeading, openArticles, openCountable, text, watchErrors } from "./helpers.js";

// The names and accounts below come from the demo stock ('manage.py demo').
test.skip(!!process.env.E2E_BASE_URL, "needs the demo stock");

const MANAGER = { username: "leitung", password: "demo1234" };

async function managerHeaders(request) {
    const answer = await request.post("/api/v1/token/", { data: MANAGER });
    expect(answer.ok()).toBeTruthy();
    return { Authorization: `Bearer ${(await answer.json()).access}` };
}

/** The running inventory of Kino Nord that is not locked: the one the counter walks into. */
async function openInventory(request, headers) {
    const inventories = await (await request.get("/api/v1/inventuren/", { headers })).json();
    return inventories.find((entry) => entry.haus === "Kino Nord" && !entry.gesperrt);
}

async function setLock(request, headers, id, on) {
    const answer = await request.post(`/api/v1/inventuren/${id}/sperre/`, {
        headers,
        data: { an: on },
    });
    expect(answer.ok()).toBeTruthy();
}

test("a quantity from a dead spot waits for the lock and arrives once it opens", async ({
    page,
    request,
}) => {
    const errors = watchErrors(page, { allowNetwork: true });
    const headers = await managerHeaders(request);
    const inventory = await openInventory(request, headers);
    await openArticles(page);
    const counted = await openCountable(page);
    const state = page.locator("button.sync-state");

    try {
        await test.step("counted without network", async () => {
            await page.context().setOffline(true);
            await page.locator(".count-row input").first().fill("7");
            await page.locator("button.next").click();
            await expect(articleHeading(page)).not.toHaveText(counted);
            await page.locator("button.back").click();
            await expect(state.locator(".count")).toHaveText("1");
        });

        await test.step("the manager locks the count in the meantime", async () => {
            await setLock(request, headers, inventory.id, true);
        });

        await test.step("with network the number waits, and the app says why", async () => {
            await page.context().setOffline(false);
            await page.getByRole("button", { name: text("header.refresh") }).click();
            // Still one waiting: the lock holds it. The sync dialog names the reason.
            await expect(state.locator(".count")).toHaveText("1");
            await state.click();
            await expect(page.getByText(/^Wartet: /)).toBeVisible();
            await page.getByRole("button", { name: text("sync.close") }).click();
        });

        await test.step("once the count is open again, the number goes out", async () => {
            await setLock(request, headers, inventory.id, false);
            await page.getByRole("button", { name: text("header.refresh") }).click();
            await expect(state.locator(".count")).toBeHidden();
            const detail = await (
                await request.get(`/api/v1/inventuren/${inventory.id}/`, { headers })
            ).json();
            const row = detail.zaehlpositionen.find(
                (entry) => entry.artikel === counted && Number(entry.eingabemenge) === 7,
            );
            expect(row).toBeTruthy();
        });
    } finally {
        // Open for the cases after this one, whatever happened above.
        await setLock(request, headers, inventory.id, false);
    }
    expect(errors).toEqual([]);
});
