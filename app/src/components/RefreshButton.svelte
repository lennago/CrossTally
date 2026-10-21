<script>
    /**
     * The button at the very right: check now whether there is anything new.
     *
     * The sync runs in the background anyway. Here it stands for the case that
     * someone does not want to wait for the next interval, say because an
     * inventory was just released. While it runs, the icon spins.
     *
     * It is also the way to fetch the **master data** anew: locations,
     * articles, packs and containers created since the inventory was first
     * loaded. The background sync leaves them alone, unless the server
     * rejected a new row built from them ('outdated' in 'sync/inventories.js').
     */
    import { t } from "../services/language.svelte.js";

    let { onclick, running = false } = $props();
</script>

<button {onclick} disabled={running} aria-label={t("header.refresh")} title={t("header.refresh")}>
    <svg viewBox="0 0 24 24" aria-hidden="true" class:spinning={running}>
        <!-- A circle that is almost closed, with an arrowhead at the end. -->
        <path d="M20 12a8 8 0 1 1-2.3-5.6" />
        <path d="M20 4v5h-5" />
    </svg>
</button>

<style>
    button {
        display: flex;
        align-items: center;
        justify-content: center;
        min-width: 2.5rem;
        min-height: 2.5rem;
        padding: 0.3rem;
        border: none;
        border-radius: 999px;
        background: none;
        color: var(--muted);
    }

    button:disabled {
        opacity: 1;
    }

    svg {
        width: 1.35rem;
        height: 1.35rem;
        fill: none;
        stroke: currentColor;
        stroke-width: 2;
        stroke-linecap: round;
        stroke-linejoin: round;
    }

    .spinning {
        animation: spin 0.9s linear infinite;
    }

    @keyframes spin {
        to {
            transform: rotate(360deg);
        }
    }

    /* Whoever has switched off motion should not get it here. The
       disabled button says anyway that something is running. */
    @media (prefers-reduced-motion: reduce) {
        .spinning {
            animation: none;
        }
    }
</style>
