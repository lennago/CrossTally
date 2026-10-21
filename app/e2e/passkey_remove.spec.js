/**
 * Removing a passkey on the website asks what creating one asks: the
 * password with the step, or without one a confirmation with one of the
 * account's passkeys first. Until 10/2026 the session alone removed every
 * key, and whoever found it open kept it while the owner was signed out.
 *
 * The device is Chromium's virtual authenticator, driven over the DevTools
 * protocol, as in 'passkey.spec.js'. The demo has no account without a
 * password that signs in, so the second test makes one in the
 * administration and deletes it again at the end.
 */

import { expect, test } from "@playwright/test";

import { signInWebsite, watchErrors } from "./helpers.js";

//: The demo's manager and administrator, see DEMO.md.
const MANAGER = { username: "leitung", password: "demo1234" };
const ADMIN = { username: "admin", password: "demo1234" };

//: The account of the second test, which exists only while it runs. Its password is
//: invented and holds only until the administration switches it off.
const WITHOUT = {
    username: "e2e-ohne-passwort",
    password: "Ein-Passwort-nur-zum-Probieren-7", // gitleaks:allow
};

/** The virtual device: it holds the key and verifies the person at once. */
async function device(page) {
    const devtools = await page.context().newCDPSession(page);
    await devtools.send("WebAuthn.enable", { enableUI: false });
    await devtools.send("WebAuthn.addVirtualAuthenticator", {
        options: {
            protocol: "ctap2",
            transport: "internal",
            hasResidentKey: true,
            hasUserVerification: true,
            isUserVerified: true,
            automaticPresenceSimulation: true,
        },
    });
}

/** A passkey made on the passkey page, with the password the page asks for first. */
async function createKey(page, name, password) {
    await page.goto("/accounts/passkeys/");
    await page.locator("[data-passkey-name]").fill(name);
    await page.locator("[data-passkey-passwort]").fill(password);
    await page.getByRole("button", { name: "Passkey anlegen" }).click();
    const key = page.locator("li").filter({ hasText: name });
    await expect(key).toBeVisible();
    return key;
}

/** Press "Entfernen" on the key and accept the question before it. */
async function remove(page, key) {
    page.once("dialog", (dialog) => dialog.accept());
    await key.getByRole("button", { name: "Entfernen" }).click();
    await page.waitForLoadState("domcontentloaded");
}

test("the website asks for the password before it removes a passkey", async ({ page }) => {
    test.skip(!!process.env.E2E_BASE_URL, "needs the demo stock");
    const errors = watchErrors(page);
    await device(page);
    await signInWebsite(page, MANAGER);
    const key = await createKey(page, "Zum Entfernen", MANAGER.password);
    const password = key.getByLabel("Dein Passwort");
    const refusal = key.locator(".invalid-feedback");

    await test.step("without the password the key stays", async () => {
        await remove(page, key);
        await expect(refusal).toHaveText("Bitte gib dein Passwort ein.");
        await expect(password).toHaveAttribute("aria-invalid", "true");
        await expect(key).toHaveCount(1);
    });

    await test.step("a wrong one stays at the field as well", async () => {
        await password.fill("daneben");
        await remove(page, key);
        await expect(refusal).toHaveText("Das Passwort stimmt nicht.");
        await expect(key).toHaveCount(1);
    });

    await test.step("with the right one it goes, and this page stays signed in", async () => {
        await password.fill(MANAGER.password);
        await remove(page, key);
        await expect(page.getByText("Passkey „Zum Entfernen“ ist entfernt.")).toBeVisible();
        await expect(key).toHaveCount(0);
    });
    expect(errors).toEqual([]);
});

test("without a password a passkey confirms the removal", async ({ browser, page }) => {
    test.skip(!!process.env.E2E_BASE_URL, "needs the demo stock");
    const errors = watchErrors(page);
    await device(page);
    // The administration in a browser of its own: its session is not the person's.
    const administration = await browser.newPage();
    await signInWebsite(administration, ADMIN);
    // In the account menu, closed: its address is all the test needs ('ADMIN_URL').
    const adminIndex = await administration
        .getByRole("link", { name: "Verwaltung", exact: true, includeHidden: true })
        .getAttribute("href");

    let account;
    await test.step("the administration makes a manager with a password", async () => {
        await administration.goto(`${adminIndex}accounts/customuser/add/`);
        await administration.locator("input[name=username]").fill(WITHOUT.username);
        await administration.locator("input[name=email]").fill(`${WITHOUT.username}@example.org`);
        await administration.locator("input[name=first_name]").fill("Ohne");
        await administration.locator("input[name=last_name]").fill("Passwort");
        // With a mail server the fields open only with the tick ('CustomUserCreationForm').
        const own = administration.locator("input[name=passwort_setzen]");
        if (await own.count()) await own.check();
        await administration.locator("input[name=password1]").fill(WITHOUT.password);
        await administration.locator("input[name=password2]").fill(WITHOUT.password);
        // A manager: a counter's start page is the block page, a 403.
        await administration.locator("input[name=is_staff]").check();
        await administration.locator("input[name=_save]").click();
        await expect(administration).toHaveURL(/\/accounts\/customuser\/\d+\/change\/$/);
        account = administration.url();
    });

    await test.step("its key comes with the password", async () => {
        await signInWebsite(page, WITHOUT);
        await createKey(page, "Ohne Passwort", WITHOUT.password);
    });

    await test.step("the administration switches the password off", async () => {
        await administration.goto(account.replace(/change\/$/, "password/"));
        await administration.locator("input[name=usable_password][value=false]").check();
        await administration.locator("input[name=unset-password]").click();
        await expect(administration).toHaveURL(account);
    });

    const key = page.locator("li").filter({ hasText: "Ohne Passwort" });
    await test.step("signed in again with the key, the page offers the confirmation", async () => {
        // A password switched off ends the sessions it had signed.
        await page.goto("/accounts/login/");
        await page.getByRole("button", { name: "Mit Passkey anmelden" }).click();
        await expect(page).not.toHaveURL(/\/accounts\/login\//);
        await page.goto("/accounts/passkeys/");
        await expect(page.getByRole("button", { name: "Mit Passkey bestätigen" })).toBeVisible();
        await expect(key.getByLabel("Dein Passwort")).toHaveCount(0);
    });

    await test.step("unconfirmed the key stays", async () => {
        await remove(page, key);
        await expect(key.getByRole("alert")).toHaveText(
            "Bitte bestätige zuerst mit einem deiner Passkeys.",
        );
        await expect(key).toHaveCount(1);
    });

    await test.step("confirmed it goes", async () => {
        await page.getByRole("button", { name: "Mit Passkey bestätigen" }).click();
        await expect(page.getByText("Bestätigt. Die Bestätigung gilt zwei Minuten.")).toBeVisible();
        await remove(page, key);
        await expect(page.getByText("Passkey „Ohne Passwort“ ist entfernt.")).toBeVisible();
        await expect(key).toHaveCount(0);
    });

    await test.step("the account goes again, so a second run finds none", async () => {
        await administration.goto(account.replace(/change\/$/, "delete/"));
        await administration.locator("input[type=submit]").click();
        await expect(administration).toHaveURL(/\/accounts\/customuser\/$/);
    });
    expect(errors).toEqual([]);
});
