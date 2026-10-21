/**
 * The sign-in via the website's session and what it does with the state.
 *
 * Checked above all is the switch: the storage always belongs to exactly
 * one account, otherwise someone sends another's numbers on as their own.
 */

import { beforeEach, expect, it, vi } from "vitest";

import { changePassword, removePasskey, signOut, fromSession, get } from "./api.js";
import { SIGN_OUT_OWED, db, remember, remembered } from "./storage.js";
import { fetchInventories } from "./sync.js";
import { respond, responds } from "../test_support.js";

const ZOE = { id: 7, benutzername: "zoe", vorname: "Zoe" };

beforeEach(async () => {
    await Promise.all([db.state.clear(), db.inventories.clear(), db.positions.clear()]);
    document.cookie = "csrftoken=abc";
});

/** For Django a call with a token is anonymous: the language has to travel along. */
it("sends the device's language with every request", async () => {
    const network = responds({ id: 7 });
    globalThis.fetch = network;
    await db.state.put({ key: "access_token", value: "a" });

    await get("/ich/");

    const [url, options] = network.mock.calls[0];
    expect(url).toBe("/api/v1/ich/");
    expect(options.headers["X-Sprache"]).toBe("de");
});

//: Removes the CSRF cookie: one that expired long ago.
const CLEARED = "csrftoken=; expires=Thu, 01 Jan 1970 00:00:00 GMT";

/** Without cookie there is no session. Then the app saves itself the request. */
it("does not even ask without a CSRF cookie", async () => {
    document.cookie = CLEARED;
    const network = vi.fn();
    globalThis.fetch = network;

    expect(await fromSession()).toBeNull();
    expect(network).not.toHaveBeenCalled();
});

it("adopts the website's sign-in", async () => {
    globalThis.fetch = responds({ access: "a", refresh: "r", ich: ZOE });

    expect(await fromSession()).toEqual(ZOE);
    expect(await remembered("refresh_token")).toBe("r");
    expect(await remembered("me")).toEqual(ZOE);
});

/**
 * Of two cookies of that name Django checks the last one. A second one can
 * come from a site on a parent domain. Until 10/2026 the app sent the first,
 * the server refused the takeover, and an app with its sign-in from the
 * website signed itself out.
 */
it("sends the last of two CSRF cookies, the one Django checks", async () => {
    await db.state.bulkPut([
        { key: "me", value: ZOE },
        { key: "from_session", value: true },
        { key: "refresh_token", value: "r" },
    ]);
    const cookies = vi
        .spyOn(document, "cookie", "get")
        .mockReturnValue("csrftoken=PARENT; sessionid=s; csrftoken=OWN");
    const sent = [];
    globalThis.fetch = (url, options) => {
        sent.push(options.headers["X-CSRFToken"]);
        return options.headers["X-CSRFToken"] === "OWN"
            ? respond({ access: "a", refresh: "r2", ich: ZOE })
            : respond({ detail: "CSRF Failed: CSRF token incorrect." }, 403);
    };

    try {
        expect(await fromSession()).toEqual(ZOE);
    } finally {
        cookies.mockRestore();
    }

    expect(sent).toEqual(["OWN"]);
    expect(await remembered("refresh_token")).toBe("r2");
});

it("clears the state away when someone else signs in there", async () => {
    await db.state.put({ key: "me", value: ZOE });
    await db.inventories.put({ id: 1, haus: "Kino Ost" });
    globalThis.fetch = responds({
        access: "a",
        refresh: "r",
        ich: { id: 9, benutzername: "tom" },
    });

    await fromSession();
    expect(await db.inventories.count()).toBe(0);
    expect(await remembered("refresh_token")).toBe("r");
});

it("keeps the state when the same account comes back", async () => {
    await db.state.put({ key: "me", value: ZOE });
    await db.inventories.put({ id: 1, haus: "Kino Ost" });
    globalThis.fetch = responds({ access: "a", refresh: "r", ich: ZOE });

    await fromSession();
    expect(await db.inventories.count()).toBe(1);
});

/** Whoever has their token from the session is signed out with it too. */
it("signs out when the website's session is gone", async () => {
    await db.state.put({ key: "me", value: ZOE });
    await db.state.put({ key: "from_session", value: true });
    await db.state.put({ key: "refresh_token", value: "r" });
    globalThis.fetch = responds({ detail: "nein" }, 403);

    expect(await fromSession()).toBeNull();
    expect(await remembered("refresh_token")).toBeNull();
});

