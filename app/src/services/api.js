/**
 * The wire to the API: hold tokens, refresh them, make requests.
 *
 * The app runs under the same origin as Django, so a relative path suffices
 * and there is no CORS. Signed in is via JWT: an access token for an hour, a
 * refresh token for two weeks.
 *
 * The API speaks German: its paths ('/ich/', '/inventuren/') and the fields
 * of its rows keep their names here. Everything the app names itself is
 * English.
 */

import { createCredential, credentialToJSON, fromBase64url, getCredential } from "./passkey.js";
import {
    SIGN_OUT_OWED,
    clearAll,
    currentMark,
    db,
    forget,
    markHolds,
    markValue,
    remember,
    remembered,
    unsavedCount,
    unsentCount,
} from "./storage.js";
import { language, t } from "./language.svelte.js";

const BASE = "/api/v1";

export class ApiError extends Error {
    constructor(status, data) {
        super(`API ${status}`);
        this.status = status;
        this.data = data;
    }

    /** Whether it is worth trying again later. */
    get isTemporary() {
        return this.status === 0 || this.status >= 500 || this.isSignedOut;
    }

    /**
     * The server no longer knows the sign-in: the token has expired or is
     * blocked, or there is none left ('call'). That is no verdict on the
     * job: it waits until someone signs in anew, instead of counting as
     * rejected.
     *
     * A 403 is no such sign. A request that needs the sign-in carries its
     * token, and with one the server never asks the website's session. Its
     * 403 says that this account may not do this: only a manager decides
     * whether the target counts. Until 10/2026 a request could go out without
     * its token, and the session's "CSRF token missing" counted as a refusal.
     */
    get isSignedOut() {
        return this.status === 401;
    }

    /**
     * The inventory is not being counted right now, but will be again: it
     * was taken back into preparation. No verdict on the job either. It waits
     * on the device until the count runs again ('submit' in 'sync/queue.js').
     */
    get isOnHold() {
        return (
            this.status === 409 &&
            this.data?.konflikt === "status" &&
            this.data?.vorlaeufig === true
        );
    }
}

/**
 * Signed out or switched while this step was on its way, in this window or another one.
 *
 * What it brought, and what it would write or send, belongs to the account
 * of before. Status 0 makes it "again later" everywhere ('isTemporary'): no
 * job counts as refused for it, and nothing is noted. Its sentence can show,
 * in the form a window still had open before it loads anew, so it comes in
 * the app's language (until 10/2026 always German).
 */
export class SignedOutMeanwhile extends ApiError {
    constructor() {
        super(0, { detail: t("error.signed_out_meanwhile") });
    }
}

/**
 * The sign-in could not be renewed this time, and it still holds.
 *
 * The server took no decision on the token: too many requests (429), or a
 * request it did not take as sent (400). The tokens stay ('refreshOnce'),
 * and what wanted the renewal waits like a request without network: no job
 * counts as refused for it.
 */
class NotRenewed extends ApiError {
    get isTemporary() {
        return true;
    }
}

//: What this window does once another window cleared the storage under it:
//: it loads anew and shows what the storage holds now. Its state in memory
//: belongs to the account of before. Replaced in the tests.
export const thisWindow = { reload: () => window.location.reload() };

function signedOutMeanwhile(taken) {
    // Still this window's own mark: another window cleared, not this one.
    if (taken === currentMark()) thisWindow.reload();
    return new SignedOutMeanwhile();
}

/**
 * 'change' in one transaction, only while the storage is the one 'taken' was taken on.
 *
 * Every write that follows an answer goes this way, and every entry. The
 * check and the write are one step, so a sign-out in this window or another
 * comes before both or after both. Until 10/2026 one check after the answer
 * had to do for several writes, a refusal or a question was written without
 * any check, and the next account found the previous one's number and could
 * send it under its own name. 'change' only touches the storage: what it
 * awaits besides would end the transaction.
 */
export async function guarded(taken, change) {
    const expected = await markValue(taken);
    return await db.transaction("rw", db.tables, async () => {
        if (!(await markHolds(expected))) throw signedOutMeanwhile(taken);
        return await change();
    });
}

