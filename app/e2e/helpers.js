/**
 * What every pass through the app needs: the demo's counter, the texts the
 * page shows, and the way down to the articles of a location. At the end
 * what the sortable lists of the website need: dragging a row, and saves
 * held on their way. And the check list of the website: a row to look at,
 * and an inventory by its house and types.
 *
 * The server behind the tests comes from 'bin/e2e-server.sh' and carries the
 * demo stock. Against another server the account comes from 'E2E_USERNAME'
 * and 'E2E_PASSWORD' ('playwright.config.js'). It has to count in a running
 * inventory there.
 */

import { expect } from "@playwright/test";

import { TEXTS } from "../src/texts.js";

//: The demo's counter, see DEMO.md. The stock is invented and the password public.
export const ACCOUNT = {
    username: process.env.E2E_USERNAME ?? "zaehler",
    password: process.env.E2E_PASSWORD ?? "demo1234",
};

//: How many articles a test moves past at most to find a fitting one.
export const SKIP_LIMIT = 8;

/** The German text behind a key: the demo and the app start in German. */
export function text(key) {
    return TEXTS[key].de;
}

/** The titles of the rows, as the list shows them. */
export function rowTitles(page) {
    return page.locator("button.row .text strong").allInnerTexts();
}

/** The heading of the entry form: the article's name. */
export function articleHeading(page) {
    return page.locator("header.article-header h1");
}

/**
 * Collect what the browser reports: script errors and console errors.
 *
 * Whoever only looks at the page misses a failed fetch or a thrown handler.
 * Each test checks the list at its end. Without network a failed request
 * is expected, 'allowNetwork' lets those lines through.
 */
export function watchErrors(page, { allowNetwork = false } = {}) {
    const errors = [];
    page.on("pageerror", (cause) => errors.push(`Skriptfehler: ${cause.message}`));
    page.on("console", (message) => {
        if (message.type() !== "error") return;
        if (
            allowNetwork &&
            /Failed to load resource|ERR_INTERNET_DISCONNECTED/.test(message.text())
        )
            return;
        errors.push(`Konsole: ${message.text()}`);
    });
    return errors;
}

export async function signIn(page, account = ACCOUNT) {
    await page.goto("/app/");
    await expect(page.getByRole("heading", { name: text("login.title") })).toBeVisible();
    await page.getByLabel(text("login.username")).fill(account.username);
    await page.getByLabel(text("login.password")).fill(account.password);
    // Exact: "Mit Passkey anmelden" also contains the word.
    await page.getByRole("button", { name: text("login.button"), exact: true }).click();
    await expect(page.locator("button.row").first()).toBeVisible();
}

