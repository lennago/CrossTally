// The conversions of a product are one field per dimension: what one unit
// is in grams, millilitres or pieces. The field of the product's own
// dimension stays hidden, there the unit computes itself. Containers belong
// to weighing: without the tick they have no subject.
//
// Decided is on the **currently chosen** unit, not on the stored one,
// hence here and not in the form.
//
// On the page of a house article the same fields stand under a prefix and
// without the unit ('WareEinstellungenForm'). The unit is fixed there, and
// the form has named the conversions itself. Left for the script are the
// containers.
(function () {
  // The reference unit per dimension and the pattern of the label stand on
  // the script tag ('components/ware_form_script.html'). There the
  // translation applies.
  const TEXT = document.currentScript.dataset;
  const PRAEFIX = TEXT.praefix ? `${TEXT.praefix}-` : "";

  const haken = document.getElementById(`id_${PRAEFIX}abwiegbar`);
  const einheit = document.getElementById(`id_${PRAEFIX}basiseinheit`);
  const behaelter = document.getElementById(`div_id_${PRAEFIX}behaelter`);
  if (!haken || !behaelter) return;

  const REFERENZ = {
    MASSE: TEXT.masse,
    VOLUMEN: TEXT.volumen,
    STUECK: TEXT.stueck,
    PORTION: TEXT.portion,
  };
  // A portion holds pieces, a piece holds millilitres and grams, a litre
  // weighs grams. Against a higher dimension the factor is entered the
  // other way round ("kg je Stk.", "g je l"), and the higher side is named
  // by its usual unit. The same order as 'RANG_JE_DIMENSION'.
  const RANG = { PORTION: 0, STUECK: 1, VOLUMEN: 2, MASSE: 3 };
  const GEGEN = {
    PORTION: TEXT.portion,
    STUECK: TEXT.stueck,
    VOLUMEN: TEXT.liter,
  };
  const felder = Object.keys(REFERENZ)
    .map((dimension) => ({
      dimension,
      block: document.getElementById(`div_id_${PRAEFIX}faktor_${dimension}`),
      feld: document.getElementById(`id_${PRAEFIX}faktor_${dimension}`),
    }))
    .filter((eintrag) => eintrag.block && eintrag.feld);

  function gewaehlt() {
    return einheit.selectedOptions[0];
  }

  function dimension() {
    const option = gewaehlt();
    return option ? option.dataset.dimension : "";
  }

  // The conversion exists only when editing, and it is needed only on a
  // change of dimension. From g to kg the server computes itself.
  // Measured against the unit the page came with. While the unit changes,
  // the factors are converted along by the server and the fields say so.
  const umrechnung = document.getElementById(`div_id_${PRAEFIX}umrechnung`);
  const bisher = einheit ? einheit.value : "";
  const bisherigeDimension = einheit ? dimension() : "";

  function behaelterRichten() {
    const wiegt = haken.checked;
    behaelter.hidden = !wiegt;
    if (!wiegt) {
      for (const kaestchen of behaelter.querySelectorAll(
        "input[type=checkbox]",
      )) {
        kaestchen.checked = false;
      }
    }
  }

  function richten() {
    behaelterRichten();
    // Without the unit in the form there is nothing to name or to hide.
    if (!einheit) return;

    const wechsel = umrechnung !== null && einheit.value !== bisher;
    if (umrechnung) umrechnung.hidden = dimension() === bisherigeDimension;

    const eigene = dimension();
    const option = gewaehlt();
    for (const { dimension: fremde, block, feld } of felder) {
      const eigen = fremde === eigene;
      block.hidden = eigen || wechsel;
      // Hidden does not mean disabled: the browser sends the value along
      // anyway. A value in the field of the own dimension would run into
      // an error message on a field nobody sees.
      if (eigen) feld.value = "";
      const beschriftung = block.querySelector("label");
      if (beschriftung && option) {
        const gedreht = RANG[fremde] < RANG[eigene];
        beschriftung.textContent = (gedreht ? TEXT.kehrwertMuster : TEXT.muster)
          .replace("{referenz}", gedreht ? GEGEN[fremde] : REFERENZ[fremde])
          .replace("{einheit}", option.textContent.trim());
      }
    }
  }

  haken.addEventListener("change", richten);
  // Tom Select reports its choice on the original <select>.
  if (einheit) einheit.addEventListener("change", richten);
  richten();
})();
