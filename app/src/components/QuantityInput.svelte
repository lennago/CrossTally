<script>
    /**
     * A count field: the number of a pack, or the piece count of a recipe article.
     *
     * The entry form has one per pack and one for the piece count. What
     * stands left of the field the form says ('label', it gets the text of
     * the adding below), and whether the row can go ('onremove'). The field
     * writes into the draft ('quantity'). The expectation stands grey in the
     * empty field ('expected'), and the enter key belongs to the form
     * ('onenter').
     */
    import { tick } from "svelte";

    import { jumpTo } from "../services/focus.js";
    import { holdKeyboard } from "../services/keyboard.js";
    import { t } from "../services/language.svelte.js";
    import {
        addQuantities,
        asNumberText,
        formatQuantity,
        subtractQuantities,
    } from "../services/numbers.js";

    let { quantity = $bindable(), expected, pack, onenter, onremove, label } = $props();

    let row = $state(null);

    /**
     * The field that adds instead of overwriting.
     *
     * A second find of the same pack, say a crate behind the door: whoever
     * has already typed 12 should not have to add 3 in their head. "+ add"
     * next to the field empties it, the typed find is added to what stood
     * there ('base'), and the sum goes into the draft **with every keystroke**.
     * The draft never holds the find alone: the copy that is written along
     * ('draft_backup.js') is then right even if the app dies in between,
     * and 'applyDraft' knows nothing of the mode.
     *
     * 'base' is the text of the field, not the row from the server: "+ add"
     * twice adds two finds one after the other. Every field has its own
     * mode. The mode ends with leaving the field, then the field shows
     * the sum. Overwriting stays the default: tapping into the field and
     * typing replaces, as before.
     *
     * **"− off" takes away the same way** ('subtracting'): a crate that went
     * out to the counter after it was counted, or three bottles counted
     * twice. The keyboard for numbers has no minus on the phone, so it is a
     * button of its own. Below zero it does not go: the difference stands in
     * the draft as it comes out, and 'validate' rejects it.
     */
    let adding = $state(null);

    /** Whether "+ add" is offered: only for a number that is already there. */
    let addable = $derived(!adding && Boolean(asNumberText(quantity)));

    /** Whether "− off" is offered: only where something stands to take from. */
    let subtractable = $derived(addable && Number(asNumberText(quantity)) > 0);

    async function startAdding(subtracting = false) {
        holdKeyboard();
        adding = { base: quantity, addend: "", subtracting };
        await tick();
        jumpTo(row?.querySelector("input"));
    }

    /** What the draft gets: the result, or the typo itself so 'validate' sees it. */
    function total() {
        if (asNumberText(adding.addend) === undefined) return adding.addend;
        const compute = adding.subtracting ? subtractQuantities : addQuantities;
        return formatQuantity(compute(asNumberText(adding.base), asNumberText(adding.addend)));
    }

    function addTyped(event) {
        if (!adding) return;
        adding.addend = event.currentTarget.value;
        quantity = total();
    }

    function stopAdding() {
        adding = null;
    }

    function addingText() {
        const base = formatQuantity(asNumberText(adding.base));
        if (!String(adding.addend).trim()) {
            return adding.subtracting
                ? t("entry.subtract_from", { base })
                : t("entry.add_to", { base });
        }
        const parts = { base, addend: adding.addend, sum: total() };
        return adding.subtracting ? t("entry.subtract_sum", parts) : t("entry.add_sum", parts);
    }
</script>

<div class="count-row" data-pack={pack} bind:this={row}>
    <label>
        {@render label(adding ? addingText() : null)}
        {#if adding}
            <input
                type="text"
                inputmode="decimal"
                enterkeyhint="next"
                onkeydown={onenter}
                value={adding.addend}
                oninput={addTyped}
                onfocusout={stopAdding}
            />
        {:else}
            <input
                type="text"
                inputmode="decimal"
                enterkeyhint="next"
                onkeydown={onenter}
                placeholder={expected}
                bind:value={quantity}
            />
        {/if}
    </label>
    {#if onremove || addable}
        <span class="actions">
            {#if addable}
                <span class="plus-minus">
                    <button class="flat" onclick={() => startAdding()}>
                        {t("entry.add")}
                    </button>
                    {#if subtractable}
                        <button class="flat" onclick={() => startAdding(true)}>
                            {t("entry.subtract")}
                        </button>
                    {/if}
                </span>
            {/if}
            {#if onremove}
                <button class="flat" onclick={onremove}>
                    {t("entry.remove")}
                </button>
            {/if}
        </span>
    {/if}
</div>

<style>
    .count-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1rem;
        padding: 0.75rem 1rem;
        border-bottom: 1px solid var(--border);
    }

    .count-row label {
        display: flex;
        flex: 1;
        min-width: 0;
        align-items: center;
        justify-content: space-between;
        gap: 1rem;
    }

    .count-row input {
        width: 5rem;
        font-size: 1.35rem;
        text-align: right;
    }

    /* "+ add" above "remove", at the right edge: side by side they would
       squeeze the field on a narrow phone. */
    .count-row .actions {
        display: flex;
        flex-direction: column;
        align-items: flex-end;
    }

    /* Plus and minus belong together. One above the other like the rest:
       side by side they lay over the field on a narrow phone. Only where
       the screen is wide enough do they stand next to each other. */
    .plus-minus {
        display: flex;
        flex-direction: column;
        align-items: flex-end;
    }

    @media (min-width: 26rem) {
        .plus-minus {
            flex-direction: row;
        }
    }
</style>
