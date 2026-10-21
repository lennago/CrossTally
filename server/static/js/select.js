// Every selection list becomes searchable, except one marked 'data-ohne-suche'
// (the select lists of the list filters, which only choose). If Tom Select does
// not load, the ordinary <select> stays, usable, only without text search.
(function () {
  // The placeholder of the multiple selection. The page passes it in on the
  // script tag, there the translation applies ('components/select_script.html').
  const SUCHEN = document.currentScript.dataset.wert;
  if (!window.TomSelect) return;

  // Options that stand once on the page instead of in every field: the
  // import plan has a selection of all products per row, and repeated per
  // row that could not be transferred (see 'NurGewaehlteOption').
  const vorraete = {};
  function vorrat(kennung) {
    if (!(kennung in vorraete)) {
      const quelle = document.getElementById(kennung);
      vorraete[kennung] = quelle ? JSON.parse(quelle.textContent) : null;
    }
    return vorraete[kennung];
  }

  function ausstatten(feld) {
    if (feld.tomselect || feld.dataset.ohneSuche !== undefined) return;
    const geteilt = feld.dataset.optionen
      ? vorrat(feld.dataset.optionen)
      : null;
    // Tom Select moves the label to its own input field. The original
    // stays in the document, unlabelled and without focus: a screen
    // reader would otherwise find a second, nameless selection field.
    feld.setAttribute("aria-hidden", "true");
    new TomSelect(feld, {
      plugins: feld.multiple ? ["remove_button"] : [],
      maxOptions: null,
      // The empty option is Django's label of the list ("-- neu anlegen --")
      // and does not belong shown as a tick.
      allowEmptyOption: true,
      placeholder: feld.multiple ? SUCHEN : undefined,
      ...(geteilt
        ? {
            options: geteilt,
            valueField: "value",
            labelField: "text",
            searchField: ["text"],
            items: feld.value ? [feld.value] : [],
          }
        : {}),
    });
    // Tom Select types into a text field, and an enter in a text field
    // presses the form's button. A plain selection list does nothing on
    // enter, and neither does this one while its list is closed. With the
    // list open, Tom Select takes the enter itself and chooses.
    feld.tomselect.wrapper.addEventListener("keydown", (ereignis) => {
      if (ereignis.key === "Enter" && !feld.tomselect.isOpen)
        ereignis.preventDefault();
    });
  }

  function alleAusstatten(bereich) {
    bereich.querySelectorAll("select").forEach(ausstatten);
  }

  alleAusstatten(document);

  // After sending in the background the page content is new
  // ('instant_actions.js'), and its selection lists are plain again.
  document.addEventListener("liste:ersetzt", () => alleAusstatten(document));

  // New rows of a formset bring new selection lists.
  document.addEventListener("formset:geaendert", (ereignis) => {
    alleAusstatten(ereignis.target);
    // The formset component hides taken entries via 'hidden' and
    // 'disabled'. Tom Select reads the state only when building, so it
    // needs the hint.
    ereignis.target.querySelectorAll("select").forEach((feld) => {
      if (feld.tomselect) feld.tomselect.sync();
    });
  });
  document.addEventListener("change", (ereignis) => {
    const block = ereignis.target.closest("[data-formset][data-einmalig]");
    if (!block || !block.dataset.einmalig) return;
    block.querySelectorAll("select").forEach((feld) => {
      if (feld.tomselect) feld.tomselect.sync();
    });
  });
})();
