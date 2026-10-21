/**
 * Changing the e-mail address in the app.
 *
 * The counters have no other way: the web interface is closed to them.
 * The server decides whether the address applies now or waits for the link
 * in the mail. Checked is that the dialog says which, that the messages
 * stand under their fields, and that the sign-in stays untouched.
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
    email: "zoe@example.org",
    hat_passwort: true,
    mail: true,
};
const NEW = "zoe@example.com";
const PASSWORD = "altes-geheimnis";

/**
 * A server that answers the change with 'answer'. Everything else it lets pass.
 *
 * An account without a password confirms with a passkey first: the server's
 * question and its confirmation are always there.
 */
function server(answer, who = ZOE) {
    const requests = [];
    globalThis.fetch = (url, options = {}) => {
        const path = url.replace("/api/v1", "");
        requests.push({ method: options.method ?? "GET", path, body: options.body });
        if (path === "/email/") return answer(options);
        if (path === "/passkey/bestaetigen/optionen/")
            return respond({ options: { challenge: "AQID" }, challenge: "gefragt" });
        if (path === "/passkey/bestaetigen/") return respond({ bestaetigung: "bestaetigt" });
        if (path === "/ich/") return respond(who);
        if (path === "/inventuren/") return respond([]);
        return respond({ detail: "?" }, 404);
    };
    return requests;
}

const changes = (requests) => requests.filter((entry) => entry.path === "/email/");

const bytes = (...values) => Uint8Array.from(values).buffer;

/** A device with a key of the account, which signs on demand. */
function device() {
    globalThis.navigator.credentials = {
        get: vi.fn(() =>
            Promise.resolve({
                id: "AQID",
                rawId: bytes(1, 2, 3),
                type: "public-key",
                response: {
                    clientDataJSON: bytes(7),
                    authenticatorData: bytes(8),
                    signature: bytes(9),
                    userHandle: bytes(10),
                },
            }),
        ),
    };
}

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
    delete globalThis.navigator.credentials;
    close();
});

/** The text of the page as a person reads it: without the template's line breaks. */
const text = () => target.textContent.replace(/\s+/g, " ");

const button = (text) => findButton(target, text);

const dialog = () => target.querySelector("[role=dialog]");

async function openDialog(label = "E-Mail-Adresse ändern") {
    app = mount(App, { target: target });
    await vi.waitFor(() => expect(target.querySelector("button.account")).toBeTruthy());
    target.querySelector("button.account").click();
    await vi.waitFor(() => expect(button(label)).toBeTruthy());
    button(label).click();
    await vi.waitFor(() => expect(dialog()).toBeTruthy());
}

function fill(address, password = PASSWORD) {
    const field = dialog().querySelector("input[type=email]");
    field.value = address;
    field.dispatchEvent(new Event("input", { bubbles: true }));
    const secret = dialog().querySelector("input[type=password]");
    if (secret) {
        secret.value = password;
        secret.dispatchEvent(new Event("input", { bubbles: true }));
    }
    dialog().querySelector("button[type=submit]").click();
}

it("stands in the account menu and names the current address", async () => {
    server(() => respond({}));

    await openDialog();

    expect(dialog().getAttribute("aria-label")).toBe("E-Mail-Adresse ändern");
    expect(text()).toContain("Zurzeit gilt zoe@example.org.");
    expect(text()).toContain("Erst wenn du ihn bestätigt hast, gilt sie.");
    expect(dialog().querySelector("input[type=password]")).toBeTruthy();
    expect(button("Mail schicken")).toBeTruthy();
    expect(button("Abmelden")).toBeUndefined();
});

it("sends address and password and says that the mail is on its way", async () => {
    const requests = server(() => respond({ email: NEW, bestaetigung: true }));
    await openDialog();

    fill(NEW);

    await vi.waitFor(() => expect(text()).toContain(`An ${NEW} ist eine Mail mit einem Link`));
    const [change] = changes(requests);
    expect(change.method).toBe("POST");
    expect(JSON.parse(change.body)).toEqual({ email: NEW, passwort: PASSWORD });
    // The form is gone, and with it the way to send twice.
    expect(dialog().querySelector("input[type=email]")).toBeNull();
    expect(await remembered("access_token")).toBe("a-old");

    button("Schließen").click();
    await vi.waitFor(() => expect(dialog()).toBeNull());
});

it("says so when the address applies at once, and refreshes the account", async () => {
    const requests = server(() => respond({ email: NEW, bestaetigung: false }));
    await openDialog();

    fill(NEW);

    await vi.waitFor(() => expect(text()).toContain(`Deine Adresse ist jetzt ${NEW}.`));
    // The account is fetched anew, so the next opening names the new address.
    await vi.waitFor(() =>
        expect(requests.filter((entry) => entry.path === "/ich/").length).toBeGreaterThan(1),
    );
});

