/**
 * What the dialogs of the app do around what they show.
 *
 * The shade behind them, the frame with its name and the button that
 * closes, the focus, and how a form sorts a refusal of the server: under
 * its field, or in one line. Held for all of them at once, also where they
 * differ. The search hangs at the top and is no modal dialog. The three forms
 * say the same about the lock and their own about the rest.
 */

import { flushSync, mount, unmount } from "svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ArticleSearch from "./components/ArticleSearch.svelte";
import EmailDialog from "./components/EmailDialog.svelte";
import PasskeyDialog from "./components/PasskeyDialog.svelte";
import PasswordDialog from "./components/PasswordDialog.svelte";
import SyncDialog from "./components/SyncDialog.svelte";
import { counting } from "./services/counting.svelte.js";
import { apply, t } from "./services/language.svelte.js";
import { db } from "./services/storage.js";
import { clearStorage, findButton, respond } from "./test_support.js";

/** Each dialog with what it needs to open, and what its frame shows. */
const DIALOGS = [
    {
        name: "SyncDialog",
        component: SyncDialog,
        props: {},
        label: "Abgleich",
        title: "Abgleich",
        closeText: "Schließen",
        modal: true,
        // No field: the button in the header comes first.
        first: (dialog) => dialog.querySelector(".header button"),
    },
    {
        name: "PasswordDialog",
        component: PasswordDialog,
        props: { username: "zoe" },
        label: "Passwort ändern",
        title: "Passwort ändern",
        closeText: "Abbrechen",
        modal: true,
        first: (dialog) => dialog.querySelector("input[type=password]"),
    },
    {
        name: "EmailDialog",
        component: EmailDialog,
        props: { username: "zoe", email: "zoe@example.org" },
        label: "E-Mail-Adresse ändern",
        title: "E-Mail-Adresse ändern",
        closeText: "Abbrechen",
        modal: true,
        first: (dialog) => dialog.querySelector("input[type=email]"),
    },
    {
        name: "PasskeyDialog",
        component: PasskeyDialog,
        props: { username: "zoe" },
        label: "Passkeys",
        title: "Passkeys",
        closeText: "Schließen",
        modal: true,
        // The name, before the password.
        first: (dialog) => dialog.querySelector("input:not([hidden])"),
    },
    {
        name: "ArticleSearch",
        component: ArticleSearch,
        props: { existing: [], all: [], choose: () => {} },
        label: "Artikel suchen",
        // The header says what the window is for, the name what one does in it.
        title: "Artikel hinzufügen",
        closeText: "Abbrechen",
        modal: false,
        first: (dialog) => dialog.querySelector("input[type=search]"),
    },
];

const offline = () => Promise.reject(new TypeError("kein Netz im Test"));

let target;
let opener;
let shown;
let credentials;

beforeEach(async () => {
    await clearStorage();
    // Signed in: a request without its token does not go out.
    await db.state.bulkPut([
        { key: "access_token", value: "a" },
        { key: "refresh_token", value: "r" },
    ]);
    apply("de");
    counting.signedOut = false;
    // The passkey dialog lists the keys as it opens.
    globalThis.fetch = (url) => (String(url).endsWith("/passkey/") ? respond([]) : offline());
    credentials = navigator.credentials;
    // Where the focus goes back to: the button that opened the dialog.
    opener = document.createElement("button");
    document.body.append(opener);
    opener.focus();
    target = document.createElement("div");
    document.body.append(target);
});

afterEach(() => {
    if (shown) unmount(shown);
    shown = null;
    target.remove();
    opener.remove();
    navigator.credentials = credentials;
    counting.signedOut = false;
});

function open(entry, close = vi.fn()) {
    shown = mount(entry.component, { target, props: { ...entry.props, close } });
    flushSync();
    return close;
}

function shut() {
    unmount(shown);
    shown = null;
}

const dialog = () => target.querySelector("[role=dialog]");

