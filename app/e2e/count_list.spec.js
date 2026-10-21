/**
 * The counting list of the website: rows moved while a save is on its way.
 *
 * Every release saves the order in the background. Until 10/2026 each one
 * sent a request of its own, and the server kept whichever arrived last. A
 * slow first save could overwrite the drag after it, and nothing said so
 * until the next save. Now a save waits for the one on its way and then
 * sends the newest order. A row button, "Save" and "Refresh" wait for it
 * too. Here the first save is held back on its way, and afterwards the
 * server has to hold the order the page shows. The other way round, once
 * such a request is on its way, the list stands still until it comes back.
 */

import { expect, test } from "@playwright/test";

import {
    dragOntoNext,
    dragStarts,
    holdAnswer,
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
//: Seven rows in a single section, and no other test needs their order.
const LOCATION = "Kühlschrank Theke";

// The counting list is kept at a desk. At phone width the sticky bars cover
// rows, and Sortable scrolls the page once the pointer comes near an edge.
test.use({ viewport: { width: 1280, height: 900 } });

async function openCountingList(page) {
    await signInWebsite(page, MANAGER);
    await page.goto("/haeuser/");
    await page.getByRole("link", { name: new RegExp(`^${LOCATION}`) }).click();
    await page.getByRole("link", { name: "Zählliste bearbeiten" }).click();
    // Until the page has run its scripts: a row button pressed before that
    // sends its form the ordinary way, and the list never stands still.
    await page.waitForLoadState("domcontentloaded");
}

function downButton(page, index) {
    // Exact: "Ganz nach unten" contains the words too.
    return rows(page).nth(index).getByRole("button", { name: "Nach unten", exact: true });
}

function upButton(page, index) {
    return rows(page).nth(index).getByRole("button", { name: "Nach oben", exact: true });
}

/** The row moved down by 'downButton' back up, and the server holds the order of before. */
async function moveBack(page, index, before) {
    await upButton(page, index + 1).click();
    await expect.poll(() => order(page)).toEqual(before);
    await page.reload();
    expect(await order(page)).toEqual(before);
}

function refreshButton(page) {
    return page.getByRole("button", { name: "Aktualisieren" });
}

/**
 * Until a refresh that went out at once is done.
 *
 * One that waits for the save has fetched nothing yet. Only a refresh sent
 * past the save could bring the list back without the drags.
 */
function refreshSentPast(page) {
    return expect
        .poll(() =>
            page.evaluate(
                () =>
                    window.reads > 0 &&
                    document.querySelector("[data-neu-laden]").hasAttribute("aria-busy"),
            ),
        )
        .toBe(false);
}

/**
 * Two drags while the first save is held back, then it arrives or fails.
 *
 * Returns the order shown.
 */
async function dragTwiceDuringFirstSave(page, outcome) {
    const saves = await holdSaves(page, 1);
    await dragOntoNext(page, 0);
    await expect.poll(saves.arrived).toBe(1);
    const shown = await dragOntoNext(page, 2);
    // A save the second drag sent at once is answered before the held one
    // goes on. Only then could the held one overwrite it.
    await settled(page, (await started(page)) - 1);
    saves.letGo(1, outcome);
    // Each drag ends in a save: the second one went out at once, or it
    // follows once the held one is answered.
    await settled(page, 2);
    return shown;
}

test("a drag during a slow save is not lost", async ({ page }) => {
    const errors = watchErrors(page);
    await openCountingList(page);
    const shown = await dragTwiceDuringFirstSave(page, "arrives");

    await page.reload();
    expect(await order(page)).toEqual(shown);
    expect(errors).toEqual([]);
});

test("after a failed save the newest order still goes out", async ({ page }) => {
    // The failed request shows in the console.
    const errors = watchErrors(page, { allowNetwork: true });
    await openCountingList(page);
    const shown = await dragTwiceDuringFirstSave(page, "fails");

    // The warning of the failed save goes once the next one has arrived.
    await expect(page.locator("[data-sicherungshinweis]")).toHaveCount(0);
    await page.reload();
    expect(await order(page)).toEqual(shown);
    expect(errors).toEqual([]);
});

for (const outcome of ["arrives", "fails"]) {
    test(`a row button waits for the save on its way (${outcome})`, async ({ page }) => {
        const errors = watchErrors(page, { allowNetwork: outcome === "fails" });
        await openCountingList(page);
        const saves = await holdSaves(page, 1);
        await dragOntoNext(page, 0);
        await expect.poll(saves.arrived).toBe(1);
        const dragged = await dragOntoNext(page, 2);

        await downButton(page, 4).click();
        await expect(downButton(page, 4)).toBeDisabled();
        // A button sent past the save is answered before the held one goes
        // on. Only then could the save undo its step.
        await settled(page, (await started(page)) - 1);
        saves.letGo(1, outcome);
        // The save and the button, nothing after them. A save of the dragged
        // order behind the button would undo its step.
        await settled(page, 2);
        const moved = swapped(dragged, 4);
        await expect.poll(() => order(page)).toEqual(moved);
        expect(await started(page)).toBe(2);
        // The button was disabled while it waited. Whoever works by keyboard
        // still finds the focus on it afterwards, in its new place.
        await expect(downButton(page, 5)).toBeFocused();
        await expect(page.locator("[data-sicherungshinweis]")).toHaveCount(0);

        await page.reload();
        expect(await order(page)).toEqual(moved);
        expect(errors).toEqual([]);
    });
}

test("'Save' waits for the order on its way before the page goes", async ({ page }) => {
    const errors = watchErrors(page);
    await openCountingList(page);
    const address = page.url();
    const saves = await holdSaves(page, 1);
    await dragOntoNext(page, 0);
    await expect.poll(saves.arrived).toBe(1);
    const shown = await dragOntoNext(page, 2);

    await page.getByRole("button", { name: "Speichern", exact: true }).click();
    // Sent at once, the page would be gone, and on a slow network the save
    // could still arrive after it. Here the browser would drop the held save
    // with the page, so the counter tells: it counts a submit at once.
    expect(await page.evaluate(() => window.saves?.leaving)).toBe(0);
    saves.letGo(1);
    await expect(page).not.toHaveURL(address);

    await page.goto(address);
    expect(await order(page)).toEqual(shown);
    expect(errors).toEqual([]);
});

test("'Refresh' waits for the save on its way and the one noted behind it", async ({ page }) => {
    const errors = watchErrors(page);
    await openCountingList(page);
    const saves = await holdSaves(page, 1);
    await dragOntoNext(page, 0);
    await expect.poll(saves.arrived).toBe(1);
    const shown = await dragOntoNext(page, 2);
    // After the refresh the form is a new one, without this mark.
    await page.locator("[data-liste] form").evaluate((form) => (form.dataset.before = ""));

    await refreshButton(page).click();
    await refreshSentPast(page);
    saves.letGo(1);
    await expect(refreshButton(page)).not.toHaveAttribute("aria-busy");
    await expect(page.locator("[data-liste] form[data-before]")).toHaveCount(0);
    // The fresh list carries both drags: the save noted behind the held one
    // went out before the page was fetched.
    expect(await order(page)).toEqual(shown);
    expect(await started(page)).toBe(2);

    await page.reload();
    expect(await order(page)).toEqual(shown);
    expect(errors).toEqual([]);
});

test("'Refresh' beside a waiting row button leaves the list to its answer", async ({ page }) => {
    const errors = watchErrors(page);
    await openCountingList(page);
    const saves = await holdSaves(page, 1);
    const dragged = await dragOntoNext(page, 0);
    await expect.poll(saves.arrived).toBe(1);

    await downButton(page, 4).click();
    await refreshButton(page).click();
    await settled(page, (await started(page)) - 1);
    await refreshSentPast(page);
    saves.letGo(1);
    await settled(page, 2);
    const moved = swapped(dragged, 4);
    await expect.poll(() => order(page)).toEqual(moved);
    // Fetched beside the button, the page could come back from the server
    // before the button's step and replace the list after its answer.
    expect(await page.evaluate(() => window.reads)).toBe(0);
    await expect(refreshButton(page)).not.toHaveAttribute("aria-busy");

    await page.reload();
    expect(await order(page)).toEqual(moved);
    expect(errors).toEqual([]);
});

// "Refresh" and a row button both bring the list anew. Until 10/2026 they
// ran beside each other, and the later answer replaced the list, even the
// older one. Here the answer of "Refresh" comes from the server before the
// button's step and arrives after it.

test("a row button while 'Refresh' is on its way: the older list is not shown", async ({
    page,
}) => {
    const errors = watchErrors(page);
    await openCountingList(page);
    const before = await order(page);
    const answer = await holdAnswer(page);

    await refreshButton(page).click();
    await expect.poll(answer.served).toBe(1);
    await downButton(page, 4).click();
    const moved = swapped(before, 4);
    await expect.poll(() => order(page)).toEqual(moved);
    answer.letGo();
    await expect(refreshButton(page)).not.toHaveAttribute("aria-busy");
    expect(await order(page)).toEqual(moved);

    await page.reload();
    expect(await order(page)).toEqual(moved);
    await moveBack(page, 4, before);
    expect(errors).toEqual([]);
});

test("'Refresh' while a row button is on its way waits for its answer", async ({ page }) => {
    const errors = watchErrors(page);
    await openCountingList(page);
    const before = await order(page);
    const saves = await holdSaves(page, 1);
    const answer = await holdAnswer(page);

    await downButton(page, 4).click();
    await expect.poll(saves.arrived).toBe(1);
    await refreshButton(page).click();
    // Sent beside the button, as until 10/2026, the page has to come back
    // from the server before the button's step reaches it.
    if (await page.evaluate(() => window.reads)) await expect.poll(answer.served).toBe(1);
    saves.letGo(1);
    const moved = swapped(before, 4);
    await expect.poll(() => order(page)).toEqual(moved);
    answer.letGo();
    await expect(refreshButton(page)).not.toHaveAttribute("aria-busy");
    expect(await order(page)).toEqual(moved);
    // Its answer brought the list anew: nothing to fetch beside it.
    expect(await page.evaluate(() => window.reads)).toBe(0);

    await page.reload();
    expect(await order(page)).toEqual(moved);
    await moveBack(page, 4, before);
    expect(errors).toEqual([]);
});

// The other way round: once a request is on its way that brings the list
// back new, a row dragged meanwhile would save an order the new list
// replaces. Its save could still arrive after the request, and the server
// would hold another order than the page shows. So nothing can be dragged
// until the new list is there.

test("the list stands still while a row button is on its way", async ({ page }) => {
    const errors = watchErrors(page);
    await openCountingList(page);
    const before = await order(page);
    const saves = await holdSaves(page, 1);

    await downButton(page, 4).click();
    await expect.poll(saves.arrived).toBe(1);
    expect(await dragStarts(page, 0)).toBe(false);
    saves.letGo(1);
    const moved = swapped(before, 4);
    await expect.poll(() => order(page)).toEqual(moved);

    // The new list can be dragged again, and saves.
    const dragged = await dragOntoNext(page, 0);
    await settled(page, 2);
    await page.reload();
    expect(await order(page)).toEqual(dragged);
    expect(errors).toEqual([]);
});

test("the list stands still while 'Refresh' is on its way", async ({ page }) => {
    const errors = watchErrors(page);
    await openCountingList(page);
    const saves = await holdSaves(page, 1, { method: "GET" });

    await refreshButton(page).click();
    await expect.poll(saves.arrived).toBe(1);
    expect(await dragStarts(page, 0)).toBe(false);
    saves.letGo(1);
    await expect(refreshButton(page)).not.toHaveAttribute("aria-busy");

    const dragged = await dragOntoNext(page, 0);
    await settled(page, 1);
    await page.reload();
    expect(await order(page)).toEqual(dragged);
    expect(errors).toEqual([]);
});

test("the list stands still while 'Save' leaves the page", async ({ page }) => {
    const errors = watchErrors(page);
    await openCountingList(page);
    const address = page.url();
    // A page on its way out cannot be pressed in the test: Playwright waits
    // for the next one. So the page notes how its form stood as it left. The
    // mark is the one that keeps a row button's list still (see above), and
    // the switch to the other tab leaves the same way.
    await page.evaluate(() =>
        document.addEventListener("submit", (event) => {
            if (event.defaultPrevented) return;
            sessionStorage.setItem(
                "busyWhenLeaving",
                event.target.getAttribute("aria-busy") ?? "no",
            );
        }),
    );

    await page.getByRole("button", { name: "Speichern", exact: true }).click();
    await expect(page).not.toHaveURL(address);
    expect(await page.evaluate(() => sessionStorage.getItem("busyWhenLeaving"))).toBe("true");
    expect(errors).toEqual([]);
});