/** What the storage holds under 'key', read in one step with the check of 'taken'. */
async function rememberedAt(taken, key) {
    const expected = await markValue(taken);
    return await db.transaction("r", db.state, async () => {
        if (!(await markHolds(expected))) throw signedOutMeanwhile(taken);
        return key === null ? null : await remembered(key);
    });
}

/**
 * Unsent things of another account still lie on the device.
 *
 * A switch clears the storage, and the numbers would be gone. Sending them
 * out under the new account would not work either, because someone else
 * counted. So ask first. 'count' and 'who' tell the login form what to name,
 * 'unsaved' the forms the app left behind ('unsavedCount').
 */
export class ForeignEntries extends Error {
    constructor(count, who, unsaved = 0) {
        super("Ungesendete Eingaben eines anderen Kontos");
        this.count = count;
        this.who = who;
        this.unsaved = unsaved;
    }
}

async function parse(response) {
    if (response.status === 204) return null;
    const text = await response.text();
    let data = null;
    try {
        data = text ? JSON.parse(text) : null;
    } catch (error) {
        // An error page of the server is HTML. Its status has to arrive
        // nonetheless, otherwise a 500 would look like "no network".
        if (response.ok) throw error;
    }
    if (!response.ok) throw new ApiError(response.status, data);
    return data;
}

/**
 * One request, for the storage 'taken' was taken on.
 *
 * Nothing goes out once that storage is cleared, and no answer comes back
 * from it, a refusal or a question no more than a success: it belongs to the
 * account of before ('SignedOutMeanwhile'). Until 10/2026 only a success was
 * held back. A 409 after signing out wrote its question into the cleared
 * storage, and the next account could answer it with the previous one's
 * number.
 */
async function call(path, options = {}, withToken = true, taken = currentMark()) {
    const headers = {
        "Content-Type": "application/json",
        // For Django a call with a token is anonymous, and its messages would
        // come in the server's language. The device's language is the one the
        // account chose; the server reads the header ('SprachwahlMiddleware').
        "X-Sprache": language.choice,
        ...(options.headers ?? {}),
    };
    // The token in one read with the check: a sign-out in between sends
    // neither the token of before nor one of the next account.
    const access = await rememberedAt(taken, withToken ? "access_token" : null);
    // Meant to carry a token and none there: it stays here, as if the server
    // had refused the sign-in ('request' renews first where it can). Sent
    // without one, the browser's cookies went along, and the server answered
    // through the website's session, as whoever was signed in there, on a
    // shared computer someone else (until 10/2026).
    if (withToken && !access) throw new ApiError(401, { detail: t("error.signed_out_meanwhile") });
    if (access) headers.Authorization = `Bearer ${access}`;
    let data;
    try {
        data = await parse(await fetch(`${BASE}${path}`, { ...options, headers }));
    } catch (error) {
        await rememberedAt(taken, null);
        if (error instanceof ApiError) throw error;
        // No network, aborted, name resolution failed: status 0 means "again
        // later", not "rejected".
        throw new ApiError(0, { detail: String(error) });
    }
    await rememberedAt(taken, null);
    return data;
}

/**
 * Like 'call', but with an attempt at refreshing the access token.
 *
 * The access token lives an hour, the refresh token two weeks. Whoever
 * opens the app again the next morning should still not have to sign in anew.
 *
 * The attempt after refreshing belongs to the same storage as the first.
 * Until 10/2026 it took the storage of the moment, and a job of the account
 * of before went out with the token of the next one.
 *
 * A request that names the refresh token in its body passes 'options' as a
 * function of it. They are built anew for every attempt: the refresh swaps
 * the token and blocks the old one. Built once, the attempt after it named
 * the blocked one, and the new one stayed valid (until 10/2026).
 */
export async function request(path, options = {}, taken = currentMark()) {
    try {
        return await call(path, await optionsNow(options, taken), true, taken);
    } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 401) throw error;
        if (!(await refresh(taken))) throw error;
        return await call(path, await optionsNow(options, taken), true, taken);
    }
}

/** The options of a request, built with the refresh token the storage of 'taken' holds now. */
async function optionsNow(options, taken) {
    if (typeof options !== "function") return options;
    return options(await rememberedAt(taken, "refresh_token"));
}

