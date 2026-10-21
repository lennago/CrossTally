/*
 * Signing in, registering and confirming with a passkey (WebAuthn).
 *
 * The browser does the work: it asks the device for FaceID, Touch ID,
 * Windows Hello or a security key. This script only carries the server's
 * question to the browser and the browser's answer back. Three buttons:
 *
 *   [data-passkey-anmelden]    on the login form, signs in without a user name
 *   [data-passkey-anlegen]     on the account's passkey page, creates a key
 *                              against the password in [data-passkey-passwort]
 *   [data-passkey-bestaetigen] on the passkey page and the address page of an
 *                              account without a password: one of its keys
 *                              confirms the person before the step that needs
 *                              it, and [data-passkey-bestaetigt] shows
 *
 * Each names its two endpoints in 'data-passkey-optionen' and
 * 'data-passkey-ziel'. Signing in names them relative to the page: the decoy
 * login under '/admin/' shows the same form and answers under its own
 * address ('accounts/honeypot.py'). The buttons stay hidden where the
 * browser cannot do this, and without the script. What the server says on
 * failure arrives as JSON ('fehler') and is shown in the box of the block.
 * The texts of the browser's own refusals come in on the script tag
 * ('components/passkey_script.html').
 */
(function () {
  const TEXT = document.currentScript.dataset;
  if (!window.PublicKeyCredential || !window.fetch) return;

  // The JSON of the standard carries bytes as base64url. The browser's API
  // wants buffers and gives buffers back.
  function vonBase64url(text) {
    const normal = text.replace(/-/g, "+").replace(/_/g, "/");
    const aufgefuellt = normal + "=".repeat((4 - (normal.length % 4)) % 4);
    return Uint8Array.from(atob(aufgefuellt), (zeichen) =>
      zeichen.charCodeAt(0),
    );
  }

  function nachBase64url(puffer) {
    const zeichen = String.fromCharCode(...new Uint8Array(puffer));
    return btoa(zeichen)
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
  }

  // As the browser holds it now ('shared.js'). The page's own was refused
  // after signing in anew in another tab (until 10/2026).
  function csrf() {
    const feld = document.querySelector("input[name=csrfmiddlewaretoken]");
    return window.csrfMarke(feld ? feld.value : "");
  }

  // Marked as a script's request like every other ('shared.js'). Without a
  // session the server answers 401 ('NurAngemeldet' in 'accounts/passkey.py')
  // and no longer knows the account's language: the page names what this
  // step says then ('abgemeldet').
  async function senden(adresse, daten, abgemeldet = TEXT.abgemeldet) {
    const antwort = await window.imHintergrund(adresse, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrf(),
      },
      body: JSON.stringify(daten),
    });
    let inhalt = {};
    try {
      inhalt = await antwort.json();
    } catch (fehler) {
      /* no JSON: the message below says it */
    }
    if (antwort.status === 401) throw new Error(abgemeldet);
    // Too many wrong passwords: 'axes' answers with its page, not with JSON.
    if (antwort.status === 429) throw new Error(TEXT.gesperrt);
    if (!antwort.ok) throw new Error(inhalt.fehler || TEXT.fehler);
    return inhalt;
  }

  // The options as the browser wants them: the byte fields decoded.
  function anlegeOptionen(json) {
    return {
      ...json,
      challenge: vonBase64url(json.challenge),
      user: { ...json.user, id: vonBase64url(json.user.id) },
      excludeCredentials: (json.excludeCredentials || []).map((eintrag) => ({
        ...eintrag,
        id: vonBase64url(eintrag.id),
      })),
    };
  }

  function anmeldeOptionen(json) {
    return {
      ...json,
      challenge: vonBase64url(json.challenge),
      allowCredentials: (json.allowCredentials || []).map((eintrag) => ({
        ...eintrag,
        id: vonBase64url(eintrag.id),
      })),
    };
  }

  // The browser's answer as JSON, the way the server's library reads it.
  function alsJson(credential) {
    const antwort = credential.response;
    const daten = {
      id: credential.id,
      rawId: nachBase64url(credential.rawId),
      type: credential.type,
      response: { clientDataJSON: nachBase64url(antwort.clientDataJSON) },
    };
    if (credential.authenticatorAttachment) {
      daten.authenticatorAttachment = credential.authenticatorAttachment;
    }
    if (antwort.attestationObject) {
      daten.response.attestationObject = nachBase64url(
        antwort.attestationObject,
      );
      if (antwort.getTransports)
        daten.response.transports = antwort.getTransports();
    } else {
      daten.response.authenticatorData = nachBase64url(
        antwort.authenticatorData,
      );
      daten.response.signature = nachBase64url(antwort.signature);
      daten.response.userHandle = antwort.userHandle
        ? nachBase64url(antwort.userHandle)
        : null;
    }
    return daten;
  }

  function melden(knopf, text) {
    const feld = knopf
      .closest("[data-passkey]")
      ?.querySelector("[data-passkey-meldung]");
    if (!feld) return;
    feld.textContent = text || "";
    feld.hidden = !text;
  }

  // What the browser's refusal means for the person.
  function erklaert(fehler) {
    if (fehler.name === "NotAllowedError") return TEXT.abgebrochen;
    if (fehler.name === "InvalidStateError") return TEXT.schonDa;
    return fehler.message || TEXT.fehler;
  }

  async function ausfuehren(knopf, arbeit) {
    melden(knopf, "");
    knopf.disabled = true;
    try {
      await arbeit();
    } catch (fehler) {
      melden(knopf, erklaert(fehler));
      knopf.disabled = false;
    }
  }

  // The block appears, and the note for browsers without passkeys goes.
  function zeigen(knopf) {
    const block = knopf.closest("[data-passkey]");
    block.hidden = false;
    const hinweis = block.parentElement.querySelector("[data-passkey-ohne]");
    if (hinweis) hinweis.hidden = true;
  }

  for (const knopf of document.querySelectorAll("[data-passkey-anmelden]")) {
    zeigen(knopf);
    knopf.addEventListener("click", () =>
      ausfuehren(knopf, async () => {
        const optionen = await senden(knopf.dataset.passkeyOptionen, {});
        const credential = await navigator.credentials.get({
          publicKey: anmeldeOptionen(optionen),
        });
        const weiter = document.querySelector("input[name=next]");
        const antwort = await senden(knopf.dataset.passkeyZiel, {
          credential: alsJson(credential),
          next: weiter ? weiter.value : "",
        });
        window.location.assign(antwort.weiter);
      }),
    );
  }

  for (const knopf of document.querySelectorAll("[data-passkey-anlegen]")) {
    zeigen(knopf);
    knopf.addEventListener("click", () =>
      ausfuehren(knopf, async () => {
        // The password goes with the question, before the device's dialog.
        // Without the field the account has none to type.
        const passwort = document.querySelector("[data-passkey-passwort]");
        const optionen = await senden(
          knopf.dataset.passkeyOptionen,
          passwort ? { passwort: passwort.value } : {},
        );
        const credential = await navigator.credentials.create({
          publicKey: anlegeOptionen(optionen),
        });
        const name = document.querySelector("[data-passkey-name]");
        await senden(knopf.dataset.passkeyZiel, {
          credential: alsJson(credential),
          name: name ? name.value : "",
        });
        // The list comes from the server, with the message about the new key.
        window.location.reload();
      }),
    );
  }

  // The server notes the confirmation in the session, and the step after it
  // asks there: the key's options or the form of the address. Not used up,
  // so the button stays for a second try once the two minutes are over.
  for (const knopf of document.querySelectorAll("[data-passkey-bestaetigen]")) {
    zeigen(knopf);
    const bestaetigt = knopf.parentElement.querySelector(
      "[data-passkey-bestaetigt]",
    );
    knopf.addEventListener("click", () =>
      ausfuehren(knopf, async () => {
        if (bestaetigt) bestaetigt.hidden = true;
        const optionen = await senden(
          knopf.dataset.passkeyOptionen,
          {},
          TEXT.nichtBestaetigt,
        );
        const credential = await navigator.credentials.get({
          publicKey: anmeldeOptionen(optionen),
        });
        await senden(
          knopf.dataset.passkeyZiel,
          { credential: alsJson(credential) },
          TEXT.nichtBestaetigt,
        );
        if (bestaetigt) bestaetigt.hidden = false;
        knopf.disabled = false;
      }),
    );
  }
})();
