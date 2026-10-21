import { mount, unmount } from "svelte";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import App from "./App.svelte";
import { db } from "./services/storage.js";
import { apply } from "./services/language.svelte.js";
import { APP_VERSION } from "./services/version.svelte.js";

let target;

beforeEach(async () => {
    // The language lives in the module. Without this the next case would see English.
    apply("de");
    target = document.createElement("div");
    document.body.append(target);
    await db.state.clear();
});

afterEach(() => {
    target.remove();
    vi.restoreAllMocks();
});

/** Signed in, as every sign-in leaves the storage: with both tokens of the pair. */
async function signedIn() {
    await db.state.bulkPut([
        { key: "access_token", value: "a" },
        { key: "refresh_token", value: "abc" },
    ]);
}

/** A start without network must not fail. It ends at the login. */
it("starts and shows the login", async () => {
    const app = mount(App, { target: target });
    await vi.waitFor(() => expect(target.textContent).toContain("Anmelden"));

    expect(target.querySelector("input[autocomplete=username]")).toBeTruthy();
    unmount(app);
});

/** With a stored refresh token it continues without signing in. */
it("shows the inventories when a token is stored", async () => {
    await signedIn();
    await db.state.put({ key: "me", value: { vorname: "Zoe" } });
    await db.inventories.put({
        id: 1,
        haus: "Kino Ost",
        stichtag: "2026-01-31",
        arten_anzeige: "Monatsinventur",
        status_anzeige: "Zählung",
    });

    const app = mount(App, { target: target });
    // Without network the last known state stays, and that comes from the
    // storage, not from the server's response.
    await vi.waitFor(() => expect(target.textContent).toContain("Kino Ost"));
    // Offline is said by the icon in the bar, not a banner over the content.
    await vi.waitFor(() => expect(target.querySelector(".sync-state svg.offline")).toBeTruthy());
    unmount(app);
});

/** The cut-off date is written the way the language does, not as the ISO date of the API. */
it("writes the cut-off date in the language's format", async () => {
    await signedIn();
    await db.state.put({ key: "me", value: { vorname: "Zoe" } });
    await db.inventories.put({
        id: 1,
        haus: "Kino Ost",
        stichtag: "2026-01-31",
        arten_anzeige: "Monatsinventur",
        status_anzeige: "Zählung",
    });

    const app = mount(App, { target: target });
    await vi.waitFor(() => expect(target.textContent).toContain("31.01.2026"));
    expect(target.textContent).not.toContain("2026-01-31");
    unmount(app);
});

/**
 * Before the first answer the list says nothing final: "nothing pending" came
 * too early (10/2026).
 */
it("waits for the first answer before it says that nothing is pending", async () => {
    await signedIn();
    await db.state.put({ key: "me", value: { vorname: "Zoe" } });
    globalThis.fetch = vi.fn(() => new Promise(() => {}));

    const app = mount(App, { target: target });
    await vi.waitFor(() => expect(target.textContent).toContain("Einen Augenblick"));
    expect(target.textContent).not.toContain("keine Inventur");
    unmount(app);
});

/** A language chosen without network goes to the account with the next start, like the theme. */
it("sends a language chosen without network with the next start", async () => {
    await signedIn();
    // Remembered without a first name: the header shows it only once the
    // fresh account has arrived.
    await db.state.put({ key: "me", value: { sprache: "de" } });
    localStorage.setItem("language_pending", "en");
    const patches = [];
    // The other cases run without network. The mock must not outlive this one.
    const offline = globalThis.fetch;
    globalThis.fetch = vi.fn((url, options = {}) => {
        const path = String(url).replace("/api/v1", "");
        if (options.method === "PATCH") patches.push(`${path} ${options.body}`);
        const data = path === "/ich/" ? { vorname: "Zoe", sprache: "en" } : [];
        return Promise.resolve({
            ok: true,
            status: 200,
            text: () => Promise.resolve(JSON.stringify(data)),
        });
    });

    const app = mount(App, { target: target });
    await vi.waitFor(() => expect(patches).toContain('/ich/ {"sprache":"en"}'));
    await vi.waitFor(() => expect(localStorage.getItem("language_pending")).toBeNull());
    // The start ends with the fresh account, and it sets the name and the
    // language in one step. Unmounted before, the language arrived during the
    // next case, which then spoke English (seen once in about twenty runs).
    await vi.waitFor(() =>
        expect(target.querySelector("button.account").textContent).toContain("Zoe"),
    );

    unmount(app);
    globalThis.fetch = offline;
    localStorage.clear();
    apply("de");
});

/**
 * The counters never see the web interface. The only way to the privacy
 * page for them leads through the app's account menu.
 */
it("links to the privacy page from the account menu", async () => {
    await signedIn();
    await db.state.put({ key: "me", value: { vorname: "Zoe" } });

    const app = mount(App, { target: target });
    await vi.waitFor(() => expect(target.querySelector("button.account")).toBeTruthy());
    target.querySelector("button.account").click();

    await vi.waitFor(() => expect(target.querySelector(".legal a")).toBeTruthy());
    // Absolute and not relative: the app lies under '/app/', the page next to it.
    // The language rides along, because the app has no Django session.
    expect(target.querySelector(".legal a").getAttribute("href")).toBe("/datenschutz/?sprache=de");
    unmount(app);
});

