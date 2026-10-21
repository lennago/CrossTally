/*
 * The multiple selections in the filter: product families and "Carried in".
 *
 * Chosen is in an expanded menu, applied only with "Apply". Whoever closes
 * the menu without "Apply" finds the applied state again on the next
 * opening, otherwise a half selection would go out unnoticed with the next
 * other filter.
 *
 * Include before 'live_filter.js': that intercepts the submit.
 */
(function () {
  // The words on the button stand translated on the button itself ('data-text-*').
  const ZUSTAENDE = [
    { wert: "", text: "egal", klasse: "btn-outline-secondary" },
    { wert: "mit", text: "muss", klasse: "btn-success" },
    { wert: "ohne", text: "nicht", klasse: "btn-danger" },
  ];

  function zeigen(knopf) {
    const feld = knopf.previousElementSibling;
    const zustand =
      ZUSTAENDE.find((z) => z.wert === feld.value) || ZUSTAENDE[0];
    knopf.textContent =
      knopf.dataset[
        "text" + zustand.text[0].toUpperCase() + zustand.text.slice(1)
      ] ?? zustand.text;
    for (const z of ZUSTAENDE) knopf.classList.toggle(z.klasse, z === zustand);
  }

  function stand(wahl) {
    return [...wahl.querySelectorAll("input")].map((feld) =>
      feld.type === "checkbox" ? feld.checked : feld.value,
    );
  }

  function zuruecksetzen(wahl, werte) {
    [...wahl.querySelectorAll("input")].forEach((feld, stelle) => {
      if (feld.type === "checkbox") feld.checked = werte[stelle];
      else feld.value = werte[stelle];
    });
    for (const knopf of wahl.querySelectorAll("[data-hauszustand]"))
      zeigen(knopf);
  }

  for (const wahl of document.querySelectorAll("[data-sammelwahl]")) {
    let angewandt = stand(wahl);
    let uebernommen = false;
    const uebernehmen = wahl.querySelector("[data-uebernehmen]");

    // Without a tick there would be nothing to show. Then better not apply.
    function pruefen() {
      const haken = wahl.querySelectorAll("input[type=checkbox]");
      uebernehmen.disabled =
        haken.length > 0 && ![...haken].some((h) => h.checked);
    }

    for (const knopf of wahl.querySelectorAll("[data-hauszustand]")) {
      zeigen(knopf);
      knopf.addEventListener("click", () => {
        const feld = knopf.previousElementSibling;
        const stelle = ZUSTAENDE.findIndex((z) => z.wert === feld.value);
        feld.value = ZUSTAENDE[(stelle + 1) % ZUSTAENDE.length].wert;
        zeigen(knopf);
      });
    }
    for (const knopf of wahl.querySelectorAll("[data-alle]")) {
      knopf.addEventListener("click", () => {
        for (const haken of wahl.querySelectorAll("input[type=checkbox]")) {
          haken.checked = knopf.dataset.alle === "1";
        }
        pruefen();
      });
    }
    // A tick on a family sets every family below it the same way
    // ('data-oben' names the entry above). Afterwards each of them can be
    // set on its own: only a change of the entry above reaches down.
    function darunter(wert) {
      const kinder = [
        ...wahl.querySelectorAll(`input[type=checkbox][data-oben="${wert}"]`),
      ];
      return kinder.flatMap((kind) => [kind, ...darunter(kind.value)]);
    }
    wahl.addEventListener("change", (ereignis) => {
      const haken = ereignis.target;
      if (haken.type === "checkbox")
        for (const kind of darunter(haken.value)) kind.checked = haken.checked;
      pruefen();
    });

    uebernehmen.addEventListener("click", () => {
      uebernommen = true;
      angewandt = stand(wahl);
      const umschalter = wahl.querySelector("[data-bs-toggle]");
      if (window.bootstrap)
        window.bootstrap.Dropdown.getOrCreateInstance(umschalter).hide();
      // The menu closes with the focus inside it. Back to its button.
      umschalter?.focus({ preventScroll: true });
    });
    wahl.addEventListener("hide.bs.dropdown", () => {
      if (!uebernommen) zuruecksetzen(wahl, angewandt);
      uebernommen = false;
      pruefen();
    });
  }
})();