/** A choice made without network belongs to the account that made it, not to the next one. */
it("clears the choices that still wait for the account on sign-out", async () => {
    localStorage.setItem("theme_pending", "dark");
    localStorage.setItem("language_pending", "en");
    localStorage.setItem("theme", "dark");

    await signOut(false);

    expect(localStorage.getItem("theme_pending")).toBeNull();
    expect(localStorage.getItem("language_pending")).toBeNull();
    // What the browser shows stays until the next account says otherwise.
    expect(localStorage.getItem("theme")).toBe("dark");
    localStorage.clear();
});

/** An own sign-in in the app, by contrast, does not hang on the website. */
it("leaves a self-fetched token alone", async () => {
    await db.state.put({ key: "me", value: ZOE });
    await db.state.put({ key: "refresh_token", value: "r" });
    globalThis.fetch = responds({ detail: "nein" }, 403);

    expect(await fromSession()).toBeNull();
    expect(await remembered("refresh_token")).toBe("r");
});

/** Without network everything stays as it is. Counting continues offline. */
it("does not sign out without network", async () => {
    await db.state.put({ key: "me", value: ZOE });
    await db.state.put({ key: "from_session", value: true });
    await db.state.put({ key: "refresh_token", value: "r" });
    globalThis.fetch = () => Promise.reject(new TypeError("kein Netz"));

    expect(await fromSession()).toBeNull();
    expect(await remembered("refresh_token")).toBe("r");
});

it("cleans up on signing out even without network", async () => {
    await db.state.put({ key: "refresh_token", value: "r" });
    await db.inventories.put({ id: 1, haus: "Kino Ost" });
    globalThis.fetch = () => Promise.reject(new TypeError("kein Netz"));

    await signOut();
    expect(await remembered("refresh_token")).toBeNull();
    expect(await db.inventories.count()).toBe(0);
});

/**
 * Every table of the schema, also one added later: a shared device keeps
 * nothing. Only the mark of the clearing stays, for the other windows, and
 * without network the note that the server still has to hear of it.
 */
it("empties every table of the storage on signing out", async () => {
    for (const table of db.tables) {
        await table.put({ [table.schema.primKey.keyPath]: 1, inventur_id: 1 });
    }
    globalThis.fetch = () => Promise.reject(new TypeError("kein Netz"));

    await signOut();

    const kept = ["clearing", "sign_out_owed"];
    for (const table of db.tables) {
        const left = (await table.toArray()).filter((row) => !kept.includes(row.key));
        expect(left, table.name).toEqual([]);
    }
    expect(await db.state.count()).toBe(2);
    expect(db.tables.length).toBeGreaterThanOrEqual(6);
});

/**
 * A sign-out without network clears the device, but the website's session in
 * the same browser lives on. Until 10/2026 the next start took it over, and
 * the account was signed in again without anyone asking, on a shared phone
 * as the next person's. The sign-out stays owed and goes out first.
 */
it("ends the website's session on the next start when signing out had no network", async () => {
    await db.state.bulkPut([
        { key: "me", value: ZOE },
        { key: "from_session", value: true },
    ]);
    await withToken();
    globalThis.fetch = () => Promise.reject(new TypeError("kein Netz"));
    await signOut();

    let session = true;
    const requests = [];
    globalThis.fetch = (url, options) => {
        const path = url.replace("/api/v1", "");
        requests.push([path, options.headers.Authorization ?? "-", options.headers["X-CSRFToken"]]);
        if (path === "/abmelden/") {
            session = false;
            return raw("", 204);
        }
        return session
            ? respond({ access: "a2", refresh: "r2", ich: ZOE })
            : respond({ detail: "keine Sitzung" }, 403);
    };

    expect(await fromSession()).toBeNull();
    expect(requests).toEqual([["/abmelden/", "-", "abc"]]);
    expect(await remembered("me")).toBeNull();
    expect(await remembered("refresh_token")).toBeNull();

    // Ended: whoever signs in on the website later is taken over as before.
    session = true;
    expect(await fromSession()).toEqual(ZOE);
    expect(requests.at(-1)[0]).toBe("/token/aus-sitzung/");
});

it("takes nothing over from the website while the owed sign-out does not get through", async () => {
    await withToken();
    globalThis.fetch = () => Promise.reject(new TypeError("kein Netz"));
    await signOut();
    const requests = [];
    globalThis.fetch = (url) => {
        const path = url.replace("/api/v1", "");
        requests.push(path);
        return path === "/abmelden/"
            ? raw(ERROR_PAGE, 500)
            : respond({ access: "a2", refresh: "r2", ich: ZOE });
    };

    expect(await fromSession()).toBeNull();
    expect(await fromSession()).toBeNull();

    expect(requests).toEqual(["/abmelden/", "/abmelden/"]);
    expect(await remembered("refresh_token")).toBeNull();
});

