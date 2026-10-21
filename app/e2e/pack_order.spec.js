/**
 * The packs of a product: "Reihenfolge speichern" sends their order in the
 * background.
 *
 * While its request is on its way the button stays disabled. Until 10/2026
 * a drag meanwhile made it pressable again, and a second click ran beside
 * the first. An arrow pressed meanwhile waits for the save, like a row
 * button of the counting list. The other way round, while an arrow is on
 * its way, the packs stand still. Here the requests are held back on their
 * way, as in 'count_list.spec.js'.
 */

import { expect, test } from "@playwright/test";

import {
    dragOntoNext,
    dragStarts,
    holdSaves,
    settled,
    signInWebsite,
    sortableOrder as order,
    sortableRows as rows,
    started,
    swapped,
    watchErrors,
} from "./helpers.js";

// The names and accounts below come from the demo stock ('manage.py demo').
test.skip(!!process.env.E2E_BASE_URL, "needs the demo stock");

//: The demo's manager, see DEMO.md.
const MANAGER = { username: "leitung", password: "demo1234" };
//: The demo's only product with three packs, and no other test needs their order.
const PRODUCT = "Schokoriegel";

// Like the counting list a page for the desk.
test.use({ viewport: { width: 1280, height: 900 } });

async function openProduct(page) {
    await signInWebsite(page, MANAGER);
    await page.goto(`/katalog/waren/?q=${encodeURIComponent(PRODUCT)}`);
    await page.getByRole("link", { name: PRODUCT, exact: true }).click();
    // Until the page has run its scripts: an arrow pressed before that
    // sends its form the ordinary way.
    await page.waitForLoadState("domcontentloaded");
    await rows(page).last().scrollIntoViewIfNeeded();
}

function saveButton(page) {
    return page.getByRole("button", { name: /^Reihenfolge (speichern|gespeichert)/ });
}

/** The saves go to the address of the form, not of the page. */
async function holdPackSaves(page, count) {
    const action = await page.locator("form:has([data-sortierbar])").getAttribute("action");
    return holdSaves(page, count, { address: action });
}

test("the save button stays disabled while its request is on its way", async ({ page }) => {
    const errors = watchErrors(page);
    await openProduct(page);
    const saves = await holdPackSaves(page, 1);
    const sent = await dragOntoNext(page, 0);
    await expect(saveButton(page)).toBeEnabled();

    await saveButton(page).click();
    await expect.poll(saves.arrived).toBe(1);
    // There and back again: the order differs from the stored one once
    // more, and the stored one is still the old one.
    await dragOntoNext(page, 0);
    await dragOntoNext(page, 0);
    await expect(saveButton(page)).toBeDisabled();

    saves.letGo(1);
    await settled(page, 1);
    await expect(saveButton(page)).toHaveText("Reihenfolge gespeichert ✓");
    await expect(saveButton(page)).toBeDisabled();
    expect(await started(page)).toBe(1);

    await page.reload();
    expect(await order(page)).toEqual(sent);
    expect(errors).toEqual([]);
});

test("an arrow pressed while the order is being saved waits for it", async ({ page }) => {
    const errors = watchErrors(page);
    await openProduct(page);
    const saves = await holdPackSaves(page, 1);
    const dragged = await dragOntoNext(page, 0);
    await saveButton(page).click();
    await expect.poll(saves.arrived).toBe(1);

    // The arrows keep the state they were drawn with. The pack now second
    // came first, so its arrow downwards is pressable. The arrows carry
    // their name in the title only.
    await rows(page).nth(1).getByTitle("Nach unten").click();
    // An arrow sent past the save is answered before the held one goes on.
    // Only then could the save undo its step.
    await settled(page, (await started(page)) - 1);
    saves.letGo(1);
    await settled(page, 2);
    const moved = swapped(dragged, 1);
    await expect.poll(() => order(page)).toEqual(moved);

    await page.reload();
    expect(await order(page)).toEqual(moved);
    expect(errors).toEqual([]);
});

test("the packs stand still while an arrow is on its way", async ({ page }) => {
    const errors = watchErrors(page);
    await openProduct(page);
    const before = await order(page);
    const saves = await holdPackSaves(page, 1);

    await rows(page).first().getByTitle("Nach unten").click();
    await expect.poll(saves.arrived).toBe(1);
    // Dragged now, the packs would make "Reihenfolge speichern" pressable
    // for an order the arrow's answer replaces.
    expect(await dragStarts(page, 1)).toBe(false);
    saves.letGo(1);
    const moved = swapped(before, 0);
    await expect.poll(() => order(page)).toEqual(moved);
    await expect(saveButton(page)).toHaveText("Reihenfolge gespeichert ✓");

    await page.reload();
    expect(await order(page)).toEqual(moved);
    expect(errors).toEqual([]);
});
