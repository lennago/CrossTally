/**
 * The draft of the entry: that a fresh weighing row preselects no vessel,
 * where the scale stands open from the start, and what happens with removed
 * packs and a left-behind draft.
 */

import { describe, expect, it } from "vitest";

import {
    addPack,
    belowTare,
    draftFrom,
    removePack,
    restoreDraft,
    validate,
    weighingExpected,
} from "./draft.svelte.js";

it("never preselects a vessel, not even the only one", () => {
    // A preselected vessel one accepted without looking. The tare would be guessed.
    for (const article of [
        { wird_gewogen: true, gebinde: [] },
        { wird_gewogen: true, gebinde: [], behaelter: [1] },
    ]) {
        expect(draftFrom(article, []).weighings[0].container).toBeNull();
    }
});

it("demands the vessel as soon as something was weighed", () => {
    const article = { wird_gewogen: true, gebinde: [] };
    const draft = draftFrom(article, []);
    expect(draft.weighings[0].container).toBeNull();
    // The opened, empty row is no input.
    expect(validate(draft)).toBe("");

    draft.weighings[0].gross = "1200";
    expect(validate(draft)).toContain("Gefäß");
    draft.weighings[0].container = 1;
    expect(validate(draft)).toBe("");
});

const BOX = { id: 5, name: "Karton", faktor_in_basiseinheit: "5000.000" };

/**
 * What the server refuses is said here, before the form is left: ten places
 * before the comma, or so many cartons that the result would not hold them.
 * Until 10/2026 such a number went out and stood here as counted.
 */
it("says when a number is more than a result holds", () => {
    const article = { wird_gewogen: true, gebinde: [BOX] };
    const box = { id: 1, gebinde: BOX, wird_gewogen: false, eingabemenge: null };
    const draft = draftFrom(article, [box]);

    for (const [quantity, refused] of [
        ["199999", false],
        ["200000", true],
        ["12345678901234", true],
    ]) {
        draft.packs[0].quantity = quantity;
        expect(validate(draft) !== "", quantity).toBe(refused);
    }
    expect(validate(draft)).toBe("Das ist mehr, als ein Ergebnis fasst. Bitte die Zahl prüfen.");

    draft.packs[0].quantity = "";
    draft.weighings = [{ weighing: null, gross: "1000000000", container: 1, isNew: true }];
    expect(validate(draft)).toContain("mehr, als ein Ergebnis fasst");
});

it("allows the scale where the location expects only the pack", () => {
    const article = { wird_gewogen: true, gebinde: [BOX] };
    const box = { id: 1, gebinde: BOX, wird_gewogen: false, eingabemenge: null };

    const draft = draftFrom(article, [box]);
    expect(weighingExpected(article, draft.scale)).toBe(false);
    expect(draft.weighings).toEqual([]);
});

it("opens the scale where a weighing row stands", () => {
    const article = { wird_gewogen: true, gebinde: [BOX] };
    const scale = { id: 2, gebinde: null, wird_gewogen: true, wiegungen: [] };

    const draft = draftFrom(article, [scale]);
    expect(weighingExpected(article, draft.scale)).toBe(true);
    expect(draft.weighings).toHaveLength(1);
    expect(draft.weighings[0].container).toBeNull();
});

it("opens it too when the product has no pack at all", () => {
    const article = { wird_gewogen: true, gebinde: [] };
    expect(weighingExpected(article, null)).toBe(true);
    expect(weighingExpected({ wird_gewogen: false, gebinde: [] }, null)).toBe(false);
});

describe("removing and fetching back packs", () => {
    const CRATE = { id: 2, name: "Kasten" };
    const BOTTLE = { id: 3, name: "Flasche" };
    const TRAY = { id: 4, name: "Tray" };
    const ARTICLE = { id: 11, gebinde: [CRATE, BOTTLE, TRAY] };
    const crateRow = { id: 55, gebinde: CRATE, eingabemenge: "2.000" };

    it("puts an expected pack into the selection, at its place", () => {
        const draft = draftFrom(ARTICLE, [crateRow]);
        removePack(draft, 0);

        expect(draft.packs).toEqual([]);
        expect(draft.morePacks.map((g) => g.name)).toEqual(["Kasten", "Flasche", "Tray"]);
        expect(draft.removed.map((e) => e.row.id)).toEqual([55]);
    });

    it("fetches it back with its row and the typed number", () => {
        const draft = draftFrom(ARTICLE, [crateRow]);
        draft.packs[0].quantity = "7";
        removePack(draft, 0);
        addPack(draft, CRATE);

        expect(draft.packs).toMatchObject([{ row: { id: 55 }, quantity: "7" }]);
        expect(draft.removed).toEqual([]);
        expect(draft.morePacks.map((g) => g.name)).toEqual(["Flasche", "Tray"]);
    });

    it("puts an unexpected one back without anything to remove", () => {
        const draft = draftFrom(ARTICLE, []);
        addPack(draft, TRAY);
        addPack(draft, BOTTLE);
        expect(draft.packs.map((e) => e.pack.name)).toEqual(["Flasche", "Tray"]);

        removePack(draft, 1);

        expect(draft.removed).toEqual([]);
        expect(draft.morePacks.map((g) => g.name)).toEqual(["Kasten", "Tray"]);
    });
});

