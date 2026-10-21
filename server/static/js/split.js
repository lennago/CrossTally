/*
 * Moves the rows between '[data-ortsliste="bleibt"]' and '="geht"'.
 * The word after the count ("Artikel", "Waren") stands as 'data-wert' on the script tag.
 */
// The rows migrate between the columns. The box in them stays the
// same, the form notices nothing of the move. Inserted is at the old
// place, not at the end. That way each side keeps the order of the
// previous location, and exactly that is saved.
(function () {
  const bleibt = document.querySelector('[data-ortsliste="bleibt"]');
  const geht = document.querySelector('[data-ortsliste="geht"]');
  if (!bleibt || !geht) return;
  const zeilen = [...bleibt.querySelectorAll("[data-reihe]")];
  // The word after the count ("Artikel", "Waren") is passed in by the page on the script tag.
  const EINHEIT = document.currentScript.dataset.wert;

  function einsortieren(zeile, ziel) {
    const reihe = Number(zeile.dataset.reihe);
    const nachbar = [...ziel.children].find(
      (kind) => Number(kind.dataset.reihe) > reihe,
    );
    ziel.insertBefore(zeile, nachbar ?? null);
  }

  function verteilen() {
    for (const zeile of zeilen) {
      const ziel = zeile.querySelector("input").checked ? geht : bleibt;
      if (zeile.parentElement !== ziel) einsortieren(zeile, ziel);
    }
    document.querySelector('[data-anzahl="bleibt"]').textContent =
      `${bleibt.children.length - bleibt.querySelectorAll("p").length} ${EINHEIT}`;
    document.querySelector('[data-anzahl="geht"]').textContent =
      `${geht.children.length} ${EINHEIT}`;
    document.querySelector('[data-leer="geht"]').hidden =
      geht.children.length > 0;
  }

  document.addEventListener("change", (ereignis) => {
    if (ereignis.target.closest("[data-reihe]")) verteilen();
  });
  verteilen();
})();
