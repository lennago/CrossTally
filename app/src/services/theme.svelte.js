/**
 * Light, dark or like the device.
 *
 * The choice hangs **on the account** and thus applies everywhere: on the
 * website, in the administration and here, on a second device too and in
 * the installed app on iOS, which has its own storage. Written via
 * 'PATCH /ich/', read it comes with every response from '/ich/'.
 *
 * In the browser it additionally lies under 'theme', the same key website
 * and Django admin use. From there 'index.html' reads it before the page is
 * painted the first time, otherwise the light version would briefly flash
 * on a dark device at startup.
 *
 * Without network the choice applies at once, and it waits under
 * 'theme_pending' until it can go out. As long as it waits, the state from
 * the server does not overrule it, otherwise the appearance would jump back
 * on the next sync.
 */

import { accountSetting } from "./account_setting.svelte.js";

//: The text stands as a key, translated only when displaying ('texts.js').
//: Here nobody knows which language currently applies.
export const CHOICES = [
    { value: "auto", key: "appearance.auto" },
    { value: "light", key: "appearance.light" },
    { value: "dark", key: "appearance.dark" },
];

function isValid(choice) {
    return CHOICES.some((entry) => entry.value === choice);
}

function deviceIsDark() {
    return Boolean(window.matchMedia?.("(prefers-color-scheme: dark)").matches);
}

const setting = accountSetting({
    key: "theme",
    field: "darstellung",
    fallback: "auto",
    isValid,
    applied(choice) {
        const dark = choice === "dark" || (choice === "auto" && deviceIsDark());
        document.documentElement.dataset.theme = dark ? "dark" : "light";
    },
});

export const appearance = setting.state;
/**
 * Sets the page's colours and remembers the choice in the browser. It does
 * not go to the account.
 */
export const apply = setting.apply;
/** Adopts what is on the account, unless an own choice still waits. */
export const fromAccount = setting.fromAccount;
/** The choice in the menu: apply at once, then to the account. */
export const choose = setting.choose;
/** Send out next time what was chosen without network. */
export const submitPending = setting.submitPending;

// "Automatic" also means: follow along while the app is open.
window.matchMedia?.("(prefers-color-scheme: dark)").addEventListener?.("change", () => {
    if (appearance.choice === "auto") apply("auto");
});
