/*
 * What the scripts that ask the server in the background share.
 *
 * **The request** ('imHintergrund'): marked as a script's
 * ('X-Requested-With: fetch'). The server then answers with a status where
 * it would otherwise lead to another page ('im_hintergrund' in
 * 'django_project/middleware.py'): 401 without a session, 403 without the
 * role. 'fetch' follows a redirect by itself, and the sign-in page answers
 * 200. Until 10/2026 a save in the background therefore counted as done
 * after the session had expired. What a 401 means stays with each script: a
 * save says so where it stands, and whatever brings the page anew leads to
 * the sign-in ('login_notice.js').
 *
 * **The CSRF token** ('csrfMarke') for a request a script sends, as the
 * browser holds it now. Django renews the token on signing in. A page that
 * was open before keeps the old one in its forms, and after signing in anew
 * in another tab Django refuses it (403). The cookie holds the current one at
 * the time of sending, and it stays readable for scripts
 * ('django_project/test_settings.py'). Without the cookie the page's own
 * token applies ('ersatz').
 *
 * Of two cookies of that name Django takes the last one, and so does this.
 * A second one can come from a site on a parent domain.
 *
 * A classic script and no module, like the others: they read their texts
 * from their own tag ('document.currentScript'), and a module has none. So
 * the helpers hang on 'window'. Every page includes this one before the
 * scripts that use it ('base.html').
 */
(function () {
  const NAME = "csrftoken=";

  window.csrfMarke = (ersatz) => {
    const eintraege = document.cookie
      .split("; ")
      .filter((teil) => teil.startsWith(NAME));
    const eintrag = eintraege[eintraege.length - 1];
    return eintrag ? decodeURIComponent(eintrag.slice(NAME.length)) : ersatz;
  };

  // What the caller passes (method, body, a signal to abort with, headers
  // of its own as a plain object) goes along unchanged.
  window.imHintergrund = (adresse, optionen = {}) =>
    fetch(adresse, {
      ...optionen,
      headers: { ...optionen.headers, "X-Requested-With": "fetch" },
      credentials: "same-origin",
    });
})();
