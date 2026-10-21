<script>
    /**
     * Paging at the bottom of the entry form: "‹ Previous" and "Next ›".
     *
     * Both adopt the draft, the form decides how ('onprevious', 'onnext').
     * 'before' and 'after' are the neighbours in the list, 'working' says
     * that saving runs. The focused field of the form ('content') moves to
     * the centre when the keyboard opens.
     */
    import { t } from "../services/language.svelte.js";

    let { content, working, before, after, onprevious, onnext } = $props();

    // Where the footer bar stands with the keyboard open, 'null' as long as
    // there is none. The devices only shrink the visible viewport
    // ('visualViewport') for the keyboard, not the page: with 'bottom: 0' the
    // bar would lie behind it, and whoever wanted on had to tap the keyboard away.
    //
    // Measured on the **document** ('pageTop'), not on the fixed frame: iOS
    // pushes the viewport against this frame when scrolling and with a field
    // further down, and a bar with 'position: fixed' then slipped behind the
    // keyboard after all. As long as it is open, the bar therefore lies
    // absolutely in the document, exactly at the bottom edge of the viewport.
    let top = $state(null);
    let bar = $state(null);
    $effect(() => {
        const viewport = window.visualViewport;
        if (!viewport) return;
        const measure = () => {
            const before = top;
            // A keyboard is several hundred pixels high. What stays below
            // is the address bar that slides in and out when scrolling.
            // Zoomed in the viewport shrinks too. That is no keyboard.
            const open = viewport.scale < 1.01 && window.innerHeight - viewport.height > 120;
            top = open
                ? Math.round(viewport.pageTop + viewport.height - (bar?.offsetHeight ?? 0))
                : null;
            // When the keyboard opens, the field moves to the centre: the device
            // scrolls it only to its edge, and there the bar stands now.
            const field = document.activeElement;
            if (
                before === null &&
                open &&
                field instanceof HTMLInputElement &&
                content?.contains(field)
            )
                field.scrollIntoView({ block: "center" });
        };
        // Once per frame, not per event: when scrolling they come densely.
        let planned = 0;
        const plan = () => {
            planned ||= requestAnimationFrame(() => {
                planned = 0;
                measure();
            });
        };
        measure();
        // 'scroll' on the window too: if the page scrolls under a standing
        // viewport, only 'pageTop' changes, and the viewport reports nothing.
        viewport.addEventListener("resize", plan);
        viewport.addEventListener("scroll", plan);
        window.addEventListener("scroll", plan, { passive: true });
        return () => {
            cancelAnimationFrame(planned);
            viewport.removeEventListener("resize", plan);
            viewport.removeEventListener("scroll", plan);
            window.removeEventListener("scroll", plan);
        };
    });
</script>

<!-- 'top' is set by the script via the CSSOM. The Content-Security-Policy
     does not block that, unlike a 'style' attribute. -->
<div
    class="footer-bar"
    class:above-keyboard={top !== null}
    style:top={top !== null ? `${top}px` : null}
    bind:this={bar}
>
    <!-- Before the first article disabled instead of vanished: otherwise the
         right button would shift, and the thumb would no longer hit it blind. -->
    <button class="previous" disabled={working || !before} onclick={onprevious}>
        {t("entry.previous")}
    </button>
    <button class="next" disabled={working} onclick={onnext}>
        {after ? t("entry.next") : t("entry.done")}
    </button>
</div>

<style>
    /* The buttons stick at the bottom, where the thumb is, on the keyboard
       when it is open. Both equal: forward and back are a pair. */
    .footer-bar {
        display: flex;
        gap: 0.5rem;
        position: fixed;
        left: 0;
        right: 0;
        bottom: 0;
        /* At the bottom the device's swipe bar, at the sides the notch in
           landscape. All three only differ from zero in the installed app. */
        padding: 0.75rem calc(1rem + env(safe-area-inset-right))
            calc(0.75rem + env(safe-area-inset-bottom)) calc(1rem + env(safe-area-inset-left));
        background: var(--surface);
        border-top: 1px solid var(--border);
    }

    /* On the keyboard the bar lies in the document, not in the frame. 'top'
       comes from the script. The device's swipe bar is not in the way there. */
    .footer-bar.above-keyboard {
        position: absolute;
        bottom: auto;
        padding-top: 0.5rem;
        padding-bottom: 0.5rem;
    }

    .footer-bar button {
        flex: 1;
        min-height: 3.25rem;
        font-size: 1.05rem;
        font-weight: 600;
    }
</style>
