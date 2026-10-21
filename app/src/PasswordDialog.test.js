/**
 * Changing the password in the app.
 *
 * The counters have no other way: the web interface is closed to them.
 * Checked is what the change must not cost. The device stays signed in,
 * what waits in the queue stays, and the password itself lands nowhere in
 * the storage.
 */

import { mount, unmount } from "svelte";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import App from "./App.svelte";
import { close } from "./services/counting.svelte.js";
import { apply } from "./services/language.svelte.js";
import { db, remembered } from "./services/storage.js";
import { findButton, respond } from "./test_support.js";

const ZOE = {
    id: 1,
    vorname: "Zoe",
    benutzername: "zoe",
    sprache: "de",
    anwendungsname: "Zählwerk Nord",
};
const OLD = "altes-geheimnis";
const NEW = "neues-geheimnis-47";

/** A server that answers the change with 'answer'. Everything else it lets pass. */
function server(answer, who = ZOE) {
    const requests = [];
    globalThis.fetch = (url, options = {}) => {
        const path = url.replace("/api/v1", "");
        requests.push({ method: options.method ?? "GET", path, body: options.body });
        if (path === "/passwort/") return answer(options);
        if (path === "/ich/") return respond(who);
        if (path === "/inventuren/") return respond([]);
        if (path.startsWith("/zaehlpositionen/")) return respond({ id: 100, inventur_id: 1 });
        return respond({ detail: "?" }, 404);
    };
    return requests;
}

const changes = (requests) => requests.filter((entry) => entry.path === "/passwort/");

let target;
let app;

beforeEach(async () => {
    await Promise.all([
        db.state.clear(),
        db.inventories.clear(),
        db.positions.clear(),
        db.masterData.clear(),
        db.queue.clear(),
    ]);
    await db.state.bulkPut([
        { key: "me", value: ZOE },
        { key: "access_token", value: "a-old" },
        { key: "refresh_token", value: "r-old" },
    ]);
    close();
    apply("de");
    target = document.createElement("div");
    document.body.append(target);
});

afterEach(() => {
    if (app) unmount(app);
    app = null;
    target.remove();
    window.location.hash = "";
    globalThis.fetch = () => Promise.reject(new TypeError("kein Netz im Test"));
    close();
});

/** The text of the page as a person reads it: without the template's line breaks. */
const text = () => target.textContent.replace(/\s+/g, " ");

const button = (text) => findButton(target, text);

const dialog = () => target.querySelector("[role=dialog]");

async function openDialog() {
    app = mount(App, { target: target });
    await vi.waitFor(() => expect(target.querySelector("button.account")).toBeTruthy());
    target.querySelector("button.account").click();
    await vi.waitFor(() => expect(button("Passwort ändern")).toBeTruthy());
    button("Passwort ändern").click();
    await vi.waitFor(() => expect(dialog()).toBeTruthy());
}

function fill(oldPassword, newPassword, repeated = newPassword) {
    const fields = dialog().querySelectorAll("input[type=password]");
    for (const [index, value] of [oldPassword, newPassword, repeated].entries()) {
        fields[index].value = value;
        fields[index].dispatchEvent(new Event("input", { bubbles: true }));
    }
    dialog().querySelector("button[type=submit]").click();
}

it("names the tab after the application, as the administration calls it", async () => {
    server(() => respond({}));
    app = mount(App, { target: target });
    await vi.waitFor(() => expect(document.title).toBe("Zählwerk Nord"));
});

it("stands in the account menu and closes it on the way into the form", async () => {
    server(() => respond({}));

    await openDialog();

    expect(dialog().getAttribute("aria-label")).toBe("Passwort ändern");
    expect(dialog().querySelectorAll("input[type=password]")).toHaveLength(3);
    expect(button("Abmelden")).toBeUndefined();
});

it("takes the focus into the first field and keeps Tab inside", async () => {
    server(() => respond({}));

    await openDialog();

    const [oldPassword] = dialog().querySelectorAll("input[type=password]");
    expect(document.activeElement).toBe(oldPassword);
    // Shift+Tab from the first button lands on the last one, not in the page behind.
    const buttons = dialog().querySelectorAll("button");
    buttons[0].focus();
    buttons[0].dispatchEvent(
        new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true }),
    );
    expect(document.activeElement).toBe(buttons[buttons.length - 1]);
});

it("closes on Escape", async () => {
    server(() => respond({}));
    await openDialog();

    document.activeElement.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );

    await vi.waitFor(() => expect(dialog()).toBeNull());
});

it("changes the password and stays signed in with the new tokens", async () => {
    const requests = server(() => respond({ access: "a-new", refresh: "r-new" }));
    await openDialog();

    fill(OLD, NEW);

    await vi.waitFor(() => expect(text()).toContain("Dein Passwort ist geändert"));
    const [change] = changes(requests);
    expect(change.method).toBe("POST");
    expect(JSON.parse(change.body)).toEqual({ altes_passwort: OLD, neues_passwort: NEW });
    expect(await remembered("access_token")).toBe("a-new");
    expect(await remembered("refresh_token")).toBe("r-new");
    // Changed is changed: the form is gone, and with it the way to send twice.
    expect(dialog().querySelector("input[type=password]")).toBeNull();

    button("Schließen").click();
    await vi.waitFor(() => expect(dialog()).toBeNull());
});

