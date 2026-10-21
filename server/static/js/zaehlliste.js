// Without a tick there is nothing to enter: the quantity field appears
// with the tick and empties itself when it falls, otherwise on the next
// opening a quantity would stand for a pack that is not even counted.
//
// Above the column a menu acts on **all rows at once**: every pack, only
// the loose single unit ('data-einzeln') or the scale ('data-wiegen'), each
// to tick or to untick. An entry that would change nothing is disabled, and
// the menu shows only where there is something to tick. Unticking packs
// empties their quantities, so it asks first ('confirm.js'), but only where
// one stands.
//
// The tab "Select articles" saves first if something in the list has
// changed: the form goes the ordinary way, with the tab as 'weiter'. Without
// a change the link does what a link does. What counts as a change is
// measured against the list as the server sent it, also after "Refresh"
// has carried the typed values over into a fresh list.
(function () {
  const WIEGEN = "wiegen";
  const EINZELN = "einzeln";

  function richten(feld) {
    const haken = document.getElementById(feld.dataset.mengeZu);
    if (!haken) return;
    feld.hidden = !haken.checked;
    if (!haken.checked) {
      for (const eingabe of feld.querySelectorAll("input")) eingabe.value = "";
    }
  }

  // Every quantity field names its tick. That is the list of the packs.
  function mengenfelder() {
    return [...document.querySelectorAll("[data-menge-zu]")];
  }

  function gebindehaken() {
    return mengenfelder()
      .map((feld) => document.getElementById(feld.dataset.mengeZu))
      .filter(Boolean);
  }

  // The ticks an entry of the menu acts on.
  function haken(art) {
    if (art === WIEGEN) {
      return [
        ...document.querySelectorAll("input[type=checkbox][data-wiegen]"),
      ];
    }
    const alle = gebindehaken();
    return art === EINZELN
      ? alle.filter((kaestchen) => kaestchen.closest("[data-einzeln]"))
      : alle;
  }

  // Whether a quantity stands next to one of these ticks. Unticking would lose it.
  function mengeEingetragen(liste) {
    return liste.some((kaestchen) => {
      const feld = document.querySelector(
        `[data-menge-zu="${CSS.escape(kaestchen.id)}"]`,
      );
      return (
        feld &&
        [...feld.querySelectorAll("input")].some(
          (eingabe) => eingabe.value.trim() !== "",
        )
      );
    });
  }

  // An entry is pressable only where it changes something: ticking while a
  // tick is missing, unticking while one is set. Unticking packs asks first
  // ('confirm.js'), but only where a quantity stands.
  function menueRichten() {
    const menue = document.querySelector("[data-alle-menue]");
    if (!menue) return;
    let etwas = false;
    for (const eintrag of menue.querySelectorAll("[data-alle]")) {
      const liste = haken(eintrag.dataset.alle);
      const an = eintrag.dataset.richtung === "an";
      etwas = etwas || liste.length > 0;
      eintrag.disabled =
        liste.length === 0 ||
        liste.every((kaestchen) => kaestchen.checked === an);
      if (!eintrag.dataset.frage) continue;
      if (mengeEingetragen(liste))
        eintrag.dataset.rueckfrage = eintrag.dataset.frage;
      else delete eintrag.dataset.rueckfrage;
    }
    menue.hidden = !etwas;
  }

  function alleRichten() {
    mengenfelder().forEach(richten);
    menueRichten();
  }

  document.addEventListener("change", (ereignis) => {
    if (ereignis.target.type !== "checkbox") return;
    const zelle = ereignis.target.closest("[data-gebindezelle]");
    if (!zelle) return;
    zelle.querySelectorAll("[data-menge-zu]").forEach(richten);
    menueRichten();
  });

  // A typed quantity decides whether unticking has to ask.
  document.addEventListener("input", (ereignis) => {
    if (ereignis.target.closest("[data-menge-zu]")) menueRichten();
  });

  document.addEventListener("click", (ereignis) => {
    const eintrag = ereignis.target.closest("[data-alle]");
    if (!eintrag || eintrag.disabled) return;
    const setzen = eintrag.dataset.richtung === "an";
    for (const kaestchen of haken(eintrag.dataset.alle))
      kaestchen.checked = setzen;
    alleRichten();
    // Bootstrap closes the menu, and the focus would stay on the hidden
    // entry. Back to the button that opened it.
    eintrag
      .closest(".dropdown")
      ?.querySelector("[data-bs-toggle]")
      ?.focus({ preventScroll: true });
  });

  // -- Saving before the other tab opens --------------------------------

  function formular(wurzel = document) {
    return wurzel.querySelector("[data-liste] form[data-sofort]");
  }

  // The list as text: ticks, quantities and order. Not the CSRF token, it
  // differs with every response, and not 'weiter', that is set right here.
  function stand(wurzel = document) {
    const form = formular(wurzel);
    if (!form) return "";
    const daten = new URLSearchParams(new FormData(form));
    daten.delete("csrfmiddlewaretoken");
    daten.delete("weiter");
    return daten.toString();
  }

  let gesendet = stand();

  document.addEventListener("liste:ersetzt", (ereignis) => {
    alleRichten();
    // The fresh page says what the server has. The list itself may already
    // carry the typed values again ('soft_reload.js').
    gesendet = stand(ereignis.detail || document);
  });

  document.addEventListener("click", (ereignis) => {
    const verweis = ereignis.target.closest("a[data-vorher-speichern]");
    if (!verweis) return;
    const form = formular();
    if (!form || stand() === gesendet) return;
    const weiter = form.querySelector("input[name=weiter]");
    const speichern = form.querySelector("[data-seitenwechsel]");
    if (!weiter || !speichern) return;
    ereignis.preventDefault();
    weiter.value = verweis.getAttribute("href");
    form.requestSubmit(speichern);
  });

  // Again after a row button: 'instant_actions.js' has replaced the list
  // then, and the new quantity fields do not know their tick yet.
  alleRichten();
})();