describe("restoreDraft", () => {
    const CRATE = { id: 2, name: "Kasten" };
    const BOTTLE = { id: 3, name: "Flasche" };
    const ARTICLE = { id: 11, gebinde: [CRATE, BOTTLE], wird_gewogen: true };

    it("matches an own row to the number it has meanwhile got from the server", () => {
        // While typing the row still carried the own number -1 and was empty.
        const then = draftFrom(ARTICLE, [{ id: -1, gebinde: CRATE, eingabemenge: null }]);
        then.packs[0].quantity = "4";
        const now = [{ id: 55, server_id: 55, gebinde: CRATE, eingabemenge: "9.000" }];

        const draft = restoreDraft(JSON.parse(JSON.stringify(then)), ARTICLE, now);

        // The number of now, but the number that was visible then.
        expect(draft.packs[0]).toMatchObject({
            row: { id: 55, eingabemenge: null },
            quantity: "4",
        });
    });

    it("creates a row that no longer exists anew on adopting", () => {
        const then = draftFrom(ARTICLE, [{ id: 56, gebinde: BOTTLE, eingabemenge: "1.000" }]);
        const draft = restoreDraft(JSON.parse(JSON.stringify(then)), ARTICLE, []);

        expect(draft.packs[0].row).toBeNull();
        expect(draft.morePacks.map((g) => g.name)).toEqual(["Kasten"]);
    });

    it("keeps two unsent bowls apart when continuing a draft", () => {
        // Neither has a number yet, only its key. Matched by number, both were the last one.
        const scale = {
            id: 57,
            gebinde: null,
            wird_gewogen: true,
            wiegungen: [
                { id: null, kennung: "k-a", brutto_gramm: "900", behaelter: 3 },
                { id: null, kennung: "k-b", brutto_gramm: "700", behaelter: 3 },
            ],
        };
        const then = draftFrom(ARTICLE, [scale]);

        const draft = restoreDraft(JSON.parse(JSON.stringify(then)), ARTICLE, [scale]);

        expect(draft.weighings.map((e) => e.weighing.kennung)).toEqual(["k-a", "k-b"]);
    });

    it("drops a weighing that is gone meanwhile and keeps new ones", () => {
        const scale = {
            id: 57,
            gebinde: null,
            wird_gewogen: true,
            wiegungen: [
                { id: 1, brutto_gramm: "500.000", behaelter: 3 },
                { id: 2, brutto_gramm: "600.000", behaelter: 3 },
            ],
        };
        const then = draftFrom(ARTICLE, [scale]);
        then.weighings.push({
            weighing: null,
            gross: "700",
            container: 3,
            removed: false,
            isNew: true,
        });
        const now = [{ ...scale, wiegungen: [scale.wiegungen[1]] }];

        const draft = restoreDraft(JSON.parse(JSON.stringify(then)), ARTICLE, now);

        expect(draft.weighings.map((e) => e.weighing?.id ?? e.gross)).toEqual([2, "700"]);
    });
});

describe("weight below the tare", () => {
    const BOWL = { id: 3, name: "Schale", tara: "250.000" };
    const article = { wird_gewogen: true, gebinde: [] };
    const weighed = (gross) => {
        const draft = draftFrom(article, []);
        draft.weighings[0].gross = gross;
        draft.weighings[0].container = BOWL.id;
        return draft;
    };

    it("blocks what is lighter than the empty vessel, and names it", () => {
        const message = validate(weighed("100"), [BOWL]);
        expect(message).toContain("leichter als das leere Gefäß „Schale“ (250 g)");
        expect(message).toContain("0 eintragen");
        // Typed with a comma too.
        expect(validate(weighed("249,5"), [BOWL])).toContain("leichter");
    });

    it("allows the 0: then there was nothing to weigh", () => {
        expect(validate(weighed("0"), [BOWL])).toBe("");
        expect(validate(weighed("0,0"), [BOWL])).toBe("");
    });

    it("allows the empty weight itself and everything above", () => {
        expect(validate(weighed("250"), [BOWL])).toBe("");
        expect(validate(weighed("1250"), [BOWL])).toBe("");
    });

    it("holds nobody because of an old weighing, but checks it after a change", () => {
        const old = { id: 9, brutto_gramm: "100.000", behaelter: BOWL.id };
        const scale = { id: 2, gebinde: null, wird_gewogen: true, wiegungen: [old] };
        const draft = draftFrom(article, [scale]);
        expect(belowTare(draft.weighings[0], [BOWL])).toBe(false);
        expect(validate(draft, [BOWL])).toBe("");

        draft.weighings[0].gross = "120";
        expect(belowTare(draft.weighings[0], [BOWL])).toBe(true);
    });
});

it("refuses a fourth decimal place before the server does", () => {
    const draft = draftFrom({ wird_gewogen: false, gebinde: [] }, []);
    draft.pieceCount = "1.2345";
    expect(validate(draft)).toBe("Höchstens drei Nachkommastellen.");
    draft.pieceCount = "1,25";
    expect(validate(draft)).toBe("");
});
