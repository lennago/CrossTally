/*
 * A list still stands as one left it when coming back.
 *
 * Remembered per page (path) in the 'sessionStorage': the query (i.e.
 * filters, sort and page number, as 'live_filter.js' writes them into the
 * address) and how far was scrolled. Which groups were expanded 'expand.js'
 * already remembers itself. That is why this script runs after it: only once
 * the groups are open is the height of the page right and with it the scroll
 * position.
 *
 * The state comes back in two ways:
 *
 *   - The breadcrumbs get the remembered query attached to their 'href'.
 *     That way the server already renders the filtered list: no reloading,
 *     no flash of the unfiltered view. Only the breadcrumbs and not every
 *     link: whoever opens the list through the main menu wants to see all
 *     of it, and "Reset filter" has to reset.
 *   - The scroll position is set at load time if the address is exactly the
 *     remembered one and the browser has not already scrolled itself. On the
 *     back button it does that, and it hits it more precisely than we do.
 *
 * In the 'sessionStorage' and not in the session on the server: the state
 * belongs to this one tab. Two lists open side by side should not switch
 * each other over, the same consideration as in 'expand.js'.
 *
 * The exception is the check list ('data-ansicht-dauerhaft'): there work goes
 * on over days, and it always comes back as one last had it. Its query is
 * remembered by the server, its scroll position by this script in the
 * 'localStorage'.
 */
(function () {
  const SCHLUESSEL = "ansicht:";
  // The check list remembers its state beyond the tab: there work goes on
  // over days ('data-ansicht-dauerhaft', 'InventurPruefenView._ansicht').
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

  //: Only pages with a filter form remember their query. Elsewhere something
  //: else is in the address (say the origin 'von') that a breadcrumb link
  //: should not inherit.
  const filterbar = !!document.querySelector("form[data-filter]");

  // The breadcrumbs lead to ordinary lists. Their state lies per tab.
  function lesen(pfad, ablage = window.sessionStorage) {
    try {
      return JSON.parse(ablage.getItem(SCHLUESSEL + pfad));
    } catch (fehler) {
      // Blocked storage or nonsense in it: then without memory.
      return null;
    }
  }

  function merken() {
    const stand = { scrollY: Math.round(window.scrollY) };
    if (filterbar) stand.abfrage = window.location.search;
    try {
      speicher.setItem(
        SCHLUESSEL + window.location.pathname,
        JSON.stringify(stand),
      );
    } catch (fehler) {
      /* see above */
    }
  }

  window.addEventListener("pagehide", merken);
  // On mobile devices a page is often only put aside instead of left. A
  // 'pagehide' then may not arrive any more.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") merken();
  });

  for (const verweis of document.querySelectorAll(".breadcrumb a[href]")) {
    const ziel = new URL(verweis.href, window.location.href);
    // Only own pages, and only where the link brings nothing of its own.
    if (ziel.origin !== window.location.origin || ziel.search) continue;
    const stand = lesen(ziel.pathname);
    if (stand && stand.abfrage) verweis.href = ziel.pathname + stand.abfrage;
  }

  const stand = lesen(window.location.pathname, speicher);
  if (!stand || !stand.scrollY || window.scrollY) return;
  // Another query means another list. The old height would not fit any more.
  if (filterbar && (stand.abfrage || "") !== window.location.search) return;

  let gesetzt = null;
  function scrollen() {
    window.scrollTo(0, stand.scrollY);
    gesetzt = Math.round(window.scrollY);
  }

  scrollen();
  // Follow up once images and fonts are in place: until then the page may
  // be too short and the jump landed too far up. Whoever has scrolled
  // themselves in the meantime is left alone.
  window.addEventListener(
    "load",
    () => {
      if (Math.round(window.scrollY) === gesetzt) scrollen();
    },
    { once: true },
  );
})();
