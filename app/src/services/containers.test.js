import { describe, expect, it } from "vitest";

import { containerGroups, containers } from "./containers.js";
import { t } from "./language.svelte.js";

const WITHOUT = { id: 1, name: "Ohne Behälter", tara: "0.000", haus: "" };
const BOWL = { id: 2, name: "Schale", tara: "250.000", haus: "" };
const BUCKET = { id: 3, name: "Eimer", tara: "300.000", haus: "dieses" };
const TUB = { id: 4, name: "Wanne", tara: "900.000", haus: "anderes" };
const BOTTLE = { id: 5, name: "Druckflasche", tara: "120.000", haus: "" };
const ALL = [WITHOUT, BOWL, BUCKET, TUB, BOTTLE];

describe("containerGroups", () => {
    it("offers first what weighs nothing, then the containers of the article, the house and other houses", () => {
        const groups = containerGroups({ behaelter: [2, 1] }, ALL);
        expect(groups.map((g) => [g.key, g.containers.map((b) => b.name)])).toEqual([
            ["without_tare", ["Ohne Behälter"]],
            ["article", ["Schale"]],
            ["house", ["Eimer"]],
            ["elsewhere", ["Wanne"]],
        ]);
        // At the very top without heading, the others with. The heading is a
        // key, translated only when displaying ('texts.js').
        expect(groups.map((g) => g.title)).toEqual([
            "",
            "container.article",
            "container.house",
            "container.elsewhere",
        ]);
        expect(groups.slice(1).map((g) => t(g.title))).toEqual([
            "Für diesen Artikel",
            "In diesem Haus",
            "Aus anderen Häusern",
        ]);
    });

    it("also brings a vessel without tare from the house to the top", () => {
        const lid = { id: 6, name: "Deckel", tara: "0", haus: "dieses" };
        const [top] = containerGroups({ behaelter: [2, 1] }, [...ALL, lid]);
        expect(top.containers.map((b) => b.name)).toEqual(["Ohne Behälter", "Deckel"]);
    });

    it("leaves out a container without house at foreign articles", () => {
        const names = containers({ behaelter: [1] }, ALL).map((b) => b.name);
        expect(names).toEqual(["Ohne Behälter", "Eimer", "Wanne"]);
        expect(names).not.toContain("Druckflasche");
    });

    it("shows it at the front at its own article", () => {
        const [, onArticle] = containerGroups({ behaelter: [5, 1] }, ALL);
        expect(onArticle.containers.map((b) => b.name)).toEqual(["Druckflasche"]);
    });

    it("names an assigned house container only once, at the front", () => {
        const groups = containerGroups({ behaelter: [3, 1] }, ALL);
        expect(groups.map((g) => g.key)).toEqual(["without_tare", "article", "elsewhere"]);
        expect(containers({ behaelter: [3, 1] }, ALL).filter((b) => b.id === 3)).toHaveLength(1);
    });

    it("copes with older master data without the house information", () => {
        const old = ALL.map(({ haus, ...rest }) => rest);
        expect(containerGroups({ behaelter: [2, 1] }, old).map((g) => g.key)).toEqual([
            "without_tare",
            "article",
        ]);
        expect(containerGroups(null, undefined)).toEqual([]);
    });
});
