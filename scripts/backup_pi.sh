#!/usr/bin/env bash
# Copies the Pi's data and its own settings to this Mac. Run it **on your Mac**:
#
#     bash scripts/backup_pi.sh                 back up `ssh pi` into ~/DeskTalkBackups/<day>-<time>/
#     bash scripts/backup_pi.sh --keep 4        ... and keep only the newest 4 backups there
#
# Flags, each with a setting in the environment that does the same, and its default:
#   --host <name>       DESKTALK_BACKUP_HOST    pi                 what `ssh` calls the Pi (a key login, never a password)
#   --pi-dir <folder>   DESKTALK_BACKUP_PI_DIR  desk-talk          the repo on the Pi, from the Pi's home folder
#   --to <folder>       DESKTALK_BACKUP_TO      ~/DeskTalkBackups  where the backups go on this Mac
#   --keep <n>          DESKTALK_BACKUP_KEEP    8                  how many backups to keep; older ones are deleted
#   --with-logs         DESKTALK_BACKUP_LOGS    0                  also copy data/logs (only for looking into a problem)
#
# Each backup is a folder laid out like the repo on the Pi, so restoring is copying it back (raspberry_pi/README.md,
# "Backups"):
#   data/                      the data folder, with object_states.sqlite as one whole file
#   raspberry_pi/.env          the containers' settings
#   raspberry_pi/mine/         your own settings (settings.mine.yaml)
# The database is copied through SQLite's own backup, inside the app container, as the Pi's user: a plain file copy
# of a database being written to can be half old and half new. So the app must be running.
# It only reads on the Pi (apart from a temporary copy in the app container's /tmp, deleted when it's sent). A backup
# that fails part way leaves no folder behind and deletes no old backup.
set -euo pipefail

HOST=${DESKTALK_BACKUP_HOST:-pi}
PI_DIR=${DESKTALK_BACKUP_PI_DIR:-desk-talk}
TO=${DESKTALK_BACKUP_TO:-$HOME/DeskTalkBackups}
KEEP=${DESKTALK_BACKUP_KEEP:-8}
WITH_LOGS=${DESKTALK_BACKUP_LOGS:-0}
DATABASE=object_states.sqlite           # the copy's name in data/, as settings.yaml's object_states.database names it
BACKUP_NAME='[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9]'   # 2026-11-10-210000

# Runs in the app container (`python -`, fed on stdin). It finds the database the way the app does, copies it with
# SQLite's backup into a temporary file and sends that file's bytes back. The copy is one plain file (the app turns
# WAL mode on again when it opens it). Exit 3: there is no database yet.
COPY_DATABASE='
import os, shutil, sqlite3, sys, tempfile
from pathlib import Path
from desktalk.config import load_config

config = load_config(root=Path.cwd())
source = config.resolve(config.object_states.database)
if not source.exists():
    sys.exit(3)
handle, temp = tempfile.mkstemp(prefix="desktalk-backup-", suffix=".sqlite")
os.close(handle)
try:
    live, copy = sqlite3.connect(source), sqlite3.connect(temp)
    try:
        live.backup(copy)
        copy.execute("PRAGMA journal_mode=DELETE")     # one whole file, with no -wal file beside it
    finally:
        copy.close()
        live.close()
    with open(temp, "rb") as made:
        shutil.copyfileobj(made, sys.stdout.buffer)
finally:
    os.unlink(temp)
'

usage() {
    echo "Usage: backup_pi.sh [--host <name>] [--pi-dir <folder>] [--to <folder>] [--keep <n>] [--with-logs]"
}

read_flags() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --host) HOST=${2:?--host needs a name}; shift ;;
            --pi-dir) PI_DIR=${2:?--pi-dir needs a folder}; shift ;;
            --to) TO=${2:?--to needs a folder}; shift ;;
            --keep) KEEP=${2:?--keep needs a number}; shift ;;
            --with-logs) WITH_LOGS=1 ;;
            -h|--help) usage; exit 0 ;;
            *) usage >&2; exit 2 ;;
        esac
        shift
    done
    case "$KEEP" in
        ''|*[!0-9]*|0) echo "--keep must be a number of backups, 1 or more, not $KEEP." >&2; exit 2 ;;
    esac
}

