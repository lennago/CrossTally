<script>
    /**
     * An article at a location, as a form, not as instant saving.
     *
     * Counting happens standing, with one hand: not every keystroke may write
     * something already. Everything stays draft until someone leaves the form:
     * "‹ Previous" and "Next ›" at the bottom like "Back" at the top (in the
     * bar as on the device) adopt it. Whoever moves on means "done" and not
     * "away with it". Discarded is only with "Cancel". A save button of its own
     * therefore does not exist.
     *
     * At the bottom one **pages along the list**: counting goes along the shelf,
     * and the detour through the list cost two handles per article. To the list
     * it goes with "Back" and with "Done" after the last article. Because form
     * follows form, the name stands large and fixed at the top: it is the only
     * thing that says it moved on.
     *
     * The keyboard may stay open in the process: the footer bar rises above it,
     * and the enter key jumps into the next field.
     *
     * **Typing happens at once.** The first field has the focus as soon as the
     * form is there, and what is in a field is selected as soon as it gets the
     * focus. The first digit thus replaces it. The suggestion stays grey in the
     * empty field ('placeholder'): it is no value, is not selected and does not
     * count. What stands black in the field is counted.
     *
     * Three ways to count, and the article says which applies: per pack a
     * count, **per vessel a weighing** (each with its own tare), or the bare
     * piece count of a counted recipe article.
     */
    import { onDestroy, tick } from "svelte";

    import { forgetDraft, saveDraft, savedDraft } from "../services/draft_backup.js";
    import {
        addPack,
        applyDraft,
        belowTare,
        draftFrom,
        emptyWeighing,
        removePack,
        restoreDraft,
        validate,
        weighingExpected,
    } from "../services/draft.svelte.js";
    import { formatQuantity, sum } from "../services/numbers.js";
    import { articles as articlesAt, inTabs, next, previous } from "../services/progress.js";
    import { guardBack } from "../services/route.svelte.js";
    import { holdKeyboard } from "../services/keyboard.js";
    import {
        jumpTo,
        problemField,
        selectOnFocus,
        targets,
        toNextField,
    } from "../services/focus.js";
    import {
        articleFromMasterData,
        containerGroupsOf,
        counting,
        loadFromStorage,
        locationFromMasterData,
        rowsOf,
    } from "../services/counting.svelte.js";
    import FooterBar from "../components/FooterBar.svelte";
    import QuantityInput from "../components/QuantityInput.svelte";
    import WeighingRow from "../components/WeighingRow.svelte";
    import { t } from "../services/language.svelte.js";
    import { packName, unitText } from "../services/units.js";

    let { locationId, articleId, done, toNext } = $props();

    let article = $derived(articleFromMasterData(articleId));
    let location = $derived(locationFromMasterData(locationId));
    let groups = $derived(containerGroupsOf(article));
    let containers = $derived(groups.flatMap((group) => group.containers));

    let draft = $state(null);
    let working = $state(false);
    let error = $state("");

    // The draft arises **once** per article. A sync in the background must
    // not overwrite the fields in the middle of typing.
    let loadedFor = $state(null);
    $effect(() => {
        const key = `${locationId}/${articleId}`;
        // Only once the state from the storage is there: otherwise an empty
        // draft would arise, and confirming would create new rows instead of
        // changing the existing ones.
        if (!counting.loaded || loadedFor === key || !article) return;
        loadedFor = key;
        draft = draftFrom(article, rowsOf(locationId, articleId));
        initial = JSON.stringify(draft);
        void checkLeftBehind(target);
    });

    // The draft is written along ('draft_backup.js'), so a crash or an empty
    // battery does not cost what was typed. 'target' says where it belongs.
    // 'initial' is the draft as it opened: if it still matches, there is
    // nothing to save.
    let target = $derived({
        inventoryId: counting.inventoryId,
        area: location?.bereich ?? "",
        locationId,
        articleId,
        label: [article?.name, location?.name].filter(Boolean).join(" · "),
    });
    let initial = null;
    //: What was typed last time and never adopted. As long as nobody has
    //: decided here, nothing is written over it.
    let leftBehind = $state(null);
    let checked = $state(false);

    async function checkLeftBehind(where) {
        leftBehind = await savedDraft(where);
        checked = true;
    }

    $effect(() => {
        if (!draft || !checked || leftBehind) return;
        const now = JSON.stringify(draft);
        if (now === initial) void forgetDraft(target);
        else void saveDraft(target, draft);
    });

    function continueDraft() {
        draft = restoreDraft(leftBehind.draft, article, rowsOf(locationId, articleId));
        leftBehind = null;
    }

    async function discardLeftBehind() {
        await forgetDraft(target);
        leftBehind = null;
    }

    // Whoever leaves the form has saved or cancelled. The copy is then
    // settled. If the app dies, this does not run, and it stays. A copy
    // nobody has decided about yet stays as well. Only on teardown, not on
    // every change of 'target': otherwise it would vanish as soon as the
    // location reloads.
    onDestroy(() => {
        if (leftBehind || !checked) return;
        const where = target;
        void forgetDraft(where);
        counting.drafts = counting.drafts.filter(
            (entry) =>
                !(
                    entry.inventoryId === where.inventoryId &&
                    entry.locationId === where.locationId &&
                    entry.articleId === where.articleId
                ),
        );
    });

    // In the tabs of the list ('inTabs'): onwards stays in the article's tab.
    let sequence = $derived(
        inTabs(articlesAt(counting.positions, locationId), counting.inventory?.inventurarten ?? []),
    );
    let after = $derived(next(sequence, articleId));
    let before = $derived(previous(sequence, articleId));

    /**
     * A further weighing row. Its vessel is next and opens.
     *
     * The holder takes the focus in the tap so the keyboard on the iPhone
     * does not close in between ('keyboard.js'). The field only exists after
     * rendering.
     */
    async function addWeighing() {
        holdKeyboard();
        draft.weighings = [...draft.weighings, emptyWeighing()];
        await tick();
        jumpTo([...(content?.querySelectorAll(".weighing select") ?? [])].at(-1));
    }

    /** Fetch a pack that was not expected here and count it right away. */
    async function fetchPack(pack) {
        holdKeyboard();
        addPack(draft, pack);
        await tick();
        jumpTo(content?.querySelector(`[data-pack="${pack.id}"] input`));
    }

    /** What usually stands here, grey in the empty field so it does not count. */
    function expectedText(row) {
        return row?.erwartete_menge ? formatQuantity(row.erwartete_menge) : "—";
    }

    function removeWeighingRow(index) {
        const entry = draft.weighings[index];
        // What was never at the server simply vanishes. Everything else is
        // noted and only removed on confirming.
        draft.weighings = entry.isNew
            ? draft.weighings.filter((_, i) => i !== index)
            : draft.weighings.map((e, i) => (i === index ? { ...e, removed: !e.removed } : e));
    }

    /** Adopt the draft. 'true' means the form may be left. */
    async function save() {
        // Nothing loaded, nothing to lose. The way back stays free.
        if (!draft) return true;
        if (working) return false;
        const message = validate(draft, containers);
        if (message) {
            error = message;
            attempted = true;
            return false;
        }
        error = "";
        working = true;
        try {
            await applyDraft(draft, { inventoryId: counting.inventoryId, location, article });
            await loadFromStorage();
            return true;
        } catch (cause) {
            error = t("error.failed", {
                reason: cause?.data?.detail ?? cause.message ?? cause,
            });
            return false;
        } finally {
            working = false;
        }
    }

    /** Save and go to 'goal'. Without goal back to the list. */
    async function leaveFor(goal) {
        if (!(await save())) {
            // The holder has the focus ('keyboard.js'). Here it stays with this
            // form: to the vessel missing for a weight, otherwise into the first
            // field.
            if (goal) {
                jumpTo(problemField(content, draft.weighings, containers) ?? targets(content)[0]);
            }
            return;
        }
        if (goal) toNext(goal.id);
        else done();
    }

    // The goal is fixed before saving: whoever removes all rows here
    // takes the article out of the list, and with it the place from which
    // the neighbour is determined. If it leads into the next form, the tap
    // keeps the keyboard open, even before saving.
    function page(goal) {
        if (goal) holdKeyboard();
        return leaveFor(goal);
    }
    const forward = () => page(after);
    const backward = () => page(before);

    let content = $state(null);

    /**
     * The first field gets the focus, once per form.
     *
     * Only once it is known whether a left-behind draft waits: then the
     * question whether it continues comes first. Without a finger tap some
     * devices (iOS) do not open the keyboard in the process. The focus sits
     * nonetheless, and the first tap into the field selects what is in it
     * ('selectAll').
     */
    let focusSet = false;
    $effect(() => {
        if (focusSet || !content || !checked || leftBehind) return;
        focusSet = true;
        void tick().then(() => jumpTo(targets(content)[0]));
    });

    //: Whether moving on was already tried in vain. Only then does a weight
    //: that is too light turn red. When typing "1250" the "1" would otherwise be briefly wrong.
    let attempted = $state(false);

    // Selecting on the focus and the enter key: 'focus.js'.
    const { selectAll, tapAfterFocus, released } = selectOnFocus();

    function enterKey(event) {
        toNextField(event, content, forward);
    }

    // "Back" only asks here and thus saves like paging.
    $effect(() => guardBack(save));

    let counted = $derived(
        draft ? sum(rowsOf(locationId, articleId).map((row) => row.menge_basiseinheit)) : 0,
    );
