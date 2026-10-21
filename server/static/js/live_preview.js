/*
 * The preview follows the choice at once.
 *
 * Copying a location and merging show first what would happen. The choice
 * stands in the form that writes, and the server writes only for the choice
 * whose preview was on screen ('gezeigt'). Otherwise it shows that preview
 * first. Without this script that is the way: choose, press the button, look
 * at the preview, press again.
 *
 * With it the preview comes as soon as the choice changes, the same trick as
 * in 'live_filter.js': the page is fetched with the choice in the address, and
 * the area '[data-vorschau]' is taken over. The choice itself is never
 * replaced, so the focus stays in it.
 */
(function () {
  const wahl = document.querySelector("select[data-vorschau-wahl]");
  const vorschau = document.querySelector("[data-vorschau]");
  if (!wahl || !vorschau || !window.fetch || !window.DOMParser) return;

  const zerleger = new DOMParser();
  let laufende = null;
  let schleierUhr = null;

  function fehlerhaft(feld) {
    return Boolean(feld && feld.classList.contains("is-invalid"));
  }

  function gezeigt() {
    const feld = vorschau.querySelector("input[name=gezeigt]");
    return feld ? feld.value : "";
  }

  // "Preview" has nothing left to do, and it goes rather than hides. Hidden,
  // it would still be the form's first button: an enter in the name field
  // would press it and merge without the question before. With an objection
  // to the choice it stays, because then the page comes as a whole anyway.
  if (!fehlerhaft(wahl))
    for (const knopf of document.querySelectorAll("[data-vorschau-knopf]"))
      knopf.remove();

  function ziel() {
    const daten = new URLSearchParams();
    if (wahl.value) daten.set(wahl.name, wahl.value);
    // A name typed by hand travels along ('data-mitnehmen'). An untouched
    // one is the server's suggestion, and that may change with the choice.
    // Once in the address, a name counts as typed.
    const bisher = new URLSearchParams(window.location.search);
    for (const feld of vorschau.querySelectorAll("[data-mitnehmen]")) {
      if (feld.value !== feld.defaultValue || bisher.has(feld.name))
        daten.set(feld.name, feld.value);
    }
    const abfrage = daten.toString();
    return window.location.pathname + (abfrage ? `?${abfrage}` : "");
  }

  async function zeigen() {
    const adresse = ziel();
    if (laufende) laufende.abort();
    const diese = new AbortController();
    laufende = diese;
    // Until the new preview is there, the old one must not be confirmed.
    for (const knopf of vorschau.querySelectorAll("button"))
      knopf.disabled = true;
    clearTimeout(schleierUhr);
    schleierUhr = setTimeout(() => vorschau.classList.add("opacity-50"), 150);
    try {
      const antwort = await window.imHintergrund(adresse, {
        signal: diese.signal,
      });
      // The session has expired ('StaffOnlyMiddleware'). The ordinary way
      // below leads to the sign-in, and that page says why ('login_notice.js').
      if (antwort.status === 401) sessionStorage.setItem("abgemeldet", "1");
      if (!antwort.ok) throw new Error(antwort.status);
      const frisch = zerleger.parseFromString(
        await antwort.text(),
        "text/html",
      );
      const neu = frisch.querySelector("[data-vorschau]");
      // An objection to the choice stands at the choice, outside the area.
      // That is rare, and then the page comes as a whole.
      const einwand =
        fehlerhaft(wahl) ||
        fehlerhaft(frisch.querySelector("select[data-vorschau-wahl]"));
      if (!neu || einwand) throw new Error("Vorschau fehlt");
      vorschau.innerHTML = neu.innerHTML;
      // Reloading and passing on show the same choice.
      window.history.replaceState(null, "", adresse);
    } catch (fehler) {
      // Overtaken by a newer choice: that one brings its own preview.
      if (fehler.name !== "AbortError") window.location.assign(adresse);
    } finally {
      if (laufende === diese) {
        clearTimeout(schleierUhr);
        vorschau.classList.remove("opacity-50");
      }
    }
  }

  // Tom Select reports its choice as 'change' on the real field as well. An
  // enter in the choice chooses and goes no further: 'select.js' catches it
  // for every searchable list, so that it does not press the form's button.
  wahl.addEventListener("change", zeigen);

  // After reloading the browser may put back a choice the preview does
  // not show.
  if (gezeigt() && wahl.value !== gezeigt()) zeigen();
})();
