/**
 * What jsdom does not bring: IndexedDB, a network and a page that scrolls.
 *
 * 'fake-indexeddb' provides the storage, 'fetch' is rejected. The app
 * should start even when the server is not there. Exactly that is the
 * normal case in storage.
 */

import { afterEach } from "vitest";

import "fake-indexeddb/auto";

const keinNetz = () => Promise.reject(new TypeError("kein Netz im Test"));
globalThis.fetch = keinNetz;
// A case that installs a server leaves none behind for the next. Until
// 10/2026 every file cleaned up for itself, or forgot to.
afterEach(() => {
    globalThis.fetch = keinNetz;
});

// jsdom draws nothing and reports every call as "not implemented".
window.scrollTo = () => {};
Element.prototype.scrollIntoView = () => {};

// 'bind:clientHeight' hangs on it, and jsdom does not know it. Nothing is
// measured anyway: every element is zero high there.
globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
};
