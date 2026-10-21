#!/usr/bin/env bash
# The Django behind the end-to-end tests ('playwright.config.js', 'webServer')
# and behind the pictures in the README ('bin/screenshots.mjs').
#
# A throwaway database with the demo stock, and the freshly built app under
# '/app/'. Playwright starts this script before the run and ends it after.
# 'DEBUG' is on, because only then Django serves the app's files itself
# ('django_project/urls.py'). In operation WhiteNoise does that. The port is
# not 8000, so a 'runserver' of the developer may keep running next to it.
#
# The tests count in the German demo of today. The README shows both
# languages, built from a fixed day ('E2E_LANGUAGE', 'E2E_DATE'), so a new
# picture only differs where the interface does.
#
# A local '.env' in 'server/' applies too, except for what is set here:
# 'environs' does not override what the environment already has.
set -euo pipefail

port="${E2E_PORT:-8123}"
language="${E2E_LANGUAGE:-de}"

# Vite's output would appear in the test protocol. Its errors go to stderr
# and stay visible.
cd "$(dirname "$0")/.."
npm run build --silent >/dev/null

cd ../server
# One file per port: a second server on another port does not delete the
# database under the first.
database="${TMPDIR:-/tmp}/crosstally-e2e-$port.sqlite3"
rm -f "$database"
export DATABASE_URL="sqlite:///$database"
# With a POSTGRES_HOST the settings connect to that PostgreSQL and pass over
# DATABASE_URL ('_datenbank' in 'settings.py'). The run would then migrate it
# and fill it with the demo. Empty and not unset: 'environs' would take the
# value from the '.env' for a variable the environment lacks. The other
# POSTGRES_* are only read behind it.
export POSTGRES_HOST=""
export DEBUG=True
# At least 32 bytes, otherwise PyJWT warns about the HMAC key on every token.
export SECRET_KEY="${SECRET_KEY:-e2e-only-key-not-secret-and-long-enough-for-hmac}"
export ADMIN_URL="${ADMIN_URL:-e2e-admin/}"
# The server speaks the demo's language, addresses included ('/inventories/'
# instead of '/inventuren/'), as on a server set up in English.
export LANGUAGE_CODE="$language"

demo=(--language "$language" --verbosity 0)
if [[ -n "${E2E_DATE:-}" ]]; then
    demo+=(--date "$E2E_DATE")
fi
uv run --frozen python manage.py migrate --noinput --verbosity 0
uv run --frozen python manage.py demo "${demo[@]}"

# Every request would otherwise appear in the test output. The lines with a
# 2xx or 3xx are dropped, warnings and errors stay. Playwright ends the
# whole process group, so the pipe needs no 'exec'.
uv run --frozen python manage.py runserver "127.0.0.1:$port" --noreload 2>&1 |
    grep --line-buffered -v -E 'HTTP/1\.[01]" [23][0-9]{2} '
