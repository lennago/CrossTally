/*
 * Filters act at once, and the search already while typing.
 *
 * The same trick as in 'instant_actions.js': submitted is the same form,
 * only via 'fetch', and from the response the list area '[data-liste]' and
 * the places with '[data-ersetzen]' are taken over. So still only the
 * template renders: no second truth about what a row looks like, and
 * without JavaScript "Apply" remains the way.
 *
 * The form itself is never replaced: otherwise the cursor would be out of
 * the search field after every letter.
 */
(function () {
  const formular = document.querySelector("form[data-filter]");
  const bereich = document.querySelector("[data-liste]");
  if (!formular || !bereich || !window.fetch || !window.DOMParser) return;

  const zerleger = new DOMParser();
  // "Apply" now has nothing to do, because the filter is already applied.
  for (const knopf of formular.querySelectorAll("[data-anwenden]"))
    knopf.hidden = true;

  //: This long after the last keystroke is waited. Short enough to feel
  //: "live", long enough that a typed word does not trigger six requests.
  const WARTEN = 250;
  let uhr = null;
  let laufende = null;
  let schleierUhr = null;

  function ziel() {
    // Empty fields do not belong in the address. They only make it long.
    // The page number drops away by itself: it is not in the form, and
    // after a change page seven would be wrong anyway.
    //
    // A multiple selection that matches its default stays out too: without
    // an entry exactly this default applies on the server, and forty-five
    // product groups would make the address unreadable.
    const wieVorgabe = new Set();
    for (const wahl of formular.querySelectorAll("[data-sammelwahl]")) {
      const haken = [
        ...wahl.querySelectorAll("input[type=checkbox][data-vorgabe]"),
      ];
      if (
        haken.length &&
        haken.every((h) => h.checked === (h.dataset.vorgabe === "1"))
      ) {
        wieVorgabe.add(haken[0].name);
      }
    }
    const daten = new URLSearchParams();
    for (const [name, wert] of new FormData(formular)) {
      if (wert && !wieVorgabe.has(name)) daten.append(name, wert);
    }
    const abfrage = daten.toString();
    return window.location.pathname + (abfrage ? `?${abfrage}` : "");
  }

  // One veil at a time: a new search takes over the one before. Until
  // 10/2026 the timer of an overtaken search went on running. 150 ms after
  // it the list turned grey and stayed so until the next search.
  function schleierAn() {
    clearTimeout(schleierUhr);
    schleierUhr = setTimeout(() => bereich.classList.add("opacity-50"), 150);
  }

  function schleierAus() {
    clearTimeout(schleierUhr);
    bereich.classList.remove("opacity-50");
  }

  async function anwenden() {
    // On this page unsaved notes can be open. They have to go out before
    // the list comes anew and overwrites them.
    if (window.notizenSichern) await window.notizenSichern();

    const adresse = ziel();
    if (laufende) laufende.abort();
    const diese = new AbortController();
    laufende = diese;
    schleierAn();
    try {
      const antwort = await window.imHintergrund(adresse, {
        signal: diese.signal,
      });
      // The session has expired ('StaffOnlyMiddleware'). With open notes
      // the page stays, and their state says so ('notes.js'). Otherwise the
      // ordinary way below leads to the sign-in, and that page says why
      // ('login_notice.js').
      if (antwort.status === 401) {
        if (window.notizenAbgemeldet?.()) return;
        sessionStorage.setItem("abgemeldet", "1");
      }
      if (!antwort.ok) throw new Error(antwort.status);
      const frisch = zerleger.parseFromString(
        await antwort.text(),
        "text/html",
      );

      const neu = frisch.querySelector("[data-liste]");
      if (!neu) throw new Error("Listenbereich fehlt");
      bereich.innerHTML = neu.innerHTML;

      // Hit count and the like outside the list.
      for (const stelle of document.querySelectorAll("[data-ersetzen]")) {
        const ersatz = frisch.querySelector(
          `[data-ersetzen="${stelle.dataset.ersetzen}"]`,
        );
        if (ersatz) stelle.innerHTML = ersatz.innerHTML;
      }

      // Carry the address along so reloading, bookmarking and passing on
      // hit the filtered state.
      window.history.replaceState(null, "", adresse);

      // The list came fresh from the server. Whoever computes with its
      // numbers (the check view, 'refresh.js') learns of it and gets the whole response.
      document.dispatchEvent(
        new CustomEvent("liste:ersetzt", { detail: frisch }),
      );
    } catch (fehler) {
      // An aborted run is the normal case while typing. Only real errors
      // lead back to the ordinary way.
      if (fehler.name !== "AbortError") formular.submit();
    } finally {
      // Overtaken by a newer search: the veil belongs to that one now.
      if (laufende === diese) schleierAus();
    }
  }

  function anstossen(verzoegert) {
    clearTimeout(uhr);
    uhr = setTimeout(anwenden, verzoegert ? WARTEN : 0);
  }

  // Selection lists act at once, typing after a short pause. Tom Select
  // reports its changes as 'change' on the real field as well. Except
  // the multiple selections ('[data-sammelwahl]'): there only "Apply"
  // acts, otherwise every tick would trigger a search of its own.
  formular.addEventListener("change", (ereignis) => {
    if (ereignis.target.closest("[data-sammelwahl]")) return;
    anstossen(
      ereignis.target.type === "search" ||
        ereignis.target.tagName === "TEXTAREA",
    );
  });
  formular.addEventListener("input", (ereignis) => {
    if (ereignis.target.closest("[data-sammelwahl]")) return;
    if (ereignis.target.tagName === "INPUT") anstossen(true);
  });

  // The enter key in the search field should not reload the page, because
  // the result is already there.
  formular.addEventListener("submit", (ereignis) => {
    ereignis.preventDefault();
    anstossen(false);
  });
})();
