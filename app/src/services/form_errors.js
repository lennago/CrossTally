/**
 * What the server refused about a form, sorted the way the dialogs show it.
 *
 * Three dialogs send a form: the password, the address and a new passkey.
 * Until 10/2026 each sorted the refusal itself, in the same order. First a
 * sign-in that no longer applies, then what the server says about a field,
 * then one line for the rest. The order lives here now. What the line says
 * for a status stays each dialog's own, because the same status can mean
 * another thing: only the address sends a mail it cannot do without (503).
 * The lock (429) is the exception. It is the sign-in's in every form, and
 * all of them name it with 'error.locked'.
 */

/** What the server says about a field, as a list of sentences. Anything else says nothing. */
export function sentences(entries) {
    return Array.isArray(entries) ? entries.map((entry) => String(entry).trim()) : [];
}

/**
 * The cause of a failed request, sorted for the dialog.
 *
 * 'signedOut': the sign-in no longer applies, and the login form is the way
 * on. Otherwise 'errors' holds the sentences per field of the server
 * ('fields'), and 'line' what stands below the form: nothing where a field
 * has a sentence, else the dialog's text for the status ('lines', 0 is no
 * network), else 'otherwise'.
 *
 * 'reveal' names the fields with a sentence that the dialog does not show
 * ('shown', all of 'fields' unless it says otherwise). Their sentence would
 * stand nowhere, so the dialog shows them from then on. That happens when
 * the device thought the account had no password and the server wants one
 * after all. Until 10/2026 nothing happened that one could see.
 */
export function formErrors(
    cause,
    { fields = [], shown = fields, lines = {}, otherwise = "" } = {},
) {
    const errors = Object.fromEntries(
        fields.map((field) => [field, sentences(cause?.data?.[field])]),
    );
    if (cause?.isSignedOut) return { signedOut: true, errors, reveal: [], line: "" };
    const reveal = fields.filter((field) => errors[field].length > 0 && !shown.includes(field));
    const atField = Object.values(errors).some((list) => list.length > 0);
    return {
        signedOut: false,
        errors,
        reveal,
        line: atField ? "" : (lines[cause?.status] ?? otherwise),
    };
}
