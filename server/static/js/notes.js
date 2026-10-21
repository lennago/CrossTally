/*
 * Save notes without anyone having to think of it.
 *
 * On this page one types and then clicks on: onto an article, back to the
 * overview, onto a filter. Without this the note just written would be gone
 * every time, and one notices only when looking for it.
 *
 * Two paths, because neither carries alone:
 *
 * 1. **When leaving a field** exactly this one note is sent. The view takes
 * along only what was in the form, so one field is enough.
 * 2. **When leaving the page** everything still unsaved goes out via
 * 'sendBeacon', because that survives closing the tab, while a 'fetch' would
 * be aborted.
 *
 * Sent twice does no harm: saving goes through 'update_or_create'.
 * The button "Save notes" sends everything the same way and stays on the
 * page. Without JavaScript it submits the form as ever.
 *
 * Sent is always the **whole row**, i.e. note and tick together: the view
 * evaluates the tick only for rows that were in the form, and a note field
 * sent alone would otherwise take it away.
 *
 * Every row carries what it showed ('notiz_vorher_<pk>', 'sollstattist_vorher_<pk>').
 * The view stores only what differs from it, so a row nobody touched here
 * changes nothing, however old the page is. After saving, the shown state
 * moves along to what was sent: otherwise the next submit would send the
 * same change once more and overwrite what someone else wrote since. A 400
 * that stored the other rows names the fields it left ('X-Nicht-Gespeichert'),
 * and only those keep what they showed.
 *
 * Where the inventory takes the difference by hand, its field
 * ('differenz_<pk>') goes the same way as the note. A difference that is no
 * number comes back with the server's words, which then stand here.
 *
 * The page carries the remarks' state it was built with ('bemerkungsstand').
 * Closing stops when someone else changed a note, a tick or a difference
 * since. The page's own saves are no news to it: their answer brings the new
 * state ('X-Bemerkungsstand'), and closing waits for a save on its way.
 *
 * If the session has expired, the text stays in its field and the state
 * says so, also when a filter or "Refresh values" hears it first
 * ('notizenAbgemeldet'). Signed in anew in another tab, "Save notes" here
 * sends it: the CSRF token comes from the cookie at sending time ('marke').
 * Reloading instead would lead to the sign-in and take the text along.
 */
