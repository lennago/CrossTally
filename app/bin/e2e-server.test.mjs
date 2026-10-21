import { spawnSync } from "node:child_process";
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, expect, it } from "vitest";

// The script builds the app, migrates and seeds its database, then starts
// Django. Here 'npm' does nothing, and 'uv' only reports what Django would
// read and then stops the script, before anything is migrated.
const SCRIPT = join(import.meta.dirname, "e2e-server.sh");
const STUBS = {
    npm: "exit 0",
    uv: 'echo "POSTGRES_HOST=[${POSTGRES_HOST-unset}] DATABASE_URL=$DATABASE_URL"\nexit 3',
};

let folder;

afterEach(() => rmSync(folder, { recursive: true, force: true }));

function start(environment) {
    folder = mkdtempSync(join(tmpdir(), "e2e-server-"));
    for (const [name, body] of Object.entries(STUBS)) {
        writeFileSync(join(folder, name), `#!/bin/sh\n${body}\n`);
        chmodSync(join(folder, name), 0o755);
    }
    return spawnSync("bash", [SCRIPT], {
        encoding: "utf8",
        env: {
            ...process.env,
            ...environment,
            PATH: `${folder}:${process.env.PATH}`,
            TMPDIR: folder,
            E2E_PORT: "8199",
        },
    });
}

it("migrates the throwaway SQLite even when the environment names a PostgreSQL", () => {
    // With POSTGRES_HOST set, the settings connect there and ignore
    // DATABASE_URL. The demo would then land in that database.
    const run = start({ POSTGRES_HOST: "db.example.org", POSTGRES_PASSWORD: "not-used" });

    expect(run.status, run.stderr).toBe(3);
    expect(run.stdout).toBe(
        `POSTGRES_HOST=[] DATABASE_URL=sqlite:///${folder}/crosstally-e2e-8199.sqlite3\n`,
    );
});

it("sets the host empty rather than unset, so a value in server/.env stays out too", () => {
    // 'environs' fills in from the '.env' only what the environment lacks.
    const run = start({});

    expect(run.stdout).toContain("POSTGRES_HOST=[] ");
});
