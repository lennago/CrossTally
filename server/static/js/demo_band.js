/**
 * Measures the demo banner and writes its height as `--band-hoehe`.
 *
 * The banner sticks at the top, and everything that sticks as well attaches
 * itself below it: the navigation bar and the page's header bar. For that its
 * height has to be known, and that is not fixed. On the phone the sentence
 * wraps onto two or three lines. A guessed number would leave a gap there or
 * cover the title.
 *
 * Without this script the variable stays 0. Then the bar sits at the very top
 * again and slides over the banner when scrolling, as before this change.
 * Nothing shifts, only the banner disappears.
 */
(function () {
  const band = document.querySelector(".demo-band");
  if (!band) return;

  function messen() {
    document.documentElement.style.setProperty(
      "--band-hoehe",
      band.offsetHeight + "px",
    );
  }

  messen();
  // The height changes when rotating the device and with the font size.
  if (window.ResizeObserver) new ResizeObserver(messen).observe(band);
  else window.addEventListener("resize", messen);
})();
