<script>
    /**
     * How far a group is counted: bar, number and colour.
     *
     * The colour is the actual information: on the walk through storage one
     * sees at a glance where nothing stands yet.
     */
    let { total, counted, state } = $props();
</script>

<div class="progress {state}">
    <div class="bar">
        <!-- 'style:' sets via the CSSOM, because a 'style' attribute would be
             blocked by the policy. -->
        <div style:width="{total ? (counted / total) * 100 : 0}%"></div>
    </div>
    <span class="count">{counted}/{total}</span>
</div>

<style>
    .progress {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        min-width: 6rem;
    }

    .bar {
        flex: 1;
        height: 0.375rem;
        border-radius: 999px;
        background: var(--border);
        overflow: hidden;
    }

    .bar div {
        height: 100%;
        background: currentColor;
    }

    .count {
        font-variant-numeric: tabular-nums;
        font-size: 0.8rem;
        color: var(--muted);
    }

    .empty {
        color: var(--open);
    }

    .partial {
        color: var(--partial);
    }

    .done {
        color: var(--done);
    }
</style>
