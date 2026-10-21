#!/bin/sh
# The version an image is built with: the build argument, else the checkout's state.
#
# 'image.yml' passes the tag of a release or of the working state as the
# build argument. Whoever builds on the machine itself passes nothing, and
# the Dockerfile asks here. The checkout's state comes from the few files of
# '.git' the build context carries ('.dockerignore'): 'HEAD' names the
# branch, the branch's file or 'packed-refs' the commit. Read by hand,
# because the build stage has no git. Without any of it: "unknown".
#
#   version.sh <build argument> <git directory>
set -eu

argument="${1:-}"
git="${2:-.git}"

if [ -n "$argument" ]; then
    echo "$argument"
    exit 0
fi

hash=""
if [ -f "$git/HEAD" ]; then
    head="$(cat "$git/HEAD")"
    case "$head" in
        ref:*)
            ref="${head#ref: }"
            if [ -f "$git/$ref" ]; then
                hash="$(cat "$git/$ref")"
            elif [ -f "$git/packed-refs" ]; then
                hash="$(grep " $ref\$" "$git/packed-refs" | head -n 1 | cut -c1-40)"
            fi
            ;;
        *)
            hash="$head"
            ;;
    esac
fi

if [ -z "$hash" ]; then
    echo "unknown"
else
    echo "lokal-$(echo "$hash" | cut -c1-7)"
fi
