<script>
    /**
     * A clickable row with title, extras and count state.
     *
     * 'key' makes the row findable for the list that scrolls back to it.
     * 'marked' shows which one someone is coming from.
     */
    let { title, hint = "", progress = null, onclick, key = null, marked = false } = $props();
</script>

<button class="row" class:marked data-key={key} {onclick}>
    <span class="text">
        <strong>{title}</strong>
        {#if hint}<span class="hint">{hint}</span>{/if}
    </span>
    {#if progress}
        <span class="right">{@render progress()}</span>
    {/if}
    <span class="arrow" aria-hidden="true">›</span>
</button>

<style>
    .row {
        display: flex;
        align-items: center;
        gap: 0.75rem;
        width: 100%;
        padding: 0.9rem 1rem;
        border: 0;
        border-bottom: 1px solid var(--border);
        background: var(--surface);
        color: inherit;
        font: inherit;
        text-align: left;
        cursor: pointer;
    }

    /* A bar at the edge instead of a surface: the colours of the count state
       should remain the loudest information of the row. */
    .marked {
        box-shadow: inset 4px 0 0 var(--accent);
    }

    .row:active {
        background: var(--selected);
    }

    .text {
        flex: 1;
        min-width: 0;
    }

    .text strong {
        display: block;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    .hint {
        font-size: 0.8rem;
        color: var(--muted);
    }

    .right {
        width: 7rem;
    }

    .arrow {
        color: var(--muted);
    }
</style>
