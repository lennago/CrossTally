// The value of the choice "own value" comes from the script tag. It is read up here:
// 'currentScript' exists only while the script runs, no longer in the event.
const EIGENER_WERT = document.currentScript.dataset.wert;

// "all" per block: only what can be chosen. Locked rows stay out.
document.addEventListener("change", (ereignis) => {
  const name = ereignis.target.dataset?.alle;
  if (!name) return;
  for (const kaestchen of document.querySelectorAll(
    `input[name="${name}"]:not(:disabled)`,
  )) {
    kaestchen.checked = ereignis.target.checked;
  }
});

// In the fourth block one answer for all: nothing, inactive or
// discontinue. A switch of the type stays per row, because it asks for
// whom it applies.
document.addEventListener("click", (ereignis) => {
  const knopf = ereignis.target.closest("[data-setze]");
  if (!knopf) return;
  for (const auswahl of document.querySelectorAll(
    'input[type="radio"][name^="ungezaehlt_"]:not(:disabled)',
  )) {
    if (auswahl.value === knopf.dataset.setze) auswahl.checked = true;
  }
});

// Fifth block: one answer for all, and whoever types an own value means
// "own value" without having to set the radio button beforehand.
document.addEventListener("click", (ereignis) => {
  const knopf = ereignis.target.closest("[data-setze-soll]");
  if (!knopf) return;
  for (const auswahl of document.querySelectorAll(
    'input[type="radio"][name^="soll_"]',
  )) {
    if (auswahl.value === knopf.dataset.setzeSoll) auswahl.checked = true;
  }
});
document.addEventListener("input", (ereignis) => {
  const schluessel = ereignis.target.dataset?.sollwert;
  if (!schluessel || !ereignis.target.value.trim()) return;
  const eigener = document.querySelector(
    `input[name="soll_${schluessel}"][value="${EIGENER_WERT}"]`,
  );
  if (eigener) eigener.checked = true;
});

// A switch of the type asks for whom it applies. The question stands only
// where a switch is chosen in the row, and goes again with it. A row's
// switch is a radio button ('data-wechsel') or the selection of the sixth
// block, empty there meaning "no".
function geltungZeigen(zeile) {
  const frage = zeile.querySelector("[data-geltung-frage]");
  if (!frage) return;
  frage.hidden = ![...zeile.querySelectorAll("[data-wechsel]")].some((feld) =>
    feld.type === "radio" ? feld.checked : feld.value !== "",
  );
}
function alleGeltungenZeigen() {
  for (const frage of document.querySelectorAll("[data-geltung-frage]")) {
    geltungZeigen(frage.closest("tr"));
  }
}
document.addEventListener("change", (ereignis) => {
  const zeile = ereignis.target.closest?.("tr");
  if (zeile) geltungZeigen(zeile);
});
// "all" in the fourth block sets the radio buttons without an event.
document.addEventListener("click", (ereignis) => {
  if (ereignis.target.closest("[data-setze]")) alleGeltungenZeigen();
});
alleGeltungenZeigen();
