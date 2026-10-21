<script>
    /**
     * The passkeys of the account, from the app.
     *
     * The counters never get to see the website, and on the phone the
     * installed app has a storage of its own: a key made in the browser
     * does not reach it. So the key for the phone arises here, in the app
     * that will use it. The list shows what the account has, and a lost
     * device loses its key here.
     *
     * A new key asks for the account's password first, as a new address
     * does: whoever only has the device in their hand adds none. It goes
     * with the server's question, before the device's dialog, and its
     * message stands under its field. A wrong one counts like a failed
     * sign-in. An account without a password confirms with a passkey it
     * already has instead, on a tap of its own: one tap, one question of
     * the device. Safari on the iPhone, where the counters use the
     * installed app, may refuse a second question on the same tap. The
     * confirmation then holds for the new key, for two minutes.
     *
     * Removing a key asks the same, in the key's own box: it signs the
     * account out everywhere else, and whoever only has the device in their
     * hand should not do that either. Until 10/2026 a second tap sufficed.
     * The box has its own field and its own confirmation, so the form for
     * a new key keeps what was typed there.
     *
     * Needs network, like the password: nothing of this is queued.
     */
    import { onMount, tick } from "svelte";

    import Dialog from "./Dialog.svelte";
    import {
        NotConfirmed,
        confirmWithPasskey,
        confirmationHolds,
        createPasskey,
        passkeys,
        removePasskey,
    } from "../services/api.js";
    import { counting } from "../services/counting.svelte.js";
    import { formErrors } from "../services/form_errors.js";
    import { locale, t } from "../services/language.svelte.js";
    import { alreadyExists, wasCancelled } from "../services/passkey.js";

    let {
        username,
        //: Whether the account signs in with a password. Without one a passkey confirms.
        hasPassword = true,
        close,
    } = $props();

    //: What the server wants where the device thought otherwise ('formErrors'):
    //: true the password, false a passkey's confirmation. Null until it says.
    let passwordWanted = $state(null);
    let asksPassword = $derived(passwordWanted ?? hasPassword);
    let keys = $state(null);
    let name = $state("");
    let password = $state("");
    //: The step on its way: "confirm" or "create", or none.
    let step = $state("");
    //: The server's confirmation for an account without a password, until a new key takes it.
    let confirmed = $state(null);
    let confirmButton = $state(null);
    let createButton = $state(null);
    let created = $state("");
    let error = $state("");
    //: What the server says about the password, as a list of sentences.
    let passwordErrors = $state([]);
    //: Which key's box is open: it asks before the key goes.
    let confirming = $state(null);
    //: The removal's password, what the server says about it, and the box's line.
    let removalPassword = $state("");
    let removalErrors = $state([]);
    let removalError = $state("");
    //: The server's confirmation for the removal, for an account without a password.
    let removalConfirmed = $state(null);
    //: The removal's step on its way: "confirm" or "remove", or none.
    let removalStep = $state("");
    let removalConfirmButton = $state(null);
    let removeButton = $state(null);

    onMount(() => void load());

    async function load() {
        try {
            keys = await passkeys();
        } catch (cause) {
            explain(cause);
            keys = [];
        }
    }

    async function confirm() {
        error = "";
        created = "";
        step = "confirm";
        try {
            confirmed = await confirmWithPasskey();
        } catch (cause) {
            explain(cause);
        } finally {
            step = "";
        }
        await onwards();
    }

    async function create() {
        error = "";
        created = "";
        passwordErrors = [];
        if (!asksPassword && !confirmationHolds(confirmed)) {
            // Older than the server takes it: once more, before anything goes out.
            if (confirmed) error = t("passkey.confirmation_expired");
            confirmed = null;
            await onwards();
            return;
        }
        step = "create";
        try {
            const proof = asksPassword ? { password } : { confirmation: confirmed };
            const key = await createPasskey(name.trim(), proof);
            created = key.name;
            name = "";
            password = "";
            // One confirmation, one key.
            confirmed = null;
            await load();
        } catch (cause) {
            explain(cause);
        } finally {
            step = "";
        }
        await onwards();
    }

    /**
     * The focus to the button of the next step, for an account without a password.
     *
     * The button of a step that is done goes, and the focus with it. The
     * dialog's keys hang on its element: Escape and Tab would not reach it.
     */
    async function onwards() {
        await tick();
        if (!asksPassword) (confirmed ? createButton : confirmButton)?.focus();
    }

    /** The box of this key, empty: what was typed or confirmed for another one goes. */
    function ask(key) {
        confirming = key.id;
        removalPassword = "";
        removalErrors = [];
        removalError = "";
        removalConfirmed = null;
    }

    async function confirmRemoval() {
        removalError = "";
        removalStep = "confirm";
        try {
            removalConfirmed = await confirmWithPasskey();
        } catch (cause) {
            explain(cause, true);
        } finally {
            removalStep = "";
        }
        await onwardsInBox();
    }

    async function remove(key) {
        error = "";
        created = "";
        removalError = "";
        removalErrors = [];
        if (!asksPassword && !confirmationHolds(removalConfirmed)) {
            // Older than the server takes it: once more, before anything goes out.
            if (removalConfirmed) removalError = t("passkey.confirmation_expired");
            removalConfirmed = null;
            await onwardsInBox();
            return;
        }
        removalStep = "remove";
        try {
            const proof = asksPassword
                ? { password: removalPassword }
                : { confirmation: removalConfirmed };
            await removePasskey(key.id, proof);
            confirming = null;
            await load();
        } catch (cause) {
            // Gone already, removed elsewhere: what was wanted holds.
            if (cause?.status === 404) {
                confirming = null;
                await load();
            } else explain(cause, true);
        } finally {
            removalStep = "";
        }
    }

    /** The focus to the box's next step, for an account without a password ('onwards'). */
    async function onwardsInBox() {
        await tick();
        if (!asksPassword) (removalConfirmed ? removeButton : removalConfirmButton)?.focus();
    }

    /** What went wrong, sorted for its form: the new key's, or the removal's box ('removal'). */
    function explain(caught, removal = false) {
        // What the confirmation with a passkey met stands inside.
        const fromConfirmation = caught instanceof NotConfirmed;
        const cause = fromConfirmation ? caught.cause : caught;
        const found = formErrors(cause, {
            fields: ["passwort", "bestaetigung"],
            shown: [asksPassword ? "passwort" : "bestaetigung"],
            lines: { 0: t("passkey.no_connection"), 429: t("error.locked") },
            otherwise: cause?.data?.detail || t("passkey.failed"),
        });
        if (found.signedOut) {
            counting.signedOut = true;
            close();
            return;
        }
        if (found.reveal.includes("passwort")) passwordWanted = true;
        if (found.reveal.includes("bestaetigung")) passwordWanted = false;
        // Refused by the server, the confirmation is asked for anew.
        const refused = found.errors.bestaetigung.length > 0;
        // The device's refusals carry no status: they are named here, the
        // confirmation's as the sign-in names them. The confirmation has no
        // field, so what the server says about it stands in the line.
        let line = found.errors.bestaetigung.join(" ") || found.line;
        if (wasCancelled(cause) && fromConfirmation) line = t("confirm.cancelled");
        else if (wasCancelled(cause)) line = t("passkey.cancelled");
        else if (alreadyExists(cause)) line = t("passkey.exists");
        if (removal) {
            if (refused) removalConfirmed = null;
            removalErrors = found.errors.passwort;
            removalError = line;
        } else {
            if (refused) confirmed = null;
            passwordErrors = found.errors.passwort;
            error = line;
        }
    }

    function day(iso) {
        return new Date(iso).toLocaleDateString(locale());
    }
