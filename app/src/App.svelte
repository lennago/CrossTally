<script>
    /**
     * The frame: header bar with the way back, below it the view for the path.
     *
     * Built for the phone and with the same buttons on the desktop: top left
     * back (in the entry form that adopts like paging), top right cancel where
     * something is being filled in (only that discards), and bottom right the
     * way to an article nobody expected here.
     *
     * The path is in the address ('#/inventory/3/area/Theke/location/7'), so
     * every level stays callable on its own and the device's back button does
     * the same as the one in the bar.
     */
    import { onMount } from "svelte";

    import { fetchMe, fromSession, isSignedIn, signOut } from "./services/api.js";
    import { remembered } from "./services/storage.js";
    import {
        fromAccount as themeFromAccount,
        submitPending as submitPendingTheme,
    } from "./services/theme.svelte.js";
    import {
        fromAccount as languageFromAccount,
        submitPending as submitPendingLanguage,
        t,
    } from "./services/language.svelte.js";
    import { newVersion } from "./services/version.svelte.js";
    import { back, go, replace, route, up } from "./services/route.svelte.js";
    import { rememberArticle, setTab } from "./services/tabs.svelte.js";
    import {
        close,
        counting,
        loadFromStorage,
        locationFromMasterData,
        open,
        refresh,
    } from "./services/counting.svelte.js";
    import { articles as articlesAt } from "./services/progress.js";
    import { holdKeyboard, setHolder } from "./services/keyboard.js";

    import Login from "./pages/Login.svelte";
    import DemoBanner from "./components/DemoBanner.svelte";
    import AccountMenu from "./components/AccountMenu.svelte";
    import Header from "./components/Header.svelte";
    import PasswordDialog from "./components/PasswordDialog.svelte";
    import EmailDialog from "./components/EmailDialog.svelte";
    import PasskeyDialog from "./components/PasskeyDialog.svelte";
    import { passkeysAvailable } from "./services/passkey.js";
    import SyncDialog from "./components/SyncDialog.svelte";
    import Articles from "./pages/Articles.svelte";
    import ArticleSearch from "./components/ArticleSearch.svelte";
    import Areas from "./pages/Areas.svelte";
    import Entry from "./pages/Entry.svelte";
    import Inventories from "./pages/Inventories.svelte";
    import Locations from "./pages/Locations.svelte";
    import Review from "./pages/Review.svelte";

    //: This often the app syncs in the background. Often enough that two people
    //: in the same house learn of each other, rarely enough that it costs no
    //: battery in a dead spot.
    const SYNC_INTERVAL = 20_000;

    let ready = $state(false);
    let signedIn = $state(false);
    let me = $state(null);
    // The name from the administration. The precached start page carries the
    // build's name, so the tab learns the real one only from the account's answer.
    $effect(() => {
        if (me?.anwendungsname) document.title = me.anwendungsname;
    });
    let searchOpen = $state(false);
    let menuOpen = $state(false);
    let syncOpen = $state(false);
    let passwordOpen = $state(false);
    let emailOpen = $state(false);
    let passkeysOpen = $state(false);
    let holder = $state(null);
    $effect(() => setHolder(holder));

    onMount(async () => {
        // First check whether the website is already signed in. Then there is
        // nothing to type here, and a switch there acts here too.
        const before = await remembered("me");
        me = await fromSession();
        if (me && before && me.id !== before.id) {
            // Another account there: its sign-in cleared the storage. What
            // the interface holds and the address belong to the previous
            // one, so they go, as after a switch at the login form.
            close();
            go();
        }
        signedIn = me !== null || (await isSignedIn());
        if (!me) me = await remembered("me");
        themeFromAccount(me);
        languageFromAccount(me);
        ready = true;
        if (signedIn) void refreshAccount();
    });

    /**
     * What is on the account, fresh from the server, above all the appearance.
     *
     * Without that an app that holds its token itself (the installed one on
     * iOS, say) would see a switch on the website only after the next sign-in.
     * Without network it stays with the remembered state. First goes out what
     * was chosen here without network.
     */
    async function refreshAccount() {
        // Both: until 10/2026 only the theme went, and a language chosen
        // without network waited forever, blocking the account's own.
        await submitPendingTheme();
        await submitPendingLanguage();
        try {
            const fresh = await fetchMe();
            me = fresh;
            themeFromAccount(fresh);
            languageFromAccount(fresh);
        } catch (error) {
            // No network, then on the next start. A sign-in that no longer
            // applies, by contrast, leads to the login form right away instead
            // of waiting for the next sync.
            if (error?.isSignedOut) counting.signedOut = true;
        }
    }

    async function signOutAndBack() {
        menuOpen = false;
        // As soon as the storage is clear, before the server is asked
        // ('signOut'): whoever takes the device next sees the login form,
        // also while a poor connection keeps the request on its way.
        await signOut(true, () => {
            close();
            signedIn = false;
            me = null;
            go();
        });
    }

    let parts = $derived(route.parts);
    let inventoryId = $derived(parts[0] === "inventory" ? numberIn(parts[1]) : null);
    let area = $derived(parts[2] === "area" ? parts[3] : null);
    let locationId = $derived(parts[4] === "location" ? numberIn(parts[5]) : null);
    let articleId = $derived(parts[6] === "article" ? numberIn(parts[7]) : null);
    //: The inventories whose counting stopped for the check. From the list, which is
    //: there before one opens: from the open inventory the areas would flash up first.
    let lockedIds = $derived(
        new Set(counting.inventories.filter((entry) => entry.gesperrt).map((entry) => entry.id)),
    );
    //: Of a locked inventory only its review list is open.
    let locked = $derived(inventoryId !== null && lockedIds.has(inventoryId));
    //: The review list of the inventory: a page of its own, beside the walk through the house.
    //: An open form stays when the lock comes: leaving it would discard what was typed.
    let reviewing = $derived(
        inventoryId !== null && articleId === null && (parts[2] === "review" || locked),
    );

    /**
     * A number out of the address, or nothing.
     *
     * A typed or mangled address ('#/inventory/abc') gave 'NaN' until
     * 10/2026. Dexie refused that with an error out of the effect, every
     * sync ran into it, and the device looked offline. Without a number the
     * way leads to the list.
     */
    function numberIn(part) {
        const value = Number(part);
        return Number.isInteger(value) && value > 0 ? value : null;
    }
    let location = $derived(locationId === null ? null : locationFromMasterData(locationId));

    // A draft the app left behind on a crash. The form one is standing in
    // asks itself. Here it is about the others. One of a locked inventory
    // waits until the lock opens: it stays stored, and then it is offered.
    let leftBehind = $derived(
        counting.drafts.find(
            (entry) =>
                !lockedIds.has(entry.inventoryId) &&
                !(
                    entry.inventoryId === inventoryId &&
                    entry.locationId === locationId &&
                    entry.articleId === articleId
                ),
        ),
    );

    function toDraft(entry) {
        holdKeyboard();
        go(
            "inventory",
            entry.inventoryId,
            "area",
            entry.area,
            "location",
            entry.locationId,
            "article",
            entry.articleId,
        );
    }

    // Only once the start has decided whose the storage is ('onMount'). Until
    // 10/2026 a restored tab loaded the inventory at once, and after a switch
    // on the website the next account saw the previous one's house, rows and
    // open form until the next sync, up to twenty seconds later.
    $effect(() => {
        if (ready && inventoryId !== null) void open(inventoryId);
    });

    // Whoever comes back from the entry form should stand in the list where
    // they last counted. After "Next ›" that is no longer the article they
    // tapped.
    $effect(() => {
        if (articleId !== null) rememberArticle(locationId, articleId);
    });

    function toNext(nextId) {
        // Out of the count the round ends with this form ('outOfCount').
        if (counting.outOfCount) return toList();
        replace("inventory", inventoryId, "area", area, "location", locationId, "article", nextId);
        // The new form starts at the top, even if the last was scrolled far down.
        window.scrollTo(0, 0);
    }

    // Locked while someone walks through the house: to the review list, in
    // place of the address, so the way back leads to the list of inventories.
    $effect(() => {
        if (locked && articleId === null && parts[2] !== "review") {
            replace("inventory", inventoryId, "review");
        }
    });

    /** "Back" from the review list of a locked inventory: there is nothing behind it. */
    function backFromHere() {
        if (locked && reviewing) go();
        else void back();
    }

    //: The form that stayed open when the inventory left the count. Only that
    //: one stays: another article of the inventory, by the address, opened as
    //: if the count went on (until 10/2026).
    let stayedOpen = null;

    // Left the count while someone was standing in it: then no way leads
    // back into this inventory, so to the list. An open entry form stays with
    // a notice: leaving it would discard what was typed (until 10/2026 it
    // did, the draft too). The way to the list follows once it is left.
    $effect(() => {
        if (!counting.gone) return;
        counting.gone = false;
        if (articleId !== null) {
            stayedOpen = `${locationId}/${articleId}`;
            counting.outOfCount = true;
        } else toList();
    });

    // The form is left, another one is asked for, or nothing was typed and
    // the storage let the inventory go along with its articles.
    $effect(() => {
        if (!counting.outOfCount) return;
        const here = articleId === null ? null : `${locationId}/${articleId}`;
        if (here !== stayedOpen || !counting.masterData) toList();
    });

    /** Out of an inventory that left the count, to the list. The notice says why. */
    function toList() {
        // 'close' clears the notice along with the rest. It says why the
        // list comes up, so it survives.
        const notice = counting.notice;
        close();
        counting.notice = notice;
        // The list and the counts of what waits belong to the account, not
        // to the inventory: back from the storage at once, not only once the
        // list's sync answers.
        void loadFromStorage();
        go();
    }

    // The sign-in has expired: to the login form, but **without** clearing
    // the storage. What waits here goes out after signing in, provided it is
    // the same account. Another one is cleared by 'signIn' itself.
    $effect(() => {
        if (!counting.signedOut) return;
        counting.signedOut = false;
        signedIn = false;
    });

    // Sync in the background: what lies here goes out as soon as there is
    // network again, without anyone having to think of it.
    $effect(() => {
        if (!signedIn) return;
        // In the background: one that comes while a sync runs is dropped, the
        // next round comes anyway ('refresh').
        const timer = setInterval(() => void refresh({ background: true }), SYNC_INTERVAL);
        const again = () => void refresh({ background: true });
        // If the app lies in the background, the device stops the clock. On
        // returning the state is thus old, however short the interval, hence
        // here once more.
        const broughtBack = () => {
            if (document.visibilityState === "visible") void refresh({ background: true });
        };
        window.addEventListener("online", again);
        document.addEventListener("visibilitychange", broughtBack);
        return () => {
            clearInterval(timer);
            window.removeEventListener("online", again);
            document.removeEventListener("visibilitychange", broughtBack);
        };
    });

    let title = $derived.by(() => {
        if (reviewing) return t("title.review");
        if (articleId !== null) return t("title.counting");
        // Locations, areas and houses are called as they stand in the catalog.
        // Translated is only what the app says itself.
        if (locationId !== null) return location?.name ?? t("title.articles");
        if (area) return area;
        if (inventoryId !== null) return counting.inventory?.haus ?? t("title.areas");
        return t("title.inventories");
    });

    let subtitle = $derived.by(() => {
        if (reviewing) return counting.inventory?.haus ?? "";
        if (articleId !== null && location) return `${location.bereich} · ${location.name}`;
        if (locationId !== null && location) return location.bereich;
        if (area) return counting.inventory?.haus ?? "";
        return "";
    });

    /** Cancel stands where something is being filled in, otherwise the sync. */
    let cancellable = $derived(articleId !== null);

    function chooseArticle(article) {
        holdKeyboard();
        searchOpen = false;
        // Whoever comes back should also see the article just counted. It
        // stands in the tab of its type. One this inventory does not count
        // stands in the first tab, and that is what the list opens then.
        setTab(locationId, article.inventurart ?? null);
        go("inventory", inventoryId, "area", area, "location", locationId, "article", article.id);
    }