const key = (name, shiftKey = false) =>
    new KeyboardEvent("keydown", { key: name, shiftKey, bubbles: true, cancelable: true });

describe.each(DIALOGS)("$name", (entry) => {
    it("lies on a shade that closes on a tap", () => {
        const close = open(entry);
        const shade = target.querySelector(".overlay");

        expect(shade.getAttribute("role")).toBe("presentation");
        // Before the dialog, so the dialog lies above it.
        expect(shade.nextElementSibling).toBe(dialog());
        shade.click();
        expect(close).toHaveBeenCalledOnce();
    });

    it("names itself and shows its title and the button that closes", () => {
        const close = open(entry);
        const header = dialog().firstElementChild;

        expect(dialog().classList.contains("dialog")).toBe(true);
        expect(dialog().getAttribute("aria-label")).toBe(entry.label);
        expect(header.classList.contains("header")).toBe(true);
        expect(header.querySelector("strong").textContent).toBe(entry.title);
        const button = header.querySelector("button");
        expect(button.classList.contains("flat")).toBe(true);
        expect(button.textContent.trim()).toBe(entry.closeText);
        button.click();
        expect(close).toHaveBeenCalledOnce();
    });

    it("takes the focus to where its work starts", () => {
        open(entry);
        expect(document.activeElement).toBe(entry.first(dialog()));
    });

    if (entry.modal) {
        it("is modal: Escape closes, Tab stays inside, the focus goes back", () => {
            const close = open(entry);
            expect(dialog().getAttribute("aria-modal")).toBe("true");
            expect(dialog().getAttribute("tabindex")).toBe("-1");

            document.activeElement.dispatchEvent(key("Escape"));
            expect(close).toHaveBeenCalledOnce();

            const buttons = [...dialog().querySelectorAll("button:not([disabled])")];
            const [first, last] = [buttons[0], buttons.at(-1)];
            last.focus();
            last.dispatchEvent(key("Tab"));
            expect(document.activeElement).toBe(first);
            first.dispatchEvent(key("Tab", true));
            expect(document.activeElement).toBe(last);

            shut();
            expect(document.activeElement).toBe(opener);
        });
    } else {
        it("is no modal dialog: Escape and the focus stay with the page", () => {
            const close = open(entry);
            expect(dialog().hasAttribute("aria-modal")).toBe(false);
            expect(dialog().hasAttribute("tabindex")).toBe(false);

            const escape = key("Escape");
            document.activeElement.dispatchEvent(escape);
            expect(close).not.toHaveBeenCalled();
            expect(escape.defaultPrevented).toBe(false);

            shut();
            expect(document.activeElement).toBe(document.body);
        });
    }
});

/** The search reaches as far down as the keyboard leaves room, and follows it. */
it("lets the search reach down to the keyboard", () => {
    let measure = null;
    Object.defineProperty(window, "visualViewport", {
        configurable: true,
        value: {
            height: 412,
            addEventListener: (_, listener) => (measure = listener),
            removeEventListener: () => {},
        },
    });
    try {
        open(DIALOGS.at(-1));
        expect(dialog().style.maxHeight).toBe("412px");

        window.visualViewport.height = 300;
        measure();
        flushSync();
        expect(dialog().style.maxHeight).toBe("300px");
    } finally {
        delete window.visualViewport;
    }
});

/** Types into a field, as a person does. */
function type(field, value) {
    field.value = value;
    field.dispatchEvent(new Event("input", { bubbles: true }));
}

/** The answer of the server: JSON with a status, an error page, or no network at all. */
function answer(status, data) {
    if (status === 0) return offline;
    if (data === "page") {
        return () =>
            Promise.resolve({ ok: false, status, text: () => Promise.resolve("<html></html>") });
    }
    return () => respond(data, status);
}

//: Where the app renews its sign-in. Asked only after a 401, and refused here:
//: the sign-in no longer applies.
const RENEWAL = "/token/erneuern/";
const renewalRefused = () => respond({ detail: "abgelaufen" }, 401);

