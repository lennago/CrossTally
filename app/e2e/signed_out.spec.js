/**
 * The scripts of the website once the session has expired.
 *
 * 'fetch' follows a redirect by itself, and the sign-in page answers 200.
 * Until 10/2026 a save in the background therefore counted as done: the
 * order of the counting list, a note. The server now answers a script with
 * 401 ('StaffOnlyMiddleware'). A save says so where it stands. Whatever
 * brings the page anew leads to the sign-in, which says why the inputs are
 * gone and comes back to the page afterwards. Here the session cookie simply
 * goes, as after signing out in another tab.
 */

import { expect, test } from "@playwright/test";

import {
    dragOntoNext,
    inventoryAndRow,
    signInWebsite,
    sortableOrder as order,
    sortableRows as rows,
    swapped,
    watchErrors,
} from "./helpers.js";

// The names and accounts below come from the demo stock ('manage.py demo').
test.skip(!!process.env.E2E_BASE_URL, "needs the demo stock");

//: The demo's manager, see DEMO.md.
const MANAGER = { username: "leitung", password: "demo1234" };
//: The location of 'count_list.spec.js' and the product of 'pack_order.spec.js'.
//: Nothing here changes their order.
const LOCATION = "Kühlschrank Theke";
const PRODUCT = "Schokoriegel";

// A page for the desk, like the counting list.
test.use({ viewport: { width: 1280, height: 900 } });

/** Signed out behind the page's back. */
async function expire(page) {
    await page.context().clearCookies({ name: "sessionid" });
}

async function openLocation(page) {
    await signInWebsite(page, MANAGER);
    await page.goto("/haeuser/");
    await page.getByRole("link", { name: new RegExp(`^${LOCATION}`) }).click();
}

async function openCountingList(page) {
    await openLocation(page);
    await page.getByRole("link", { name: "Zählliste bearbeiten" }).click();
    await expect(page).toHaveURL(/\/zaehlliste\/$/);
    // Until the page has run its scripts, as in 'count_list.spec.js'.
    await page.waitForLoadState("domcontentloaded");
}

async function openProduct(page) {
    await signInWebsite(page, MANAGER);
    await page.goto(`/katalog/waren/?q=${encodeURIComponent(PRODUCT)}`);
    await page.getByRole("link", { name: PRODUCT, exact: true }).click();
    await expect(page).toHaveURL(/\/waren\/\d+\/$/);
    await page.waitForLoadState("domcontentloaded");
}

/**
 * On the sign-in page, with the notice that the session had expired, or without it.
 *
 * Only once the page has run its scripts: the notice starts hidden, and
 * 'login_notice.js' shows it at the end of the page.
 */
