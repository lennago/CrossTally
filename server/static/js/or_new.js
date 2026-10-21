// Sections with 'data-neu-wenn-leer' belong to a selection list: they
// describe what is to be created if nothing existing is chosen there.
// Without JavaScript they stay visible. The form points out on submit
// that both together does not work.
for (const abschnitt of document.querySelectorAll("[data-neu-wenn-leer]")) {
  const auswahl = document.getElementById(abschnitt.dataset.neuWennLeer);
  if (!auswahl) continue;

  const richten = () => {
    abschnitt.hidden = Boolean(auswahl.value);
  };
  auswahl.addEventListener("change", richten);
  richten();
}
