#!/usr/bin/env bash
# Brings a Pi that already has Desk Talk up to date, keeping its data, pairing key, models and .env:
#
#     bash raspberry_pi/update.sh            get the new code, fill in what a new kernel may have changed, and rebuild
#     bash raspberry_pi/update.sh --dry-run  say what it would do, and change nothing
#
# It needs no sudo on a Pi install.sh set up. On one an older install.sh set up, it asks for sudo once, to keep the logs
# on the SD card, start Desk Talk on boot and give it its own room on the card (raspberry_pi/data_room.sh). Everything
# but the `git pull` and the last step is install.sh --update.
# The last step, after a good build only: Docker's saved build steps are cut down to INSTALL_KEEP_BUILD_STEPS_MB (in
# .env, or .env.example), the ones not used for longest first, so they can't take over the SD card over many updates.
# Docker 28 and newer call that --max-used-space, older ones --keep-storage. It never touches the images or volumes the
# containers use.
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
    bash "$here/install.sh" --update "$@"         # stops here if the build failed (set -e)
    keep_build_steps_under "$here" "$dry_run"
}

# A value from .env, or from .env.example when .env doesn't have it.
setting() {
    local file
    for file in "$1/.env" "$1/.env.example"; do
        if [ -f "$file" ] && grep -q "^$2=" "$file"; then
            awk -v name="$2" 'index($0, name "=") == 1 { print substr($0, length(name) + 2); exit }' "$file"
            return
        fi
    done
    echo "$2 is missing from $1/.env.example." >&2
    return 1
}

keep_build_steps_under() {
    local most said size
    most=$(setting "$1" INSTALL_KEEP_BUILD_STEPS_MB)
    if [ "$2" = 1 ]; then
        echo "Would keep Docker's saved build steps under $most MB: docker builder prune -af --max-used-space ${most}mb"
        return
    fi
    echo "Keeping Docker's saved build steps under $most MB ..."
    docker builder prune -af --max-used-space "${most}mb" > /dev/null 2>&1 && return
    if said=$(docker builder prune -af --keep-storage "${most}mb" 2>&1); then
        return
    fi
    size=$(docker system df --format '{{.Type}}={{.Size}}' 2> /dev/null | awk -F= '$1 == "Build Cache" { print $2 }') || true
    echo "Docker's saved build steps weren't cut down, so they keep growing with each update. Docker said: ${said##*$'\n'}"
    echo "They take ${size:-an unknown amount} now. See with: docker system df. The new containers are running all the same."
}

{ main "$@"; exit; }       # one line, read whole before it runs, so a git pull that changed this file can't matter
