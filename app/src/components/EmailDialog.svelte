<script>
    /**
     * A new e-mail address, without the way through the web interface.
     *
     * The counters never get to see the website, and the app has no session
     * there. The form is the one of the account menu on the website: the new
     * address and the password, the password so that nobody changes the
     * address who only has the device in their hand.
     *
     * Needs network: an address is never queued. With a mail server the
     * address applies only once the person confirmed it through the link in
     * the mail. The server says which of the two happened, and the dialog
     * says it on. Its messages stand under the field they belong to, in the
     * account's language.
     *
     * A wrong password counts like a failed sign-in. After the fifth the
     * server locks for a while, here as at the login form. An account
     * without a password confirms with one of its passkeys instead, before
     * the address goes out ('changeEmail').
     */
    import Dialog from "./Dialog.svelte";
    import { NotConfirmed, changeEmail } from "../services/api.js";
    import { counting } from "../services/counting.svelte.js";
    import { formErrors } from "../services/form_errors.js";
    import { t } from "../services/language.svelte.js";
    import { wasCancelled } from "../services/passkey.js";

    let {
        username,
        email,
        //: Whether the account signs in with a password. Without one a passkey confirms.
        hasPassword = true,
        //: Whether a mail server exists. Without one the address applies at once.
        confirms = true,
        changed,
        close,
    } = $props();

    //: What the server wants where the device thought otherwise ('formErrors'):
    //: true the password, false a passkey's confirmation. Null until it says.
    let passwordWanted = $state(null);
    let asksPassword = $derived(passwordWanted ?? hasPassword);
    let address = $state("");
    let password = $state("");
    let running = $state(false);
    //: The server's answer once it accepted: the address and whether it still waits.
    let done = $state(null);
    //: What the server says about a field, as a list of sentences.
    let addressErrors = $state([]);
    let passwordErrors = $state([]);
    let error = $state("");

    async function submit(event) {
        event.preventDefault();
        addressErrors = [];
        passwordErrors = [];
        error = "";
        running = true;
        try {
            const answer = await changeEmail(address.trim(), asksPassword ? password : undefined);
            done = { email: answer?.email ?? address.trim(), confirms: answer?.bestaetigung };
            if (!done.confirms) changed?.();
        } catch (cause) {
            explain(cause);
        } finally {
            running = false;
        }
    }

    function explain(caught) {
        // What the confirmation with a passkey met stands inside. Only its
        // refusals are sentences meant for the person, as at the sign-in.
        const confirming = caught instanceof NotConfirmed;
        const cause = confirming ? caught.cause : caught;
        const found = formErrors(cause, {
            fields: ["email", "passwort", "bestaetigung"],
            shown: ["email", asksPassword ? "passwort" : "bestaetigung"],
            lines: {
                0: t("email.no_connection"),
                429: t("error.locked"),
                503: t("email.not_sent"),
            },
            otherwise: (confirming && cause?.data?.detail) || t("email.failed"),
        });
        if (found.signedOut) {
            // The sign-in no longer applies. The login form is the way on,
            // and after it this dialog should not stand there again.
            counting.signedOut = true;
            close();
            return;
        }
        if (found.reveal.includes("passwort")) passwordWanted = true;
        if (found.reveal.includes("bestaetigung")) passwordWanted = false;
        addressErrors = found.errors.email;
        passwordErrors = found.errors.passwort;
        // The device's refusal carries no status: it is named as the sign-in
        // names it. The confirmation has no field, so what the server says
        // about it stands in the line.
        if (wasCancelled(cause) && confirming) error = t("confirm.cancelled");
        else error = found.errors.bestaetigung.join(" ") || found.line;
    }
</script>

<Dialog label={t("email.title")} closeText={done ? t("email.close") : t("email.cancel")} {close}>
    {#if done}
        <p class="done" role="status">
            {done.confirms
                ? t("email.sent", { email: done.email })
                : t("email.changed", { email: done.email })}
        </p>
    {:else}
        <form onsubmit={submit}>
            <p class="small">
                {#if email}{t("email.current", { email })}{/if}
                {confirms ? t("email.hint_confirm") : t("email.hint_direct")}
            </p>
            <!-- Invisible, for the password manager: without the name it does
                 not know whose entry the password belongs to. -->
            <input type="text" autocomplete="username" value={username ?? ""} readonly hidden />
            <!-- The messages stand next to the label, not in it: otherwise
                 they would become part of the field's name. -->
            <div class="field">
                <label>
                    {t("email.new")}
                    <input
                        type="email"
                        bind:value={address}
                        autocomplete="email"
                        aria-invalid={addressErrors.length > 0}
                        aria-describedby="email-new-errors"
                        required
                    />
                </label>
                <div id="email-new-errors" role="alert">
                    {#each addressErrors as sentence, index (index)}
                        <p class="error">{sentence}</p>
                    {/each}
                </div>
            </div>
            {#if asksPassword}
                <div class="field">
                    <label>
                        {t("email.password")}
                        <input
                            type="password"
                            bind:value={password}
                            autocomplete="current-password"
                            aria-invalid={passwordErrors.length > 0}
                            aria-describedby="email-password-errors"
                            required
                        />
                    </label>
                    <div id="email-password-errors" role="alert">
                        {#each passwordErrors as sentence, index (index)}
                            <p class="error">{sentence}</p>
                        {/each}
                    </div>
                </div>
            {:else}
                <p class="small">{t("email.confirm_hint")}</p>
            {/if}
            {#if error}<p class="error" role="alert">{error}</p>{/if}
            <button class="green" type="submit" disabled={running}>
                {running
                    ? t("email.running")
                    : confirms
                      ? t("email.submit_confirm")
                      : t("email.submit_direct")}
            </button>
        </form>
    {/if}
</Dialog>

<style>
    /* Scrolls by itself: with the keyboard open little of the height remains. */
    form {
        flex: 1;
        display: flex;
        flex-direction: column;
        gap: 1rem;
        width: 100%;
        max-width: 24rem;
        margin: 0 auto;
        padding: 1rem 1rem calc(1rem + env(safe-area-inset-bottom));
        overflow-y: auto;
    }

    .field,
    label {
        display: flex;
        flex-direction: column;
        gap: 0.25rem;
    }

    label {
        font-size: 0.9rem;
    }

    input {
        min-height: 2.75rem;
        padding: 0.5rem 0.75rem;
        font-size: 1.05rem;
    }

    .small {
        margin: 0;
        font-size: 0.9rem;
        color: var(--muted);
    }

    .error {
        margin: 0;
        font-size: 0.9rem;
        color: var(--open);
    }

    .done {
        max-width: 24rem;
        margin: 0 auto;
        padding: 1.5rem 1rem;
    }

    .green {
        min-height: 2.75rem;
        font-weight: 600;
        color: #fff;
        background: var(--done);
        border-color: var(--done);
    }
</style>