/** A server that answers the form's request ('path') with 'reply'. */
function server(path, reply) {
    globalThis.fetch = (url, options = {}) => {
        const at = String(url).replace("/api/v1", "");
        if (at === "/passkey/" && (options.method ?? "GET") === "GET") return respond([]);
        if (at === RENEWAL) return renewalRefused();
        if (at.startsWith(path)) return reply();
        return offline();
    };
}

/** The line under the form, for what belongs to no field. */
const line = () => dialog().querySelector("p.error[role=alert]")?.textContent ?? "";

/** What stands at the field of this input: its sentences, one per paragraph. */
const atField = (input) =>
    [...input.closest(".field").querySelectorAll("p.error")].map((entry) => entry.textContent);

/**
 * The three forms, with what each sends and what it says.
 *
 * 'fields' names the server's fields and the input each belongs to. 'says'
 * is the line for a status, 'otherwise' the line for everything else. The
 * lock (429) is the sign-in's and reads the same in all three. Only the
 * address sends a mail it cannot do without, so only it names 503. The
 * passkey dialog shows the server's own sentence where there is one: only
 * its refusals are sentences meant for people.
 */
const FORMS = [
    {
        name: "PasswordDialog",
        component: PasswordDialog,
        props: { username: "zoe" },
        path: "/passwort/",
        submit() {
            const [old, fresh, repeated] = dialog().querySelectorAll("input[type=password]");
            type(old, "alt");
            type(fresh, "ganz-neu-2026");
            type(repeated, "ganz-neu-2026");
            dialog().querySelector("button[type=submit]").click();
        },
        fields: {
            altes_passwort: () => dialog().querySelectorAll("input[type=password]")[0],
            neues_passwort: () => dialog().querySelectorAll("input[type=password]")[1],
        },
        says: {
            0: () => t("password.no_connection"),
            429: () => t("error.locked"),
            503: () => t("password.failed"),
        },
        otherwise: () => t("password.failed"),
        detail: false,
    },
    {
        name: "EmailDialog",
        component: EmailDialog,
        props: { username: "zoe", email: "zoe@example.org" },
        path: "/email/",
        submit() {
            type(dialog().querySelector("input[type=email]"), "zoe@example.com");
            type(dialog().querySelector("input[type=password]"), "geheim");
            dialog().querySelector("button[type=submit]").click();
        },
        fields: {
            email: () => dialog().querySelector("input[type=email]"),
            passwort: () => dialog().querySelector("input[type=password]"),
        },
        says: {
            0: () => t("email.no_connection"),
            429: () => t("error.locked"),
            503: () => t("email.not_sent"),
        },
        otherwise: () => t("email.failed"),
        detail: false,
    },
    {
        name: "PasskeyDialog",
        component: PasskeyDialog,
        props: { username: "zoe" },
        path: "/passkey/anlegen/optionen/",
        submit() {
            type(dialog().querySelector("input[type=password]"), "geheim");
            [...dialog().querySelectorAll("button")].at(-1).click();
        },
        fields: { passwort: () => dialog().querySelector("input[type=password]") },
        says: { 0: () => t("passkey.no_connection"), 429: () => t("error.locked") },
        otherwise: () => t("passkey.failed"),
        detail: true,
    },
];

