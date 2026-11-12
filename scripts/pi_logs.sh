#!/usr/bin/env bash
# Gets the Pi's recent logs onto your Mac. **On your Mac**, from anywhere:
#
#     bash scripts/pi_logs.sh [--host pi] [--hours 24] [--repo desk-talk] [--to pi_logs]
#
#   --host <name>   the Pi, as you'd type it after ssh (LOGS_SSH_HOST in raspberry_pi/.env.example, or DESKTALK_PI,
#                   the same name the Pi tests use)
#   --hours <n>     how many hours of logs (LOGS_HOURS)
#   --repo <folder> where the repo is on the Pi, from your home folder there (LOGS_PI_REPO)
#   --to <folder>   where the file goes on your Mac (LOGS_MAC_FOLDER, from the repo's top folder; it isn't in git)
#
# It runs raspberry_pi/get_logs.sh on the Pi over ssh, copies the .tar.gz it makes back with scp, and prints its path
# last. Read it with: tar -xzf <the file>
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ENV_FILE=$ROOT/raspberry_pi/.env
EXAMPLE=$ROOT/raspberry_pi/.env.example

say() { printf '%s\n' "$*"; }

usage() {
    say "Usage: pi_logs.sh [--host <name>] [--hours <n>] [--repo <folder on the Pi>] [--to <folder here>]" >&2
    exit 2
}

# A value from raspberry_pi/.env, or .env.example when .env doesn't have that name (as in install.sh).
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

main() {
    local host hours repo to said remote
    host=${DESKTALK_PI:-$(setting LOGS_SSH_HOST)}
    hours=$(setting LOGS_HOURS)
    repo=$(setting LOGS_PI_REPO)
    to=$(setting LOGS_MAC_FOLDER)
    while [ $# -gt 0 ]; do
        case "$1" in
            --host|--hours|--repo|--to)
                [ $# -ge 2 ] || { say "$1 needs a value." >&2; usage; }
                case "$1" in
                    --host) host=$2 ;;
                    --hours) hours=$2 ;;
                    --repo) repo=$2 ;;
                    --to) to=$2 ;;
                esac
                shift ;;
            *) say "I don't know the flag $1." >&2; usage ;;
        esac
        shift
    done
    if ! [[ "$hours" =~ ^[0-9]+$ ]] || [ "$hours" -eq 0 ]; then
        say "The hours must be a whole number, like 24, and not $hours." >&2
        usage
    fi
    cd "$ROOT"
    mkdir -p -- "$to"
    to=$(cd -- "$to" && pwd)

    say "Asking $host for the last $hours hours of logs ..."
    if ! said=$(ssh "$host" bash "$(printf '%q' "$repo/raspberry_pi/get_logs.sh")" "$hours"); then
        say "$said"
        say "Couldn't get the logs from $host over ssh. Check that the Pi is on and that this works: ssh $host" >&2
        exit 1
    fi
    say "$said"
    remote=$(printf '%s\n' "$said" | tail -n 1)
    case "$remote" in
        /*.tar.gz) ;;
        *) say "The Pi didn't say where it saved the logs." >&2; exit 1 ;;
    esac
    scp "$host:$remote" "$to" || { say "The file couldn't be copied from $host:$remote." >&2; exit 1; }
    say "$to/$(basename "$remote")"
}

main "$@"
