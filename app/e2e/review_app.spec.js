/**
 * The review list in the counting app, against the demo stock.
 *
 * Counters see it too: the difference large per article, the note behind a
 * pen. In the cup inventory of Kino Nord the difference comes by hand
 * ('server/demo/inventories.py'), and there it is a field. Only a manager
 * switches that, and not where a target stock came in. The cup inventory is
 * locked as well: it opens on its review list, and notes and differences go
 * on there. "Target counts" stands at the cola syrup of Kino Nord, and only a
 * manager switches it.
 */

import { expect, test } from "@playwright/test";

import { ACCOUNT, signIn, signOut, text, watchErrors } from "./helpers.js";

// The names and accounts below come from the demo stock ('manage.py demo').
test.skip(!!process.env.E2E_BASE_URL, "needs the demo stock");

const MANAGER = { username: "leitung", password: "demo1234" };
const RUN = "Monatsinventur \\+ Becherinventur";
const CUPS = "Becherinventur";

/** A text of the app with its placeholders filled. */
function said(key, values) {
    return Object.entries(values).reduce(
        (result, [name, value]) => result.replace(`{${name}}`, value),
        text(key),
    );
}

/** The inventory row of a house whose types read exactly like this. */
function inventoryOf(page, house, types) {
    return page
        .locator("button.row")
        .filter({ hasText: house })
        .filter({ hasText: new RegExp(`· ${types} ·`) });
}

/** The review list of an inventory. A locked one opens on it, the others lead there. */
async function openReview(page, house, types) {
    await inventoryOf(page, house, types).click();
    const entry = page.locator("button.row").filter({ hasText: text("review.entry") });
    await expect(entry.or(page.locator("li.row")).first()).toBeVisible();
    if (await entry.count()) await entry.click();
    await expect(page.locator("li.row").first()).toBeVisible();
}

function rowNamed(page, name) {
    return page.locator("li.row").filter({ has: page.getByText(name, { exact: true }) });
}

/** Waits for the change of a row to reach the server. */
function sent(page) {
    return page.waitForResponse(
        (answer) => answer.url().includes("/pruefliste/") && answer.request().method() === "PATCH",
    );
}

test.describe("without network", () => {
    // 'request' reports every call of the page only without the service worker.
    test.use({ serviceWorkers: "block" });

    /**
     * Opened without network, the review list waits for the next sync. Until
     * 10/2026 it asked again at once, hundreds of times a second, on the
     * areas too, and the phone ran hot in the store.
     */
    test("the review list opened without network asks once, not again and again", async ({
        page,
    }) => {
        const errors = watchErrors(page, { allowNetwork: true });
        await signIn(page, ACCOUNT);
        await inventoryOf(page, "Kino Nord", RUN).click();
        const entry = page.locator("button.row").filter({ hasText: text("review.entry") });
        await expect(entry).toBeVisible();
        const asked = [];
        page.on("request", (request) => {
            if (new URL(request.url()).pathname.startsWith("/api/")) asked.push(request.url());
        });

        await page.context().setOffline(true);
        await entry.click();
        await page.waitForTimeout(2000);
        await page.locator("button.back").click();
        await page.waitForTimeout(1000);
        await page.context().setOffline(false);

        // A sync on opening, perhaps one more: not hundreds.
        expect(asked.length).toBeLessThan(10);
        expect(errors).toEqual([]);
    });
});

test("counters see the list and keep a note behind the pen", async ({ page }) => {
    const errors = watchErrors(page);
    await signIn(page, ACCOUNT);
    await openReview(page, "Kino Nord", RUN);

    // A target came in. Counters get no switch, but the whole list.
    await expect(page.locator(".manual input[type=checkbox]")).toHaveCount(0);
    await page.getByRole("button", { name: text("review.deviating") }).click();
    const name = await page.locator("li.row .name strong").first().innerText();
    const row = rowNamed(page, name);
    await expect(row.locator(".difference strong")).not.toHaveText("–");

    await row.getByRole("button", { name: said("review.note_of", { name }), exact: true }).click();
    await row.locator("textarea").fill("zwei Kästen hinten im Lager");
    const answer = sent(page);
    await row.getByRole("button", { name: text("review.note_save") }).click();
    await answer;
    // The note does not stand in the list, only the × says there is one.
    await expect(row.locator("textarea")).toHaveCount(0);
    await expect(row).not.toContainText("zwei Kästen hinten im Lager");
    const remove = said("review.note_remove", { name });
    await expect(row.getByRole("button", { name: remove })).toBeVisible();

    // Fetched anew, it comes from the server.
    await page.reload();
    await expect(rowNamed(page, name).getByRole("button", { name: remove })).toBeVisible();
    await rowNamed(page, name)
        .getByRole("button", { name: said("review.note_of", { name }), exact: true })
        .click();
    await expect(rowNamed(page, name).locator("textarea")).toHaveValue(
        "zwei Kästen hinten im Lager",
    );
    expect(errors).toEqual([]);
});