export function get(path, taken = currentMark()) {
    return request(path, {}, taken);
}

export function send(path, payload, method = "POST", taken = currentMark()) {
    return request(path, { method, body: JSON.stringify(payload) }, taken);
}

export function remove(path, taken = currentMark()) {
    return request(path, { method: "DELETE" }, taken);
}

/**
 * Sign in with user name and password.
 *
 * If the account changes in the process, the app clears its storage. If
 * something from the previous account still waits in it, it aborts with
 * 'ForeignEntries' instead and leaves everything as it was, its tokens too.
 * Only with 'discard' does it go on nonetheless.
 */
export async function signIn(username, password, { discard = false } = {}) {
    const taken = currentMark();
    const data = await call(
        "/token/",
        {
            method: "POST",
            body: JSON.stringify({ username, password }),
        },
        false,
        taken,
    );
    // Who signed in, asked with the new token in hand and not from the
    // storage: until the switch is decided, the storage holds the previous
    // account's state, and its unsent numbers must not go out under the new
    // name. Until 10/2026 the new pair lay there during this answer, and a
    // sync in another window sent them.
    const me = await call(
        "/ich/",
        { headers: { Authorization: `Bearer ${data.access}` } },
        false,
        taken,
    );
    return await adopt(data, me, discard, taken);
}

/**
 * Sign in with a passkey, without a user name.
 *
 * Two steps: the server's question, the device's signature. The browser
 * does the asking (face, finger, PIN) and throws if the person cancels or
 * the device has no key for this address. The server answers with the
 * token pair and who it is, like the website's sign-in does. The rest is
 * the same as with a password, the question about a switched account too.
 *
 * For Django the call is anonymous. A refusal comes in the app's language
 * all the same: 'call' sends it in the header, as with every request. Until
 * 10/2026 it also stood in the address, and a call without it asked in German.
 */
export async function signInWithPasskey({ discard = false } = {}) {
    const taken = currentMark();
    const step = await call(
        "/passkey/anmelden/optionen/",
        { method: "POST", body: "{}" },
        false,
        taken,
    );
    const credential = await getCredential(step.options);
    const data = await call(
        "/passkey/anmelden/",
        {
            method: "POST",
            body: JSON.stringify({
                credential: credentialToJSON(credential),
                challenge: step.challenge,
            }),
        },
        false,
        taken,
    );
    return await adopt(data, data.ich, discard, taken);
}

/**
 * The new sign-in takes over, unless someone else's numbers still wait.
 *
 * Another account does not see the previous one's state and must certainly
 * not send it on as its own. With unsent things, or a form the app left
 * behind unsaved, it aborts with 'ForeignEntries' and leaves the storage as
 * it was, unless 'discard' says to go on. After a switch the storage is a
 * new one, and so is the mark.
 *
 * Decided before anything of the new sign-in is stored: its pair goes in
 * with 'me', in one step, after the clearing. Stored first, it lay next to
 * the previous account's numbers until the decision (until 10/2026).
 *
 * Whose the storage is, is read in the guarded step that decides, and the
 * pair of the same account goes in with it. Read before the sign-in's
 * requests, a second window could sign in another account meanwhile and
 * count. Until 10/2026 the decision then saw nobody, and the pair went in
 * next to that account's numbers.
 */
async function adopt(data, me, discard, taken) {
    const switching = await guarded(taken, async () => {
        const before = await remembered("me");
        if (!before || before.id === me.id) {
            await storePair(data, me);
            return false;
        }
        const pending = await unsentCount();
        const unsaved = await unsavedCount();
        if ((pending || unsaved) && !discard) throw new ForeignEntries(pending, before, unsaved);
        return true;
    });
    if (switching) {
        await clearAll();
        await guarded(currentMark(), () => storePair(data, me));
    }
    return me;
}

/** The pair of a sign-in and who signed in. Inside a guarded step. */
async function storePair(data, me) {
    await remember("access_token", data.access);
    await remember("refresh_token", data.refresh);
    await remember("me", me);
    await forget("from_session");
}

/** The passkeys of the account, as the server lists them. */
export function passkeys() {
    return get("/passkey/");
}

