// The conversion when merging exists only where the two products are
// carried in different dimensions: pieces against litres, not g against
// kg. That hangs on the currently chosen product and on who stays, hence
// here and not in the form. Without JavaScript the field stays, and the
// form skips an entry it does not need.
(function () {
  const quelle = document.getElementById("id_quelle");
  const block = document.getElementById("div_id_umrechnung");
  if (!quelle || !block) return;

  const feld = document.getElementById("id_umrechnung");
  const beschriftung = block.querySelector("label");
  const SEITE = document.currentScript.dataset;

  function bleibtDieser() {
    const gewaehlt = document.querySelector("[name='bleibt']:checked");
    return !gewaehlt || gewaehlt.value === "dieser";
  }

  function richten() {
    const option = quelle.selectedOptions[0];
    const braucht = Boolean(
      option && option.value && option.dataset.dimension !== SEITE.dimension,
    );
    block.hidden = !braucht;
    if (!braucht && feld) feld.value = "";
    if (!braucht || !beschriftung || !SEITE.wert) return;
    // Whoever stays is the target: the label names them first.
    const [ziel, aufgeloest] = bleibtDieser()
      ? [SEITE.einheit, option.dataset.einheit]
      : [option.dataset.einheit, SEITE.einheit];
    beschriftung.textContent = SEITE.wert
      .replace("{ziel}", ziel)
      .replace("{quelle}", aufgeloest);
  }

  quelle.addEventListener("change", richten);
  for (const knopf of document.querySelectorAll("[name='bleibt']"))
    knopf.addEventListener("change", richten);
  richten();
})();
