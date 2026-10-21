/**
 * Unit and pack as text, in the app's language.
 *
 * The API sends the **id** of the base unit ('STUECK', 'GRAMM' …), not its
 * text. It has to, because a row lies offline in the storage and outlives
 * every language switch there. A frozen text would not.
 *
 * With the pack only the loose single unit is a word of the language. All
 * other names are data from the catalog and stand as they are entered.
 */

import { t } from "./language.svelte.js";

/** Id from the API to the key in 'texts.js'. */
const UNITS = {
    STUECK: "unit.piece",
    GRAMM: "unit.gram",
    KILOGRAMM: "unit.kilogram",
    MILLILITER: "unit.millilitre",
    LITER: "unit.litre",
    PORTION: "unit.portion",
};

/**
 * The text for a unit.
 *
 * What is no known id comes back unchanged. That affects rows that came
 * into the storage before this change: there "Stück(e)" still stands, and
 * that is better than an empty place.
 */
export function unitText(id) {
    const key = UNITS[id];
    return key ? t(key) : String(id ?? "");
}

/** The name of a pack. The loose single unit is called differently in every language. */
export function packName(pack) {
    if (!pack) return "";
    return pack.ist_einzeln === true ? t("pack.single") : (pack.name ?? "");
}
