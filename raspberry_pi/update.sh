#!/usr/bin/env bash
# Brings a Pi that already has Desk Talk up to date, keeping its data, pairing key, models and .env:
#
#     bash raspberry_pi/update.sh            get the new code, fill in what a new kernel may have changed, and rebuild
#     bash raspberry_pi/update.sh --dry-run  say what it would do, and change nothing
#
# It needs no sudo once Docker works without it. Everything but the `git pull` is install.sh --update.
set -euo pipefail

# All inside a function: git pull may change this very file, and bash reads a script as it goes.
main() {
    local here root dry_run=0 flag
    for flag in "$@"; do
        if [ "$flag" = --dry-run ]; then
            dry_run=1
        fi
    done
    here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
    root=$(dirname "$here")
    if ! git -C "$root" rev-parse --is-inside-work-tree > /dev/null 2>&1; then
        echo "This folder isn't a git checkout, so no new code was fetched. Using the code that's here."
    elif [ "$dry_run" = 1 ]; then
        echo "Would get the new code with git pull."
    elif ! git -C "$root" pull --ff-only; then
        echo "git pull didn't work (no internet, or files changed by hand), so this uses the code that's here."
    fi
    exec bash "$here/install.sh" --update "$@"
}

main "$@"