describe.each(FORMS)("$name sorts a refusal", (form) => {
    async function refused(status, data) {
        server(form.path, answer(status, data));
        const close = open(form);
        // Until the list of the passkey dialog stands: its field comes after it.
        await vi.waitFor(() => expect(dialog().querySelector("button:not(.flat)")).toBeTruthy());
        form.submit();
        return close;
    }

    it("closes and leads to the login form when the sign-in no longer applies", async () => {
        const close = await refused(401, { detail: "abgelaufen" });
        await vi.waitFor(() => expect(close).toHaveBeenCalledOnce());
        expect(counting.signedOut).toBe(true);
    });

    for (const field of Object.keys(form.fields)) {
        it(`puts the sentences about '${field}' under its field and nothing in the line`, async () => {
            await refused(400, { [field]: [" Satz A ", "Satz B"] });
            const input = form.fields[field]();
            await vi.waitFor(() => expect(atField(input)).toEqual(["Satz A", "Satz B"]));
            expect(input.getAttribute("aria-invalid")).toBe("true");
            expect(line()).toBe("");
        });

        it(`takes a sentence about '${field}' that is no list for none`, async () => {
            await refused(400, { [field]: "kein Satz in einer Liste" });
            await vi.waitFor(() => expect(line()).toBe(form.otherwise()));
            expect(atField(form.fields[field]())).toEqual([]);
        });
    }

    for (const status of [0, 429, 503]) {
        it(`says what status ${status} means in the line`, async () => {
            await refused(status, { detail: "Satz des Servers" });
            const expected = form.says[status]?.() ?? (form.detail ? "Satz des Servers" : null);
            await vi.waitFor(() => expect(line()).toBe(expected ?? form.otherwise()));
        });
    }

    it("says the server's own sentence only where it shows one", async () => {
        await refused(400, { detail: "Satz des Servers" });
        const expected = form.detail ? "Satz des Servers" : form.otherwise();
        await vi.waitFor(() => expect(line()).toBe(expected));
    });

    it("takes an error page of the server for a failure", async () => {
        await refused(500, "page");
        await vi.waitFor(() => expect(line()).toBe(form.otherwise()));
    });
});

describe("PasskeyDialog tells the device's refusals apart", () => {
    const OPTIONS = { options: { challenge: "AQID", user: { id: "BAU" } }, challenge: "signed" };

    async function device(refusal) {
        globalThis.PublicKeyCredential = function PublicKeyCredential() {};
        navigator.credentials = { create: vi.fn(() => Promise.reject(refusal)) };
        server("/passkey/anlegen/optionen/", () => respond(OPTIONS));
        open(FORMS[2]);
        await vi.waitFor(() => expect(dialog().querySelector("button:not(.flat)")).toBeTruthy());
        FORMS[2].submit();
    }

    afterEach(() => {
        delete globalThis.PublicKeyCredential;
    });

    it.each([
        ["NotAllowedError", "passkey.cancelled"],
        ["InvalidStateError", "passkey.exists"],
        ["UnknownError", "passkey.failed"],
    ])("%s", async (name, text) => {
        await device(Object.assign(new Error("nope"), { name }));
        await vi.waitFor(() => expect(line()).toBe(t(text)));
    });
});

const bytes = (...values) => Uint8Array.from(values).buffer;

/** What the device signs for the confirmation with a passkey. */
const SIGNATURE = {
    id: "AQID",
    rawId: bytes(1, 2, 3),
    type: "public-key",
    response: {
        clientDataJSON: bytes(7),
        authenticatorData: bytes(8),
        signature: bytes(9),
        userHandle: bytes(10),
    },
};

/**
 * A server for the form's request ('path'), with the confirmation with a passkey before it.
 *
 * 'answers' replaces the server's answer at a path of the confirmation,
 * 'device' what the device does with its question. Back come the bodies of
 * the form's requests.
 */
function confirming(path, reply, { answers = {}, device = () => Promise.resolve(SIGNATURE) } = {}) {
    navigator.credentials = { get: vi.fn(device), create: vi.fn(() => offline()) };
    const bodies = [];
    globalThis.fetch = (url, options = {}) => {
        const at = String(url).replace("/api/v1", "");
        if (at === "/passkey/" && (options.method ?? "GET") === "GET") return respond([]);
        if (at in answers) return answers[at]();
        if (at === RENEWAL) return renewalRefused();
        if (at === "/passkey/bestaetigen/optionen/") {
            return respond({ options: { challenge: "AQID" }, challenge: "gefragt" });
        }
        if (at === "/passkey/bestaetigen/") return respond({ bestaetigung: "bestaetigt" });
        if (at.startsWith(path)) {
            bodies.push(JSON.parse(options.body));
            return reply();
        }
        return offline();
    };
    return bodies;
}

