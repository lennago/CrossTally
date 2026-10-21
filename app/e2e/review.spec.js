/**
 * The check list of the website, in a real browser.
 *
 * Two things only a browser shows: whether the mark of "Refresh values" can
 * be seen, and what an older page does to notes written elsewhere. Until
 * 10/2026 the mark sat on table cells, where Bootstrap's own background
 * covered it. And on 2026-10-12 a closing from a second tab emptied every
 * note of an inventory.
 *
 * The counts change through the API, as the counting app sends them. The
 * running inventory of Kino Ost is used, the app's tests count in Kino Nord.
 * Only the difference by hand needs the cup inventory of Kino Nord, which
 * the app's tests read too: what a case changes there, it puts back.
 */

import { expect, test } from "@playwright/test";

import {
    holdSaves,
    inventoryAndRow,
    runningInventory,
    settled,
    signInWebsite,
    started,
    watchErrors,
} from "./helpers.js";

// The names and accounts below come from the demo stock ('manage.py demo').
test.skip(!!process.env.E2E_BASE_URL, "needs the demo stock");

//: The demo's manager, see DEMO.md.
const MANAGER = { username: "leitung", password: "demo1234" };

test("Refresh values marks what changed, visibly also in the table", async ({ page, request }) => {
    const { headers, inventory, row } = await inventoryAndRow(request);
    await signInWebsite(page, MANAGER);
    await page.goto(`/inventuren/${inventory.id}/pruefen/?zuruecksetzen=1`);

    const before = row.eingabemenge;
    const counted = String(Number(before ?? 0) + 3);
    const sent = await request.patch(`/api/v1/zaehlpositionen/${row.id}/`, {
        headers,
        data: { eingabemenge: counted, eingabemenge_vorher: before },
    });
    expect(sent.ok()).toBeTruthy();

    // The page asks every 30 seconds and when the tab comes back. That second way now.
    await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")));
    const button = page.locator("[data-auffrischen]");
    await expect(button).toHaveClass(/btn-warning/);
    await button.click();
    await expect(page.locator("span[data-geaendert]")).toContainText("1");

    const marked = page.locator(`[data-wert="ist-${row.artikel_id}"]`);
    await expect(marked).toHaveClass(/wert-neu/);
    // Compared against the same mark outside a table: there it always showed.
    const [inTable, reference] = await marked.evaluate((element) => {
        const probe = document.createElement("span");
        probe.className = "wert-neu";
        probe.textContent = "0";
        document.body.append(probe);
        const seen = [element, probe].map((node) => getComputedStyle(node).backgroundColor);
        probe.remove();
        return seen;
    });
    expect(inTable).toBe(reference);
    expect(inTable).not.toBe("rgba(0, 0, 0, 0)");

    // Back to the demo's state, for the tests after this one.
    await request.patch(`/api/v1/zaehlpositionen/${row.id}/`, {
        headers,
        data: { eingabemenge: before, eingabemenge_vorher: counted },
    });
});

test("an older page does not empty a note written in another tab", async ({ browser, request }) => {
    const { inventory, row } = await inventoryAndRow(request);
    const context = await browser.newContext();
    const older = await context.newPage();
    await signInWebsite(older, MANAGER);
    const address = `/inventuren/${inventory.id}/pruefen/?zuruecksetzen=1`;
    const field = `textarea[name="notiz_${row.artikel_id}"]`;

    // As on 2026-10-12: one tab open before the notes, written in another one.
    await older.goto(address);
    await expect(older.locator(field)).toHaveValue("");

    const newer = await context.newPage();
    await newer.goto(address);
    await newer.locator(field).fill("-3 in der Kühlung");
    await newer.locator("h1").click();
    await expect(newer.locator("[data-notizstand]")).toHaveText("Notizen gesichert");

    // The older tab sends all its rows. Closing would do the same, and the
    // demo's inventory should stay open for the tests after this one.
    await older.getByRole("button", { name: "Notizen speichern" }).click();
    await expect(older.locator("[data-notizstand]")).toHaveText("Notizen gesichert");

    await newer.reload();
    await expect(newer.locator(field)).toHaveValue("-3 in der Kühlung");
    await context.close();
});

