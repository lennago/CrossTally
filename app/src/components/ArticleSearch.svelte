<script>
    /**
     * A window over the list: which article should still be counted here?
     *
     * Available is what the house carries and has **no row yet** here.
     * Everything else is already in the list. Chosen is without pack. Which
     * one it is is decided in the entry form.
     *
     * Plus inactive articles that would be due in this inventory (seasonal
     * goods, say), behind all active ones and marked as such. A counted one
     * can be reactivated before closing. At the very back **discontinued**
     * ones whose slot in the house is still free: what was delisted is
     * sometimes still on the shelf, and otherwise the only option would be to
     * take some similar article.
     *
     * Marked with its type is an article this inventory does not count: the
     * closing asks whether it should be counted in one of its types from
     * now on. Where the inventory counts several, every hit names its type,
     * because that is the tab it lands in.
     *
     * The window hangs at the **top**, not at the bottom: with the first letter
     * the keyboard comes out and covers the lower half. A window sticking to
     * the bottom edge and shrinking with every hit fewer vanished behind it,
     * exactly when only the article searched for was left.
     */
    import { onMount } from "svelte";

    import Dialog from "./Dialog.svelte";
    import { addable } from "../services/progress.js";
    import { t } from "../services/language.svelte.js";
    import { unitText } from "../services/units.js";

    let { existing, all, types = [], allTypes = [], choose, close } = $props();

    let term = $state("");
    let field = $state(null);
    let missing = $derived(addable(all, existing, term));
    let typeNames = $derived(new Map([...allTypes, ...types].map((type) => [type.id, type.name])));

    /** The name of the article's type where it says something, otherwise empty. */
    function typeName(article) {
        const counted = types.some((type) => type.id === article.inventurart);
        return counted && types.length < 2 ? "" : (typeNames.get(article.inventurart) ?? "");
    }

    // The dialog exists to type a search term into. Not through 'autofocus':
    // Svelte's yields to whatever holds the focus, and Chromium focuses the
    // "+" that opens the window. Until 10/2026 the field stayed without the
    // focus there, on Android too.
    onMount(() => field?.focus());

    // How much of the screen remains above the keyboard. The devices only
    // shrink the visible viewport ('visualViewport') for that, not the page.
    // '85vh' thus reached further behind the keyboard, and the last hits
    // could not be scrolled up.
    let visible = $state(window.visualViewport?.height ?? null);
    $effect(() => {
        const viewport = window.visualViewport;
        if (!viewport) return;
        const measure = () => (visible = viewport.height);
        viewport.addEventListener("resize", measure);
        return () => viewport.removeEventListener("resize", measure);
    });
</script>

<Dialog
    label={t("search.title")}
    title={t("search.add")}
    closeText={t("header.cancel")}
    {close}
    top
    height={visible}
>
    <div class="search">
        <input
            type="search"
            bind:this={field}
            bind:value={term}
            placeholder={t("search.title")}
            autocapitalize="none"
        />
    </div>
    <div class="list">
        {#each missing.slice(0, 40) as article (article.id)}
            <button class="hit" onclick={() => choose(article)}>
                <strong>{article.name}</strong>
                <span class="small">
                    {unitText(article.einheit)}{#if article.eingestellt === true}
                        · <span class="discontinued">{t("search.discontinued")}</span>
                    {:else if article.aktiv === false}
                        · <span class="inactive">{t("search.inactive")}</span>{/if}
                    {#if typeName(article)}· {typeName(article)}{/if}
                </span>
            </button>
        {:else}
            <p class="small">
                {term ? t("search.no_match") : t("search.all_listed")}
            </p>
        {/each}
        {#if missing.length > 40}
            <p class="small">{t("search.more", { n: missing.length - 40 })}</p>
        {/if}
    </div>
</Dialog>

<style>
    .search {
        padding: 0.75rem 1rem;
    }

    .search input {
        width: 100%;
    }

    .list {
        overflow-y: auto;
        /* The rounding at the bottom should not cut off a hit. */
        padding-bottom: 0.5rem;
    }

    .hit {
        display: block;
        width: 100%;
        text-align: left;
        border: 0;
        border-top: 1px solid var(--border);
        border-radius: 0;
        background: var(--surface);
        padding: 0.9rem 1rem;
    }

    .inactive {
        color: var(--partial);
        font-weight: 600;
    }

    /* Clearer than "inactive": the house no longer carries this article at all. */
    .discontinued {
        color: var(--open);
        font-weight: 600;
    }

    .small {
        display: block;
        padding: 0.5rem 1rem;
        font-size: 0.85rem;
        color: var(--muted);
    }
</style>
