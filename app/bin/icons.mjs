/**
 * Make the icons the devices demand from the two SVG sources.
 *
 * Sources are 'server/static/logo.svg' (rounded corners, for tab and
 * website) and 'server/static/logo-maskable.svg' (edge-filling, for the home
 * screen). Whoever has an own logo replaces the two files and runs
 * 'npm run icons'. The generated PNGs lie in the repo so a build gets by
 * without Chromium.
 *
 * Rendered with Chromium (Playwright is available for the smoke test
 * anyway): an SVG at exactly the target size, then a screenshot of it.
 */

import { copyFile, mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright";

const HERE = dirname(fileURLToPath(import.meta.url));
const SOURCES = resolve(HERE, "../../server/static");
const TO_PWA = resolve(HERE, "../public/icons");
const TO_WEBSITE = SOURCES;

/**
 * What goes where, and from which source.
 *
 * 'opaque' means: without alpha channel. That applies to the Apple icon,
 * because iOS backs transparency with black, and an icon with transparent
 * corners would get a black frame.
 */
const IMAGES = [
    { source: "logo.svg", target: `${TO_PWA}/icon-192.png`, size: 192 },
    { source: "logo.svg", target: `${TO_PWA}/icon-512.png`, size: 512 },
    { source: "logo-maskable.svg", target: `${TO_PWA}/icon-maskable-192.png`, size: 192 },
    { source: "logo-maskable.svg", target: `${TO_PWA}/icon-maskable-512.png`, size: 512 },
    // 180 points is the size iOS reads for the home screen.
    {
        source: "logo-maskable.svg",
        target: `${TO_PWA}/apple-touch-icon.png`,
        size: 180,
        opaque: true,
    },
    { source: "logo.svg", target: `${TO_PWA}/favicon-32.png`, size: 32 },
    { source: "logo.svg", target: `${TO_WEBSITE}/favicon-32.png`, size: 32 },
    {
        source: "logo-maskable.svg",
        target: `${TO_WEBSITE}/apple-touch-icon.png`,
        size: 180,
        opaque: true,
    },
];

// The SVG itself goes into the app: browsers that take it as favicon get a
// logo that stays sharp at every size.
await mkdir(TO_PWA, { recursive: true });
await copyFile(resolve(SOURCES, "logo.svg"), `${TO_PWA}/logo.svg`);
console.log(`       logo.svg             -> ${TO_PWA}/logo.svg`);

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 512, height: 512 } });

for (const { source, target, size, opaque } of IMAGES) {
    const svg = await readFile(resolve(SOURCES, source), "utf8");
    await page.setContent(
        `<!doctype html><body style="margin:0">
         <div id="image" style="width:${size}px;height:${size}px">${svg}</div>
         <style>#image>svg{width:100%;height:100%;display:block}</style></body>`,
    );
    await mkdir(dirname(target), { recursive: true });
    const image = await page.locator("#image").screenshot({ omitBackground: !opaque });
    await writeFile(target, image);
    console.log(`${size.toString().padStart(3)} px  ${source.padEnd(20)} -> ${target}`);
}

await browser.close();
