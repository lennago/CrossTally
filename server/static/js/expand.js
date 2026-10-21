/*
 * Grouped lists remember which groups were expanded.
 *
 * The groups ('<details data-gruppe="...">') come collapsed from the server.
 * What someone opens is then in the 'sessionStorage', per page (path without
 * query, so a filter does not reset the view) and per tab. That way the view
 * stays the same after submitting a form, after a row button and when going
 * back through the breadcrumbs.
 *
 * Deliberately in the browser and not in the session on the server: that
 * would apply to all tabs at once, and two lists open side by side would flip
 * each other. Remembered is only what is **open**: a group a filter is
 * currently hiding thus keeps its state.
 *
 * The check list ('data-ansicht-dauerhaft') remembers it in the
 * 'localStorage', beyond the tab: there work goes on over days.
 *
 * Where work is done instead of looked up (import plan), the groups come
 * with 'data-anfangs-offen'. There it is the other way round: remembered is
 * what is **closed**. A group with an error message stays open in any case:
 * otherwise one would search after submitting for what went wrong.
 */
(function () {
  const SCHLUESSEL = "aufgeklappt:" + window.location.pathname;
  // The check list remembers its state beyond the tab: there work goes on
  // over days ('data-ansicht-dauerhaft', see 'view_state.js').
  const speicher = ablage();

  function ablage() {
    try {
      return document.querySelector("[data-ansicht-dauerhaft]")
        ? window.localStorage
        : window.sessionStorage;
    } catch (fehler) {
      return null;
    }
  }

  function lesen() {
    try {
      return new Set(JSON.parse(speicher.getItem(SCHLUESSEL)) || []);
    } catch (fehler) {
      // Blocked storage or nonsense in it: then without memory.
      return new Set();
    }
  }

  function schreiben(offen) {
    try {
      speicher.setItem(SCHLUESSEL, JSON.stringify([...offen]));
    } catch (fehler) {
      /* see above */
    }
  }

  // Under which entry the group is in the storage, and what it means.
  function eintrag(gruppe) {
    const anfangsOffen = gruppe.hasAttribute("data-anfangs-offen");
    return {
      name: (anfangsOffen ? "zu:" : "") + gruppe.dataset.gruppe,
      heisstOffen: !anfangsOffen,
    };
  }

  function herstellen(wurzel) {
    const gemerkt = lesen();
    const gruppen = wurzel.querySelectorAll("details[data-gruppe]");
    for (const gruppe of gruppen) {
      const { name, heisstOffen } = eintrag(gruppe);
      gruppe.open =
        gemerkt.has(name) === heisstOffen ||
        gruppe.querySelector(".errorlist") !== null;
    }
    for (const knoepfe of wurzel.querySelectorAll("[data-aufklapp-knoepfe]")) {
      knoepfe.hidden = gruppen.length === 0;
    }
  }

  // 'toggle' does not bubble. Hence in the capture phase on the document.
  document.addEventListener(
    "toggle",
    (ereignis) => {
      const gruppe = ereignis.target;
      if (!(gruppe instanceof HTMLDetailsElement) || !gruppe.dataset.gruppe)
        return;
      const gemerkt = lesen();
      const { name, heisstOffen } = eintrag(gruppe);
      if (gruppe.open === heisstOffen) gemerkt.add(name);
      else gemerkt.delete(name);
      schreiben(gemerkt);
    },
    true,
  );

  document.addEventListener("click", (ereignis) => {
    const knopf = ereignis.target.closest("[data-alle-gruppen]");
    if (!knopf) return;
    const auf = knopf.dataset.alleGruppen === "auf";
    // The remembering is done by 'toggle' per group.
    for (const gruppe of document.querySelectorAll("details[data-gruppe]"))
      gruppe.open = auf;
  });

  herstellen(document);

  // Filters and row buttons swap the list area. The fresh groups come
  // collapsed again and need their state back.
  const beobachtet = new WeakSet();

  function beobachten() {
    if (!window.MutationObserver) return;
    for (const bereich of document.querySelectorAll("[data-liste]")) {
      if (beobachtet.has(bereich)) continue;
      beobachtet.add(bereich);
      new MutationObserver(() => herstellen(bereich)).observe(bereich, {
        childList: true,
      });
    }
  }

  beobachten();
  // A form outside every list swaps the whole page content
  // ('instant_actions.js'). The lists in it are new elements then.
  document.addEventListener("liste:ersetzt", () => {
    herstellen(document);
    beobachten();
  });
})();
