import { describe, expect, it } from "vitest";

import {
    articles,
    areas,
    addable,
    inTabs,
    isCounted,
    next,
    locations,
    progress,
    search,
    previous,
    tabs,
} from "./progress.js";

function position(overrides = {}) {
    return {
        id: 1,
        ort: { id: 1, name: "Thekenfront", bereich: "Theke" },
        location_id: 1,
        artikel_id: 7,
        artikel: "Artikel C 0,33",
        einheit: "Stück(e)",
        wird_gewogen: false,
        wiegungen: [],
        eingabemenge: null,
        unerwartet: false,
        ...overrides,
    };
}

describe("isCounted", () => {
    it("recognises an entered count", () => {
        expect(isCounted(position())).toBe(false);
        expect(isCounted(position({ eingabemenge: "0" }))).toBe(true);
    });

    it("asks for the weighings with weighed goods", () => {
        const weighed = position({ wird_gewogen: true, eingabemenge: "5" });
        expect(isCounted(weighed)).toBe(false);
        expect(isCounted({ ...weighed, wiegungen: [{ id: 1 }] })).toBe(true);
    });
});

describe("progress", () => {
    it("distinguishes empty, partial and done", () => {
        expect(progress([]).state).toBe("empty");
        expect(progress([position(), position()]).state).toBe("empty");
        expect(progress([position({ eingabemenge: "1" }), position()]).state).toBe("partial");
        expect(progress([position({ eingabemenge: "1" })]).state).toBe("done");
    });

    it("counts open rows", () => {
        expect(progress([position({ eingabemenge: "1" }), position()]).open).toBe(1);
    });
});

describe("grouping", () => {
    const positions = [
        position({ id: 1 }),
        position({
            id: 2,
            ort: { id: 2, name: "Rückseite", bereich: "Theke" },
            location_id: 2,
        }),
        position({
            id: 3,
            ort: { id: 3, name: "Trockenlager", bereich: "Lager" },
            location_id: 3,
            artikel_id: 9,
            artikel: "Artikel N",
            eingabemenge: "2",
        }),
    ];

    it("groups by area", () => {
        expect(areas(positions).map((entry) => entry.name)).toEqual(["Lager", "Theke"]);
        expect(areas(positions).find((e) => e.name === "Theke").total).toBe(2);
    });

    it("lists the locations of an area", () => {
        expect(locations(positions, "Theke").map((entry) => entry.name)).toEqual([
            "Rückseite",
            "Thekenfront",
        ]);
    });

    describe("with locations nothing is expected at", () => {
        const places = [
            { id: 1, name: "Thekenfront", bereich: "Theke" },
            { id: 4, name: "Vitrine", bereich: "Theke" },
            { id: 5, name: "Büro", bereich: "Verwaltung" },
        ];

        it("names their areas too", () => {
            expect(areas(positions, places).map((entry) => entry.name)).toEqual([
                "Lager",
                "Theke",
                "Verwaltung",
            ]);
        });

        it("counts nothing for an area without rows", () => {
            const office = areas(positions, places).find((entry) => entry.name === "Verwaltung");
            expect(office.total).toBe(0);
            expect(office.open).toBe(0);
            expect(office.rows).toEqual([]);
        });

        it("leaves the numbers of the other areas alone", () => {
            expect(areas(positions, places).find((e) => e.name === "Theke").total).toBe(2);
        });

        it("lists them among the locations of their area", () => {
            const found = locations(positions, "Theke", places);
            expect(found.map((entry) => entry.name)).toEqual([
                "Rückseite",
                "Thekenfront",
                "Vitrine",
            ]);
            expect(found.find((entry) => entry.name === "Vitrine")).toMatchObject({
                id: 4,
                total: 0,
            });
        });

        it("believes the rows where the master data is older", () => {
            // The master data is fetched once. A location that has moved to
            // another area since then stands where its rows say.
            const moved = [{ id: 3, name: "Trockenlager", bereich: "Keller" }];
            expect(areas(positions, moved).map((entry) => entry.name)).toEqual(["Lager", "Theke"]);
            expect(locations(positions, "Keller", moved)).toEqual([]);
        });

        it("shows a house nothing is expected in at all", () => {
            expect(areas([], places).map((entry) => entry.name)).toEqual(["Theke", "Verwaltung"]);
        });
    });

    it("groups all rows of a location per article", () => {
        const two = [position({ id: 1 }), position({ id: 2, eingabemenge: "3" })];
        const [entry] = articles(two, 1);
        expect(entry.total).toBe(2);
        expect(entry.state).toBe("partial");
    });
});