/** Until the list of the passkey dialog stands: its field and its button come after it. */
const loaded = () =>
    vi.waitFor(() => expect(dialog().querySelector("button:not(.flat)")).toBeTruthy());

/**
 * The two forms that ask for the password, and what they do without one.
 *
 * 'submit' goes the whole way, with the password where its field stands.
 * 'confirm' takes the tap that confirms with a passkey: the address sends
 * on the same tap, a new key waits for one of its own. 'says' is the line
 * for no network, 'otherwise' the one for the rest.
 */
const PROVING = [
    {
        name: "EmailDialog",
        component: EmailDialog,
        props: { username: "zoe", email: "zoe@example.org" },
        path: "/email/",
        submit(password = "geheim") {
            type(dialog().querySelector("input[type=email]"), "zoe@example.com");
            const field = dialog().querySelector("input[type=password]");
            if (field) type(field, password);
            dialog().querySelector("button[type=submit]").click();
        },
        confirm() {
            this.submit();
        },
        says: () => t("email.no_connection"),
        otherwise: () => t("email.failed"),
    },
    {
        name: "PasskeyDialog",
        component: PasskeyDialog,
        props: { username: "zoe" },
        path: "/passkey/anlegen/optionen/",
        async submit(password = "geheim") {
            const field = dialog().querySelector("input[type=password]");
            if (field) type(field, password);
            const create = () => findButton(dialog(), t("passkey.create"));
            if (findButton(dialog(), t("passkey.confirm"))) {
                this.confirm();
                await vi.waitFor(() => expect(create().disabled).toBe(false));
            }
            create().click();
        },
        confirm() {
            findButton(dialog(), t("passkey.confirm")).click();
        },
        says: () => t("passkey.no_connection"),
        otherwise: () => t("passkey.failed"),
    },
];

/** The same forms for an account the device knows without a password. */
const WITHOUT_PASSWORD = PROVING.map((form) => ({
    ...form,
    props: { ...form.props, hasPassword: false },
}));

/**
 * The device thought the account had no password, and the server wants one
 * after all: set since the app last asked who is signed in. Until 10/2026
 * the sentence went to a field the dialog did not show, and nothing
 * happened that one could see.
 */
describe.each(WITHOUT_PASSWORD)(
    "$name, when the server wants a password the device did not know of",
    (form) => {
        it("shows the field with the server's sentence and sends the password next time", async () => {
            const bodies = confirming(form.path, () =>
                respond({ passwort: ["Dieses Feld ist erforderlich."] }, 400),
            );
            open(form);
            await loaded();
            expect(dialog().querySelector("input[type=password]")).toBeNull();

            await form.submit();

            await vi.waitFor(() =>
                expect(dialog().querySelector("input[type=password]")).toBeTruthy(),
            );
            const field = dialog().querySelector("input[type=password]");
            expect(atField(field)).toEqual(["Dieses Feld ist erforderlich."]);
            expect(field.getAttribute("aria-invalid")).toBe("true");
            expect(line()).toBe("");
            expect(bodies[0]).not.toHaveProperty("passwort");

            await form.submit("geheim");

            await vi.waitFor(() => expect(bodies).toHaveLength(2));
            expect(bodies[1].passwort).toBe("geheim");
            expect(bodies[1]).not.toHaveProperty("bestaetigung");
            // With a password the device is not asked again.
            expect(navigator.credentials.get).toHaveBeenCalledOnce();
            // It stays: the next refusal is again one about the password.
            await vi.waitFor(() =>
                expect(atField(field)).toEqual(["Dieses Feld ist erforderlich."]),
            );
        });
    },
);

/**
 * The other way round: the device thought the account had a password, and
 * the server wants a passkey's confirmation, because the password has gone
 * since. The field goes, the server's sentence stands in the line, and the
 * next try confirms with a passkey.
 */