/**
 * The confirmation with a passkey came to nothing, refused by the device or by the server.
 *
 * What was thrown stands in 'cause'. The forms tell it from a refusal of
 * their own: the device's refusal means here what it means at the sign-in,
 * and so does the server's sentence, which is meant for the person.
 */
export class NotConfirmed extends Error {
    constructor(cause) {
        super("Mit dem Passkey nicht bestätigt");
        this.cause = cause;
    }
}

//: How long the server takes a passkey's confirmation: two minutes, less
//: a little for the request that still has to arrive. Older, the form asks
//: for a new one instead of sending one the server would refuse.
const CONFIRMATION_HOLDS = 110_000;

/**
 * Whoever holds the device shows that the account is theirs, for an account without a password.
 *
 * A token does not say who holds the device, so a new key and a new address
 * want the password. An account without one confirms with one of its
 * passkeys instead, in the two steps of signing in: the server's question,
 * the device's signature. Back comes the server's confirmation ('token'),
 * and until when it holds ('until'). Until 10/2026 such an account was asked
 * nothing. Whoever found the app open could add a key of their own or move
 * the address to a mailbox of their own.
 */
export async function confirmWithPasskey(taken = currentMark()) {
    try {
        const step = await send("/passkey/bestaetigen/optionen/", {}, "POST", taken);
        const credential = await getCredential(step.options);
        const answer = await send(
            "/passkey/bestaetigen/",
            { credential: credentialToJSON(credential), challenge: step.challenge },
            "POST",
            taken,
        );
        return { token: answer.bestaetigung, until: Date.now() + CONFIRMATION_HOLDS };
    } catch (cause) {
        throw new NotConfirmed(cause);
    }
}

/** Whether a confirmation from 'confirmWithPasskey' still holds. */
export function confirmationHolds(confirmation) {
    return Boolean(confirmation) && Date.now() < confirmation.until;
}

/** The password, or for an account without one ('password' undefined) a passkey's confirmation. */
async function proof(password, taken) {
    if (password !== undefined) return { passwort: password };
    return { bestaetigung: (await confirmWithPasskey(taken)).token };
}

/**
 * A new passkey for this device, named by the person or by its provider.
 *
 * Needs network, like the password: the device's half arises here and now,
 * and the server has to learn the public half at once. The account's
 * password goes with the question, before the device's dialog: without the
 * right one the server asks nothing, and the device is not asked either. An
 * account without a password sends a passkey's confirmation instead, from
 * 'confirmWithPasskey' on a tap of its own. On the same tap as the new key,
 * the device would be asked twice, and Safari on the iPhone may refuse the
 * second question.
 */
export async function createPasskey(name, { password, confirmation } = {}) {
    const taken = currentMark();
    const step = await send(
        "/passkey/anlegen/optionen/",
        password === undefined ? { bestaetigung: confirmation?.token } : { passwort: password },
        "POST",
        taken,
    );
    const credential = await createCredential(step.options);
    return await send(
        "/passkey/anlegen/",
        {
            credential: credentialToJSON(credential),
            challenge: step.challenge,
            name,
        },
        "POST",
        taken,
    );
}

/**
 * Removes a key of the account, and this device stays signed in.
 *
 * Against the account's password, as a new key: removing signs the account
 * out everywhere else ('accounts/sign_out.py' on the server), and whoever
 * only has the device in their hand should do neither. An account without
 * a password ('password' undefined) sends a passkey's confirmation instead,
 * from 'confirmWithPasskey' on a tap of its own. Until 10/2026 a signed-in
 * app sufficed.
 *
 * The device names its own refresh token, the one of the moment
 * ('request'), and the server spares it and this session: the device keeps
 * its pair, and the answer is empty. A token the server does not know
 * removes nothing, and its sentence asks to sign in anew. The dialog shows
 * it. Not through 'remove': that sends no body.
 *
 * A server from before 10/2026 ignores the token and the password, signs
 * this device out too and sends it a fresh pair instead, as after a new
 * password. Stored, the device stays signed in there as well.
 */
