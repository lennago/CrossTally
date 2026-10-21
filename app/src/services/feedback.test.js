/**
 * The mail behind "Report a problem or suggestion".
 *
 * The recipient filters by the start of the subject, so it stays the same in
 * every language. The rest is what a report needs and the person can rarely
 * say: version, page, account, browser, connection and what still waits.
 */

import { afterEach, describe, expect, it } from "vitest";

import { SUBJECT_TAG, feedbackLink } from "./feedback.js";
import { apply } from "./language.svelte.js";
import { APP_VERSION } from "./version.svelte.js";

const ADDRESS = "rueckmeldung@example.org";

function parts(link) {
    const url = new URL(link);
    return {
        to: url.pathname,
        subject: url.searchParams.get("subject"),
        lines: url.searchParams.get("body").split("\r\n"),
    };
}

afterEach(() => apply("de"));

describe("feedbackLink", () => {
    it("writes to the address the administration named", () => {
        const link = feedbackLink({ address: ADDRESS, username: "zoe", online: true, waiting: 0 });
        expect(link.startsWith(`mailto:${ADDRESS}?`)).toBe(true);
        expect(parts(link).to).toBe(ADDRESS);
    });

    it("starts the subject the same in every language", () => {
        const link = () => feedbackLink({ address: ADDRESS, online: true, waiting: 0 });
        expect(SUBJECT_TAG).toBe("[CrossTally]");
        expect(parts(link()).subject).toBe(`${SUBJECT_TAG} Rückmeldung aus der Zähl-App`);
        apply("en");
        expect(parts(link()).subject).toBe(`${SUBJECT_TAG} Feedback from the counting app`);
        expect(parts(link()).lines).toContain("Technical details, please leave them in:");
    });

    it("brings along what a report needs", () => {
        const { lines } = parts(
            feedbackLink({ address: ADDRESS, username: "zoe", online: false, waiting: 3 }),
        );
        expect(lines[0]).toBe("Was ist passiert, oder was wünschst du dir?");
        expect(lines).toContain("Technische Angaben, bitte stehen lassen:");
        expect(lines).toContain(`Version: ${APP_VERSION}`);
        expect(lines).toContain(`Seite: ${location.href}`);
        expect(lines).toContain("Konto: zoe");
        expect(lines).toContain(`Browser: ${navigator.userAgent}`);
        expect(lines).toContain("Verbindung: offline");
        expect(lines).toContain("Wartende Eingaben: 3");
        expect(lines.some((line) => line.startsWith("Zeit: "))).toBe(true);
    });

    it("leaves no mark unencoded that RFC 6068 forbids", () => {
        const link = feedbackLink({ address: ADDRESS, username: "zoe", online: true, waiting: 0 });
        const query = link.split("?")[1];
        expect(query).toContain("%0D%0A");
        expect(query).not.toMatch(/[ /\n]/);
    });
});
