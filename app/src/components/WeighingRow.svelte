<script>
    /**
     * One weighing of the entry form: its vessel first, then its gross weight.
     *
     * The form holds the draft: the vessel and the weight are bound to it
     * ('container', 'gross'), a removed row stays struck through until
     * moving on ('removed', 'onremove'). 'netto' is what the server made of
     * a stored weighing, 'tooLight' that the weight lies below the tare.
     */
    import { containerChosen } from "../services/focus.js";
    import { t } from "../services/language.svelte.js";
    import { formatQuantity } from "../services/numbers.js";
    import { unitText } from "../services/units.js";

    let {
        container = $bindable(),
        gross = $bindable(),
        removed,
        netto,
        groups,
        unit,
        tooLight,
        onenter,
        onremove,
    } = $props();
</script>

<div class="weighing" class:removed>
    <div class="fields">
        <!-- Per weighing its own vessel: the same product
             often lies in different containers. It stands
             before the weight, because first the vessel is
             chosen, then weighed. Nothing is preselected:
             a wrong tare would go unnoticed by anyone when
             counting. 'data-empty' tells the enter key that
             a choice is still to be made here. -->
        <select
            bind:value={container}
            disabled={removed}
            aria-label={t("entry.container")}
            data-empty={container == null ? "" : undefined}
            onchange={containerChosen}
        >
            {#if container == null}
                <option value={null} disabled>{t("entry.choose_container")}</option>
            {/if}
            <!-- Grouped as one reaches for it: first what
                 belongs to the article, then the house,
                 finally borrowed ('containers.js'). -->
            {#snippet choice(item)}
                <option value={item.id}>
                    {item.name} ({formatQuantity(item.tara)} g)
                </option>
            {/snippet}
            {#each groups as group (group.key)}
                {#if group.title}
                    <optgroup label={t(group.title)}>
                        {#each group.containers as item (item.id)}
                            {@render choice(item)}
                        {/each}
                    </optgroup>
                {:else}
                    <!-- What weighs nothing stands at the very top and
                         without a heading ('containers.js'). -->
                    {#each group.containers as item (item.id)}
                        {@render choice(item)}
                    {/each}
                {/if}
            {/each}
        </select>
        <div class="input">
            <!-- Read-only until the vessel is chosen: without tare nothing
                 could be subtracted, and a missing vessel should not even
                 occur. Whoever lands here is led into the selection
                 ('containerFirst'). On the iPhone the form assistant then
                 skips the field, there it is tapped. -->
            <input
                type="text"
                inputmode="decimal"
                enterkeyhint="next"
                onkeydown={onenter}
                placeholder={container == null ? t("entry.container_first") : t("entry.gross")}
                class:too-light={tooLight}
                readonly={container == null}
                disabled={removed}
                bind:value={gross}
            />
            <button class="flat" onclick={onremove}>
                {removed ? t("entry.keep") : t("entry.remove")}
            </button>
        </div>
        {#if netto}
            <span class="small">
                {t("entry.saved", {
                    quantity: formatQuantity(netto),
                    unit: unitText(unit),
                })}
            </span>
        {/if}
    </div>
</div>

<style>
    .small {
        display: block;
        font-size: 0.8rem;
        color: var(--muted);
    }

    .weighing {
        padding: 0.6rem 0;
        border-bottom: 1px solid var(--border);
    }

    .weighing select {
        width: 100%;
        min-width: 0;
        margin-bottom: 0.4rem;
    }

    .input {
        display: flex;
        align-items: center;
        gap: 0.5rem;
    }

    .input input {
        flex: 1;
        min-width: 0;
        font-size: 1.2rem;
    }

    .weighing.removed {
        opacity: 0.45;
        text-decoration: line-through;
    }

    .fields {
        min-width: 0;
    }

    /* Lighter than the empty vessel: outlined red until it is right. */
    .input input.too-light {
        border-color: var(--open);
        outline: 2px solid var(--open);
    }
</style>