/** Sign in on the website, for the pages the app does not have. */
export async function signInWebsite(page, account) {
    await page.goto("/accounts/login/");
    await page.fill("input[name=username]", account.username);
    await page.fill("input[name=password]", account.password);
    // The navigation bar carries forms of its own (language, colours) before this one.
    await page.getByRole("button", { name: "Anmelden", exact: true }).click();
    await expect(page).not.toHaveURL(/\/accounts\/login\//);
}

async function token(request) {
    const answer = await request.post("/api/v1/token/", { data: ACCOUNT });
    expect(answer.ok()).toBeTruthy();
    return (await answer.json()).access;
}

/**
 * The running inventory of Kino Ost and a counted row of a house article in it.
 *
 * For the check list of the website. The app's tests count in Kino Nord.
 * Read through the API, with the headers to change the row with.
 */
export async function inventoryAndRow(request) {
    const headers = { Authorization: `Bearer ${await token(request)}` };
    const inventories = await (await request.get("/api/v1/inventuren/", { headers })).json();
    const inventory = inventories.find((entry) => entry.haus === "Kino Ost");
    const detail = await (
        await request.get(`/api/v1/inventuren/${inventory.id}/`, { headers })
    ).json();
    const row = detail.zaehlpositionen.find((entry) => entry.gebinde && !entry.wird_gewogen);
    return { headers, inventory, row };
}

/**
 * A running inventory of a house that counts exactly these types.
 *
 * For the check list of the website where 'inventoryAndRow' does not fit:
 * the cup inventory of Kino Nord takes the difference by hand.
 */
export async function runningInventory(request, house, types) {
    const headers = { Authorization: `Bearer ${await token(request)}` };
    const inventories = await (await request.get("/api/v1/inventuren/", { headers })).json();
    return inventories.find((entry) => entry.haus === house && entry.arten_anzeige === types);
}

/**
 * Sign out through the account menu. Asks nothing as long as everything is out.
 *
 * 'anyway': something still waits on the device, and the menu asks before it
 * deletes it. This confirms.
 */
export async function signOut(page, { anyway = false } = {}) {
    await page.locator("button.account").click();
    await page.getByRole("button", { name: text("account.sign_out"), exact: true }).click();
    if (anyway) await page.getByRole("button", { name: text("account.sign_out_anyway") }).click();
    await expect(page.getByRole("heading", { name: text("login.title") })).toBeVisible();
}

/**
 * The first row that expects something.
 *
 * An area or location without rows is allowed, the demo shows one on
 * purpose. It leads to "nothing expected here" and not to articles. Above
 * the areas stands the way to the review list, which is no place either. A
 * locked inventory opens on its review list: nothing to count there.
 */
export function populatedRow(page) {
    const empty = page.getByText(text("list.nothing_expected"), { exact: true });
    const review = page.getByText(text("review.entry"), { exact: true });
    const locked = new RegExp(`· ${text("inventories.locked")}$`);
    return page
        .locator("button.row")
        .filter({ hasNot: empty })
        .filter({ hasNot: review })
        .filter({ hasNotText: locked })
        .first();
}

/** The row with exactly this title. */
export function rowTitled(page, title) {
    return page.locator("button.row").filter({ has: page.getByText(title, { exact: true }) });
}

/**
 * Open a row and wait for the change, not for "some row".
 *
 * Every level shows clickable rows. Whoever only waits for those reads the
 * old list and does not notice. The heading, by contrast, changes with
 * every step, and the list builds once the state from the storage is there.
 */
export async function descend(page, row = populatedRow(page)) {
    const heading = page.locator("header.header strong");
    const before = await heading.innerText();
    await row.click();
    await expect(heading).not.toHaveText(before);
    await expect(page.locator("button.row").first()).toBeVisible();
    return heading.innerText();
}

/** Sign in and walk down to the articles of the first populated location. */
export async function openArticles(page) {
    await signIn(page);
    await descend(page); // the inventory, its areas
    await descend(page); // the area, its locations
    await descend(page); // the location, its articles
    await expect(page.locator("input[type=search]")).toBeVisible();
    return rowTitles(page);
}

/**
 * Open the first article and move on until one passes 'fits'.
 *
 * Not every article has a count field: one that is only weighed shows the
 * scale. The test moves on like a counter would, a few times at most.
 */
export async function openArticleWhere(page, fits, what) {
    await page.locator("button.row").first().click();
    const heading = articleHeading(page);
    for (let skipped = 0; skipped < SKIP_LIMIT; skipped += 1) {
        await expect(heading).toBeVisible();
        if (await fits(page)) return heading.innerText();
        const before = await heading.innerText();
        await page.locator("button.next").click();
        await expect(heading).not.toHaveText(before);
    }
    throw new Error(`Keiner der ersten ${SKIP_LIMIT} Artikel ${what}.`);
}

/** An article with a count field. */
export function openCountable(page) {
    return openArticleWhere(
        page,
        async (page) => (await page.locator(".count-row input").count()) > 0,
        "hat ein Zählfeld",
    );
}

// -- The sortable lists of the website -----------------------------------

/**
 * The rows of the page's sortable list, without the copy that follows the
 * pointer while dragging.
 */
export function sortableRows(page) {
    return page.locator("[data-sortierbar] > :not(.sortable-fallback)");
}

/** The order as the form sends it: one hidden field per row. */
export function sortableOrder(page) {
    return sortableRows(page)
        .locator("input[name=reihenfolge]")
        .evaluateAll((fields) => fields.map((field) => field.value));
}

/**
 * Until Sortable has taken the list.
 *
 * The page sets it up with its last scripts ('sortable.js'). A press before
 * that drags nothing, and a test that expects no drag would pass for the
 * wrong reason. Until 10/2026 a drag right after opening the counting list
 * failed now and then.
 */
async function sortableReady(page) {
    await expect
        .poll(() =>
            sortableRows(page)
                .first()
                .evaluate((row) => Boolean(window.Sortable?.get(row.parentElement))),
        )
        .toBe(true);
}

export function swapped(sequence, index) {
    const result = [...sequence];
    [result[index], result[index + 1]] = [result[index + 1], result[index]];
    return result;
}

/**
 * Drag a row by its handle onto the row below it and return the new order.
 *
 * The list drags with Sortable's own imitation ('forceFallback' in
 * 'sortable.js'). It begins with the first move and looks at what lies
 * under the pointer every 50 ms. So the pointer first leaves the handle a
 * little, then rests on the neighbour until the rows have swapped.
 */
export async function dragOntoNext(page, index) {
    await sortableReady(page);
    const rows = sortableRows(page);
    // Rows that have just swapped still glide to their place for a moment.
    await expect
        .poll(() => rows.evaluateAll((all) => all.every((row) => !row.style.transform)))
        .toBe(true);
    const expected = swapped(await sortableOrder(page), index);
    const handle = await rows.nth(index).locator("[data-griff]").boundingBox();
    const next = await rows.nth(index + 1).boundingBox();
    const x = handle.x + handle.width / 2;
    const y = handle.y + handle.height / 2;

    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x, y + 5);
    await expect(page.locator(".sortable-fallback")).toBeAttached();
    await page.mouse.move(x, next.y + next.height / 2, { steps: 5 });
    await expect.poll(() => sortableOrder(page)).toEqual(expected);
    await page.mouse.up();
    await expect(page.locator(".sortable-fallback")).toHaveCount(0);
    return expected;
}

