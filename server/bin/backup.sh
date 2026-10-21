#!/usr/bin/env bash
# A backup of the database, encrypted. Called on the machine compose runs on:
#
#   ./server/bin/backup.sh   (or via cron)
#
# Copying a file worked without a tool with SQLite. With Postgres it needs
# this, and without a backup the move would be a step backwards.
#
# 'pg_dump' runs in the container of the database, because its version has to
# match the server's. Encrypting happens in a container of the application's
# image: the image of the database brings nothing to encrypt with. The key is
# the 'BACKUP_KEY' from the '.env', and it never leaves the container. Until
# 09/2026 the script ran inside the container of the database and wrote
# unencrypted.
#
# Whoever runs it has to be allowed to write into the backup folder: root, or
# the user the folder belongs to (see DEPLOYMENT.md).
set -euo pipefail

# The dump contains the accounts with their password hashes. Encrypted or not,
# it belongs to whoever runs the script and to nobody else on the machine.
umask 077

# Into the folder of the 'compose.yaml': compose finds its services from there.
cd "$(dirname "$0")/../.."

ZIEL="${ZIEL:-backups}"
BEHALTEN="${BEHALTEN:-7}"
NAME="$ZIEL/crosstally-$(date +%Y-%m-%d-%H%M).sql.gz.enc"

mkdir -p "$ZIEL"

# Write into a side file first, then rename: a dump that breaks off, or a key
# that is missing, must not leave something behind that looks like a backup.
vorlaeufig="$(mktemp "$ZIEL/.abbild.XXXXXX")"
trap 'rm -f "$vorlaeufig"' EXIT

# What anonymous calls leave behind, cleared away first: expired sessions and
# old attempts at the decoy under '/admin/'. Once a day is enough, and the
# backup already runs daily. If it fails, the backup still goes ahead.
docker compose exec -T web python manage.py cleanup ||
    echo "Aufräumen fehlgeschlagen, das Backup läuft weiter." >&2

# Encrypted in a container of its own, like the way back in DEPLOYMENT.md, not
# in the running 'web'. The dump is the way back for a broken database, and it
# is needed most when the application does not run: stopped, or stuck after a
# failed migration. Until 10/2026 'exec' then stopped the backup too.
# Single quotes on purpose: the names are those of the container, not of this shell.
# shellcheck disable=SC2016
docker compose exec -T db sh -c 'pg_dump --username "$POSTGRES_USER" "$POSTGRES_DB"' |
    docker compose run --rm --no-deps -T --entrypoint python web manage.py backup encrypt \
        >"$vorlaeufig"

mv "$vorlaeufig" "$NAME"
trap - EXIT
echo "geschrieben: $NAME"

# Keep only the most recent states. Old backups otherwise fill the disk. The
# unencrypted ones from before age out the same way.
find "$ZIEL" -maxdepth 1 -name 'crosstally-*.sql.gz*' | sort -r | tail -n "+$((BEHALTEN + 1))" |
    xargs -r rm --
