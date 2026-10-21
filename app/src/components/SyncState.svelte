<script>
    /**
     * Connected or not, as a drawing instead of a character.
     *
     * A combined slash ("⇅̸") sits somewhere depending on the font. But the
     * stroke belongs exactly through the symbol. As SVG it lies where it
     * belongs, in every font and every size.
     */
    import { t } from "../services/language.svelte.js";

    let { online, pending = 0, conflicts = 0 } = $props();
</script>

<svg viewBox="0 0 24 24" aria-hidden="true" class:offline={!online}>
    <!-- Two arrows: up and down, the sync in both directions. -->
    <path d="M8 20V5m0 0L4.5 8.5M8 5l3.5 3.5" />
    <path d="M16 4v15m0 0l3.5-3.5M16 19l-3.5-3.5" />
    {#if !online}
        <!-- Two layers: first a light seam so the stroke stands out from the
             arrows, then the stroke itself. -->
        <path class="seam" d="M4 20L20 4" />
        <path class="stroke" d="M4 20L20 4" />
    {/if}
</svg>
{#if pending}<span class="count">{pending}</span>{/if}
{#if conflicts}<span class="warning" title={t("sync_state.attention")}>!</span>{/if}

<style>
    svg {
        width: 1.35rem;
        height: 1.35rem;
        fill: none;
        stroke: var(--done);
        stroke-width: 2;
        stroke-linecap: round;
        stroke-linejoin: round;
    }

    svg.offline {
        stroke: var(--partial);
    }

    .seam {
        stroke: var(--surface);
        stroke-width: 4;
    }

    .stroke {
        stroke-width: 2;
    }

    .count {
        font-size: 0.75rem;
        font-weight: 600;
    }

    /* Rejected is no edge case, because a counted quantity is missing in the end. */
    .warning {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 1.1rem;
        height: 1.1rem;
        border-radius: 999px;
        background: var(--open);
        color: #fff;
        font-size: 0.75rem;
        font-weight: 700;
    }
</style>
