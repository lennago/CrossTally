/**
 * An expired sign-in with a number still waiting: the login form says so,
 * and after signing in the number goes out.
 *
 * The sign-in expires here by hand: the stored tokens are overwritten in
 * the device's storage, so the server answers 401 to both of them, exactly
 * as after a new password or a revoked token. Nothing on the server changes.
 */

import { expect, test } from "@playwright/test";

import {
    ACCOUNT,
    articleHeading,
    openArticles,
    openCountable,
    rowTitled,
    signOut,
    text,
    watchErrors,
} from "./helpers.js";

/** Overwrite both tokens in the app's storage ('storage.js', table 'state'). */
async function expireTokens(page) {
    await page.evaluate(
        () =>
            new Promise((done, fail) => {
                const opening = indexedDB.open("crosstally");
                opening.onerror = () => fail(opening.error);
                opening.onsuccess = () => {
                    const db = opening.result;
                    const tx = db.transaction("state", "readwrite");
                    const state = tx.objectStore("state");
                    state.put({ key: "access_token", value: "abgelaufen" });
                    state.put({ key: "refresh_token", value: "abgelaufen" });
                    tx.oncomplete = () => {
                        db.close();
                        done();
                    };
                    tx.onerror = () => fail(tx.error);
                };
            }),
    );
}

test("an expired sign-in keeps the waiting number and sends it after signing in", async ({
    page,
}) => {
    const errors = watchErrors(page, { allowNetwork: true });
    await openArticles(page);
    const counted = await openCountable(page);
    const state = page.locator("button.sync-state");

    await test.step("counted without network", async () => {
        await page.context().setOffline(true);
        await page.locator(".count-row input").first().fill("4");
        await page.locator("button.next").click();
        await expect(articleHeading(page)).not.toHaveText(counted);
        await page.locator("button.back").click();
        await expect(state.locator(".count")).toHaveText("1");
    });

    await test.step("the sign-in expires meanwhile", async () => {
        await expireTokens(page);
        // Back online the app syncs by itself, runs into the 401 and shows the form.
        await page.context().setOffline(false);
        await expect(page.getByRole("heading", { name: text("login.title") })).toBeVisible({
            timeout: 25_000,
        });
        // The storage stays, and the form says what still lies here.
        await expect(page.locator(".hint")).toContainText("1 Eingabe wartet noch");
    });

    await test.step("signing in anew sends it", async () => {
        await page.getByLabel(text("login.username")).fill(ACCOUNT.username);
        await page.getByLabel(text("login.password")).fill(ACCOUNT.password);
        await page.getByRole("button", { name: text("login.button"), exact: true }).click();
        await expect(page.locator("button.row").first()).toBeVisible();
        await expect(state.locator(".count")).toBeHidden();
    });

    await test.step("a fresh sign-in shows it from the server", async () => {
        await signOut(page);
        await openArticles(page);
        await rowTitled(page, counted).first().click();
        await expect(articleHeading(page)).toHaveText(counted);
        await expect(page.locator(".count-row input").first()).toHaveValue("4");
    });

    expect(errors).toEqual([]);
});
