#!/usr/bin/env bash
# Saves the Pi's recent logs in one file, to read later or send to whoever is helping. **On the Pi**, from anywhere:
#
#     bash raspberry_pi/get_logs.sh [hours]
#
# It saves the last [hours] (LOGS_HOURS in .env when not given) of each container's log from the journal, the list of
# boots, how the containers are doing, the disk and the temperature, as one dated .tar.gz in LOGS_FOLDER, keeps only the
# newest LOGS_KEEP there, and prints the file's path last. From your Mac, scripts/pi_logs.sh runs this and copies it back.
# It changes nothing on the Pi but that folder, and needs no sudo.
set -euo pipefail

PI_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ENV_FILE=$PI_DIR/.env
EXAMPLE=$PI_DIR/.env.example
NAME_START=desktalk-logs-
WORK=""

say() { printf '%s\n' "$*"; }

usage() {
    say "Usage: get_logs.sh [hours], with hours a whole number like 24." >&2
    exit 2
}

# A value from .env, or .env.example when .env doesn't have that name (as in install.sh).
setting() {
    local file
    for file in "$ENV_FILE" "$EXAMPLE"; do
        if [ -f "$file" ] && grep -q "^$1=" "$file"; then
            awk -v name="$1" 'index($0, name "=") == 1 { print substr($0, length(name) + 2); exit }' "$file"
            return
        fi
    done
    say "$1 is missing from $EXAMPLE." >&2
    exit 1
}

whole_number() {
    [[ "$1" =~ ^[0-9]+$ ]] && [ "$1" -gt 0 ]
}

# Runs a command into a file, after a line saying what it was. A command that fails says so there, and the rest goes on.
into() {
    local file=$1 status=0
    shift
    {
        printf '$ %s\n' "$*"
        "$@" 2>&1 || status=$?
        if [ "$status" != 0 ]; then
            printf '(this failed, with code %s)\n' "$status"
        fi
        printf '\n'
    } >> "$file"
}

# Raspberry Pi OS puts its first user in adm, which may read the whole journal. Without that, journalctl shows only
# this user's own lines, and none of the containers'.
may_read_the_journal() {
    [ "$(id -u)" = 0 ] && return 0
    case " $(id -nG) " in
        *" adm "*|*" systemd-journal "*) return 0 ;;
    esac
    return 1
}

temperature() {
    if command -v vcgencmd > /dev/null 2>&1; then
        vcgencmd measure_temp
        vcgencmd get_throttled           # anything but throttled=0x0: too hot, or too little power, now or since boot
    else
        cat /sys/class/thermal/thermal_zone*/temp
    fi
}

# Only the newest files this script made; anything else in the folder stays.
keep_newest() {
    local folder=$1 keep=$2 old
    # The names hold the date and time, so they sort oldest first.
    while IFS= read -r old; do
        rm -f -- "$folder/$old"
    done < <(cd "$folder" && { ls -1 | grep "^$NAME_START.*\.tar\.gz\$" || true; } | sort \
                 | awk -v keep="$keep" '{ name[NR] = $0 } END { for (i = 1; i <= NR - keep; i++) print name[i] }')
}

main() {
    local hours folder keep name services service journal=1
    [ $# -le 1 ] || usage
    hours=${1:-$(setting LOGS_HOURS)}
    whole_number "$hours" || { say "The hours must be a whole number, like 24, and not $hours." >&2; usage; }
    keep=$(setting LOGS_KEEP)
    whole_number "$keep" || { say "LOGS_KEEP=$keep in raspberry_pi/.env must be a whole number." >&2; exit 1; }
    folder=$(setting LOGS_FOLDER)
    cd "$PI_DIR"
    mkdir -p -- "$folder"
    folder=$(cd -- "$folder" && pwd)

    name=$NAME_START$(hostname -s)-$(date +%Y-%m-%d_%H%M%S)
    WORK=$(mktemp -d)
    trap 'rm -rf "$WORK"' EXIT
    local work=$WORK
    mkdir "$work/$name"

    say "Saving the last $hours hours of logs ..."
    if ! may_read_the_journal; then
        journal=0
        say "$(id -un) isn't in the adm group, so the journal can't be read. Using what Docker still has: the containers running now, not ones a rebuild replaced. To read it all: sudo usermod -aG adm $(id -un), then log in again."
    fi
    services=$(docker compose config --services 2> /dev/null || true)
    for service in $services; do
        if [ "$journal" = 1 ]; then
            # The tag in compose.yaml. The journal keeps the lines of containers a rebuild replaced too.
            into "$work/$name/$service.log" journalctl -t "desktalk-$service" "--since=-${hours}h" -o short-iso --no-pager
        else
            into "$work/$name/$service.log" docker compose logs --since "${hours}h" --timestamps --no-color "$service"
        fi
    done
    into "$work/$name/boots.txt" journalctl --list-boots --no-pager
    into "$work/$name/containers.txt" docker compose ps -a
    into "$work/$name/start-on-boot.txt" systemctl status --no-pager desktalk.service docker.service
    if [ "$journal" = 1 ]; then
        into "$work/$name/start-on-boot.txt" journalctl -u desktalk.service -u docker.service "--since=-${hours}h" -o short-iso --no-pager
    fi
    into "$work/$name/disk.txt" df -h
    into "$work/$name/disk.txt" docker system df
    if [ "$journal" = 1 ]; then
        into "$work/$name/disk.txt" journalctl --disk-usage
    fi
    into "$work/$name/temperature.txt" temperature
    into "$work/$name/temperature.txt" uptime

    tar -czf "$folder/$name.tar.gz" -C "$work" "$name"
    keep_newest "$folder" "$keep"
    say "Saved. To read it: tar -xzf <the file>"
    say "$folder/$name.tar.gz"
}

main "$@"
