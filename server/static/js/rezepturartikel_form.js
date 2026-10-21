// Containers and shares belong to the scale. As long as counting is by
// pieces, both are pointless. They disappear and empty themselves, so on a
// change of unit nothing from before remains and the form rejects.
//
// With "shares in percent" the quantity column of the recipe means something
// else. Its label says so, in every row, including the ones added later.
(function () {
  const einheit = document.getElementById("id_basiseinheit");
  const block = document.getElementById("div_id_behaelter");
  const anteilBlock = document.getElementById("div_id_anteilig");
  const anteilig = document.getElementById("id_anteilig");
  if (!einheit || !block) return;
  const anteilText = document.currentScript.dataset.wert;

  // The dimension of the chosen unit is written onto the option by the server.
  function gewogen() {
    const gewaehlt = einheit.selectedOptions[0];
    return !!gewaehlt && gewaehlt.dataset.dimension === "MASSE";
  }

  function beschriften() {
    const prozent = !!anteilig && anteilig.checked;
    const labels = document.querySelectorAll(
      "[data-formset] label[for$='-menge_in_basiseinheit']",
    );
    for (const label of labels) {
      // Only the words, not the asterisk of the required field next to them.
      const text = [...label.childNodes].find(
        (knoten) => knoten.nodeType === Node.TEXT_NODE,
      );
      if (!text) continue;
      if (!label.dataset.ursprung) label.dataset.ursprung = text.textContent;
      text.textContent = prozent ? anteilText : label.dataset.ursprung;
    }
  }

  function richten() {
    const zeigen = gewogen();
    block.hidden = !zeigen;
    if (anteilBlock) anteilBlock.hidden = !zeigen;
    if (!zeigen) {
      for (const kaestchen of block.querySelectorAll("input[type=checkbox]")) {
        kaestchen.checked = false;
      }
      if (anteilig) anteilig.checked = false;
    }
    beschriften();
  }

  // Tom Select reports its choice on the original <select>.
  einheit.addEventListener("change", richten);
  if (anteilig) anteilig.addEventListener("change", beschriften);
  // A new row comes with the label from the template. It is relabelled too.
  document.addEventListener("formset:geaendert", beschriften);
  richten();
})();
