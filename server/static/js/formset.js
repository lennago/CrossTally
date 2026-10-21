// Add and remove rows, plus hiding already taken entries in 'einmalig'
// selection fields.
(function () {
  for (const block of document.querySelectorAll("[data-formset]")) {
    const reihen = block.querySelector("[data-formset-reihen]");
    const vorlage = block.querySelector("[data-formset-vorlage]");
    const plus = block.querySelector("[data-formset-plus]");
    const gesamt = document.getElementById(
      `id_${block.dataset.prefix}-TOTAL_FORMS`,
    );
    if (!reihen || !vorlage || !plus || !gesamt) continue;

    const einmalig = block.dataset.einmalig;

    // The formset's delete box is the way to remove without JavaScript.
    // With the script the button does that, then it only gets in the way.
    function knoepfeRichten() {
      for (const reihe of reihen.querySelectorAll("[data-formset-reihe]")) {
        const minus = reihe.querySelector("[data-formset-minus]");
        if (minus) minus.hidden = false;
        const loeschen = reihe.querySelector("input[name$='-DELETE']");
        const kaestchen = loeschen && loeschen.closest(".form-check, .mb-3");
        if (kaestchen) kaestchen.hidden = true;
      }
    }

    // The row stays in the form, only empty and invisible: if one removed
    // it, gaps would open in the numbering, which Django does not skip
    // when reading the formset.
    function zeileEntfernen(reihe) {
      const loeschen = reihe.querySelector("input[name$='-DELETE']");
      if (loeschen) loeschen.checked = true;
      for (const feld of reihe.querySelectorAll("input, select, textarea")) {
        if (feld === loeschen || feld.type === "hidden") continue;
        if (feld.type === "checkbox" || feld.type === "radio")
          feld.checked = false;
        else feld.value = "";
      }
      reihe.hidden = true;
      auswahlAuffrischen();
      gemeldet();
    }

    // Emptying by script triggers no 'input' event. Whoever watches the
    // rows (say the pack selection in the house article form) learns of
    // it only through this message.
    function gemeldet() {
      block.dispatchEvent(
        new CustomEvent("formset:geaendert", { bubbles: true }),
      );
    }

    function auswahlfelder() {
      return [
        ...reihen.querySelectorAll(`select[name$='-${einmalig}']`),
      ].filter((feld) => !feld.closest("[data-formset-reihe]").hidden);
    }

    function auswahlAuffrischen() {
      if (!einmalig) return;
      const felder = auswahlfelder();
      const belegt = new Set(felder.map((feld) => feld.value).filter(Boolean));
      for (const feld of felder) {
        for (const eintrag of feld.options) {
          // The own choice stays, otherwise it would vanish
          // from its own row.
          const blockiert =
            eintrag.value &&
            eintrag.value !== feld.value &&
            belegt.has(eintrag.value);
          eintrag.hidden = blockiert;
          eintrag.disabled = blockiert;
        }
      }
    }

    plus.addEventListener("click", () => {
      const nummer = Number(gesamt.value);
      reihen.insertAdjacentHTML(
        "beforeend",
        vorlage.innerHTML.replaceAll("__prefix__", nummer),
      );
      gesamt.value = nummer + 1;
      knoepfeRichten();
      auswahlAuffrischen();
      gemeldet();
      const erstes = reihen.lastElementChild.querySelector(
        "input, select, textarea",
      );
      if (erstes) erstes.focus();
    });

    // One listener on the block suffices: the events of new rows bubble up.
    block.addEventListener("click", (ereignis) => {
      const minus = ereignis.target.closest("[data-formset-minus]");
      if (minus) zeileEntfernen(minus.closest("[data-formset-reihe]"));
    });
    block.addEventListener("change", auswahlAuffrischen);

    knoepfeRichten();
    auswahlAuffrischen();
  }
})();
