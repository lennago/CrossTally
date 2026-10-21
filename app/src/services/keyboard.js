/**
 * Keep the keyboard open while one form follows the next.
 *
 * iOS opens the keyboard only if a field gets the focus **in the finger tap
 * itself**. But the next form only arises after saving, i.e. outside the
 * tap: its field then has the focus, the keyboard stays closed. The trick:
 * in the tap an invisible field of the app gets the focus
 * ('holdKeyboard'). The keyboard opens or stays open, and the new form
 * passes the focus on to its first field. From field to field that may
 * happen without a tap too, as long as the keyboard is already open.
 *
 * Without any tap, say on starting the app in the middle of a form, it
 * stays that way: then one has to tap into the field.
 */

let holder = null;
let timer = 0;

/** The invisible field 'App.svelte' creates once. */
export function setHolder(field) {
    holder = field;
}

/**
 * Hold the focus until the next form takes it over.
 *
 * Has to run **synchronously** in the click, before the first 'await'. If
 * no form takes over (say because the article does not belong to the
 * house), the holder gives the focus up again after a short time.
 * Otherwise a keyboard without field would stand there.
 */
export function holdKeyboard() {
    if (!holder) return;
    holder.focus({ preventScroll: true });
    clearTimeout(timer);
    timer = setTimeout(() => {
        if (document.activeElement === holder) holder.blur();
    }, 1500);
}
