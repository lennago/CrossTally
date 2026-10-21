/*
 * Why the inputs are gone: the session had expired while someone was typing.
 *
 * A script that hears 401 from the server ('StaffOnlyMiddleware') leaves a
 * flag in 'sessionStorage' and lets the page go to the sign-in: a row button,
 * "Refresh", the filter and the preview. This shows the notice the template
 * holds hidden ('data-abgemeldet'). The text stands in the template, because
 * gettext does not work in a '.js' file.
 */
(function () {
  const hinweis = document.querySelector("[data-abgemeldet]");
  if (!hinweis || sessionStorage.getItem("abgemeldet") !== "1") return;
  sessionStorage.removeItem("abgemeldet");
  hinweis.hidden = false;
})();
