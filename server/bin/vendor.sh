#!/usr/bin/env bash
# Fetches the third-party browser libraries into 'static/vendor/'.
#
# They are served by the application itself (WhiteNoise), no longer from the
# CDN: the web interface thus runs without internet too, and no call goes to
# jsdelivr with the machine's address.
#
# Every file is checked against its SRI hash, the same that used to stand in
# the 'integrity' attribute. If it does not match, the script aborts before
# anything is overwritten. To update: change version and hash below (the hash
# is on the package's jsdelivr page, or
# 'curl -s URL | openssl dgst -sha384 -binary | openssl base64 -A'), then run
# again and click through the interface once.
#
# With every library comes its license text ('LICENSE.txt'), checked likewise.
# MIT and Apache-2.0 require that it goes along, and the header of the
# minified files is not enough for that: for Tom Select it is one line, and
# Popper, which is in Bootstrap's bundle, it does not name at all. Whoever
# bumps a version checks whether the license changed and updates
# 'THIRD_PARTY_NOTICES.md' at the root of the repo.
#
# The line 'sourceMappingURL' at the end of the file is replaced: we do not
# ship the maps, and 'collectstatic' with ManifestStaticFilesStorage would
# abort on the reference to a file that does not exist. In its place stands a
# note that the file is modified. Apache-2.0 (§ 4b) requires that for Tom
# Select, and the others get it for uniformity's sake. Checked beforehand, on
# the original.

set -euo pipefail

cd "$(dirname "$0")/.."
ZIEL="static/vendor"

BOOTSTRAP="5.3.8"
TOM_SELECT="2.4.3"
SORTABLE="1.15.6"
# Sits inside 'bootstrap.bundle.min.js'. Bootstrap names the version in its
# 'package.json' under 'peerDependencies'.
POPPER="2.11.8"

# Location | address | SRI hash
DATEIEN=(
    "bootstrap/bootstrap.min.css|https://cdn.jsdelivr.net/npm/bootstrap@${BOOTSTRAP}/dist/css/bootstrap.min.css|sha384-sRIl4kxILFvY47J16cr9ZwB07vP4J8+LH7qKQnuqkuIAvNWLzeN8tE5YBujZqJLB"
    "bootstrap/bootstrap.bundle.min.js|https://cdn.jsdelivr.net/npm/bootstrap@${BOOTSTRAP}/dist/js/bootstrap.bundle.min.js|sha384-FKyoEForCGlyvwx9Hj09JcYn3nv7wiPVlz7YYwJrWVcXK/BmnVDxM+D2scQbITxI"
    "tom-select/tom-select.bootstrap5.min.css|https://cdn.jsdelivr.net/npm/tom-select@${TOM_SELECT}/dist/css/tom-select.bootstrap5.min.css|sha384-oNaL7h8UJDHZ8k9XxysQRnqKxysB2lmlq7uU5Ic9CsOy89U4US8mT9pGYFf0ygyM"
    "tom-select/tom-select.complete.min.js|https://cdn.jsdelivr.net/npm/tom-select@${TOM_SELECT}/dist/js/tom-select.complete.min.js|sha384-A4TlP4zHVPP9oVIQ8pTVaV6MhvBkDx1sRpmEUfAlO5e7xpodrdr6uNmUV7alXAWW"
    "sortablejs/Sortable.min.js|https://cdn.jsdelivr.net/npm/sortablejs@${SORTABLE}/Sortable.min.js|sha384-HZZ/fukV+9G8gwTNjN7zQDG0Sp7MsZy5DDN6VfY3Be7V9dvQpEpR2jF2HlyFUUjU"
    "bootstrap/LICENSE.txt|https://cdn.jsdelivr.net/npm/bootstrap@${BOOTSTRAP}/LICENSE|sha384-WyeULmOarnkxIWJQ6Byifrdakj+J3Z1ONoIjyqqdmUvfwML66D4izPruHb7zDZbG"
    "bootstrap/LICENSE-popper.txt|https://cdn.jsdelivr.net/npm/@popperjs/core@${POPPER}/LICENSE.md|sha384-RRbMTt7ysPZNz3DqLd2FeQKDYlDRic+XMyjnqyEHZ3Hx45ZNiCWPTNPlsbBD0z8Z"
    "tom-select/LICENSE.txt|https://cdn.jsdelivr.net/npm/tom-select@${TOM_SELECT}/LICENSE|sha384-5satProukSRU9mtb/RQ1HOpcAeGYYos/9oWj08AzdCEtajTkP75gHUXPdcXWWezT"
    "sortablejs/LICENSE.txt|https://cdn.jsdelivr.net/npm/sortablejs@${SORTABLE}/LICENSE|sha384-mevydalsL4d7yB2GsEFX1+hNJfVYvZK8+0MuGrlwEvk9zc1vAgLWOcvFi/Yc5sB+"
)

# Applies in CSS and in JavaScript. '/*!' is kept by minifiers, in case
# someone sends the files through one again.
VERMERK="/*! Angepasst für dieses Projekt: Verweis auf die Source-Map entfernt (bin/vendor.sh). */"

ZWISCHEN="$(mktemp -d)"
trap 'rm -rf "$ZWISCHEN"' EXIT

# Fetch and check everything first, only then write: a wrong hash must not
# leave a half-updated folder behind.
for eintrag in "${DATEIEN[@]}"; do
    IFS="|" read -r ablage adresse erwartet <<<"$eintrag"
    mkdir -p "$ZWISCHEN/$(dirname "$ablage")"
    curl --fail --silent --show-error --location "$adresse" -o "$ZWISCHEN/$ablage"
    tatsaechlich="sha384-$(openssl dgst -sha384 -binary "$ZWISCHEN/$ablage" | openssl base64 -A)"
    if [[ "$tatsaechlich" != "$erwartet" ]]; then
        echo "Hash stimmt nicht: $ablage" >&2
        echo "  erwartet:    $erwartet" >&2
        echo "  bekommen:    $tatsaechlich" >&2
        exit 1
    fi
    sed -i "s|^.*sourceMappingURL=.*\$|$VERMERK|" "$ZWISCHEN/$ablage"
    echo "geprüft: $ablage"
done

for eintrag in "${DATEIEN[@]}"; do
    IFS="|" read -r ablage _ _ <<<"$eintrag"
    mkdir -p "$ZIEL/$(dirname "$ablage")"
    cp "$ZWISCHEN/$ablage" "$ZIEL/$ablage"
done
echo "Nach $ZIEL geschrieben."
