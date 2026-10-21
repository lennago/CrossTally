/**
 * The preview of copying and merging follows the choice at once ('live_preview.js').
 *
 * The page is fetched in the background with the choice in the address, and
 * its preview is taken over. The choice itself stays, and so does a name
 * typed by hand. Until the new preview is there, the old one cannot be
 * confirmed. A choice overtaken by the next one is dropped on its way. An
 * objection to the choice stands at the choice, outside the preview, and
 * then the page comes as a whole. Nothing here is copied or merged. What
 * happens once the session has expired 'signed_out.spec.js' checks.
 */

import { expect, test } from "@playwright/test";

import { holdAnswer, signInWebsite, watchErrors } from "./helpers.js";

// The names and accounts below come from the demo stock ('manage.py demo').
test.skip(!!process.env.E2E_BASE_URL, "needs the demo stock");

//: The demo's manager, see DEMO.md.
const MANAGER = { username: "leitung", password: "demo1234" };
//: A location of Kino Nord. Its sibling in the same area has counted rows in
//: running inventories, so merging the two waits for those.
const LOCATION = "Kühlschrank Theke";

// Like the lists a page for the desk.
test.use({ viewport: { width: 1280, height: 900 } });

/** The page of the location, then the button to one of its actions. */
async function openAction(page, action) {
    await signInWebsite(page, MANAGER);
    await page.goto("/haeuser/");
    await page.getByRole("link", { name: new RegExp(`^${LOCATION}`) }).click();
    await page.getByRole("link", { name: action, exact: true }).click();
    // Until the page has run its scripts: chosen before that, nothing listens.
    await page.waitForLoadState("domcontentloaded");
    // A reload would take this along.
    await page.evaluate(() => (window.stillHere = true));
    return new URL(page.url()).pathname;
}

/**
 * Choose in the searchable list ('select.js') by click, as a person does.
 * On the copy page its options stand in groups, one per house.
 */
async function choose(page, group, option) {
    await page.locator("select[data-vorschau-wahl] + .ts-wrapper .ts-control").click();
    let options = page.locator(".ts-dropdown");
    if (group)
        options = options.locator(".optgroup").filter({
            has: page.locator(".optgroup-header", { hasText: group }),
        });
    await options.getByRole("option", { name: option, exact: true }).click();
}

/** The heading of the copy's counting list in the preview. */
function copyHeading(page) {
    return page.locator("[data-vorschau] .card-header span").first();
}

/** Until the page asks for this choice in the background. */
function asking(page, address, name) {
    return page.waitForRequest((request) => {
        const url = new URL(request.url());
        return url.pathname === address && url.searchParams.has(name);
    });
}

function stillHere(page) {
    return page.evaluate(() => window.stillHere);
}

test("the preview follows the choice, and a name typed by hand goes along", async ({ page }) => {
    const errors = watchErrors(page);
    const address = await openAction(page, "Kopieren");
    const name = page.locator("[data-vorschau] input[name=name]");
    await expect(copyHeading(page)).toHaveText("Zählliste der Kopie in Kino Nord, Kühlung");
    await expect(name).toHaveValue(`${LOCATION} (Kopie)`);

    const asked = asking(page, address, "bereich");
    await choose(page, "Kino Ost", "Lager");
    // Marked as a script's: the server answers such a request with a status
    // where it would otherwise lead to the sign-in ('im_hintergrund').
    expect((await asked).headers()["x-requested-with"]).toBe("fetch");
    await expect(copyHeading(page)).toHaveText("Zählliste der Kopie in Kino Ost, Lager");
    await expect(page).toHaveURL(/\?bereich=\d+$/);
    // An untouched name is the server's suggestion, and in another area the
    // name is free.
    await expect(name).toHaveValue(LOCATION);
    expect(await stillHere(page)).toBe(true);

    // The name is typed now. The next preview brings it back.
    await name.fill("Kühlschrank Ost");
    await choose(page, "Kino Nord", "Theke");
    await expect(copyHeading(page)).toHaveText("Zählliste der Kopie in Kino Nord, Theke");
    await expect(page).toHaveURL(/\?bereich=\d+&name=K%C3%BChlschrank\+Ost$/);
    await expect(name).toHaveValue("Kühlschrank Ost");
    expect(await stillHere(page)).toBe(true);
    expect(errors).toEqual([]);
});

test("a choice overtaken by the next one is dropped on its way", async ({ page }) => {
    const errors = watchErrors(page);
    const address = await openAction(page, "Kopieren");
    const copy = page.locator("[data-vorschau]").getByRole("button", { name: "Kopieren" });
    const answer = await holdAnswer(page, { address });

    const firstAsked = asking(page, address, "bereich");
    await choose(page, "Kino Ost", "Lager");
    const first = await firstAsked;
    await expect.poll(answer.served).toBe(1);
    // The preview on screen is the old one: it cannot be confirmed now.
    await expect(copy).toBeDisabled();

    const dropped = page.waitForEvent("requestfailed", {
        predicate: (request) => request === first,
    });
    await choose(page, "Kino Nord", "Theke");
    expect((await dropped).failure().errorText).toBe("net::ERR_ABORTED");
    await expect(copyHeading(page)).toHaveText("Zählliste der Kopie in Kino Nord, Theke");
    await expect(copy).toBeEnabled();

    // The older answer arrives now and finds nobody waiting for it.
    answer.letGo();
    await expect(copyHeading(page)).toHaveText("Zählliste der Kopie in Kino Nord, Theke");
    expect(await stillHere(page)).toBe(true);
    expect(errors).toEqual([]);
});

test("an objection to the choice brings the whole page", async ({ page }) => {
    const errors = watchErrors(page);
    const address = await openAction(page, "Zusammenführen");

    const asked = asking(page, address, "quelle");
    await choose(page, null, "Tiefkühltruhe");
    expect((await asked).headers()["x-requested-with"]).toBe("fetch");
    // The objection is no preview: the page comes as a whole, with the choice.
    await expect(page.getByText(/Zusammenführen geht erst, wenn/)).toBeVisible();
    await expect(page).toHaveURL(/\?quelle=\d+$/);
    expect(await stillHere(page)).toBeUndefined();
    await expect(page.locator("select[data-vorschau-wahl]")).toHaveClass(/is-invalid/);
    expect(errors).toEqual([]);
});
