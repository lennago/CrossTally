import { beforeEach, expect, it } from "vitest";

import { TEXTS } from "../texts.js";
import { apply } from "./language.svelte.js";
import { unitText, packName } from "./units.js";

beforeEach(() => apply("de"));

it("translates the id of the unit", () => {
    expect(unitText("STUECK")).toBe("Stk.");
    expect(unitText("KILOGRAMM")).toBe("kg");
    apply("en");
    expect(unitText("STUECK")).toBe("pcs");
    expect(unitText("KILOGRAMM")).toBe("kg");
});

it("leaves alone what is no id", () => {
    // Rows that came into the storage before the change carry the text.
    expect(unitText("Stück(e)")).toBe("Stück(e)");
    expect(unitText(null)).toBe("");
});

it("lists every id as its own text", () => {
    for (const id of ["STUECK", "GRAMM", "KILOGRAMM", "MILLILITER", "LITER"]) {
        expect(unitText(id), id).not.toBe(id);
    }
    expect("unit.piece" in TEXTS).toBe(true);
});

it("names the loose single piece in the app's language", () => {
    const single = { name: "Einzeln", ist_einzeln: true };
    expect(packName(single)).toBe("Einzeln");
    apply("en");
    expect(packName(single)).toBe("Single");
});

it("leaves every other pack name as it stands in the catalog", () => {
    apply("en");
    expect(packName({ name: "Kasten", ist_einzeln: false })).toBe("Kasten");
    // Old rows from the storage do not know the switch yet.
    expect(packName({ name: "Kasten" })).toBe("Kasten");
    expect(packName(null)).toBe("");
});
