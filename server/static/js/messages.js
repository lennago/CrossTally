/*
 * Success messages disappear by themselves.
 *
 * They lie over the content. If a "Saved" message stayed, it would cover the
 * place where one wants to continue working. Warnings and errors stay. Those
 * belong read and clicked away.
 *
 * The observer is needed because messages also come in **afterwards**:
 * 'instant_actions.js' swaps the message area without reloading the page.
 */
(function () {
  const bereich = document.querySelector("[data-meldungen]");
  if (!bereich || !window.MutationObserver) return;

  //: This long a success message stays.
  const DAUER = 5000;

  function verabschieden(meldung) {
    if (meldung.dataset.laeuft) return;
    meldung.dataset.laeuft = "ja";
    setTimeout(() => {
      meldung.classList.remove("show");
      setTimeout(() => meldung.remove(), 300);
    }, DAUER);
  }

  function durchsehen() {
    for (const meldung of bereich.querySelectorAll("[data-vergaenglich]")) {
      verabschieden(meldung);
    }
  }

  durchsehen();
  new MutationObserver(durchsehen).observe(bereich, {
    childList: true,
    subtree: true,
  });
})();