/**
 * Hold the first 'count' saves sent to 'address' on their way until 'letGo'.
 *
 * Later ones pass at once and overtake them, as on a network where one
 * request hangs. The page counts what it starts and what it has heard back
 * from ('window.saves'): the route learns of a request only a moment after
 * the page sent it. Fetches without a body ("Refresh") count apart
 * ('window.reads'), and so does a submit that leaves the page the ordinary
 * way ('leaving'): a listener after all of the page's scripts sees it only
 * if none of them took it over. With 'method: "GET"' the next fetch of the
 * page is held, the one of "Refresh". A reload before it would be held instead.
 */
export async function holdSaves(
    page,
    count,
    { address = new URL(page.url()).pathname, method = "POST" } = {},
) {
    const gates = Array.from({ length: count }, () => Promise.withResolvers());
    let arrived = 0;
    await page.route(
        (url) => url.pathname === address,
        async (route) => {
            if (route.request().method() === method && arrived < count) {
                const gate = gates[arrived];
                arrived += 1;
                if ((await gate.promise) === "fails") return route.abort();
            }
            await route.continue();
        },
    );
    await page.evaluate(() => {
        const original = window.fetch;
        window.saves = { started: 0, settled: 0, leaving: 0 };
        window.reads = 0;
        window.fetch = (resource, options) => {
            const answer = original.call(window, resource, options);
            if (options?.method !== "POST") {
                window.reads += 1;
                return answer;
            }
            window.saves.started += 1;
            return answer.finally(() => (window.saves.settled += 1));
        };
        document.addEventListener("submit", (event) => {
            if (!event.defaultPrevented) window.saves.leaving += 1;
        });
    });
    return {
        arrived: () => arrived,
        letGo: (number, outcome = "arrives") => gates[number - 1].resolve(outcome),
    };
}

/**
 * Hold the answer to the first request with 'method' to 'address' until 'letGo'.
 *
 * Unlike 'holdSaves' the request reaches the server at once. The page it
 * brings back is the one of that moment, and it arrives only once let go,
 * so it can come after a later request and carry an older state. A
 * request a redirect led to passes, the page a row button's answer brings.
 * Other requests go on to the routes before this one ('route.fallback'):
 * register it after 'holdSaves'. 'served' counts the answers the server
 * has given to the held one.
 */
export async function holdAnswer(
    page,
    { address = new URL(page.url()).pathname, method = "GET" } = {},
) {
    const gate = Promise.withResolvers();
    let held = false;
    let served = 0;
    await page.route(
        (url) => url.pathname === address,
        async (route) => {
            const request = route.request();
            if (held || request.method() !== method || request.redirectedFrom())
                return route.fallback();
            held = true;
            const response = await route.fetch();
            served += 1;
            await gate.promise;
            await route.fulfill({ response });
        },
    );
    return { served: () => served, letGo: () => gate.resolve() };
}

/**
 * Press a row's handle and let go again: whether Sortable took the row.
 *
 * It marks the row it takes at once, on the press ('sortable-chosen'), and
 * a press it refuses leaves no mark. So there is nothing to wait for. Let go
 * without moving, a taken row stays where it is.
 */
export async function dragStarts(page, index) {
    await sortableReady(page);
    const row = sortableRows(page).nth(index);
    const handle = await row.locator("[data-griff]").boundingBox();
    await page.mouse.move(handle.x + handle.width / 2, handle.y + handle.height / 2);
    await page.mouse.down();
    const taken = await row.evaluate((element) => element.classList.contains("sortable-chosen"));
    await page.mouse.up();
    return taken;
}

/** How many saves the page has started ('holdSaves'). */
export function started(page) {
    return page.evaluate(() => window.saves.started);
}

/**
 * Until the page has heard back from this many saves and acted on it.
 *
 * Asked as a task of its own: what the page does with an answer runs before
 * it, a save that follows included.
 */
export function settled(page, count) {
    return expect
        .poll(() => page.evaluate(() => window.saves.settled))
        .toBeGreaterThanOrEqual(count);
}
