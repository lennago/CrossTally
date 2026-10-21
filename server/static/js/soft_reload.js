/*
 * "Refresh": the lists of the page anew, what is typed in them stays.
 *
 * A pack created in another tab, an article someone else has just taken in:
 * reloading the page would show it, but would throw away every tick and
 * every quantity typed since the last save. The button fetches the page via
 * 'fetch', takes the lists ('data-liste') out of it, carries the state of
 * the fields over (ticks as ticks, texts as texts) and puts the fresh lists
 * in place of the old ones, as 'instant_actions.js' does after a row button.
 * Then 'liste:ersetzt' is announced with the fresh page as 'detail', so the
 * scripts set up the rows again and know what the server sent.
 *
 * A field is recognised by its name. Within a row that names itself
 * ('data-kennung', say the expectation of a counting list row) the row's
 * number in the name does not count, the identity does: rows may have been
 * re-sorted or taken out elsewhere in the meantime, and a tick must stay
 * with its article. A field that no longer exists loses its value, a new
 * one starts as the server sends it. Hidden fields come fresh: the order and
 * the CSRF token belong to the page, not to the person.
 *
 * The texts come in via the data attributes of the own script tag
 * ('components/soft_reload_script.html'). Without script the button is not
 * even shown. Rows that vanished meanwhile take their typed values with
 * them: that is said in a warning, with their number. A session that has
 * expired leads to the sign-in; a flag in 'sessionStorage' lets that page
 * say why the inputs are gone ('login_notice.js').
 */
(function () {
  const TEXT = document.currentScript.dataset;
  const knopf = document.querySelector("[data-neu-laden]");
  if (!knopf || !window.fetch || !window.DOMParser) return;
  if (!document.querySelector("[data-liste]")) return;
  knopf.hidden = false;
  const zerleger = new DOMParser();

  const FELDER = "input, select, textarea";
  const OHNE = new Set(["hidden", "submit", "button", "reset", "file"]);

  function istWahl(feld) {
    return feld.type === "checkbox" || feld.type === "radio";
  }

  // The key of a field: its name, with the row number swapped for the row's
  // identity where the row has one.
  function schluessel(feld) {
    const zeile = feld.closest("[data-kennung]");
    let name = feld.name;
    if (zeile)
      name = `${zeile.dataset.kennung}|${name.replace(/-\d+-/, "-#-")}`;
    return istWahl(feld) ? `${name}=${feld.value}` : name;
  }

  function eingaben(wurzel) {
    const werte = new Map();
    for (const feld of wurzel.querySelectorAll(FELDER)) {
      if (!feld.name || OHNE.has(feld.type)) continue;
      werte.set(schluessel(feld), istWahl(feld) ? feld.checked : feld.value);
    }
    return werte;
  }

  // Returns the keys that found a field again.
  function einsetzen(wurzel, werte) {
    const benutzt = new Set();
    for (const feld of wurzel.querySelectorAll(FELDER)) {
      if (!feld.name || OHNE.has(feld.type)) continue;
      const kennung = schluessel(feld);
      const wert = werte.get(kennung);
      if (wert === undefined) continue;
      benutzt.add(kennung);
      if (istWahl(feld)) feld.checked = wert;
      else feld.value = wert;
    }
    return benutzt;
  }

  // How many rows with a typed value found no field again: their row is gone.
  function verlorene(werte, benutzt) {
    const zeilen = new Set();
    for (const [kennung, wert] of werte) {
      if (benutzt.has(kennung) || wert === "" || wert === false) continue;
      zeilen.add(kennung.split("|")[0]);
    }
    return zeilen.size;
  }

  function melden(text) {
    const bereich = document.querySelector("[data-meldungen]");
    if (!bereich) return;
    const meldung = document.createElement("div");
    meldung.className = "alert alert-warning fade show shadow-sm";
    meldung.setAttribute("role", "alert");
    meldung.dataset.vergaenglich = "";
    meldung.textContent = text;
    const huelle = document.createElement("div");
    huelle.className = "meldungen";
    huelle.append(meldung);
    bereich.replaceChildren(huelle);
  }

  async function auffrischen() {
    const gesendet = window.sofortGesendet?.();
    const antwort = await window.imHintergrund(window.location.href);
    // The session has expired: the server answers 401 and not with the
    // sign-in page ('StaffOnlyMiddleware'). Reloading leads there, and that
    // page says why the inputs are gone ('login_notice.js').
    if (antwort.status === 401) {
      sessionStorage.setItem("abgemeldet", "1");
      window.location.reload();
      return;
    }
    if (!antwort.ok) throw new Error(antwort.status);
    // Another page after all: then the browser goes there.
    if (new URL(antwort.url).pathname !== window.location.pathname) {
      window.location.assign(antwort.url);
      return;
    }
    const frisch = zerleger.parseFromString(await antwort.text(), "text/html");
    // A row button went out meanwhile ('instant_actions.js'). Its answer
    // shows the list after its step, this one may be older. Until 10/2026
    // the later answer replaced the list, even the older one.
    if (window.sofortGesendet && window.sofortGesendet() !== gesendet) return;
    const hier = [...document.querySelectorAll("[data-liste]")];
    const dort = [...frisch.querySelectorAll("[data-liste]")];
    if (hier.length !== dort.length) throw new Error("Listen passen nicht");

    let weg = 0;
    hier.forEach((alt, stelle) => {
      const werte = eingaben(alt);
      alt.innerHTML = dort[stelle].innerHTML;
      weg += verlorene(werte, einsetzen(alt, werte));
    });
    // Counts and the like outside the lists, as after a row button.
    for (const stelle of document.querySelectorAll("[data-ersetzen]")) {
      const ersatz = frisch.querySelector(
        `[data-ersetzen="${stelle.dataset.ersetzen}"]`,
      );
      if (ersatz) stelle.innerHTML = ersatz.innerHTML;
    }
    document.dispatchEvent(
      new CustomEvent("liste:ersetzt", { detail: frisch }),
    );
    if (weg) melden(TEXT.weggefallen.replace("{anzahl}", weg));
  }

  // Busy, not disabled: a disabled button drops the focus to the page, and
  // whoever refreshed by keyboard started at the top again (until 10/2026).
  // Busy also while a save of the order is still on its way: the fresh list
  // can only show it once the server has it ('sortable.js'). If a row button
  // or "Save" waits for that save too, nothing is fetched here. Its answer
  // brings the list fresh, and a second request beside it would only race it.
  // The same for a row button already on its way ('instant_actions.js').
  let laeuft = false;
  knopf.addEventListener("click", async () => {
    if (laeuft) return;
    laeuft = true;
    knopf.setAttribute("aria-busy", "true");
    // The lists stand still until they are new. A row dragged meanwhile
    // would save an order the fresh list replaces ('sortable.js').
    const listen = [...document.querySelectorAll("[data-liste]")];
    for (const liste of listen) liste.setAttribute("aria-busy", "true");
    try {
      if (window.reihenfolgeAbwarten && !(await window.reihenfolgeAbwarten()))
        return;
      if (window.sofortAbwarten && !(await window.sofortAbwarten())) return;
      await auffrischen();
    } catch (fehler) {
      melden(TEXT.wert);
    } finally {
      laeuft = false;
      knopf.removeAttribute("aria-busy");
      for (const liste of listen) liste.removeAttribute("aria-busy");
    }
  });
})();
