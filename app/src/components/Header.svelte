<script>
    /**
     * The header bar: left back, in the middle where one stands, right the
     * account and the sync, or "Cancel" where something is being filled in.
     *
     * Big enough for the thumb: it is used standing, with one hand.
     */
    import { counting } from "../services/counting.svelte.js";
    import { t } from "../services/language.svelte.js";

    import RefreshButton from "./RefreshButton.svelte";
    import SyncState from "./SyncState.svelte";

    let {
        title,
        subtitle = "",
        canGoBack,
        cancellable,
        accountName,
        back,
        cancel,
        menu,
        sync,
        refresh,
    } = $props();
</script>

<header class="header">
    {#if canGoBack}
        <button class="back" onclick={back}>
            <span aria-hidden="true">‹</span>
            {t("header.back")}
        </button>
    {/if}
    <span class="middle">
        <strong>{title}</strong>
        {#if subtitle}<span class="small">{subtitle}</span>{/if}
    </span>
    {#if cancellable}
        <button class="flat" onclick={cancel}>{t("header.cancel")}</button>
    {:else}
        <span class="right">
            <button class="account" onclick={menu}>
                {accountName}
            </button>
            <!-- Green means: the state is out. Yellow means: it waits.
                 Counting goes on, that is the normal case in storage. -->
            <button
                class="sync-state"
                onclick={sync}
                title={counting.online
                    ? t("sync_state.online")
                    : counting.pending
                      ? t("sync_state.offline_pending", { n: counting.pending })
                      : t("sync_state.offline")}
                aria-label={t("header.sync")}
            >
                <SyncState
                    online={counting.online}
                    pending={counting.pending}
                    conflicts={counting.rejected + counting.quantityConflicts}
                />
            </button>
            <RefreshButton onclick={refresh} running={counting.loading} />
        </span>
    {/if}
</header>

<style>
    .account {
        padding: 0.3rem 0.5rem;
        border: none;
        border-radius: 999px;
        background: none;
        color: var(--muted);
        font-size: 0.8rem;
    }

    .header {
        position: sticky;
        top: 0;
        z-index: 2;
        display: flex;
        align-items: center;
        gap: 0.5rem;
        min-height: var(--header-height);
        /* As an installed app there is no browser bar any more: the content
           then starts below the clock and the notch ('viewport-fit=cover').
           The insets at the top and the sides keep the bar clear of them.
           In the browser they are zero and change nothing. */
        padding: calc(0.35rem + env(safe-area-inset-top)) calc(0.5rem + env(safe-area-inset-right))
            0.35rem calc(0.5rem + env(safe-area-inset-left));
        background: var(--surface);
        border-bottom: 1px solid var(--border);
    }

    /* Big enough for the thumb and clearly recognisable as the way back. */
    .back {
        display: flex;
        align-items: center;
        gap: 0.2rem;
        min-height: 2.75rem;
        padding: 0.4rem 0.8rem 0.4rem 0.6rem;
        border: 1px solid var(--border);
        border-radius: 999px;
        background: var(--selected);
        color: var(--accent);
        font-weight: 600;
    }

    .back span[aria-hidden] {
        font-size: 1.35rem;
        line-height: 1;
    }

    .middle {
        flex: 1;
        min-width: 0;
        text-align: center;
    }

    .middle strong {
        display: block;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    .small {
        font-size: 0.78rem;
        color: var(--muted);
    }

    .right {
        display: flex;
        align-items: center;
        gap: 0.4rem;
    }

    .sync-state {
        display: flex;
        align-items: center;
        gap: 0.25rem;
        min-height: 2.25rem;
        padding: 0.3rem 0.5rem;
        border: 1px solid transparent;
        border-radius: 999px;
        background: none;
        line-height: 1;
    }
</style>
