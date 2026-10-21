/**
 * All texts of the app, in both languages side by side.
 *
 * German is the original: every text arises there first, and if the English
 * version is missing, the German one applies ('services/language.svelte.js').
 * Both stand in the same line so a gap stands out when reading.
 *
 * The keys say where the text stands, not what it means: if the wording
 * changes, the key stays. An entry with 'one' and 'many' has a plural
 * form, selected by 'n'.
 *
 * Not translated are the data themselves: article, product, house and
 * location names stand as they are entered in the catalog.
 */

export const TEXTS = {
    // -- The way through the house ---------------------------------------
    "list.open": { de: "{n} offen", en: "{n} open" },
    "list.nothing_expected": { de: "nichts erwartet", en: "nothing expected" },
    "areas.empty": {
        de: "Dieses Haus hat noch keine Orte. Bereiche und Orte werden in der Weboberfläche angelegt.",
        en: "This site has no locations yet. Areas and locations are created in the web interface.",
    },
    "areas.loading": {
        de: "Die Orte dieser Inventur sind noch nicht geladen.",
        en: "The locations of this inventory have not been loaded yet.",
    },
    "inventories.locked": { de: "Gesperrt", en: "Locked" },
    "inventories.on_hold": { de: "wartet auf die Zählung", en: "waiting for the count" },
    "inventories.empty": {
        de: "Für dich steht gerade keine Inventur an. Zugeordnet werden Zählende in der Weboberfläche. Los geht es, sobald dort „Zählen starten“ gedrückt wurde.",
        en: "No inventory is waiting for you. Counters are assigned in the web interface. It starts once someone selects “Start counting” there.",
    },

    // -- Header bar and sync ---------------------------------------------
    "header.back": { de: "Zurück", en: "Back" },
    "header.cancel": { de: "Abbrechen", en: "Cancel" },
    "header.account": { de: "Konto", en: "Account" },
    "header.refresh": { de: "Auffrischen", en: "Refresh" },
    "header.sync": { de: "Abgleich", en: "Sync" },
    "sync_state.online": { de: "Verbunden, abgeglichen", en: "Online, in sync" },
    "sync_state.offline": { de: "Offline", en: "Offline" },
    "sync_state.offline_pending": { de: "Offline, {n} wartet", en: "Offline, {n} waiting" },
    "sync_state.attention": { de: "Bitte ansehen", en: "Needs attention" },
    "app.loading": { de: "Einen Augenblick …", en: "One moment …" },
    "app.new_version": { de: "Eine neue Fassung steht bereit", en: "A new version is ready" },
    "app.load_now": { de: "Jetzt laden", en: "Load now" },
    "app.counted_by_other": {
        de: "Hier hat schon jemand anderes gezählt",
        en: "Someone else has already counted here",
    },
    "app.decide": { de: "Entscheiden", en: "Decide" },
    "app.not_saved": { de: "Nicht gespeichert: {label}", en: "Not saved: {label}" },

    // -- Login ------------------------------------------------------------
    "login.title": { de: "Inventur", en: "Inventory" },
    "login.username": { de: "Benutzername", en: "Username" },
    "login.password": { de: "Passwort", en: "Password" },
    "login.button": { de: "Anmelden", en: "Sign in" },
    "login.running": { de: "Anmelden …", en: "Signing in …" },
    "login.no_connection": {
        de: "Keine Verbindung zum Server.",
        en: "No connection to the server.",
    },
    "login.wrong_credentials": {
        de: "Benutzername oder Passwort stimmt nicht.",
        en: "Username or password is wrong.",
    },
    "login.server_error": {
        de: "Der Server antwortet gerade nicht. Bitte versuche es später noch einmal.",
        en: "The server is not answering right now. Please try again later.",
    },
    "login.previous_account": { de: "dem vorigen Konto", en: "the previous account" },
    "login.or": { de: "oder", en: "or" },
    "login.passkey": { de: "Mit Passkey anmelden", en: "Sign in with passkey" },
    "login.passkey_account": { de: "dem Konto des Passkeys", en: "the passkey's account" },
    "login.passkey_cancelled": {
        de: "Abgebrochen, oder auf diesem Gerät gibt es keinen Passkey für diese Adresse.",
        en: "Cancelled, or this device has no passkey for this address.",
    },
    "login.passkey_failed": {
        de: "Die Anmeldung mit dem Passkey hat nicht geklappt.",
        en: "Signing in with the passkey did not work.",
    },
    "login.expired": {
        de: {
            one: "Die Anmeldung ist abgelaufen. {n} Eingabe wartet noch und geht nach dem Anmelden hinaus.",
            many: "Die Anmeldung ist abgelaufen. {n} Eingaben warten noch und gehen nach dem Anmelden hinaus.",
        },
        en: {
            one: "Your session has expired. {n} entry is still waiting and will be sent once you sign in.",
            many: "Your session has expired. {n} entries are still waiting and will be sent once you sign in.",
        },
    },
    "login.foreign": {
        de: {
            one: "Auf diesem Gerät wartet noch {n} Eingabe von {who}, die nicht beim Server ist. Meldet sich {who} hier an, geht sie hinaus.",
            many: "Auf diesem Gerät warten noch {n} Eingaben von {who}, die nicht beim Server sind. Meldet sich {who} hier an, gehen sie hinaus.",
        },
        en: {
            one: "This device still holds {n} entry from {who} that has not reached the server. If {who} signs in here, it will be sent.",
            many: "This device still holds {n} entries from {who} that have not reached the server. If {who} signs in here, they will be sent.",
        },
    },
    "login.foreign_unsaved": {
        de: {
            one: "Auf diesem Gerät liegt noch {n} Eingabe von {who}, die nicht gespeichert wurde. Meldet sich {who} hier an, kann es damit weitergehen.",
            many: "Auf diesem Gerät liegen noch {n} Eingaben von {who}, die nicht gespeichert wurden. Meldet sich {who} hier an, kann es damit weitergehen.",
        },
        en: {
            one: "This device still holds {n} entry from {who} that was not saved. If {who} signs in here, it can be continued.",
            many: "This device still holds {n} entries from {who} that were not saved. If {who} signs in here, they can be continued.",
        },
    },
    "login.discard": {
        de: "Eingaben löschen und als {who} anmelden",
        en: "Discard entries and sign in as {who}",
    },

    // -- Account menu -----------------------------------------------------
    "account.who": { de: "Angemeldet als {who}", en: "Signed in as {who}" },
    "account.sign_out": { de: "Abmelden", en: "Sign out" },
    "account.waiting": {
        de: {
            one: "{n} Eingabe ist noch nicht beim Server. Abmelden löscht sie von diesem Gerät.",
            many: "{n} Eingaben sind noch nicht beim Server. Abmelden löscht sie alle von diesem Gerät.",
        },
        en: {
            one: "{n} entry has not reached the server yet. Signing out deletes it from this device.",
            many: "{n} entries have not reached the server yet. Signing out deletes all of them from this device.",
        },
    },
    "account.unsaved": {
        de: {
            one: "{n} Eingabe wurde nicht gespeichert. Abmelden löscht sie von diesem Gerät.",
            many: "{n} Eingaben wurden nicht gespeichert. Abmelden löscht sie alle von diesem Gerät.",
        },
        en: {
            one: "{n} entry was not saved. Signing out deletes it from this device.",
            many: "{n} entries were not saved. Signing out deletes all of them from this device.",
        },
    },
    "account.sync_now": { de: "Jetzt abgleichen", en: "Sync now" },
    "account.syncing": { de: "Läuft …", en: "Working …" },
    "account.sign_out_anyway": {
        de: "Trotzdem abmelden und löschen",
        en: "Sign out and delete anyway",
    },
    "account.privacy": { de: "Datenschutz", en: "Privacy" },
    "account.legal_notice": { de: "Impressum", en: "Legal notice" },
    "account.change_password": { de: "Passwort ändern", en: "Change password" },
    "account.change_email": { de: "E-Mail-Adresse ändern", en: "Change e-mail address" },
    "account.passkeys": { de: "Passkeys", en: "Passkeys" },
    "account.feedback": { de: "Problem oder Wunsch melden", en: "Report a problem or suggestion" },
    "account.version": { de: "Version {version}", en: "Version {version}" },
    "appearance.title": { de: "Darstellung", en: "Appearance" },
    "appearance.auto": { de: "Automatisch", en: "Automatic" },
    "appearance.light": { de: "Hell", en: "Light" },
    "appearance.dark": { de: "Dunkel", en: "Dark" },
    "language.title": { de: "Sprache", en: "Language" },

    // -- The mail behind "Report a problem or suggestion" ----------------
    "feedback_mail.subject": {
        de: "Rückmeldung aus der Zähl-App",
        en: "Feedback from the counting app",
    },
    "feedback_mail.question": {
        de: "Was ist passiert, oder was wünschst du dir?",
        en: "What happened, or what would you like to see?",
    },
    "feedback_mail.details": {
        de: "Technische Angaben, bitte stehen lassen:",
        en: "Technical details, please leave them in:",
    },
    "feedback_mail.version": { de: "Version", en: "Version" },
    "feedback_mail.page": { de: "Seite", en: "Page" },
    "feedback_mail.account": { de: "Konto", en: "Account" },
    "feedback_mail.time": { de: "Zeit", en: "Time" },
    "feedback_mail.browser": { de: "Browser", en: "Browser" },
    "feedback_mail.connection": { de: "Verbindung", en: "Connection" },
    "feedback_mail.online": { de: "online", en: "online" },
    "feedback_mail.offline": { de: "offline", en: "offline" },
    "feedback_mail.waiting": { de: "Wartende Eingaben", en: "Entries waiting" },

    // -- Passkeys ----------------------------------------------------------
    "passkey.title": { de: "Passkeys", en: "Passkeys" },
    "passkey.close": { de: "Schließen", en: "Close" },
    "passkey.hint": {
        de: "Anmelden ohne Passwort, mit Gesicht, Finger oder PIN dieses Geräts. Der Passkey entsteht hier und bleibt hier, bei Apple und Google auch auf den anderen Geräten desselben Kontos.",
        en: "Sign in without a password, with face, finger or PIN of this device. The passkey is created here and stays here, with Apple and Google also on the other devices of the same account.",
    },
    "passkey.loading": { de: "Wird geladen …", en: "Loading …" },
    "passkey.none": {
        de: "Noch kein Passkey. Mit dem Passwort geht es weiter wie bisher.",
        en: "No passkey yet. The password works as before.",
    },
    "passkey.created_on": { de: "angelegt am {date}", en: "created on {date}" },
    "passkey.last_used": { de: "zuletzt benutzt {date}", en: "last used {date}" },
    "passkey.never_used": { de: "noch nie benutzt", en: "never used yet" },
    "passkey.remove": { de: "Entfernen", en: "Remove" },
    "passkey.remove_sure": {
        de: "Wirklich entfernen? Das Gerät kann sich damit nicht mehr anmelden, und auf deinen anderen Geräten wirst du abgemeldet.",
        en: "Really remove? The device can no longer sign in with it, and you are signed out on your other devices.",
    },
    "passkey.remove_confirm": { de: "Ja, entfernen", en: "Yes, remove" },
    "passkey.removing": { de: "Wird entfernt …", en: "Removing …" },
    // Removing asks what adding asks: the password, or a passkey's confirmation without one.
    "passkey.remove_password": {
        de: "Dein Passwort zum Entfernen",
        en: "Your password to remove it",
    },
    "passkey.remove_password_hint": {
        de: "Zur Sicherheit, damit niemand einen Passkey entfernt, der nur dein Gerät hat.",
        en: "For safety, so that nobody removes a passkey who only has your device.",
    },
    "passkey.remove_confirm_hint": {
        de: "Zur Sicherheit bestätigst du zuerst mit einem Passkey deines Kontos. Danach entfernst du ihn.",
        en: "For safety, you first confirm with a passkey of your account. Then you remove it.",
    },
    "passkey.remove_confirmed": {
        de: "Bestätigt. Jetzt kannst du ihn entfernen.",
        en: "Confirmed. Now you can remove it.",
    },
    "passkey.cancel": { de: "Abbrechen", en: "Cancel" },
    "passkey.name": { de: "Name, z. B. mein Handy", en: "Name, e.g. my phone" },
    "passkey.password": { de: "Dein Passwort", en: "Your password" },
    "passkey.password_hint": {
        de: "Zur Sicherheit, damit niemand einen Passkey anlegt, der nur dein Gerät hat.",
        en: "For safety, so that nobody adds a passkey who only has your device.",
    },
    "passkey.confirm_hint": {
        de: "Zur Sicherheit bestätigst du zuerst mit einem Passkey, den dein Konto schon hat. Danach legst du den neuen an.",
        en: "For safety, you first confirm with a passkey your account already has. Then you create the new one.",
    },
    "passkey.confirm": { de: "Mit Passkey bestätigen", en: "Confirm with passkey" },
    "passkey.confirming": { de: "Wird bestätigt …", en: "Confirming …" },
    "passkey.confirmed": {
        de: "Bestätigt. Jetzt kannst du den neuen Passkey anlegen.",
        en: "Confirmed. Now you can create the new passkey.",
    },
    "passkey.confirmation_expired": {
        de: "Die Bestätigung gilt nur zwei Minuten. Bitte bestätige noch einmal.",
        en: "The confirmation only holds for two minutes. Please confirm again.",
    },
    "passkey.create": {
        de: "Passkey für dieses Gerät anlegen",
        en: "Create a passkey for this device",
    },
    "passkey.creating": { de: "Wird angelegt …", en: "Creating …" },
    "passkey.created": {
        de: "Der Passkey „{name}“ ist angelegt. Beim nächsten Anmelden genügt er.",
        en: "The passkey “{name}” is created. Next time it is all you need to sign in.",
    },
    "passkey.cancelled": { de: "Abgebrochen.", en: "Cancelled." },
    "passkey.exists": {
        de: "Für dieses Konto gibt es auf diesem Gerät schon einen Passkey.",
        en: "This device already has a passkey for this account.",
    },
    "passkey.no_connection": {
        de: "Keine Verbindung zum Server. Passkeys lassen sich nur mit Verbindung verwalten.",
        en: "No connection to the server. Passkeys can only be managed while connected.",
    },
    "passkey.failed": {
        de: "Das hat nicht geklappt. Bitte noch einmal versuchen.",
        en: "That did not work. Please try again.",
    },

    // -- Confirming with a passkey ---------------------------------------
    // In place of the password, for an account without one: before a new
    // passkey and a new address. The device's refusal reads as at the sign-in.
    "confirm.cancelled": {
        de: "Abgebrochen, oder auf diesem Gerät gibt es keinen Passkey für dieses Konto.",
        en: "Cancelled, or this device has no passkey for this account.",
    },

    // -- Change password -------------------------------------------------
    "password.title": { de: "Passwort ändern", en: "Change password" },
    "password.cancel": { de: "Abbrechen", en: "Cancel" },
    "password.close": { de: "Schließen", en: "Close" },
    "password.hint": {
        de: "Das neue Passwort gilt auch auf der Website. Dieses Gerät bleibt angemeldet. Auf jedem anderen meldest du dich neu an.",
        en: "The new password also applies on the website. This device stays signed in. On every other one you sign in again.",
    },
    "password.old": { de: "Altes Passwort", en: "Old password" },
    "password.new": { de: "Neues Passwort", en: "New password" },
    "password.repeat": { de: "Neues Passwort wiederholen", en: "Repeat new password" },
    "password.submit": { de: "Passwort ändern", en: "Change password" },
    "password.running": { de: "Wird geändert …", en: "Changing …" },
    "password.mismatch": {
        de: "Die beiden neuen Passwörter stimmen nicht überein.",
        en: "The two new passwords do not match.",
    },
    "password.no_connection": {
        de: "Keine Verbindung zum Server. Das Passwort lässt sich nur mit Verbindung ändern.",
        en: "No connection to the server. The password can only be changed while connected.",
    },
    "password.failed": {
        de: "Das Passwort wurde nicht geändert. Bitte versuche es später noch einmal.",
        en: "The password was not changed. Please try again later.",
    },
    "password.changed": {
        de: "Dein Passwort ist geändert. Was auf anderen Geräten noch wartet, geht nach dem Anmelden hinaus.",
        en: "Your password has been changed. What still waits on other devices will be sent once you sign in there.",
    },

    // -- Change e-mail address -------------------------------------------
    "email.title": { de: "E-Mail-Adresse ändern", en: "Change e-mail address" },
    "email.cancel": { de: "Abbrechen", en: "Cancel" },
    "email.close": { de: "Schließen", en: "Close" },
    "email.current": { de: "Zurzeit gilt {email}.", en: "Currently {email} applies." },
    "email.hint_confirm": {
        de: "An die neue Adresse geht eine Mail mit einem Link. Erst wenn du ihn bestätigt hast, gilt sie.",
        en: "A mail with a link goes to the new address. It applies once you have confirmed it.",
    },
    "email.hint_direct": {
        de: "Die neue Adresse gilt sofort.",
        en: "The new address applies at once.",
    },
    "email.new": { de: "Neue E-Mail-Adresse", en: "New e-mail address" },
    "email.password": { de: "Dein Passwort", en: "Your password" },
    "email.confirm_hint": {
        de: "Zur Sicherheit fragt dein Gerät zuerst nach einem Passkey deines Kontos.",
        en: "For safety, your device first asks for a passkey of your account.",
    },
    "email.submit_confirm": { de: "Mail schicken", en: "Send mail" },
    "email.submit_direct": { de: "Adresse ändern", en: "Change address" },
    "email.running": { de: "Wird gesendet …", en: "Sending …" },
    "email.sent": {
        de: "An {email} ist eine Mail mit einem Link unterwegs. Erst wenn du ihn bestätigt hast, gilt die neue Adresse.",
        en: "A mail with a link is on its way to {email}. The new address applies once you have confirmed it.",
    },
    "email.changed": { de: "Deine Adresse ist jetzt {email}.", en: "Your address is now {email}." },
    "email.no_connection": {
        de: "Keine Verbindung zum Server. Die Adresse lässt sich nur mit Verbindung ändern.",
        en: "No connection to the server. The address can only be changed while connected.",
    },
    "email.not_sent": {
        de: "Die Mail ging nicht hinaus, der E-Mail-Server antwortet nicht. Bitte versuche es später noch einmal.",
        en: "The mail did not leave, the e-mail server does not answer. Please try again later.",
    },
    "email.failed": {
        de: "Die Adresse wurde nicht geändert. Bitte versuche es später noch einmal.",
        en: "The address was not changed. Please try again later.",
    },

    // -- Search and add articles -----------------------------------------
    "search.title": { de: "Artikel suchen", en: "Search articles" },
    "search.add": { de: "Artikel hinzufügen", en: "Add article" },
    "search.discontinued": { de: "eingestellt", en: "discontinued" },
    "search.inactive": { de: "inaktiv", en: "inactive" },
    "search.no_match": { de: "Kein Artikel passt dazu.", en: "No article matches." },
    "search.all_listed": {
        de: "Hier ist schon alles aufgeführt.",
        en: "Everything is already listed.",
    },
    "search.more": {
        de: "… und {n} weitere. Bitte genauer suchen.",
        en: "… and {n} more. Please narrow the search.",
    },

    // -- Sync -------------------------------------------------------------
    "sync.title": { de: "Abgleich", en: "Sync" },
    "sync.close": { de: "Schließen", en: "Close" },
    "sync.online": { de: "Verbunden", en: "Online" },
    "sync.offline": { de: "Keine Verbindung", en: "No connection" },
    "sync.last": { de: "zuletzt abgeglichen: {time}", en: "last sync: {time}" },
    "sync.never": { de: "noch nie", en: "never" },
    "sync.empty": { de: "leer", en: "empty" },
    "sync.conflicts": { de: "Schon gezählt ({n})", en: "Already counted ({n})" },
    "sync.conflicts_text": {
        de: "Hier hat inzwischen jemand anderes gezählt. Was soll mit deiner Zahl geschehen?",
        en: "Someone else has counted here in the meantime. What should happen to your number?",
    },
    "sync.theirs_empty": {
        de: "Dort steht inzwischen nichts mehr.",
        en: "Nothing is recorded there any more.",
    },
    "sync.theirs": {
        de: "Dort steht inzwischen: {quantity}.",
        en: "Recorded there now: {quantity}.",
    },
    "sync.mine": { de: "Deine Zahl: {quantity}.", en: "Your number: {quantity}." },
    "sync.add": { de: "Addieren: {quantity}", en: "Add up: {quantity}" },
    "sync.discard_empty": { de: "Meine verwerfen: bleibt leer", en: "Discard mine: stays empty" },
    "sync.discard": {
        de: "Meine verwerfen: {quantity} bleibt",
        en: "Discard mine: {quantity} remains",
    },
    "sync.overwrite": { de: "Überschreiben: {quantity}", en: "Overwrite: {quantity}" },
    "sync.waiting": { de: "Wartet auf den Server ({n})", en: "Waiting for the server ({n})" },
    "sync.all_sent": { de: "Alles ist draußen.", en: "Everything has been sent." },
    "sync.rejected": { de: "Nicht angenommen ({n})", en: "Not accepted ({n})" },
    "sync.rejected_text": {
        de: "Diese Eingaben sind <strong>nicht</strong> beim Server angekommen. Bitte noch einmal zählen und eintragen. Danach lässt sich der Hinweis wegräumen.",
        en: "These entries did <strong>not</strong> reach the server. Please count and enter them again. The notice can be cleared afterwards.",
    },
    "sync.dismiss_notices": { de: "Hinweise verwerfen", en: "Dismiss notices" },
    // Why a job waits although there is network: the server's words for it.
    "sync.on_hold": { de: "Wartet: {reason}", en: "Waiting: {reason}" },
    "sync.weight": { de: "{quantity} g", en: "{quantity} g" },
    // The job kinds of the queue and of the rejected jobs.
    "job.position": { de: "Neue Zeile", en: "New line" },
    "job.position_removed": { de: "Zeile entfernt", en: "Line removed" },
    "job.quantity": { de: "Menge", en: "Quantity" },
    "job.weighing": { de: "Wiegung", en: "Weighing" },
    "job.weighing_changed": { de: "Wiegung berichtigt", en: "Weighing corrected" },
    "job.weighing_removed": { de: "Wiegung entfernt", en: "Weighing removed" },
    "job.note": { de: "Notiz", en: "Note" },
    "job.difference": { de: "Differenz", en: "Difference" },
    "job.target": { de: "Soll zählt", en: "Target counts" },
    "job.on": { de: "an", en: "on" },
    "job.off": { de: "aus", en: "off" },

    // -- Articles of a location ------------------------------------------
    "articles.types": { de: "Inventurarten", en: "Inventory types" },
    "articles.rows": {
        de: { one: "{n} Zeile · {open} offen", many: "{n} Zeilen · {open} offen" },
        en: { one: "{n} line · {open} open", many: "{n} lines · {open} open" },
    },
    "articles.empty": { de: "Hier wird nichts erwartet.", en: "Nothing is expected here." },

    // -- Review list ------------------------------------------------------
    "review.entry": { de: "Prüfliste", en: "Review list" },
    "review.entry_hint": {
        de: "Ist, Soll und Differenz je Artikel",
        en: "Actual, target and difference per article",
    },
    "review.entry_manual": {
        de: "Ist und Differenz von Hand je Artikel",
        en: "Actual and the difference by hand per article",
    },
    "review.filters": { de: "Zeigen", en: "Show" },
    "review.all": { de: "Alle", en: "All" },
    "review.deviating": { de: "Abweichung", en: "Deviating" },
    "review.uncounted": { de: "Ungezählt", en: "Uncounted" },
    "review.with_note": { de: "Notiz", en: "Note" },
    "review.summary": {
        de: "{n} Artikel · {deviating} mit Abweichung · {open} ungezählt",
        en: "{n} articles · {deviating} deviating · {open} uncounted",
    },
    "review.as_of": { de: "Stand {time}", en: "As of {time}" },
    "review.as_of_offline": {
        de: "ohne Verbindung, Stand {time}",
        en: "offline, as of {time}",
    },
    "review.not_loaded": {
        de: "Die Prüfliste kommt mit der nächsten Verbindung.",
        en: "The review list arrives with the next connection.",
    },
    "review.empty": {
        de: "In dieser Inventur steht noch kein Artikel.",
        en: "No article in this inventory yet.",
    },
    "review.number": { de: "Nr. {number}", en: "No. {number}" },
    "review.actual": { de: "Ist", en: "Actual" },
    "review.target": { de: "Soll", en: "Target" },
    "review.not_counted": { de: "noch nicht gezählt", en: "not counted yet" },
    "review.note_of": { de: "Notiz zu {name}", en: "Note on {name}" },
    "review.note_remove": { de: "Notiz zu {name} löschen", en: "Delete the note on {name}" },
    "review.note_remove_sure": {
        de: "Die Notiz zu {name} löschen?",
        en: "Delete the note on {name}?",
    },
    "review.note_save": { de: "Übernehmen", en: "Save" },
    "review.target_counts": { de: "Soll zählt", en: "Target counts" },
    "review.target_counts_of": { de: "Soll zählt bei {name}", en: "Target counts for {name}" },
    "review.target_counts_hint": {
        de: "Wo „Soll zählt“ an ist, gilt beim Abschluss der Sollbestand als Ergebnis. Bis dahin bleibt die Zählung, wie sie ist.",
        en: "Where “Target counts” is on, the target stock becomes the result on closing. Until then the count stays as it is.",
    },
    "review.difference_of": { de: "Differenz zu {name}", en: "Difference for {name}" },
    "review.sign_of": {
        de: "Vorzeichen der Differenz zu {name} umdrehen",
        en: "Switch the sign of the difference for {name}",
    },
    "review.manual": { de: "Differenz von Hand", en: "Difference by hand" },
    "review.manual_hint": {
        de: "Ohne Sollbestand: Die Differenz aus der Warenwirtschaft wird je Artikel eingetragen, in der Einheit des Ist. Ein Minus heißt: Es fehlt etwas.",
        en: "Without a target stock: the difference from the merchandise management system is entered per article, in the unit of the actual. A minus means: something is missing.",
    },
    "review.manual_counter": {
        de: "Die Differenz wird hier von Hand eingetragen, aus der Warenwirtschaft, in der Einheit des Ist. Ein Minus heißt: Es fehlt etwas.",
        en: "The difference is entered here by hand, from the merchandise management system, in the unit of the actual. A minus means: something is missing.",
    },
    "review.manual_offline": {
        de: "Umschalten geht nur mit Verbindung.",
        en: "Switching needs a connection.",
    },
    "review.locked": {
        de: "Gesperrt: Gezählt wird gerade nicht. Notizen und Differenzen gehen weiter.",
        en: "Locked: counting has stopped. Notes and differences go on.",
    },
    "review.lock": { de: "Sperren", en: "Lock" },
    "review.unlock": { de: "Öffnen", en: "Open" },

    // -- Entry ------------------------------------------------------------
    "entry.loading": { de: "Wird geladen …", en: "Loading …" },
    "entry.foreign": {
        de: "Dieser Artikel gehört nicht zu diesem Haus.",
        en: "This article does not belong to this site.",
    },
    "entry.counted_in": { de: "gezählt in {unit}", en: "counted in {unit}" },
    "entry.up_next": { de: "Danach: {name}", en: "Next: {name}" },
    "entry.left_behind": {
        de: "Hier war noch eine Eingabe offen, die nicht gespeichert wurde.",
        en: "An entry was left open here and never saved.",
    },
    "entry.continue": { de: "Fortsetzen", en: "Continue" },
    "entry.locked": {
        de: "Die Inventur ist gesperrt. Was du hier speicherst, wartet auf dem Gerät, bis sie wieder offen ist.",
        en: "The inventory is locked. What you save here waits on the device until it is open again.",
    },
    "entry.out_of_count": {
        de: "Was du hier eingibst, bleibt auf dem Gerät, bis du es speicherst oder abbrichst. Danach geht es zur Liste.",
        en: "What you enter here stays on this device until you save or cancel it. Then the list comes up.",
    },
    "entry.discard": { de: "Verwerfen", en: "Discard" },
    "entry.each": { de: "à {quantity} {unit}", en: "{quantity} {unit} each" },
    "entry.usual": { de: "· üblich {quantity}", en: "· usually {quantity}" },
    "entry.unexpected": { de: "· unerwartet", en: "· unexpected" },
    "entry.remove": { de: "entfernen", en: "remove" },
    "entry.add": { de: "+ dazu", en: "+ add" },
    "entry.add_to": { de: "bisher {base} +", en: "so far {base} +" },
    "entry.add_sum": {
        de: "bisher {base} + {addend} = {sum}",
        en: "so far {base} + {addend} = {sum}",
    },
    "entry.subtract": { de: "− ab", en: "− off" },
    "entry.subtract_from": { de: "bisher {base} −", en: "so far {base} −" },
    "entry.subtract_sum": {
        de: "bisher {base} − {addend} = {sum}",
        en: "so far {base} − {addend} = {sum}",
    },
    "entry.keep": { de: "behalten", en: "keep" },
    "entry.another_pack": { de: "Weiteres Gebinde:", en: "Another pack:" },
    "entry.not_weighed": { de: "Hier nicht gewogen:", en: "Not weighed here:" },
    "entry.add_weighing": { de: "+ Wiegen", en: "+ Weigh" },
    "entry.weighing": { de: "Wiegen", en: "Weighing" },
    "entry.weighing_unexpected": { de: "hier nicht erwartet", en: "not expected here" },
    "entry.container": { de: "Gefäß", en: "Container" },
    "entry.choose_container": { de: "Gefäß wählen …", en: "Choose a container …" },
    "entry.container_first": { de: "Erst Gefäß wählen", en: "Choose a container first" },
    "entry.gross": { de: "Brutto in g", en: "Gross in g" },
    "entry.saved": {
        de: "gespeichert: {quantity} {unit}",
        en: "saved: {quantity} {unit}",
    },
    "entry.another_weighing": { de: "+ Weitere Wiegung", en: "+ Another weighing" },
    "entry.tare_note": {
        de: "Jedes Gefäß einzeln: von jeder Wiegung geht seine eigene Tara ab.",
        en: "One line per container: each weighing has its own tare deducted.",
    },
    "entry.count": { de: "Anzahl", en: "Quantity" },
    "entry.in_unit": { de: "in {unit}", en: "in {unit}" },
    "entry.so_far": {
        de: "Bisher gespeichert: {quantity} {unit}",
        en: "Saved so far: {quantity} {unit}",
    },
    "entry.previous": { de: "‹ Voriger", en: "‹ Previous" },
    "entry.next": { de: "Nächster ›", en: "Next ›" },
    "entry.done": { de: "Fertig", en: "Done" },

    // -- Units and packs --------------------------------------------------
    // The short form of the base unit. The API sends only the id for it,
    // see 'services/units.js'.
    "unit.piece": { de: "Stk.", en: "pcs" },
    "unit.gram": { de: "g", en: "g" },
    "unit.kilogram": { de: "kg", en: "kg" },
    "unit.millilitre": { de: "ml", en: "ml" },
    "unit.litre": { de: "l", en: "l" },
    "unit.portion": { de: "Portion", en: "portion" },
    // The loose single unit. Every other pack name comes from the catalog.
    "pack.single": { de: "Einzeln", en: "Single" },

    // -- From the services ------------------------------------------------
    "container.article": { de: "Für diesen Artikel", en: "For this article" },
    "container.house": { de: "In diesem Haus", en: "At this site" },
    "container.elsewhere": { de: "Aus anderen Häusern", en: "From other sites" },
    "error.sync": { de: "Der Abgleich ist gescheitert.", en: "The sync failed." },
    "error.numbers_only": { de: "Bitte nur Zahlen eintragen.", en: "Please enter numbers only." },
    "error.decimals": {
        de: "Höchstens drei Nachkommastellen.",
        en: "At most three decimal places.",
    },
    "error.too_large": {
        de: "Das ist mehr, als ein Ergebnis fasst. Bitte die Zahl prüfen.",
        en: "That is more than a result can hold. Please check the number.",
    },
    "error.below_zero": {
        de: "Eine Menge kann nicht unter null liegen. Bitte die Zahl prüfen.",
        en: "A quantity cannot be below zero. Please check the number.",
    },
    "error.failed": { de: "Das ging nicht: {reason}", en: "That did not work: {reason}" },
    "error.server": { de: "Der Server hat nicht geantwortet.", en: "The server did not answer." },
    "error.gone": {
        de: "Diese Inventur ist gerade nicht in der Zählung: abgeschlossen, gelöscht oder zurück in der Vorbereitung.",
        en: "This inventory is not being counted right now: closed, deleted or back in preparation.",
    },
    "error.on_hold": {
        de: "Diese Inventur steht gerade nicht in der Zählung. Deine Zahlen warten hier und gehen hinaus, sobald wieder gezählt wird.",
        en: "This inventory is not being counted right now. Your numbers wait here and go out as soon as counting resumes.",
    },
    "error.no_container": {
        de: "Bitte zu jeder Wiegung das Gefäß wählen.",
        en: "Please choose a container for every weighing.",
    },
    "error.below_tare": {
        de: "{gross} g ist leichter als das leere Gefäß „{container}“ ({tare} g). Falsches Gefäß oder vertippt? War nichts zu wiegen, bitte 0 eintragen.",
        en: "{gross} g is lighter than the empty container “{container}” ({tare} g). Wrong container or a typo? If there was nothing to weigh, enter 0.",
    },
    "error.no_row": {
        de: "Die Zeile ist noch nicht angelegt.",
        en: "The line has not been created yet.",
    },
    "error.signed_out_meanwhile": {
        de: "Du wurdest inzwischen abgemeldet.",
        en: "You have been signed out in the meantime.",
    },
    // The lock of the sign-in. A wrong password counts the same in every form
    // that asks for one, and the lock holds for the sign-in too.
    "error.locked": {
        de: "Zu viele Fehlversuche. Dieses Konto ist von hier aus vorübergehend gesperrt. Bitte versuche es später noch einmal.",
        en: "Too many failed attempts. This account is temporarily locked from here. Please try again later.",
    },

    // -- Demo -------------------------------------------------------------
    "demo.banner": {
        de: "Demo · alle Daten sind erfunden",
        en: "Demo · all data is made up",
    },

    // -- Headings of the header ------------------------------------------
    "title.counting": { de: "Zählen", en: "Counting" },
    "title.articles": { de: "Artikel", en: "Articles" },
    "title.areas": { de: "Bereiche", en: "Areas" },
    "title.inventories": { de: "Inventuren", en: "Inventories" },
    "title.review": { de: "Prüfliste", en: "Review list" },
};
