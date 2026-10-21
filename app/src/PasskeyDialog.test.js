/**
 * Passkeys in the app: the dialog in the account menu and the sign-in without a password.
 *
 * The device is replaced by a fake 'navigator.credentials': what it hands
 * back has to reach the server in the library's shape, and the server's
 * answer has to end in a signed-in app.
 */

import { mount, unmount } from "svelte";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import App from "./App.svelte";
import { close } from "./services/counting.svelte.js";
import { apply } from "./services/language.svelte.js";
import { db, remembered } from "./services/storage.js";
import { clearStorage, findButton, respond } from "./test_support.js";

const ZOE = { id: 1, vorname: "Zoe", benutzername: "zoe", sprache: "de" };
const KEY = { id: 5, name: "Handy", erstellt_am: "2026-10-06T10:00:00Z", zuletzt_benutzt: null };

const bytes = (...values) => Uint8Array.from(values).buffer;

/** A device with one key: creates on demand and signs on demand. */
function device() {
    globalThis.PublicKeyCredential = function PublicKeyCredential() {};
    globalThis.navigator.credentials = {
        create: vi.fn(() =>
            Promise.resolve({
                id: "AQID",
                rawId: bytes(1, 2, 3),
                type: "public-key",
                response: { clientDataJSON: bytes(7), attestationObject: bytes(8) },
            }),
        ),
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

/** A server with the passkey endpoints. Everything else it lets pass. */
function server(keys = [KEY], who = ZOE) {
    const requests = [];
    globalThis.fetch = (url, options = {}) => {
        const path = url.replace("/api/v1", "");
        requests.push({
            method: options.method ?? "GET",
            path,
            body: options.body,
            language: options.headers?.["X-Sprache"],
        });
        if (path === "/passkey/") return respond(keys);
        if (path.startsWith("/passkey/bestaetigen/optionen/"))
            return respond({
                options: {
                    challenge: "AQID",
                    allowCredentials: [{ id: "AQID", type: "public-key" }],
                },
                challenge: "gefragt",
            });
        if (path.startsWith("/passkey/bestaetigen/"))
            return respond({ bestaetigung: "bestaetigt" });
        if (path.startsWith("/passkey/anlegen/optionen/"))
            return respond({
                options: { challenge: "AQID", user: { id: "BAU" } },
                challenge: "signed",
            });
        if (path.startsWith("/passkey/anlegen/"))
            return respond({ ...KEY, name: "Mein Handy" }, 201);
        if (path.startsWith("/passkey/anmelden/optionen/"))
            return respond({ options: { challenge: "AQID" }, challenge: "signed" });
        if (path.startsWith("/passkey/anmelden/"))
            return respond({ access: "a-new", refresh: "r-new", ich: who });
        if (path.startsWith("/passkey/5/")) return respond(null, 204);
        if (path === "/ich/") return respond(who);
        if (path === "/inventuren/") return respond([]);
        return respond({ detail: "?" }, 404);
    };
    return requests;
}

let target;
let app;

beforeEach(async () => {
    await clearStorage();
    apply("de");
    device();
    target = document.createElement("div");
    document.body.append(target);
});

afterEach(() => {
    if (app) unmount(app);
    app = null;
    target.remove();
    window.location.hash = "";
    globalThis.fetch = () => Promise.reject(new TypeError("kein Netz im Test"));
    delete globalThis.PublicKeyCredential;
    close();
});

const text = () => target.textContent.replace(/\s+/g, " ");

const button = (text) => findButton(target, text);

const dialog = () => target.querySelector("[role=dialog]");

async function signedIn(who = ZOE) {
    await db.state.bulkPut([
        { key: "me", value: who },
        { key: "access_token", value: "a" },
        { key: "refresh_token", value: "r" },
    ]);
}

async function openDialog() {
    app = mount(App, { target });
    await vi.waitFor(() => expect(target.querySelector("button.account")).toBeTruthy());
    target.querySelector("button.account").click();
    await vi.waitFor(() => expect(button("Passkeys")).toBeTruthy());
    button("Passkeys").click();
    await vi.waitFor(() => expect(dialog()).toBeTruthy());
}

it("lists the keys of the account", async () => {
    await signedIn();
    server();
    await openDialog();
    // The row, not the word: "Handy" stands in the name field's hint from the start.
    await vi.waitFor(() => expect(text()).toContain("noch nie benutzt"));
    expect(dialog().querySelector("li")?.textContent).toContain("Handy");
});

it("creates a key for this device and sends the device's answer", async () => {
    await signedIn();
    const requests = server([]);
    await openDialog();
    await vi.waitFor(() => expect(text()).toContain("Noch kein Passkey"));

    const field = dialog().querySelector("input");
    field.value = "Mein Handy";
    field.dispatchEvent(new Event("input", { bubbles: true }));
    button("Passkey für dieses Gerät anlegen").click();

    await vi.waitFor(() => expect(text()).toContain("„Mein Handy“ ist angelegt"));
    const created = requests.find((entry) => entry.path === "/passkey/anlegen/");
    const body = JSON.parse(created.body);
    expect(body.challenge).toBe("signed");
    expect(body.name).toBe("Mein Handy");
    expect(body.credential.rawId).toBe("AQID");
    expect(body.credential.response.attestationObject).toBe("CA");
    expect(navigator.credentials.create).toHaveBeenCalledOnce();
    const [{ publicKey }] = navigator.credentials.create.mock.calls[0];
    expect([...publicKey.challenge]).toEqual([1, 2, 3]);
});

/** Types into a field of the dialog, as a person does. */
function type(field, value) {
    field.value = value;
    field.dispatchEvent(new Event("input", { bubbles: true }));
}

const passwordField = () => dialog().querySelector("input[type=password]");

/**
 * Until 10/2026 a signed-in app sufficed: whoever found it open on a phone
 * added a key of their own. The password goes with the question, before the
 * device is asked.
 */
it("sends the password with the question, before the device is asked", async () => {
    await signedIn();
    const requests = server([]);
    await openDialog();
    await vi.waitFor(() => expect(text()).toContain("Noch kein Passkey"));
    expect(text()).toContain("damit niemand einen Passkey anlegt");
    expect(passwordField().getAttribute("autocomplete")).toBe("current-password");

    type(passwordField(), "geheim");
    button("Passkey für dieses Gerät anlegen").click();

    await vi.waitFor(() => expect(text()).toContain("ist angelegt"));
    const question = requests.find((entry) => entry.path.startsWith("/passkey/anlegen/optionen/"));
    expect(JSON.parse(question.body)).toEqual({ passwort: "geheim" });
    expect(navigator.credentials.create).toHaveBeenCalledOnce();
    // Not kept for the next key.
    expect(passwordField().value).toBe("");
});

it("puts a wrong password under its field and does not ask the device", async () => {
    await signedIn();
    const requests = server([]);
    const answers = globalThis.fetch;
    globalThis.fetch = (url, options = {}) =>
        url.includes("/passkey/anlegen/optionen/")
            ? (requests.push({ method: "POST", path: url.replace("/api/v1", "") }),
              respond({ passwort: ["Das Passwort stimmt nicht."] }, 400))
            : answers(url, options);
    await openDialog();
    await vi.waitFor(() => expect(text()).toContain("Noch kein Passkey"));

    type(passwordField(), "daneben");
    button("Passkey für dieses Gerät anlegen").click();

    await vi.waitFor(() => expect(text()).toContain("Das Passwort stimmt nicht."));
    expect(passwordField().getAttribute("aria-invalid")).toBe("true");
    const described = passwordField().getAttribute("aria-describedby").split(" ");
    expect(described.map((id) => document.getElementById(id).textContent).join(" ")).toContain(
        "Das Passwort stimmt nicht.",
    );
    expect(navigator.credentials.create).not.toHaveBeenCalled();
    expect(requests.some((entry) => entry.path === "/passkey/anlegen/")).toBe(false);
    // Kept, so the slip can be mended.
    expect(passwordField().value).toBe("daneben");
});

it("says that too many wrong passwords lock, as the sign-in does", async () => {
    await signedIn();
    server([]);
    const answers = globalThis.fetch;
    globalThis.fetch = (url, options = {}) =>
        url.includes("/passkey/anlegen/optionen/")
            ? respond({ detail: "Satz des Servers", gesperrt: true }, 429)
            : answers(url, options);
    await openDialog();
    await vi.waitFor(() => expect(text()).toContain("Noch kein Passkey"));

    type(passwordField(), "geheim");
    button("Passkey für dieses Gerät anlegen").click();

    // In the app's language and with what it means: the sign-in is locked as well.
    await vi.waitFor(() =>
        expect(text()).toContain("Dieses Konto ist von hier aus vorübergehend gesperrt."),
    );
    expect(text()).not.toContain("Satz des Servers");
    expect(navigator.credentials.create).not.toHaveBeenCalled();
});

const WITHOUT = { ...ZOE, hat_passwort: false };

const confirmButton = () => button("Mit Passkey bestätigen");

const createButton = () => button("Passkey für dieses Gerät anlegen");

/** The dialog of an account without a password, once its list stands. */
async function openWithout() {
    await signedIn(WITHOUT);
    const requests = server([], WITHOUT);
    await openDialog();
    await vi.waitFor(() => expect(text()).toContain("Noch kein Passkey"));
    return requests;
}

/** The confirmation on its own tap, until the key can be made. */
async function confirmed() {
    confirmButton().click();
    await vi.waitFor(() => expect(createButton().disabled).toBe(false));
}

/**
 * An account without a password has nothing to type. Until 10/2026 a
 * signed-in app sufficed for it, and whoever found one open added a key of
 * their own. A passkey the account already has confirms first, on a tap of
 * its own: Safari on the iPhone may refuse a second question of the device
 * on the same tap. The next tap makes the new key with the confirmation.
 */
it("confirms with a passkey of the account on one tap and makes the key on the next", async () => {
    const requests = await openWithout();
    expect(passwordField()).toBeNull();
    expect(text()).toContain("bestätigst du zuerst mit einem Passkey, den dein Konto schon hat");
    expect(createButton().disabled).toBe(true);

    confirmButton().click();

    await vi.waitFor(() => expect(text()).toContain("Bestätigt."));
    expect(navigator.credentials.get).toHaveBeenCalledOnce();
    expect(navigator.credentials.create).not.toHaveBeenCalled();
    expect(confirmButton()).toBeUndefined();
    expect(createButton().disabled).toBe(false);
    // The button that went took the focus along: the next step has it.
    expect(document.activeElement).toBe(createButton());

    createButton().click();

    await vi.waitFor(() => expect(text()).toContain("ist angelegt"));
    expect(navigator.credentials.get).toHaveBeenCalledOnce();
    expect(navigator.credentials.create).toHaveBeenCalledOnce();
    const sent = requests.filter((entry) => entry.method === "POST");
    expect(sent.map((entry) => entry.path)).toEqual([
        "/passkey/bestaetigen/optionen/",
        "/passkey/bestaetigen/",
        "/passkey/anlegen/optionen/",
        "/passkey/anlegen/",
    ]);
    const signature = JSON.parse(sent[1].body);
    expect(signature.challenge).toBe("gefragt");
    expect(signature.credential.response.signature).toBe("CQ");
    expect(JSON.parse(sent[2].body)).toEqual({ bestaetigung: "bestaetigt" });
    const [{ publicKey }] = navigator.credentials.get.mock.calls[0];
    expect([...publicKey.challenge]).toEqual([1, 2, 3]);
    expect([...publicKey.allowCredentials[0].id]).toEqual([1, 2, 3]);
    // One confirmation, one key: the next key is confirmed anew.
    expect(confirmButton()).toBeTruthy();
});

/** Cancelled, or no key of the account on this device: the browser does not say which. */
it("names a refused confirmation as the sign-in does and makes no key", async () => {
    navigator.credentials.get = vi.fn(() =>
        Promise.reject(Object.assign(new Error("nope"), { name: "NotAllowedError" })),
    );
    const requests = await openWithout();

    confirmButton().click();

    await vi.waitFor(() =>
        expect(text()).toContain(
            "Abgebrochen, oder auf diesem Gerät gibt es keinen Passkey für dieses Konto.",
        ),
    );
    expect(confirmButton()).toBeTruthy();
    expect(createButton().disabled).toBe(true);
    expect(navigator.credentials.create).not.toHaveBeenCalled();
    expect(requests.some((entry) => entry.path.startsWith("/passkey/anlegen/"))).toBe(false);
});

/** The confirmation holds two minutes: after a cancelled new key only the key is asked again. */
it("keeps the confirmation when the device refuses the new key", async () => {
    const requests = await openWithout();
    const create = navigator.credentials.create;
    navigator.credentials.create = vi.fn(() =>
        Promise.reject(Object.assign(new Error("nope"), { name: "NotAllowedError" })),
    );
    await confirmed();

    createButton().click();

    await vi.waitFor(() => expect(text()).toContain("Abgebrochen."));
    expect(createButton().disabled).toBe(false);
    navigator.credentials.create = create;
    createButton().click();

    await vi.waitFor(() => expect(text()).toContain("ist angelegt"));
    expect(navigator.credentials.get).toHaveBeenCalledOnce();
    const asked = requests.filter((entry) => entry.path === "/passkey/anlegen/optionen/");
    expect(asked.map((entry) => JSON.parse(entry.body))).toEqual([
        { bestaetigung: "bestaetigt" },
        { bestaetigung: "bestaetigt" },
    ]);
});

/** The server takes it for two minutes. Older, it would only refuse it. */
it("asks to confirm again once the confirmation is older than its two minutes", async () => {
    const requests = await openWithout();
    await confirmed();
    const clock = vi.spyOn(Date, "now").mockReturnValue(Date.now() + 121_000);
    try {
        createButton().click();

        await vi.waitFor(() => expect(confirmButton()).toBeTruthy());
        expect(text()).toContain("Die Bestätigung gilt nur zwei Minuten.");
        expect(createButton().disabled).toBe(true);
        expect(document.activeElement).toBe(confirmButton());
        expect(navigator.credentials.create).not.toHaveBeenCalled();
        expect(requests.some((entry) => entry.path.startsWith("/passkey/anlegen/"))).toBe(false);
    } finally {
        clock.mockRestore();
    }
});

it("asks to confirm again when the server refuses the confirmation", async () => {
    await openWithout();
    const answers = globalThis.fetch;
    globalThis.fetch = (url, options = {}) =>
        url.includes("/passkey/anlegen/optionen/")
            ? respond({ bestaetigung: ["Die Bestätigung ist abgelaufen."] }, 400)
            : answers(url, options);
    await confirmed();

    createButton().click();

    await vi.waitFor(() => expect(text()).toContain("Die Bestätigung ist abgelaufen."));
    expect(confirmButton()).toBeTruthy();
    expect(createButton().disabled).toBe(true);
    expect(navigator.credentials.create).not.toHaveBeenCalled();
});

/** The removal's own field for the password, in the box of the key. */
const removalField = () => dialog().querySelector("li input[type=password]");

/** The key's box with its question, after the tap on "Remove". */
async function askToRemove() {
    await vi.waitFor(() => expect(button("Entfernen")).toBeTruthy());
    button("Entfernen").click();
    await vi.waitFor(() => expect(text()).toContain("Wirklich entfernen"));
}

/** What the server heard as removals, with their bodies. */
const removals = (requests) =>
    requests
        .filter((entry) => entry.method === "DELETE")
        .map((entry) => [entry.path, JSON.parse(entry.body)]);

/**
 * Removing a key signs the account out everywhere else, so it wants what a
 * new key wants: the password. Until 10/2026 a second tap sufficed, and
 * whoever found the app open removed the owner's keys.
 */
it("asks for the password before it removes a key", async () => {
    await signedIn();
    const requests = server();
    await openDialog();
    await askToRemove();
    expect(removalField().getAttribute("autocomplete")).toBe("current-password");
    expect(text()).toContain("damit niemand einen Passkey entfernt");
    expect(removals(requests)).toEqual([]);

    type(removalField(), "geheim");
    button("Ja, entfernen").click();

    await vi.waitFor(() => expect(text()).not.toContain("Wirklich entfernen"));
    expect(removals(requests)).toEqual([["/passkey/5/", { refresh: "r", passwort: "geheim" }]]);
});

/**
 * Removing signs the account out everywhere else. The device names its own
 * refresh token, and the server spares it: the pair stays, and the answer
 * is empty.
 */
it("names its own refresh token on removing a key and keeps its pair", async () => {
    await signedIn();
    const requests = server();
    await openDialog();
    await askToRemove();
    type(removalField(), "geheim");

    button("Ja, entfernen").click();

    await vi.waitFor(() => expect(removals(requests)).toHaveLength(1));
    expect(removals(requests)[0][1].refresh).toBe("r");
    await vi.waitFor(() => expect(text()).not.toContain("Wirklich entfernen"));
    expect(await remembered("access_token")).toBe("a");
    expect(await remembered("refresh_token")).toBe("r");
});

/** A server that answers the removal with 'reply', and lets the rest pass. */
function refusing(reply) {
    const requests = server();
    const answers = globalThis.fetch;
    globalThis.fetch = (url, options = {}) =>
        options.method === "DELETE"
            ? (requests.push({
                  method: "DELETE",
                  path: url.replace("/api/v1", ""),
                  body: options.body,
              }),
              reply())
            : answers(url, options);
    return requests;
}

it("puts a wrong password under the removal's field and keeps the key", async () => {
    await signedIn();
    refusing(() => respond({ passwort: ["Das Passwort stimmt nicht."] }, 400));
    await openDialog();
    await askToRemove();
    type(removalField(), "daneben");

    button("Ja, entfernen").click();

    await vi.waitFor(() => expect(text()).toContain("Das Passwort stimmt nicht."));
    expect(removalField().getAttribute("aria-invalid")).toBe("true");
    const described = removalField().getAttribute("aria-describedby").split(" ");
    expect(described.map((id) => document.getElementById(id).textContent).join(" ")).toContain(
        "Das Passwort stimmt nicht.",
    );
    expect(dialog().querySelector("li")?.textContent).toContain("Handy");
    // Kept, so the slip can be mended.
    expect(removalField().value).toBe("daneben");
});

it("says that too many wrong passwords lock, at the removal too", async () => {
    await signedIn();
    refusing(() => respond({ detail: "Satz des Servers", gesperrt: true }, 429));
    await openDialog();
    await askToRemove();
    type(removalField(), "geheim");

    button("Ja, entfernen").click();

    await vi.waitFor(() =>
        expect(text()).toContain("Dieses Konto ist von hier aus vorübergehend gesperrt."),
    );
    expect(text()).not.toContain("Satz des Servers");
    expect(dialog().querySelector("li")?.textContent).toContain("Handy");
});

/**
 * Without a password a passkey of the account confirms, on a tap of its own,
 * as before a new key. Then the next tap removes.
 */
it("confirms with a passkey on a tap of its own before it removes, without a password", async () => {
    await signedIn(WITHOUT);
    const requests = server([KEY], WITHOUT);
    await openDialog();
    await askToRemove();
    const box = () => dialog().querySelector("li");
    expect(removalField()).toBeNull();
    expect(text()).toContain("bestätigst du zuerst mit einem Passkey deines Kontos");
    expect(findButton(box(), "Ja, entfernen").disabled).toBe(true);

    findButton(box(), "Mit Passkey bestätigen").click();

    await vi.waitFor(() => expect(text()).toContain("Jetzt kannst du ihn entfernen."));
    expect(navigator.credentials.get).toHaveBeenCalledOnce();
    expect(findButton(box(), "Mit Passkey bestätigen")).toBeUndefined();
    expect(findButton(box(), "Ja, entfernen").disabled).toBe(false);
    expect(document.activeElement).toBe(findButton(box(), "Ja, entfernen"));
    expect(removals(requests)).toEqual([]);

    findButton(box(), "Ja, entfernen").click();

    await vi.waitFor(() => expect(removals(requests)).toHaveLength(1));
    expect(removals(requests)[0]).toEqual([
        "/passkey/5/",
        { refresh: "r", bestaetigung: "bestaetigt" },
    ]);
    expect(navigator.credentials.get).toHaveBeenCalledOnce();
});

it("asks to confirm the removal again once the confirmation is older than its two minutes", async () => {
    await signedIn(WITHOUT);
    const requests = server([KEY], WITHOUT);
    await openDialog();
    await askToRemove();
    const box = () => dialog().querySelector("li");
    findButton(box(), "Mit Passkey bestätigen").click();
    await vi.waitFor(() => expect(findButton(box(), "Ja, entfernen").disabled).toBe(false));
    const clock = vi.spyOn(Date, "now").mockReturnValue(Date.now() + 121_000);
    try {
        findButton(box(), "Ja, entfernen").click();

        await vi.waitFor(() => expect(findButton(box(), "Mit Passkey bestätigen")).toBeTruthy());
        expect(text()).toContain("Die Bestätigung gilt nur zwei Minuten.");
        expect(findButton(box(), "Ja, entfernen").disabled).toBe(true);
        expect(removals(requests)).toEqual([]);
    } finally {
        clock.mockRestore();
    }
});

it("asks to confirm the removal again when the server refuses the confirmation", async () => {
    await signedIn(WITHOUT);
    server([KEY], WITHOUT);
    const answers = globalThis.fetch;
    globalThis.fetch = (url, options = {}) =>
        options.method === "DELETE"
            ? respond({ bestaetigung: ["Die Bestätigung ist abgelaufen."] }, 400)
            : answers(url, options);
    await openDialog();
    await askToRemove();
    const box = () => dialog().querySelector("li");
    findButton(box(), "Mit Passkey bestätigen").click();
    await vi.waitFor(() => expect(findButton(box(), "Ja, entfernen").disabled).toBe(false));

    findButton(box(), "Ja, entfernen").click();

    await vi.waitFor(() => expect(text()).toContain("Die Bestätigung ist abgelaufen."));
    expect(findButton(box(), "Mit Passkey bestätigen")).toBeTruthy();
    expect(findButton(box(), "Ja, entfernen").disabled).toBe(true);
});

/** Where the device thought the account had no password, the server says otherwise. */
it("shows the removal's password field when the server wants the password after all", async () => {
    await signedIn(WITHOUT);
    server([KEY], WITHOUT);
    const answers = globalThis.fetch;
    globalThis.fetch = (url, options = {}) =>
        options.method === "DELETE"
            ? respond({ passwort: ["Bitte gib dein Passwort ein."] }, 400)
            : answers(url, options);
    await openDialog();
    await askToRemove();
    findButton(dialog().querySelector("li"), "Mit Passkey bestätigen").click();
    await vi.waitFor(() => expect(button("Ja, entfernen").disabled).toBe(false));

    button("Ja, entfernen").click();

    await vi.waitFor(() => expect(removalField()).toBeTruthy());
    expect(text()).toContain("Bitte gib dein Passwort ein.");
});

/** Removed elsewhere in the meantime: what the person wanted holds, and the list says so. */
it("takes a key the server no longer knows off the list", async () => {
    await signedIn();
    const keys = [KEY];
    server(keys);
    const answers = globalThis.fetch;
    globalThis.fetch = (url, options = {}) => {
        if (options.method !== "DELETE") return answers(url, options);
        keys.length = 0;
        return respond({ detail: "Nicht gefunden." }, 404);
    };
    await openDialog();
    await askToRemove();
    type(removalField(), "geheim");

    button("Ja, entfernen").click();

    await vi.waitFor(() => expect(text()).toContain("Noch kein Passkey"));
    expect(text()).not.toContain("Wirklich entfernen");
    expect(text()).not.toContain("Nicht gefunden.");
});

/**
 * Without a refresh token the server knows, it removes nothing and asks to
 * sign in anew. Its sentence is for the person, so the dialog shows it.
 */
it("shows the server's sentence when it keeps the key", async () => {
    await signedIn();
    refusing(() => respond({ detail: "Melde dich bitte neu an, dann geht es." }, 403));
    await openDialog();
    await askToRemove();
    type(removalField(), "geheim");

    button("Ja, entfernen").click();

    await vi.waitFor(() => expect(text()).toContain("Melde dich bitte neu an, dann geht es."));
    expect(dialog().querySelector("li")?.textContent).toContain("Handy");
    expect(await remembered("refresh_token")).toBe("r");
});

/**
 * A server from before 10/2026 signs this device out too and sends it a
 * fresh pair along. Until 10/2026 the app dropped the pair and signed itself
 * out at the next refresh.
 */
it("stays signed in on this device after removing a key", async () => {
    await signedIn();
    const requests = refusing(() => respond({ access: "a-fresh", refresh: "r-fresh" }));
    await openDialog();
    await askToRemove();
    expect(text()).toContain("auf deinen anderen Geräten wirst du abgemeldet");
    type(removalField(), "geheim");

    button("Ja, entfernen").click();

    await vi.waitFor(async () => expect(await remembered("access_token")).toBe("a-fresh"));
    expect(await remembered("refresh_token")).toBe("r-fresh");
    expect(removals(requests)).toHaveLength(1);
});

it("says what the browser refused", async () => {
    await signedIn();
    server([]);
    navigator.credentials.create = vi.fn(() =>
        Promise.reject(Object.assign(new Error("nope"), { name: "InvalidStateError" })),
    );
    await openDialog();
    await vi.waitFor(() => expect(button("Passkey für dieses Gerät anlegen")).toBeTruthy());
    button("Passkey für dieses Gerät anlegen").click();
    await vi.waitFor(() => expect(text()).toContain("schon einen Passkey"));
});

it("signs in with a passkey, without a user name", async () => {
    const requests = server();
    app = mount(App, { target });
    await vi.waitFor(() => expect(button("Mit Passkey anmelden")).toBeTruthy());
    button("Mit Passkey anmelden").click();

    await vi.waitFor(() => expect(target.querySelector("button.account")).toBeTruthy());
    expect(await remembered("access_token")).toBe("a-new");
    expect(await remembered("refresh_token")).toBe("r-new");
    expect((await remembered("me")).benutzername).toBe("zoe");
    const signIn = requests.find((entry) => entry.path === "/passkey/anmelden/");
    expect(signIn.language).toBe("de");
    const body = JSON.parse(signIn.body);
    expect(body.credential.response.userHandle).toBe("Cg");
    expect(body.challenge).toBe("signed");
    expect(requests.some((entry) => entry.path === "/token/")).toBe(false);
});

/**
 * For Django these calls are anonymous, so the language travels with them:
 * in the header, as with every call of the app ('api.js'). Until 10/2026 it
 * also stood in the address, and a call that forgot it asked in German.
 */
it("asks the passkey's questions in the app's language, by the header alone", async () => {
    apply("en");
    const requests = server([], { ...ZOE, sprache: "en" });
    app = mount(App, { target });
    await vi.waitFor(() => expect(button("Sign in with passkey")).toBeTruthy());
    button("Sign in with passkey").click();
    await vi.waitFor(() => expect(target.querySelector("button.account")).toBeTruthy());
    target.querySelector("button.account").click();
    await vi.waitFor(() => expect(button("Passkeys")).toBeTruthy());
    button("Passkeys").click();
    await vi.waitFor(() => expect(text()).toContain("No passkey"));
    type(passwordField(), "geheim");
    button("Create a passkey for this device").click();
    await vi.waitFor(() => expect(text()).toContain("“Mein Handy” is created"));

    const asked = requests.filter((entry) => entry.path.startsWith("/passkey/an"));
    expect(asked.map((entry) => [entry.path, entry.language])).toEqual([
        ["/passkey/anmelden/optionen/", "en"],
        ["/passkey/anmelden/", "en"],
        ["/passkey/anlegen/optionen/", "en"],
        ["/passkey/anlegen/", "en"],
    ]);
});

it("explains a cancelled passkey and the server's refusal", async () => {
    server();
    navigator.credentials.get = vi.fn(() =>
        Promise.reject(Object.assign(new Error("nope"), { name: "NotAllowedError" })),
    );
    app = mount(App, { target });
    await vi.waitFor(() => expect(button("Mit Passkey anmelden")).toBeTruthy());
    button("Mit Passkey anmelden").click();
    await vi.waitFor(() => expect(text()).toContain("keinen Passkey für diese Adresse"));

    device();
    const original = globalThis.fetch;
    globalThis.fetch = (url, options) =>
        url.endsWith("/passkey/anmelden/")
            ? respond({ detail: "Dieser Passkey ist hier nicht bekannt." }, 400)
            : original(url, options);
    button("Mit Passkey anmelden").click();
    await vi.waitFor(() => expect(text()).toContain("hier nicht bekannt"));
});

it("offers no passkey button where the browser cannot", async () => {
    delete globalThis.PublicKeyCredential;
    server();
    app = mount(App, { target });
    await vi.waitFor(() => expect(button("Anmelden")).toBeTruthy());
    expect(button("Mit Passkey anmelden")).toBeUndefined();
});
