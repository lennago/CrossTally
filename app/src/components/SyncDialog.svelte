<script>
    /**
     * What still waits outside and what the server did not accept.
     *
     * In storage offline is the normal case. Then counted things lie here and
     * go out later. Whoever cannot check that does not know in the end whether
     * their value arrived. Rejected things do not vanish by themselves: a
     * quantity would otherwise be missing in the end, and nobody would learn of it.
     *
     * At the very top stand the rows in which someone else had already
     * counted. There lies a number someone has to decide about.
     */
    import Dialog from "./Dialog.svelte";
    import {
        discardRejected,
        jobKind,
        pendingJobs,
        quantityConflicts,
        rejectedJobs,
        resolveQuantityConflict,
    } from "../services/sync.js";
    import { counting, loadFromStorage, refresh } from "../services/counting.svelte.js";
    import { addQuantities, formatQuantity } from "../services/numbers.js";
    import { locale, t } from "../services/language.svelte.js";

    let { close } = $props();

    let jobs = $state([]);
    let rejected = $state([]);
    let conflicts = $state([]);
    let running = $state(false);

    load();

    async function load() {
        jobs = await pendingJobs();
        rejected = await rejectedJobs();
        conflicts = await quantityConflicts();
    }

    async function resolve(conflict, choice) {
        await resolveQuantityConflict(conflict.id, choice);
        await loadFromStorage();
        await load();
    }

    function quantityText(value) {
        return formatQuantity(value) || t("sync.empty");
    }

    /** The number with its pack, where the conflict knows one: "14 Kasten". */
    function withPack(value, conflict) {
        return [quantityText(value), conflict.pack].filter(Boolean).join(" ");
    }

    /** The number a job carries: grams for a weighing, "leer" for a cleared quantity. */
    function valueText(entry) {
        if (entry.unit === "g") return t("sync.weight", { quantity: formatQuantity(entry.value) });
        return quantityText(entry.value);
    }

    async function syncNow() {
        running = true;
        try {
            await refresh();
        } finally {
            running = false;
            await load();
        }
    }

    async function discard() {
        await discardRejected();
        await loadFromStorage();
        await load();
    }

    function timeText(timestamp) {
        if (!timestamp) return t("sync.never");
        return new Date(timestamp).toLocaleString(locale(), {
            day: "2-digit",
            month: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
        });
    }
</script>

<Dialog label={t("sync.title")} closeText={t("sync.close")} {close}>
    <p class="small status">
        {counting.online ? t("sync.online") : t("sync.offline")} ·
        {t("sync.last", { time: timeText(counting.lastSynced) })}
    </p>

    <div class="list">
        {#if conflicts.length}
            <h2 class="warning">{t("sync.conflicts", { n: conflicts.length })}</h2>
            <p class="small">{t("sync.conflicts_text")}</p>
            {#each conflicts as conflict (conflict.id)}
                <div class="item conflict">
                    <strong>{conflict.label}</strong>
                    <span class="small">
                        {conflict.theirs == null
                            ? t("sync.theirs_empty")
                            : t("sync.theirs", { quantity: withPack(conflict.theirs, conflict) })}
                        {t("sync.mine", { quantity: withPack(conflict.mine, conflict) })}
                    </span>
                    <div class="choice">
                        <!-- Add only where there are two numbers. Otherwise it would mean
                             the same as overwrite or discard. -->
                        {#if conflict.theirs != null && conflict.mine != null}
                            <button onclick={() => resolve(conflict, "add")}>
                                {t("sync.add", {
                                    quantity: quantityText(
                                        addQuantities(conflict.theirs, conflict.mine),
                                    ),
                                })}
                            </button>
                        {/if}
                        <button onclick={() => resolve(conflict, "discard")}>
                            {conflict.theirs == null
                                ? t("sync.discard_empty")
                                : t("sync.discard", { quantity: quantityText(conflict.theirs) })}
                        </button>
                        <button onclick={() => resolve(conflict, "overwrite")}>
                            {t("sync.overwrite", { quantity: quantityText(conflict.mine) })}
                        </button>
                    </div>
                </div>
            {/each}
        {/if}

        <h2>{t("sync.waiting", { n: jobs.length })}</h2>
        {#each jobs as job (job.number)}
            <div class="item">
                <strong
                    >{jobKind(job.kind)}{#if job.value !== undefined}: {valueText(job)}{/if}</strong
                >
                {#if job.label}<span class="small">{job.label}</span>{/if}
                <!-- The inventory is not being counted right now. The numbers wait for that. -->
                {#if job.held}<span class="small held"
                        >{t("sync.on_hold", { reason: job.held })}</span
                    >{/if}
            </div>
        {:else}
            <p class="small">{t("sync.all_sent")}</p>
        {/each}

        {#if rejected.length}
            <h2 class="warning">{t("sync.rejected", { n: rejected.length })}</h2>
            {#each rejected as entry, index (index)}
                <div class="item rejected">
                    <strong
                        >{jobKind(entry.kind)}{#if entry.value !== undefined}: {valueText(
                                entry,
                            )}{/if}</strong
                    >
                    {#if entry.label}<span class="small">{entry.label}</span>{/if}
                    <span class="small reason">{entry.reason} · {timeText(entry.at)}</span>
                </div>
            {/each}
            <!-- The text carries an emphasis, hence '@html'. It comes from
                 'texts.js' and not from outside. -->
            <p class="small">{@html t("sync.rejected_text")}</p>
            <button class="flat wide" onclick={discard}>{t("sync.dismiss_notices")}</button>
        {/if}
    </div>

    <div class="footer">
        <button class="green" disabled={running} onclick={syncNow}>
            {running ? t("account.syncing") : t("account.sync_now")}
        </button>
    </div>
</Dialog>

<style>
    .small {
        display: block;
        font-size: 0.8rem;
        color: var(--muted);
    }

    .status {
        padding: 0.5rem 1rem 0;
    }

    .list {
        flex: 1;
        overflow-y: auto;
        padding: 0.5rem 1rem 1rem;
    }

    h2 {
        margin: 1rem 0 0.5rem;
        font-size: 0.95rem;
    }

    h2.warning {
        color: var(--open);
    }

    .item {
        padding: 0.5rem 0;
        border-bottom: 1px solid var(--border);
    }

    .item.rejected .reason {
        color: var(--open);
    }

    .item .held {
        display: block;
        margin-top: 0.15rem;
    }

    /* One below the other and big enough for the thumb: decided is standing. */
    .choice {
        display: flex;
        flex-direction: column;
        gap: 0.4rem;
        margin-top: 0.5rem;
    }

    .choice button {
        min-height: 2.75rem;
        text-align: left;
    }

    .wide {
        width: 100%;
        margin-top: 0.75rem;
    }

    .footer {
        padding: 0.75rem 1rem calc(0.75rem + env(safe-area-inset-bottom));
        border-top: 1px solid var(--border);
    }

    .green {
        width: 100%;
        min-height: 3rem;
        font-weight: 600;
        color: #fff;
        background: var(--done);
        border-color: var(--done);
    }
</style>
