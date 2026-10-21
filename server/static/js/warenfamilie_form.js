/*
 * Brings the two blocks together: the unit hangs on the chosen products.
 * Without JavaScript everything stays visible and the form checks the
 * same rules.
 */
(function () {
  // "{anzahl} gewählt". The page passes the text in on the script tag,
  // there the translation applies.
  const GEWAEHLT = document.currentScript.dataset.wert;
  const formular = document.querySelector("[data-familienformular]");
  if (!formular) return;
  // Products in families below are summed here too: then there is
  // something to sum before a box is ticked.
  const warenDarunter = formular.hasAttribute("data-waren-darunter");

  const zeilen = [...formular.querySelectorAll("[data-ware]")];
  const auswahl = formular.querySelector("#id_leiteinheit");
  const einheitenblock = formular.querySelector("[data-einheitenblock]");
  const zahl = formular.querySelector("[data-warenzahl]");
  const suche = formular.querySelector("[data-warensuche]");
  const freie = formular.querySelector('[data-warenliste="frei"]');
  const gewaehlte = formular.querySelector('[data-warenliste="gewaehlt"]');
  const leerHinweis = formular.querySelector("[data-leer-hinweis]");
  const nichtsGewaehlt = formular.querySelector("[data-nichts-gewaehlt]");
  if (!auswahl) return;

  // Tom Select keeps its own copy of the choice and shows that. A value
  // set on the <select> alone would stay invisible.
  function nachziehen(feld) {
    const auswahlfeld = feld.tomselect;
    if (auswahlfeld) auswahlfeld.setValue(feld.value, true);
  }

  function gewaehlt() {
    return zeilen.filter((zeile) => zeile.querySelector("input").checked);
  }

  // Every row stands in the column it belongs to. Moved is the element
  // itself. The box in it stays the same, the form notices nothing of
  // the move.
  function einsortieren(zeile, ziel) {
    const reihe = Number(zeile.dataset.reihe);
    // Back to the old place, not to the end: the list is sorted, and
    // whoever takes a product out looks for it where it stood before.
    const nachbar = [...ziel.children].find(
      (kind) => Number(kind.dataset.reihe) > reihe,
    );
    ziel.insertBefore(zeile, nachbar ?? null);
  }

  function verteilen() {
    for (const zeile of zeilen) {
      const ziel = zeile.querySelector("input").checked ? gewaehlte : freie;
      if (zeile.parentElement !== ziel) einsortieren(zeile, ziel);
    }
    if (nichtsGewaehlt) nichtsGewaehlt.hidden = gewaehlte.children.length > 0;
    suchen();
  }

  // Searched is only on the left, because what stands over there is chosen anyway.
  function suchen() {
    if (!suche) return;
    const begriff = suche.value.trim().toLowerCase();
    let treffer = 0;
    for (const zeile of freie.querySelectorAll("[data-ware]")) {
      const passt = !begriff || zeile.dataset.name.includes(begriff);
      zeile.hidden = !passt;
      if (passt) treffer += 1;
    }
    if (leerHinweis) leerHinweis.hidden = treffer > 0 || !begriff;
  }

  function ordnen() {
    const dabei = gewaehlt();

    verteilen();
    if (zahl) zahl.textContent = GEWAEHLT.replace("{anzahl}", dabei.length);
    // Without a product there is nothing to sum. The block would be a
    // question without a subject. Every unit stays up for choice: a family
    // in litres may hold nothing but bottles in pieces.
    einheitenblock.hidden = dabei.length === 0 && !warenDarunter;

    // The first product presets its unit. Whoever wants another one chooses it.
    if (!auswahl.value && dabei.length > 0) {
      auswahl.value = dabei[0].dataset.einheit;
      nachziehen(auswahl);
    }
  }

  formular.addEventListener("change", (ereignis) => {
    if (ereignis.target.closest("[data-ware]") || ereignis.target === auswahl)
      ordnen();
  });

  if (suche) suche.addEventListener("input", suchen);

  ordnen();
})();
