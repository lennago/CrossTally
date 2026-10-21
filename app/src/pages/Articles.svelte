<script>
    /**
     * The articles at a location, in the order of the counting list.
     *
     * As the goods stand on the shelf: that saves searching on the round. The
     * colour says where nothing is entered yet. Searched is by name, because
     * nobody knows the article number by heart when counting.
     *
     * **A tab per inventory type** the inventory counts, as far as there are
     * rows for it here: in front the monthly round, behind it what is only
     * due now. With one tab the bar goes. The search covers all of them.
     * Whoever types a name searches for the article and not the tab, and
     * the row then names its type.
     *
     * Whoever comes back from the entry form stands again **at the last counted
     * article** instead of at the top: at a location with sixty rows every way
     * back would otherwise mean searching for the place anew.
     */
    import { articles as articlesAt, inTabs, search, tabs } from "../services/progress.js";
    import { rememberedArticle, setTab, tabOf } from "../services/tabs.svelte.js";
    import { go } from "../services/route.svelte.js";
    import { holdKeyboard } from "../services/keyboard.js";
    import { counting } from "../services/counting.svelte.js";
    import Progress from "../components/Progress.svelte";
    import Row from "../components/Row.svelte";
    import { t } from "../services/language.svelte.js";

    let { area, locationId } = $props();

    let term = $state("");
    let types = $derived(counting.inventory?.inventurarten ?? []);
    let expected = $derived(inTabs(articlesAt(counting.positions, locationId), types));
    let shownTabs = $derived(tabs(expected, types));
    let several = $derived(shownTabs.length > 1);
    let tab = $derived(tabOf(locationId, shownTabs));
    let searching = $derived(term.trim() !== "");
    let inTab = $derived(
        several && !searching ? expected.filter((entry) => entry.tab === tab) : expected,
    );
    let shown = $derived(search(inTab, term));

    // The names of all types, also those this inventory does not count: the
    // master data brings them.
    let typeNames = $derived(
        new Map(
            [...(counting.masterData?.inventurarten ?? []), ...types].map((type) => [
                type.id,
                type.name,
            ]),
        ),
    );

    /**
     * The type of a row where it says something: counted on top, outside the
     * inventory's types, or in a search over several tabs.
     */
    function typeSuffix(entry) {
        // Without the list of types nothing is foreign: an inventory stored
        // before the types arrived knows none until the first sync succeeds.
        const outside =
            types.length > 0 &&
            entry.type !== null &&
            !types.some((type) => type.id === entry.type);
        const name = typeNames.get(entry.type);
        return name && (outside || (several && searching)) ? ` · ${name}` : "";
    }

    // Once per build, as soon as the list is there. After that scrolling
    // belongs to the person again, even if a sync redraws the list.
    let last = $derived(rememberedArticle(locationId));
    let scrolled = false;
    $effect(() => {
        if (scrolled || !shown.length) return;
        scrolled = true;
        if (last === null) return;
        document.querySelector(`[data-key="${last}"]`)?.scrollIntoView({ block: "center" });
    });

    function open(articleId) {
        // In the tap itself, so the keyboard is open in the form ('keyboard.js').
        holdKeyboard();
        go(
            "inventory",
            counting.inventoryId,
            "area",
            area,
            "location",
            locationId,
            "article",
            articleId,
        );
    }
</script>

<div class="header">
    {#if several}
        <div class="tabs" role="tablist" aria-label={t("articles.types")}>
            {#each shownTabs as shownTab (shownTab.key)}
                <button
                    role="tab"
                    class={shownTab.state}
                    aria-selected={!searching && tab === shownTab.key}
                    onclick={() => {
                        term = "";
                        setTab(locationId, shownTab.key);
                    }}
                >
                    {shownTab.name}
                    <span class="count">{shownTab.counted}/{shownTab.total}</span>
                </button>
            {/each}
        </div>
    {/if}
    <div class="search">
        <input
            type="search"
            bind:value={term}
            placeholder={t("search.title")}
            autocapitalize="none"
        />
    </div>
</div>

{#each shown as entry (entry.id)}
    <Row
        key={entry.id}
        marked={entry.id === last}
        title={entry.name}
        hint={t("articles.rows", { n: entry.total, open: entry.open }) + typeSuffix(entry)}
        onclick={() => open(entry.id)}
    >
        {#snippet progress()}
            <Progress {...entry} />
        {/snippet}
    </Row>
{:else}
    <p class="hint">
        {#if searching}
            {t("search.no_match")}
        {:else}
            {t("articles.empty")}
        {/if}
    </p>
{/each}

<style>
    .header {
        position: sticky;
        top: calc(var(--header-height) + var(--banner-height));
        z-index: 1;
        background: var(--background);
        border-bottom: 1px solid var(--border);
    }

    .search {
        padding: 0.75rem 1rem;
    }

    .tabs {
        display: flex;
        /* With four or more types the row scrolls, not the whole page. */
        overflow-x: auto;
        scrollbar-width: thin;
        border-bottom: 1px solid var(--border);
    }

    .tabs button {
        flex: 1 0 auto;
        white-space: nowrap;
        padding: 0.7rem 0.5rem 0.55rem;
        border: 0;
        border-bottom: 3px solid transparent;
        background: none;
        color: var(--muted);
        font: inherit;
        cursor: pointer;
    }

    .tabs button[aria-selected="true"] {
        border-bottom-color: var(--accent);
        color: var(--text);
        font-weight: 600;
    }

    /* The same colours as the bar on the rows: where nothing stands yet one
       sees on the tab too. */
    .count {
        margin-left: 0.35rem;
        font-size: 0.8rem;
        font-weight: 400;
        font-variant-numeric: tabular-nums;
    }

    .empty .count {
        color: var(--open);
    }

    .partial .count {
        color: var(--partial);
    }

    .done .count {
        color: var(--done);
    }

    .search input {
        width: 100%;
    }

    .hint {
        padding: 0 1rem 1rem;
        color: var(--muted);
    }
</style>