export async function removePasskey(id, { password, confirmation } = {}) {
    const taken = currentMark();
    const proof =
        password === undefined ? { bestaetigung: confirmation?.token } : { passwort: password };
    const data = await request(
        `/passkey/${id}/`,
        (refreshToken) => ({
            method: "DELETE",
            body: JSON.stringify({ refresh: refreshToken, ...proof }),
        }),
        taken,
    );
    if (data?.access) await storeTokens(data, taken);
    return data;
}

/**
 * The token pair as the server sends it, into the storage of 'taken'.
 *
 * For a pair that comes with another answer of the same account. A sign-in
 * stores its pair together with who signed in ('adopt').
 */
async function storeTokens(data, taken) {
    await guarded(taken, async () => {
        await remember("access_token", data.access);
        await remember("refresh_token", data.refresh);
    });
}

/** Both tokens out of the storage: the sign-in is over. Inside a guarded step. */
async function dropTokens() {
    await forget("access_token");
    await forget("refresh_token");
}

//: The running refresh and the storage it belongs to. Two requests that get
//: a 401 at once share one attempt: the server swaps the refresh token every
//: time, the second would come with the one just blocked and throw away the
//: fresh pair. A request of another storage starts its own.
let refreshing = null;

function refresh(taken) {
    if (refreshing?.taken !== taken) {
        const running = { taken, done: null };
        running.done = refreshOnce(taken).finally(() => {
            if (refreshing === running) refreshing = null;
        });
        refreshing = running;
    }
    return refreshing.done;
}

/**
 * 'true': there is a new access token. 'false': the server refused the
 * token (401), and the tokens are gone. The app then leads to the login form.
 *
 * Only that refusal ends the sign-in. No network, a server that fails, a
 * proxy that restarts (502, 503), too many requests (429) or a request the
 * server did not take as sent (400) are no verdict on the token. It stays,
 * and the error counts as "again later" ('isTemporary'). Until 10/2026 every
 * answer but a success dropped the tokens, a 500 too, after a phone had once
 * stayed "offline" behind an error page of the refresh. The server answers
 * that case with 401 since ('TokenErneuernView'). Dropped on a passing error,
 * the requests after it went out without a token, and the website's session
 * answered them.
 *
 * The new pair is written only into the storage the refresh belongs to.
 * Until 10/2026 a refresh in a second window that answered after a sign-out
 * here wrote the previous account's pair back, and the next start was
 * signed in as that account.
 */
async function refreshOnce(taken) {
    const token = await rememberedAt(taken, "refresh_token");
    if (!token) return false;
    try {
        const data = await call(
            "/token/erneuern/",
            { method: "POST", body: JSON.stringify({ refresh: token }) },
            false,
            taken,
        );
        await guarded(taken, async () => {
            await remember("access_token", data.access);
            // The server swaps the refresh token every time and puts the old
            // one on the blocklist. So the new one has to come along.
            if (data.refresh) await remember("refresh_token", data.refresh);
        });
        return true;
    } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 401) throw againLater(error);
        // Fresh tokens have arrived in the meantime, from a new password or
        // a new sign-in. The rejection was meant for the old one, and
        // clearing would throw away the new ones.
        return await guarded(taken, async () => {
            const current = await remembered("refresh_token");
            if (current && current !== token) return true;
            await dropTokens();
            return false;
        });
    }
}

/** What a failed renewal says to the request that wanted it: always "again later". */
function againLater(error) {
    if (!(error instanceof ApiError) || error.isTemporary) return error;
    return new NotRenewed(error.status, error.data);
}

export async function isSignedIn() {
    return Boolean(await remembered("refresh_token"));
}

/**
 * Change the password and stay signed in on this device.
 *
 * A new password ends every sign-in made with the old one, the one of this
 * device too. The server therefore sends a fresh token pair along, and what
 * waits in the queue goes out with it.
 *
 * Never queued for later: a password does not belong in the storage, and
 * whoever changes it wants to know at once whether it worked.
 */
export async function changePassword(oldPassword, newPassword) {
    const taken = currentMark();
    const data = await send(
        "/passwort/",
        {
            altes_passwort: oldPassword,
            neues_passwort: newPassword,
        },
        "POST",
        taken,
    );
    await storeTokens(data, taken);
}

/**
 * Who is signed in, fresh from the server, and remembered for the storage it came for.
 *
 * Remembered after a sign-out, the next start named the account of before.
 */
