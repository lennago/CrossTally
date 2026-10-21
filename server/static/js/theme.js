/*
 * Light or dark. The choice applies to the site, the administration and the app.
 *
 * Included in the <head> via 'components/theme_script.html', classic and
 * without 'defer': that way it runs before the first paint. If it came later,
 * the light version would briefly flash on every page change.
 *
 * A file and no inline script, so the Content-Security-Policy gets by without
 * a nonce ('SECURE_CSP' in 'settings.py'): what the page knows stands as
 * 'data-am-konto' and 'data-adresse' on the script tag.
 */
(function () {
  const SCHLUESSEL = "theme";
  const WAHLEN = ["auto", "light", "dark"];
  const dunkel = window.matchMedia("(prefers-color-scheme: dark)");
  // Both are passed in by the page on the script tag. Signed out it is empty.
  // 'currentScript' exists only while the script runs, hence right here.
  const AM_KONTO = document.currentScript.dataset.amKonto || "";
  const ADRESSE = document.currentScript.dataset.adresse || "";

  function imBrowser() {
    // In private windows the access itself can throw.
    try {
      return localStorage.getItem(SCHLUESSEL) || "auto";
    } catch (fehler) {
      return "auto";
    }
  }

  function merken(wahl) {
    try {
      localStorage.setItem(SCHLUESSEL, wahl);
    } catch (fehler) {
      // Then the choice in the browser applies only to this page.
    }
  }

  function gewaehlt() {
    return WAHLEN.includes(AM_KONTO) ? AM_KONTO : imBrowser();
  }

  function anwenden(wahl) {
    const wurzel = document.documentElement;
    // 'theme' is read by the admin, 'bs-theme' by Bootstrap on the site.
    wurzel.dataset.theme = wahl;
    wurzel.dataset.bsTheme =
      wahl === "dark" || (wahl === "auto" && dunkel.matches) ? "dark" : "light";
    for (const eintrag of document.querySelectorAll("[data-thema]")) {
      eintrag.setAttribute(
        "aria-current",
        String(eintrag.dataset.thema === wahl),
      );
    }
  }

  // As 'csrfMarke' in 'shared.js' reads it, which is not there yet in the
  // head and not at all in the administration. Of two cookies of that name
  // Django takes the last one. Until 10/2026 this took the first, and beside
  // a second one Django refused the choice (403).
  function csrf() {
    const NAME = "csrftoken=";
    const eintraege = document.cookie
      .split("; ")
      .filter((teil) => teil.startsWith(NAME));
    const eintrag = eintraege[eintraege.length - 1];
    return eintrag ? decodeURIComponent(eintrag.slice(NAME.length)) : "";
  }

  let aktuell = gewaehlt();
  // The account wins. The browser remembers it for the admin and the login page.
  if (AM_KONTO) merken(aktuell);
  anwenden(aktuell);

  // Delegated, because the buttons only stand in the document later.
  document.addEventListener("click", (ereignis) => {
    const eintrag = ereignis.target.closest("[data-thema]");
    if (!eintrag) return;
    ereignis.preventDefault();
    aktuell = eintrag.dataset.thema;
    merken(aktuell);
    anwenden(aktuell);
    if (!ADRESSE) return;
    // To the account, so the app and the next device see the same choice.
    // If it fails, the choice at least applies in this browser.
    fetch(ADRESSE, {
      method: "PATCH",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      credentials: "same-origin",
      body: JSON.stringify({ darstellung: aktuell }),
    }).catch(() => {});
  });

  // Now the buttons are there and get their tick.
  document.addEventListener("DOMContentLoaded", () => anwenden(aktuell));

  // "Automatic" also means: follow along while the page is open.
  dunkel.addEventListener("change", () => {
    if (aktuell === "auto") anwenden("auto");
  });
})();