test("a note typed without network reaches the server later", async ({ page, request }) => {
    const errors = watchErrors(page, { allowNetwork: true });
    await signIn(page, ACCOUNT);
    await openReview(page, "Kino Nord", RUN);
    await page.getByRole("button", { name: text("review.deviating") }).click();
    const name = await page.locator("li.row .name strong").last().innerText();
    const row = rowNamed(page, name);
    const state = page.locator("button.sync-state");

    await page.context().setOffline(true);
    await row.getByRole("button", { name: said("review.note_of", { name }), exact: true }).click();
    await row.locator("textarea").fill("aus dem Funkloch");
    await row.getByRole("button", { name: text("review.note_save") }).click();
    // Shown at once, waiting on the device: the × says there is a note.
    const remove = said("review.note_remove", { name });
    await expect(row.getByRole("button", { name: remove })).toBeVisible();
    await expect(state.locator(".count")).toHaveText("1");

    const answer = sent(page);
    await page.context().setOffline(false);
    await answer;
    await expect(state.locator(".count")).toBeHidden();

    // On the server, as the website reads it.
    const token = await request.post("/api/v1/token/", { data: ACCOUNT });
    const headers = { Authorization: `Bearer ${(await token.json()).access}` };
    const inventories = await (await request.get("/api/v1/inventuren/", { headers })).json();
    const inventory = inventories.find(
        (entry) =>
            entry.haus === "Kino Nord" &&
            /^Monatsinventur \+ Becherinventur$/.test(entry.arten_anzeige ?? ""),
    );
    const list = await (
        await request.get(`/api/v1/inventuren/${inventory.id}/pruefliste/`, { headers })
    ).json();
    const stored = list.zeilen.find((entry) => entry.name === name);
    expect(stored.notiz).toBe("aus dem Funkloch");

    // Cleared again, for the cases after this one.
    await request.patch(`/api/v1/inventuren/${inventory.id}/pruefliste/${stored.hausartikel}/`, {
        headers,
        data: { notiz: "" },
    });
    expect(errors).toEqual([]);
});

test("in the cup inventory the difference is typed in", async ({ page }) => {
    const errors = watchErrors(page);
    await signIn(page, ACCOUNT);
    await openReview(page, "Kino Nord", CUPS);

    await expect(rowNamed(page, "Mehrwegbecher 0,3 l").locator(".difference input")).toHaveValue(
        "-12",
    );
    // Added in the app, and still without its difference.
    const field = rowNamed(page, "Becher 0,4 l").locator(".difference input");
    await expect(field).toHaveValue("");
    // Only digits, as on the number pad of a phone. The minus comes from ±.
    await field.fill("7");
    const answer = sent(page);
    await rowNamed(page, "Becher 0,4 l")
        .getByRole("button", { name: said("review.sign_of", { name: "Becher 0,4 l" }) })
        .click();
    await expect(field).toHaveValue("-7");
    await expect(field).toBeFocused();
    await answer;

    await page.reload();
    await expect(rowNamed(page, "Becher 0,4 l").locator(".difference input")).toHaveValue("-7");
    expect(errors).toEqual([]);
});

