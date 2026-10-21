/**
 * The contract of the translation.
 *
 * The most important case is the third: every key the code uses has to be
 * in 'texts.js', and every entry needs both languages. Otherwise at some
 * point the key itself or abruptly German stands in the app.
 */

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TEXTS } from "../texts.js";
import { apply, choose, fromAccount, language, submitPending, t } from "./language.svelte.js";
import { db, remember, remembered } from "./storage.js";
import { responds } from "../test_support.js";

/** All source files in which 't(...)' can occur. */
function sources(folder = "src") {
    const found = [];
    for (const entry of readdirSync(folder, { withFileTypes: true })) {
        const path = join(folder, entry.name);
        if (entry.isDirectory()) found.push(...sources(path));
        else if (/\.(js|svelte)$/.test(entry.name) && !entry.name.endsWith(".test.js")) {
            found.push(path);
        }
    }
    return found;
}

describe("t", () => {
    beforeEach(() => apply("de"));

    it("returns the German text", () => {
        expect(t("header.back")).toBe("Zurück");
    });

    it("follows the chosen language", () => {
        apply("en");
        expect(t("header.back")).toBe("Back");
        expect(language.choice).toBe("en");
    });

    it("fills in placeholders", () => {
        expect(t("list.open", { n: 3 })).toBe("3 offen");
    });

    it("distinguishes singular and plural", () => {
        expect(t("account.waiting", { n: 1 })).toContain("1 Eingabe ist");
        expect(t("account.waiting", { n: 2 })).toContain("2 Eingaben sind");
    });

    it("returns the key when it does not exist", () => {
        expect(t("gibt.es.nicht")).toBe("gibt.es.nicht");
    });

    it("does not accept an unknown language", () => {
        apply("kl");
        expect(language.choice).toBe("de");
    });
});

describe("texts.js", () => {
    it("lists every entry in both languages", () => {
        for (const [key, entry] of Object.entries(TEXTS)) {
            expect(entry.de, `${key}: deutsch fehlt`).toBeTruthy();
            expect(entry.en, `${key}: englisch fehlt`).toBeTruthy();
            // Plural forms exist either in both languages or in none.
            expect(typeof entry.de, `${key}: Bauart weicht ab`).toBe(typeof entry.en);
        }
    });

    it("knows every key the code uses", () => {
        const used = new Set();
        for (const path of sources()) {
            const content = readFileSync(path, "utf-8");
            for (const [, key] of content.matchAll(/\bt\(\s*"([a-z_]+\.[a-z_]+)"/g)) {
                used.add(key);
            }
        }
        // Otherwise the test would check nothing because it found nothing.
        expect(used.size).toBeGreaterThan(40);
        const missing = [...used].filter((key) => !(key in TEXTS));
        expect(missing).toEqual([]);
    });

    it("has no fixed text in a template", () => {
        // What stands between the tags and not in curly braces the person sees
        // as it stands, in every language. Three such bars in 'App.svelte' stood
        // in German on English until 09/2026.
        const found = [];
        for (const path of sources().filter((path) => path.endsWith(".svelte"))) {
            let content = readFileSync(path, "utf-8")
                .replace(/<script\b[\s\S]*?<\/script>/g, "")
                .replace(/<style\b[\s\S]*?<\/style>/g, "")
                .replace(/<!--[\s\S]*?-->/g, "");
            // Expressions in braces, nested ones too, are code.
            for (let depth = 0; depth < 4; depth += 1)
                content = content.replace(/\{[^{}]*\}/g, " ");
            content = content.replace(/<[^>]*>/g, "\n");
            for (const row of content.split("\n")) {
                const text = row.trim();
                if (/[A-Za-zÄÖÜäöüß]{2,}/.test(text)) found.push(`${path}: ${text}`);
            }
        }
        expect(found).toEqual([]);
    });

    it("has no German sentence in a service", () => {
        // Services return their messages via 't()'. A sentence with an umlaut
        // or "Bitte" in a string otherwise lands literally in the app.
        const found = [];
        for (const path of sources("src/services").filter((path) => path.endsWith(".js"))) {
            for (const [number, row] of readFileSync(path, "utf-8").split("\n").entries()) {
                if (/^\s*(\/\/|\*|\/\*)/.test(row) || /\b(Error|super)\(/.test(row)) continue;
                for (const [, text] of row.matchAll(/"([^"]*)"/g)) {
                    if (/[äöüÄÖÜß]|\bBitte\b/.test(text))
                        found.push(`${path}:${number + 1}: ${text}`);
                }
            }
        }
        expect(found).toEqual([]);
    });

    it("has no entry nobody uses", () => {
        const content = sources()
            .map((path) => readFileSync(path, "utf-8"))
            .join("\n");
        const unused = Object.keys(TEXTS).filter((key) => !content.includes(`"${key}"`));
        expect(unused).toEqual([]);
    });
});

describe("the choice on the account", () => {
    beforeEach(async () => {
        localStorage.clear();
        await db.state.clear();
        await remember("access_token", "a");
        apply("de");
    });

    afterEach(() => {
        apply("de");
        globalThis.fetch = () => Promise.reject(new TypeError("kein Netz im Test"));
    });

    it("sends the choice to the account", async () => {
        const network = responds({ id: 7, sprache: "en" });
        globalThis.fetch = network;

        await choose("en");

        const [url, options] = network.mock.calls[0];
        expect(url).toBe("/api/v1/ich/");
        expect(options.method).toBe("PATCH");
        expect(JSON.parse(options.body)).toEqual({ sprache: "en" });
        expect((await remembered("me")).sprache).toBe("en");
        expect(localStorage.getItem("language_pending")).toBeNull();
    });

    it("without network the choice applies at once and is not overruled by the server state", async () => {
        globalThis.fetch = () => Promise.reject(new TypeError("kein Netz"));

        await choose("en");
        expect(language.choice).toBe("en");
        expect(localStorage.getItem("language_pending")).toBe("en");

        // The sync brings the old state from the server. The own choice stays.
        fromAccount({ id: 7, sprache: "de" });
        expect(language.choice).toBe("en");

        // Network again: it goes out and no longer waits.
        globalThis.fetch = responds({ id: 7, sprache: "en" });
        await submitPending();
        expect(localStorage.getItem("language_pending")).toBeNull();
    });
});

/** Before any account answers, the page stands in the last choice, the login form too (10/2026). */
it("marks the page in the remembered language from the start", async () => {
    localStorage.setItem("language", "en");
    document.documentElement.lang = "de";
    try {
        vi.resetModules();
        const fresh = await import("./language.svelte.js");
        expect(fresh.language.choice).toBe("en");
        expect(document.documentElement.lang).toBe("en");
    } finally {
        localStorage.removeItem("language");
        vi.resetModules();
        document.documentElement.lang = "de";
    }
});