</script>

{#if !ready}
    <p class="hint">{t("app.loading")}</p>
{:else if !signedIn}
    <Login
        pending={counting.pending}
        done={(who) => {
            // Another account than the one whose sign-in expired: what stands in
            // memory is the previous one's. The sign-in cleared its storage, this
            // goes the same way, as on signing out. Until 10/2026 it showed until
            // the first sync.
            if (me?.id !== who.id) close();
            me = who;
            signedIn = true;
            themeFromAccount(who);
            languageFromAccount(who);
            go();
        }}
    />
{:else}
    <Header
        {title}
        {subtitle}
        canGoBack={parts.length > 0}
        {cancellable}
        accountName={me?.vorname || me?.benutzername || t("header.account")}
        back={backFromHere}
        cancel={up}
        menu={() => (menuOpen = !menuOpen)}
        sync={() => (syncOpen = true)}
        refresh={() => refresh({ masterData: true, review: reviewing })}
    />

    <DemoBanner visible={me?.demo === true} />

    {#if menuOpen}
        <AccountMenu
            username={me?.benutzername}
            signOut={signOutAndBack}
            unsent={counting.pending + counting.quantityConflicts}
            sync={refresh}
            changePassword={() => {
                menuOpen = false;
                passwordOpen = true;
            }}
            changeEmail={() => {
                menuOpen = false;
                emailOpen = true;
            }}
            managePasskeys={passkeysAvailable()
                ? () => {
                      menuOpen = false;
                      passkeysOpen = true;
                  }
                : null}
            privacy={me?.datenschutz !== false}
            privacyUrl={me?.datenschutz_adresse || "/datenschutz/"}
            legalNotice={me?.impressum === true}
            legalNoticeUrl={me?.impressum_adresse || "/impressum/"}
            feedbackAddress={me?.rueckmeldeadresse || ""}
            online={counting.online}
        />
    {/if}

    {#if newVersion.ready}
        <!-- Do not reload by itself: that would tear away the draft someone
             is typing on right now. -->
        <button class="notice-button" onclick={newVersion.apply}>
            {t("app.new_version")} · <strong>{t("app.load_now")}</strong>
        </button>
    {/if}

    {#if counting.quantityConflicts}
        <!-- A number waits for a decision. The small exclamation mark on the
             icon alone is overlooked, and until then the number is missing. -->
        <button class="notice-button question" onclick={() => (syncOpen = true)}>
            {t("app.counted_by_other")} · <strong>{t("app.decide")}</strong>
        </button>
    {/if}

    {#if leftBehind}
        <!-- The app died before the form was left. Typed it is still,
             counted it is not. It continues there. -->
        <button class="notice-button question" onclick={() => toDraft(leftBehind)}>
            {t("app.not_saved", { label: leftBehind.label })} ·
            <strong>{t("entry.continue")}</strong>
        </button>
    {/if}

    {#if counting.message}
        <p class="message">{counting.message}</p>
    {/if}

    {#if counting.notice}
        <p class="message">{t(counting.notice.key)}</p>
    {/if}

    {#if locked && articleId !== null}
        <p class="message">{t("entry.locked")}</p>
    {/if}

    {#if counting.outOfCount && articleId !== null}
        <p class="message">{t("entry.out_of_count")}</p>
    {/if}

    <main>
        {#if inventoryId === null}
            <Inventories />
        {:else if articleId !== null}
            <!-- Per article a fresh form: draft, error message and the guard on
                 the way back belong to the article, not to the view. -->
            {#key articleId}
                <Entry {locationId} {articleId} done={up} {toNext} />
            {/key}
        {:else if reviewing}
            <!-- Managers decide whether the difference is typed in by hand,
                 and whether the count is locked. -->
            <Review manager={me?.ist_fuehrungskraft === true || me?.ist_admin === true} />
        {:else if locationId !== null}
            <Articles {area} {locationId} />
        {:else if area}
            <Locations {area} />
        {:else}
            <Areas />
        {/if}
    </main>

    <!-- Keeps the keyboard open while one form follows the next
         ('services/keyboard.js'). Invisible and reachable by nobody. -->
    <input
        class="keyboard-holder"
        type="text"
        inputmode="decimal"
        tabindex="-1"
        aria-hidden="true"
        bind:this={holder}
    />

    {#if locationId !== null && articleId === null && !reviewing}
        <!-- Bottom right, within thumb's reach: count something nobody
             expected here. -->
        <button class="add" onclick={() => (searchOpen = true)} aria-label={t("search.add")}>
            <!-- Drawn instead of as a character: a "+" sits above or below the
                 centre depending on the font, because ascender and descender differ. -->
            <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
                <path
                    d="M12 4v16M4 12h16"
                    stroke="currentColor"
                    stroke-width="2.5"
                    stroke-linecap="round"
                />
            </svg>
        </button>
    {/if}

    {#if syncOpen}
        <SyncDialog close={() => (syncOpen = false)} />
    {/if}

    {#if passwordOpen}
        <PasswordDialog username={me?.benutzername} close={() => (passwordOpen = false)} />
    {/if}

    {#if emailOpen}
        <!-- An older state in the storage knows neither the address nor the
             two flags: then the dialog asks for the password and promises
             the mail, as with a server that has one. -->
        <EmailDialog
            username={me?.benutzername}
            email={me?.email}
            hasPassword={me?.hat_passwort !== false}
            confirms={me?.mail !== false}
            changed={() => void refreshAccount()}
            close={() => (emailOpen = false)}
        />
    {/if}

    {#if passkeysOpen}
        <!-- An older state in the storage does not know whether the account
             has a password: then the dialog asks for it, as for one that has. -->
        <PasskeyDialog
            username={me?.benutzername}
            hasPassword={me?.hat_passwort !== false}
            close={() => (passkeysOpen = false)}
        />
    {/if}

    {#if searchOpen}
        <ArticleSearch
            existing={articlesAt(counting.positions, locationId).map((entry) => entry.id)}
            all={counting.masterData?.artikel ?? []}
            types={counting.inventory?.inventurarten ?? []}
            allTypes={counting.masterData?.inventurarten ?? []}
            choose={chooseArticle}
            close={() => (searchOpen = false)}
        />
    {/if}
{/if}

<style>
    /* Not 'display: none': a hidden field gets no focus. 16 px, otherwise
       iOS zooms in as soon as it has the focus. */
    .keyboard-holder {
        position: fixed;
        top: 0;
        left: 0;
        width: 1px;
        height: 1px;
        padding: 0;
        border: 0;
        opacity: 0;
        font-size: 16px;
        pointer-events: none;
    }

    .notice-button {
        display: block;
        width: 100%;
        padding: 0.6rem 1rem;
        border: none;
        border-bottom: 1px solid var(--border);
        background: var(--selected);
        color: var(--accent);
        font-size: 0.9rem;
        text-align: left;
    }

    .question {
        background: var(--notice);
        color: var(--text);
    }

    .message {
        margin: 0;
        padding: 0.5rem 1rem;
        background: var(--notice);
        font-size: 0.85rem;
    }

    .add {
        position: fixed;
        right: 1rem;
        bottom: calc(1rem + env(safe-area-inset-bottom));
        width: 3.5rem;
        height: 3.5rem;
        border-radius: 50%;
        display: grid;
        place-items: center;
        padding: 0;
        border: 0;
        background: var(--accent);
        color: #fff;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.25);
        z-index: 4;
    }

    .hint {
        padding: 2rem 1rem;
        color: var(--muted);
    }
</style>
