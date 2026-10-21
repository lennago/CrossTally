/**
 * The app and the website share the sign-in, because both lie under one origin.
 *
 * Whoever is signed in on the website opens the app without typing a
 * password. Signing out in the app ends the website's session too, otherwise
 * the app would fetch a token again at once on the next start. With
 * 'npm run dev' on port 5173 none of this works, so only here it shows.
 */

import { expect, test } from "@playwright/test";

import { signOut, text, watchErrors } from "./helpers.js";

// The names and accounts below come from the demo stock ('manage.py demo').
// Against another server ('E2E_BASE_URL') this does not hold.
test.skip(!!process.env.E2E_BASE_URL, "needs the demo stock");

//: The demo's manager, see DEMO.md: the web interface and counting in both running inventories.
const MANAGER = { username: "leitung", password: "demo1234" };

test("the app takes over the website's sign-in and the sign-out", async ({ page }) => {
    const errors = watchErrors(page);

    await test.step("signed in on the website", async () => {
        await page.goto("/accounts/login/");
        await page.locator('input[name="username"]').fill(MANAGER.username);
        await page.locator('input[name="password"]').fill(MANAGER.password);
        await page.getByRole("button", { name: "Anmelden", exact: true }).click();
        await expect(page).not.toHaveURL(/\/accounts\/login\//);
    });

    await test.step("the app asks for no password", async () => {
        await page.goto("/app/");
        await expect(page.locator("button.row").first()).toBeVisible();
        await expect(page.getByRole("heading", { name: text("login.title") })).toBeHidden();
    });

    await test.step("signing out in the app signs out the website", async () => {
        await signOut(page);
        await page.goto("/");
        await expect(page).toHaveURL(/\/accounts\/login\//);
    });

    expect(errors).toEqual([]);
});
