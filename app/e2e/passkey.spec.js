/**
 * A passkey made in the app, and the sign-in with it. On the website, the
 * password that a new key asks for before the device's dialog. Removing a key
 * asks for the password as well, in the app and on the website.
 *
 * The device is Chromium's virtual authenticator, driven over the DevTools
 * protocol: it holds the key, verifies the person at once and asks no
 * questions. The address has to be 'localhost': WebAuthn ties a key to a
 * domain, and an IP address is none ('playwright.config.js').
 */

import { expect, test } from "@playwright/test";

import { ACCOUNT, signIn, signInWebsite, signOut, text, watchErrors } from "./helpers.js";

//: The demo's manager, see DEMO.md.
const MANAGER = { username: "leitung", password: "demo1234" };

/** The virtual device, and how many keys it holds. */
async function device(page) {
    const devtools = await page.context().newCDPSession(page);
    await devtools.send("WebAuthn.enable", { enableUI: false });
    const { authenticatorId } = await devtools.send("WebAuthn.addVirtualAuthenticator", {
        options: {
            protocol: "ctap2",
            transport: "internal",
            hasResidentKey: true,
            hasUserVerification: true,
            isUserVerified: true,
            automaticPresenceSimulation: true,
        },
    });
    return async () =>
        (await devtools.send("WebAuthn.getCredentials", { authenticatorId })).credentials.length;
}

test("a counter creates a passkey in the app and signs in with it", async ({ page }) => {
    const errors = watchErrors(page);
    await device(page);

    await test.step("the key arises in the account menu", async () => {
        await signIn(page);
        await page.locator("button.account").click();
        await page.getByRole("button", { name: text("account.passkeys") }).click();
        const dialog = page.getByRole("dialog", { name: text("passkey.title") });
        await expect(dialog.getByText(text("passkey.none"))).toBeVisible();
        await dialog.getByLabel(text("passkey.name")).fill("Testgerät");
        // Asked before the device's dialog, as on the website.
        await dialog.getByLabel(text("passkey.password")).fill(ACCOUNT.password);
        await dialog.getByRole("button", { name: text("passkey.create") }).click();
        await expect(dialog.getByText("Testgerät", { exact: true })).toBeVisible();
        await dialog.getByRole("button", { name: text("passkey.close") }).click();
    });

    await test.step("signed out, the key alone signs in again", async () => {
        await signOut(page);
        await page.getByRole("button", { name: text("login.passkey") }).click();
        await expect(page.locator("button.row").first()).toBeVisible();
        await expect(page.getByRole("heading", { name: text("login.title") })).toBeHidden();
    });

    await test.step("the key is removed again, so a second run finds a clean account", async () => {
        await page.locator("button.account").click();
        await page.getByRole("button", { name: text("account.passkeys") }).click();
        const dialog = page.getByRole("dialog", { name: text("passkey.title") });
        await dialog.getByRole("button", { name: text("passkey.remove") }).click();
        await dialog.getByLabel(text("passkey.remove_password")).fill(ACCOUNT.password);
        await dialog.getByRole("button", { name: text("passkey.remove_confirm") }).click();
        await expect(dialog.getByText(text("passkey.none"))).toBeVisible();
    });

    expect(errors).toEqual([]);
});

test("the website asks for the password before the device's dialog", async ({ page }) => {
    test.skip(!!process.env.E2E_BASE_URL, "needs the demo stock");
    // A refusal is a 400 or 429 in the console.
    const errors = watchErrors(page, { allowNetwork: true });
    const stored = await device(page);
    await signInWebsite(page, MANAGER);
    await page.goto("/accounts/passkeys/");
    const password = page.getByLabel("Dein Passwort");
    const create = page.getByRole("button", { name: "Passkey anlegen" });
    const message = page.locator("[data-passkey-meldung]");

    await test.step("a wrong password stops before the device", async () => {
        await password.fill("daneben");
        await create.click();
        await expect(message).toHaveText("Das Passwort stimmt nicht.");
        expect(await stored()).toBe(0);
    });

    await test.step("the lock after too many says so", async () => {
        // Answered here: five real ones would lock the demo's manager for the tests after this.
        const options = "**/accounts/passkeys/add/options/";
        await page.route(options, (route) =>
            route.fulfill({ status: 429, contentType: "text/html", body: "<p>gesperrt</p>" }),
        );
        await create.click();
        await expect(message).toHaveText(
            "Zu viele Fehlversuche. Dieses Konto ist von hier aus vorübergehend gesperrt. " +
                "Bitte später noch einmal versuchen.",
        );
        await page.unroute(options);
    });

    await test.step("the right one leads to the device and to the key", async () => {
        await page.locator("[data-passkey-name]").fill("Testgerät");
        await password.fill(MANAGER.password);
        await create.click();
        const key = page.locator("li").filter({ hasText: "Testgerät" });
        await expect(key).toBeVisible();
        expect(await stored()).toBe(1);

        // Removed again, so a second run finds a clean account.
        await key.getByLabel("Dein Passwort").fill(MANAGER.password);
        page.once("dialog", (dialog) => dialog.accept());
        await key.getByRole("button", { name: "Entfernen" }).click();
        await expect(key).toHaveCount(0);
    });
    expect(errors).toEqual([]);
});
