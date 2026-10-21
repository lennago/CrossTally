/**
 * The mail behind "Report a problem or suggestion" in the account menu.
 *
 * Whoever reports a problem rarely knows the version, and "it does not work"
 * says little without the page. So the mail comes prefilled with both, along
 * with the account, the time, the browser, the connection and what still
 * waits on the device. Only the device knows the last two, which is why the
 * app builds the mail itself and the server only names the address
 * ('rueckmeldeadresse'). The person sends it from their own mail program and
 * sees everything before. Nothing is stored.
 *
 * The website builds the same mail ('server/django_project/feedback.py').
 */

import { locale, t } from "./language.svelte.js";
import { APP_VERSION } from "./version.svelte.js";

//: The start of every subject, in every language and whatever the application
//: is called in the appearance. The recipient's mail filter holds on to it.
export const SUBJECT_TAG = "[CrossTally]";

/**
 * The 'mailto:' address. RFC 6068: line breaks as CRLF, and in subject and
 * body every mark encoded that the RFC does not allow there.
 */
export function feedbackLink({ address, username, online, waiting, now = new Date() }) {
    const details = [
        [t("feedback_mail.version"), APP_VERSION],
        [t("feedback_mail.page"), location.href],
        [t("feedback_mail.account"), username ?? ""],
        [
            t("feedback_mail.time"),
            now.toLocaleString(locale(), { dateStyle: "medium", timeStyle: "short" }),
        ],
        [t("feedback_mail.browser"), navigator.userAgent],
        [
            t("feedback_mail.connection"),
            online ? t("feedback_mail.online") : t("feedback_mail.offline"),
        ],
        [t("feedback_mail.waiting"), String(waiting)],
    ];
    const body = [
        t("feedback_mail.question"),
        "",
        "",
        "",
        t("feedback_mail.details"),
        ...details.map(([label, value]) => `${label}: ${value}`),
    ].join("\r\n");
    const subject = `${SUBJECT_TAG} ${t("feedback_mail.subject")}`;
    const to = encodeURIComponent(address).replace("%40", "@");
    return `mailto:${to}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}