describe.each(PROVING)("$name, when the server wants a confirmation instead", (form) => {
    it("leaves out the password and confirms with a passkey next time", async () => {
        const bodies = confirming(form.path, () =>
            respond({ bestaetigung: ["Bestätige es mit einem Passkey."] }, 400),
        );
        open(form);
        await loaded();

        await form.submit("geheim");

        await vi.waitFor(() => expect(dialog().querySelector("input[type=password]")).toBeNull());
        expect(line()).toBe("Bestätige es mit einem Passkey.");
        expect(bodies[0].passwort).toBe("geheim");
        expect(navigator.credentials.get).not.toHaveBeenCalled();

        await form.submit();

        await vi.waitFor(() => expect(bodies).toHaveLength(2));
        expect(bodies[1].bestaetigung).toBe("bestaetigt");
        expect(bodies[1]).not.toHaveProperty("passwort");
        expect(navigator.credentials.get).toHaveBeenCalledOnce();
    });
});

/**
 * Without a password a passkey of the account confirms before the form goes
 * out. What it meets the forms say in their line: the device's refusal as
 * the sign-in names it, and the server's sentence about the confirmation,
 * which is meant for the person. Until it is confirmed the form sends nothing.
 */
describe.each(WITHOUT_PASSWORD)("$name sorts what the confirmation meets", (form) => {
    async function confirmingWith(options) {
        const bodies = confirming(form.path, () => respond({}, 400), options);
        const close = open(form);
        await loaded();
        form.confirm();
        return { bodies, close };
    }

    it.each([
        ["NotAllowedError", () => t("confirm.cancelled")],
        ["UnknownError", (form) => form.otherwise()],
    ])("names the device's %s", async (name, expected) => {
        const refusal = Object.assign(new Error("nope"), { name });
        const { bodies } = await confirmingWith({ device: () => Promise.reject(refusal) });

        await vi.waitFor(() => expect(line()).toBe(expected(form)));
        expect(bodies).toEqual([]);
    });

    it.each(["/passkey/bestaetigen/optionen/", "/passkey/bestaetigen/"])(
        "says the server's sentence from %s",
        async (path) => {
            const { bodies } = await confirmingWith({
                answers: { [path]: () => respond({ detail: "Satz des Servers" }, 400) },
            });

            await vi.waitFor(() => expect(line()).toBe("Satz des Servers"));
            expect(bodies).toEqual([]);
        },
    );

    it("says what no network means", async () => {
        await confirmingWith({ answers: { "/passkey/bestaetigen/optionen/": offline } });
        await vi.waitFor(() => expect(line()).toBe(form.says()));
        expect(navigator.credentials.get).not.toHaveBeenCalled();
    });

    it("takes an error page of the server for a failure", async () => {
        const { bodies } = await confirmingWith({
            answers: { "/passkey/bestaetigen/": answer(500, "page") },
        });
        await vi.waitFor(() => expect(line()).toBe(form.otherwise()));
        expect(bodies).toEqual([]);
    });

    it("closes and leads to the login form when the sign-in no longer applies", async () => {
        const { close } = await confirmingWith({
            answers: { "/passkey/bestaetigen/optionen/": answer(401, { detail: "abgelaufen" }) },
        });
        await vi.waitFor(() => expect(close).toHaveBeenCalledOnce());
        expect(counting.signedOut).toBe(true);
    });

    it("puts what the form's answer says about the confirmation in the line", async () => {
        const bodies = confirming(form.path, () =>
            respond({ bestaetigung: [" Satz A ", "Satz B"] }, 400),
        );
        open(form);
        await loaded();

        await form.submit();

        await vi.waitFor(() => expect(line()).toBe("Satz A Satz B"));
        expect(bodies).toHaveLength(1);
        expect(bodies[0].bestaetigung).toBe("bestaetigt");
        expect(bodies[0]).not.toHaveProperty("passwort");
        expect([...dialog().querySelectorAll(".field p.error")]).toEqual([]);
    });
});