it("keeps what waits in the queue", async () => {
    await db.queue.add({
        kind: "quantity",
        position: 100,
        payload: { eingabemenge: "3", eingabemenge_vorher: null },
    });
    server(() => respond({ access: "a-new", refresh: "r-new" }));
    await openDialog();
    const waiting = await db.queue.count();

    fill(OLD, NEW);

    await vi.waitFor(() => expect(text()).toContain("Dein Passwort ist geändert"));
    expect(await db.queue.count()).toBe(waiting);
});

it("puts the password nowhere in the storage", async () => {
    server(() => respond({ access: "a-new", refresh: "r-new" }));
    await openDialog();

    fill(OLD, NEW);

    await vi.waitFor(() => expect(text()).toContain("Dein Passwort ist geändert"));
    const stored = JSON.stringify([await db.state.toArray(), await db.queue.toArray()]);
    expect(stored).not.toContain(OLD);
    expect(stored).not.toContain(NEW);
});

it("does not send when the repetition differs", async () => {
    const requests = server(() => respond({ access: "a-new", refresh: "r-new" }));
    await openDialog();

    fill(OLD, NEW, "vertippt");

    await vi.waitFor(() => expect(text()).toContain("stimmen nicht überein"));
    expect(changes(requests)).toHaveLength(0);
    expect(await remembered("access_token")).toBe("a-old");
});

it("shows what the server says under the field it belongs to", async () => {
    server(() =>
        respond(
            {
                altes_passwort: ["Das alte Passwort stimmt nicht."],
                neues_passwort: ["Dieses Passwort ist zu kurz.", "Dieses Passwort ist zu üblich. "],
            },
            400,
        ),
    );
    await openDialog();

    fill("daneben", "kurz");

    await vi.waitFor(() => expect(text()).toContain("Das alte Passwort stimmt nicht."));
    const [oldField, newField] = dialog().querySelectorAll(".field");
    expect(oldField.textContent).toContain("Das alte Passwort stimmt nicht.");
    expect(newField.textContent).toContain("Dieses Passwort ist zu kurz.");
    expect(newField.textContent).toContain("Dieses Passwort ist zu üblich.");
    expect(oldField.querySelector("input").getAttribute("aria-invalid")).toBe("true");
    // The field keeps its name. The message describes it and does not rename it.
    expect(oldField.querySelector("label").textContent.trim()).toBe("Altes Passwort");
    const described = oldField.querySelector("input").getAttribute("aria-describedby");
    expect(dialog().querySelector(`#${described}`).textContent).toContain("stimmt nicht");
    // Nothing changed, so the sign-in stays what it was.
    expect(await remembered("access_token")).toBe("a-old");
    expect(await remembered("refresh_token")).toBe("r-old");
});

it("tells a dead spot from a rejection", async () => {
    server(() => Promise.reject(new TypeError("Funkloch")));
    await openDialog();

    fill(OLD, NEW);

    await vi.waitFor(() => expect(text()).toContain("Keine Verbindung zum Server"));
    expect(await remembered("refresh_token")).toBe("r-old");
    expect(await db.queue.count()).toBe(0);
});

it("names the lock after too many failed attempts", async () => {
    server(() => respond({ detail: "Zu viele Fehlversuche.", gesperrt: true }, 429));
    await openDialog();

    fill("daneben", NEW);

    // The lock of the sign-in: it says so, as the sign-in does.
    await vi.waitFor(() =>
        expect(text()).toContain("Dieses Konto ist von hier aus vorübergehend gesperrt."),
    );
    expect(await remembered("refresh_token")).toBe("r-old");
});

it("does not take a fault of the server for a wrong password", async () => {
    server(() => respond({ detail: "Server Error" }, 500));
    await openDialog();

    fill(OLD, NEW);

    await vi.waitFor(() => expect(text()).toContain("Das Passwort wurde nicht geändert"));
});

it("leads to the login form when the sign-in no longer applies", async () => {
    const answers = server(() => respond({ detail: "abgelaufen" }, 401));
    await openDialog();
    // The refresh is rejected too: the password was changed somewhere else.
    const known = globalThis.fetch;
    globalThis.fetch = (url, options) =>
        url.endsWith("/token/erneuern/")
            ? respond({ detail: "gesperrt" }, 401)
            : known(url, options);

    fill(OLD, NEW);

    await vi.waitFor(() => expect(dialog()).toBeNull());
    await vi.waitFor(() => expect(text()).toContain("Anmelden"));
    expect(target.querySelector("input[autocomplete=username]")).toBeTruthy();
    expect(changes(answers)).toHaveLength(1);
});

it("speaks English when the account does", async () => {
    const english = { ...ZOE, sprache: "en" };
    await db.state.put({ key: "me", value: english });
    server(() => respond({ access: "a-new", refresh: "r-new" }), english);
    app = mount(App, { target: target });
    await vi.waitFor(() => expect(target.querySelector("button.account")).toBeTruthy());
    target.querySelector("button.account").click();
    await vi.waitFor(() => expect(button("Change password")).toBeTruthy());
    button("Change password").click();
    await vi.waitFor(() => expect(dialog()).toBeTruthy());

    expect(text()).toContain("Old password");
    expect(text()).toContain("Repeat new password");
});
