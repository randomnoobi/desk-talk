#!/usr/bin/env bash
# Sets up this Mac to back up the Pi once a week with scripts/backup_pi.sh. Run it **on your Mac**:
#
#     bash scripts/install_weekly_backup.sh                                   every Sunday at 10:00
#     bash scripts/install_weekly_backup.sh --day wednesday --time 21:30     another day and time
#     bash scripts/install_weekly_backup.sh --uninstall                      stop the weekly backup
#
# Flags and their defaults:
#   --day <day>          sunday                         monday ... sunday
#   --time <HH:MM>       10:00                          24-hour time on this Mac's clock
#   --host, --to, --keep (backup_pi.sh's own defaults)  handed to backup_pi.sh as they are
#   --plist-dir <folder> ~/Library/LaunchAgents         where the job's file goes
#   --no-load            write the job's file but don't start it (to look at it first)
#
# It writes a launchd job (a .plist file) and loads it with launchctl, so it runs while you're logged in. A Mac that's
# asleep at that time runs it when it wakes; one that's off skips that week. What each run printed goes to
# backup.log in the backup folder. The job runs this repo's backup_pi.sh where it is now: after moving the repo, run
# this again.
set -euo pipefail

LABEL=local.desktalk.weekly-backup
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BACKUP=$HERE/backup_pi.sh
DAY=sunday
TIME=10:00
PLIST_DIR=$HOME/Library/LaunchAgents
LOAD=1
UNINSTALL=0
BACKUP_ARGS=()
LOG_DIR=${DESKTALK_BACKUP_TO:-$HOME/DeskTalkBackups}     # backup_pi.sh's default backup folder

usage() {
    echo "Usage: install_weekly_backup.sh [--day <day>] [--time <HH:MM>] [--host <name>] [--to <folder>] [--keep <n>]"
    echo "                                [--plist-dir <folder>] [--no-load] [--uninstall]"
}

refuse() {
    echo "$1" >&2
    usage >&2
    exit 2
}

read_flags() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --day) DAY=$(printf '%s' "${2:-}" | tr '[:upper:]' '[:lower:]'); shift ;;
            --time) TIME=${2:-}; shift ;;
            --host|--keep) BACKUP_ARGS+=("$1" "${2:?$1 needs a value}"); shift ;;
            --to) BACKUP_ARGS+=("$1" "${2:?--to needs a folder}"); LOG_DIR=$2; shift ;;
            --plist-dir) PLIST_DIR=${2:?--plist-dir needs a folder}; shift ;;
            --no-load) LOAD=0 ;;
            --uninstall) UNINSTALL=1 ;;
            -h|--help) usage; exit 0 ;;
            *) refuse "Unknown flag: $1" ;;
        esac
        shift
    done
}

# launchd counts the days from Sunday = 0.
weekday() {
    case "$DAY" in
        sunday) echo 0 ;; monday) echo 1 ;; tuesday) echo 2 ;; wednesday) echo 3 ;;
        thursday) echo 4 ;; friday) echo 5 ;; saturday) echo 6 ;;
        *) refuse "--day must be a day of the week, like sunday, not $DAY." ;;
    esac
}

# The text of a value for the .plist file, with &, < and > written the XML way.
xml() {
    local text=$1
    text=${text//&/&amp;}
    text=${text//</&lt;}
    text=${text//>/&gt;}
    printf '%s' "$text"
}

write_plist() {
    local plist=$1 day=$2 hour=$3 minute=$4 arg
    {
        echo '<?xml version="1.0" encoding="UTF-8"?>'
        echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
        echo '<plist version="1.0">'
        echo '<dict>'
        echo "    <key>Label</key><string>$LABEL</string>"
        echo '    <key>ProgramArguments</key>'
        echo '    <array>'
        echo '        <string>/bin/bash</string>'
        echo "        <string>$(xml "$BACKUP")</string>"
        for arg in ${BACKUP_ARGS[@]+"${BACKUP_ARGS[@]}"}; do
            echo "        <string>$(xml "$arg")</string>"
        done
        echo '    </array>'
        echo '    <key>StartCalendarInterval</key>'
        echo "    <dict><key>Weekday</key><integer>$day</integer><key>Hour</key><integer>$hour</integer><key>Minute</key><integer>$minute</integer></dict>"
        echo "    <key>StandardOutPath</key><string>$(xml "$LOG_DIR/backup.log")</string>"
        echo "    <key>StandardErrorPath</key><string>$(xml "$LOG_DIR/backup.log")</string>"
        echo '</dict>'
        echo '</plist>'
    } > "$plist"
}

main() {
    read_flags "$@"
    local plist=$PLIST_DIR/$LABEL.plist domain day hour minute
    domain=gui/$(id -u)
    if [ "$UNINSTALL" = 1 ]; then
        launchctl bootout "$domain/$LABEL" 2> /dev/null || true
        rm -f "$plist"
        echo "The weekly backup is off. The backups already made stay where they are."
        return
    fi
    day=$(weekday)
    [[ "$TIME" =~ ^([01]?[0-9]|2[0-3]):([0-5][0-9])$ ]] || refuse "--time must be like 21:30, not $TIME."
    hour=$((10#${BASH_REMATCH[1]}))
    minute=$((10#${BASH_REMATCH[2]}))
    mkdir -p "$PLIST_DIR" "$LOG_DIR"
    write_plist "$plist" "$day" "$hour" "$minute"
    echo "Wrote $plist: backup_pi.sh every $DAY at $(printf '%02d:%02d' "$hour" "$minute")."
    if [ "$LOAD" = 0 ]; then
        echo "Not started (--no-load). To start it: launchctl bootstrap $domain $plist"
        return
    fi
    launchctl bootout "$domain/$LABEL" 2> /dev/null || true      # an older copy of the job, if there is one
    launchctl bootstrap "$domain" "$plist"
    echo "Started. What each run prints goes to $LOG_DIR/backup.log. To try it now: launchctl kickstart $domain/$LABEL"
}

main "$@"