(function () {
  // The texts are passed in by the page on the script tag. There the
  // translation applies ('components/notes_script.html').
  const TEXT = document.currentScript.dataset;
  // Explicitly the check form: the first POST form of the page is the
  // sign-out in the navigation bar, and there is no note there.
  const formular = document.querySelector("form[data-pruefliste]");
  const anzeige = document.querySelector("[data-notizstand]");
  if (!formular || !window.fetch) return;

  const feldMarke = formular.querySelector("[name=csrfmiddlewaretoken]");
  // The remarks' state this page carries. Every save sends it, and the answer
  // brings the state after the page's own writes, as long as nobody else
  // wrote in between ('notizen_speichern'). Closing compares it.
  const feldStand = formular.querySelector("[data-bemerkungsstand-feld]");
  const offen = new Map(); // field name -> last typed text
  // One save at a time. A row asked for while one is on its way waits here
  // and goes out once it is answered, with what its fields hold then. Until
  // 10/2026 such a save was dropped: "Notizen speichern" pressed while
  // leaving a field sent nothing, and a row whose save had failed stayed
  // unsent although the status said nothing.
  const wartend = new Set(); // row numbers
  let lauf = null; // the saves on their way, until nothing waits
  let abschluss = null; // the closing button, while it waits for them

  // The CSRF token as the browser holds it now, and the page's own only
  // without it ('shared.js'). After signing in anew in another tab the page's
  // own is refused, and until 10/2026 the notes could then only be copied.
  function marke() {
    return window.csrfMarke(feldMarke.value);
  }

  function melden(text, klasse) {
    if (!anzeige) return;
    anzeige.textContent = text;
    anzeige.className = `small ms-2 ${klasse}`;
  }

  function daten(eintraege) {
    const paket = new FormData();
    paket.append("csrfmiddlewaretoken", marke());
    // Tells the view that this is saving on the side: it answers briefly
    // and stores no message that would otherwise pop up at some point
    // on an entirely different page.
    paket.append("nebenbei", "1");
    if (feldStand) paket.append("bemerkungsstand", feldStand.value);
    for (const [name, wert] of eintraege) paket.append(name, wert);
    return paket;
  }

  // Saves these rows, after the save on its way if there is one. Back comes
  // a promise that holds until nothing waits any more: whoever replaces
  // the list waits for it ('notizenSichern').
  function sichern(nummern) {
    for (const pk of nummern) wartend.add(pk);
    if (!lauf) {
      lauf = abarbeiten();
      lauf.finally(() => {
        lauf = null;
      });
    }
    return lauf;
  }

  async function abarbeiten() {
    while (wartend.size) {
      const nummern = [...wartend];
      wartend.clear();
      await senden(nummern.flatMap(zeilenfelder));
    }
  }

  // The view answers a request in the background ('imHintergrund') with 204
  // instead of the whole page. Nothing of it is of use here.
  async function senden(eintraege) {
    if (!eintraege.length) return;
    melden(TEXT.laeuft, "text-body-secondary");
    try {
      const antwort = await window.imHintergrund(
        formular.action || window.location.href,
        { method: "POST", body: daten(eintraege) },
      );
      // Also with a 400: the rows it did not name are written.
      const stand = antwort.headers.get("X-Bemerkungsstand");
      if (stand && feldStand) feldStand.value = stand;
      // A 400 names what was not stored, a 409 that the inventory was
      // closed meanwhile, both in the account's language. What was not
      // stored stays open. A 401 means the session has expired: the server
      // answers so instead of with the sign-in page ('StaffOnlyMiddleware').
      // Every other refusal brings a page, and its source is no message
      // (until 10/2026 it stood here).
      if (antwort.status === 400) {
        // The rest is stored and moves along as after a 204. Until 10/2026
        // it kept what it showed at loading, and changing it again heard
        // that someone else had written there. Without the header nothing
        // was stored.
        const liegen = antwort.headers.get("X-Nicht-Gespeichert");
        if (liegen !== null) gespeichert(eintraege, liegen.split(" "));
        throw new Error(await antwort.text());
      }
      if (antwort.status === 409) throw new Error(await antwort.text());
      if (antwort.status === 401) throw new Error(TEXT.abgemeldet);
      if (!antwort.ok) throw new Error("");
      gespeichert(eintraege);
      melden(offen.size ? "" : TEXT.fertig, "text-success");
    } catch (fehler) {
      melden(fehler.message || TEXT.fehler, "text-danger");
    }
  }

  function feld(name) {
    return formular.querySelector(`[name="${CSS.escape(name)}"]`);
  }

  // Exactly 'notiz_<pk>', 'differenz_<pk>' and 'sollstattist_<pk>': the
  // fields with what the row showed start the same way ('notiz_vorher_<pk>').
  const NOTIZ = /^notiz_(\d+)$/;
  const DIFFERENZ = /^differenz_(\d+)$/;
  const HAKEN = /^sollstattist_(\d+)$/;
  //: What is typed into: saved on leaving the field.
  const GETIPPT = [NOTIZ, DIFFERENZ];

  function istGetippt(element) {
    return (
      !!element && GETIPPT.some((muster) => muster.test(element.name || ""))
    );
  }

  function istHaken(element) {
    return !!element && HAKEN.test(element.name || "");
  }

  function nummer(name) {
    return [...GETIPPT, HAKEN]
      .map((muster) => muster.exec(name))
      .find(Boolean)[1];
  }

  // All fields of a row: an unticked box sends nothing, exactly so the
  // view reads it as deselected. Along with them what the row showed.
  function zeilenfelder(pk) {
    const eintraege = [];
    for (const name of [
      `notiz_vorher_${pk}`,
      `sollstattist_vorher_${pk}`,
      `differenz_vorher_${pk}`,
    ]) {
      const vorher = feld(name);
      if (vorher) eintraege.push([name, vorher.value]);
    }
    for (const name of [`notiz_${pk}`, `differenz_${pk}`]) {
      const getippt = feld(name);
      if (getippt) eintraege.push([name, getippt.value]);
    }
    const haken = feld(`sollstattist_${pk}`);
    if (haken && haken.checked) eintraege.push([haken.name, "on"]);
    return eintraege;
  }

  // What the server stored of what was sent: the rows show it from now on,
  // and it is no longer open. 'ausser' names the fields it left as they were
  // ('X-Nicht-Gespeichert'). A tick goes with the note of its row: the server
  // stores both or neither.
  function gespeichert(eintraege, ausser = []) {
    const liegen = new Set(ausser);
    const gesichert = eintraege.filter(([name]) => !liegen.has(name));
    vorherNachziehen(gesichert);
    // Open stays what was typed further while the save was on its way.
    // Until 10/2026 the answer closed it all the same, and leaving the
    // field, a filter or leaving the page sent nothing of it.
    for (const [name, wert] of gesichert) {
      if (offen.get(name) === wert) offen.delete(name);
    }
  }

  // What was sent is what the row shows from now on. A box not sent was unticked.
  function vorherNachziehen(eintraege) {
    const gesendet = new Map(eintraege);
    for (const [name, wert] of eintraege) {
      if (!GETIPPT.some((muster) => muster.test(name))) continue;
      const vorher = feld(name.replace("_", "_vorher_"));
      if (vorher) vorher.value = wert;
      if (!NOTIZ.test(name)) continue;
      const pk = nummer(name);
      const hakenVorher = feld(`sollstattist_vorher_${pk}`);
      if (hakenVorher)
        hakenVorher.value = gesendet.has(`sollstattist_${pk}`) ? "1" : "";
    }
  }

  formular.addEventListener("input", (ereignis) => {
    if (!istGetippt(ereignis.target)) return;
    offen.set(ereignis.target.name, ereignis.target.value);
    melden(TEXT.offen, "text-body-secondary");
  });

  // 'focusout' instead of 'blur': that bubbles, so a single listener on
  // the form suffices for all fields, also for those only added after
  // a refresh.
  formular.addEventListener("focusout", (ereignis) => {
    if (!istGetippt(ereignis.target)) return;
    if (offen.has(ereignis.target.name))
      sichern([nummer(ereignis.target.name)]);
  });

  // The tick only takes effect at closing, saved it is at once
  // nonetheless, otherwise it would be gone again after a filter change.
  formular.addEventListener("change", (ereignis) => {
    if (!istHaken(ereignis.target)) return;
    sichern([nummer(ereignis.target.name)]);
  });

  // "Save notes" stays on the page, so it sends in the background like the
  // fields themselves, only everything at once. Closing leaves the page and
  // goes the ordinary way, which saves everything along anyway.
  formular.addEventListener("submit", (ereignis) => {
    // The closing goes the ordinary way, with the token of the form.
    feldMarke.value = marke();
    if (ereignis.submitter?.name === "abschliessen") {
      // A save on its way first: its answer brings the remarks' state the
      // closing sends along. Leaving a note field for the button starts one.
      // Sent before its answer, the page's own note looked like someone
      // else's, and closing stopped.
      if (lauf) {
        ereignis.preventDefault();
        if (!abschluss) {
          abschluss = ereignis.submitter;
          lauf.finally(() => {
            const knopf = abschluss;
            abschluss = null;
            formular.requestSubmit(knopf);
          });
        }
        return;
      }
      offen.clear();
      return;
    }
    ereignis.preventDefault();
    sichern(
      [...formular.elements]
        .filter(istGetippt)
        .map((element) => nummer(element.name)),
    );
  });

  // Whoever swaps the list has to know beforehand that nothing is open
  // any more, otherwise the freshly rendered row would come back with
  // the old text and overwrite what was just typed. The filter script
  // calls this before it fetches the list.
  // Here too row by row: a note field alone would take the tick away.
  function offeneNummern() {
    return [...new Set([...offen.keys()].map(nummer))];
  }

  function offeneZeilen() {
    return offeneNummern().flatMap(zeilenfelder);
  }

  window.notizenSichern = () => sichern(offeneNummern());

  // A script that hears the session has expired asks here before it leaves
  // the page ('live_filter.js', 'refresh.js'). With open notes the page
  // stays, and their state says so: signed in anew in another tab, "Save
  // notes" sends them. Until 10/2026 the script led to the sign-in, and the
  // notes were gone. Without open notes there is nothing to lose.
  window.notizenAbgemeldet = () => {
    if (!offen.size) return false;
    melden(TEXT.abgemeldet, "text-danger");
    return true;
  };

  // Last chance. 'pagehide' fires reliably there too where
  // 'beforeunload' is skipped (mobile devices, back button).
  window.addEventListener("pagehide", () => {
    if (!offen.size || !navigator.sendBeacon) return;
    navigator.sendBeacon(
      formular.action || window.location.href,
      daten(offeneZeilen()),
    );
    offen.clear();
  });
})();