# A command on the Pi. BatchMode: a key login only, so it never stops to ask for a password.
on_pi() {
    ssh -o BatchMode=yes -o ConnectTimeout=15 "$HOST" "$@"
}

# The Pi's repo folder, quoted for the Pi's shell. An absolute folder stays as it is; others are from the Pi's home.
pi_repo() {
    printf '%q' "$PI_DIR"
}

copy_database() {
    local into=$1 status=0
    # The Pi's user, not root (docker compose exec's default): SQLite run as root could leave files the app can't open.
    on_pi "cd $(pi_repo)/raspberry_pi && docker compose exec -T --user \"\$(id -u):\$(id -g)\" app python -" \
        <<< "$COPY_DATABASE" > "$into/data/$DATABASE" || status=$?
    if [ "$status" = 3 ]; then
        rm -f "$into/data/$DATABASE"
        echo "The Pi has no object state database yet, so there was none to copy."
        return
    fi
    if [ "$status" != 0 ]; then
        echo "The database couldn't be copied. Is the app running? Start it on the Pi with: cd ~/$PI_DIR/raspberry_pi && docker compose up -d" >&2
        return 1
    fi
    if command -v sqlite3 > /dev/null && [ "$(sqlite3 -readonly "$into/data/$DATABASE" 'PRAGMA integrity_check')" != ok ]; then
        echo "The database copy came back damaged." >&2
        return 1
    fi
}

copy_files() {
    local into=$1 skip_logs=--exclude=./logs
    if [ "$WITH_LOGS" = 1 ]; then
        skip_logs=
    fi
    # The data folder is where .env's DESKTALK_DATA says (../data unless changed), from raspberry_pi/. A \$ is for the
    # Pi's shell. The Pi's tar ends with 1 when a file changed while it read it (the camera keeps adding story
    # pictures): that file may be cut short, the rest is whole, so it's no failure. 2 is.
    on_pi "cd $(pi_repo)/raspberry_pi && data=\$(sed -n 's/^DESKTALK_DATA=//p' .env 2>/dev/null | tail -n 1) && cd \"\${data:-../data}\" && { tar -cf - --exclude='./$DATABASE' --exclude='./$DATABASE-*' $skip_logs . || [ \$? = 1 ]; }" \
        | tar -xf - -C "$into/data"
    # Only the files that are there: a Pi may have no mine/ folder yet.
    on_pi "cd $(pi_repo)/raspberry_pi && set -- && for f in .env mine; do [ -e \"\$f\" ] && set -- \"\$@\" \"\$f\"; done; tar -cf - \"\$@\" || [ \$? = 1 ]" \
        | tar -xf - -C "$into/raspberry_pi"
}

# Deletes the oldest backups past the newest $KEEP. Only folders named like a backup: nothing else in $TO is touched.
keep_newest() {
    local old
    while IFS= read -r old; do
        [ -n "$old" ] || continue
        rm -rf "${TO:?}/$old"
        echo "Deleted the old backup $old (keeping the newest $KEEP)."
    done < <(cd "$TO" && for d in $BACKUP_NAME; do [ -d "$d" ] && echo "$d"; done | sort \
                 | awk -v keep="$KEEP" '{ name[NR] = $0 } END { for (i = 1; i <= NR - keep; i++) print name[i] }')
}

PARTIAL=                                # the backup being made, until it's whole
main() {
    read_flags "$@"
    local stamp folder
    stamp=$(date +%Y-%m-%d-%H%M%S)
    folder=$TO/$stamp
    PARTIAL=$TO/.$stamp.partial
    mkdir -p "$PARTIAL/data" "$PARTIAL/raspberry_pi"
    trap 'rm -rf "$PARTIAL"' EXIT              # a failed run leaves no half backup
    if ! on_pi true; then
        echo "Couldn't reach the Pi with: ssh $HOST. Is it on, and does ssh $HOST work without a password?" >&2
        exit 1
    fi
    echo "Backing up $HOST:$PI_DIR into $folder ..."
    copy_database "$PARTIAL"
    copy_files "$PARTIAL"
    mv "$PARTIAL" "$folder"
    trap - EXIT
    echo "Saved:"
    (cd "$folder" && du -sh data/* raspberry_pi/.env raspberry_pi/mine 2>/dev/null | sed 's/^/  /')
    echo "All of it: $(du -sh "$folder" | cut -f1)"
    keep_newest
}

main "$@"
