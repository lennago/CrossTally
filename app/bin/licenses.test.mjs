import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, beforeEach, expect, it } from "vitest";

import licenses, {
    FILE_NAME,
    compose,
    packageOf,
    packagesFrom,
    workboxPackages,
} from "./licenses.mjs";

let root;

beforeEach(async () => {
    root = await mkdtemp(join(tmpdir(), "licenses-"));
    await mkdir(join(root, "dist"));
});

afterEach(async () => {
    await rm(root, { recursive: true, force: true });
});

async function pkg(name, licenseText, file = "LICENSE") {
    const folder = join(root, "node_modules", name);
    await mkdir(folder, { recursive: true });
    await writeFile(
        join(folder, "package.json"),
        JSON.stringify({ name, version: "1.2.3", license: "MIT" }),
    );
    if (licenseText) await writeFile(join(folder, file), licenseText);
}

/** A build in miniature: a chunk with the modules, then the service worker. */
async function build(modules, serviceWorker = "") {
    const plugin = licenses();
    plugin.configResolved({ root, build: { outDir: "dist" } });
    const chunk = { type: "chunk", modules: Object.fromEntries(modules.map((id) => [id, {}])) };
    plugin.generateBundle({}, { "index.js": chunk, "index.css": { type: "asset" } });
    await writeFile(join(root, "dist", "workbox-abc123.js"), serviceWorker);
    await plugin.closeBundle.handler();
    return readFile(join(root, "dist", FILE_NAME), "utf8");
}

it("recognises the package behind a module id", () => {
    expect(packageOf("/p/node_modules/dexie/dist/dexie.min.js")).toBe("dexie");
    expect(packageOf("\0/p/node_modules/dexie/dist/dexie.min.js?commonjs-module")).toBe("dexie");
    expect(packageOf("/p/node_modules/@scope/teil/index.js")).toBe("@scope/teil");
    // Nested, the innermost package counts, because its code is in the build.
    expect(packageOf("/p/node_modules/a/node_modules/b/x.js")).toBe("b");
    expect(packageOf("\0vite/preload-helper.js")).toBe("vite");
    expect(packageOf("\0commonjsHelpers.js")).toBe("vite");
    expect(packageOf("/@vite-plugin-pwa/virtual:pwa-register")).toBe("vite-plugin-pwa");
    expect(packageOf("/p/src/App.svelte")).toBeNull();
});

it("reads the Workbox packages from the markers of the service worker", () => {
    const sourceText =
        'x["workbox:core:7.4.0"]&&1;y["workbox:precaching:7.4.0"];"workbox:core:7.4.0"';
    expect([...workboxPackages(sourceText)]).toEqual(["workbox-core", "workbox-precaching"]);
});

it("sorts by name so every build yields the same file", () => {
    const text = compose([
        { name: "zeta", version: "1", license: "MIT", text: "Z" },
        { name: "alpha", version: "2", license: "MIT", text: "A" },
    ]);
    expect(text.indexOf("alpha 2 (MIT)")).toBeLessThan(text.indexOf("zeta 1 (MIT)"));
});

it("reads the package names back from its own file", () => {
    const text = compose([
        { name: "@scope/paket", version: "1.0.0", license: "(MIT OR CC0-1.0)", text: "S" },
        { name: "alpha", version: "2", license: "MIT", text: "Copyright A\n\nnoch eine Zeile" },
    ]);
    expect(packagesFrom(text)).toEqual(["@scope/paket", "alpha"]);
});

it("writes the full text of every package from bundle and service worker", async () => {
    await pkg("paket-a", "Copyright A");
    await pkg("workbox-core", "Copyright Google", "LICENSE.md");
    const text = await build(
        [join(root, "node_modules/paket-a/index.js"), join(root, "src/main.js")],
        '"workbox:core:7.4.0"',
    );
    expect(text).toContain("paket-a 1.2.3 (MIT)");
    expect(text).toContain("Copyright A");
    expect(text).toContain("workbox-core 1.2.3 (MIT)");
    expect(text).toContain("Copyright Google");
});

it("takes a NOTICE file along, as Apache-2.0 demands", async () => {
    await pkg("paket-n", "Apache License");
    await writeFile(join(root, "node_modules/paket-n/NOTICE"), "Copyright N");
    const text = await build([join(root, "node_modules/paket-n/index.js")]);
    expect(text).toContain("Apache License");
    expect(text).toContain("Copyright N");
});

it("aborts when a package lacks its license text", async () => {
    await pkg("ohne-text", null);
    await expect(build([join(root, "node_modules/ohne-text/index.js")])).rejects.toThrow(
        "Kein Lizenztext im Paket ohne-text",
    );
});
