/**
 * The filter of a list on the website acts while typing ('live_filter.js').
 *
 * The same form goes out in the background, and from the answer the list and
 * the places outside it that follow it ('data-ersetzen') are taken over. The
 * page itself stays, with the cursor in the search field, and the address
 * carries the filter. A search overtaken by the next one is dropped on its
 * way: its answer could otherwise arrive last and show the list of a word
 * nobody typed any more. Its grey veil goes with it. What happens once the
 * session has expired 'signed_out.spec.js' checks.
 */

import { expect, test } from "@playwright/test";

import { holdAnswer, signInWebsite, watchErrors } from "./helpers.js";

// The names and accounts below come from the demo stock ('manage.py demo').
test.skip(!!process.env.E2E_BASE_URL, "needs the demo stock");

//: The demo's manager, see DEMO.md.
const MANAGER = { username: "leitung", password: "demo1234" };
//: The list of the products. Nothing here changes them.
const LIST = "/katalog/waren/";

// The lists of the website are kept at a desk.
test.use({ viewport: { width: 1280, height: 900 } });

async function openList(page) {
    await signInWebsite(page, MANAGER);
    await page.goto(LIST);
    // Until the page has run its scripts: typed before that, nothing listens.
    await page.waitForLoadState("domcontentloaded");
    // A reload would take this along.
    await page.evaluate(() => (window.stillHere = true));
}

function search(page) {
    return page.locator("form[data-filter] input[name=q]");
}

/** The hit count above the filter, outside the list. */
function hits(page) {
    return page.locator('[data-ersetzen="anzahl"]');
}

/** A product of the list, by its name. */
function product(page, name) {
    return page.locator("[data-liste]").getByRole("link", { name, exact: true });
}

/** Until the list asks for this word in the background. */
function asking(page, word) {
    return page.waitForRequest((request) => {
        const address = new URL(request.url());
        return address.pathname === LIST && address.searchParams.get("q") === word;
    });
}

function stillHere(page) {
    return page.evaluate(() => window.stillHere);
}

test("the search filters the list while typing and the page stays", async ({ page }) => {
    const errors = watchErrors(page);
    await openList(page);
    const all = await hits(page).innerText();
    // "Anwenden" has nothing to do: the filter is already applied.
    await expect(page.locator("form[data-filter] [data-anwenden]")).toBeHidden();

    const asked = asking(page, "Schokoriegel");
    await search(page).fill("Schokoriegel");
    // Marked as a script's: the server answers such a request with a status
    // where it would otherwise lead to the sign-in ('im_hintergrund').
    expect((await asked).headers()["x-requested-with"]).toBe("fetch");
    await expect(hits(page)).toHaveText("1 Ware");
    await expect(product(page, "Schokoriegel")).toBeVisible();
    await expect(product(page, "Cola 0,5 l")).toHaveCount(0);
    await expect(page).toHaveURL(/\/katalog\/waren\/\?q=Schokoriegel$/);
    await expect(search(page)).toBeFocused();
    expect(await stillHere(page)).toBe(true);

    // Emptied, the whole list comes back, and the address loses the word.
    await search(page).fill("");
    await expect(hits(page)).toHaveText(all);
    await expect(page).toHaveURL(/\/katalog\/waren\/$/);
    await expect(product(page, "Cola 0,5 l")).toBeVisible();
    expect(await stillHere(page)).toBe(true);
    expect(errors).toEqual([]);
});

test("a search overtaken by the next one is dropped on its way", async ({ page }) => {
    const errors = watchErrors(page);
    await openList(page);
    const answer = await holdAnswer(page, { address: LIST });

    const firstAsked = asking(page, "Cola");
    await search(page).fill("Cola");
    const first = await firstAsked;
    // The server has answered, and its answer waits on the way.
    await expect.poll(answer.served).toBe(1);

    const dropped = page.waitForEvent("requestfailed", {
        predicate: (request) => request === first,
    });
    await search(page).fill("Schokoriegel");
    expect((await dropped).failure().errorText).toBe("net::ERR_ABORTED");
    await expect(hits(page)).toHaveText("1 Ware");

    // The older answer arrives now and finds nobody waiting for it.
    answer.letGo();
    await expect(hits(page)).toHaveText("1 Ware");
    await expect(page).toHaveURL(/\?q=Schokoriegel$/);
    expect(await stillHere(page)).toBe(true);
    expect(errors).toEqual([]);
});

test("a filter changed twice in quick succession leaves no grey veil behind", async ({ page }) => {
    const errors = watchErrors(page);
    await signInWebsite(page, MANAGER);
    // The page's timers run only when the test says so. The list greys
    // 150 ms after a change whose answer is not there yet.
    await page.clock.install();
    await page.goto(LIST);
    await page.waitForLoadState("domcontentloaded");
    await page.clock.pauseAt(Date.now() + 2000);
    const status = page.locator("form[data-filter] select[name=status]");
    const list = page.locator("[data-liste]");
    const answer = await holdAnswer(page, { address: LIST });

    // The first change goes out, and its answer waits on the way. A choice
    // goes out after 0 ms, still from a timer: the clock moves it on.
    await status.selectOption("aktiv");
    await page.clock.runFor(1);
    await expect.poll(answer.served).toBe(1);
    // The second one overtakes it and is answered before the veil is due.
    await status.selectOption("inaktiv");
    await page.clock.runFor(1);
    await expect(page).toHaveURL(/status=inaktiv/);
    await expect(list).not.toHaveClass(/opacity-50/);

    // The veil of the first change was due now. Until 10/2026 it came and
    // stayed, until the next change of the filter.
    await page.clock.runFor(200);
    await expect(list).not.toHaveClass(/opacity-50/);
    answer.letGo();
    expect(errors).toEqual([]);
});
