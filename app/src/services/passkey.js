/**
 * The browser's half of a passkey: what WebAuthn wants and what it gives back.
 *
 * The server's library speaks JSON with bytes as base64url. The browser's
 * API wants buffers and returns buffers. Here is the conversion in both
 * directions, and nothing else: whether a passkey exists the browser
 * never says ('NotAllowedError' in every case).
 */

export function passkeysAvailable() {
    return typeof window !== "undefined" && !!window.PublicKeyCredential && !!navigator.credentials;
}

export function fromBase64url(text) {
    const normal = text.replace(/-/g, "+").replace(/_/g, "/");
    const padded = normal + "=".repeat((4 - (normal.length % 4)) % 4);
    return Uint8Array.from(atob(padded), (character) => character.charCodeAt(0));
}

export function toBase64url(buffer) {
    const characters = String.fromCharCode(...new Uint8Array(buffer));
    return btoa(characters).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** The options for creating a key, with the byte fields decoded. */
export function creationOptions(json) {
    return {
        ...json,
        challenge: fromBase64url(json.challenge),
        user: { ...json.user, id: fromBase64url(json.user.id) },
        excludeCredentials: (json.excludeCredentials ?? []).map((entry) => ({
            ...entry,
            id: fromBase64url(entry.id),
        })),
    };
}

/** The options for signing in, with the byte fields decoded. */
export function requestOptions(json) {
    return {
        ...json,
        challenge: fromBase64url(json.challenge),
        allowCredentials: (json.allowCredentials ?? []).map((entry) => ({
            ...entry,
            id: fromBase64url(entry.id),
        })),
    };
}

/** The browser's answer as JSON, the way the server's library reads it. */
export function credentialToJSON(credential) {
    const response = credential.response;
    const data = {
        id: credential.id,
        rawId: toBase64url(credential.rawId),
        type: credential.type,
        response: { clientDataJSON: toBase64url(response.clientDataJSON) },
    };
    if (credential.authenticatorAttachment) {
        data.authenticatorAttachment = credential.authenticatorAttachment;
    }
    if (response.attestationObject) {
        data.response.attestationObject = toBase64url(response.attestationObject);
        if (response.getTransports) data.response.transports = response.getTransports();
    } else {
        data.response.authenticatorData = toBase64url(response.authenticatorData);
        data.response.signature = toBase64url(response.signature);
        data.response.userHandle = response.userHandle ? toBase64url(response.userHandle) : null;
    }
    return data;
}

/** Ask the device for a new key. Throws what the browser throws. */
export function createCredential(options) {
    return navigator.credentials.create({ publicKey: creationOptions(options) });
}

/** Ask the device for a signature. Throws what the browser throws. */
export function getCredential(options) {
    return navigator.credentials.get({ publicKey: requestOptions(options) });
}

/** Whether the browser refused: cancelled, or no key for this address. */
export function wasCancelled(error) {
    return error?.name === "NotAllowedError";
}

/** Whether the device already holds a key for this account. */
export function alreadyExists(error) {
    return error?.name === "InvalidStateError";
}
