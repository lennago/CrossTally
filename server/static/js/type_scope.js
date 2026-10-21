/*
 * A new inventory type asks for whom it applies, but only once it is new.
 *
 * Next to the choice of the type stands the question "only this article"
 * or "all articles of the product". As long as the stored type stays
 * chosen there is nothing to answer, and the question would only be in the
 * way. 'data-geltung' names the selection it hangs on, 'data-gespeichert'
 * the stored value. Compared with that and not with the value at loading:
 * after a rejected attempt the page comes back with the new type chosen,
 * and the question has to stand there. Without JavaScript it always stands
 * there.
 */
(function () {
  for (const frage of document.querySelectorAll("[data-geltung]")) {
    const wahl = document.getElementById(frage.dataset.geltung);
    if (!wahl) continue;
    const zeigen = () => {
      frage.hidden = wahl.value === frage.dataset.gespeichert;
    };
    wahl.addEventListener("change", zeigen);
    zeigen();
  }
})();
