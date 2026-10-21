<script>
    /**
     * A new password, without the way through the web interface.
     *
     * The counters never get to see the website, and the app has no session
     * there. Until 09/2026 a counter could only have a new password set by a
     * manager.
     *
     * Needs network: a password is never queued. The new one is typed twice,
     * because nobody sees what they type, and compared here. The server gets
     * it once and checks it against the same rules as the website. Its
     * messages stand under the field they belong to, in the account's
     * language.
     *
     * A wrong old password counts like a failed sign-in. After the fifth the
     * server locks for a while, here as at the login form.
     */
    import Dialog from "./Dialog.svelte";
    import { changePassword } from "../services/api.js";
    import { counting } from "../services/counting.svelte.js";
    import { formErrors } from "../services/form_errors.js";
    import { t } from "../services/language.svelte.js";

    let { username, close } = $props();

    let oldPassword = $state("");
    let newPassword = $state("");
    let repeated = $state("");
    let running = $state(false);
    let changed = $state(false);
    //: What the server says about a field, as a list of sentences.
    let oldErrors = $state([]);
    let newErrors = $state([]);
    let error = $state("");

    async function submit(event) {
        event.preventDefault();
        oldErrors = [];
        newErrors = [];
        error = "";
        if (newPassword !== repeated) {
            error = t("password.mismatch");
            return;
        }
        running = true;
        try {
            await changePassword(oldPassword, newPassword);
            changed = true;
        } catch (cause) {
            explain(cause);
        } finally {
            running = false;
        }
    }

    function explain(cause) {
        const found = formErrors(cause, {
            fields: ["altes_passwort", "neues_passwort"],
            lines: { 0: t("password.no_connection"), 429: t("error.locked") },
            otherwise: t("password.failed"),
        });
        if (found.signedOut) {
            // The sign-in no longer applies. The login form is the way on,
            // and after it this dialog should not stand there again.
            counting.signedOut = true;
            close();
            return;
        }
        oldErrors = found.errors.altes_passwort;
        newErrors = found.errors.neues_passwort;
        error = found.line;
    }
</script>

<Dialog
    label={t("password.title")}
    closeText={changed ? t("password.close") : t("password.cancel")}
    {close}
>
    {#if changed}
        <p class="done" role="status">{t("password.changed")}</p>
    {:else}
        <form onsubmit={submit}>
            <p class="small">{t("password.hint")}</p>
            <!-- Invisible, for the password manager: without the name it does
                 not know whose entry it should replace. -->
            <input type="text" autocomplete="username" value={username ?? ""} readonly hidden />
            <!-- The messages stand next to the label, not in it: otherwise
                 they would become part of the field's name. -->
            <div class="field">
                <label>
                    {t("password.old")}
                    <input
                        type="password"
                        bind:value={oldPassword}
                        autocomplete="current-password"
                        aria-invalid={oldErrors.length > 0}
                        aria-describedby="password-old-errors"
                        required
                    />
                </label>
                <div id="password-old-errors" role="alert">
                    {#each oldErrors as sentence, index (index)}
                        <p class="error">{sentence}</p>
                    {/each}
                </div>
            </div>
            <div class="field">
                <label>
                    {t("password.new")}
                    <input
                        type="password"
                        bind:value={newPassword}
                        autocomplete="new-password"
                        aria-invalid={newErrors.length > 0}
                        aria-describedby="password-new-errors"
                        required
                    />
                </label>
                <div id="password-new-errors" role="alert">
                    {#each newErrors as sentence, index (index)}
                        <p class="error">{sentence}</p>
                    {/each}
                </div>
            </div>
            <label>
                {t("password.repeat")}
                <input type="password" bind:value={repeated} autocomplete="new-password" required />
            </label>
            {#if error}<p class="error" role="alert">{error}</p>{/if}
            <button class="green" type="submit" disabled={running}>
                {running ? t("password.running") : t("password.submit")}
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
        color: var(--muted);
    }

    .small {
        margin: 0;
        font-size: 0.8rem;
        color: var(--muted);
    }

    .error {
        margin: 0;
        font-size: 0.9rem;
        color: var(--open);
    }

    .done {
        margin: 0;
        padding: 1.5rem 1rem;
    }

    .green {
        min-height: 3rem;
        font-weight: 600;
        color: #fff;
        background: var(--done);
        border-color: var(--done);
    }
</style>
