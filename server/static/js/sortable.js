// Every list with 'data-sortierbar' can be dragged by the handle. If Sortable
// does not load, the list stays. The arrow buttons do the same work.
//
// With 'data-sofort-sichern' the order goes to the server in the background
// after every release: the same form, via 'fetch', answered with 204. A
// crashing tab or an accidentally closed window thus costs nothing more of
// what was already dragged. Per form only one request is on its way at a
// time ('abschicken'). Whatever else sends the form or fetches the list
// anew waits for it. Once that is on its way, the list stands still until
// it comes back new ('steht'). If the session has expired, the server
// answers 401 and not with the sign-in page ('StaffOnlyMiddleware'), and the
// hint says so: pressing the button again would not help then.
//
// A button with 'data-reihenfolge-speichern' also sends in the background.
// It is green and says "Save order" only as long as the order deviates from
// the stored one. Otherwise it is disabled and says "Order saved": at load
// time, after saving, after an arrow button (that one saves by itself) and
// after a pack was dragged back to its old place. Until 10/2026 it stood
// green and disabled after an arrow click, and that looked like an unsaved
// change nobody could save. Without the script it is always pressable and
// reloads the page like every form.

(function () {
  // The texts are passed in by the page on the script tag. There the
  // translation applies ('components/sortable_script.html').
  const TEXT = document.currentScript.dataset;

  // The status of the answer, 0 without one (no network). With the CSRF
  // token as the browser holds it now ('shared.js'): the form's own is the
  // page's, and after signing in anew in another tab Django refused it
  // (until 10/2026).
  function senden(formular) {
    const daten = new FormData(formular);
    const marke = "csrfmiddlewaretoken";
    daten.set(marke, window.csrfMarke(daten.get(marke)));
    const anfrage = window.imHintergrund(formular.action, {
      method: "POST",
      body: daten,
    });
    return anfrage.then(
      (antwort) => antwort.status,
      () => 0,
    );
  }

  function fehlschlag(status, text) {
    return status === 401 ? TEXT.abgemeldet : text;
  }

  // Only on failure a word: successful saving is the normal case and
  // should not flash something on every drag.
  function melden(formular, text) {
    let hinweis = formular.querySelector("[data-sicherungshinweis]");
    if (!text) {
      hinweis?.remove();
      return;
    }
    if (!hinweis) {
      hinweis = document.createElement("div");
      hinweis.className = "alert alert-warning py-2 small";
      hinweis.dataset.sicherungshinweis = "";
      formular.prepend(hinweis);
    }
    hinweis.textContent = text;
  }

  // One request per form is on its way at a time, whether a release sent it
  // or the button. Requests can overtake each other on the way, and the
  // server keeps the order that arrives last. Until 10/2026 every release
  // sent a request of its own, and a slow save could overwrite the drag
  // after it without a word. Now a release during a save only notes that
  // the order changed ('nochmal'). Once the save is done, whether it worked
  // or not, the newest order follows, and only once. A second tab still
  // saves on its own: there the last save wins, as with any form.
  // A plain 'Map', because "Refresh" asks whether anything is on its way
  // at all. An entry goes once its request is answered.
  const unterwegs = new Map();

  // "Refresh" waiting until nothing is on its way any more
  // ('reihenfolgeAbwarten').
  const wartende = [];

  function bescheid(ergebnis) {
    for (const fertig of wartende.splice(0)) fertig(ergebnis);
  }

  // 'danach' hears whether the request arrived, and its status, before
  // what waited for it goes on.
  function abschicken(formular, danach) {
    const lauf = { nochmal: false, wartend: null };
    unterwegs.set(formular, lauf);
    senden(formular).then((status) => {
      unterwegs.delete(formular);
      danach(status >= 200 && status < 300, status);
      // The list may have been replaced meanwhile, by a row button or
      // "Refresh" that was on its way before. The new one shows what the
      // server holds, and the old form would overwrite that with an order
      // nobody sees any more. What waited on the old form is gone with it.
      if (formular.isConnected) {
        if (lauf.wartend) loslassen(formular, lauf.wartend);
        else if (lauf.nochmal) sichern(formular);
      }
      if (!unterwegs.size) bescheid(true);
    });
  }

  function sichern(formular) {
    const laufend = unterwegs.get(formular);
    if (laufend) {
      laufend.nochmal = true;
      return;
    }
    abschicken(formular, (angekommen, status) =>
      melden(
        formular,
        angekommen ? "" : fehlschlag(status, TEXT.nichtGesichert),
      ),
    );
  }

  // Whatever brings the list back new marks it as busy: a submit of the
  // form (from the click on, also while it waits) and "Refresh"
  // ('soft_reload.js'). A row dragged meanwhile would save an order that
  // the new list replaces at once, and its save could still arrive after
  // the request. Until 10/2026 the page could then show another order than
  // the server held. Rather than reconcile that afterwards, no drag starts
  // until the new list is there, and that comes without the mark. Whoever
  // stops the browser while it leaves the page keeps the mark until reloading.
  function steht(element) {
    return Boolean(element.closest('[aria-busy="true"]'));
  }

  // Whatever else sends the form waits for the request on its way: a row
  // button, "Save", the switch to the other tab. Until 10/2026 it went out
  // at once. If it arrived first, the older order of the save undid its
  // step. Once the save is answered, whether it worked or not, the submit
  // goes out after all. It carries the newest order, drags while it waited
  // included, so no save follows behind it. Meanwhile the buttons of the
  // form are disabled, as during the request of a row button itself
  // ('instant_actions.js'). A second click thus does not get through, and
  // the first one counts. Two would not fit together anyway: the answer to
  // the first replaces the list the second belongs to. In the capture
  // phase, so that 'instant_actions.js' never sees the submit that waits.
  document.addEventListener(
    "submit",
    (ereignis) => {
      const formular = ereignis.target;
      if (formular.querySelector("[data-sortierbar]"))
        formular.setAttribute("aria-busy", "true");
      const lauf = unterwegs.get(formular);
      if (!lauf) return;
      ereignis.preventDefault();
      ereignis.stopImmediatePropagation();
      if (lauf.wartend) return;
      lauf.wartend = { knopf: ereignis.submitter, gesperrt: sperren(formular) };
      // Its answer brings the list fresh, and "Refresh" beside it would
      // only race it.
      bescheid(false);
    },
    true,
  );

  // Only the buttons that were pressable: the first row's arrow upwards
  // stays disabled afterwards too.
  function sperren(formular) {
    const knoepfe = [...formular.elements].filter(
      (element) => element.type === "submit" && !element.disabled,
    );
    for (const knopf of knoepfe) knopf.disabled = true;
    return knoepfe;
  }

  // The buttons come back first: a disabled button would not send its name
  // and value along. Whoever works by keyboard finds the focus on the pressed
  // button afterwards, set by 'instant_actions.js' once the list is new.
  function loslassen(formular, { knopf, gesperrt }) {
    for (const element of gesperrt) element.disabled = false;
    // The submit marks the form again once it goes out. If it does not (the
    // pressed button is gone), the list may be dragged again.
    formular.removeAttribute("aria-busy");
    if (!knopf || knopf.form === formular) formular.requestSubmit(knopf);
  }

  // Back from the back-forward cache, a form that left the page the
  // ordinary way would stand still for good.
  window.addEventListener("pageshow", (ereignis) => {
    if (!ereignis.persisted) return;
    for (const formular of document.querySelectorAll("form[aria-busy]"))
      formular.removeAttribute("aria-busy");
  });

  // "Refresh" asks before it fetches the page ('soft_reload.js'). The fresh
  // list shows the order the server holds, and a save still on its way
  // would not be in it yet. A save noted behind it is waited for as well.
  // Until 10/2026 the list came back without the last drag. True once
  // nothing is on its way any more. False if a submit waits: its answer
  // brings the list fresh anyway.
  window.reihenfolgeAbwarten = () => {
    if ([...unterwegs.values()].some((lauf) => lauf.wartend))
      return Promise.resolve(false);
    if (!unterwegs.size) return Promise.resolve(true);
    return new Promise((fertig) => wartende.push(fertig));
  };

  const SPEICHERN = TEXT.speichern;
  const GESPEICHERT = TEXT.gespeichert;

  // The sequence as it stands in the form: one hidden field per row.
  function folge(formular) {
    return [...formular.querySelectorAll("input[name=reihenfolge]")]
      .map((feld) => feld.value)
      .join(",");
  }

  // What the server last received, per button. At load time that is the
  // page's sequence, because it comes from the database.
  const gesichert = new Map();

  // Two states: changed (green, pressable, "Save order") and in step with
  // the server (outlined, disabled, "Order saved"). While a request of the
  // form is on its way the button stays disabled, even if a drag changes
  // the order meanwhile. Until 10/2026 that made it pressable again, and a
  // second click ran beside the first. So it does while the list stands
  // still ('steht').
  function knopfZeigt(knopf) {
    const geaendert = folge(knopf.form) !== gesichert.get(knopf);
    knopf.textContent = geaendert ? SPEICHERN : GESPEICHERT;
    knopf.disabled = !geaendert || unterwegs.has(knopf.form) || steht(knopf);
    knopf.classList.toggle("btn-success", geaendert);
    knopf.classList.toggle("btn-outline-success", !geaendert);
  }

  // Runs at load time and again after 'instant_actions.js' has replaced the
  // list: the rows are new elements then, and what hung on the old ones is
  // gone with them.
  function einrichten() {
    for (const knopf of document.querySelectorAll(
      "[data-reihenfolge-speichern]",
    )) {
      if (!knopf.form || gesichert.has(knopf)) continue;
      knopfEinrichten(knopf);
    }
    if (!window.Sortable) return;
    for (const liste of document.querySelectorAll("[data-sortierbar]")) {
      if (!Sortable.get(liste)) listeEinrichten(liste);
    }
  }

  function knopfEinrichten(knopf) {
    gesichert.set(knopf, folge(knopf.form));
    knopfZeigt(knopf);
    knopf.addEventListener("click", (ereignis) => {
      const formular = knopf.form;
      ereignis.preventDefault();
      const abgeschickt = folge(formular);
      abschicken(formular, (angekommen, status) => {
        // After a failure nobody knows what the server holds: the request
        // may have arrived and only its answer got lost. Until a save
        // arrives, the button stays pressable, also for an order dragged
        // back to the old one.
        gesichert.set(knopf, angekommen ? abgeschickt : null);
        knopfZeigt(knopf);
        melden(
          formular,
          angekommen ? "" : fehlschlag(status, TEXT.nichtGespeichert),
        );
      });
      knopfZeigt(knopf);
    });
  }

  function listeEinrichten(liste) {
    Sortable.create(liste, {
      animation: 150,
      handle: "[data-griff]",
      ghostClass: "table-active",
      // Dragged is with Sortable's own imitation, not with the browser's
      // drag & drop. Native dragging paints an image of the whole table
      // row including input fields, and with long lists exactly that
      // crashed the tab.
      forceFallback: true,
      // A counting list quickly has a hundred rows: whoever drags a row
      // to the top or bottom edge and holds it there should have the
      // page scroll along instead of having to release and grab again.
      // Only the page itself scrolls. Passing it on to every scrollable
      // ancestor ('bubbleScroll') also made the horizontally scrollable
      // table frame scroll along and judder.
      scroll: true,
      scrollSensitivity: 90,
      scrollSpeed: 18,
      bubbleScroll: false,
      // No drag while the list stands still ('steht'). Only the handle is
      // refused: Sortable cancels the press it refuses, and a click into a
      // quantity field should still place the cursor there.
      filter: (ereignis) =>
        steht(liste) && Boolean(ereignis.target.closest("[data-griff]")),
      onEnd(ereignis) {
        // A drag that began before the list stood still saves nothing. The
        // new list replaces it anyway.
        if (ereignis.oldIndex === ereignis.newIndex || steht(liste)) return;
        const formular = liste.closest("form");
        const knopf = formular?.querySelector("[data-reihenfolge-speichern]");
        if (knopf) knopfZeigt(knopf);
        if (formular && "sofortSichern" in liste.dataset) sichern(formular);
      },
    });
  }

  einrichten();
  document.addEventListener("liste:ersetzt", einrichten);
})();
