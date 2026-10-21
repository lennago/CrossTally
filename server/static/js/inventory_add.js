/*
 * Creating an inventory: first the types, then the houses.
 *
 * A house can only count a type it carries an active article in. Every
 * house box names its types in 'data-arten' (numbers, separated by spaces).
 * Whatever has none of the chosen ones is disabled and unticked, and its
 * hint ('data-ohne-art') says why. Disabled boxes are not sent, the server
 * checks the same once more.
 *
 * "All" ('data-alle' with the field name of its group) ticks every box of
 * the group that is available, and follows them: ticked when all are,
 * half when some are. Without the script it stays hidden
 * ('data-alle-zeile'), because then it would be a box without effect.
 *
 * The list of counters ('data-zaehlende-wahl') only counts with the box
 * for all houses, and stands there only with it. Without the script it
 * stays visible, the server ignores it without the box.
 */
(function () {
  const fuerAlle = document.querySelector("#id_zaehlende_fuer_alle");
  const liste = document.querySelector("[data-zaehlende-wahl]");
  if (!fuerAlle || !liste) return;

  function anpassen() {
    liste.hidden = !fuerAlle.checked;
  }

  fuerAlle.addEventListener("change", anpassen);
  anpassen();
})();

(function () {
  const arten = [...document.querySelectorAll("input[name='inventurarten']")];
  const haeuser = [...document.querySelectorAll("input[name='haeuser']")];
  const alleFelder = [...document.querySelectorAll("[data-alle]")];
  if (!arten.length || !haeuser.length) return;

  function haeuserAbgleichen() {
    const gewaehlt = new Set(
      arten.filter((feld) => feld.checked).map((feld) => feld.value),
    );
    for (const haus of haeuser) {
      const eigene = (haus.dataset.arten || "").split(" ").filter(Boolean);
      const passt = eigene.some((art) => gewaehlt.has(art));
      haus.disabled = !passt;
      if (!passt) haus.checked = false;
      const hinweis = haus
        .closest(".form-check")
        ?.querySelector("[data-ohne-art]");
      // Without a chosen type every house would carry the hint.
      if (hinweis) hinweis.hidden = passt || gewaehlt.size === 0;
    }
  }

  function gruppe(alle) {
    return [...document.querySelectorAll(`input[name='${alle.dataset.alle}']`)];
  }

  function alleNachziehen() {
    for (const alle of alleFelder) {
      const offen = gruppe(alle).filter((feld) => !feld.disabled);
      const an = offen.filter((feld) => feld.checked).length;
      alle.checked = offen.length > 0 && an === offen.length;
      alle.indeterminate = an > 0 && an < offen.length;
      alle.disabled = offen.length === 0;
    }
  }

  for (const alle of alleFelder) {
    alle.closest("[data-alle-zeile]").hidden = false;
    alle.addEventListener("change", () => {
      for (const feld of gruppe(alle)) {
        if (!feld.disabled) feld.checked = alle.checked;
      }
      haeuserAbgleichen();
      alleNachziehen();
    });
  }
  for (const feld of [...arten, ...haeuser]) {
    feld.addEventListener("change", () => {
      haeuserAbgleichen();
      alleNachziehen();
    });
  }
  haeuserAbgleichen();
  alleNachziehen();
})();
