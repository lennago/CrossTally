/**
 * Quantities as the app's language writes them.
 *
 * The API sends decimals as text with a point and all decimal places
 * ('36.000'). Displayed unfiltered that reads as thirty-six thousand in
 * German, and '1.000' for a single piece is simply wrong. That is why no
 * number goes into the interface unfiltered.
 *
 * Without thousands separator: the text comes back into the input field as
 * a preset, and "1.250" would become a quarter more than one on the way back.
 */

import { locale } from "./language.svelte.js";

function formatter() {
    return new Intl.NumberFormat(locale(), {
        maximumFractionDigits: 3,
        useGrouping: false,
    });
}

export function formatQuantity(value) {
    if (value === null || value === undefined || value === "") return "";
    const number = Number(value);
    // What is no number stays as it came: better a strange text than a
    // silent "NaN".
    return Number.isNaN(number) ? String(value) : formatter().format(number);
}

/** Sums what stands at one place. Missing counts as nothing. */
export function sum(values) {
    return values.reduce((total, value) => total + (Number(value) || 0), 0);
}

//: Plain decimal notation with a point: a sign, digits, at most one point.
const DECIMAL = /^[+-]?(\d+\.?\d*|\.\d+)$/;

/**
 * What someone typed, as decimal text for the API.
 *
 * Two traps at once: in German one types `1,5`, and an
 * `<input type="number">` delivers a **number** via `bind:value` instead of
 * a text. `.trim()` on that throws. That is why no input goes to the API
 * without this detour. The fields are text fields with `inputmode="decimal"`
 * for the same reason: with `type="number"` the browser discards the comma.
 *
 * Only plain decimals, the notation every field of the server takes.
 * JavaScript also reads "1e-5", "0x10" or "Infinity" as numbers, and the
 * checks here let them pass. The server refuses them, and until 10/2026 a
 * count typed so was refused only once it went out.
 */
export function asNumberText(value) {
    const text = String(value ?? "")
        .trim()
        .replace(",", ".");
    if (text === "") return null;
    return DECIMAL.test(text) ? text : undefined;
}

/**
 * Two quantities added, as decimal text for the API.
 *
 * Computed in thousandths, the precision of the input on the server. As a
 * floating point number 0.1 + 0.2 would otherwise give 0.30000000000000004.
 * Empty counts as nothing, if both are empty it stays empty.
 */
export function addQuantities(left, right) {
    if ((left ?? "") === "" && (right ?? "") === "") return null;
    return String((thousandths(left) + thousandths(right)) / 1000);
}

/**
 * The second quantity taken off the first, as decimal text for the API.
 *
 * The result may fall below zero: whoever takes off more than stands there
 * has mistyped, and the form should say so instead of silently making a
 * zero of it ('isBelowZero').
 */
export function subtractQuantities(left, right) {
    if ((left ?? "") === "" && (right ?? "") === "") return null;
    return String((thousandths(left) - thousandths(right)) / 1000);
}

/**
 * A quantity times a factor, as decimal text with three places.
 *
 * In thousandths like the sums above, rounded as the server rounds: half up.
 */
export function multiplyQuantities(quantity, factor) {
    const product = thousandths(quantity) * thousandths(factor);
    return (Math.round(product / 1000) / 1000).toFixed(3);
}

function thousandths(value) {
    return Math.round((Number(value) || 0) * 1000);
}

/** Whether a typed quantity carries more than the three places the server stores. */
export function hasTooManyDecimals(value) {
    const text = asNumberText(value);
    if (text == null) return false;
    const [, decimals = ""] = text.split(".");
    return decimals.length > 3;
}

/**
 * What a number from here may reach at most, exclusive.
 *
 * The server's fields hold nine places before the comma, and a result holds
 * no more ('HOECHSTMENGE' in 'katalog/formats/base.py'). Beyond it the server
 * refuses, but only once the form is left.
 */
export const LIMIT = 1e9;

/** Whether a typed number is beyond what the server holds ('LIMIT'). */
export function isTooLarge(value) {
    const text = asNumberText(value);
    return text !== null && text !== undefined && Math.abs(Number(text)) >= LIMIT;
}

/** Whether a typed quantity is a number below zero. No shelf holds that. */
export function isBelowZero(value) {
    const text = asNumberText(value);
    return text !== null && text !== undefined && Number(text) < 0;
}
