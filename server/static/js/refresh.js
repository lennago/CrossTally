/*
 * "Refresh values": new numbers only when someone fetches them.
 *
 * The numbers of the check list never change by themselves, not even when
 * filtering or searching. Whoever reads should not be surprised in the middle
 * of reading and miss nothing.
 *
 * - **Checking:** every 30 seconds and when returning to the tab the script
 *   queries the id of the stored count state ('data-zaehlstand-adresse') and
 *   compares it with the one the numbers here belong to. Nothing new: the
 *   button is disabled, "No new values". Something new: the button turns
 *   yellow and pressable, and closing is locked, because it would close with
 *   numbers nobody here has seen. The server checks the same once more
 *   ('_abschliessen'). Has the session expired, the button says that, and
 *   closing is locked too: it would land on the sign-in.
 * - **Fetching:** the button fetches the same page via 'fetch' and takes
 *   over from it only the numbers ('data-wert') and the classes of the rows
 *   ('data-zeile'). The text fields stay untouched. What changed is marked
 *   until the next fetch, in rows the filter is currently hiding too. After a
 *   reload nothing is marked.
 * - **Filtering:** search and filters fetch the list anew as well
 *   ('live_filter.js', event 'liste:ersetzt'), and new numbers would come
 *   along silently. That is why the script afterwards reinserts the numbers
 *   from the snapshot ('template[data-schnappschuss]'): all rows at the state
 *   of this page. The sum per product family hangs on the filter and cannot
 *   be held that way. If a new state came along, "…" stands there until
 *   fetched.
 *
 * Without JavaScript the button is an ordinary link to the same address.
 */
