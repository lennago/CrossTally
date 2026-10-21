/*
 * The password fields of a new account appear only with the tick
 * "Set the password yourself".
 *
 * With a mail server the invitation is the normal way, and two empty fields
 * nobody needs only raise the question whether they have to be filled.
 * Without a mail server there is no tick, and the fields simply stand there.
 * Without JavaScript too, and then the form says what it makes of them.
 *
 * Hidden fields are emptied, also right before sending: a password manager
 * may fill a field nobody sees, and the form would refuse a password without
 * the tick.
 *
 * The site includes the script at the end of the page, the administration in
 * its head ('Media'), where the form does not exist yet. Hence it waits for
 * the page there.
 */
(function () {
  function verbinden(haken) {
    // On the site crispy-forms wraps every field in 'div_id_<name>', in the
    // administration the row carries the class 'field-<name>'.
    const zeilen = ["password1", "password2"]
      .map((name) => document.querySelector(`#div_id_${name}, .field-${name}`))
      .filter(Boolean);

    function leeren() {
      for (const zeile of zeilen) {
        for (const feld of zeile.querySelectorAll("input")) feld.value = "";
      }
    }

    function anpassen() {
      for (const zeile of zeilen) zeile.hidden = !haken.checked;
      if (!haken.checked) leeren();
    }

    haken.addEventListener("change", anpassen);
    haken.form?.addEventListener("submit", () => {
      if (!haken.checked) leeren();
    });
    anpassen();
  }

  function einrichten() {
    const haken = document.querySelector("#id_passwort_setzen");
    if (haken) verbinden(haken);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", einrichten);
  } else {
    einrichten();
  }
})();
