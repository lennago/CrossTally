#!/usr/bin/env bash
# Saves the state including user accounts to data/backups/complete/.
#
# The file contains password hashes: it stays local, is not checked in
# (data/ is in .gitignore) and belongs only to the executing user.
#
# Encrypted as soon as 'BACKUP_KEY' stands in the '.env'. Without a key the
# state is written as before, and the script says so.
set -euo pipefail

# Into the project directory: manage.py and the virtual environment live there.
cd "$(dirname "$0")/.."

ziel="data/backups/complete"
mkdir -p "$ziel"

# Into memory first, as the button does: the readable state never lies on the
# disk. A dump that breaks off stops the script here. Piped straight into
# 'backup store', its first half got the final name (until 10/2026), because
# the store cannot tell a whole dump from half a one.
inhalt="$(
    .venv/bin/python manage.py dumpdata accounts.CustomUser accounts.Passkey \
        accounts.Anmeldeversuch katalog organisation inventur demo.Demokennzeichen \
        --natural-foreign --natural-primary --indent 2
)"

# 'backup store' writes into a side file first and renames: a run that fails
# must not destroy the previous backup or leave half a one behind. It prints
# the name it wrote, with the ending that says which kind of file it is.
stand="$(
    printf '%s\n' "$inhalt" |
        .venv/bin/python manage.py backup store "$ziel/stand_$(date +%Y-%m-%d_%H-%M-%S)"
)"

# The link carries the same ending as the file it points to.
ln -sfn "$(basename "$stand")" "$ziel/stand.json${stand##*.json}"

echo "Gesichert: $stand"
