/**
 * Two windows of the app in one browser: they share the storage and the sign-in.
 *
 * Whoever signs out in one window has signed out the other too. That one
 * loads anew and shows the login form, and what it still held in memory
 * goes nowhere: no job lands in the storage the first window just cleared,
 * and nothing goes out. Until 10/2026 the other window went on counting into
 * the cleared storage, and its numbers waited there for whoever signed in
 * next. jsdom knows no second window, so this shows only here.
 */

import { expect, test } from "@playwright/test";

import { descend, openCountable, signIn, signOut, text, watchErrors } from "./helpers.js";

/** What the storage of the origin still holds: jobs, and whether a sign-in is left. */
function storage(page) {
    return page.evaluate(
        () =>
            new Promise((resolve, reject) => {
                const opening = indexedDB.open("crosstally");
                opening.onerror = () => reject(opening.error);
                opening.onsuccess = () => {
                    const db = opening.result;
                    const reading = db.transaction(["queue", "state"], "readonly");
                    const jobs = reading.objectStore("queue").count();
                    const token = reading.objectStore("state").get("refresh_token");
                    reading.oncomplete = () => {
                        db.close();
                        resolve({ jobs: jobs.result, signedIn: Boolean(token.result) });
                    };
                    reading.onerror = () => reject(reading.error);
                };
            }),
    );
}

test("signing out in one window leaves the other nothing to write or send", async ({
    context,
    page,
}) => {
    const errors = watchErrors(page);
    await signIn(page);

    const other = await context.newPage();
    await test.step("the second window is signed in through the shared storage", async () => {
        await other.goto("/app/");
        await expect(other.locator("button.row").first()).toBeVisible();
        await descend(other); // the inventory, its areas
        await descend(other); // the area, its locations
        await descend(other); // the location, its articles
        await openCountable(other);
        await other.locator(".count-row input").first().fill("7");
    });

    const sent = [];
    other.on("request", (request) => {
        if (request.url().includes("/api/v1/zaehlpositionen")) sent.push(request.url());
    });

    await test.step("signed out in the first window", async () => {
        // The number typed in the second window waits as a draft in the shared
        // storage, so the first one asks before it deletes it, and is told to.
        await signOut(page, { anyway: true });
    });

    await test.step("the second one counts nothing more and loads anew", async () => {
        // Its next sync may come first and reload it already. Then there is no
        // button left to tap, and the outcome is the same.
        await other
            .locator("button.next")
            .click({ timeout: 2_000 })
            .catch(() => {});
        await expect(other.getByRole("heading", { name: text("login.title") })).toBeVisible();
        expect(sent).toEqual([]);
        expect(await storage(other)).toEqual({ jobs: 0, signedIn: false });
    });

    expect(errors).toEqual([]);
});
