/**
 * Make the pictures in the README from the demo, in German and in English.
 *
 * Two motifs: the counting app in the beverage store of a demo house, with a
 * tab per inventory type and a count of crates being typed, and the review
 * list of the website while the counting is still going on. Both come from
 * 'manage.py demo' and never from real data. The server is the one of the
 * end-to-end tests ('bin/e2e-server.sh'): a throwaway database with the demo
 * stock, here built from a fixed day. A new run thus shows the same numbers,
 * and a picture only changes where the interface does. Whoever changes
 * something visible runs 'npm run screenshots' and commits the pictures.
 *
 * Rendered with Chromium like 'npm run icons', at twice the pixel density:
 * on a sharp screen a picture at single density looks blurred. pngquant then
 * reduces the colours, which more than halves the files without a visible
 * difference. It comes through 'uvx' and is no dependency of the project.
 * Without uv the pictures stay as Chromium wrote them.
 */

import { spawn, spawnSync } from "node:child_process";
import { rmSync } from "node:fs";
import { copyFile, mkdir, mkdtemp, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright";

const HERE = dirname(fileURLToPath(import.meta.url));
const APP = resolve(HERE, "..");
const TARGET = resolve(HERE, "../../docs/screenshots");

//: Not the tests' 8123: a test server may still be running there.
const PORT = process.env.E2E_PORT ?? "8124";
// 'localhost' like the tests. The server behind it listens on 127.0.0.1.
const BASE_URL = `http://localhost:${PORT}`;

//: The day the demo is built from. Its running inventory is then the one of
//: 31 August 2026, as in the table of DEMO.md.
const DAY = "2026-09-15";

//: The demo's accounts, see DEMO.md. The stock is invented and the password public.
const COUNTER = { username: "zaehler", password: "demo1234" };
const MANAGER = { username: "leitung", password: "demo1234" };

const SCALE = 2;
//: A common Android phone. At 390 px the location's name was cut off in the header.
const PHONE = { width: 412, height: 915 };
//: Narrower, and the review list scrolls sideways: its table needs 76rem.
const DESKTOP = { width: 1280, height: 960 };

//: pngquant from PyPI at a fixed version: the same picture compresses the same way.
//: It lies outside every lock file, and 'uvx' checks no hashes for a tool. So
//: it takes only files that were there on the day after the release
//: ('--exclude-newer'), and it builds nothing from source ('--no-build'): a
//: file added to the release later, or a platform without a wheel, gets no say.
const PNGQUANT = [
    "--no-build",
    "--exclude-newer",
    "2024-10-05T00:00:00Z",
    "--from",
    "pngquant-cli==3.0.3",
    "pngquant",
];

/**
 * What the pictures show, per language.
 *
 * The names come from the demo ('server/demo/names_en.py'), the addresses
 * from the server's language ('server/django_project/routes.py').
 */
const LANGUAGES = {
    de: {
        locale: "de-DE",
        house: "Kino Nord",
        types: "Monatsinventur + Becherinventur",
        area: "Lager",
        location: "Getränkelager",
        review: (id) => `/inventuren/${id}/pruefen/`,
    },
    en: {
        locale: "en-GB",
        house: "Cinema North",
        types: "Monthly inventory + Cup inventory",
        area: "Store room",
        location: "Beverage store",
        review: (id) => `/inventories/${id}/review/`,
    },
};

//: The server currently running and the folder of the unoptimised pictures.
//: Both go on every way out, Ctrl+C included: in a process group of its own
//: the server does not see the terminal's signal.
let running = null;
let scratch = null;
process.on("exit", () => {
    if (running && !ended(running)) {
        try {
            process.kill(-running.pid, "SIGTERM");
        } catch {
            // Already gone between the last look and now.
        }
    }
    if (scratch) rmSync(scratch, { recursive: true, force: true });
});
process.on("SIGINT", () => process.exit(130));
process.on("SIGTERM", () => process.exit(143));

function wait(milliseconds) {
    return new Promise((done) => setTimeout(done, milliseconds));
}

function ended(server) {
    return server.exitCode !== null || server.signalCode !== null;
}

/** The server's answer to a path, or nothing if nobody listens on the port. */
async function ask(path) {
    try {
        const answer = await fetch(`http://127.0.0.1:${PORT}${path}`);
        await answer.body?.cancel();
        return answer;
    } catch {
        return null;
    }
}

/**
 * Start the demo server in a language and wait until the app answers.
 *
 * In a process group of its own: the script starts uv, uv starts Django, and
 * only the group as a whole can be ended. What it prints is kept and only
 * shown if it does not come up.
 */
async function startServer(language) {
    const server = spawn("bin/e2e-server.sh", {
        cwd: APP,
        detached: true,
        env: { ...process.env, E2E_PORT: PORT, E2E_LANGUAGE: language, E2E_DATE: DAY },
        stdio: ["ignore", "pipe", "pipe"],
    });
    running = server;
    let output = "";
    server.stdout.on("data", (chunk) => (output += chunk));
    server.stderr.on("data", (chunk) => (output += chunk));
    // Build, migrations and demo stock take a few seconds, as in 'playwright.config.js'.
    const until = Date.now() + 180_000;
    while (Date.now() < until) {
        if (ended(server)) {
            throw new Error(`Der Demo-Server (${language}) ist nicht angelaufen:\n${output}`);
        }
        if ((await ask("/app/"))?.ok) return server;
        await wait(500);
    }
    throw new Error(`Der Demo-Server (${language}) antwortet nach drei Minuten nicht:\n${output}`);
}

/** End the server and wait until the port is free for the next language. */
async function stopServer(server) {
    if (!ended(server)) {
        const exit = new Promise((done) => server.once("exit", done));
        process.kill(-server.pid, "SIGTERM");
        await exit;
    }
    // Django may outlive the script by a moment.
    const until = Date.now() + 10_000;
    while (Date.now() < until) {
        if (!(await ask("/"))) return;
        await wait(200);
    }
    throw new Error(`Port ${PORT} bleibt nach dem Ende des Demo-Servers belegt.`);
}

/** What every browser context shares: the server, light colours, no motion. */
function contextFor(names) {
    return {
        baseURL: BASE_URL,
        deviceScaleFactor: SCALE,
        colorScheme: "light",
        reducedMotion: "reduce",
        locale: names.locale,
        timezoneId: "Europe/Berlin",
    };
}

/** Until the page has stopped fetching and its fonts are there. */
async function settle(page) {
    await page.waitForLoadState("networkidle");
    await page.evaluate(() => document.fonts.ready);
}

/** The upper part of the page, down to 'bottom'. */
function down(bottom) {
    return { x: 0, y: 0, width: PHONE.width, height: Math.ceil(bottom) };
}

/** The row with exactly this title, as the app's list shows it. */
function rowTitled(page, title) {
    return page.locator("button.row").filter({ has: page.getByText(title, { exact: true }) });
}

/**
 * Open a row and wait for the next level.
 *
 * Every level shows rows. Whoever only waits for those would photograph the
 * old list. The header, by contrast, changes with every step.
 */
async function descend(page, row) {
    const heading = page.locator("header.header strong");
    const before = await heading.innerText();
    await row.click();
    await page.waitForFunction(
        (text) => document.querySelector("header.header strong")?.innerText !== text,
        before,
    );
    await page.locator("button.row").first().waitFor();
}

/** The running inventory of a house, found the way the app finds it. */
async function runningInventory(request, house) {
    const token = await request.post("/api/v1/token/", { data: COUNTER });
    if (!token.ok()) throw new Error(`Anmeldung an der API gescheitert: ${token.status()}`);
    const headers = { Authorization: `Bearer ${(await token.json()).access}` };
    const inventories = await (await request.get("/api/v1/inventuren/", { headers })).json();
    const inventory = inventories.find((entry) => entry.haus === house && entry.zaehlbar);
    if (!inventory) throw new Error(`Keine laufende Inventur in „${house}“.`);
    return inventory.id;
}

/**
 * The review list of the running inventory of the house, on the website.
 *
 * Fully counted, with "Target counts" set at the cola syrup and its note:
 * what a manager sees before closing. The inventory is found through the
 * API, because its address carries a number that depends on the build.
 */
async function reviewList(browser, names) {
    const context = await browser.newContext({ ...contextFor(names), viewport: DESKTOP });
    const inventory = await runningInventory(context.request, names.house);
    const page = await context.newPage();
    await page.goto("/accounts/login/");
    await page.locator("input[name=username]").fill(MANAGER.username);
    const password = page.locator("input[name=password]");
    await password.fill(MANAGER.password);
    await Promise.all([
        page.waitForURL((address) => !address.pathname.startsWith("/accounts/")),
        password.press("Enter"),
    ]);
    await page.goto(names.review(inventory));
    await settle(page);
    // The time of the numbers is the server's clock. It would change every
    // picture without anything on the page having changed.
    await page
        .locator('[data-wert="stand"]')
        .locator("..")
        .evaluate((element) => (element.style.visibility = "hidden"));
    const picture = await page.screenshot({ animations: "disabled" });
    await context.close();
    return picture;
}

/**
 * The counting app: the beverage store with its two tabs, then its first
 * article with a count of crates being typed.
 *
 * In this house only the cola's crates are still open in the beverage store
 * ('OFFENE_GEBINDE' in 'server/demo/inventories.py'), and the cups give it
 * a second tab. The count is typed, not confirmed: it stays in this browser
 * and does not reach the server.
 */
async function countingApp(browser, names, language) {
    const context = await browser.newContext({
        ...contextFor(names),
        viewport: PHONE,
        isMobile: true,
        hasTouch: true,
    });
    const page = await context.newPage();
    await page.goto("/app/");
    await page.locator("input[autocomplete=username]").fill(COUNTER.username);
    const password = page.locator("input[autocomplete=current-password]");
    await password.fill(COUNTER.password);
    await password.press("Enter");
    await page.locator("button.row").first().waitFor();
    // Before signing in the app speaks German, afterwards the account's language.
    await page.waitForFunction((wanted) => document.documentElement.lang === wanted, language);
    const run = page.locator("button.row").filter({ hasText: names.house });
    await descend(page, run.filter({ hasText: names.types }));
    await descend(page, rowTitled(page, names.area).first());
    await descend(page, rowTitled(page, names.location).first());
    await page.getByRole("tab").first().waitFor();
    await settle(page);
    // The tabs, the open cola and the first three counted after it.
    const fourth = await page.locator("button.row").nth(3).boundingBox();
    const list = await page.screenshot({
        clip: down(fourth.y + fourth.height),
        animations: "disabled",
    });

    await page.locator("button.row").first().click();
    const crates = page.locator(".count-row input").first();
    await crates.click();
    await crates.pressSequentially("9");
    await settle(page);
    const bottom = await page.evaluate(() =>
        Math.max(
            ...[...document.querySelectorAll(".count-row, .extras")].map(
                (element) => element.getBoundingClientRect().bottom,
            ),
        ),
    );
    const entry = await page.screenshot({ clip: down(bottom), animations: "disabled" });
    await context.close();
    return [list, entry];
}

/**
 * Pictures one below the other, each with a thin frame.
 *
 * Without the frame the white edge of a page merges with the white of the
 * README. Drawn by Chromium at the pictures' density, so every pixel of a
 * picture lands on exactly one pixel of the result.
 */
async function framed(page, pictures) {
    const frames = pictures.map((picture) => {
        // The PNG header holds the width in pixels, the page wants it in points.
        const width = picture.readUInt32BE(16) / SCALE;
        const source = `data:image/png;base64,${picture.toString("base64")}`;
        return `<div class="frame"><img width="${width}" src="${source}"></div>`;
    });
    await page.setContent(
        `<!doctype html><body style="margin:0">
         <div id="pictures">${frames.join("")}</div>
         <style>
             #pictures { display: inline-flex; flex-direction: column; gap: 16px }
             .frame { border: 1px solid #d0d7de; border-radius: 12px; overflow: hidden }
             .frame img { display: block }
         </style></body>`,
    );
    return page.locator("#pictures").screenshot({ omitBackground: true });
}

/** Write a picture, through pngquant where it can be had. */
async function save(picture, name) {
    const target = join(TARGET, name);
    const raw = join(scratch, name);
    await writeFile(raw, picture);
    // Without dithering the letters stay sharp. A picture pngquant cannot
    // keep at this quality, or would make larger, it leaves alone.
    const quantised = spawnSync(
        "uvx",
        [
            ...PNGQUANT,
            ...["--quality", "80-98", "--nofs", "--speed", "1", "--strip", "--skip-if-larger"],
            ...["--force", "--output", target, raw],
        ],
        { stdio: ["ignore", "ignore", "pipe"] },
    );
    if (quantised.status !== 0) {
        await copyFile(raw, target);
        const reason = quantised.error?.message ?? quantised.stderr.toString().trim();
        console.log(`       ohne pngquant (${reason || `Status ${quantised.status}`})`);
    }
    const size = Math.round((await stat(target)).size / 1024);
    console.log(`${size.toString().padStart(4)} kB  ${target}`);
}

if (await ask("/")) {
    throw new Error(`Auf Port ${PORT} antwortet schon etwas. Einen anderen nennt E2E_PORT=<port>.`);
}
await mkdir(TARGET, { recursive: true });
scratch = await mkdtemp(join(tmpdir(), "crosstally-screenshots-"));
const browser = await chromium.launch();
try {
    for (const [language, names] of Object.entries(LANGUAGES)) {
        console.log(`Demo (${language}) auf Port ${PORT} …`);
        const server = await startServer(language);
        try {
            const review = await reviewList(browser, names);
            const app = await countingApp(browser, names, language);
            const page = await browser.newPage({
                deviceScaleFactor: SCALE,
                viewport: { width: DESKTOP.width + 100, height: DESKTOP.height + 100 },
            });
            await save(await framed(page, app), `counting-app.${language}.png`);
            await save(await framed(page, [review]), `review-list.${language}.png`);
            await page.close();
        } finally {
            await stopServer(server);
        }
    }
} finally {
    await browser.close();
}
