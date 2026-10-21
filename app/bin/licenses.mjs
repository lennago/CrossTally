/**
 * The licenses of the third-party packages that land in the counting app's build.
 *
 * MIT and Apache-2.0 require that license text and copyright go along when
 * the code is passed on. But the minifier throws away the comments they
 * stood in. That is why this plugin writes the file 'third-party-licenses.txt'
 * next to the app on every build, with the full text of every package.
 *
 * Which packages they are is not in a list but comes from the build itself:
 * from the modules of the chunks and from the markers Workbox writes into the
 * service worker ('workbox:core:7.4.0'). The service worker is built by
 * 'vite-plugin-pwa' only in 'closeBundle'. That is why this plugin runs
 * there, with 'order: "post"' afterwards. A maintained list would silently go
 * stale on the next update.
 *
 * If a package brings a NOTICE file, it goes along too. Apache-2.0 (§ 4d)
 * requires that, and Dexie has one.
 *
 * If a package lacks its license text, the build aborts. A gap should show
 * up before it is shipped.
 */

import { readdir, readFile, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";

export const FILE_NAME = "third-party-licenses.txt";

const LICENSE_FILE = /^(licen[cs]e|copying)(\.(md|txt))?$/i;
const NOTICE = /^notice(\.(md|txt))?$/i;

/**
 * The package name for a module id, or null for own code.
 *
 * Vite's own helpers ('\0vite/preload-helper', '\0commonjsHelpers.js' from
 * the bundled CommonJS plugin) and the registration of the service worker
 * ('/@vite-plugin-pwa/virtual:pwa-register') do not lie under
 * 'node_modules' but are foreign code nonetheless.
 */
export function packageOf(id) {
    const withoutNull = id.replace(/^\0/, "");
    if (withoutNull.startsWith("vite/") || withoutNull.startsWith("commonjsHelpers")) {
        return "vite";
    }
    if (withoutNull.includes("virtual:pwa-")) return "vite-plugin-pwa";
    const index = withoutNull.lastIndexOf("/node_modules/");
    if (index === -1) return null;
    const parts = withoutNull.slice(index + "/node_modules/".length).split("/");
    return parts[0].startsWith("@") ? `${parts[0]}/${parts[1]}` : parts[0];
}

/** The Workbox packages whose markers are in a piece of service worker. */
export function workboxPackages(sourceText) {
    const names = new Set();
    for (const match of sourceText.matchAll(/workbox:([a-z-]+):\d/g)) {
        names.add(`workbox-${match[1]}`);
    }
    return names;
}

async function licenseOf(name, root) {
    const folder = join(root, "node_modules", name);
    const pkg = JSON.parse(await readFile(join(folder, "package.json"), "utf8"));
    const files = await readdir(folder);
    const file = files.find((entry) => LICENSE_FILE.test(entry));
    if (!file) {
        throw new Error(`Kein Lizenztext im Paket ${name} (${folder}).`);
    }
    const texts = [file, files.find((entry) => NOTICE.test(entry))].filter(Boolean);
    const read = await Promise.all(texts.map((f) => readFile(join(folder, f), "utf8")));
    return {
        name,
        version: pkg.version,
        license: pkg.license ?? "?",
        text: read.map((t) => t.trim()).join(`\n\n${"-".repeat(78)}\n\n`),
    };
}

/** The content of the file, sorted by name, so a new build yields the same file. */
export function compose(entries) {
    const header = [
        "Fremde Pakete in der Zähl-App",
        "",
        "Diese Datei entsteht beim Build. Sie nennt jedes fremde Paket, dessen Code",
        "im ausgelieferten JavaScript steckt, mit Version, Lizenz und vollem Lizenztext.",
    ].join("\n");
    const sections = [...entries]
        .sort((a, b) => a.name.localeCompare(b.name))
        .map(
            (e) =>
                `${"=".repeat(78)}\n${e.name} ${e.version} (${e.license})\n${"=".repeat(78)}\n\n${e.text}`,
        );
    return `${header}\n\n${sections.join("\n\n")}\n`;
}

/**
 * The package names from a file by 'compose'. Stands here because only
 * this module knows the format. The file is the only place that says what
 * is shipped, and 'audit.mjs' checks exactly these packages.
 */
export function packagesFrom(content) {
    const line = "=".repeat(78);
    const header = new RegExp(`^${line}\\n(\\S+) \\S+ \\(.*\\)\\n${line}$`, "gm");
    return [...content.matchAll(header)].map((match) => match[1]);
}

export default function licenses() {
    const packages = new Set();
    let output;
    let root;
    return {
        name: "crosstally-licenses",
        apply: "build",
        configResolved(config) {
            output = resolve(config.root, config.build.outDir);
            root = config.root;
        },
        generateBundle(_options, bundle) {
            for (const part of Object.values(bundle)) {
                if (part.type !== "chunk") continue;
                for (const id of Object.keys(part.modules)) {
                    const name = packageOf(id);
                    if (name) packages.add(name);
                }
            }
        },
        closeBundle: {
            sequential: true,
            order: "post",
            async handler() {
                for (const file of await readdir(output)) {
                    if (/^(sw|workbox-.*)\.js$/.test(file)) {
                        const sourceText = await readFile(join(output, file), "utf8");
                        for (const name of workboxPackages(sourceText)) packages.add(name);
                    }
                }
                const entries = await Promise.all([...packages].map((n) => licenseOf(n, root)));
                await writeFile(join(output, FILE_NAME), compose(entries));
            },
        },
    };
}
