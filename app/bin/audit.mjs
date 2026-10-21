/**
 * Known vulnerabilities in the packages that really are in the counting app.
 *
 * Neither part of 'package.json' tells that. Under 'dependencies' stand Dexie,
 * Svelte and the PWA plugin, and with the plugin its build chain (Babel,
 * Rollup), which no browser ever sees. Vite's helper lands in the app,
 * although Vite is under 'devDependencies'. An 'npm audit' over everything
 * also reported Vitest or Playwright and turned the run red for something
 * that is not shipped. What is shipped only the build knows. It names it in
 * 'third-party-licenses.txt' (see 'licenses.mjs'). Hence first
 * 'npm run build', then this script.
 *
 * A package is reported only if it has a vulnerability itself. npm also
 * lists packages that merely depend on a vulnerable one. Svelte would then
 * appear because of a vulnerability in its compiler, and that runs only at
 * build time. If the vulnerable package itself is in the app, it is in the
 * list anyway.
 */

import { spawnSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { FILE_NAME, packagesFrom } from "./licenses.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const LIST = resolve(HERE, "../../server/pwa", FILE_NAME);

// Of Vite only a helper of twenty lines is in the app, which loads chunks.
// Its advisories almost always concern the development server, and that
// runs only here on the machine. In the license text Vite stays, because
// the helper is its code nonetheless.
const BUILD_ONLY = new Set(["vite"]);

/** One line per vulnerability in a shipped package. Empty means: nothing found. */
export function vulnerabilities(report, shipped) {
    const names = new Set(shipped.filter((name) => !BUILD_ONLY.has(name)));
    return Object.values(report.vulnerabilities ?? {})
        .filter((entry) => names.has(entry.name))
        .flatMap((entry) =>
            entry.via
                .filter((cause) => typeof cause === "object")
                .map((cause) => `${entry.name} (${cause.severity}): ${cause.title} ${cause.url}`),
        );
}

async function check() {
    const shipped = packagesFrom(await readFile(LIST, "utf8"));
    if (shipped.length === 0) {
        throw new Error(`${LIST} nennt kein Paket. Stimmt das Format noch?`);
    }
    // 'npm audit' ends with an error code as soon as it finds anything, in
    // tools too. Only the filter should decide.
    const run = spawnSync("npm", ["audit", "--json"], {
        cwd: resolve(HERE, ".."),
        encoding: "utf8",
    });
    const report = JSON.parse(run.stdout);
    if (report.error) {
        throw new Error(`npm audit ist gescheitert: ${report.error.summary}`);
    }
    const found = vulnerabilities(report, shipped);
    console.log(`${shipped.length} ausgelieferte Pakete geprüft: ${shipped.join(", ")}`);
    if (found.length > 0) {
        console.error(`Bekannte Lücken in der Zähl-App:\n${found.join("\n")}`);
        process.exitCode = 1;
    }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
    await check();
}