/** A refresh token as the server signs it, with this id. The app reads only its payload. */
function refreshToken(jti) {
    const part = (data) =>
        btoa(JSON.stringify(data)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    const payload = { token_type: "refresh", exp: 1790000000, jti, user_id: "zoe" };
    return `${part({ alg: "HS256", typ: "JWT" })}.${part(payload)}.c2lnbmllcnQ`;
}

/** Signed in with a refresh token of this id, then signed out without network. */
async function signedOutOffline(jti) {
    await db.state.bulkPut([
        { key: "me", value: ZOE },
        { key: "access_token", value: "a" },
        { key: "refresh_token", value: refreshToken(jti) },
    ]);
    globalThis.fetch = () => Promise.reject(new TypeError("kein Netz"));
    await signOut();
}

/** A server that ends sessions and hands out pairs, and the requests it heard. */
function sessionServer(who = ZOE) {
    const sent = [];
    globalThis.fetch = (url, options) => {
        const path = url.replace("/api/v1", "");
        sent.push([
            path,
            options.headers.Authorization ?? "-",
            options.headers["X-CSRFToken"] ?? "-",
            JSON.parse(options.body),
        ]);
        return path.startsWith("/abmelden/")
            ? raw("", 204)
            : respond({ access: "a2", refresh: "r2", ich: who });
    };
    return sent;
}

/**
 * Kept, the refresh token would stay behind on a shared phone as a sign-in.
 * Its id is no sign-in: it only names the token to the server, which blocks
 * it on the next start. Until 10/2026 the token held until it ran out.
 */
it("notes only the token's id and the CSRF value for the sign-out it owes", async () => {
    await signedOutOffline("101abc");

    expect(await remembered(SIGN_OUT_OWED)).toEqual({ jti: "101abc", csrf: "abc" });
    const left = JSON.stringify(await db.state.toArray());
    expect(left).not.toContain(refreshToken("101abc"));
    expect(await remembered("refresh_token")).toBeNull();
});

/** The token by its id at its own door, then the session: nobody signed in on the website since. */
it("names the owed token at its own door and ends the website's session", async () => {
    await signedOutOffline("101abc");
    const sent = sessionServer();

    expect(await fromSession()).toBeNull();

    expect(sent).toEqual([
        ["/abmelden/nachgeholt/", "-", "-", { jti: "101abc" }],
        ["/abmelden/", "-", "abc", {}],
    ]);
    expect(await remembered(SIGN_OUT_OWED)).toBeNull();
});

/**
 * Django swaps the CSRF value with every sign-in on the website. Another one
 * than at the sign-out: someone signed in there since, on purpose, and the
 * owed sign-out must not end that session. Until 10/2026 it did. Only the
 * token goes, and the app takes the session over as on any start. Without a
 * CSRF cookie at the sign-out there was no session to end at all. The token
 * goes by its id at its own door: through the session the server blocked it
 * only for the session's account, and whoever signed in since may be someone
 * else (until 10/2026).
 */
it("leaves a website session opened since the owed sign-out alone and takes it over", async () => {
    const ZARA = { id: 8, benutzername: "zara", vorname: "Zara" };
    for (const before of ["abc", ""]) {
        document.cookie = before ? `csrftoken=${before}` : CLEARED;
        await signedOutOffline("101abc");
        document.cookie = "csrftoken=neu";
        const sent = sessionServer(ZARA);

        expect(await fromSession(), before).toEqual(ZARA);

        expect(sent, before).toEqual([
            ["/abmelden/nachgeholt/", "-", "-", { jti: "101abc" }],
            ["/token/aus-sitzung/", "-", "neu", {}],
        ]);
        expect(await remembered(SIGN_OUT_OWED), before).toBeNull();
        await db.state.clear();
    }
});

/** A note from before 10/2026 names neither: it ends the session, whatever came since. */
it("settles a sign-out the version before owed as it did", async () => {
    await remember(SIGN_OUT_OWED, true);
    document.cookie = "csrftoken=neu";
    const sent = sessionServer();

    expect(await fromSession()).toBeNull();

    expect(sent).toEqual([["/abmelden/", "-", "neu", {}]]);
    expect(await remembered(SIGN_OUT_OWED)).toBeNull();
});

/** 401: there is no session. 403: none this browser can use. Nothing is left to end. */
it("lets the owed sign-out go when there is no session left to end", async () => {
    for (const status of [401, 403]) {
        await withToken();
        globalThis.fetch = () => Promise.reject(new TypeError("kein Netz"));
        await signOut();
        const requests = [];
        globalThis.fetch = (url) => {
            const path = url.replace("/api/v1", "");
            requests.push(path);
            return path === "/abmelden/"
                ? respond({ detail: "nein" }, status)
                : respond({ access: "a2", refresh: "r2", ich: ZOE });
        };

        expect(await fromSession()).toBeNull();
        expect(await fromSession()).toEqual(ZOE);

        expect(requests, String(status)).toEqual(["/abmelden/", "/token/aus-sitzung/"]);
    }
});

//: A token's id as the server writes it: 32 hex digits.
const JTI = "1010a2b3c4d5e6f708192a3b4c5d6e7f";

/**
 * A server with no session this browser can use ('status' at '/abmelden/'),
 * and its door for an owed token, answering with 'owed'. Back comes what it
 * heard, the cookies too ('credentials').
 */
function sessionless(status, owed = () => raw("", 204)) {
    const heard = [];
    globalThis.fetch = (url, options) => {
        const path = url.replace("/api/v1", "");
        heard.push({
            path,
            credentials: options.credentials ?? "same-origin",
            csrf: options.headers["X-CSRFToken"] ?? "-",
            token: options.headers.Authorization ?? "-",
            body: JSON.parse(options.body),
        });
        if (path === "/abmelden/") return respond({ detail: "nein" }, status);
        if (path === "/abmelden/nachgeholt/") return owed();
        return respond({ access: "a2", refresh: "r2", ich: ZOE });
    };
    return heard;
}

/** What the owed token's door hears: its id alone, without cookie, session or token. */
const OWED = { path: "/abmelden/nachgeholt/", credentials: "omit", csrf: "-", token: "-" };

/**
 * Without a session no session blocks the token: 401 says there is none,
 * 403 that this browser cannot use it. Until 10/2026 the note went all the
 * same, and the token held until it ran out. Now its id goes to the door
 * that takes it without any sign-in.
 */
it("names the owed token by its id alone where no session can end it", async () => {
    for (const status of [401, 403]) {
        await signedOutOffline(JTI);
        const heard = sessionless(status);

        expect(await fromSession(), String(status)).toBeNull();

        expect(heard.map((entry) => entry.path)).toEqual(["/abmelden/nachgeholt/", "/abmelden/"]);
        expect(heard[0]).toEqual({ ...OWED, body: { jti: JTI } });
        expect(await remembered(SIGN_OUT_OWED), String(status)).toBeNull();
        // Settled: the next start takes the website's sign-in over as before.
        expect(await fromSession(), String(status)).toEqual(ZOE);
        await db.state.clear();
    }
});

/** The installed app on the iPhone has no cookie of the website: there is no session at all. */
it("names the owed token by its id also where the browser has no cookie of the website", async () => {
    document.cookie = CLEARED;
    await signedOutOffline(JTI);
    const heard = sessionless(401);

    expect(await fromSession()).toBeNull();

    expect(heard).toEqual([{ ...OWED, body: { jti: JTI } }]);
    expect(await remembered(SIGN_OUT_OWED)).toBeNull();
});

it("keeps the sign-out owed while the token's id does not reach the server", async () => {
    const failures = [() => Promise.reject(new TypeError("Funkloch")), () => raw(ERROR_PAGE, 500)];
    for (const failure of failures) {
        await signedOutOffline(JTI);
        const heard = sessionless(401, failure);

        expect(await fromSession()).toBeNull();
        expect(await fromSession()).toBeNull();

        expect(heard.filter((entry) => entry.path === OWED.path)).toHaveLength(2);
        expect(heard.some((entry) => entry.path === "/token/aus-sitzung/")).toBe(false);
        expect(await remembered(SIGN_OUT_OWED)).toEqual({ jti: JTI, csrf: "abc" });
        await db.state.clear();
    }
});

/** Settled only once both sides are done: the token at its own door, and the session. */
it("keeps the sign-out owed until the website's session has ended as well", async () => {
    await signedOutOffline(JTI);
    let failing = true;
    const heard = [];
    globalThis.fetch = (url) => {
        const path = url.replace("/api/v1", "");
        heard.push(path);
        if (path === "/abmelden/nachgeholt/") return raw("", 204);
        if (path === "/abmelden/") return failing ? raw(ERROR_PAGE, 502) : raw("", 204);
        return respond({ access: "a2", refresh: "r2", ich: ZOE });
    };

    expect(await fromSession()).toBeNull();
    expect(await remembered(SIGN_OUT_OWED)).toEqual({ jti: JTI, csrf: "abc" });

    failing = false;
    expect(await fromSession()).toBeNull();

    const twice = ["/abmelden/nachgeholt/", "/abmelden/"];
    expect(heard).toEqual([...twice, ...twice]);
    expect(await remembered(SIGN_OUT_OWED)).toBeNull();
});

/** Signed in through the website's session, with a refresh token of this id. */
async function fromTheWebsite(jti) {
    await db.state.bulkPut([
        { key: "me", value: ZOE },
        { key: "from_session", value: true },
        { key: "access_token", value: "a" },
        { key: "refresh_token", value: refreshToken(jti) },
    ]);
}

/**
 * A website without the session the app had its sign-in from: the takeover
 * answers 'takeover', the owed token's door 'owed'. Back comes what it heard.
 */
function website(owed = () => raw("", 204), takeover = () => respond({ detail: "nein" }, 403)) {
    const heard = [];
    globalThis.fetch = (url, options) => {
        const path = url.replace("/api/v1", "");
        heard.push({
            path,
            credentials: options.credentials ?? "same-origin",
            csrf: options.headers["X-CSRFToken"] ?? "-",
            token: options.headers.Authorization ?? "-",
            body: JSON.parse(options.body),
        });
        if (path === "/abmelden/nachgeholt/") return owed();
        if (path === "/token/aus-sitzung/") return takeover();
        return raw("", 204);
    };
    return heard;
}

/**
 * The app follows the website's sign-out: its token came from the session,
 * and the session is gone. Until 10/2026 it dropped the token without a
 * word, and the token held until it ran out. Its id goes to its own door.
 * The website's session, maybe someone else's by now, is not touched.
 */
it("names the token by its id alone when it follows the website's sign-out", async () => {
    await fromTheWebsite(JTI);
    const heard = website();

    expect(await fromSession()).toBeNull();

    expect(heard.map((entry) => entry.path)).toEqual([
        "/token/aus-sitzung/",
        "/abmelden/nachgeholt/",
    ]);
    expect(heard[1]).toEqual({ ...OWED, body: { jti: JTI } });
    expect(await remembered("refresh_token")).toBeNull();
    expect(await remembered(SIGN_OUT_OWED)).toBeNull();
});

/**
 * Without network the id stays owed, in a note no start ends a session for:
 * the session there by then may be someone else's. Its empty CSRF value says
 * so. A note naming no value would end it.
 */
it("owes the token alone when its id does not get through, and ends no session for it", async () => {
    const ZARA = { id: 8, benutzername: "zara", vorname: "Zara" };
    await fromTheWebsite(JTI);
    website(() => Promise.reject(new TypeError("Funkloch")));

    expect(await fromSession()).toBeNull();

    expect(await remembered(SIGN_OUT_OWED)).toEqual({ jti: JTI, csrf: "" });
    expect(await remembered("refresh_token")).toBeNull();

    // The next start, and someone else is signed in on the website by now.
    document.cookie = "csrftoken=neu";
    const heard = website(undefined, () => respond({ access: "a3", refresh: "r3", ich: ZARA }));

    expect(await fromSession()).toEqual(ZARA);

    expect(heard.map((entry) => entry.path)).toEqual([
        "/abmelden/nachgeholt/",
        "/token/aus-sitzung/",
    ]);
    expect(await remembered(SIGN_OUT_OWED)).toBeNull();
});

/** A note from before 10/2026 names no token. Without a cookie there is nothing to settle it by. */
it("keeps a sign-out the version before owed while there is no cookie of the website", async () => {
    document.cookie = CLEARED;
    await remember(SIGN_OUT_OWED, true);
    const heard = sessionless(401);

    expect(await fromSession()).toBeNull();

    expect(heard).toEqual([]);
    expect(await remembered(SIGN_OUT_OWED)).toBe(true);
});

/** A 400 says the id is malformed. It will never be good, so nothing is owed any more. */
it("lets the owed sign-out go when the server refuses the token's id for good", async () => {
    await signedOutOffline(JTI);
    sessionless(403, () => respond({ jti: ["Das ist keine Kennung."] }, 400));

    expect(await fromSession()).toBeNull();

    expect(await remembered(SIGN_OUT_OWED)).toBeNull();
});

/**
 * An expired sign-in ends nothing with its token, and the server refuses the
 * call before it could end the session. Until 10/2026 the session survived.
 * Once more without the token: then the session alone signs out.
 */
it("signs the website's session out without the token when the sign-in has expired", async () => {
    await withToken();
    const requests = [];
    globalThis.fetch = (url, options) => {
        const path = url.replace("/api/v1", "");
        requests.push(`${path} ${options.headers.Authorization ?? "-"}`);
        if (path === "/abmelden/" && !options.headers.Authorization) return raw("", 204);
        if (path === "/token/aus-sitzung/") {
            return respond({ access: "a2", refresh: "r2", ich: ZOE });
        }
        return raw(JSON.stringify({ detail: "abgelaufen" }), 401);
    };

    await signOut();
    // Reached: the next start owes nothing and takes the website's sign-in over.
    await fromSession();

    expect(requests).toEqual([
        "/abmelden/ Bearer a",
        "/token/erneuern/ -",
        "/abmelden/ -",
        "/token/aus-sitzung/ -",
    ]);
});

/**
 * Signed out while a sync is on its way: its answer goes nowhere. Until
 * 10/2026 it landed in the storage just cleared, for the next account to see.
 */
it("keeps an answer that comes after signing out out of the storage", async () => {
    await db.state.bulkPut([
        { key: "access_token", value: "a" },
        { key: "refresh_token", value: "r" },
    ]);
    let release;
    const late = new Promise((resolve) => (release = resolve));
    globalThis.fetch = async (url) => {
        if (String(url).endsWith("/abmelden/")) {
            return { ok: true, status: 204, text: () => Promise.resolve("") };
        }
        await late;
        const list = JSON.stringify([{ id: 1, haus: "Kino Ost", stichtag: "2026-01-31" }]);
        return { ok: true, status: 200, text: () => Promise.resolve(list) };
    };

    const asked = fetchInventories().catch((error) => error);
    await signOut();
    release();

    expect((await asked).status).toBe(0);
    expect(await db.inventories.count()).toBe(0);
});

/**
 * Refreshing the access token, and when the sign-in ends.
 *
 * Only the server's refusal of the token (401) ends it. Until 10/2026 every
 * other answer did too, a 502 while the proxy restarted. What followed went
 * out without a token, and the server answered it through the website's
 * session, as whoever was signed in there.
 */
const ERROR_PAGE = "<!doctype html><h1>Server Error (500)</h1>";

function raw(text, status) {
    return Promise.resolve({ ok: status < 400, status, text: () => Promise.resolve(text) });
}

async function withToken() {
    await db.state.bulkPut([
        { key: "access_token", value: "a" },
        { key: "refresh_token", value: "r" },
    ]);
}

it("keeps the sign-in when the refresh meets a passing error, and tries again later", async () => {
    const answers = {
        400: JSON.stringify({ refresh: ["Dieses Feld ist zwingend erforderlich."] }),
        429: JSON.stringify({ detail: "Zu viele Anfragen." }),
        500: ERROR_PAGE,
        502: "<html>Bad Gateway</html>",
        503: "<html>Service Unavailable</html>",
    };
    for (const [status, page] of Object.entries(answers)) {
        await withToken();
        globalThis.fetch = (url) =>
            url.endsWith("/token/erneuern/")
                ? raw(page, Number(status))
                : raw(JSON.stringify({ detail: "abgelaufen" }), 401);

        const error = await get("/inventuren/").catch((cause) => cause);

        expect(error.isTemporary, status).toBe(true);
        expect(error.isSignedOut, status).toBe(false);
        expect(await remembered("access_token"), status).toBe("a");
        expect(await remembered("refresh_token"), status).toBe("r");
    }
});

/**
 * Sent without its token, a request took the browser's cookies along, and
 * the server answered it through the website's session, as whoever was
 * signed in there (until 10/2026). Without a token it does not go out.
 */
it("sends no request without its token and says that the sign-in is missing", async () => {
    const network = responds([{ id: 9, haus: "Kino West", stichtag: "2026-01-31" }]);
    globalThis.fetch = network;

    const error = await get("/inventuren/").catch((cause) => cause);

    expect(error.isSignedOut).toBe(true);
    expect(network).not.toHaveBeenCalled();
});

it("renews first when only the refresh token is left, and sends with the new one", async () => {
    await db.state.put({ key: "refresh_token", value: "r" });
    const requests = [];
    globalThis.fetch = (url, options) => {
        requests.push(`${url.replace("/api/v1", "")} ${options.headers.Authorization ?? "-"}`);
        return url.endsWith("/token/erneuern/")
            ? raw(JSON.stringify({ access: "a2", refresh: "r2" }), 200)
            : raw(JSON.stringify([]), 200);
    };

    expect(await get("/inventuren/")).toEqual([]);

    expect(requests).toEqual(["/token/erneuern/ -", "/inventuren/ Bearer a2"]);
});

it("keeps the token when the refresh fails only on the network", async () => {
    await withToken();
    globalThis.fetch = (url) =>
        url.endsWith("/token/erneuern/")
            ? Promise.reject(new TypeError("Funkloch"))
            : raw(JSON.stringify({ detail: "abgelaufen" }), 401);

    const error = await get("/inventuren/").catch((cause) => cause);

    // Status 0 means "again later", because in a dead spot nobody signs in anew.
    expect(error.status).toBe(0);
    expect(await remembered("refresh_token")).toBe("r");
});

it("reads an error page as a fault of the server, not as missing network", async () => {
    await withToken();
    globalThis.fetch = () => raw(ERROR_PAGE, 500);

    const error = await get("/inventuren/").catch((cause) => cause);

    expect(error.status).toBe(500);
});

it("refreshes only once when two requests are rejected at the same time", async () => {
    await withToken();
    let refreshed = 0;
    globalThis.fetch = (url, options) => {
        if (url.endsWith("/token/erneuern/")) {
            refreshed += 1;
            // Like the server: the old token no longer applies after the swap.
            return refreshed === 1
                ? raw(JSON.stringify({ access: "a2", refresh: "r2" }), 200)
                : raw(JSON.stringify({ detail: "gesperrt" }), 401);
        }
        return options.headers.Authorization === "Bearer a2"
            ? raw(JSON.stringify([]), 200)
            : raw(JSON.stringify({ detail: "abgelaufen" }), 401);
    };

    await Promise.all([get("/inventuren/"), get("/ich/")]);

    expect(refreshed).toBe(1);
    expect(await remembered("refresh_token")).toBe("r2");
});

/**
 * Signing out blocks the refresh token the app names. With an expired access
 * token the request refreshes first, and the server swaps the refresh token
 * and blocks the old one. Until 10/2026 the attempt after the refresh named
 * the old one again, and the new one stayed valid for two weeks.
 */
it("names the refresh token of the moment when signing out after a refresh", async () => {
    await withToken();
    const sent = [];
    globalThis.fetch = (url, options) => {
        if (url.endsWith("/token/erneuern/")) {
            return raw(JSON.stringify({ access: "a2", refresh: "r2" }), 200);
        }
        sent.push([options.headers.Authorization, JSON.parse(options.body)]);
        return options.headers.Authorization === "Bearer a2"
            ? raw("", 204)
            : raw(JSON.stringify({ detail: "abgelaufen" }), 401);
    };

    await signOut();

    expect(sent).toEqual([
        ["Bearer a", { refresh: "r" }],
        ["Bearer a2", { refresh: "r2" }],
    ]);
});

/**
 * The renewal blocks the token the note names. When the sign-out after it
 * does not get through, the fresh token is the one still valid, and the next
 * start has to block that one.
 */
it("owes the fresh token when the sign-out after a renewal does not get through", async () => {
    const fresh = refreshToken(JTI);
    await withToken();
    globalThis.fetch = (url, options) => {
        if (url.endsWith("/token/erneuern/")) {
            return raw(JSON.stringify({ access: "a2", refresh: fresh }), 200);
        }
        return options.headers.Authorization === "Bearer a2"
            ? Promise.reject(new TypeError("Funkloch"))
            : raw(JSON.stringify({ detail: "abgelaufen" }), 401);
    };

    await signOut();

    expect(await remembered(SIGN_OUT_OWED)).toEqual({ jti: JTI, csrf: "abc" });
    expect(JSON.stringify(await db.state.toArray())).not.toContain(fresh);
});

/**
 * A new password ends the sign-in it was changed with. The background sync
 * keeps running meanwhile, and its rejected refresh must not take the fresh
 * tokens along.
 */
it("keeps fresh tokens that arrived while the refresh was being rejected", async () => {
    await withToken();
    globalThis.fetch = async (url, options) => {
        if (url.endsWith("/token/erneuern/")) {
            // The change comes through exactly between question and answer.
            await db.state.bulkPut([
                { key: "access_token", value: "a-new" },
                { key: "refresh_token", value: "r-new" },
            ]);
            return raw(JSON.stringify({ detail: "Passwort geändert" }), 401);
        }
        return options.headers.Authorization === "Bearer a-new"
            ? raw(JSON.stringify([]), 200)
            : raw(JSON.stringify({ detail: "abgelaufen" }), 401);
    };

    expect(await get("/inventuren/")).toEqual([]);
    expect(await remembered("access_token")).toBe("a-new");
    expect(await remembered("refresh_token")).toBe("r-new");
});

it("still signs out when nothing fresh has arrived", async () => {
    await withToken();
    globalThis.fetch = () => raw(JSON.stringify({ detail: "gesperrt" }), 401);

    const error = await get("/inventuren/").catch((cause) => cause);

    expect(error.isSignedOut).toBe(true);
    expect(await remembered("refresh_token")).toBeNull();
});

it("stores the tokens the change of the password brings", async () => {
    await withToken();
    const network = vi.fn(() => raw(JSON.stringify({ access: "a-new", refresh: "r-new" }), 200));
    globalThis.fetch = network;

    await changePassword("alt", "neu");

    const [url, options] = network.mock.calls[0];
    expect(url).toBe("/api/v1/passwort/");
    expect(options.headers.Authorization).toBe("Bearer a");
    expect(JSON.parse(options.body)).toEqual({ altes_passwort: "alt", neues_passwort: "neu" });
    expect(await remembered("access_token")).toBe("a-new");
    expect(await remembered("refresh_token")).toBe("r-new");
});

it("leaves the tokens alone when the change is rejected", async () => {
    await withToken();
    globalThis.fetch = () => raw(JSON.stringify({ altes_passwort: ["stimmt nicht"] }), 400);

    const error = await changePassword("daneben", "neu").catch((cause) => cause);

    expect(error.status).toBe(400);
    expect(error.data.altes_passwort).toEqual(["stimmt nicht"]);
    expect(await remembered("access_token")).toBe("a");
    expect(await remembered("refresh_token")).toBe("r");
});

/**
 * Removing a passkey signs the account out everywhere else, so it goes
 * against the password, as a new key. The app names its own refresh token,
 * and the server spares it and this session: the device keeps its pair, and
 * the answer is empty. The token is the one of the moment, after a refresh
 * too.
 */
it("names its own refresh token and the password when removing a passkey, and keeps its pair", async () => {
    await withToken();
    const sent = [];
    globalThis.fetch = (url, options) => {
        if (url.endsWith("/token/erneuern/")) {
            return raw(JSON.stringify({ access: "a2", refresh: "r2" }), 200);
        }
        sent.push([url, options.method, options.headers.Authorization, JSON.parse(options.body)]);
        return options.headers.Authorization === "Bearer a2"
            ? raw("", 204)
            : raw(JSON.stringify({ detail: "abgelaufen" }), 401);
    };

    expect(await removePasskey(5, { password: "geheim" })).toBeNull();

    expect(sent).toEqual([
        ["/api/v1/passkey/5/", "DELETE", "Bearer a", { refresh: "r", passwort: "geheim" }],
        ["/api/v1/passkey/5/", "DELETE", "Bearer a2", { refresh: "r2", passwort: "geheim" }],
    ]);
    expect(await remembered("access_token")).toBe("a2");
    expect(await remembered("refresh_token")).toBe("r2");
});

/** An account without a password sends a passkey's confirmation in its place. */
it("sends the passkey's confirmation in place of the password when removing", async () => {
    await withToken();
    const network = vi.fn(() => raw("", 204));
    globalThis.fetch = network;

    await removePasskey(5, { confirmation: { token: "bestaetigt", until: Date.now() + 1000 } });

    const [url, options] = network.mock.calls[0];
    expect([url, options.method]).toEqual(["/api/v1/passkey/5/", "DELETE"]);
    expect(JSON.parse(options.body)).toEqual({ refresh: "r", bestaetigung: "bestaetigt" });
});

/** A token the server does not know: it removes nothing and asks to sign in anew. */
it("keeps its pair when the server refuses the removal, and passes its sentence on", async () => {
    await withToken();
    globalThis.fetch = () => raw(JSON.stringify({ detail: "Bitte melde dich neu an." }), 403);

    const error = await removePasskey(5, { password: "geheim" }).catch((cause) => cause);

    expect([error.status, error.data.detail]).toEqual([403, "Bitte melde dich neu an."]);
    expect(await remembered("access_token")).toBe("a");
    expect(await remembered("refresh_token")).toBe("r");
});

/**
 * A server from before 10/2026 ignores the token, signs this device out too
 * and sends it a fresh pair instead. Stored, the device stays signed in there.
 */
it("stores the pair a server from before sends with the removal", async () => {
    await withToken();
    globalThis.fetch = vi.fn(() => raw(JSON.stringify({ access: "a-new", refresh: "r-new" }), 200));

    await removePasskey(5, { password: "geheim" });

    expect(await remembered("access_token")).toBe("a-new");
    expect(await remembered("refresh_token")).toBe("r-new");
});