</script>

{#if !counting.loaded}
    <!-- While the storage loads, the article is just not there yet, and "does
         not belong to this house" would be wrong information at that moment. -->
    <p class="hint">{t("entry.loading")}</p>
{:else if !article || !draft}
    <p class="hint">{t("entry.foreign")}</p>
{:else}
    <div
        class="content"
        bind:this={content}
        onfocusin={selectAll}
        onclick={tapAfterFocus}
        onkeydown={released}
        onfocusout={released}
        role="presentation"
    >
        <header class="article-header">
            <h1>{article.name}</h1>
            <p>{t("entry.counted_in", { unit: unitText(article.einheit) })}</p>
            {#if after}
                <p class="after">{t("entry.up_next", { name: after.name })}</p>
            {/if}
        </header>

        {#if leftBehind}
            <!-- Last time the app died before the form was left. What was
                 typed is not lost, but not counted either until it is adopted. -->
            <div class="left-behind" role="alert">
                <p>{t("entry.left_behind")}</p>
                <div class="buttons">
                    <button class="green" onclick={continueDraft}>{t("entry.continue")}</button>
                    <button onclick={discardLeftBehind}>{t("entry.discard")}</button>
                </div>
            </div>
        {/if}

        {#each draft.packs as entry, index (entry.pack.id)}
            <!-- A row that does not even exist here belongs away ('onremove'),
                 otherwise it stands as uncounted in the list until closing. The
                 pack then stands at the bottom for choice, in case it was a slip.
                 The row is only gone with moving on. -->
            <QuantityInput
                pack={entry.pack.id}
                bind:quantity={draft.packs[index].quantity}
                expected={expectedText(entry.row)}
                onenter={enterKey}
                onremove={() => removePack(draft, index)}
            >
                {#snippet label(adding)}
                    <strong>{packName(entry.pack)}</strong>
                    <span class="small">
                        {t("entry.each", {
                            quantity: formatQuantity(entry.pack.faktor_in_basiseinheit),
                            unit: unitText(article.einheit),
                        })}
                        {#if entry.row?.erwartete_menge}
                            {t("entry.usual", {
                                quantity: formatQuantity(entry.row.erwartete_menge),
                            })}
                        {/if}
                        {#if !entry.row}{t("entry.unexpected")}{/if}
                        {#if adding}
                            <span class="adding">{adding}</span>
                        {/if}
                    </span>
                {/snippet}
            </QuantityInput>
        {/each}

        {#if draft.morePacks.length}
            <!-- Only what is expected here stands at the top. The rest is added on
                 request. The row then arises as unexpected. -->
            <div class="extras">
                <span class="small">{t("entry.another_pack")}</span>
                {#each draft.morePacks as pack (pack.id)}
                    <button class="flat" onclick={() => fetchPack(pack)}>
                        + {packName(pack)}
                    </button>
                {/each}
            </div>
        {/if}

        {#if article.wird_gewogen && !draft.weighings.length}
            <!-- At this location the product is not weighed. Like a pack that is
                 not expected here, the scale is only added on request. -->
            <div class="extras">
                <span class="small">{t("entry.not_weighed")}</span>
                <button class="flat" onclick={addWeighing}>{t("entry.add_weighing")}</button>
            </div>
        {:else if article.wird_gewogen}
            <section class="block">
                <h2>
                    {t("entry.weighing")}
                    {#if !weighingExpected(article, draft.scale)}
                        <span class="small">{t("entry.weighing_unexpected")}</span>
                    {/if}
                </h2>
                {#each draft.weighings as entry, index (entry.weighing?.id ?? `new-${index}`)}
                    <WeighingRow
                        bind:container={draft.weighings[index].container}
                        bind:gross={draft.weighings[index].gross}
                        removed={entry.removed}
                        netto={entry.weighing?.netto}
                        {groups}
                        unit={article.einheit}
                        tooLight={attempted && belowTare(entry, containers)}
                        onenter={enterKey}
                        onremove={() => removeWeighingRow(index)}
                    />
                {/each}

                <button class="flat wide" onclick={addWeighing}
                    >{t("entry.another_weighing")}</button
                >
                <p class="small">{t("entry.tare_note")}</p>
            </section>
        {:else if !article.gebinde.length}
            <QuantityInput
                bind:quantity={draft.pieceCount}
                expected={expectedText(draft.piece)}
                onenter={enterKey}
            >
                {#snippet label(adding)}
                    <span>
                        <strong>{t("entry.count")}</strong>
                        <span class="small"
                            >{t("entry.in_unit", {
                                unit: unitText(article.einheit),
                            })}
                            {#if adding}
                                <span class="adding">{adding}</span>
                            {/if}</span
                        >
                    </span>
                {/snippet}
            </QuantityInput>
        {/if}

        {#if error}<p class="error">{error}</p>{/if}

        {#if counted && !article.ist_rezepturartikel}
            <!-- For the recipe article a sum in its own unit says nothing: it
                 is only distributed through the recipe at closing. -->
            <p class="sum small">
                {t("entry.so_far", {
                    quantity: formatQuantity(counted),
                    unit: unitText(article.einheit),
                })}
            </p>
        {/if}
    </div>

    <FooterBar {content} {working} {before} {after} onprevious={backward} onnext={forward} />
{/if}

<style>
    .content {
        padding-bottom: 5.5rem;
    }

    /* Stays when scrolling: with many packs or weighings nobody would
       otherwise know any more which article is currently open. */
    header {
        position: sticky;
        top: calc(var(--header-height) + var(--banner-height) + env(safe-area-inset-top));
        z-index: 1;
        padding: 0.85rem 1rem 0.85rem calc(1rem - 5px);
        border-left: 5px solid var(--accent);
        border-bottom: 1px solid var(--border);
        background: var(--selected);
    }

    header h1 {
        margin: 0;
        font-size: 1.5rem;
        line-height: 1.2;
        overflow-wrap: anywhere;
    }

    header .after {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    header p {
        margin: 0.25rem 0 0;
        color: var(--muted);
        font-size: 0.85rem;
    }

    .adding {
        display: block;
        color: var(--accent);
    }

    .extras {
        padding: 0.5rem 1rem 0;
    }

    .left-behind {
        margin: 0.5rem 1rem;
        padding: 0.6rem 0.8rem;
        border-radius: 0.5rem;
        background: var(--notice);
        font-size: 0.9rem;
    }

    .left-behind p {
        margin: 0 0 0.5rem;
    }

    .left-behind .buttons {
        display: flex;
        gap: 0.5rem;
    }

    .left-behind button {
        flex: 1;
        min-height: 2.75rem;
    }

    .left-behind .green {
        font-weight: 600;
        color: #fff;
        background: var(--done);
        border-color: var(--done);
    }

    .block h2 .small {
        display: inline;
        margin-left: 0.4rem;
        font-weight: 400;
    }

    .small {
        display: block;
        font-size: 0.8rem;
        color: var(--muted);
    }

    .block {
        padding: 0.5rem 1rem 1rem;
        border-top: 1px solid var(--border);
    }

    .block h2 {
        font-size: 1rem;
    }

    .wide {
        width: 100%;
        margin-top: 0.75rem;
        border: 1px dashed var(--border);
    }

    .sum {
        padding: 1rem;
    }

    .error {
        margin: 0;
        padding: 0.5rem 1rem;
        color: var(--open);
    }

    .hint {
        padding: 1rem;
        color: var(--muted);
    }
</style>
