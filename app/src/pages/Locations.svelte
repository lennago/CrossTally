<script>
    /**
     * The locations of an area. Counting happens at the location, the expectations hang here.
     *
     * A location nothing is expected at stands here too, without a bar. Whoever
     * finds something there adds it through the search.
     */
    import { locations } from "../services/progress.js";
    import { go } from "../services/route.svelte.js";
    import { t } from "../services/language.svelte.js";
    import { counting } from "../services/counting.svelte.js";
    import Progress from "../components/Progress.svelte";
    import Row from "../components/Row.svelte";

    let { area } = $props();
    let list = $derived(locations(counting.positions, area, counting.masterData?.orte ?? []));
</script>

{#each list as location (location.id)}
    <Row
        title={location.name}
        hint={location.total ? t("list.open", { n: location.open }) : t("list.nothing_expected")}
        onclick={() => go("inventory", counting.inventoryId, "area", area, "location", location.id)}
    >
        {#snippet progress()}
            {#if location.total}
                <Progress {...location} />
            {/if}
        {/snippet}
    </Row>
{/each}
