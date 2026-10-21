/**
 * Playwright: the way through the app in a real Chromium ('e2e/').
 *
 * Vitest mounts the interface in jsdom, which knows no service worker and no
 * real layout. This here starts the built app once and clicks through it. The
 * server behind it comes from 'bin/e2e-server.sh': a Django with the demo
 * stock on a throwaway database. Playwright starts it and ends it.
 *
 * Against another server: 'E2E_BASE_URL' names it, 'E2E_USERNAME' and
 * 'E2E_PASSWORD' a counter of a running inventory there (read in the spec).
 * If something already answers under the address, the own server is not
 * started. In the CI it always is.
 */

import { defineConfig } from "@playwright/test";

const PORT = process.env.E2E_PORT ?? "8123";
// 'localhost' and not '127.0.0.1': a passkey is tied to a domain, and an IP
// address is none. The server behind it still listens on 127.0.0.1.
const BASE_URL = process.env.E2E_BASE_URL ?? `http://localhost:${PORT}`;

export default defineConfig({
    testDir: "./e2e",
    // One server, one demo stock: the tests run one after the other.
    fullyParallel: false,
    workers: 1,
    forbidOnly: !!process.env.CI,
    // What the tests wait for comes from the API. The first list after
    // signing in takes longer than Playwright's five seconds.
    expect: { timeout: 15_000 },
    reporter: process.env.CI ? [["list"], ["github"], ["html", { open: "never" }]] : "list",
    use: {
        baseURL: BASE_URL,
        browserName: "chromium",
        // Phone format: that is what the app is built for.
        viewport: { width: 390, height: 844 },
        trace: "retain-on-failure",
        screenshot: "only-on-failure",
    },
    projects: [
        { name: "phone" },
        // The smallest phone still around, in the dark: the pass through the
        // house once more. Until 10/2026 the run only knew 390 px and light.
        {
            name: "small-dark",
            testMatch: /smoke\.spec\.js/,
            use: { viewport: { width: 320, height: 568 }, colorScheme: "dark" },
        },
    ],
    webServer: {
        command: "bin/e2e-server.sh",
        url: `${BASE_URL}/app/`,
        reuseExistingServer: !process.env.CI,
        // Build, migrations and demo stock take a few seconds, uv's first
        // sync in the CI longer.
        timeout: 180_000,
        stdout: "pipe",
        stderr: "pipe",
        env: { E2E_PORT: PORT },
    },
});
