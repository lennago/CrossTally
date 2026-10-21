/*
 * Light or dark before the page is painted the first time, otherwise the
 * light version would briefly flash at startup on a dark device. Read is the
 * last known choice ('theme', as on the website). What is on the account is
 * adopted afterwards by 'services/theme.svelte.js'.
 *
 * A file in 'public/' and no inline script in the 'index.html': the
 * Content-Security-Policy allows only scripts from here, and the start page
 * is served by WhiteNoise. Nobody would write a nonce into it there. Included
 * classically, a file too holds up the first paint, and the service worker
 * keeps it ready for the start without network like everything else.
 */
(function () {
    var choice = "auto";
    try {
        choice = localStorage.getItem("theme") || "auto";
    } catch (error) {}
    var dark =
        choice === "dark" ||
        (choice === "auto" &&
            window.matchMedia &&
            window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.dataset.theme = dark ? "dark" : "light";
})();
