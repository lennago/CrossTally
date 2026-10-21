// The packs do not exist yet when filling in. Linked is therefore by
// name. What is typed at the top shows up at the bottom as a choice. On
// submit the server rebuilds the same list from the names.
// Without JavaScript the packs the product already has stay.
(function () {
  const auswahlen = [...document.querySelectorAll("[data-gebindeauswahl]")];
  const block = document.querySelector("[data-formset][data-prefix='gebinde']");
  if (!auswahlen.length || !block) return;

  const vorhanden = new Set(
    JSON.parse(document.getElementById("vorhandene-gebinde").textContent),
  );

  function checkbox(prefix, name, nummer, angehakt) {
    const feld = document.createElement("input");
    feld.type = "checkbox";
    feld.className = "form-check-input";
    feld.name = prefix;
    feld.value = name;
    feld.checked = angehakt;
    feld.id = `${prefix}-neu-${nummer}`;

    const beschriftung = document.createElement("label");
    beschriftung.className = "form-check-label";
    beschriftung.htmlFor = feld.id;
    beschriftung.textContent = name;

    const huelle = document.createElement("div");
    huelle.className = "form-check form-check-inline";
    huelle.dataset.getippt = "";
    huelle.append(feld, beschriftung);
    return huelle;
  }

  function aktualisieren() {
    // Query fresh: the plus button appends new rows at any time.
    // Removed rows stay hidden and do not count.
    const getippt = [...block.querySelectorAll("input[name$='-name']")]
      .filter((feld) => !feld.closest("[data-formset-reihe]").hidden)
      .map((feld) => feld.value.trim())
      .filter(Boolean);
    const namen = [...new Set(getippt)].filter((name) => !vorhanden.has(name));

    for (const auswahl of auswahlen) {
      const angehakt = new Set(
        [...auswahl.querySelectorAll("[data-getippt] input:checked")].map(
          (feld) => feld.value,
        ),
      );
      auswahl.querySelectorAll("[data-getippt]").forEach((alt) => alt.remove());
      namen.forEach((name, nummer) =>
        auswahl.append(
          checkbox(auswahl.dataset.prefix, name, nummer, angehakt.has(name)),
        ),
      );
    }
  }

  // One listener on the block suffices: the events of new fields bubble up.
  block.addEventListener("input", aktualisieren);
  // Adding and removing is reported separately by the formset component,
  // because emptying a row by script triggers no 'input'.
  block.addEventListener("formset:geaendert", aktualisieren);
  aktualisieren();
})();