export async function fetchMe() {
    const taken = currentMark();
    const fresh = await get("/ich/", taken);
    await guarded(taken, () => remember("me", fresh));
    return fresh;
}

/**
 * A new e-mail address, given against the password.
 *
 * An account without a password ('password' undefined) confirms with a
 * passkey before the address goes out ('confirmWithPasskey'). With a mail
 * server the address applies only once the person confirmed it through the
 * link in the mail. The answer says which of the two happened
 * ('bestaetigung'). Never queued: whoever changes it wants to know at once.
 */
export async function changeEmail(email, password) {
    const taken = currentMark();
    return await send("/email/", { email, ...(await proof(password, taken)) }, "POST", taken);
}

/**
 * The CSRF value from the cookie Django set on the website.
 *
 * Of two cookies of that name Django checks the last one, and so do the
 * website's scripts ('csrfMarke' in 'static/js/shared.js'). A second one can
 * come from a site on a parent domain. Until 10/2026 the app sent the first,
 * the server refused the takeover, and an app with its sign-in from the
 * website signed itself out.
 */
function csrfValue() {
    const found = [...document.cookie.matchAll(/(?:^|;\s*)csrftoken=([^;]*)/g)].at(-1);
    return found ? decodeURIComponent(found[1]) : "";
}

/**
 * Take over the website's sign-in if there is one.
 *
 * Otherwise whoever has the web interface open and opens the app next to it
 * types the same password. Runs on every start. In the process a **switch**
 * also stands out: if someone else signs in on the website, the local state
 * no longer belongs to the account that sees it and has to go.
 *
 * Returns who is signed in now, or null if there is no session, or if a
 * sign-out owed to it ended it just now or still waits. No network changes
 * nothing: whoever has a token keeps counting offline.
 *
 * If unsent things still wait here, it stays with the previous account: the
 * switch would otherwise delete numbers without anyone being asked. They go
 * out under the account that counted them. Whoever wants to switch signs out
 * in the app and is asked there.
 *
 * A form the app left behind unsaved keeps nobody signed in. It holds no
 * number yet, no sync ever empties it, and it goes with the storage it
 * belongs to. Until 10/2026 it kept the previous account signed in, the
 * website's sign-out too: on a shared tablet the next person worked as that
 * account, saw its inventories and counted under its name.
 */
export async function fromSession() {
    const csrf = csrfValue();
    const taken = currentMark();
    // A sign-out here that never reached the server left the website's
    // session behind ('signOut'), and its refresh token valid. Taken over,
    // the session signed the same account in again without anyone asking,
    // on a shared phone as the next person's (until 10/2026). So the token
    // goes and the session ends first, and this start takes nothing over.
    // Whoever signed in on the website since keeps their session: Django
    // swaps the CSRF value with every sign-in. Until 10/2026 it ended too.
    const owed = owedSignOut(await remembered(SIGN_OUT_OWED));
    if (owed) {
        const signedInSince = Boolean(csrf) && owed.csrf !== null && owed.csrf !== csrf;
        if (!(await settleOwed(taken, owed, csrf, signedInSince))) return null;
        if (!signedInSince) return null;
    }
    // Without the CSRF cookie there is no session either, because Django
    // sets both together. Then do not even ask, the answer would be 403 anyway.
    if (!csrf) return null;
    const before = await remembered("me");
    let data;
    try {
        data = await call(
            "/token/aus-sitzung/",
            { method: "POST", headers: { "X-CSRFToken": csrf }, body: "{}" },
            false,
            taken,
        );
    } catch (error) {
        // 401/403 means: no session. If the app fetched its token from there,
        // signing out on the website is one here too. A self-signed-in token,
        // by contrast, stays. Likewise as long as something still waits here:
        // signing out would clear the storage, and the numbers would be gone.
        // They go out as long as the token still applies, after that after the
        // next sign-in. On this path the website's session is not touched:
        // the one there by now may be someone else's ('signOut' without it).
        if (error.status === 401 || error.status === 403) {
            const fromThere = before && (await remembered("from_session"));
            if (fromThere && (await unsentCount()) === 0) await signOut(false);
        }
        return null;
    }
    let now = taken;
    if (before && before.id !== data.ich.id) {
        if ((await unsentCount()) > 0) return null;
        await clearAll();
        now = currentMark();
    }
    await guarded(now, async () => {
        await remember("access_token", data.access);
        await remember("refresh_token", data.refresh);
        await remember("me", data.ich);
        await remember("from_session", true);
    });
    return data.ich;
}