(function () {
  // The texts are passed in by the page on the script tag. There the
  // translation applies ('components/refresh_script.html').
  const TEXT = document.currentScript.dataset;
  const knopf = document.querySelector("[data-auffrischen]");
  if (!knopf || !window.fetch || !window.DOMParser) return;

  const zerleger = new DOMParser();
  const hinweis = document.querySelector("[data-neue-zeilen]");
  const abschluss = document.querySelector("[data-abschliessen]");
  const veraltetHinweis = document.querySelector("[data-veraltet-hinweis]");
  const feld = document.querySelector("[data-zaehlstand-feld]");
  const geaendert = document.querySelector("[data-geaendert]");
  const vorlage = document.querySelector("template[data-schnappschuss]");
  const TAKT = 30000;
  //: Changes on every fetch and is therefore no news.
  const IMMER_NEU = new Set(["stand"]);
  //: Hangs on the filter: depending on the search a different sum under the same name.
  const abgeleitet = (schluessel) => schluessel.startsWith("familiensumme-");

  let laeuft = false;
  let veraltet = false;
  //: The session has expired: nobody here learns any more whether
  //: something is new, and closing would land on the sign-in.
  let abgemeldet = false;
  //: The numbers at the state of this page, per 'data-wert', from the snapshot
  //: and the page's header. Against them is measured what is new.
  const bekannt = new Map();
  //: The class of every row at the same state (say "auffällig").
  const klassen = new Map();
  //: What is currently marked. If the filter replaces the list, it is marked again.
  const markiert = new Set();

  /** All numbers and row classes of a page, including its snapshot. */
  function stand(seite) {
    const werte = new Map();
    const zeilen = new Map();
    const schnappschuss = seite.querySelector(
      "template[data-schnappschuss]",
    )?.content;
    for (const quelle of [seite, schnappschuss]) {
      if (!quelle) continue;
      for (const stelle of quelle.querySelectorAll("[data-wert]")) {
        if (!abgeleitet(stelle.dataset.wert))
          werte.set(stelle.dataset.wert, stelle.innerHTML);
      }
      for (const zeile of quelle.querySelectorAll("[data-zeile]"))
        zeilen.set(zeile.dataset.zeile, zeile.className);
    }
    return { werte, zeilen };
  }

  /** Bring what stands here to the known state and set the marks. */
  function einsetzen() {
    for (const stelle of document.querySelectorAll("[data-wert]")) {
      const wert = bekannt.get(stelle.dataset.wert);
      if (wert !== undefined && stelle.innerHTML !== wert)
        stelle.innerHTML = wert;
      stelle.classList.toggle("wert-neu", markiert.has(stelle.dataset.wert));
    }
    for (const zeile of document.querySelectorAll("[data-zeile]")) {
      const klasse = klassen.get(zeile.dataset.zeile);
      if (klasse !== undefined) zeile.className = klasse;
    }
  }

  function zeigen() {
    const drueckbar = (veraltet || abgemeldet) && !laeuft;
    knopf.classList.toggle("disabled", !drueckbar);
    knopf.classList.toggle("btn-warning", drueckbar);
    knopf.classList.toggle("btn-outline-secondary", !drueckbar);
    knopf.setAttribute("aria-disabled", String(!drueckbar));
    knopf.tabIndex = drueckbar ? 0 : -1;
    knopf.textContent = laeuft
      ? TEXT.laeuft
      : abgemeldet
        ? TEXT.abgemeldet
        : veraltet
          ? TEXT.veraltet
          : TEXT.aktuell;
    if (abschluss) abschluss.disabled = veraltet || laeuft || abgemeldet;
    if (veraltetHinweis) veraltetHinweis.hidden = !veraltet;
  }

  async function nachsehen() {
    if (laeuft || !knopf.dataset.zaehlstandAdresse) return;
    try {
      const antwort = await window.imHintergrund(
        knopf.dataset.zaehlstandAdresse,
      );
      // A 401 is the expired session, and the button says so. Until
      // 10/2026 it went on saying there were no new values. Signed in anew
      // in another tab, the next look finds the session again.
      abgemeldet = antwort.status === 401;
      if (!antwort.ok) {
        zeigen();
        return;
      }
      const neu = (await antwort.json()).stand;
      // During the check the button may already have fetched new numbers.
      if (laeuft) return;
      veraltet = neu !== knopf.dataset.zaehlstand;
      zeigen();
    } catch (fehler) {
      // No network: then nobody knows whether there is anything new. It stays as it is.
    }
  }

  /** Take over the state of the freshly fetched page and mark what changed. */
  function uebernehmen(frisch) {
    const neu = stand(frisch);
    markiert.clear();
    for (const [schluessel, wert] of neu.werte) {
      const vorher = bekannt.get(schluessel);
      if (vorher !== undefined && vorher !== wert && !IMMER_NEU.has(schluessel))
        markiert.add(schluessel);
      bekannt.set(schluessel, wert);
    }
    for (const [zeile, klasse] of neu.zeilen) klassen.set(zeile, klasse);

    // The sums per product family come as the server computes them for this
    // list: fetched was the same filtered list.
    for (const stelle of frisch.querySelectorAll("[data-wert]")) {
      if (!abgeleitet(stelle.dataset.wert)) continue;
      const hier = document.querySelector(
        `[data-wert="${stelle.dataset.wert}"]`,
      );
      if (hier) {
        hier.innerHTML = stelle.innerHTML;
        hier.removeAttribute("title");
      }
    }
    einsetzen();

    const frischeVorlage = frisch.querySelector("template[data-schnappschuss]");
    if (vorlage && frischeVorlage) vorlage.innerHTML = frischeVorlage.innerHTML;

    // New articles cannot be inserted without rebuilding the sort and the
    // groups. Then better say honestly that a reload is due.
    const hier = new Set(
      [...document.querySelectorAll("[data-zeile]")].map(
        (zeile) => zeile.dataset.zeile,
      ),
    );
    const unbekannt = [...frisch.querySelectorAll("[data-zeile]")].filter(
      (zeile) => !hier.has(zeile.dataset.zeile),
    ).length;
    if (hinweis) hinweis.hidden = unbekannt === 0;
    if (geaendert) {
      // Per article there are several numbers ('ist-12', 'differenz-12' …).
      // Counted is the article, not each of its cells.
      const artikel = new Set(
        [...markiert]
          .map((schluessel) => schluessel.match(/-(\d+)$/)?.[1])
          .filter(Boolean),
      );
      geaendert.textContent = artikel.size
        ? TEXT.geaendert.replace("{anzahl}", artikel.size)
        : TEXT.unveraendert;
    }

    // The numbers here now belong to this state, until the next input.
    const kennung =
      frisch.querySelector("[data-auffrischen]")?.dataset.zaehlstand;
    if (kennung) {
      knopf.dataset.zaehlstand = kennung;
      if (feld) feld.value = kennung;
    }
  }

  knopf.addEventListener("click", async (ereignis) => {
    ereignis.preventDefault();
    if (!(veraltet || abgemeldet) || laeuft) return;
    laeuft = true;
    zeigen();
    try {
      // The address of now, not the one at load time: the search carries it
      // along, and fetched should be the same filtered list that stands here.
      const antwort = await window.imHintergrund(window.location.href);
      // The session has expired ('StaffOnlyMiddleware'). With open notes
      // the page stays, and their state says so ('notes.js'). Otherwise the
      // reload below leads to the sign-in, and that page says why
      // ('login_notice.js'). Until 10/2026 the sign-in page came as the
      // answer, and the button said there were no new values.
      abgemeldet = antwort.status === 401;
      if (abgemeldet) {
        if (window.notizenAbgemeldet?.()) return;
        sessionStorage.setItem("abgemeldet", "1");
      }
      if (!antwort.ok) throw new Error(antwort.status);
      uebernehmen(zerleger.parseFromString(await antwort.text(), "text/html"));
      veraltet = false;
    } catch (fehler) {
      window.location.reload();
    } finally {
      laeuft = false;
      zeigen();
    }
  });

  // Search or filter replaced the list, possibly with newer numbers. Those
  // of this page apply. Only the sums per family cannot be brought back:
  // if a new state came along, they stay open.
  document.addEventListener("liste:ersetzt", (ereignis) => {
    const kennung =
      ereignis.detail?.querySelector("[data-auffrischen]")?.dataset.zaehlstand;
    einsetzen();
    if (kennung === undefined || kennung === knopf.dataset.zaehlstand) return;
    for (const stelle of document.querySelectorAll("[data-wert]")) {
      if (!abgeleitet(stelle.dataset.wert)) continue;
      stelle.textContent = "…";
      stelle.title = TEXT.veraltetHinweis;
    }
    veraltet = true;
    zeigen();
  });

  const anfang = stand(document);
  for (const [schluessel, wert] of anfang.werte) bekannt.set(schluessel, wert);
  for (const [zeile, klasse] of anfang.zeilen) klassen.set(zeile, klasse);
  zeigen();
  void nachsehen();
  window.setInterval(nachsehen, TAKT);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") void nachsehen();
  });
})();
