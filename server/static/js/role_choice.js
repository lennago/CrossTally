/*
 * For a manager there is no house assignment to make.
 *
 * They are bound to no single house: the pages show "all houses" for them,
 * and whether they may count is decided by the assignment on the individual
 * inventory. The box list would thus only stand there and invite an input
 * that has no effect.
 *
 * Hidden, not emptied: whoever becomes a counter again later should not have
 * lost their houses. Without JavaScript the list stays visible and does no
 * harm.
 */
(function () {
  const kaestchen = document.querySelector("#id_is_staff");
  // crispy-forms wraps every field in 'div_id_<name>'.
  const haeuser = document.querySelector("#div_id_haeuser");
  if (!kaestchen || !haeuser) return;

  function anpassen() {
    haeuser.hidden = kaestchen.checked;
  }

  kaestchen.addEventListener("change", anpassen);
  anpassen();
})();
