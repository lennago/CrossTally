/**
 * Row buttons of the website send their form in the background ('instant_actions.js').
 *
 * While an answer takes a while, the area of its button turns grey, and only
 * that area. Until 10/2026 one timer served every button: a second one pressed
 * meanwhile took it over. The area of the first turned grey after its answer
 * and stayed so, and the slow second one got no veil. The veil goes once no
 * request of its area is on its way any more. Until 10/2026 the first answer
 * took it away while a second one of the same area was still waiting. Here
 * the buttons stand in two lists of a house article, its packs and its
 * locations. Their answers come from a route that leads back to the page, so
 * nothing on the server changes.
 */

import { expect, test } from "@playwright/test";

import { signInWebsite, watchErrors } from "./helpers.js";

// The names and accounts below come from the demo stock ('manage.py demo').
test.skip(!!process.env.E2E_BASE_URL, "needs the demo stock");

//: The demo's manager, see DEMO.md.
const MANAGER = { username: "leitung", password: "demo1234" };
//: A product with three packs, kept in Kino Nord.
const PRODUCT = "Schokoriegel";

// Like the lists a page for the desk.
test.use({ viewport: { width: 1280, height: 900 } });

/**
 * The page of the house article in Kino Nord, with its clock standing.
 *
 * The page's timers run only when the test says so. An area turns grey 150 ms
 * after its button if the answer is not there yet.
 */
async function openArticle(page) {
    await signInWebsite(page, MANAGER);
    await page.clock.install();
    await page.goto(`/katalog/waren/?q=${encodeURIComponent(PRODUCT)}`);
    await page
        .locator("[data-liste]")
        .getByRole("link", { name: "Kino Nord", exact: true })
        .click();
    await expect(page).toHaveURL(/\/katalog\/hausartikel\/\d+\//);
    // Until the page has run its scripts: pressed before that, a button
    // sends its form the ordinary way.
    await page.waitForLoadState("domcontentloaded");
    await page.clock.pauseAt(Date.now() + 2000);
}

function packsOf(page) {
    return page.locator("[data-liste]").filter({ has: page.locator("[data-sortierbar]") });
}

function placesOf(page) {
    return page.locator("[data-liste]").filter({ hasText: "Kommt vor an" });
}

/**
 * Hold the requests to 'address' until 'letGo', then answer with the way back
 * to 'back', as the views do after a row button.
 */
async function heldBack(page, address, back) {
    const gate = Promise.withResolvers();
    let arrived = 0;
    await page.route(
        (url) => url.pathname === address,
        async (route) => {
            arrived += 1;
            await gate.promise;
            await route.fulfill({ status: 302, headers: { Location: back } });
        },
    );
    return { arrived: () => arrived, letGo: () => gate.resolve() };
}

function actionOf(form) {
    return form.evaluate((element) => new URL(element.action).pathname);
}

/** Mark the first row of an area. Returns the wait until the area has come anew. */
async function marked(area) {
    await area
        .locator("li")
        .first()
        .evaluate((row) => (row.dataset.before = ""));
    return () => expect(area.locator("[data-before]")).toHaveCount(0);
}

test("two row buttons in two lists grey their own list each", async ({ page }) => {
    const errors = watchErrors(page);
    await openArticle(page);
    const packs = packsOf(page);
    const places = placesOf(page);
    const down = packs.getByTitle("Nach unten", { exact: true }).first();
    const add = places.getByRole("button", { name: "Hinzufügen" }).first();
    const arrow = await heldBack(page, await actionOf(packs.locator("form")), page.url());
    const place = await heldBack(page, await actionOf(places.locator("form").first()), page.url());
    const packsRenewed = await marked(packs);

    await down.click();
    await expect.poll(arrow.arrived).toBe(1);
    await add.click();
    await expect.poll(place.arrived).toBe(1);
    // The arrow's answer comes, the other one keeps waiting.
    arrow.letGo();
    await packsRenewed();

    // The veils were due now: the waiting list turns grey, the answered one
    // stays as it is.
    await page.clock.runFor(200);
    await expect(places).toHaveClass(/opacity-50/);
    await expect(packs).not.toHaveClass(/opacity-50/);

    place.letGo();
    await expect(places).not.toHaveClass(/opacity-50/);
    await expect(packs).not.toHaveClass(/opacity-50/);
    expect(errors).toEqual([]);
});

test("two row buttons in one list keep it grey until both are answered", async ({ page }) => {
    const errors = watchErrors(page);
    await openArticle(page);
    const places = placesOf(page);
    // Two locations the article can be added at, each with a form of its own.
    const forms = places
        .locator("form")
        .filter({ has: page.getByRole("button", { name: "Hinzufügen" }) });
    const first = await heldBack(page, await actionOf(forms.nth(0)), page.url());
    const second = await heldBack(page, await actionOf(forms.nth(1)), page.url());

    await forms.nth(0).getByRole("button").click();
    await expect.poll(first.arrived).toBe(1);
    await forms.nth(1).getByRole("button").click();
    await expect.poll(second.arrived).toBe(1);
    await page.clock.runFor(200);
    await expect(places).toHaveClass(/opacity-50/);

    // The first answer comes, the second request is still on its way.
    let renewed = await marked(places);
    first.letGo();
    await renewed();
    await expect(places).toHaveClass(/opacity-50/);

    // Now nothing of the list is on its way any more.
    renewed = await marked(places);
    second.letGo();
    await renewed();
    await expect(places).not.toHaveClass(/opacity-50/);
    expect(errors).toEqual([]);
});
