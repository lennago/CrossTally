/**
 * The keyboard's holder: it takes the focus in the tap and gives it up
 * again if no form takes it over. Otherwise a keyboard without field would stand there.
 */

import { afterEach, expect, it, vi } from "vitest";

import { setHolder, holdKeyboard } from "./keyboard.js";

afterEach(() => {
    vi.useRealTimers();
    document.body.replaceChildren();
    setHolder(null);
});

it("gives the focus up after a short time when no form takes it over", () => {
    vi.useFakeTimers();
    const holder = document.createElement("input");
    document.body.append(holder);
    setHolder(holder);

    holdKeyboard();
    expect(document.activeElement).toBe(holder);

    vi.advanceTimersByTime(1500);
    expect(document.activeElement).not.toBe(holder);
});

it("leaves the focus with a field that took it over", () => {
    vi.useFakeTimers();
    const holder = document.createElement("input");
    const field = document.createElement("input");
    document.body.append(holder, field);
    setHolder(holder);

    holdKeyboard();
    field.focus();
    vi.advanceTimersByTime(1500);

    expect(document.activeElement).toBe(field);
});

it("does nothing without a holder", () => {
    expect(() => holdKeyboard()).not.toThrow();
});
