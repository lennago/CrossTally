#!/usr/bin/env bash
# Checks the libraries under 'static/vendor/' for known vulnerabilities and
# allowed licenses.
#
#   bin/vendor_audit.sh luecken
#   bin/vendor_audit.sh lizenzen "MIT Apache-2.0 …"
#
# They are in no lock file, so neither 'npm audit' nor 'pip-audit' sees them.
# And Trivy does not recognise a minified file as a library. Which ones they
# are stands only in 'bin/vendor.sh': in the addresses ('npm/<paket>@${VAR}')
# and the versions above them. This script reads from there. A list of its
# own would be forgotten on the next update.
#
# The vulnerabilities come from OSV (osv.dev), which collects the advisories
# for npm packages, the license from the npm registry, for exactly the pinned
# version.

set -euo pipefail

cd "$(dirname "$0")/.."
QUELLE="bin/vendor.sh"

# A single reset connection must not turn the weekly run red. '--retry' alone
# covers only timeouts and server errors, not a handshake that fails.
HOLEN=(curl --fail --silent --show-error --retry 3 --retry-all-errors)

# Only the assignments of the versions ('BOOTSTRAP="5.3.8"'), not the script.
eval "$(grep -E '^[A-Z_]+="[0-9][^"]*"$' "$QUELLE")"

# 'bootstrap 5.3.8', one line per package.
pakete() {
    grep -oE 'npm/(@[a-z0-9._-]+/)?[a-z0-9._-]+@\$\{[A-Z_]+\}' "$QUELLE" | sort -u |
        while IFS= read -r treffer; do
            name="${treffer#npm/}"
            name="${name%@*}"
            variable="${treffer##*\{}"
            variable="${variable%\}}"
            echo "$name ${!variable}"
        done
}

luecken() {
    local anfrage antwort gefunden
    anfrage=$(pakete | jq -R -s -c '
        split("\n") | map(select(. != "") | split(" "))
        | {queries: map({package: {ecosystem: "npm", name: .[0]}, version: .[1]})}')
    antwort=$("${HOLEN[@]}" -X POST -d "$anfrage" https://api.osv.dev/v1/querybatch)
    gefunden=$(jq -r --argjson anfrage "$anfrage" '
        [.results, $anfrage.queries] | transpose[]
        | select(.[0].vulns != null)
        | "\(.[1].package.name) \(.[1].version): \(.[0].vulns | map(.id) | join(", "))"
    ' <<<"$antwort")
    pakete | sed 's/^/geprüft: /'
    if [[ -n "$gefunden" ]]; then
        echo "Bekannte Lücken (Einzelheiten unter https://osv.dev/vulnerability/<ID>):" >&2
        echo "$gefunden" >&2
        return 1
    fi
}

lizenzen() {
    local erlaubt=" $1 " fehler=0 name version lizenz
    while read -r name version; do
        lizenz=$("${HOLEN[@]}" "https://registry.npmjs.org/$name/$version" |
            jq -r '.license // "keine Angabe"')
        if [[ "$erlaubt" == *" $lizenz "* ]]; then
            echo "geprüft: $name $version ($lizenz)"
        else
            echo "Nicht erlaubte Lizenz: $name $version ($lizenz)" >&2
            fehler=1
        fi
    done < <(pakete)
    return "$fehler"
}

case "${1:-}" in
    luecken) luecken ;;
    lizenzen) lizenzen "${2:?Die erlaubten Lizenzen fehlen.}" ;;
    *)
        echo "Aufruf: $0 luecken | lizenzen \"MIT Apache-2.0 …\"" >&2
        exit 2
        ;;
esac