describe("tabs", () => {
    const MONTH = { id: 1, name: "Monatsinventur" };
    const YEAR = { id: 2, name: "Jahresinventur" };
    const CUPS = { id: 3, name: "Becherinventur" };
    const positions = [
        position({
            id: 1,
            artikel_id: 7,
            artikel: "Artikel C 0,33",
            sortierung: 2,
            inventurart: 1,
        }),
        position({ id: 2, artikel_id: 8, artikel: "Mehrwegbecher", sortierung: 1, inventurart: 3 }),
        position({
            id: 3,
            artikel_id: 9,
            artikel: "Posten P",
            sortierung: 3,
            inventurart: 3,
            eingabemenge: "4",
        }),
    ];

    function shown(rows, types) {
        return tabs(inTabs(articles(rows, 1), types), types);
    }

    it("separates the tabs and leaves each the order of the counting list", () => {
        const all = articles(positions, 1);
        expect(all.map((entry) => entry.name)).toEqual([
            "Mehrwegbecher",
            "Artikel C 0,33",
            "Posten P",
        ]);
        const [month, cups] = shown(positions, [MONTH, CUPS]);
        expect(month.entries.map((e) => e.name)).toEqual(["Artikel C 0,33"]);
        expect(cups.entries.map((e) => e.name)).toEqual(["Mehrwegbecher", "Posten P"]);
    });

    it("counts the progress per tab", () => {
        const cups = shown(positions, [MONTH, CUPS])[1];
        expect(cups.counted).toBe(1);
        expect(cups.total).toBe(2);
        expect(cups.state).toBe("partial");
    });

    it("keeps the order of the types", () => {
        expect(shown(positions, [CUPS, MONTH]).map((tab) => tab.name)).toEqual([
            "Becherinventur",
            "Monatsinventur",
        ]);
    });

    it("shows a tab only where its type has rows", () => {
        expect(shown(positions, [MONTH, YEAR, CUPS]).map((tab) => tab.name)).toEqual([
            "Monatsinventur",
            "Becherinventur",
        ]);
    });

    it("puts an article of another type into the first tab with rows", () => {
        // Counted on top, through the search: it opens no tab of its own.
        const cupsOnly = [
            ...positions.slice(1),
            position({ id: 4, artikel_id: 10, artikel: "Ware J", inventurart: 2 }),
        ];
        const [cups, ...more] = shown(cupsOnly, [MONTH, CUPS]);
        expect(more).toEqual([]);
        expect(cups.name).toBe("Becherinventur");
        expect(cups.entries.map((e) => e.name)).toContain("Ware J");
    });

    it("takes rows without the field into the first tab", () => {
        // A storage from before 10/2026 does not know 'inventurart' yet.
        expect(shown([position()], [MONTH, CUPS]).map((tab) => tab.name)).toEqual([
            "Monatsinventur",
        ]);
    });
});

describe("next", () => {
    const sequence = [
        { id: 1, name: "Artikel C" },
        { id: 2, name: "Mehrwegbecher" },
        { id: 3, name: "Artikel F" },
    ];
    const inTwoTabs = [
        { id: 1, name: "Artikel C", tab: 1 },
        { id: 2, name: "Mehrwegbecher", tab: 3 },
        { id: 3, name: "Artikel F", tab: 1 },
    ];

    it("goes along the list", () => {
        expect(next(sequence, 1).id).toBe(2);
        expect(next(sequence, 2).id).toBe(3);
    });

    it("knows none behind the last", () => {
        expect(next(sequence, 3)).toBeNull();
    });

    it("stays in the article's tab", () => {
        expect(next(inTwoTabs, 1).id).toBe(3);
        expect(next(inTwoTabs, 2)).toBeNull();
    });

    it("has no neighbour for an article without row", () => {
        expect(next(sequence, 99)).toBeNull();
    });
});

describe("search", () => {
    const list = [
        { name: "Artikel C 0,33", artikelnummer: "101001" },
        { name: "Artikel N lose", artikelnummer: "101002" },
    ];

    it("finds by name", () => {
        expect(search(list, "artikel c")).toHaveLength(1);
    });

    it("does not search by article number", () => {
        // Nobody knows it by heart when counting. Hits on it only confuse.
        expect(search(list, "101002")).toHaveLength(0);
    });

    it("returns everything without a term", () => {
        expect(search(list, "   ")).toHaveLength(2);
    });
});

describe("addable", () => {
    const all = [
        { id: 1, name: "Artikel C alt", aktiv: false },
        { id: 2, name: "Artikel C 0,33", aktiv: true },
        { id: 3, name: "Artikel N" },
        { id: 4, name: "Artikel C Zero", aktiv: true },
    ];

    it("puts inactive ones behind all active ones", () => {
        expect(addable(all, [], "artikel c").map((a) => a.id)).toEqual([2, 4, 1]);
    });

    it("leaves out what already has a row at the location", () => {
        expect(addable(all, [2, 3], "").map((a) => a.id)).toEqual([4, 1]);
    });

    it("puts discontinued ones at the very back, behind the inactive ones", () => {
        const withOld = [
            { id: 9, name: "Artikel C ausgelistet", aktiv: false, eingestellt: true },
            ...all,
        ];
        expect(addable(withOld, [], "artikel c").map((a) => a.id)).toEqual([2, 4, 1, 9]);
    });

    it("takes master data without the field for active", () => {
        // A storage from before the change does not know 'aktiv' yet.
        expect(addable(all, [], "artikel n").map((a) => a.id)).toEqual([3]);
    });
});

describe("previous", () => {
    const sequence = [
        { id: 1, name: "Artikel C" },
        { id: 2, name: "Mehrwegbecher" },
        { id: 3, name: "Artikel F" },
    ];
    const inTwoTabs = [
        { id: 1, name: "Artikel C", tab: 1 },
        { id: 2, name: "Mehrwegbecher", tab: 3 },
        { id: 3, name: "Artikel F", tab: 1 },
    ];

    it("goes along the list backwards", () => {
        expect(previous(sequence, 3).id).toBe(2);
        expect(previous(sequence, 2).id).toBe(1);
    });

    it("knows none before the first", () => {
        expect(previous(sequence, 1)).toBeNull();
    });

    it("stays in the article's tab", () => {
        expect(previous(inTwoTabs, 3).id).toBe(1);
        expect(previous(inTwoTabs, 2)).toBeNull();
    });

    it("has no neighbour for an article without row", () => {
        expect(previous(sequence, 99)).toBeNull();
    });
});