</script>

<Dialog label={t("passkey.title")} closeText={t("passkey.close")} {close}>
    <div class="body">
        <p class="small">{t("passkey.hint")}</p>

        {#if keys === null}
            <p class="small">{t("passkey.loading")}</p>
        {:else if keys.length === 0}
            <p class="small">{t("passkey.none")}</p>
        {:else}
            <ul class="keys">
                {#each keys as key (key.id)}
                    <li>
                        <div class="key">
                            <strong>{key.name}</strong>
                            <span class="small">
                                {t("passkey.created_on", { date: day(key.erstellt_am) })}
                                ·
                                {key.zuletzt_benutzt
                                    ? t("passkey.last_used", { date: day(key.zuletzt_benutzt) })
                                    : t("passkey.never_used")}
                            </span>
                        </div>
                        {#if confirming === key.id}
                            <div class="confirm">
                                <p role="alert">{t("passkey.remove_sure")}</p>
                                {#if asksPassword}
                                    <div class="field">
                                        <label>
                                            {t("passkey.remove_password")}
                                            <input
                                                type="password"
                                                bind:value={removalPassword}
                                                autocomplete="current-password"
                                                aria-invalid={removalErrors.length > 0}
                                                aria-describedby="passkey-remove-hint passkey-remove-errors"
                                            />
                                        </label>
                                        <p class="small" id="passkey-remove-hint">
                                            {t("passkey.remove_password_hint")}
                                        </p>
                                        <div id="passkey-remove-errors" role="alert">
                                            {#each removalErrors as sentence, index (index)}
                                                <p class="error">{sentence}</p>
                                            {/each}
                                        </div>
                                    </div>
                                {:else if removalConfirmed}
                                    <p class="done" role="status">
                                        {t("passkey.remove_confirmed")}
                                    </p>
                                {:else}
                                    <p class="small">{t("passkey.remove_confirm_hint")}</p>
                                    <button
                                        class="green"
                                        disabled={removalStep !== ""}
                                        onclick={confirmRemoval}
                                        bind:this={removalConfirmButton}
                                    >
                                        {removalStep === "confirm"
                                            ? t("passkey.confirming")
                                            : t("passkey.confirm")}
                                    </button>
                                {/if}
                                {#if removalError}
                                    <p class="error" role="alert">{removalError}</p>
                                {/if}
                                <!-- Without a password it waits for the confirmation. -->
                                <button
                                    class="delete"
                                    disabled={removalStep !== "" ||
                                        (!asksPassword && !removalConfirmed)}
                                    onclick={() => remove(key)}
                                    bind:this={removeButton}
                                >
                                    {removalStep === "remove"
                                        ? t("passkey.removing")
                                        : t("passkey.remove_confirm")}
                                </button>
                                <button onclick={() => (confirming = null)}>
                                    {t("passkey.cancel")}
                                </button>
                            </div>
                        {:else}
                            <button class="flat" onclick={() => ask(key)}>
                                {t("passkey.remove")}
                            </button>
                        {/if}
                    </li>
                {/each}
            </ul>
        {/if}

        {#if created}
            <p class="done" role="status">{t("passkey.created", { name: created })}</p>
        {/if}
        {#if error}<p class="error" role="alert">{error}</p>{/if}

        <label>
            {t("passkey.name")}
            <input bind:value={name} maxlength="100" autocomplete="off" />
        </label>
        {#if asksPassword}
            <!-- Invisible, for the password manager: without the name it does
                 not know whose entry the password belongs to. -->
            <input type="text" autocomplete="username" value={username ?? ""} readonly hidden />
            <!-- The messages stand next to the label, not in it: otherwise
                 they would become part of the field's name. -->
            <div class="field">
                <label>
                    {t("passkey.password")}
                    <input
                        type="password"
                        bind:value={password}
                        autocomplete="current-password"
                        aria-invalid={passwordErrors.length > 0}
                        aria-describedby="passkey-password-hint passkey-password-errors"
                    />
                </label>
                <p class="small" id="passkey-password-hint">{t("passkey.password_hint")}</p>
                <div id="passkey-password-errors" role="alert">
                    {#each passwordErrors as sentence, index (index)}
                        <p class="error">{sentence}</p>
                    {/each}
                </div>
            </div>
        {:else if confirmed}
            <p class="done" role="status">{t("passkey.confirmed")}</p>
        {:else}
            <p class="small">{t("passkey.confirm_hint")}</p>
            <button
                class="green"
                disabled={step !== ""}
                onclick={confirm}
                bind:this={confirmButton}
            >
                {step === "confirm" ? t("passkey.confirming") : t("passkey.confirm")}
            </button>
        {/if}
        <!-- Without a password it waits for the confirmation. -->
        <button
            class="green"
            disabled={step !== "" || (!asksPassword && !confirmed)}
            onclick={create}
            bind:this={createButton}
        >
            {step === "create" ? t("passkey.creating") : t("passkey.create")}
        </button>
    </div>
</Dialog>

<style>
    .body {
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

    .small {
        margin: 0;
        font-size: 0.8rem;
        color: var(--muted);
    }

    .keys {
        list-style: none;
        margin: 0;
        padding: 0;
        display: flex;
        flex-direction: column;
        gap: 0.5rem;
    }

    .keys li {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        justify-content: space-between;
        gap: 0.5rem;
        padding: 0.6rem 0.8rem;
        border: 1px solid var(--border);
        border-radius: 0.5rem;
    }

    .key {
        display: flex;
        flex-direction: column;
        gap: 0.15rem;
    }

    .confirm {
        display: flex;
        flex: 1 1 100%;
        flex-direction: column;
        gap: 0.5rem;
        padding: 0.6rem 0.75rem;
        border-radius: 0.5rem;
        background: var(--notice);
        font-size: 0.9rem;
    }

    .confirm p {
        margin: 0;
    }

    .delete {
        color: var(--open);
        border-color: var(--open);
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

    .error {
        margin: 0;
        font-size: 0.9rem;
        color: var(--open);
    }

    .done {
        margin: 0;
        padding: 0.6rem 0.8rem;
        border-radius: 0.5rem;
        background: var(--notice);
        font-size: 0.9rem;
    }

    .green {
        min-height: 3rem;
        font-weight: 600;
        color: #fff;
        background: var(--done);
        border-color: var(--done);
    }
</style>
