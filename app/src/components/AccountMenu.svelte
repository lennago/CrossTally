<script>
    /**
     * The account menu is kept small: the appearance, the language, the
     * password, signing out, the link to the privacy page and the version.
     * Everything else is in the web interface. Appearance and language hang
     * on the account and apply there as well.
     *
     * The password has its form in a dialog of its own ('PasswordDialog'),
     * the address ('EmailDialog') and the passkeys ('PasskeyDialog')
     * likewise. The menu only leads there.
     *
     * The privacy page stands here because the counters never get to see the
     * web interface. The link leads out of the app onto a page by Django. That
     * works because both lie under the same origin. With 'npm run dev' on port
     * 5173 it does not.
     *
     * The language rides along in the address ('?sprache=', the website's
     * parameter). The app identifies itself with a token and has no Django
     * session: for Django the call is anonymous, and without an account the
     * page would always come in the server's language.
     *
     * Signing out clears the storage. If something still waits in it, the menu
     * asks first. In a dead spot what was counted would otherwise be gone with one tap.
     * A form the app left behind unsaved counts too: what was typed in it goes as well.
     *
     * "Report a problem or suggestion" opens a mail to the address the
     * administration named ('services/feedback.js'). Without one the entry is
     * missing. The version stands below it, the first question on any report.
     */
    import { onMount } from "svelte";

    import { feedbackLink } from "../services/feedback.js";
    import { unsavedCount, unsentCount } from "../services/storage.js";
    import { CHOICES, appearance, choose } from "../services/theme.svelte.js";
    import {
        LANGUAGES,
        language,
        t,
        choose as chooseLanguage,
    } from "../services/language.svelte.js";
    import { APP_VERSION } from "../services/version.svelte.js";

    //: Whether the "Privacy" page exists. The administration can empty it, then
    //: the menu would point into nothing. An old state without the entry has it.
    //: Its address follows the server's language ('/datenschutz/' or
    //: '/privacy/'), and the server names it when signing in. An old state
    //: without the entry takes the German path.
    //: The legal notice likewise, except that it exists only where the
    //: administration wrote one. An old state without the entry has none.
    //: The same holds for the address for feedback.
    let {
        username,
        signOut,
        unsent = 0,
        sync,
        changePassword,
        changeEmail,
        managePasskeys,
        privacy = true,
        privacyUrl = "/datenschutz/",
        legalNotice = false,
        legalNoticeUrl = "/impressum/",
        feedbackAddress = "",
        online = true,
    } = $props();

    //: What waits in the storage, counted when the menu opens, for the mail.
    //: Right after the start the working state ('unsent') does not know it yet.
    let stored = $state(0);
    onMount(async () => {
        if (feedbackAddress) stored = await unsentCount();
    });

    //: Built when the menu opens, so the mail names the page and the time of now.
    let feedbackHref = $derived(
        feedbackAddress
            ? feedbackLink({
                  address: feedbackAddress,
                  username,
                  online,
                  waiting: Math.max(stored, unsent),
              })
            : "",
    );

    //: Counted on tap, in the storage itself. The working state ('unsent')
    //: is not loaded yet right after the start, and whoever signs out then
    //: already would delete without a question.
    let counted = $state(0);
    //: The forms left behind unsaved, counted with them. A sync saves none.
    let unsaved = $state(0);
    let asked = $state(false);
    let syncing = $state(false);
    let count = $derived(Math.max(counted, unsent));
    // If everything is out by now, the question is moot.
    let warn = $derived(asked && (count > 0 || unsaved > 0));

    async function countWaiting() {
        counted = await unsentCount();
        unsaved = await unsavedCount();
    }

    async function signOutAsked() {
        await countWaiting();
        if (counted > 0 || unsaved > 0) asked = true;
        else signOut();
    }

    async function syncNow() {
        syncing = true;
        try {
            await sync();
        } finally {
            await countWaiting();
            syncing = false;
        }
    }

    /**
     * The language, and then a sync: the status of an inventory comes from
     * the server in the language it was asked in. Until 10/2026 it stood in
     * the old language until the next sync. Without network the choice
     * waits, and so does the list.
     */
    async function switchLanguage(choice) {
        const me = await chooseLanguage(choice);
        if (me && sync) await sync();
    }
</script>

