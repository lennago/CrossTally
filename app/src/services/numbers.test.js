import { afterEach, expect, it } from "vitest";

import { language } from "./language.svelte.js";
import {
    addQuantities,
    asNumberText,
    formatQuantity,
    hasTooManyDecimals,
    isBelowZero,
    isTooLarge,
    multiplyQuantities,
    subtractQuantities,
    sum,
} from "./numbers.js";

afterEach(() => {
    language.choice = "de";
});

it("writes quantities in German and without trailing zeros", () => {
    expect(formatQuantity("36.000")).toBe("36");
    // The case that stood out in the app: '1.000' is one piece, not a thousand.
    expect(formatQuantity("1.000")).toBe("1");
    expect(formatQuantity("0.140")).toBe("0,14");
    // Without thousands separator: the text goes back into the field as a preset.
    expect(formatQuantity("1250.000")).toBe("1250");
});

it("writes quantities in English when the app is English", () => {
    language.choice = "en";
    expect(formatQuantity("0.140")).toBe("0.14");
    expect(formatQuantity("1250.000")).toBe("1250");
});

it("leaves empty things empty and unreadable things as they are", () => {
    expect(formatQuantity(null)).toBe("");
    expect(formatQuantity("")).toBe("");
    expect(formatQuantity("viel")).toBe("viel");
});

it("sums across missing values", () => {
    expect(sum(["1.500", null, "2"])).toBe(3.5);
    expect(sum([])).toBe(0);
});

it("takes comma like point and reports nonsense", () => {
    expect(asNumberText("1,5")).toBe("1.5");
    expect(asNumberText("1.5")).toBe("1.5");
    // A number field delivers a number via 'bind:value', not a text.
    expect(asNumberText(1250)).toBe("1250");
    expect(asNumberText("  7 ")).toBe("7");
});

it("distinguishes empty from wrong", () => {
    expect(asNumberText("")).toBeNull();
    expect(asNumberText(null)).toBeNull();
    expect(asNumberText("drei")).toBeUndefined();
});

/**
 * JavaScript reads more as a number than the server takes. Until 10/2026
 * such a count passed every check here and was refused once it went out.
 */
it("takes only the plain decimals the server takes", () => {
    for (const typed of [
        "1e5",
        "1e-5",
        "0x10",
        "0b11",
        "Infinity",
        "-Infinity",
        "1.000,5",
        "--3",
    ]) {
        expect(asNumberText(typed), typed).toBeUndefined();
        expect(hasTooManyDecimals(typed), typed).toBe(false);
    }
    expect(asNumberText("-3")).toBe("-3");
    expect(asNumberText("+3")).toBe("+3");
    expect(asNumberText(",5")).toBe(".5");
    expect(asNumberText("5,")).toBe("5.");
    expect(asNumberText(0.25)).toBe("0.25");
});

it("adds two quantities without floating point rounding errors", () => {
    expect(addQuantities("5.000", "3")).toBe("8");
    expect(addQuantities("0.1", "0.2")).toBe("0.3");
    expect(addQuantities("2.5", "1.125")).toBe("3.625");
});

it("counts empty as nothing and leaves twice empty empty", () => {
    expect(addQuantities(null, "3")).toBe("3");
    expect(addQuantities("4", "")).toBe("4");
    expect(addQuantities(null, null)).toBeNull();
});

it("takes one quantity off another without floating point rounding errors", () => {
    expect(subtractQuantities("12.000", "3")).toBe("9");
    expect(subtractQuantities("0.3", "0.1")).toBe("0.2");
    expect(subtractQuantities("3.625", "1.125")).toBe("2.5");
});

it("takes nothing off when nothing is typed, and leaves twice empty empty", () => {
    expect(subtractQuantities("4", "")).toBe("4");
    expect(subtractQuantities("4", null)).toBe("4");
    expect(subtractQuantities(null, null)).toBeNull();
});

it("lets the difference fall below zero so the form can say so", () => {
    expect(subtractQuantities("12", "15")).toBe("-3");
    expect(isBelowZero("-3")).toBe(true);
    expect(isBelowZero("-0,5")).toBe(true);
});

it("takes nothing but a number below zero for one", () => {
    for (const value of ["0", "0,5", "12", "", null, undefined, "viel"]) {
        expect(isBelowZero(value), String(value)).toBe(false);
    }
});

it("multiplies a quantity by a pack factor in thousandths, half up", () => {
    expect(multiplyQuantities("3", "20.000")).toBe("60.000");
    expect(multiplyQuantities("1.5", "0.125")).toBe("0.188");
    expect(multiplyQuantities("0.1", "0.2")).toBe("0.020");
});

/** Nine places before the comma, like the server's fields and a result. */
it("knows a number beyond what the server holds", () => {
    expect(isTooLarge("999999999,999")).toBe(false);
    expect(isTooLarge("1000000000")).toBe(true);
    expect(isTooLarge("-1000000000")).toBe(true);
    expect(isTooLarge("12345678901234")).toBe(true);
    for (const value of ["", null, "drei", "0"]) {
        expect(isTooLarge(value), String(value)).toBe(false);
    }
});

it("knows a fourth decimal place", () => {
    expect(hasTooManyDecimals("1.2345")).toBe(true);
    expect(hasTooManyDecimals("1,234")).toBe(false);
    expect(hasTooManyDecimals("")).toBe(false);
    expect(hasTooManyDecimals(null)).toBe(false);
});