test("a note for an inventory closed meanwhile stays in its field and says why", async ({
    page,
    request,
}) => {
    const errors = watchErrors(page, { allowNetwork: true });
    const { inventory, row } = await inventoryAndRow(request);
    await signInWebsite(page, MANAGER);
    // A closed inventory of the same house. Closing the demo's running one
    // would take it from the tests after this one.
    await page.goto("/inventuren/?status=ABGESCHLOSSEN");
    const closed = await page
        .getByRole("link", { name: "Kino Ost", exact: true })
        .first()
        .getAttribute("href");
    const review = `/inventuren/${inventory.id}/pruefen/`;
    await page.goto(`${review}?zuruecksetzen=1`);
    const field = page.locator(`textarea[name="notiz_${row.artikel_id}"]`);
    const state = page.locator("[data-notizstand]");
    const save = page.getByRole("button", { name: "Notizen speichern" });
    const before = await field.inputValue();

    // The saves of this page go to the closed inventory: for the server that
    // is the same request as one from a page that was open while someone
    // closed. Until 10/2026 it answered with a redirect, 'fetch' followed it
    // to a page that said 200, and the state read "Notizen gesichert".
    const ofThisPage = (url) => url.pathname === review;
    await page.route(ofThisPage, (route) =>
        route.request().method() === "POST"
            ? route.continue({ url: new URL(`${closed}pruefen/`, page.url()).href })
            : route.continue(),
    );
    await field.fill("Im Lager nachzählen");
    await page.locator("h1").click();
    await expect(state).toHaveText(
        "Notizen nicht gesichert, denn die Inventur ist inzwischen abgeschlossen.",
    );
    await expect(field).toHaveValue("Im Lager nachzählen");
    // The row stays open: it still says what it showed, and leaving the
    // page would send it once more.
    await expect(page.locator(`[name="notiz_vorher_${row.artikel_id}"]`)).toHaveValue(before);

    // Back to the demo's state, for the tests after this one.
    await page.unroute(ofThisPage);
    await field.fill(before);
    await save.click();
    await expect(state).toHaveText("Notizen gesichert");
    expect(errors).toEqual([]);
});

test("Notizen speichern while a field's save is on its way goes out after it", async ({
    page,
    request,
}) => {
    const errors = watchErrors(page);
    const { inventory } = await inventoryAndRow(request);
    await signInWebsite(page, MANAGER);
    const review = `/inventuren/${inventory.id}/pruefen/`;
    await page.goto(`${review}?zuruecksetzen=1`);
    const names = await page
        .locator('textarea[name^="notiz_"]')
        .evaluateAll((fields) => fields.slice(0, 2).map((field) => field.name));
    const [first, second] = names.map((name) => page.locator(`textarea[name="${name}"]`));
    const before = [await first.inputValue(), await second.inputValue()];
    const state = page.locator("[data-notizstand]");
    const save = page.getByRole("button", { name: "Notizen speichern" });

    const saves = await holdSaves(page, 1);
    await first.fill("Regal oben");
    // Leaving the first field sends its row, and that save hangs on the way.
    await second.click();
    await expect.poll(saves.arrived).toBe(1);
    await second.fill("Regal unten");
    // Leaves the second field, then sends every row. Until 10/2026 both
    // were dropped while the first save was on its way.
    await save.click();
    saves.letGo(1);
    await expect(state).toHaveText("Notizen gesichert");
    expect(await started(page)).toBe(2);

    // Another tab finds both. A reload here would send what is open on leaving.
    const other = await page.context().newPage();
    await other.goto(review);
    for (const [name, text] of [
        [names[0], "Regal oben"],
        [names[1], "Regal unten"],
    ])
        await expect(other.locator(`textarea[name="${name}"]`)).toHaveValue(text);
    await other.close();

    // Back to the demo's state, for the tests after this one.
    await first.fill(before[0]);
    await second.fill(before[1]);
    await save.click();
    await expect(state).toHaveText("Notizen gesichert");
    expect(errors).toEqual([]);
});

