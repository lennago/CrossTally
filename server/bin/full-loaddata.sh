#!/usr/bin/env bash
# Restores a backup. Without an argument the most recent from data/backups/complete.
#
# Merges instead of replacing: what has the same id is overwritten,
# everything else stays. Afterwards everyone is signed out.
#
# An encrypted state is read with 'BACKUP_KEY' from the '.env'. An unencrypted
# one is read as it is without a key. With a key only after '--plaintext' in
# front of the path: anyone who may write into the folder could have placed it,
# and its contents would be loaded as they are.
#
#   bin/full-loaddata.sh [--plaintext] [state]
set -euo pipefail

# Into the project directory: manage.py and the virtual environment live there.
cd "$(dirname "$0")/.."

# The word of whoever calls that an unencrypted state is their own old one.
lesen=(backup read)
if [[ "${1:-}" == "--plaintext" ]]; then
    lesen+=(--plaintext)
    shift
fi

ziel="data/backups/complete"
# The names carry their time, so the last one by name is the most recent one,
# whichever kind of file it is.
stand="${1:-$(find "$ziel" -maxdepth 1 -name 'stand_*.json*' 2>/dev/null | sort | tail -n 1)}"
if [[ -z "$stand" || ! -f "$stand" ]]; then
    echo "Keine Sicherung unter ${stand:-$ziel}." >&2
    exit 1
fi

# Read first, load afterwards: a key that does not fit should end here with
# its message and not inside 'loaddata' with a traceback about an empty input.
inhalt="$(mktemp)"
trap 'rm -f "$inhalt"' EXIT
.venv/bin/python manage.py "${lesen[@]}" "$stand" >"$inhalt"

.venv/bin/python manage.py migrate
# '--ignorenonexistent': an older state still carries fields that no longer
# exist. They are skipped instead of rejected.
.venv/bin/python manage.py loaddata --ignorenonexistent --format=json - <"$inhalt"

# The state brings the accounts back as they were at its moment, passwords and
# passkeys included. The sessions of the website and the refresh tokens of the
# counting app stay in the database while merging, and those a newer password
# had shut out would apply again. So everyone signs in anew, as after a restore
# in the administration.
.venv/bin/python manage.py sign_out_everyone

echo "Eingespielt: $stand"
