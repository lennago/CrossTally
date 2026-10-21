/*
 * Forms that may arrive only once:
 *
 *   <form method="post" data-einmal="Wird eingespielt …">…</form>
 *
 * After the first submit the buttons are disabled and carry the text from
 * 'data-einmal'. If the response takes long, the page otherwise looked as if
 * nothing happened: an enter in the field had already submitted the form, the
 * click afterwards sent a second request after it, and the browser showed
 * that one's response instead of the first. The lock in the server remains
 * the actual one nonetheless. This only saves the page that reports it.
 */
document.addEventListener("submit", (ereignis) => {
  const formular = ereignis.target.closest("form[data-einmal]");
  if (!formular) return;
  if (formular.dataset.abgeschickt) {
    ereignis.preventDefault();
    return;
  }
  formular.dataset.abgeschickt = "1";
  // Lock only after submitting: a disabled button would no longer send
  // its name and value along.
  window.setTimeout(() => {
    for (const knopf of formular.querySelectorAll("[type=submit]")) {
      knopf.disabled = true;
      if (!formular.dataset.einmal) continue;
      if (knopf instanceof HTMLInputElement)
        knopf.value = formular.dataset.einmal;
      else knopf.textContent = formular.dataset.einmal;
    }
  });
});