test("closing right after a note waits for its save and sends the state it brought", async ({
    page,
    request,
}) => {
    const errors = watchErrors(page);
    const { inventory, row } = await inventoryAndRow(request);
    await signInWebsite(page, MANAGER);
    const review = `/inventuren/${inventory.id}/pruefen/`;
    await page.goto(`${review}?zuruecksetzen=1`);
    const field = page.locator(`textarea[name="notiz_${row.artikel_id}"]`);
    const before = await field.inputValue();
    const loaded = await page.locator("[data-bemerkungsstand-feld]").inputValue();
    const state = page.locator("[data-notizstand]");

    // The save of the note is held on its way. The closing is caught and
    // answered here: closing the demo's running inventory would take it
    // from the tests after this one.
    const gate = Promise.withResolvers();
    const closing = Promise.withResolvers();
    let brought;
    await page.route(
        (url) => url.pathname === review,
        async (route) => {
            const sent = route.request();
            if (sent.method() !== "POST") return route.continue();
            if (sent.headers()["x-requested-with"] !== "fetch") {
                closing.resolve(new URLSearchParams(sent.postData()).get("bemerkungsstand"));
                return route.fulfill({ status: 303, headers: { location: review } });
            }
            await gate.promise;
            const answer = await route.fetch();
            brought = answer.headers()["x-bemerkungsstand"];
            return route.fulfill({ response: answer });
        },
    );
    await field.fill("Regal hinten");
    page.once("dialog", (dialog) => dialog.accept());
    // Leaving the field for the button starts its save. Until 10/2026 the
    // closing went out at once with the state of the page's loading, and
    // the page's own note looked like someone else's: closing stopped.
    await page.getByRole("button", { name: "Inventur abschließen" }).click();
    gate.resolve();
    const sent = await closing.promise;
    expect(brought).toBeTruthy();
    expect(brought).not.toBe(loaded);
    expect(sent).toBe(brought);

    // Back to the demo's state, for the tests after this one.
    await page.waitForURL(review);
    await field.fill(before);
    await page.getByRole("button", { name: "Notizen speichern" }).click();
    await expect(state).toHaveText("Notizen gesichert");
    expect(errors).toEqual([]);
});

/** Until the page has heard back from its next save of the check list. */
function nextSave(page, review) {
    return page.waitForResponse(
        (answer) =>
            answer.request().method() === "POST" && new URL(answer.url()).pathname === review,
    );
}

test("a difference that is no number leaves the note of its row stored and moved along", async ({
    page,
    request,
}) => {
    const errors = watchErrors(page, { allowNetwork: true });
    const inventory = await runningInventory(request, "Kino Nord", "Becherinventur");
    await signInWebsite(page, MANAGER);
    const review = `/inventuren/${inventory.id}/pruefen/`;
    await page.goto(`${review}?zuruecksetzen=1`);
    const note = page.getByRole("textbox", { name: "Notiz zu Mehrwegbecher 0,3 l", exact: true });
    const difference = page.getByRole("textbox", {
        name: "Differenz zu Mehrwegbecher 0,3 l",
        exact: true,
    });
    const before = [await note.inputValue(), await difference.inputValue()];
    const state = page.locator("[data-notizstand]");
    const noNumber =
        "Nicht gespeichert, weil die Differenz keine Zahl ist (höchstens drei " +
        "Nachkommastellen): Mehrwegbecher 0,3 l.";

    // Leaving the difference sends its row: nothing stored, the server says why.
    await difference.fill("zwölf");
    await note.click();
    await expect(state).toHaveText(noNumber);
    // The note goes out with the same difference: stored, but the answer is a 400.
    await note.fill("Spülmaschine leer");
    let answer = nextSave(page, review);
    await page.locator("h1").click();
    expect((await answer).status()).toBe(400);

    // Changed once more before reloading. Until 10/2026 the row still said
    // what it showed at loading, and the page heard that someone else had
    // written there.
    await note.fill("Spülmaschine halb leer");
    answer = nextSave(page, review);
    await page.locator("h1").click();
    expect(await (await answer).text()).toBe(noNumber);
    // The difference was never stored and still says what it showed.
    await difference.fill("-13");
    answer = nextSave(page, review);
    await page.locator("h1").click();
    expect((await answer).status()).toBe(204);
    await expect(state).toHaveText("Notizen gesichert");

    const other = await page.context().newPage();
    await other.goto(review);
    const exactly = (name) => other.getByRole("textbox", { name, exact: true });
    await expect(exactly("Notiz zu Mehrwegbecher 0,3 l")).toHaveValue("Spülmaschine halb leer");
    await expect(exactly("Differenz zu Mehrwegbecher 0,3 l")).toHaveValue("-13");
    await other.close();

    // Back to the demo's state, for the tests after this one.
    await note.fill(before[0]);
    await difference.fill(before[1]);
    await page.getByRole("button", { name: "Notizen speichern" }).click();
    await expect(state).toHaveText("Notizen gesichert");
    expect(errors).toEqual([]);
});

