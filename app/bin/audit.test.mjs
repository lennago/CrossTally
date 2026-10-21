import { expect, it } from "vitest";

import { vulnerabilities } from "./audit.mjs";

const advisory = {
    source: 1,
    name: "paket-a",
    title: "Prototype Pollution",
    url: "https://github.com/advisories/GHSA-0000",
    severity: "high",
};

it("reports a vulnerability in a shipped package", () => {
    const report = { vulnerabilities: { "paket-a": { name: "paket-a", via: [advisory] } } };
    expect(vulnerabilities(report, ["paket-a"])).toEqual([
        "paket-a (high): Prototype Pollution https://github.com/advisories/GHSA-0000",
    ]);
});

it("skips tools that are not in the app", () => {
    const report = { vulnerabilities: { "paket-a": { name: "paket-a", via: [advisory] } } };
    expect(vulnerabilities(report, ["paket-b"])).toEqual([]);
});

it("skips a package that merely depends on a vulnerable one", () => {
    // This is how Svelte would appear if its compiler had a vulnerability.
    const report = {
        vulnerabilities: {
            "paket-a": { name: "paket-a", via: [advisory] },
            "paket-b": { name: "paket-b", via: ["paket-a"] },
        },
    };
    expect(vulnerabilities(report, ["paket-b"])).toEqual([]);
});

it("copes with a report without findings", () => {
    expect(vulnerabilities({ vulnerabilities: {} }, ["paket-a"])).toEqual([]);
});

it("skips Vite, whose advisories concern the development server", () => {
    const report = {
        vulnerabilities: { vite: { name: "vite", via: [{ ...advisory, name: "vite" }] } },
    };
    expect(vulnerabilities(report, ["vite"])).toEqual([]);
});
