/**
 * A tab per inventory type, where the location holds rows of it.
 *
 * The demo's running run counts the monthly and the cup inventory
 * ('server/demo/inventories.py'). In Kino Nord the reusable cups come back
 * to the beverage store, so it shows both tabs. Kino Ost carries no cups:
 * its locations show none, although its inventory counts them too.
 */

import { expect, test } from "@playwright/test";

import { descend, rowTitled, signIn, watchErrors } from "./helpers.js";

// The names and accounts below come from the demo stock ('manage.py demo').
// Against another server ('E2E_BASE_URL') this does not hold.
test.skip(!!process.env.E2E_BASE_URL, "needs the demo stock");

const RUN = "Monatsinventur + Becherinventur";
const CUP = "Mehrwegbecher 0,3 l";

function inventoryOf(page, house) {
    return page.locator("button.row").filter({ hasText: house }).filter({ hasText: RUN });
}

test("the beverage store shows a tab per type, each with its own articles", async ({ page }) => {
    const errors = watchErrors(page);
    await signIn(page);
    await descend(page, inventoryOf(page, "Kino Nord"));
    await descend(page, rowTitled(page, "Lager").first());
    await descend(page, rowTitled(page, "Getränkelager").first());

    const tabs = page.getByRole("tab");
    await expect(tabs).toHaveCount(2);
    await expect(tabs.nth(0)).toContainText("Monatsinventur");
    await expect(tabs.nth(1)).toContainText("Becherinventur");
    // In front the monthly round, the cups only behind their tab.
    await expect(tabs.nth(0)).toHaveAttribute("aria-selected", "true");
    await expect(rowTitled(page, CUP)).toHaveCount(0);

    await tabs.nth(1).click();
    await expect(rowTitled(page, CUP)).toBeVisible();
    await expect(rowTitled(page, "Weißwein 0,2 l")).toHaveCount(0);
    expect(errors).toEqual([]);
});

test("tabs that do not fit scroll instead of squeezing", async ({ page }) => {
    // The demo has three types, and no location shows more than two tabs.
    // A strip narrower than two tabs is the same situation as five tabs at
    // phone width: the strip has to scroll, and the last tab stays reachable.
    const errors = watchErrors(page);
    await signIn(page);
    await descend(page, inventoryOf(page, "Kino Nord"));
    await descend(page, rowTitled(page, "Lager").first());
    await descend(page, rowTitled(page, "Getränkelager").first());
    await page.setViewportSize({ width: 240, height: 700 });

    const strip = page.getByRole("tablist");
    await expect
        .poll(() => strip.evaluate((node) => node.scrollWidth > node.clientWidth))
        .toBe(true);
    // Whether the page as a whole fits a phone is checked at 320 px by the
    // smoke run. This width is below any phone and only here to crowd the strip.

    const last = page.getByRole("tab").last();
    await last.click();
    await expect(last).toHaveAttribute("aria-selected", "true");
    await expect(rowTitled(page, CUP)).toBeVisible();
    expect(errors).toEqual([]);
});

test("a house without cups shows no tabs, although its inventory counts them", async ({ page }) => {
    const errors = watchErrors(page);
    await signIn(page);
    await descend(page, inventoryOf(page, "Kino Ost"));
    await descend(page); // the area, its locations
    await descend(page); // the location, its articles
    await expect(page.locator("input[type=search]")).toBeVisible();

    await expect(page.locator("button.row").first()).toBeVisible();
    await expect(page.getByRole("tab")).toHaveCount(0);
    expect(errors).toEqual([]);
});
