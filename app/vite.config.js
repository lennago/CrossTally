import { svelte } from "@sveltejs/vite-plugin-svelte";
import { defineConfig } from "vite";
import { VitePWA } from "vite-plugin-pwa";
import { configDefaults } from "vitest/config";

import licenses from "./bin/licenses.mjs";

// The app runs under '/app/', the same origin as the API, hence no CORS.
// The path has to stand here because the service worker may only control
// the scope it is served under itself: if it lay under '/static/', it
// would not reach the pages under '/app/'.
const BASE = "/app/";

export default defineConfig({
    base: BASE,
    // The version the account menu shows and a report names. The image passes
    // it in as for the server ('Dockerfile'). The device runs the build from its
    // cache, which can be older than the server, so the build carries its own.
    define: {
        __APP_VERSION__: JSON.stringify(process.env.APP_VERSION || "lokal"),
    },
    plugins: [
        svelte(),
        VitePWA({
            // 'prompt' instead of 'autoUpdate': a reload in the middle of counting
            // would tear away the draft someone is typing on right now. The new
            // version therefore waits until the notice in the bar is tapped.
            registerType: "prompt",
            // Registered by hand in 'main.js', because only that way can a new
            // version be checked for regularly and the notice be shown.
            injectRegister: null,
            // The counting interface has to start without network too. The API
            // responses lie in IndexedDB anyway and are not cached here, because a
            // cached count state would be a second truth.
            workbox: {
                globPatterns: ["**/*.{js,css,html,svg,png,woff2}"],
                navigateFallback: `${BASE}index.html`,
                navigateFallbackDenylist: [/^\/api\//, /^\/admin\//, /^\/katalog\//],
                runtimeCaching: [],
            },
            manifest: {
                name: "CrossTally",
                // On the home screen it ends after about twelve characters.
                // What is longer the device shortens with an ellipsis.
                short_name: "CrossTally",
                description: "Zählen für die Inventur",
                lang: "de",
                start_url: BASE,
                scope: BASE,
                display: "standalone",
                // The colour of the splash screen Android shows when opening.
                // Matching the app's surface, otherwise something else briefly flashes.
                background_color: "#ffffff",
                // What Android tints the title bar of the installed app with.
                // The same colour as the logo, otherwise the icon would stand over a
                // bar in another colour.
                theme_color: "#0d6efd",
                // The addresses point past '/app/' to the server. There '/icons/'
                // answers with the uploaded logo if there is one, otherwise with the
                // files here from 'public/icons/'. If the own path stood here, the
                // icon could only be changed with a new build, i.e. only from an own
                // fork.
                //
                // Two sets, and on purpose: 'any' is displayed as it is (browser tab,
                // task overview), 'maskable' Android puts into its own shape (circle,
                // squircle or teardrop) and cuts away up to ten percent per side in
                // the process. A single entry "any maskable" would look wrong in one
                // of the two roles: either with cut-off corners or with far too much
                // air.
                icons: [
                    {
                        src: "/icons/icon-192.png",
                        sizes: "192x192",
                        type: "image/png",
                        purpose: "any",
                    },
                    {
                        src: "/icons/icon-512.png",
                        sizes: "512x512",
                        type: "image/png",
                        purpose: "any",
                    },
                    {
                        src: "/icons/icon-maskable-192.png",
                        sizes: "192x192",
                        type: "image/png",
                        purpose: "maskable",
                    },
                    {
                        src: "/icons/icon-maskable-512.png",
                        sizes: "512x512",
                        type: "image/png",
                        purpose: "maskable",
                    },
                ],
            },
        }),
        // After the service worker, because its Workbox parts need their
        // license text too (see 'bin/licenses.mjs').
        licenses(),
    ],
    server: {
        // In development Vite and Django run separately. The proxy pushes the
        // API over so one origin applies here too and the CORS settings can
        // stay empty.
        proxy: {
            "/api": {
                target: "http://127.0.0.1:8000",
                changeOrigin: true,
            },
        },
    },
    // In the test Svelte should deliver its **browser** version, otherwise one
    // lands in the server variant, which knows no 'mount'. Outside the test
    // 'resolve' stays **unset**: an empty conditions list would overwrite
    // Vite's defaults and push exactly this server version into the build. It
    // then compiles without error and the app stays white in the browser.
    resolve: process.env.VITEST ? { conditions: ["browser"] } : undefined,
    test: {
        // The computing functions need no browser, mounting the interface does.
        // For that jsdom suffices, and that runs everywhere, in the CI too. A
        // real browser is Playwright's ('e2e/').
        environment: "jsdom",
        setupFiles: ["./src/vitest.setup.js"],
        // The specs under 'e2e/' belong to Playwright ('playwright.config.js').
        // Vitest would find them by name and fail on '@playwright/test'.
        exclude: [...configDefaults.exclude, "e2e/**"],
        server: { deps: { inline: ["svelte"] } },
        // What the tests reach of the app's own code ('npm run test:coverage',
        // the CI runs it). Off for 'npm test': a run of a single file would
        // miss the bars every time. They sit two and a half points below the
        // measurement of 20 October 2026, rounded down (statements 95.7 %,
        // branches 88.3 %, functions 96.0 %, lines 97.1 %). That leaves room
        // for a small step back, not for a feature without tests.
        coverage: {
            provider: "v8",
            include: ["src/**"],
            exclude: ["src/test_support.js", "src/vitest.setup.js"],
            thresholds: { statements: 93, branches: 85, functions: 93, lines: 94 },
        },
    },
    build: {
        // Django serves from here: in operation WhiteNoise ('django_project/wsgi.py'),
        // with 'DEBUG' its own view ('django_project/urls.py').
        outDir: "../server/pwa",
        emptyOutDir: true,
    },
});
