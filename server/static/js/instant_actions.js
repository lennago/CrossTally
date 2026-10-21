/*
 * Forms that stay on their page act without reloading it.
 *
 * Without that one jumps back to the top of the page with every click. With
 * two hundred rows that is the difference between usable and not.
 *
 * The trick: submitted is the same form as otherwise, only via 'fetch'. The
 * view then redirects back to the page, 'fetch' follows the redirect by
 * itself, and back comes the freshly rendered page. From it the area around
 * the form and the messages are taken over. That way there is no second truth
 * about what a row looks like: still only the template renders, and without
 * JavaScript the buttons work unchanged.
 *
 * Which form goes this way it says itself with 'data-sofort'. What is taken
 * over is the list the form stands in ('data-liste'). A form outside every
 * list means the page as a whole: a status button in the header changes the
 * header too. Then the content ('data-seite') is taken over.
 *
 * A form may also enclose the whole list (counting list, packs of a
 * product). Then the row buttons go this way, and the button that leaves the
 * page afterwards says so with 'data-seitenwechsel'. Whoever has set something
 * up on the rows hears 'liste:ersetzt' and sets it up again.
 *
 * If the response is another page after all (the last open row is
 * answered), the browser goes there. The messages were already used up by
 * the response and travel along in the 'sessionStorage'. A session that has
 * expired answers 401 ('StaffOnlyMiddleware'): then the page reloads into
 * the sign-in, which says why ('login_notice.js').
 *
 * "Refresh" ('soft_reload.js') brings the lists anew as well. Both on their
 * way at once, the later answer replaced the list, even the older one
 * (until 10/2026). So "Refresh" asks how many requests went out here
 * ('sofortGesendet') and waits for those on their way ('sofortAbwarten').
 */