/**
 * Sign out: here at once, and where possible on the website too.
 *
 * The storage goes first, before the server is asked, and 'cleared' runs
 * then: the interface lets go of the account too. Whoever takes the device
 * right after the tap finds nothing of it, however long a poor connection
 * keeps the request on its way. Until 10/2026 the interface and the storage
 * held the account until the server had answered.
 *
 * If the session remained, the app would fetch a token again at once on
 * the next start via 'fromSession'. The sign-out would have no effect. So
 * it stays owed until the server hears it, noted in the step that clears
 * the storage ('SIGN_OUT_OWED'), and the next start settles a note still
 * there first.
 *
 * 'withSession' false follows a sign-out on the website ('fromSession'):
 * that session is gone, and the one there by now may be someone else's.
 * It is not touched. Only the token goes, by its id alone ('blockToken').
 * Until 10/2026 it was dropped without a word and held until it ran out.
 */
export async function signOut(withSession = true, cleared = () => {}) {
    // What the server needs, read in the step that clears: no request of
    // another account comes in between.
    const pair = {};
    let owed = false;
    await clearAll({
        signOutOwed: async () => {
            pair.access = await remembered("access_token");
            pair.refresh = await remembered("refresh_token");
            owed = owedNote(pair.refresh, withSession);
            return owed;
        },
    });
    cleared();
    if (!owed) return;
    const taken = currentMark();
    const heard = withSession
        ? await signOutThere({ ...pair, owed }, taken)
        : await blockToken(taken, owed.jti);
    if (heard) await settleSignOut(taken);
}

/**
 * What the next start needs to settle a sign-out the server did not hear ('fromSession').
 *
 * Not the refresh token: on a shared phone it would stay behind as a
 * sign-in. Its id ('jti') names it to the server, which blocks it. The CSRF
 * value tells whether someone signed in on the website since: a session
 * ends only while the value is still the noted one. Empty, there was no
 * session, or none this sign-out may touch ('withSession'), and no start
 * ends one for it. 'false' where nothing is owed: without the session there
 * is only the token to name.
 */
function owedNote(refreshToken, withSession) {
    const jti = tokenId(refreshToken);
    if (!withSession) return jti ? { jti, csrf: "" } : false;
    return { jti, csrf: csrfValue() };
}

/** The owed sign-out as noted, or null. One from before 10/2026 is 'true': it ends the session. */
function owedSignOut(noted) {
    if (!noted) return null;
    return { jti: noted.jti ?? null, csrf: noted.csrf ?? null };
}

/** The id in a refresh token's payload, unchecked: the server checks. Null for anything else. */
function tokenId(token) {
    try {
        const payload = JSON.parse(new TextDecoder().decode(fromBase64url(token.split(".")[1])));
        return typeof payload?.jti === "string" ? payload.jti : null;
    } catch {
        return null;
    }
}

/**
 * The server blocks the refresh token and ends the website's session. 'true' once it has.
 *
 * With the pair read before the storage went ('signOut'), held only here.
 * Without network the token stays valid until the next start settles it.
 * Locally it is gone nonetheless, because nobody here should see the
 * other's numbers.
 *
 * An access token that expired is renewed for this one request, and the
 * fresh pair is never stored. The renewal blocks the refresh token the note
 * names, so the fresh one is owed from then on. A sign-in the server no
 * longer knows ends nothing with its token, and the server refuses the call
 * before it could end the session. Until 10/2026 the session survived that
 * too. Once more without the token: then the session alone signs out.
 */