<div class="menu">
    <div class="row">
        <p class="who">{t("account.who", { who: username ?? "?" })}</p>
        <button class="flat" onclick={signOutAsked}>{t("account.sign_out")}</button>
    </div>
    {#if warn}
        <div class="warning" role="alert">
            {#if count > 0}
                <p>{t("account.waiting", { n: count })}</p>
            {/if}
            {#if unsaved > 0}
                <p>{t("account.unsaved", { n: unsaved })}</p>
            {/if}
            <!-- A sync sends what waits. A form left behind is saved in the
                 form, where the notice at the top leads. -->
            {#if count > 0}
                <button class="green" disabled={syncing} onclick={syncNow}>
                    {syncing ? t("account.syncing") : t("account.sync_now")}
                </button>
            {/if}
            <button class="delete" onclick={signOut}>{t("account.sign_out_anyway")}</button>
        </div>
    {/if}
    <div class="choices" role="group" aria-label={t("appearance.title")}>
        {#each CHOICES as entry (entry.value)}
            <button
                class:chosen={appearance.choice === entry.value}
                aria-pressed={appearance.choice === entry.value}
                onclick={() => void choose(entry.value)}
            >
                {t(entry.key)}
            </button>
        {/each}
    </div>

    <!-- The same row for the language. Like the appearance it hangs on the
         account and thus applies on the website too. -->
    <div class="choices" role="group" aria-label={t("language.title")}>
        {#each LANGUAGES as entry (entry.value)}
            <button
                class:chosen={language.choice === entry.value}
                aria-pressed={language.choice === entry.value}
                onclick={() => void switchLanguage(entry.value)}
            >
                {entry.text}
            </button>
        {/each}
    </div>
    <button class="wide" onclick={changePassword}>{t("account.change_password")}</button>
    <button class="wide" onclick={changeEmail}>{t("account.change_email")}</button>
    <!-- Only where the browser can: elsewhere the dialog could only say no. -->
    {#if managePasskeys}
        <button class="wide" onclick={managePasskeys}>{t("account.passkeys")}</button>
    {/if}
    {#if feedbackHref}
        <a class="wide feedback" href={feedbackHref}>{t("account.feedback")}</a>
    {/if}
    {#if privacy || legalNotice}
        <p class="legal">
            {#if legalNotice}
                <a href="{legalNoticeUrl}?sprache={language.choice}">{t("account.legal_notice")}</a>
            {/if}
            {#if privacy}
                <a href="{privacyUrl}?sprache={language.choice}">{t("account.privacy")}</a>
            {/if}
        </p>
    {/if}
    <p class="version">{t("account.version", { version: APP_VERSION })}</p>
</div>

<style>
    .menu {
        position: sticky;
        top: calc(var(--header-height) + var(--banner-height) + env(safe-area-inset-top));
        z-index: 2;
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        justify-content: space-between;
        gap: 0.5rem;
        padding: 0.5rem 0.75rem;
        background: var(--selected);
        border-bottom: 1px solid var(--border);
    }

    .menu .who {
        margin: 0;
        font-size: 0.85rem;
        color: var(--muted);
    }

    .menu .row {
        display: flex;
        flex: 1 1 100%;
        align-items: center;
        justify-content: space-between;
        gap: 0.5rem;
    }

    .warning {
        display: flex;
        flex: 1 1 100%;
        flex-direction: column;
        gap: 0.5rem;
        padding: 0.6rem 0.75rem;
        border-radius: 0.5rem;
        background: var(--notice);
        font-size: 0.9rem;
    }

    .warning p {
        margin: 0;
    }

    .warning button {
        min-height: 2.75rem;
    }

    .green {
        font-weight: 600;
        color: #fff;
        background: var(--done);
        border-color: var(--done);
    }

    .delete {
        color: var(--open);
        border-color: var(--open);
    }

    /* Three equally wide fields side by side, big enough for the thumb. */
    .choices {
        display: flex;
        flex: 1 1 100%;
        gap: 0.25rem;
    }

    .choices button {
        flex: 1;
        min-height: 2.5rem;
        padding: 0.35rem 0.5rem;
        font-size: 0.9rem;
    }

    .choices button.chosen {
        border-color: var(--text);
        font-weight: 600;
    }

    .wide {
        flex: 1 1 100%;
        min-height: 2.5rem;
        padding: 0.35rem 0.5rem;
        font-size: 0.9rem;
    }

    .legal {
        flex: 1 1 100%;
        display: flex;
        flex-wrap: wrap;
        justify-content: center;
        gap: 0 1rem;
        margin: 0;
        font-size: 0.8rem;
    }

    .legal a {
        color: var(--muted);
    }

    /* A link that looks like the buttons above it: a mail program opens, no page. */
    .feedback {
        display: flex;
        align-items: center;
        justify-content: center;
        color: inherit;
        text-decoration: none;
        border: 1px solid var(--border);
        border-radius: 0.5rem;
        background: var(--surface);
    }

    .version {
        flex: 1 1 100%;
        margin: 0;
        text-align: center;
        font-size: 0.8rem;
        color: var(--muted);
    }
</style>