it("leaves the link out when the administration emptied the page", async () => {
    await signedIn();
    await db.state.put({ key: "me", value: { vorname: "Zoe", datenschutz: false } });

    const app = mount(App, { target: target });
    await vi.waitFor(() => expect(target.querySelector("button.account")).toBeTruthy());
    target.querySelector("button.account").click();

    await vi.waitFor(() => expect(target.querySelector(".choices")).toBeTruthy());
    expect(target.querySelector(".legal a")).toBeNull();
    unmount(app);
});

it("takes the privacy address the server names", async () => {
    // On an English server the page is called '/privacy/'. The app is built
    // with the German path and can only learn that from the server.
    await signedIn();
    await db.state.put({
        key: "me",
        value: { vorname: "Zoe", datenschutz_adresse: "/privacy/" },
    });

    const app = mount(App, { target: target });
    await vi.waitFor(() => expect(target.querySelector("button.account")).toBeTruthy());
    target.querySelector("button.account").click();

    await vi.waitFor(() => expect(target.querySelector(".legal a")).toBeTruthy());
    expect(target.querySelector(".legal a").getAttribute("href")).toBe("/privacy/?sprache=de");
    unmount(app);
});

it("links to the legal notice only where the administration wrote one", async () => {
    await signedIn();
    await db.state.put({ key: "me", value: { vorname: "Zoe" } });

    let app = mount(App, { target: target });
    await vi.waitFor(() => expect(target.querySelector("button.account")).toBeTruthy());
    target.querySelector("button.account").click();
    await vi.waitFor(() => expect(target.querySelector(".legal a")).toBeTruthy());
    // An old state without the entry has none: the shipped application has none either.
    expect(target.querySelectorAll(".legal a")).toHaveLength(1);
    unmount(app);

    // On an English server the page is called '/legal-notice/'.
    await db.state.put({
        key: "me",
        value: { vorname: "Zoe", impressum: true, impressum_adresse: "/legal-notice/" },
    });
    app = mount(App, { target: target });
    await vi.waitFor(() => expect(target.querySelector("button.account")).toBeTruthy());
    target.querySelector("button.account").click();
    await vi.waitFor(() => expect(target.querySelectorAll(".legal a")).toHaveLength(2));
    const [legalNotice, privacy] = target.querySelectorAll(".legal a");
    expect(legalNotice.textContent).toBe("Impressum");
    expect(legalNotice.getAttribute("href")).toBe("/legal-notice/?sprache=de");
    expect(privacy.getAttribute("href")).toBe("/datenschutz/?sprache=de");
    unmount(app);
});

/** Where the administration named an address, the menu offers a mail. Otherwise not. */
it("offers a mail for a problem or a suggestion only with an address", async () => {
    await signedIn();
    await db.state.put({ key: "me", value: { vorname: "Zoe", benutzername: "zoe" } });

    let app = mount(App, { target: target });
    await vi.waitFor(() => expect(target.querySelector("button.account")).toBeTruthy());
    target.querySelector("button.account").click();
    await vi.waitFor(() => expect(target.querySelector(".version")).toBeTruthy());
    expect(target.querySelector("a.feedback")).toBeNull();
    unmount(app);

    await db.state.put({
        key: "me",
        value: {
            vorname: "Zoe",
            benutzername: "zoe",
            rueckmeldeadresse: "rueckmeldung@example.org",
        },
    });
    app = mount(App, { target: target });
    await vi.waitFor(() => expect(target.querySelector("button.account")).toBeTruthy());
    target.querySelector("button.account").click();
    await vi.waitFor(() => expect(target.querySelector("a.feedback")).toBeTruthy());

    const link = new URL(target.querySelector("a.feedback").getAttribute("href"));
    expect(link.pathname).toBe("rueckmeldung@example.org");
    expect(link.searchParams.get("subject")).toBe("[CrossTally] Rückmeldung aus der Zähl-App");
    expect(link.searchParams.get("body")).toContain("Konto: zoe");
    unmount(app);
});

/** The first question on any report. The website shows it in its account menu too. */
it("shows the version it was built from", async () => {
    await signedIn();
    await db.state.put({ key: "me", value: { vorname: "Zoe" } });

    const app = mount(App, { target: target });
    await vi.waitFor(() => expect(target.querySelector("button.account")).toBeTruthy());
    target.querySelector("button.account").click();

    await vi.waitFor(() => expect(target.querySelector(".version")).toBeTruthy());
    expect(target.querySelector(".version").textContent).toBe(`Version ${APP_VERSION}`);
    unmount(app);
});

it("passes the app's language along to the privacy page", async () => {
    await signedIn();
    await db.state.put({ key: "me", value: { vorname: "Zoe" } });
    apply("en");

    const app = mount(App, { target: target });
    await vi.waitFor(() => expect(target.querySelector("button.account")).toBeTruthy());
    target.querySelector("button.account").click();

    await vi.waitFor(() => expect(target.querySelector(".legal a")).toBeTruthy());
    expect(target.querySelector(".legal a").getAttribute("href")).toBe("/datenschutz/?sprache=en");
    unmount(app);
});
