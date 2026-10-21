// The conversion to the product exists only where the house counts in a
// different dimension than the chosen product: pieces against litres, not
// g against kg. That hangs on the currently **chosen** product and unit,
// hence here and not in the form. Without JavaScript the field stays, and
// the form skips an entry it does not need.
//
// Dimension and short form stand on the options ('EinheitenAuswahl',
// 'WarenAuswahl'), the label "1 {haus} = wie viele {ware}?" on the script tag.
(function () {
  const einheit = document.getElementById("id_basiseinheit");
  const ware = document.getElementById("id_ware");
  const block = document.getElementById("div_id_faktor_zur_ware");
  if (!einheit || !ware || !block) return;

  const feld = document.getElementById("id_faktor_zur_ware");
  const beschriftung = block.querySelector("label");
  const VORLAGE = document.currentScript.dataset.wert || "";

  function gewaehlt(auswahl) {
    return auswahl.selectedOptions[0] || null;
  }

  function richten() {
    const haus = gewaehlt(einheit);
    const gewaehlteWare = gewaehlt(ware);
    // An empty unit ("– bitte wählen –") means the product's own. Its
    // placeholder option has no dimension and would otherwise always differ.
    const braucht = Boolean(
      haus &&
      haus.value &&
      gewaehlteWare &&
      gewaehlteWare.value &&
      haus.dataset.dimension !== gewaehlteWare.dataset.dimension,
    );
    block.hidden = !braucht;
    // Hidden does not mean disabled: the browser sends the value along
    // anyway. A remainder from an earlier choice should not ride along.
    if (!braucht && feld) feld.value = "";
    if (braucht && beschriftung && VORLAGE) {
      beschriftung.textContent = VORLAGE.replace(
        "{ware}",
        gewaehlteWare.dataset.einheit,
      ).replace("{haus}", haus.textContent.trim());
    }
  }

  einheit.addEventListener("change", richten);
  ware.addEventListener("change", richten);
  richten();
})();