(function () {
  if (!window.fetch || !window.DOMParser) return;

  const MITGENOMMEN = "meldungen:mitgenommen";
  const zerleger = new DOMParser();

  function meldungenSetzen(html) {
    const hier = document.querySelector("[data-meldungen]");
    if (hier) hier.innerHTML = html;
  }

  function meldungenZeigen(dokument) {
    const neu = dokument.querySelector("[data-meldungen]");
    if (neu) meldungenSetzen(neu.innerHTML);
  }

  // What a response left behind before the browser came here.
  try {
    const mitgenommen = window.sessionStorage.getItem(MITGENOMMEN);
    if (mitgenommen) {
      window.sessionStorage.removeItem(MITGENOMMEN);
      meldungenSetzen(mitgenommen);
    }
  } catch (fehler) {
    // Blocked storage: then without the message.
  }

  function woandershin(antwort, dokument) {
    const neu = dokument.querySelector("[data-meldungen]");
    try {
      if (neu && neu.children.length)
        window.sessionStorage.setItem(MITGENOMMEN, neu.innerHTML);
    } catch (fehler) {
      /* see above */
    }
    // No flag for the sign-in page here: this is a page of its own. Until
    // 10/2026 it was set for every one, and the next sign-in in this tab,
    // say after signing out, said the session had expired.
    window.location.assign(antwort.url);
  }

  // During the request the buttons are disabled: a second click on
  // "Inactive" would otherwise run against a state the server has
  // already changed.
  function knoepfeSperren(formular, gesperrt) {
    for (const knopf of formular.querySelectorAll("button"))
      knopf.disabled = gesperrt;
  }

  // Requests sent so far, and those not answered yet. What waits for them
  // hears 'false' once none is on its way: their answers brought the list
  // anew ('soft_reload.js').
  let gesendet = 0;
  let unterwegs = 0;
  const wartende = [];
  window.sofortGesendet = () => gesendet;
  window.sofortAbwarten = () =>
    unterwegs
      ? new Promise((fertig) => wartende.push(fertig))
      : Promise.resolve(true);

  // The grey veil shows that something is running, but only after a
  // short while. Otherwise it would flicker on every click, because the
  // response usually arrives after a few milliseconds. Every request has a
  // timer of its own. Until 10/2026 one served them all, and a second
  // button pressed meanwhile took it over: the first one's area turned grey
  // after its answer and stayed so, and the second one got no veil.
  //
  // The veil goes once no request of its area is on its way any more. Until
  // 10/2026 the first answer took it away while a second request of the
  // same area was still waiting.
  const unterwegsJeBereich = new WeakMap();

  function schleierAn(bereich) {
    unterwegsJeBereich.set(bereich, (unterwegsJeBereich.get(bereich) ?? 0) + 1);
    return setTimeout(() => bereich.classList.add("opacity-50"), 150);
  }

  function schleierAus(bereich, uhr) {
    clearTimeout(uhr);
    const offen = unterwegsJeBereich.get(bereich) - 1;
    if (offen) {
      unterwegsJeBereich.set(bereich, offen);
      return;
    }
    unterwegsJeBereich.delete(bereich);
    bereich.classList.remove("opacity-50");
  }

  // The same area in the fresh page: the list by its position among the
  // lists, the page by its mark. If the lists no longer match, the page.
  function gegenstueck(bereich, frisch) {
    if (bereich.hasAttribute("data-liste")) {
      const hier = [...document.querySelectorAll("[data-liste]")];
      const dort = [...frisch.querySelectorAll("[data-liste]")];
      if (hier.length === dort.length)
        return [bereich, dort[hier.indexOf(bereich)]];
    }
    return [
      document.querySelector("[data-seite]"),
      frisch.querySelector("[data-seite]"),
    ];
  }

  // Whoever works by keyboard presses the same button again. It is a new
  // element now and would otherwise have lost the focus. Found by its own
  // mark ('data-fokus', unique per row), else by name and value. If it is
  // gone or disabled (the pressed status, a removed row), the next usable
  // element of its row takes the focus, else the list itself: better its
  // start than the top of the page (until 10/2026 Tab began there again).
  function fokusZurueck(bereich, knopf) {
    if (!knopf) return;
    let ziel = null;
    if (knopf.dataset.fokus)
      ziel = bereich.querySelector(
        `[data-fokus="${CSS.escape(knopf.dataset.fokus)}"]`,
      );
    if (!ziel && knopf.name)
      ziel = bereich.querySelector(
        `button[name="${CSS.escape(knopf.name)}"][value="${CSS.escape(knopf.value)}"]`,
      );
    if (ziel?.disabled) {
      const zeile = ziel.closest("tr, li, form");
      ziel =
        zeile?.querySelector(
          "button:not([disabled]), a[href], input:not([type=hidden]):not([disabled])",
        ) ?? null;
    }
    if (!ziel) {
      bereich.tabIndex = -1;
      ziel = bereich;
    }
    ziel.focus({ preventScroll: true });
  }

  document.addEventListener("submit", async (ereignis) => {
    const formular = ereignis.target.closest("form[data-sofort]");
    if (!formular || ereignis.defaultPrevented) return;
    const knopf = ereignis.submitter;
    if (knopf?.hasAttribute("data-seitenwechsel")) return;
    // The ordinary way after a failure, see below.
    if ("ohneSkript" in formular.dataset) return;
    const bereich =
      formular.closest("[data-liste]") ||
      document.querySelector("[data-seite]");
    if (!bereich) return;
    ereignis.preventDefault();

    // The CSRF token as the browser holds it now ('shared.js'), also for the
    // ordinary way after a failure. The form's own is the page's, and after
    // signing in anew in another tab Django refused it (until 10/2026).
    const feldMarke = formular.querySelector("[name=csrfmiddlewaretoken]");
    if (feldMarke) feldMarke.value = window.csrfMarke(feldMarke.value);
    // Which button was pressed is not in the form. With two buttons of
    // the same name only the event knows.
    const daten = new FormData(formular, knopf);
    // A button may name an address of its own ('formaction').
    const adresse = knopf?.getAttribute("formaction") || formular.action;
    knoepfeSperren(formular, true);
    const schleier = schleierAn(bereich);
    gesendet += 1;
    unterwegs += 1;
    try {
      const antwort = await window.imHintergrund(adresse, {
        method: "POST",
        body: daten,
      });
      // The session has expired ('StaffOnlyMiddleware'). Reloaded, this
      // page leads to the sign-in, which says why ('login_notice.js') and
      // comes back here afterwards. The ordinary way below would come back
      // to the form's address instead.
      if (antwort.status === 401) {
        sessionStorage.setItem("abgemeldet", "1");
        window.location.reload();
        return;
      }
      if (!antwort.ok) throw new Error(antwort.status);

      const frisch = zerleger.parseFromString(
        await antwort.text(),
        "text/html",
      );
      if (new URL(antwort.url).pathname !== window.location.pathname) {
        woandershin(antwort, frisch);
        return;
      }
      const [alt, neu] = gegenstueck(bereich, frisch);
      if (!alt || !neu) throw new Error("Bereich fehlt");
      alt.innerHTML = neu.innerHTML;
      meldungenZeigen(frisch);

      // Individual places outside the area that have to follow along, say
      // the hit count above the filter form. The form itself stays
      // untouched, otherwise the input would be gone on the next click.
      for (const stelle of document.querySelectorAll("[data-ersetzen]")) {
        const dort = frisch.querySelector(
          `[data-ersetzen="${stelle.dataset.ersetzen}"]`,
        );
        if (dort) stelle.innerHTML = dort.innerHTML;
      }
      document.dispatchEvent(new CustomEvent("liste:ersetzt"));

      fokusZurueck(alt, knopf);
    } catch (fehler) {
      // If in doubt the ordinary way: better a jump to the top than a
      // button that does nothing. With the pressed button, because on it
      // hangs what the form means.
      knoepfeSperren(formular, false);
      formular.dataset.ohneSkript = "";
      if (knopf?.isConnected) formular.requestSubmit(knopf);
      else formular.submit();
    } finally {
      // Has to run after success too: the area was refilled, but the
      // class sits on the area itself and would otherwise remain as a
      // grey veil over the page.
      schleierAus(bereich, schleier);
      unterwegs -= 1;
      if (!unterwegs) for (const fertig of wartende.splice(0)) fertig(false);
    }
  });
})();
