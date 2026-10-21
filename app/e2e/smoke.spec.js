/**
 * One pass through the app, as a counter makes it.
 *
 * Vitest mounts the interface in jsdom and checks the arithmetic, the build
 * only checks that it compiles. Neither notices when the app stays white in
 * the browser. This here starts the built app in a real Chromium, against a
 * Django with the demo stock ('bin/e2e-server.sh'). The demo's counter is
 * assigned to the running inventories, so the way through the house is open:
 * sign in, areas, locations, articles, a quantity, the search window.
 */

import { expect, test } from "@playwright/test";

import {
    articleHeading,
    openArticles,
    openCountable,
    rowTitled,
    rowTitles,
    watchErrors,
} from "./helpers.js";

test("a counter walks through the house and counts an article", async ({ page }) => {
    const errors = watchErrors(page);
    const articles = await openArticles(page);
    expect(articles.length).toBeGreaterThan(0);

    await test.step("the page fits the width and follows the device's colour scheme", async () => {
        // No horizontal scrolling: a list that is wider than the phone is a
        // list someone has to drag around. Checked on the narrow project too.
        const [fits, dark, theme] = await page.evaluate(() => [
            document.documentElement.scrollWidth <= window.innerWidth,
            window.matchMedia("(prefers-color-scheme: dark)").matches,
            document.documentElement.dataset.theme,
        ]);
        expect(fits).toBe(true);
        expect(theme).toBe(dark ? "dark" : "light");
    });

    await test.step("the search narrows the list by name", async () => {
        const search = page.locator("input[type=search]");
        const wanted = articles[0];
        const unrelated = (hits) =>
            hits.filter((hit) => !hit.toLowerCase().includes(wanted.toLowerCase()));
        await search.fill(wanted);
        await expect.poll(() => rowTitles(page)).toContain(wanted);
        await expect.poll(async () => unrelated(await rowTitles(page))).toEqual([]);
        await search.fill("");
        await expect.poll(() => rowTitles(page)).toEqual(articles);
    });

    await test.step("a quantity sticks to the article", async () => {
        const counted = await openCountable(page);
        const heading = articleHeading(page);

        // Typed is into the draft. Only moving on adopts it.
        await page.locator(".count-row input").first().fill("4");
        await page.locator("button.next").click();
        // "Next" leads to the next article: the heading changes, the form stays.
        await expect(heading).not.toHaveText(counted);
        const current = await heading.innerText();

        // Back to the list: it stands at the article someone comes from, not at the top.
        await page.locator("button.back").click();
        const marked = page.locator("button.row.marked");
        await expect(marked).toBeVisible();
        await expect(marked.locator(".text strong")).toHaveText(current);

        // The quantity has left the draft: opened again, the article shows it.
        await rowTitled(page, counted).first().click();
        await expect(heading).toHaveText(counted);
        await expect(page.locator(".count-row input").first()).toHaveValue("4");
    });

    expect(errors).toEqual([]);
});

test("the search window sticks to the top", async ({ page }) => {
    const errors = watchErrors(page);
    await openArticles(page);

    // Bottom right, within thumb's reach: count something nobody expected here.
    await page.locator("button.add").click();
    const dialog = page.locator(".dialog");
    await expect(dialog).toBeVisible();
    await dialog.locator("input[type=search]").fill("zz");

    // The window hangs at the top, because at the bottom the keyboard covered it.
    const box = await dialog.boundingBox();
    expect(box?.y).toBe(0);

    expect(errors).toEqual([]);
});

test("an article nobody expected here joins the list", async ({ page }) => {
    const errors = watchErrors(page);
    const articles = await openArticles(page);

    // The window offers what the house carries and has no row here yet.
    await page.locator("button.add").click();
    const hit = page.locator(".dialog button.hit").first();
    await expect(hit).toBeVisible();
    const name = await hit.locator("strong").innerText();
    expect(articles).not.toContain(name);
    await hit.click();

    const heading = articleHeading(page);
    await expect(heading).toHaveText(name);
    // Without a row nothing is expected: the pack sizes stand below for choice.
    if (!(await page.locator(".count-row input").count())) {
        await page.locator(".extras button.flat").first().click();
    }
    await page.locator(".count-row input").first().fill("2");
    await page.locator("button.next").click();
    // At the end of the list "Done" leads back, otherwise the next article opens.
    if (await heading.isVisible()) await page.locator("button.back").click();

    await expect.poll(() => rowTitles(page)).toContain(name);
    expect(errors).toEqual([]);
});
