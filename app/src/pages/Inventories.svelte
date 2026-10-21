<script>
    /**
     * What is pending: the running inventories I am assigned to.
     *
     * The list comes from the working state, not from a state of its own.
     * That way the background sync takes it along. Whoever has the app lying
     * open while someone creates an inventory sees it by itself. The bar per
     * house comes from the storage ('counting.progress'). The sync fetches
     * every inventory, the never opened ones too. A locked one says so: it
     * opens on its review list.
     */
    import { onMount } from "svelte";

    import { go } from "../services/route.svelte.js";
    import { locale, t } from "../services/language.svelte.js";
    import { counting, refresh } from "../services/counting.svelte.js";
    import Progress from "../components/Progress.svelte";
    import Row from "../components/Row.svelte";

    onMount(refresh);

    /** The cut-off date the way the language writes it, not the ISO date of the API. */
    function dayText(iso) {
        const [year, month, day] = iso.split("-").map(Number);
        if (!year || !month || !day) return iso;
        return new Date(year, month - 1, day).toLocaleDateString(locale(), { dateStyle: "medium" });
    }
</script>

<!-- One that only a draft keeps is not offered: nothing says it will be
     counted again. The notice of the draft leads to it ('cleanUp'). -->
{#each counting.inventories.filter((entry) => !entry.draftOnly) as inventory (inventory.id)}
    <Row
        title={inventory.haus}
        hint={[
            dayText(inventory.stichtag),
            inventory.arten_anzeige,
            // Its stored status still says "counting": numbers or a draft wait for it ('cleanUp').
            inventory.held ? t("inventories.on_hold") : inventory.status_anzeige,
            inventory.gesperrt ? t("inventories.locked") : null,
        ]
            .filter(Boolean)
            .join(" · ")}
        onclick={() => go("inventory", inventory.id)}
    >
        {#snippet progress()}
            {#if counting.progress[inventory.id]}
                <Progress {...counting.progress[inventory.id]} />
            {/if}
        {/snippet}
    </Row>
{:else}
    <!-- Before the first answer the list is empty for a moment. "Nothing
         pending" would then be a false sentence (until 10/2026 it stood there). -->
    <p class="hint">{counting.synced ? t("inventories.empty") : t("app.loading")}</p>
{/each}

<style>
    .hint {
        padding: 0 1rem 1rem;
        color: var(--muted);
    }
</style>
