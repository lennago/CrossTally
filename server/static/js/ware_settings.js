// The settings on the page of a house article belong to the product the
// article has. Whoever chooses another product above means that one: the
// card hides, and the server skips what stands in it. Back on the previous
// product it shows again, with everything that was typed.
//
// Without JavaScript the card stays visible. The server decides the same
// way, by the chosen product.
(function () {
  const karte = document.querySelector("[data-einstellungen-der-ware]");
  const ware = document.getElementById("id_ware");
  if (!karte || !ware) return;

  const richten = () => {
    karte.hidden = ware.value !== karte.dataset.einstellungenDerWare;
  };
  // Tom Select reports its choice on the original <select>.
  ware.addEventListener("change", richten);
  richten();
})();
