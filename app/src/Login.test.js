/**
 * What the login form says when it is not the password: the lock after too
 * many attempts (429) and a server that does not answer (5xx). Until
 * 10/2026 both read as "wrong password", and the right one looked wrong for
 * half an hour.
 */

import { mount, unmount } from "svelte";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import App from "./App.svelte";
import { apply } from "./services/language.svelte.js";
import { clearStorage, findButton } from "./test_support.js";

let target;
let app;

beforeEach(async () => {
    apply("de");
    await clearStorage();
    target = document.createElement("div");
    document.body.append(target);
});

afterEach(() => {
    if (app) unmount(app);
    app = null;
    target.remove();
});

async function signInAgainst(status, body) {
    globalThis.fetch = vi.fn(() =>
        Promise.resolve({ ok: false, status, text: () => Promise.resolve(body) }),
    );
    app = mount(App, { target });
    await vi.waitFor(() =>
        expect(target.querySelector("input[autocomplete=username]")).toBeTruthy(),
    );
    for (const [selector, value] of [
        ["input[autocomplete=username]", "zoe"],
        ["input[type=password]", "geheim"],
    ]) {
        const field = target.querySelector(selector);
        field.value = value;
        field.dispatchEvent(new Event("input", { bubbles: true }));
    }
    findButton(target, "Anmelden").click();
}

it("names the lock after too many attempts", async () => {
    await signInAgainst(429, JSON.stringify({ detail: "Zu viele Fehlversuche.", gesperrt: true }));

    await vi.waitFor(() =>
        expect(target.textContent).toContain(
            "Dieses Konto ist von hier aus vorübergehend gesperrt.",
        ),
    );
    expect(target.textContent).not.toContain("Passwort stimmt nicht");
});

it("says that the server does not answer, not that the password is wrong", async () => {
    await signInAgainst(503, "<html>Service Unavailable</html>");

    await vi.waitFor(() =>
        expect(target.textContent).toContain("Der Server antwortet gerade nicht."),
    );
    expect(target.textContent).not.toContain("Passwort stimmt nicht");
});
