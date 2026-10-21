<script>
    /**
     * The areas of a house, first level of the way through the house.
     *
     * Also those in which nothing is expected: their locations come from the
     * master data. They carry no bar, because nothing is open there.
     *
     * Above them the way to the review list: what closing would freeze, per
     * article. Counters see it too. Whoever sees where the count deviates can
     * count again before anyone closes.
     */
    import { areas } from "../services/progress.js";
    import { go } from "../services/route.svelte.js";
    import { t } from "../services/language.svelte.js";
    import { counting } from "../services/counting.svelte.js";
    import Progress from "../components/Progress.svelte";
    import Row from "../components/Row.svelte";

    let list = $derived(areas(counting.positions, counting.masterData?.orte ?? []));
</script>

<div class="review">
    <Row
        title={t("review.entry")}
        hint={counting.inventory?.differenz_von_hand
            ? t("review.entry_manual")
            : t("review.entry_hint")}
        onclick={() => go("inventory", counting.inventoryId, "review")}
    />
</div>

{#each list as area (area.name)}
    <Row
        title={area.name}
        hint={area.total ? t("list.open", { n: area.open }) : t("list.nothing_expected")}
        onclick={() => go("inventory", counting.inventoryId, "area", area.name)}
    >
        {#snippet progress()}
            {#if area.total}
                <Progress {...area} />
            {/if}
        {/snippet}
    </Row>
{:else}
    <!-- Without master data nobody here knows the house yet. Only with it
         an empty list says something about the house. -->
    <p class="hint">{counting.masterData ? t("areas.empty") : t("areas.loading")}</p>
{/each}

<style>
    /* Apart from the areas: it is no place in the house. */
    .review {
        margin-bottom: 0.75rem;
        border-top: 1px solid var(--border);
    }

    .hint {
        padding: 1rem;
        color: var(--muted);
    }
</style>
