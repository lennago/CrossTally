/**
 * The app's language: German or English.
 *
 * It hangs **on the account** and thus applies everywhere, like the
 * appearance: on the website, in the administration and here. Written via
 * 'PATCH /ich/', read it comes with every response from '/ich/'.
 *
 * In the browser it additionally lies under 'language'. From there it
 * applies at startup, before the first response is there, and it applies
 * **offline**: in storage there is no network, and the app should still
 * stand in the familiar language.
 *
 * What is translated here are the app's texts. Article, house and location
 * names come from the catalog and stay as they are entered.
 */

import { accountSetting } from "./account_setting.svelte.js";
import { TEXTS } from "../texts.js";

export const LANGUAGES = [
    { value: "de", text: "Deutsch" },
    { value: "en", text: "English" },
];

//: How numbers and times are written in the language ('Intl').
const LOCALES = { de: "de-DE", en: "en-GB" };

/** The locale for the chosen language, for 'Intl' and 'toLocaleString'. */
export function locale() {
    return LOCALES[language.choice] ?? LOCALES.de;
}

function isValid(choice) {
    return LANGUAGES.some((entry) => entry.value === choice);
}

const setting = accountSetting({
    key: "language",
    field: "sprache",
    fallback: "de",
    isValid,
    applied(choice) {
        // For screen readers and the browser's hyphenation.
        document.documentElement.lang = choice;
    },
});

export const language = setting.state;
/** Sets the language and remembers it in the browser. It does not go to the account. */
export const apply = setting.apply;
/** Adopts what is on the account, unless an own choice still waits. */
export const fromAccount = setting.fromAccount;
/** The choice in the menu: apply at once, then to the account. */
export const choose = setting.choose;
/** Send out next time what was chosen without network. */
export const submitPending = setting.submitPending;

/**
 * The text for a key, in the chosen language.
 *
 * If the translation is missing, the German version applies: every text
 * arises there first. If the key is missing entirely, it comes back
 * itself. That shows up in the interface at once, and a test additionally
 * pins it down.
 *
 * Placeholders stand in curly braces: t("list.open", { n: 3 }). If an
 * entry carries singular and plural, 'n' decides.
 */
export function t(key, values) {
    const entry = TEXTS[key];
    if (!entry) return key;
    const version = entry[language.choice] ?? entry.de;
    // An entry with singular and plural decides by 'n'. Both languages
    // distinguish at exactly one, that suffices here.
    const text =
        typeof version === "string" ? version : values?.n === 1 ? version.one : version.many;
    if (!values) return text;
    return text.replace(/\{(\w+)\}/g, (whole, name) =>
        name in values ? String(values[name]) : whole,
    );
}
