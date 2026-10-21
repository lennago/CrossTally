/**
 * The search window for articles that have no row at the location yet.
 *
 * Checked is what someone has to read from it: which hit is an article the
 * house no longer carries at all.
 */

import { flushSync, mount, unmount } from "svelte";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import ArtikelSearch from "./components/ArticleSearch.svelte";

const ALL = [
    { id: 1, name: "Artikel C 0,33", einheit: "STUECK", aktiv: true },
    { id: 2, name: "Artikel C Kirsch", einheit: "STUECK", aktiv: false },
    { id: 3, name: "Artikel C Vanille", einheit: "STUECK", aktiv: false, eingestellt: true },
];

let target;

beforeEach(() => {
    target = document.createElement("div");
    document.body.append(target);
});

afterEach(() => target.remove());

function open(overrides = {}) {
    return mount(ArtikelSearch, {
        target: target,
        props: {
            existing: [],
            all: ALL,
            choose: () => {},
            close: () => {},
            ...overrides,
        },
    });
}

it("marks discontinued articles and puts them last", () => {
    const dialog = open();
    const hits = [...target.querySelectorAll(".hit")];

    expect(hits.map((row) => row.querySelector("strong").textContent)).toEqual([
        "Artikel C 0,33",
        "Artikel C Kirsch",
        "Artikel C Vanille",
    ]);
    expect(hits[1].textContent).toContain("inaktiv");
    // "discontinued" instead of "inactive", not both. 'aktiv' is wrong for it too.
    expect(hits[2].textContent).toContain("eingestellt");
    expect(hits[2].textContent).not.toContain("inaktiv");
    unmount(dialog);
});

/**
 * The window exists to type into. Svelte's 'autofocus' yields to whatever
 * holds the focus, and Chromium focuses the "+" that opens the window: until
 * 10/2026 the field stayed without the focus there, on Android too.
 */
it("takes the focus into its field, also from the button that opened it", async () => {
    const opener = document.createElement("button");
    document.body.append(opener);
    opener.focus();

    const dialog = open();

    await vi.waitFor(() =>
        expect(document.activeElement).toBe(target.querySelector("input[type=search]")),
    );
    unmount(dialog);
    opener.remove();
});

it("passes the chosen article on", () => {
    const choose = vi.fn();
    const dialog = open({ choose });
    target.querySelectorAll(".hit")[2].click();
    flushSync();
    expect(choose).toHaveBeenCalledWith(expect.objectContaining({ id: 3 }));
    unmount(dialog);
});
