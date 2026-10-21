#!/usr/bin/env bash
# Saves the master data to data/backups/: catalog and houses, without accounts
# and without inventories. Everything together is saved by bin/full-dumpdata.sh.
#
# The file stays local and is not checked in (data/ is in .gitignore).
#
# Encrypted as soon as 'BACKUP_KEY' stands in the '.env'. Without a key the
# state is written as before, and the script says so.
set -euo pipefail

# Into the project directory: manage.py and the virtual environment live there.
cd "$(dirname "$0")/.."

ziel="data/backups"
mkdir -p "$ziel"

# 'backup store' writes into a side file first and renames: a run that fails
# must not destroy the previous backup or leave half a one behind. It prints
# the name it wrote, with the ending that says which kind of file it is.
stand="$(
    .venv/bin/python manage.py dumpdata katalog organisation \
        --natural-foreign --natural-primary --indent 2 |
        .venv/bin/python manage.py backup store "$ziel/stand_$(date +%Y-%m-%d_%H-%M-%S)"
)"

# The link carries the same ending as the file it points to.
ln -sfn "$(basename "$stand")" "$ziel/stand.json${stand##*.json}"

echo "Gesichert: $stand"
