/**
 * Weighing: vessel first, then the gross weight, the net is the server's.
 *
 * The scale is the most involved part of the form, and the net only arises
 * on the server from the tare of the vessel. Only a round trip shows that it
 * comes back. The location comes from the demo's blueprint
 * ('server/demo/houses.py'): 'Kino Nord', 'Theke', 'Süßwarenregal' weighs.
 */

import { expect, test } from "@playwright/test";

import {
    articleHeading,
    descend,
    openArticleWhere,
    rowTitled,
    signIn,
    text,
    watchErrors,
} from "./helpers.js";

// The names and accounts below come from the demo stock ('manage.py demo').
// Against another server ('E2E_BASE_URL') this does not hold.
test.skip(!!process.env.E2E_BASE_URL, "needs the demo stock");

const HOUSE = "Kino Nord";
const AREA = "Theke";
const LOCATION = "Süßwarenregal";

test("a weighing comes back with its net weight", async ({ page }) => {
    const errors = watchErrors(page);
    await signIn(page);
    await descend(page, page.locator("button.row").filter({ hasText: HOUSE }).first());
    await descend(page, rowTitled(page, AREA).first());
    await descend(page, rowTitled(page, LOCATION).first());

    const counted = await openArticleWhere(
        page,
        async (page) => (await page.locator(".weighing select").count()) > 0,
        "wird gewogen",
    );
    // The demo has weighed here already. One more vessel joins them.
    const saved = page.locator(".weighing .small", { hasText: text("entry.saved").split("{")[0] });
    const before = await saved.count();
    await page.getByRole("button", { name: text("entry.another_weighing") }).click();
    const weighing = page.locator(".weighing").last();

    // Nothing is preselected: a wrong tare would go unnoticed when counting.
    await expect(weighing.locator("input")).toHaveAttribute("readonly", "");
    await weighing.locator("select").selectOption({ index: 1 });
    await weighing.locator("input").fill("1500");
    await page.locator("button.next").click();
    await expect(articleHeading(page)).not.toHaveText(counted);

    // Opened again, the new row names what the server made of it.
    await page.locator("button.back").click();
    await rowTitled(page, counted).first().click();
    await expect(articleHeading(page)).toHaveText(counted);
    await expect(saved).toHaveCount(before + 1);

    // Corrected afterwards: the last bowl holds 100 g more, and the net
    // follows, in the article's unit. The correction goes out as a change of
    // the stored weighing.
    const net = async () => number(await saved.last().textContent());
    const netBefore = await net();
    const step = (await saved.last().textContent()).trim().endsWith("kg") ? 0.1 : 100;
    await page.locator(".weighing").last().locator("input").fill("1600");
    await page.locator("button.next").click();
    await expect(articleHeading(page)).not.toHaveText(counted);
    await page.locator("button.back").click();
    await rowTitled(page, counted).first().click();
    await expect(articleHeading(page)).toHaveText(counted);
    await expect(saved).toHaveCount(before + 1);
    await expect.poll(net).toBeCloseTo(netBefore + step, 3);

    expect(errors).toEqual([]);
});

/** The number out of "gespeichert: 1,5 kg", as the German format writes it. */
function number(text) {
    return Number(
        text
            .replace(/[^\d,.-]/g, "")
            .replace(/\./g, "")
            .replace(",", "."),
    );
}
