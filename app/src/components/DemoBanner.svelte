<script>
    /**
     * A banner below the header as long as the app runs on the invented stock.
     *
     * Whether it is is said by the API at "who am I" ('demo'). It thus hangs on
     * the server's data and not on the app: whoever signs in to a real stock
     * no longer sees it.
     *
     * It sticks below the header and stays when scrolling. Whoever works here
     * should never forget that the numbers are invented. Everything that in
     * turn attaches itself below the header (the account menu, the search
     * line, the article header) reckons with its height: it stands as
     * '--banner-height' on the root node. It is measured instead of set,
     * because on a narrow device the sentence wraps.
     */
    import { t } from "../services/language.svelte.js";

    let { visible = false } = $props();

    let height = $state(0);

    $effect(() => {
        const root = document.documentElement;
        if (!visible) {
            // Without banner the zero from 'style.css' applies again.
            root.style.removeProperty("--banner-height");
            return;
        }
        root.style.setProperty("--banner-height", `${height}px`);
        return () => root.style.removeProperty("--banner-height");
    });
</script>

{#if visible}
    <p class="demo" bind:clientHeight={height}>{t("demo.banner")}</p>
{/if}

<style>
    .demo {
        position: sticky;
        top: calc(var(--header-height) + env(safe-area-inset-top));
        z-index: 2;
        margin: 0;
        padding: 0.3rem 0.6rem;
        /* The same surface as every other notice. It knows light and dark. */
        background: var(--notice);
        color: var(--text);
        border-bottom: 1px solid var(--border);
        font-size: 0.78rem;
        text-align: center;
    }
</style>
