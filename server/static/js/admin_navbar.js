/*
 * The dropdown menus of the bar in the administration close like at the front.
 *
 * Expanding and collapsing <details> can do alone. What it lacks is what one
 * is used to from Bootstrap on the site: a click next to it, choosing an
 * entry or "Escape" closes the menu again. Without this script the bar stays
 * usable. The menu then just stays open until one collapses it.
 */
(function () {
  const leiste = document.querySelector(".leiste");
  if (!leiste) return;

  function schliessen(ausser) {
    for (const menue of leiste.querySelectorAll("details[open]")) {
      if (menue !== ausser) menue.removeAttribute("open");
    }
  }

  document.addEventListener("click", (ereignis) => {
    // An entry in the menu closes it. A click on the head belongs to <details>.
    const imMenue = ereignis.target.closest(
      ".leiste-menue a, .leiste-menue button",
    );
    schliessen(imMenue ? null : ereignis.target.closest(".leiste details"));
  });

  document.addEventListener("keydown", (ereignis) => {
    if (ereignis.key !== "Escape") return;
    const offen = leiste.querySelector("details[open]");
    if (!offen) return;
    schliessen(null);
    // The focus was in the menu that no longer exists. Back onto the head.
    offen.querySelector("summary").focus();
  });
})();
