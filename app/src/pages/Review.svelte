<script>
    /**
     * The review list: per article what closing would freeze.
     *
     * The same rows as on the website ('pruefliste' of the API), for everyone
     * who counts here, counters too: whoever sees where the count deviates can
     * count again before anyone closes. Built for the phone, so per article
     * only what is looked for: the name, the difference large and in its
     * colour, below it actual and target. The product group stays out.
     *
     * **The note stays hidden.** A pen per row opens the field with it, and
     * an × beside the pen says that there is one and removes it. Otherwise
     * two hundred notes would push the numbers apart.
     *
     * **With the difference by hand** ('differenz_von_hand') the difference is
     * a field and the target goes: there is none. Whoever counts here types
     * it in. Whether it is typed at all, a manager decides, here as on the
     * website.
     *
     * Notes and differences go through the queue like counts: shown at once,
     * sent once there is network. The list itself comes only on request, when
     * the page opens and with the button in the header. As on the website,
     * the numbers do not move while someone reads them.
     *
     * **A difference is taken over when its field is left**, and in addition
     * when the app goes to the background or the page is left with the cursor
     * still in a field ('typed'). Until 10/2026 such a value was lost when the
     * phone locked or the system ended the app in the background.
     *
     * **Locked** ('gesperrt'), counting has stopped and the check begins: the
     * inventory then opens here ('App.svelte'). Notes and differences go on.
     * A manager locks and opens it here as on the website.
     *
     * **"Target counts"** ('soll_statt_ist') stands at every row with a target,
     * as a button for managers. At closing the target then becomes the result,
     * the count stays as it is until then. Only managers switch it, as on the
     * website: a counter could hide a shortage with it. Counters see that it
     * is set. Through the queue like a note.
     */
    import { onDestroy, onMount, untrack } from "svelte";

    import { counting, loadFromStorage, refresh } from "../services/counting.svelte.js";
    import {
        enterDifference,
        enterNote,
        enterTargetCounts,
        switchLock,
        switchManualDifference,
    } from "../services/sync.js";
    import {
        asNumberText,
        formatQuantity,
        hasTooManyDecimals,
        isTooLarge,
    } from "../services/numbers.js";
    import { unitText } from "../services/units.js";
    import { locale, t } from "../services/language.svelte.js";

    let { manager = false } = $props();

    //: What the list shows: everything, deviations, uncounted, with a note.
    const FILTERS = ["all", "deviating", "uncounted", "notes"];
    const FILTER_TEXT = {
        all: "review.all",
        deviating: "review.deviating",
        uncounted: "review.uncounted",
        notes: "review.with_note",
    };

    let term = $state("");
    let filter = $state("all");
    //: The article whose note field is open, and what is typed in it.
    let editing = $state(null);
    let draft = $state("");
    //: The row whose difference is no number, until it is.
    let invalid = $state(null);
    let switching = $state(false);
    let locking = $state(false);
    let message = $state("");

    //: Differences typed but not taken over yet: the article, its field and its row.
    //: Leaving the field takes them over ('change'). Going to the background or
    //: leaving the page does it too ('takeOverTyped').
    const typed = new Map();

    function takeOverTyped() {
        const waiting = [...typed.values()];
        typed.clear();
        for (const { row, field } of waiting) void saveDifference(row, field);
    }

    function intoBackground() {
        if (document.visibilityState === "hidden") takeOverTyped();
    }

    onMount(() => {
        document.addEventListener("visibilitychange", intoBackground);
        window.addEventListener("pagehide", takeOverTyped);
    });

    // The review list is asked for whenever another inventory opens here, not
    // only on mounting: the page stays mounted from one locked inventory to
    // the next. 'untrack' keeps the sync's own state out of the dependencies,
    // otherwise every finished sync would ask for the next one.
    $effect(() => {
        const id = counting.inventoryId;
        if (id !== null) untrack(() => void refresh({ review: true }));
    });

    // Left through the app, the field is gone before it could be left. Its
    // value is still readable, and saving does not need the page any more.
    onDestroy(() => {
        document.removeEventListener("visibilitychange", intoBackground);
        window.removeEventListener("pagehide", takeOverTyped);
        takeOverTyped();
    });

    let review = $derived(counting.review);
    let manual = $derived(
        review?.differenz_von_hand ?? counting.inventory?.differenz_von_hand ?? false,
    );
    let withTarget = $derived(
        review?.hat_sollbestand ?? counting.inventory?.hat_sollbestand ?? false,
    );
    let rows = $derived([...(review?.zeilen ?? [])].sort(byNumber));
    let shown = $derived(rows.filter((row) => matches(row, term) && passes(row, filter)));
    // "Target counts" is explained above the list, where a phone shows it:
    // to managers as soon as a target stands (they can switch it), to
    // counters as soon as a row carries it. Until 10/2026 it was a tooltip.
    let targetHint = $derived(
        !manual && (manager ? withTarget : rows.some((row) => row.soll_statt_ist)),
    );
    let deviating = $derived(rows.filter(deviates).length);
    let uncounted = $derived(rows.filter((row) => !row.gezaehlt).length);
    let asOf = $derived(review?.stand ? asOfText(review.stand) : "");

    /**
     * When the server computed the list, with the day where it is not today.
     * The list lies on the device, and until 10/2026 one from yesterday read
     * "as of 18:30" like one of now.
     */
    function asOfText(stand) {
        const when = new Date(stand);
        const time = { hour: "2-digit", minute: "2-digit" };
        if (when.toDateString() === new Date().toDateString()) {
            return when.toLocaleTimeString(locale(), time);
        }
        return when.toLocaleString(locale(), { day: "2-digit", month: "2-digit", ...time });
    }

    /**
     * By article number, as the website lists them and the merchandise management
     * system: whoever carries the numbers over finds every row in the same place.
     * Numbers of different length compare as numbers. Own articles without a
     * number come last, by name.
     */
    function byNumber(left, right) {
        const first = left.nummer ?? "";
        const second = right.nummer ?? "";
        if (!first !== !second) return first ? -1 : 1;
        return (
            first.localeCompare(second, undefined, { numeric: true }) ||
            left.name.localeCompare(right.name, locale())
        );
    }

    /**
     * The server's verdict, on the difference rounded like the unit, as the
     * website counts it ('auffaellig'). Until 10/2026 the page read the number
     * itself, and a difference by hand of 0.4 pieces deviated here and not
     * there. A difference typed here that has not reached the server has no
     * verdict yet, and an older server sends none: then the number decides.
     */
    function deviates(row) {
        if (typeof row.auffaellig === "boolean") return row.auffaellig;
        return row.differenz !== null && row.differenz !== undefined && Number(row.differenz) !== 0;
    }

    function passes(row, chosen) {
        if (chosen === "deviating") return deviates(row);
        if (chosen === "uncounted") return !row.gezaehlt;
        if (chosen === "notes") return !!row.notiz;
        return true;
    }

    /** By product name, the house's designation and the number, as in the search. */
    function matches(row, text) {
        const wanted = text.trim().toLocaleLowerCase(locale());
        if (!wanted) return true;
        return [row.name, row.name_im_haus, row.nummer].some((part) =>
            String(part ?? "")
                .toLocaleLowerCase(locale())
                .includes(wanted),
        );
    }

    /** Red where something is missing, yellow where there is more, green where it fits. */
    function tone(value) {
        if (value === null || value === undefined || value === "") return "";
        const number = Number(value);
        if (number < 0) return "missing";
        return number > 0 ? "more" : "fits";
    }

    /** A difference with its sign: "+12" and "-12" are two different messages. */
    function signed(value) {
        if (value === null || value === undefined || value === "") return "–";
        const text = formatQuantity(value);
        return Number(value) > 0 ? `+${text}` : text;
    }

    /** Whether the row has a target that could count. Not with the difference by hand. */
    function hasTarget(row) {
        return !manual && row.soll !== null && row.soll !== undefined;
    }

    async function switchTarget(row) {
        await enterTargetCounts(counting.inventoryId, row.hausartikel, !row.soll_statt_ist);
        await loadFromStorage();
    }

    function openNote(row) {
        if (editing === row.hausartikel) {
            editing = null;
            return;
        }
        editing = row.hausartikel;
        draft = row.notiz ?? "";
    }

    async function saveNote(row) {
        const text = draft.trim();
        editing = null;
        if (text === (row.notiz ?? "")) return;
        await enterNote(counting.inventoryId, row.hausartikel, text);
        await loadFromStorage();
    }

    async function removeNote(row) {
        if (!window.confirm(t("review.note_remove_sure", { name: row.name }))) return;
        if (editing === row.hausartikel) editing = null;
        await enterNote(counting.inventoryId, row.hausartikel, "");
        await loadFromStorage();
    }

    async function saveDifference(row, field) {
        typed.delete(row.hausartikel);
        const text = asNumberText(field.value);
        // What the server would refuse stays out, as in the entry form.
        const problem =
            text === undefined
                ? "error.numbers_only"
                : hasTooManyDecimals(text)
                  ? "error.decimals"
                  : isTooLarge(text)
                    ? "error.too_large"
                    : null;
        if (problem) {
            invalid = row.hausartikel;
            message = t(problem);
            return;
        }
        if (invalid === row.hausartikel) {
            invalid = null;
            message = "";
        }
        const before = row.differenz ?? null;
        const same =
            text === null ? before === null : before !== null && Number(text) === Number(before);
        if (same) return;
        await enterDifference(counting.inventoryId, row.hausartikel, text);
        await loadFromStorage();
    }

    /**
     * Turn the sign of a difference: "12" becomes "-12" and back.
     *
     * A number is taken over at once, like on leaving the field. Only the sign
     * so far waits for its number, and leaving the field or the app takes it.
     */
    function switchSign(row, field) {
        const value = field.value.trim();
        field.value = value.startsWith("-") ? value.slice(1) : `-${value}`;
        field.focus();
        if (!asNumberText(field.value)) {
            typed.set(row.hausartikel, { row, field });
            return;
        }
        void saveDifference(row, field);
    }

    //: Counting stopped for the check. The list goes on, the walk through the house does not.
    let locked = $derived(counting.inventory?.gesperrt === true);

    async function switchLocked() {
        locking = true;
        message = "";
        try {
            await switchLock(counting.inventoryId, !locked);
        } catch (error) {
            message =
                error?.status === 0
                    ? t("review.manual_offline")
                    : [error?.data?.detail ?? t("error.failed", { reason: t("error.server") })]
                          .flat()
                          .join(" ");
        } finally {
            locking = false;
            await loadFromStorage();
        }
    }

    async function switchManual(field) {
        const wanted = field.checked;
        switching = true;
        message = "";
        try {
            await switchManualDifference(counting.inventoryId, wanted);
            await refresh({ review: true });
        } catch (error) {
            // The box says what applies, not what was tapped.
            field.checked = manual;
            // The server names the reason as a list ('detail').
            message =
                error?.status === 0
                    ? t("review.manual_offline")
                    : [error?.data?.detail ?? t("error.failed", { reason: t("error.server") })]
                          .flat()
                          .join(" ");
        } finally {
            switching = false;
            await loadFromStorage();
        }
    }