/**
 * Without a password a passkey of the account confirms, before the address
 * goes out. Until 10/2026 a signed-in app sufficed, and whoever found one
 * open could bend the address to a mailbox of their own.
 */
it("asks for no password and promises no mail where there is none", async () => {
    const plain = { ...ZOE, hat_passwort: false, mail: false };
    await db.state.put({ key: "me", value: plain });
    device();
    const requests = server(() => respond({ email: NEW, bestaetigung: false }), plain);
    await openDialog();

    expect(dialog().querySelector("input[type=password]")).toBeNull();
    expect(text()).toContain("Die neue Adresse gilt sofort.");
    expect(text()).toContain("fragt dein Gerät zuerst nach einem Passkey deines Kontos");
    expect(button("Adresse ändern")).toBeTruthy();

    fill(NEW);

    await vi.waitFor(() => expect(text()).toContain("Deine Adresse ist jetzt"));
    const sent = requests.filter((entry) => entry.method === "POST");
    expect(sent.map((entry) => entry.path)).toEqual([
        "/passkey/bestaetigen/optionen/",
        "/passkey/bestaetigen/",
        "/email/",
    ]);
    expect(JSON.parse(sent[1].body).credential.response.signature).toBe("CQ");
    expect(JSON.parse(changes(requests)[0].body)).toEqual({
        email: NEW,
        bestaetigung: "bestaetigt",
    });
    expect(navigator.credentials.get).toHaveBeenCalledOnce();
});

it("shows what the server says under the field it belongs to", async () => {
    server(() =>
        respond(
            {
                email: ["Diese Adresse gehört schon zu einem anderen Konto."],
                passwort: ["Das Passwort stimmt nicht."],
            },
            400,
        ),
    );
    await openDialog();

    fill("fremd@example.org", "daneben");

    await vi.waitFor(() => expect(text()).toContain("Das Passwort stimmt nicht."));
    const [addressField, passwordField] = dialog().querySelectorAll(".field");
    expect(addressField.textContent).toContain("anderen Konto");
    expect(passwordField.textContent).toContain("Das Passwort stimmt nicht.");
    expect(addressField.querySelector("input").getAttribute("aria-invalid")).toBe("true");
    expect(addressField.querySelector("label").textContent.trim()).toBe("Neue E-Mail-Adresse");
    expect(await remembered("refresh_token")).toBe("r-old");
});

it("tells a dead spot, the lock and a mail server that does not answer apart", async () => {
    const answers = [
        [() => Promise.reject(new TypeError("Funkloch")), "Keine Verbindung zum Server"],
        [
            () => respond({ detail: "Zu viele Fehlversuche.", gesperrt: true }, 429),
            "Dieses Konto ist von hier aus vorübergehend gesperrt.",
        ],
        [
            () => respond({ detail: "Die Mail ging nicht hinaus" }, 503),
            "Die Mail ging nicht hinaus",
        ],
        [() => respond({ detail: "Server Error" }, 500), "Die Adresse wurde nicht geändert"],
    ];
    for (const [answer, expected] of answers) {
        server(answer);
        await openDialog();
        fill(NEW);
        await vi.waitFor(() => expect(text()).toContain(expected));
        expect(dialog().querySelector("input[type=email]")).toBeTruthy();
        unmount(app);
        app = null;
    }
});

it("leads to the login form when the sign-in no longer applies", async () => {
    server(() => respond({ detail: "abgelaufen" }, 401));
    await openDialog();
    const known = globalThis.fetch;
    globalThis.fetch = (url, options) =>
        url.endsWith("/token/erneuern/")
            ? respond({ detail: "gesperrt" }, 401)
            : known(url, options);

    fill(NEW);

    await vi.waitFor(() => expect(dialog()).toBeNull());
    await vi.waitFor(() => expect(text()).toContain("Anmelden"));
});

it("speaks English when the account does", async () => {
    const english = { ...ZOE, sprache: "en" };
    await db.state.put({ key: "me", value: english });
    server(() => respond({ email: NEW, bestaetigung: true }), english);

    await openDialog("Change e-mail address");

    expect(text()).toContain("Currently zoe@example.org applies.");
    expect(text()).toContain("New e-mail address");
    expect(button("Send mail")).toBeTruthy();
});

it("syncs after the language switch so the server's words follow", async () => {
    const requests = server(() => respond({}));
    app = mount(App, { target: target });
    await vi.waitFor(() => expect(target.querySelector("button.account")).toBeTruthy());
    target.querySelector("button.account").click();
    await vi.waitFor(() => expect(button("English")).toBeTruthy());
    const before = requests.filter((entry) => entry.path === "/inventuren/").length;

    button("English").click();

    await vi.waitFor(() =>
        expect(requests.filter((entry) => entry.path === "/inventuren/").length).toBeGreaterThan(
            before,
        ),
    );
    apply("de");
});