test("after a save that stored the other rows, changing one of them again goes through", async ({
    page,
    request,
}) => {
    const errors = watchErrors(page, { allowNetwork: true });
    const { inventory } = await inventoryAndRow(request);
    await signInWebsite(page, MANAGER);
    const review = `/inventuren/${inventory.id}/pruefen/`;
    await page.goto(`${review}?zuruecksetzen=1`);
    const names = await page
        .locator('textarea[name^="notiz_"]')
        .evaluateAll((fields) => fields.slice(0, 2).map((field) => field.name));
    const [mine, theirs] = names.map((name) => page.locator(`textarea[name="${name}"]`));
    const before = [await mine.inputValue(), await theirs.inputValue()];
    const theirsShown = page.locator(`[name="${names[1].replace("_", "_vorher_")}"]`);
    const state = page.locator("[data-notizstand]");
    const save = page.getByRole("button", { name: "Notizen speichern" });

    // Another tab writes in the second row after this page was loaded.
    const other = await page.context().newPage();
    await other.goto(review);
    await other.locator(`textarea[name="${names[1]}"]`).fill("aus einem anderen Tab");
    await other.locator("h1").click();
    await expect(other.locator("[data-notizstand]")).toHaveText("Notizen gesichert");

    // Leaving the second row sends it, and that save hangs on its way. "Notizen
    // speichern" then sends both rows together: the first is stored, the second
    // is not, for someone else wrote there.
    const saves = await holdSaves(page, 1);
    await theirs.fill("Regal unten");
    await mine.click();
    await expect.poll(saves.arrived).toBe(1);
    await mine.fill("Regal oben");
    await save.click();
    saves.letGo(1);
    await expect.poll(() => page.evaluate(() => window.saves.settled)).toBe(2);
    await expect(state).toContainText("jemand anderes dort etwas eingetragen hat");

    // The first row once more. Until 10/2026 it still said what it showed at
    // loading, and the answer was that someone else had written there.
    await mine.fill("Regal oben links");
    const answer = nextSave(page, review);
    await page.locator("h1").click();
    expect((await answer).status()).toBe(204);
    // The second row still says what it showed: its text is no own entry.
    await expect(theirsShown).toHaveValue(before[1]);

    await other.reload();
    await expect(other.locator(`textarea[name="${names[0]}"]`)).toHaveValue("Regal oben links");
    await expect(other.locator(`textarea[name="${names[1]}"]`)).toHaveValue(
        "aus einem anderen Tab",
    );
    await other.close();

    // Back to the demo's state, for the tests after this one. Taking the
    // other tab's text first: it is stored, so sending it is no conflict.
    await theirs.fill("aus einem anderen Tab");
    await save.click();
    await expect(state).toHaveText("Notizen gesichert");
    await mine.fill(before[0]);
    await theirs.fill(before[1]);
    await save.click();
    await expect(state).toHaveText("Notizen gesichert");
    expect(errors).toEqual([]);
});

test("text typed while its save is on its way goes out when the field is left", async ({
    page,
    request,
}) => {
    const errors = watchErrors(page);
    const { inventory, row } = await inventoryAndRow(request);
    await signInWebsite(page, MANAGER);
    const review = `/inventuren/${inventory.id}/pruefen/`;
    await page.goto(`${review}?zuruecksetzen=1`);
    const field = page.locator(`textarea[name="notiz_${row.artikel_id}"]`);
    const before = await field.inputValue();
    const state = page.locator("[data-notizstand]");

    // Leaving the field sends the note, and that save hangs on its way.
    const saves = await holdSaves(page, 1);
    await field.fill("Regal");
    await page.locator("h1").click();
    await expect.poll(saves.arrived).toBe(1);
    // Back in the field, typed further before the answer.
    await field.click();
    await field.press("End");
    await field.pressSequentially(" hinten");
    saves.letGo(1);
    await settled(page, 1);
    // Until 10/2026 the answer took the field for saved, and leaving it sent nothing.
    await page.locator("h1").click();
    await expect.poll(() => started(page)).toBe(2);
    await expect(state).toHaveText("Notizen gesichert");

    const other = await page.context().newPage();
    await other.goto(review);
    await expect(other.locator(`textarea[name="notiz_${row.artikel_id}"]`)).toHaveValue(
        "Regal hinten",
    );
    await other.close();

    // Back to the demo's state, for the tests after this one.
    await field.fill(before);
    await page.getByRole("button", { name: "Notizen speichern" }).click();
    await expect(state).toHaveText("Notizen gesichert");
    expect(errors).toEqual([]);
});
