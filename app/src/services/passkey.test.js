/**
 * The conversion between the server's JSON and the browser's buffers.
 *
 * What comes back from the device has to arrive at the server as the
 * library expects it, and the challenge has to survive the round trip.
 */

import { describe, expect, it } from "vitest";

import {
    alreadyExists,
    creationOptions,
    credentialToJSON,
    fromBase64url,
    requestOptions,
    toBase64url,
    wasCancelled,
} from "./passkey.js";

const bytes = (...values) => Uint8Array.from(values);

describe("base64url", () => {
    it("decodes without padding and encodes without it", () => {
        expect([...fromBase64url("AQID")]).toEqual([1, 2, 3]);
        expect([...fromBase64url("_-8")]).toEqual([255, 239]);
        expect(toBase64url(bytes(1, 2, 3))).toBe("AQID");
        expect(toBase64url(bytes(255, 239))).toBe("_-8");
    });

    it("survives the round trip", () => {
        const raw = bytes(0, 1, 127, 128, 254, 255);
        expect([...fromBase64url(toBase64url(raw))]).toEqual([...raw]);
    });
});

describe("options", () => {
    it("decodes the byte fields for creating", () => {
        const options = creationOptions({
            challenge: "AQID",
            rp: { id: "localhost" },
            user: { id: "BAU", name: "zoe" },
            excludeCredentials: [{ id: "Bg", type: "public-key" }],
        });
        expect([...options.challenge]).toEqual([1, 2, 3]);
        expect([...options.user.id]).toEqual([4, 5]);
        expect(options.user.name).toBe("zoe");
        expect([...options.excludeCredentials[0].id]).toEqual([6]);
        expect(options.rp).toEqual({ id: "localhost" });
    });

    it("decodes the byte fields for signing in", () => {
        const options = requestOptions({ challenge: "AQID", rpId: "localhost" });
        expect([...options.challenge]).toEqual([1, 2, 3]);
        expect(options.allowCredentials).toEqual([]);
    });
});

describe("credentialToJSON", () => {
    it("packs an attestation", () => {
        const json = credentialToJSON({
            id: "AQID",
            rawId: bytes(1, 2, 3).buffer,
            type: "public-key",
            authenticatorAttachment: "platform",
            response: {
                clientDataJSON: bytes(7).buffer,
                attestationObject: bytes(8).buffer,
                getTransports: () => ["internal"],
            },
        });
        expect(json).toEqual({
            id: "AQID",
            rawId: "AQID",
            type: "public-key",
            authenticatorAttachment: "platform",
            response: { clientDataJSON: "Bw", attestationObject: "CA", transports: ["internal"] },
        });
    });

    it("packs an assertion, with and without a user handle", () => {
        const assertion = (userHandle) =>
            credentialToJSON({
                id: "AQID",
                rawId: bytes(1, 2, 3).buffer,
                type: "public-key",
                response: {
                    clientDataJSON: bytes(7).buffer,
                    authenticatorData: bytes(8).buffer,
                    signature: bytes(9).buffer,
                    userHandle,
                },
            });
        expect(assertion(bytes(10).buffer).response).toEqual({
            clientDataJSON: "Bw",
            authenticatorData: "CA",
            signature: "CQ",
            userHandle: "Cg",
        });
        expect(assertion(null).response.userHandle).toBeNull();
    });
});

describe("the browser's refusals", () => {
    it("tells cancelled from already there", () => {
        expect(wasCancelled({ name: "NotAllowedError" })).toBe(true);
        expect(wasCancelled({ name: "InvalidStateError" })).toBe(false);
        expect(alreadyExists({ name: "InvalidStateError" })).toBe(true);
        expect(wasCancelled(null)).toBe(false);
    });
});
