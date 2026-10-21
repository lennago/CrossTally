/**
 * Counting without network, as it happens in storage.
 *
 * The app's promise: what is counted in a dead spot waits in the device and
 * goes out by itself once there is network again. jsdom knows neither a
 * network switch nor the 'online' event, so this can only be shown here.
 */

import { expect, test } from "@playwright/test";

import {
    articleHeading,
    openArticles,
    openCountable,
    rowTitled,
    signIn,
    signOut,
    text,
    watchErrors,
} from "./helpers.js";

test("a quantity counted without network reaches the server later", async ({ page }) => {
    const errors = watchErrors(page, { allowNetwork: true });
    await openArticles(page);
    const counted = await openCountable(page);
    const state = page.locator("button.sync-state");

    await test.step("without network the quantity waits in the device", async () => {
        await page.context().setOffline(true);
        await page.locator(".count-row input").first().fill("3");
        await page.locator("button.next").click();
        await expect(articleHeading(page)).not.toHaveText(counted);
        // The icon stands in the list, not in the form. The number of waiting
        // jobs shows at once. The stroke follows with the next sync, which
        // runs every twenty seconds or on "Refresh".
        await page.locator("button.back").click();
        await expect(state.locator(".count")).toHaveText("1");
        await page.getByRole("button", { name: text("header.refresh") }).click();
        await expect(state.locator("svg.offline")).toBeVisible();
    });

    await test.step("with network it goes out by itself", async () => {
        await page.context().setOffline(false);
        await expect(state.locator(".count")).toBeHidden();
        await expect(state.locator("svg.offline")).toBeHidden();
    });

    await test.step("a fresh sign-in shows it from the server", async () => {
        // Signing out clears the storage: whatever shows afterwards came from the server.
        await signOut(page);
        await openArticles(page);
        await rowTitled(page, counted).first().click();
        await expect(articleHeading(page)).toHaveText(counted);
        await expect(page.locator(".count-row input").first()).toHaveValue("3");
    });

    expect(errors).toEqual([]);
});

test("the app starts without network from what the device holds", async ({ page }) => {
    // The walk into storage begins at the door, where there is no reception
    // either. The service worker holds the app, the storage holds the houses.
    const errors = watchErrors(page, { allowNetwork: true });
    await signIn(page);
    await page.waitForFunction(
        async () =>
            (await navigator.serviceWorker.getRegistration())?.active?.state === "activated",
    );

    await page.context().setOffline(true);
    await page.goto("/app/");

    await expect(page.locator("button.row").first()).toBeVisible();
    await expect(page.getByRole("heading", { name: text("login.title") })).toBeHidden();
    await expect(page.locator("button.sync-state svg.offline")).toBeVisible();
    expect(errors).toEqual([]);
});
