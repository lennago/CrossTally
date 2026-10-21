/**
 * Light or dark: stored on the account, applied at once.
 *
 * Checked above all is the interplay with the network: a choice without
 * network applies at once, waits until it can go out, and is not overruled
 * by the server state meanwhile.
 */

import { beforeEach, expect, it, vi } from "vitest";

import { db, remembered, remember } from "./services/storage.js";
import { apply, appearance, submitPending, fromAccount, choose } from "./services/theme.svelte.js";
import { responds } from "./test_support.js";

beforeEach(async () => {
    localStorage.clear();
    await db.state.clear();
    await remember("access_token", "a");
    delete document.documentElement.dataset.theme;
});

it("dark sets the page's colours and remembers it in the browser", () => {
    apply("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("theme")).toBe("dark");
    expect(appearance.choice).toBe("dark");
});

it("an unknown choice falls back to automatic", () => {
    apply("lila");
    expect(appearance.choice).toBe("auto");
});

it("adopts what is on the account", () => {
    fromAccount({ id: 7, darstellung: "light" });
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(appearance.choice).toBe("light");
});

it("sends the choice to the account", async () => {
    const network = responds({ id: 7, darstellung: "dark" });
    globalThis.fetch = network;

    await choose("dark");

    const [url, options] = network.mock.calls[0];
    expect(url).toBe("/api/v1/ich/");
    expect(options.method).toBe("PATCH");
    expect(JSON.parse(options.body)).toEqual({ darstellung: "dark" });
    expect((await remembered("me")).darstellung).toBe("dark");
    expect(localStorage.getItem("theme_pending")).toBeNull();
});

it("without network the choice applies at once and is not overruled by the server state", async () => {
    globalThis.fetch = () => Promise.reject(new TypeError("kein Netz"));

    await choose("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("theme_pending")).toBe("dark");

    // The sync brings the old state from the server. The own choice stays.
    fromAccount({ id: 7, darstellung: "light" });
    expect(appearance.choice).toBe("dark");

    // Network again: it goes out and no longer waits.
    globalThis.fetch = responds({ id: 7, darstellung: "dark" });
    await submitPending();
    expect(localStorage.getItem("theme_pending")).toBeNull();
});
