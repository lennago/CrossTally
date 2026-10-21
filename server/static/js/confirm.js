/*
 * Confirmations before buttons that act further than their row suggests:
 *
 *   <button type="submit" data-rueckfrage="Wirklich alle Waren umstellen?">…</button>
 *
 * The text stands as an attribute in the page and not as 'onclick'. That way
 * it needs no JavaScript escaping, and the page gets by without inline script.
 *
 * In the capture phase and on the document: the refusal has to apply before
 * everything else, otherwise 'instant_actions.js' would still submit the form
 * in the background.
 */
document.addEventListener(
  "click",
  (ereignis) => {
    const knopf = ereignis.target.closest("[data-rueckfrage]");
    if (!knopf || knopf.disabled) return;
    if (!window.confirm(knopf.dataset.rueckfrage)) {
      ereignis.preventDefault();
      ereignis.stopImmediatePropagation();
    }
  },
  true,
);