async function onSignIn(page, { notice }) {
    await expect(page).toHaveURL(/\/accounts\/login\//);
    await page.waitForLoadState("domcontentloaded");
    const text = page.getByText("Die Anmeldung war abgelaufen.");
    if (notice) await expect(text).toBeVisible();
    else await expect(text).toBeHidden();
}

/** Signing in on the sign-in page that is open: back to where it came from. */
async function signInHere(page) {
    await page.fill("input[name=username]", MANAGER.username);
    await page.fill("input[name=password]", MANAGER.password);
    await page.getByRole("button", { name: "Anmelden", exact: true }).click();
    await expect(page).not.toHaveURL(/\/accounts\/login\//);
}

function pathOf(page) {
    return new URL(page.url()).pathname;
}

test("a drag on the counting list says that its order is not saved", async ({ page }) => {
    const errors = watchErrors(page, { allowNetwork: true });
    await openCountingList(page);
    const before = await order(page);

    await expire(page);
    await dragOntoNext(page, 0);
    await expect(page.locator("[data-sicherungshinweis]")).toHaveText(
        "Die Reihenfolge ist nicht gesichert, denn die Anmeldung ist abgelaufen. " +
            "Bitte melde dich neu an.",
    );

    await openCountingList(page);
    expect(await order(page)).toEqual(before);
    expect(errors).toEqual([]);
});

test("a note waits in its field and goes out after signing in in another tab", async ({
    page,
    request,
}) => {
    const errors = watchErrors(page, { allowNetwork: true });
    const { inventory, row } = await inventoryAndRow(request);
    const address = `/inventuren/${inventory.id}/pruefen/?zuruecksetzen=1`;
    const field = page.locator(`textarea[name="notiz_${row.artikel_id}"]`);
    const state = page.locator("[data-notizstand]");
    const save = page.getByRole("button", { name: "Notizen speichern" });
    await signInWebsite(page, MANAGER);
    await page.goto(address);
    const before = await field.inputValue();

    await test.step("the session expires while typing", async () => {
        await expire(page);
        await field.fill("Im Lager nachzählen");
        await page.locator("h1").click();
        await expect(state).toHaveText(
            "Notizen nicht gesichert, denn die Anmeldung ist abgelaufen. Melde dich in " +
                "einem anderen Tab neu an und drücke dann hier „Notizen speichern“.",
        );
        await expect(field).toHaveValue("Im Lager nachzählen");
    });

    await test.step("signed in anew in another tab, the page saves", async () => {
        // Django renews the CSRF token on signing in. The page's form holds
        // the old one, and until 10/2026 saving failed with it.
        await signInElsewhere(page);
        await save.click();
        await expect(state).toHaveText("Notizen gesichert");
        await page.reload();
        await expect(field).toHaveValue("Im Lager nachzählen");
    });

    // Back to the demo's state, for the tests after this one.
    await field.fill(before);
    await save.click();
    await expect(state).toHaveText("Notizen gesichert");
    expect(errors).toEqual([]);
});

/** Signed in anew in another tab: Django renews the CSRF token, the page keeps the old one. */
async function signInElsewhere(page) {
    const other = await page.context().newPage();
    await signInWebsite(other, MANAGER);
    await other.close();
}

test("a drag on the counting list is saved after signing in anew in another tab", async ({
    page,
}) => {
    const errors = watchErrors(page);
    await openCountingList(page);
    const list = pathOf(page);
    const before = await order(page);
    const action = await page
        .locator("form:has([data-sortierbar])")
        .evaluate((form) => new URL(form.action).pathname);
    const saved = () =>
        page.waitForResponse(
            (answer) => answer.request().method() === "POST" && pathOf(answer) === action,
        );

    // Until 10/2026 the save in the background sent the page's token and
    // heard 403.
    await signInElsewhere(page);
    let answer = saved();
    const dragged = await dragOntoNext(page, 0);
    expect((await answer).status()).toBe(204);
    await expect(page.locator("[data-sicherungshinweis]")).toHaveCount(0);
    await page.goto(list);
    expect(await order(page)).toEqual(dragged);

    // Back to the demo's state, for the tests after this one.
    answer = saved();
    await dragOntoNext(page, 0);
    expect((await answer).status()).toBe(204);
    await page.goto(list);
    expect(await order(page)).toEqual(before);
    expect(errors).toEqual([]);
});

test("a passkey is added after signing in anew in another tab", async ({ page }) => {
    const errors = watchErrors(page);
    // Chromium's virtual authenticator, as in 'passkey.spec.js'.
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
    await signInWebsite(page, MANAGER);
    await page.goto("/accounts/passkeys/");

    // Until 10/2026 the page's token was refused, and it said "Das hat
    // nicht geklappt".
    await signInElsewhere(page);
    await page.locator("[data-passkey-name]").fill("Testgerät");
    // A new key asks for the password before the device's dialog.
    await page.locator("[data-passkey-passwort]").fill(MANAGER.password);
    await page.getByRole("button", { name: "Passkey anlegen" }).click();
    const key = page.locator("li").filter({ hasText: "Testgerät" });
    await expect(key).toBeVisible();

    // Removed again, so a second run finds a clean account. Removing asks for
    // the password as well.
    await key.getByLabel("Dein Passwort").fill(MANAGER.password);
    page.once("dialog", (dialog) => dialog.accept());
    await key.getByRole("button", { name: "Entfernen" }).click();
    await expect(key).toHaveCount(0);
    expect(errors).toEqual([]);
});

test("a row button works after signing in anew in another tab", async ({ page }) => {
    const errors = watchErrors(page);
    await openProduct(page);
    const product = pathOf(page);
    const before = await order(page);

    // Until 10/2026 it sent the page's token: the save in the background
    // heard 403, and the ordinary way after it ended on Django's page for a
    // refused token.
    await signInElsewhere(page);
    await rows(page).first().getByTitle("Nach unten", { exact: true }).click();
    await expect.poll(() => order(page)).toEqual(swapped(before, 0));
    expect(pathOf(page)).toBe(product);

    // Back to the demo's state, for the tests after this one.
    await rows(page).nth(1).getByTitle("Nach oben", { exact: true }).click();
    await expect.poll(() => order(page)).toEqual(before);
    expect(errors).toEqual([]);
});

test("a row button leads to the sign-in and back to its page", async ({ page }) => {
    const errors = watchErrors(page, { allowNetwork: true });
    await openProduct(page);
    const product = pathOf(page);
    const before = await order(page);

    await expire(page);
    // The arrows carry their name in the title only. Their form sends to an
    // address of its own, which knows no GET.
    await rows(page).first().getByTitle("Nach unten", { exact: true }).click();
    await onSignIn(page, { notice: true });

    // Until 10/2026 signing in led to the address of the form, and that said 405.
    await signInHere(page);
    expect(pathOf(page)).toBe(product);
    expect(await order(page)).toEqual(before);
    expect(errors).toEqual([]);
});

test("a row button that leads to another page leaves no notice behind", async ({ page }) => {
    const errors = watchErrors(page);
    await openCountingList(page);
    // The form names no address of its own: it sends to the page.
    const action = await page
        .locator("form:has([data-sortierbar])")
        .evaluate((form) => new URL(form.action).pathname);
    // As after the last open row of a clarification: the answer is another page.
    await page.route(
        (url) => url.pathname === action,
        (route) =>
            route.request().method() === "POST"
                ? route.fulfill({ status: 302, headers: { Location: "/haeuser/" } })
                : route.continue(),
    );

    await rows(page).first().getByRole("button", { name: "Nach unten", exact: true }).click();
    await expect(page).toHaveURL(/\/haeuser\/$/);

    // Until 10/2026 the next sign-in in this tab said the session had expired.
    await page.getByRole("button", { name: MANAGER.username, exact: true }).click();
    await page.getByRole("button", { name: "Abmelden", exact: true }).click();
    await onSignIn(page, { notice: false });
    expect(errors).toEqual([]);
});

test("Refresh leads to the sign-in and back to the counting list", async ({ page }) => {
    const errors = watchErrors(page, { allowNetwork: true });
    await openCountingList(page);
    const list = pathOf(page);

    await expire(page);
    await page.getByRole("button", { name: "Aktualisieren" }).click();
    await onSignIn(page, { notice: true });

    await signInHere(page);
    expect(pathOf(page)).toBe(list);
    expect(errors).toEqual([]);
});

test("Refresh values leads to the sign-in instead of saying nothing is new", async ({
    page,
    request,
}) => {
    const errors = watchErrors(page, { allowNetwork: true });
    const { headers, inventory, row } = await inventoryAndRow(request);
    await signInWebsite(page, MANAGER);
    await page.goto(`/inventuren/${inventory.id}/pruefen/?zuruecksetzen=1`);
    const review = pathOf(page);

    // New values, as in 'review.spec.js': counted through the API.
    const before = row.eingabemenge;
    const counted = String(Number(before ?? 0) + 3);
    const sent = await request.patch(`/api/v1/zaehlpositionen/${row.id}/`, {
        headers,
        data: { eingabemenge: counted, eingabemenge_vorher: before },
    });
    expect(sent.ok()).toBeTruthy();
    try {
        await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
        const button = page.locator("[data-auffrischen]");
        await expect(button).toHaveClass(/btn-warning/);

        await expire(page);
        await button.click();
        await onSignIn(page, { notice: true });
        await signInHere(page);
        expect(pathOf(page)).toBe(review);
    } finally {
        // Back to the demo's state, for the tests after this one.
        await request.patch(`/api/v1/zaehlpositionen/${row.id}/`, {
            headers,
            data: { eingabemenge: before, eingabemenge_vorher: counted },
        });
    }
    expect(errors).toEqual([]);
});

/** The check list of the demo's running inventory of Kino Ost, with a note field to type in. */
async function openReview(page, request) {
    const found = await inventoryAndRow(request);
    await signInWebsite(page, MANAGER);
    await page.goto(`/inventuren/${found.inventory.id}/pruefen/?zuruecksetzen=1`);
    return {
        ...found,
        field: page.locator(`textarea[name="notiz_${found.row.artikel_id}"]`),
        state: page.locator("[data-notizstand]"),
    };
}

const NOTE_SIGNED_OUT =
    "Notizen nicht gesichert, denn die Anmeldung ist abgelaufen. Melde dich in " +
    "einem anderen Tab neu an und drücke dann hier „Notizen speichern“.";

test("the filter of the check list keeps an open note and stays on the page", async ({
    page,
    request,
}) => {
    const errors = watchErrors(page, { allowNetwork: true });
    const { field, state } = await openReview(page, request);
    const review = pathOf(page);
    const before = await field.inputValue();

    await expire(page);
    await field.fill("Im Lager nachzählen");
    // Typed and cleared again: the list asks once, and later no 'change'
    // brings a filtered list in which the row might be missing.
    const filtered = page.waitForResponse(
        (answer) => answer.request().method() === "GET" && pathOf(answer) === review,
    );
    const search = page.locator("form[data-filter] input[name=q]");
    await search.fill("Artikel");
    await search.fill("");
    // Until 10/2026 the filter led to the sign-in, and the note was gone.
    expect((await filtered).status()).toBe(401);
    await expect(state).toHaveText(NOTE_SIGNED_OUT);
    await expect(page.locator("[data-liste]")).not.toHaveClass(/opacity-50/);
    expect(pathOf(page)).toBe(review);
    await expect(field).toHaveValue("Im Lager nachzählen");

    // Signed in anew in another tab, the note goes out from here.
    await signInElsewhere(page);
    const save = page.getByRole("button", { name: "Notizen speichern" });
    await save.click();
    await expect(state).toHaveText("Notizen gesichert");

    // Back to the demo's state, for the tests after this one.
    await field.fill(before);
    await save.click();
    await expect(state).toHaveText("Notizen gesichert");
    expect(errors).toEqual([]);
});

test("Refresh values keeps an open note and stays on the page", async ({ page, request }) => {
    const errors = watchErrors(page, { allowNetwork: true });
    const { headers, row, field, state } = await openReview(page, request);
    const review = pathOf(page);
    const button = page.locator("[data-auffrischen]");

    const before = row.eingabemenge;
    const counted = String(Number(before ?? 0) + 3);
    const sent = await request.patch(`/api/v1/zaehlpositionen/${row.id}/`, {
        headers,
        data: { eingabemenge: counted, eingabemenge_vorher: before },
    });
    expect(sent.ok()).toBeTruthy();
    try {
        await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
        await expect(button).toHaveClass(/btn-warning/);

        await field.fill("Im Lager nachzählen");
        await expire(page);
        // Until 10/2026 the button reloaded the page into the sign-in.
        await button.click();
        await expect(state).toHaveText(NOTE_SIGNED_OUT);
        await expect(button).toHaveText("Anmeldung abgelaufen");
        expect(pathOf(page)).toBe(review);
        await expect(field).toHaveValue("Im Lager nachzählen");
    } finally {
        // Back to the demo's state, for the tests after this one. The note was
        // never stored: the session had expired.
        await request.patch(`/api/v1/zaehlpositionen/${row.id}/`, {
            headers,
            data: { eingabemenge: before, eingabemenge_vorher: counted },
        });
    }
    expect(errors).toEqual([]);
});

test("the check list says when the session has expired and locks closing", async ({
    page,
    request,
}) => {
    const errors = watchErrors(page, { allowNetwork: true });
    await openReview(page, request);
    const button = page.locator("[data-auffrischen]");
    const closing = page.locator("[data-abschliessen]");
    const look = () => page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
    await expect(button).toHaveText("Keine neuen Werte");

    // The page looks every 30 seconds and when the tab comes back. Until
    // 10/2026 it went on saying there were no new values.
    await expire(page);
    await look();
    await expect(button).toHaveText("Anmeldung abgelaufen");
    await expect(closing).toBeDisabled();

    // Signed in anew in another tab, the next look finds the session again.
    await signInElsewhere(page);
    await look();
    await expect(button).toHaveText("Keine neuen Werte");
    await expect(closing).toBeEnabled();
    expect(errors).toEqual([]);
});

test("the filter of a list leads to the sign-in", async ({ page }) => {
    const errors = watchErrors(page, { allowNetwork: true });
    await signInWebsite(page, MANAGER);
    await page.goto("/katalog/waren/");

    await expire(page);
    await page.locator("form[data-filter] input[name=q]").fill("Cola");
    await onSignIn(page, { notice: true });

    await signInHere(page);
    await expect(page).toHaveURL(/\/katalog\/waren\/\?.*Cola/);
    expect(errors).toEqual([]);
});

test("a preview leads to the sign-in", async ({ page }) => {
    const errors = watchErrors(page, { allowNetwork: true });
    await openLocation(page);
    await page.getByRole("link", { name: "Zusammenführen", exact: true }).click();
    await expect(page).toHaveURL(/\/zusammenfuehren\/$/);
    const merge = pathOf(page);

    await expire(page);
    // A searchable list ('select.js'): chosen by click, as a person does.
    await page.locator("select[data-vorschau-wahl] + .ts-wrapper .ts-control").click();
    await page.locator(".ts-dropdown .option:not([data-value=''])").first().click();
    await onSignIn(page, { notice: true });

    await signInHere(page);
    expect(pathOf(page)).toBe(merge);
    expect(errors).toEqual([]);
});

test("adding a passkey says that the session has expired", async ({ page }) => {
    const errors = watchErrors(page, { allowNetwork: true });
    await signInWebsite(page, MANAGER);
    await page.goto("/accounts/passkeys/");
    await page.locator("[data-passkey-passwort]").fill(MANAGER.password);

    await expire(page);
    await page.getByRole("button", { name: "Passkey anlegen" }).click();
    await expect(page.locator("[data-passkey-meldung]")).toHaveText(
        "Der Passkey ist nicht angelegt, denn die Anmeldung ist abgelaufen. " +
            "Bitte melde dich neu an.",
    );
    expect(errors).toEqual([]);
});
