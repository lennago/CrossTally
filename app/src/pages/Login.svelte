<script>
    import { ForeignEntries, signIn, signInWithPasskey } from "../services/api.js";
    import { t } from "../services/language.svelte.js";
    import { passkeysAvailable, wasCancelled } from "../services/passkey.js";

    let { done, pending = 0 } = $props();
    let username = $state("");
    let password = $state("");
    let error = $state("");
    let running = $state(false);
    //: Unsent things of another account. Then a question comes first.
    let foreign = $state(null);
    //: Which way the question about the switch belongs to: password or passkey.
    let withPasskey = $state(false);
    //: Only where the browser can: the button stays away otherwise.
    const passkey = passkeysAvailable();

    async function submit(event) {
        event.preventDefault();
        withPasskey = false;
        await attempt(false);
    }

    async function attempt(discard) {
        error = "";
        running = true;
        try {
            const who = withPasskey
                ? await signInWithPasskey({ discard })
                : await signIn(username, password, { discard });
            done(who);
            foreign = null;
        } catch (cause) {
            if (cause instanceof ForeignEntries) {
                foreign = cause;
                return;
            }
            error = explain(cause);
        } finally {
            running = false;
        }
    }

    async function withKey() {
        withPasskey = true;
        await attempt(false);
    }

    // A wrong password and a missing network feel the same but are
    // different problems, so keep them apart. The browser's refusal of a
    // passkey is a third, and what the server says about one comes as a
    // sentence of its own. The lock after too many attempts (429) and a
    // server that does not answer (5xx) are not a wrong password either:
    // until 10/2026 both read as one, and the right password looked wrong
    // for half an hour.
    function explain(cause) {
        if (wasCancelled(cause)) return t("login.passkey_cancelled");
        if (cause.status === 0) return t("login.no_connection");
        if (cause.status === 429) return t("error.locked");
        if (cause.status >= 500) return t("login.server_error");
        if (withPasskey) return cause.data?.detail || t("login.passkey_failed");
        return t("login.wrong_credentials");
    }

    function nameOf(who) {
        return who?.vorname || who?.benutzername || t("login.previous_account");
    }
</script>

<form onsubmit={submit}>
    <h1>{t("login.title")}</h1>
    {#if pending}
        <!-- The sign-in has expired, but something still lies here: that should
             hurry nobody, only reassure. -->
        <p class="hint">{t("login.expired", { n: pending })}</p>
    {/if}
    <label>
        {t("login.username")}
        <input bind:value={username} autocapitalize="none" autocomplete="username" required />
    </label>
    <label>
        {t("login.password")}
        <input type="password" bind:value={password} autocomplete="current-password" required />
    </label>
    {#if error}<p class="error">{error}</p>{/if}
    {#if foreign}
        <!-- A switch would clear the storage. What still waits there someone
             else counted, and it would be gone without a question. -->
        <div class="foreign">
            {#if foreign.count}
                <p>{t("login.foreign", { n: foreign.count, who: nameOf(foreign.who) })}</p>
            {/if}
            {#if foreign.unsaved}
                <p>
                    {t("login.foreign_unsaved", { n: foreign.unsaved, who: nameOf(foreign.who) })}
                </p>
            {/if}
            <button type="button" class="discard" disabled={running} onclick={() => attempt(true)}>
                {t("login.discard", { who: withPasskey ? t("login.passkey_account") : username })}
            </button>
        </div>
    {/if}
    <button type="submit" disabled={running}>
        {running ? t("login.running") : t("login.button")}
    </button>
    {#if passkey}
        <!-- Without a user name: the key on the device knows its account. -->
        <p class="or">{t("login.or")}</p>
        <button type="button" class="passkey" disabled={running} onclick={withKey}>
            {t("login.passkey")}
        </button>
    {/if}
</form>

<style>
    form {
        display: flex;
        flex-direction: column;
        gap: 1rem;
        padding: 2rem 1rem;
        max-width: 24rem;
        margin: 0 auto;
    }

    label {
        display: flex;
        flex-direction: column;
        gap: 0.25rem;
        font-size: 0.9rem;
        color: var(--muted);
    }

    .error {
        color: var(--open);
        margin: 0;
    }

    .foreign {
        display: flex;
        flex-direction: column;
        gap: 0.5rem;
        padding: 0.6rem 0.8rem;
        border-radius: 0.5rem;
        background: var(--notice);
        font-size: 0.9rem;
    }

    .foreign p {
        margin: 0;
    }

    .discard {
        color: var(--open);
        border-color: var(--open);
    }

    .hint {
        margin: 0;
        padding: 0.6rem 0.8rem;
        border-radius: 0.5rem;
        background: var(--notice);
        font-size: 0.9rem;
    }

    .or {
        margin: -0.5rem 0;
        text-align: center;
        font-size: 0.85rem;
        color: var(--muted);
    }

    .passkey {
        min-height: 2.75rem;
    }
</style>
