<script>
    /**
     * The frame of the app's dialogs: the shade behind, the sheet, and its
     * header with the title and the button that closes.
     *
     * Until 10/2026 each of the five dialogs built it itself, with the same
     * rules under the same three classes. What a dialog shows comes in as its
     * children, with its own styles: the frame knows only the frame.
     *
     * The sheet rises from below, up to the header bar, and is modal: its
     * first field gets the focus, Escape closes, Tab stays inside, and the
     * focus goes back afterwards ('services/dialog.js'). The search ('top')
     * hangs from the top edge instead: with the first letter the keyboard
     * comes out and covers the lower half. It is no modal dialog, it focuses
     * its field itself, and its height follows what the keyboard leaves
     * ('height').
     */
    import { modal } from "../services/dialog.js";

    let {
        //: The dialog's name for the screen reader, and its title unless 'title' differs.
        label,
        title = label,
        //: The text of the button in the header that closes the dialog.
        closeText,
        close,
        top = false,
        //: How high the search may get, in pixels, where the device names its visible viewport.
        height = null,
        children,
    } = $props();
</script>

{#snippet header()}
    <div class="header">
        <strong>{title}</strong>
        <button class="flat" onclick={close}>{closeText}</button>
    </div>
{/snippet}

<div class="overlay" class:top role="presentation" onclick={close}></div>

{#if top}
    <div
        class="dialog top"
        role="dialog"
        aria-label={label}
        style:max-height={height ? `${height}px` : null}
    >
        {@render header()}
        {@render children()}
    </div>
{:else}
    <div
        class="dialog sheet"
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabindex="-1"
        use:modal={close}
    >
        {@render header()}
        {@render children()}
    </div>
{/if}

<style>
    .overlay {
        position: fixed;
        inset: 0;
        background: rgba(0, 0, 0, 0.35);
        z-index: 8;
    }

    .dialog {
        position: fixed;
        display: flex;
        flex-direction: column;
        background: var(--surface);
    }

    .sheet {
        inset: 3rem 0 0;
        z-index: 9;
        border-top-left-radius: 1rem;
        border-top-right-radius: 1rem;
        overflow: hidden;
    }

    .header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 0.75rem 1rem;
        border-bottom: 1px solid var(--border);
    }

    /* The search lies on a darker shade, below where the sheets lie. */
    .overlay.top {
        background: rgba(0, 0, 0, 0.4);
        z-index: 5;
    }

    .dialog.top {
        inset: 0 0 auto 0;
        /* Applies where the device does not name the visible viewport. */
        max-height: 100dvh;
        /* As an installed app the page starts below clock and notch. */
        padding-top: env(safe-area-inset-top);
        border-radius: 0 0 1rem 1rem;
        box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25);
        z-index: 6;
    }
</style>