test("a difference still in its field survives the app going to the background", async ({
    page,
}) => {
    const errors = watchErrors(page);
    await signIn(page, ACCOUNT);
    await openReview(page, "Kino Nord", CUPS);

    // Typed, and the cursor stays in the field: no 'change' comes.
    const field = rowNamed(page, "Mehrwegbecher 0,5 l").locator(".difference input");
    await field.fill("-31");
    const answer = sent(page);
    await page.evaluate(() => {
        Object.defineProperty(document, "visibilityState", {
            configurable: true,
            get: () => "hidden",
        });
        document.dispatchEvent(new Event("visibilitychange"));
    });
    await answer;
    await page.evaluate(() => delete document.visibilityState);

    await page.reload();
    await expect(rowNamed(page, "Mehrwegbecher 0,5 l").locator(".difference input")).toHaveValue(
        "-31",
    );
    expect(errors).toEqual([]);
});

test("only a manager switches, and not where a target stock came in", async ({ page }) => {
    const errors = watchErrors(page);
    await signIn(page, MANAGER);
    await openReview(page, "Kino Nord", RUN);
    // A target stock came in: nothing to switch, the box is not even there.
    await expect(page.locator("li.row").first()).toBeVisible();
    await expect(page.locator(".manual input[type=checkbox]")).toHaveCount(0);

    await page.getByRole("button", { name: text("header.back") }).click();
    await page.getByRole("button", { name: text("header.back") }).click();
    await openReview(page, "Kino Nord", CUPS);
    const box = page.locator(".manual input[type=checkbox]");
    await expect(box).toBeChecked();

    // Off: the fields go, and what was typed stays stored.
    await box.uncheck();
    await expect(page.locator("li.row .difference input")).toHaveCount(0);
    await box.check();
    await expect(rowNamed(page, "Mehrwegbecher 0,3 l").locator(".difference input")).toHaveValue(
        "-12",
    );
    expect(errors).toEqual([]);
});

test("a locked inventory opens on its review list, and a manager opens it", async ({ page }) => {
    const errors = watchErrors(page);
    await signIn(page, ACCOUNT);
    await openReview(page, "Kino Nord", CUPS);
    // No areas in between: the inventory opened on its list.
    const banner = page.getByText(text("review.locked"));
    await expect(banner).toBeVisible();
    await expect(page.getByRole("button", { name: text("review.unlock") })).toHaveCount(0);
    await page.getByRole("button", { name: text("header.back") }).click();
    await expect(inventoryOf(page, "Kino Nord", CUPS)).toContainText(text("inventories.locked"));

    await signOut(page);
    await signIn(page, MANAGER);
    await openReview(page, "Kino Nord", CUPS);
    await page.getByRole("button", { name: text("review.unlock") }).click();
    await expect(banner).toHaveCount(0);
    // Locked again, as the other cases expect it.
    await page.getByRole("button", { name: text("review.lock") }).click();
    await expect(banner).toBeVisible();
    expect(errors).toEqual([]);
});

test("a manager switches 'target counts', counters only see it", async ({ page }) => {
    const errors = watchErrors(page);
    await signIn(page, MANAGER);
    await openReview(page, "Kino Nord", RUN);
    // The demo set it on the cola syrup ('SOLL_ZAEHLT' in 'server/demo/inventories.py').
    const syrup = page
        .locator("li.row")
        .filter({ hasText: said("review.number", { number: "101006" }) });
    const button = syrup.getByRole("button", { name: /^Soll zählt bei / });
    await expect(button).toHaveAttribute("aria-pressed", "true");

    let answer = sent(page);
    await button.click();
    await answer;
    await expect(button).toHaveAttribute("aria-pressed", "false");
    // Fetched anew, it comes from the server.
    await page.reload();
    await expect(button).toHaveAttribute("aria-pressed", "false");

    // Back as the demo had it, for the other cases and the next run.
    answer = sent(page);
    await button.click();
    await answer;
    await expect(button).toHaveAttribute("aria-pressed", "true");

    await page.getByRole("button", { name: text("header.back") }).click();
    await page.getByRole("button", { name: text("header.back") }).click();
    await signOut(page);
    await signIn(page, ACCOUNT);
    await openReview(page, "Kino Nord", RUN);
    await expect(syrup).toContainText(text("review.target_counts"));
    await expect(page.getByRole("button", { name: /^Soll zählt bei / })).toHaveCount(0);
    expect(errors).toEqual([]);
});
