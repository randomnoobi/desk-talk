#!/usr/bin/env bash
# Writes this boot's numbers for the camera's kinds of device into raspberry_pi/.env. Run it from anywhere:
#
#     bash raspberry_pi/device_numbers.sh
#
# The kernel gives the media and dma_heap kinds their numbers at each boot, in whatever order their drivers load, so
# any boot can change them, with the same kernel too. compose.yaml lets the camera container open only the numbers in
# .env (CAMERA_MEDIA_MAJOR, CAMERA_DMA_HEAP_MAJOR), and a container keeps the rules it was made with, so with an old
# number it can't open the camera at all. desktalk.service runs this at each boot, before `docker compose up -d`,
# which then makes the camera container again if a number changed. By hand, in raspberry_pi:
#
#     bash device_numbers.sh && docker compose up -d
#
# A driver may still be loading when this runs, so it waits up to DEVICE_NUMBERS_WAIT_SEC for a kind the kernel doesn't
# list yet (in .env, or .env.example). It needs no sudo and no network, and changes nothing else in .env.
set -euo pipefail

PI_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ENV_FILE=$PI_DIR/.env
EXAMPLE=$PI_DIR/.env.example
PROC=${DESKTALK_PROC:-/proc}                # only tests change this

# Each name in .env, and the kind of device its number is for.
NAMES="CAMERA_MEDIA_MAJOR CAMERA_DMA_HEAP_MAJOR"
kind_of() {
    case "$1" in
        CAMERA_MEDIA_MAJOR) printf 'media' ;;
        CAMERA_DMA_HEAP_MAJOR) printf 'dma_heap' ;;
    esac
}

say() { printf '%s\n' "$*"; }
stop() { say "Stopping: $*" >&2; exit 1; }

# The new .env while set_env makes it. If the script stops before it takes .env's place, it is removed.
NEW_ENV=""
remove_new_env() {
    if [ -n "$NEW_ENV" ]; then
        rm -f "$NEW_ENV"
    fi
}
trap remove_new_env EXIT
trap 'exit 1' HUP INT TERM                  # so a stop by signal removes it too

env_has() {
    [ -f "$1" ] && grep -q "^$2=" "$1"
}

# A value from .env, or .env.example when .env doesn't have that name.
setting() {
    local file
    for file in "$ENV_FILE" "$EXAMPLE"; do
        if env_has "$file" "$1"; then
            awk -v name="$1" 'index($0, name "=") == 1 { print substr($0, length(name) + 2); exit }' "$file"
            return
        fi
    done
    stop "$1 is missing from $EXAMPLE."
}

# A number of seconds, which may have a fraction, like 30 or 0.5.
seconds_setting() {
    local value
    value=$(setting "$1")
    [[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] || stop "$1=$value in raspberry_pi/.env isn't a number of seconds."
    printf '%s' "$value"
}

# The number the kernel gave a kind of character device. Block devices come after, and can have the same names.
device_major() {
    awk -v kind="$1" '/^Block devices:/ { exit } $2 == kind { print $1; exit }' "$PROC/devices" 2> /dev/null || true
}

all_listed() {
    local name
    for name in $NAMES; do
        [ -n "$(device_major "$(kind_of "$name")")" ] || return 1
    done
}

# Waits until the kernel lists every kind, or the wait is over.
wait_for_drivers() {
    local wait every checks
    wait=$(seconds_setting DEVICE_NUMBERS_WAIT_SEC) || exit 1
    every=$(seconds_setting DEVICE_NUMBERS_CHECK_EVERY_SEC) || exit 1
    checks=$(awk -v wait="$wait" -v every="$every" 'BEGIN {
        if (every <= 0) { print -1; exit }
        n = int(wait / every); if (n * every < wait) n++; print n }')
    [ "$checks" -ge 0 ] || stop "DEVICE_NUMBERS_CHECK_EVERY_SEC in raspberry_pi/.env must be more than 0."
    while ! all_listed && [ "$checks" -gt 0 ]; do
        sleep "$every"
        checks=$((checks - 1))
    done
}

# Swaps a whole new .env in, so a power cut can't leave it half written.
set_env() {
    NEW_ENV=$(mktemp "$ENV_FILE.XXXXXX")
    if env_has "$ENV_FILE" "$1"; then
        NEW_VALUE=$2 awk -v name="$1" -F= '$1 == name { print name "=" ENVIRON["NEW_VALUE"]; next } { print }' \
            "$ENV_FILE" > "$NEW_ENV"
    else
        cat "$ENV_FILE" > "$NEW_ENV"
        if [ -s "$NEW_ENV" ] && [ -n "$(tail -c 1 "$NEW_ENV")" ]; then
            printf '\n' >> "$NEW_ENV"
        fi
        printf '%s=%s\n' "$1" "$2" >> "$NEW_ENV"
    fi
    chmod 644 "$NEW_ENV"
    sync "$NEW_ENV" 2> /dev/null || true    # on the card before it takes .env's place
    mv "$NEW_ENV" "$ENV_FILE"
    NEW_ENV=""
    sync "$PI_DIR" 2> /dev/null || true     # and the swap itself, so it lasts through a power cut
}

check() {
    local name=$1 kind major current="" in_env=false waited
    kind=$(kind_of "$name")
    major=$(device_major "$kind")
    if env_has "$ENV_FILE" "$name"; then
        in_env=true
        current=$(setting "$name")
    fi
    if [ -z "$major" ]; then
        waited=$(setting DEVICE_NUMBERS_WAIT_SEC)
        if [ -z "$current" ]; then
            if $in_env; then current="(empty in .env)"; else current="(not in .env)"; fi
        fi
        say "$name: kept $current: $PROC/devices listed no $kind devices in $waited seconds."
    elif [ "$major" = "$current" ]; then
        say "$name: $major, unchanged."
    elif ! $in_env; then
        set_env "$name" "$major"
        say "$name: $major (added to .env)"
    elif [ -z "$current" ]; then
        set_env "$name" "$major"
        say "$name: $major (filled in: .env had no number for it)"
    else
        set_env "$name" "$major"
        say "$name: $current → $major (this boot numbered the camera's devices differently)"
    fi
}

main() {
    local name
    if [ ! -f "$ENV_FILE" ]; then
        say "There's no $ENV_FILE yet, so there are no numbers to check. install.sh makes it."
        exit 0
    fi
    wait_for_drivers
    for name in $NAMES; do
        check "$name"
    done
}

main "$@"
