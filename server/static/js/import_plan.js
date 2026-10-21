/*
 * The import plan in the browser: rows migrate, product groups keep count.
 *
 * The plan stands in two levels. Outside the block that says what happens
 * with a row, inside the product group. If someone changes the procedure,
 * the row migrates into the matching block and there into its product
 * group. That is why all blocks between which migration happens keep the
 * same product groups, empty ones too (see 'BaseImportFormSet.bloecke').
 * Here things are only moved, nothing is built.
 */
(function () {
  // Per block and product group one table body: bloecke["aktiv"]["3"].
  // Not every plan has every block: a maintained house whose file brings
  // nothing new has no body for "aktiv", and the conflicts still need
  // serving. Only a page without any block is none of ours.
  const bloecke = {};
  for (const koerper of document.querySelectorAll("[data-block]")) {
    const block = (bloecke[koerper.dataset.block] ||= {});
    block[koerper.dataset.warengruppe] = koerper;
  }
  if (!Object.keys(bloecke).length) return;

  // The conversion to the product exists only where the house counts in a
  // different dimension than the chosen product. The dimension of every
  // product is in the option list ('ware-optionen'), that of the unit on the row.
  const VORLAGE = document.currentScript.dataset.wert || "";
  const waren = new Map();
  const quelle = document.getElementById("ware-optionen");
  for (const option of quelle ? JSON.parse(quelle.textContent) : [])
    waren.set(String(option.value), option);

  function umrechnungRichten(zeile) {
    const block = zeile.querySelector("[data-umrechnung]");
    if (!block) return;
    const ware = waren.get(wert(zeile, "ware"));
    const braucht = Boolean(ware && ware.dimension !== block.dataset.dimension);
    block.hidden = !braucht;
    const feld = block.querySelector("input");
    // Hidden does not mean disabled: a remainder from an earlier choice
    // should not ride along.
    if (!braucht && feld) feld.value = "";
    const text = block.querySelector("[data-umrechnung-text]");
    if (braucht && text && VORLAGE)
      text.textContent = VORLAGE.replace("{ware}", ware.einheit).replace(
        "{haus}",
        block.dataset.einheit,
      );
  }

  // With a button row there is one field per choice. Asked for is the
  // ticked one. For menus and text fields it stays with the first.
  function wert(zeile, endung) {
    const gewaehlt = zeile.querySelector(`[name$='-${endung}']:checked`);
    if (gewaehlt) return gewaehlt.value;
    const feld = zeile.querySelector(`[name$='-${endung}']`);
    return feld && feld.type !== "radio" ? feld.value : "";
  }

  // The hint row belongs to the row above and has to migrate along.
  function hinweiszeile(zeile) {
    const naechste = zeile.nextElementSibling;
    return naechste && naechste.hasAttribute("data-hinweis") ? naechste : null;
  }

  // The blocks for creating are named after the procedure that leads into them.
  function einordnen(zeile) {
    const vorgehen = wert(zeile, "vorgehen");
    // "Leave" touches nothing. The row steps back.
    zeile.classList.toggle("opacity-50", vorgehen === "uebergehen");
    // What the new article arises with is asked only for "Replace".
    const statuswahl = zeile.querySelector("[data-nur-bei-ersetzen]");
    if (statuswahl) statuswahl.hidden = vorgehen !== "ersetzen";
    // What is adopted from the file, likewise only for "Change", and what
    // is not adopted steps back.
    const teilwahl = zeile.querySelector("[data-nur-bei-aendern]");
    if (teilwahl) {
      teilwahl.hidden = vorgehen !== "aktualisieren";
      const gebinde = zeile.querySelector("[name$='-gebinde']");
      const gewaehlt = zeile.querySelector(
        "[name$='-uebernehmen'][value='gebinde']",
      );
      if (gebinde && gewaehlt)
        gebinde.classList.toggle(
          "opacity-50",
          vorgehen === "aktualisieren" && !gewaehlt.checked,
        );
    }
    // What concerns an existing row stays in its block, except the conflict:
    // it comes without preselection and counts as resolved with the choice.
    const art = wert(zeile, "art");
    let block = null;
    if (art === "neu") block = bloecke[vorgehen] || bloecke.aktiv;
    else if (art === "konflikt")
      block = vorgehen ? bloecke.behoben : bloecke.konflikt;
    const ziel = block && block[zeile.dataset.warengruppe];
    if (ziel && zeile.parentElement !== ziel) {
      const anhang = hinweiszeile(zeile);
      ziel.appendChild(zeile);
      if (anhang) ziel.appendChild(anhang);
      // Otherwise the row would vanish with the click into a collapsed block.
      let auf = ziel.closest("details");
      while (auf) {
        auf.open = true;
        auf = auf.parentElement && auf.parentElement.closest("details");
      }
    }
  }

  // A whole product group at once. What the row does not know it leaves
  // be: in a block stand only rows that can have this procedure.
  function alleSetzen(gruppe, vorgehen) {
    for (const zeile of gruppe.querySelectorAll("[data-zeile]")) {
      const knopf = zeile.querySelector(
        `[name$='-vorgehen'][value='${vorgehen}']`,
      );
      if (!knopf || knopf.checked) continue;
      knopf.checked = true;
      einordnen(zeile);
    }
  }

  function zaehlen() {
    for (const [kennung, gruppen] of Object.entries(bloecke)) {
      let gesamt = 0;
      for (const [nummer, koerper] of Object.entries(gruppen)) {
        const anzahl = koerper.querySelectorAll("[data-zeile]").length;
        gesamt += anzahl;
        setzen(`[data-anzahl-warengruppe='${kennung}:${nummer}']`, anzahl);
        const gruppe = document.querySelector(
          `[data-warengruppe-block='${kennung}:${nummer}']`,
        );
        // An empty product group stands there only as a target. Nobody has to see it.
        if (gruppe) gruppe.hidden = anzahl === 0;
      }
      setzen(`[data-anzahl-block='${kennung}']`, gesamt);
      const leer = document.querySelector(`[data-leer-block='${kennung}']`);
      if (leer) leer.hidden = gesamt > 0;
    }
    sperren();
  }

  function setzen(auswahl, anzahl) {
    const anzeige = document.querySelector(auswahl);
    if (anzeige) anzeige.textContent = anzahl;
  }

  // Adopting happens only once every conflict is decided. The server has
  // the last word. Here it only shows up earlier.
  function sperren() {
    const offen = bloecke.konflikt
      ? document.querySelectorAll("[data-block='konflikt'] [data-zeile]").length
      : 0;
    const knopf = document.querySelector("[data-uebernehmen]");
    if (knopf) knopf.disabled = offen > 0;
    const hinweis = document.querySelector("[data-offene-konflikte]");
    if (!hinweis) return;
    hinweis.hidden = offen === 0;
    hinweis.querySelector("[data-offen]").textContent = offen;
  }

  // Two rows on the same product stand out at once instead of only on
  // submit. Marked only. Hiding would be too expensive with a thousand
  // rows, and the server has the last word anyway.
  function ware(zeile) {
    const gewaehlt = wert(zeile, "ware");
    if (gewaehlt) return `pk:${gewaehlt}`;
    const text = (wert(zeile, "neuer_ware") || wert(zeile, "name")).trim();
    return text ? `name:${text.toLowerCase()}` : "";
  }

  // Carried is what arises or stays active or inactive, because only that
  // occupies the product in the house. For "Replace" the new status decides.
  function belegtPlatz(zeile) {
    const vorgehen = wert(zeile, "vorgehen");
    if (vorgehen === "ersetzen")
      return ["aktiv", "inaktiv"].includes(wert(zeile, "status_neu"));
    // Whoever is missing from the file keeps their slot until discontinued.
    if (wert(zeile, "art") === "fehlt") return vorgehen !== "einstellen";
    return ["aktiv", "inaktiv", "aktualisieren", "zurueckholen"].includes(
      vorgehen,
    );
  }

  function doppelte_markieren() {
    const gesehen = new Map();
    for (const zeile of document.querySelectorAll("[data-zeile]")) {
      zeile.classList.remove("table-danger");
      if (!belegtPlatz(zeile)) continue;
      const schluessel = ware(zeile);
      if (!schluessel) continue;
      if (gesehen.has(schluessel)) {
        zeile.classList.add("table-danger");
        gesehen.get(schluessel).classList.add("table-danger");
      } else {
        gesehen.set(schluessel, zeile);
      }
    }
  }

  document.addEventListener("change", (ereignis) => {
    const zeile = ereignis.target.closest("[data-zeile]");
    if (zeile && /-(vorgehen|uebernehmen)$/.test(ereignis.target.name))
      einordnen(zeile);
    if (zeile && /-ware$/.test(ereignis.target.name)) umrechnungRichten(zeile);
    doppelte_markieren();
    zaehlen();
  });

  document.addEventListener("click", (ereignis) => {
    const knopf = ereignis.target.closest("[data-alle-setzen]");
    if (!knopf) return;
    // The button is in the summary. Without this the click would stick on
    // the '<summary>' and collapse the product group.
    ereignis.preventDefault();
    const gruppe = knopf.closest("[data-warengruppe-block]");
    if (!gruppe) return;
    alleSetzen(gruppe, knopf.dataset.alleSetzen);
    doppelte_markieren();
    zaehlen();
  });

  for (const zeile of document.querySelectorAll("[data-zeile]")) {
    einordnen(zeile);
    umrechnungRichten(zeile);
  }
  doppelte_markieren();
  zaehlen();
})();