</script>

<div class="header">
    <div class="search">
        <input
            type="search"
            bind:value={term}
            placeholder={t("search.title")}
            autocapitalize="none"
        />
    </div>
    <div class="filters" role="group" aria-label={t("review.filters")}>
        {#each FILTERS as choice (choice)}
            <button
                class:chosen={filter === choice}
                aria-pressed={filter === choice}
                onclick={() => (filter = choice)}
            >
                {t(FILTER_TEXT[choice])}
            </button>
        {/each}
    </div>
    {#if review}
        <p class="summary">
            {t("review.summary", { n: rows.length, deviating, open: uncounted })}
            <span class="as-of">
                · {counting.online
                    ? t("review.as_of", { time: asOf })
                    : t("review.as_of_offline", { time: asOf })}
            </span>
        </p>
    {/if}
</div>

<!-- Everyone sees the lock, a manager switches it. The icon shows the
     state, the word what the button does, as on the website. -->
{#if locked || manager}
    <div class="lock" class:locked>
        {#if locked}
            <p>{t("review.locked")}</p>
        {/if}
        {#if manager}
            <button type="button" disabled={locking} onclick={switchLocked}>
                <!-- Drawn: a character sits differently in every font. -->
                <svg viewBox="0 0 16 16" width="18" height="18" aria-hidden="true">
                    <rect x="3" y="7" width="10" height="8" rx="1.5" fill="currentColor" />
                    <path
                        d={locked ? "M5 7V5a3 3 0 0 1 6 0v2" : "M11 4.5a3 3 0 0 0-6 0V7"}
                        fill="none"
                        stroke="currentColor"
                        stroke-width="1.6"
                    />
                </svg>
                {locked ? t("review.unlock") : t("review.lock")}
            </button>
        {/if}
    </div>
{/if}

<!-- Only where it can be switched: with a target stock the difference
     computes itself, as on the website. -->
{#if manager && (manual || !withTarget)}
    <div class="manual">
        <label>
            <input
                type="checkbox"
                checked={manual}
                disabled={switching}
                onchange={(event) => switchManual(event.currentTarget)}
            />
            {t("review.manual")}
        </label>
        <p class="small">{t("review.manual_hint")}</p>
    </div>
{:else if manual}
    <p class="manual small">{t("review.manual_counter")}</p>
{/if}

{#if message}
    <p class="message" role="alert">{message}</p>
{/if}

{#if !review}
    <p class="hint">{counting.loading ? t("app.loading") : t("review.not_loaded")}</p>
{:else}
    {#if targetHint}
        <p class="hint small">{t("review.target_counts_hint")}</p>
    {/if}
    <ul class="rows">
        {#each shown as row (row.hausartikel)}
            <li class="row" class:open={editing === row.hausartikel}>
                <span class="name">
                    <strong>{row.name}</strong>
                    {#if !row.aktiv}<span class="badge">{t("search.inactive")}</span>{/if}
                </span>
                <span class="difference">
                    {#if manual}
                        <!-- The number pad of a phone has no minus, and a difference
                             is mostly one. The button keeps the field and its keyboard
                             ('mousedown' does not take the focus). -->
                        <button
                            class="sign"
                            type="button"
                            aria-label={t("review.sign_of", { name: row.name })}
                            onmousedown={(event) => event.preventDefault()}
                            onclick={(event) =>
                                switchSign(
                                    row,
                                    event.currentTarget
                                        .closest(".difference")
                                        .querySelector("input"),
                                )}>±</button
                        >
                        <input
                            class={tone(row.differenz)}
                            class:wrong={invalid === row.hausartikel}
                            type="text"
                            inputmode="decimal"
                            autocomplete="off"
                            value={formatQuantity(row.differenz)}
                            placeholder="–"
                            aria-label={t("review.difference_of", { name: row.name })}
                            aria-invalid={invalid === row.hausartikel}
                            oninput={(event) =>
                                typed.set(row.hausartikel, { row, field: event.currentTarget })}
                            onchange={(event) => saveDifference(row, event.currentTarget)}
                            onkeydown={(event) => {
                                if (event.key === "Enter") event.currentTarget.blur();
                            }}
                        />
                    {:else}
                        <strong class={tone(row.differenz)}>{signed(row.differenz)}</strong>
                    {/if}
                </span>
                <!-- Each part stays whole: next to "Soll zählt" the line wraps
                     between number, actual and target, not inside "Soll 120". -->
                <span class="meta">
                    {#if row.nummer}<span class="part"
                            >{t("review.number", { number: row.nummer })}</span
                        > ·{/if}
                    <span class="part"
                        >{t("review.actual")}
                        <span class="value">{formatQuantity(row.ist)}</span>
                        {unitText(row.einheit)}</span
                    >
                    {#if !manual}
                        ·
                        <span class="part"
                            >{t("review.target")}
                            <span class="value"
                                >{row.soll === null ? "–" : formatQuantity(row.soll)}</span
                            ></span
                        >
                    {/if}
                    {#if !row.gezaehlt}
                        · <span class="badge">{t("review.not_counted")}</span>
                    {/if}
                    {#if !manager && hasTarget(row) && row.soll_statt_ist}
                        · <span class="badge">{t("review.target_counts")}</span>
                    {/if}
                </span>
                <span class="actions">
                    {#if manager && hasTarget(row)}
                        <button
                            class="target"
                            class:on={row.soll_statt_ist}
                            aria-pressed={row.soll_statt_ist === true}
                            aria-label={t("review.target_counts_of", { name: row.name })}
                            onclick={() => switchTarget(row)}
                        >
                            {t("review.target_counts")}
                        </button>
                    {/if}
                    <button
                        class="icon"
                        aria-label={t("review.note_of", { name: row.name })}
                        aria-expanded={editing === row.hausartikel}
                        onclick={() => openNote(row)}
                    >
                        <!-- A pen, drawn: a character sits differently in every font. -->
                        <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
                            <path
                                d="M4 20h4L19 9l-4-4L4 16v4zM14 6l4 4"
                                fill="none"
                                stroke="currentColor"
                                stroke-width="2"
                                stroke-linejoin="round"
                            />
                        </svg>
                    </button>
                    {#if row.notiz}
                        <button
                            class="icon remove"
                            aria-label={t("review.note_remove", { name: row.name })}
                            onclick={() => removeNote(row)}
                        >
                            <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                                <path
                                    d="M6 6l12 12M18 6L6 18"
                                    stroke="currentColor"
                                    stroke-width="2.5"
                                    stroke-linecap="round"
                                />
                            </svg>
                        </button>
                    {/if}
                </span>
                {#if editing === row.hausartikel}
                    <div class="note">
                        <!-- The field opens because someone tapped the pencil to type. -->
                        <!-- svelte-ignore a11y_autofocus -->
                        <!-- The server keeps at most 2000 characters and refuses more. -->
                        <textarea
                            bind:value={draft}
                            rows="3"
                            maxlength="2000"
                            autofocus
                            aria-label={t("review.note_of", { name: row.name })}></textarea>
                        <div class="buttons">
                            <button class="flat" onclick={() => (editing = null)}>
                                {t("header.cancel")}
                            </button>
                            <button class="save" onclick={() => saveNote(row)}>
                                {t("review.note_save")}
                            </button>
                        </div>
                    </div>
                {/if}
            </li>
        {:else}
            <li class="hint">{rows.length ? t("search.no_match") : t("review.empty")}</li>
        {/each}
    </ul>
{/if}

<style>
    .header {
        position: sticky;
        top: calc(var(--header-height) + var(--banner-height));
        z-index: 1;
        background: var(--background);
        border-bottom: 1px solid var(--border);
    }

    .search {
        padding: 0.75rem 1rem 0.5rem;
    }

    .search input {
        width: 100%;
    }

    /* One row of buttons, as wide as the screen: whoever looks for the
       deviations should reach them with the thumb. */
    .filters {
        display: flex;
        gap: 0.25rem;
        padding: 0 1rem 0.5rem;
    }

    .filters button {
        flex: 1;
        min-height: 2.5rem;
        padding: 0.3rem 0.25rem;
        font-size: 0.85rem;
        color: var(--muted);
    }

    .filters button.chosen {
        border-color: var(--accent);
        color: var(--text);
        font-weight: 600;
        background: var(--selected);
    }

    .summary {
        margin: 0;
        padding: 0 1rem 0.6rem;
        font-size: 0.8rem;
        color: var(--muted);
    }

    .lock {
        display: flex;
        align-items: center;
        gap: 0.75rem;
        padding: 0.6rem 1rem;
        border-bottom: 1px solid var(--border);
        background: var(--surface);
    }

    .lock.locked {
        background: var(--notice);
    }

    .lock p {
        flex: 1;
        margin: 0;
        font-size: 0.85rem;
    }

    .lock button {
        display: inline-flex;
        align-items: center;
        gap: 0.4rem;
        min-height: 2.5rem;
        margin-left: auto;
        padding: 0.3rem 0.9rem;
    }

    .manual {
        margin: 0;
        padding: 0.6rem 1rem;
        border-bottom: 1px solid var(--border);
        background: var(--surface);
    }

    .manual label {
        display: flex;
        align-items: center;
        gap: 0.6rem;
        font-weight: 600;
    }

    .manual input {
        width: 1.25rem;
        height: 1.25rem;
    }

    .manual p {
        margin: 0.25rem 0 0;
    }

    .small {
        font-size: 0.8rem;
        color: var(--muted);
    }

    .message {
        margin: 0;
        padding: 0.5rem 1rem;
        background: var(--notice);
        font-size: 0.85rem;
    }

    .rows {
        list-style: none;
        margin: 0;
        padding: 0;
    }

    /* Two lines per article: name and difference on top, below what it
       comes from and the pen. The difference stands where the eye runs
       down the list. */
    .row {
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        grid-template-areas:
            "name difference"
            "meta actions";
        align-items: center;
        column-gap: 0.75rem;
        padding: 0.6rem 1rem 0.4rem;
        border-bottom: 1px solid var(--border);
        background: var(--surface);
    }

    .row.open {
        grid-template-areas:
            "name difference"
            "meta actions"
            "note note";
    }

    .name {
        grid-area: name;
        min-width: 0;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    .difference {
        grid-area: difference;
        display: flex;
        align-items: center;
        justify-content: flex-end;
        gap: 0.25rem;
        font-size: 1.15rem;
        font-variant-numeric: tabular-nums;
    }

    .difference .sign {
        min-height: 2.5rem;
        padding: 0 0.6rem;
        font-weight: 600;
        color: var(--accent);
    }

    .difference input {
        width: 6rem;
        padding: 0.35rem 0.5rem;
        text-align: right;
        font-weight: 600;
    }

    .difference input.wrong {
        border-color: var(--open);
        outline: 2px solid var(--open);
    }

    .meta {
        grid-area: meta;
        min-width: 0;
        font-size: 0.8rem;
        color: var(--muted);
    }

    .value {
        color: var(--text);
        font-variant-numeric: tabular-nums;
    }

    .part {
        white-space: nowrap;
    }

    .badge {
        display: inline-block;
        margin-left: 0.3rem;
        padding: 0 0.35rem;
        border: 1px solid var(--border);
        border-radius: 0.3rem;
        font-size: 0.75rem;
        font-weight: 400;
        color: var(--muted);
    }

    .actions {
        grid-area: actions;
        display: flex;
        justify-content: flex-end;
        gap: 0.25rem;
    }

    /* Like a filter button: muted while off, in the accent colour once the
       target counts. The state is the information, not the action. */
    .target {
        align-self: center;
        min-height: 2.5rem;
        padding: 0.15rem 0.5rem;
        font-size: 0.75rem;
        white-space: nowrap;
        color: var(--muted);
    }

    .target.on {
        border-color: var(--accent);
        color: var(--text);
        font-weight: 600;
        background: var(--selected);
    }

    .icon {
        display: grid;
        place-items: center;
        width: 2.75rem;
        min-height: 2.5rem;
        padding: 0;
        border-color: transparent;
        background: none;
        color: var(--accent);
    }

    .icon.remove {
        color: var(--muted);
    }

    .note {
        grid-area: note;
        padding: 0.25rem 0 0.5rem;
    }

    .note textarea {
        display: block;
        width: 100%;
        padding: 0.5rem 0.75rem;
        border: 1px solid var(--border);
        border-radius: 0.5rem;
        background: var(--background);
        color: inherit;
        font: inherit;
        font-size: max(1em, 16px);
    }

    .note .buttons {
        display: flex;
        justify-content: flex-end;
        gap: 0.5rem;
        margin-top: 0.4rem;
    }

    .note .save {
        font-weight: 600;
        color: #fff;
        background: var(--accent);
        border-color: var(--accent);
    }

    /* The same colours as on the website: red is missing, yellow is more,
       green fits. */
    .missing {
        color: var(--open);
    }

    .more {
        color: var(--partial);
    }

    .fits {
        color: var(--done);
    }

    .hint {
        padding: 1rem;
        color: var(--muted);
    }
</style>
