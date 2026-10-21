/**
 * The choice of light or dark on the website goes to the account ('theme.js').
 *
 * The script runs in the head and in the administration, where 'shared.js' is
 * not there, and reads the CSRF token itself. Of two cookies of that name it
 * takes the last one, as Django does. Until 10/2026 it took the first: beside
 * a second cookie of that name, say from a site on a parent domain, Django
 * refused the token (403), and the choice stayed in this browser alone. Here
 * the second cookie applies to the pages of the catalog only, and there the
 * browser lists it first.
 */

import { expect, test } from "@playwright/test";

import { signInWebsite, watchErrors } from "./helpers.js";

// The names and accounts below come from the demo stock ('manage.py demo').
test.skip(!!process.env.E2E_BASE_URL, "needs the demo stock");

//: The demo's manager, see DEMO.md.
const MANAGER = { username: "leitung", password: "demo1234" };
//: Where the account keeps its choice.
const ACCOUNT = "/api/v1/ich/";

// The bar of the website, with its account menu, at a desk.
test.use({ viewport: { width: 1280, height: 900 } });

async function shown(page) {
    return (await (await page.request.get(ACCOUNT)).json()).darstellung;
}

test("the choice of the theme reaches the account beside a second CSRF cookie", async ({
    page,
}) => {
    const errors = watchErrors(page);
    const browser = page.context();
    await signInWebsite(page, MANAGER);
    const before = await shown(page);
    const own = (await browser.cookies()).find((cookie) => cookie.name === "csrftoken");
    const other = { name: "csrftoken", value: "x".repeat(64), domain: "localhost" };
    await browser.addCookies([{ ...other, path: "/katalog/" }]);
    await page.goto("/katalog/waren/");
    await page.waitForLoadState("domcontentloaded");
    const tokens = await page.evaluate(() =>
        document.cookie.split("; ").filter((part) => part.startsWith("csrftoken=")),
    );
    expect(tokens).toHaveLength(2);

    const sent = page.waitForRequest(
        (request) => new URL(request.url()).pathname === ACCOUNT && request.method() === "PATCH",
    );
    await page.getByRole("button", { name: MANAGER.username, exact: true }).click();
    await page.getByRole("button", { name: "Dunkel", exact: true }).click();
    const request = await sent;
    expect(request.headers()["x-csrftoken"]).toBe(own.value);
    expect((await request.response()).status()).toBe(200);
    expect(await shown(page)).toBe("dark");
    await expect(page.locator("html")).toHaveAttribute("data-bs-theme", "dark");

    // Back to the demo's state, for the tests after this one.
    const back = await page.request.patch(ACCOUNT, {
        data: { darstellung: before },
        headers: { "X-CSRFToken": own.value },
    });
    expect(back.ok()).toBeTruthy();
    expect(errors).toEqual([]);
});