async function signOutThere({ access, refresh, owed }, taken) {
    if (access) {
        const told = await tellSignOut(access, refresh, taken);
        if (told !== EXPIRED) return told;
    }
    if (refresh) {
        let fresh;
        try {
            const body = JSON.stringify({ refresh });
            fresh = await call("/token/erneuern/", { method: "POST", body }, false, taken);
        } catch (error) {
            // Refused: the token is dead already. Anything else stays owed.
            if (!(error instanceof ApiError) || error.status !== 401) return false;
            fresh = null;
        }
        if (fresh) {
            const renewed = fresh.refresh ?? refresh;
            await noteOwed(taken, { ...owed, jti: tokenId(renewed) });
            const told = await tellSignOut(fresh.access, renewed, taken);
            if (told !== EXPIRED) return told;
        }
    }
    return await endSession(taken);
}

//: What the server says to a sign-out with a token it no longer takes (401).
const EXPIRED = "expired";

/** The sign-out with this pair: 'true' once heard, 'EXPIRED' on a 401, 'false' otherwise. */
async function tellSignOut(access, refresh, taken) {
    try {
        await call(
            "/abmelden/",
            {
                method: "POST",
                headers: { Authorization: `Bearer ${access}`, "X-CSRFToken": csrfValue() },
                body: JSON.stringify({ refresh }),
            },
            false,
            taken,
        );
        return true;
    } catch (error) {
        return error instanceof ApiError && error.isSignedOut ? EXPIRED : false;
    }
}

/** Another token is owed from now on ('signOutThere'). */
async function noteOwed(taken, note) {
    await quietly(taken, () => remember(SIGN_OUT_OWED, note));
}

/**
 * Settles a sign-out the server did not hear ('signOut'). 'true' once nothing is owed any more.
 *
 * The token first, by its id at its own door ('blockToken'): the server
 * blocks it whoever's session there is, or none. Until 10/2026 the id went
 * through the session, and the server blocked it only for the session's
 * account. After someone else had signed in on the website, the token
 * stayed valid. Then the session ends, unless someone signed in since: then
 * it is theirs. Settled only once both are done. Without network, or with a
 * server that fails, it waits for the next start. A note from before
 * 10/2026 names no token, and without a cookie it waits for a session, as
 * it did.
 */
async function settleOwed(taken, owed, csrf, signedInSince) {
    if (owed.jti && !(await blockToken(taken, owed.jti))) return false;
    if (csrf) {
        if (!signedInSince && !(await endSession(taken))) return false;
    } else if (!owed.jti) {
        return false;
    }
    await settleSignOut(taken);
    return true;
}

/**
 * Ends the website's session in this browser, by the session alone and without a token.
 *
 * 'true' once nothing is left to end: the server ended it, or 401 says
 * there is no session, 403 that this browser cannot use it (its CSRF value
 * does not fit). Then 'fromSession' could not take it over either. 'false'
 * without network, or with a server that fails: it stays owed.
 */
async function endSession(taken) {
    try {
        await call(
            "/abmelden/",
            { method: "POST", headers: { "X-CSRFToken": csrfValue() }, body: "{}" },
            false,
            taken,
        );
        return true;
    } catch (error) {
        return error instanceof ApiError && (error.status === 401 || error.status === 403);
    }
}

/**
 * Names a refresh token the app no longer holds to the server, by its id alone.
 *
 * For every owed sign-out ('settleOwed') and for one that follows the
 * website's ('signOut'): the server takes the id without any sign-in and
 * blocks the token, whoever's session there is. The browser's cookies stay
 * home ('omit'): a session would only ask for its CSRF value.
 * 'true' once the server took it (204), or refused it for good (400: the
 * id is malformed and never will be good). Without network, or with a
 * server that fails, it stays owed.
 */
async function blockToken(taken, jti) {
    try {
        await call(
            "/abmelden/nachgeholt/",
            { method: "POST", credentials: "omit", body: JSON.stringify({ jti }) },
            false,
            taken,
        );
        return true;
    } catch (error) {
        return error instanceof ApiError && error.status === 400;
    }
}

/** Nothing owed any more. */
async function settleSignOut(taken) {
    await quietly(taken, () => forget(SIGN_OUT_OWED));
}

/** A guarded step that keeps quiet where another window cleared meanwhile: it has decided. */
async function quietly(taken, change) {
    try {
        await guarded(taken, change);
    } catch (error) {
        if (!(error instanceof SignedOutMeanwhile)) throw error;
    }
}
