#!/usr/bin/env bash
# Gives Desk Talk its own room on the SD card: a file of a fixed size (DATA_ROOM_SIZE_MB in raspberry_pi/.env), with
# its own filesystem, mounted at the data folder (DESKTALK_DATA) on every boot by
# raspberry_pi/systemd/desktalk-data-room.service. Its space is set aside when it's made, so other files filling the
# card can't take the room the history needs. install.sh runs this, with the containers stopped; run it as your normal
# user, it asks for sudo itself:
#
#     bash raspberry_pi/data_room.sh status          says one word: room, elsewhere, unmounted or none
#     bash raspberry_pi/data_room.sh make [--dry-run]
#
# status:
#   room       the data folder is the mounted room, with the marker (DATA_ROOM_MARKER) in it
#   elsewhere  the data folder is on another disk than the SD card (a USB drive), so it's left alone
#   unmounted  the room's file is there, but it isn't mounted
#   none       there's no room yet
# make:
#   room       grows the room if DATA_ROOM_SIZE_MB is now bigger. It can't be made smaller.
#   elsewhere  does nothing
#   unmounted  starts the service once to mount it. If that doesn't work it stops, and never makes a new room over the
#              old one: that may hold the only history.
#   none       makes the room, copies the data folder into it and checks the copy, then swaps it in. The old folder is
#              kept, renamed, until you delete it. If anything goes wrong, the old folder is left (or put back) as it was.
#   --dry-run  does the same checks, says what it would do, and changes nothing
#
# Paths in .env start from raspberry_pi/, as in install.sh and compose.yaml.
set -euo pipefail

PI_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ENV_FILE=$PI_DIR/.env
EXAMPLE=$PI_DIR/.env.example
TEMPLATE=$PI_DIR/systemd/desktalk-data-room.service
MB=$((1024 * 1024))                             # df -m, fallocate's M and the settings all count MB this way
# mkfs.ext4 and losetup live in sbin, which isn't on a normal user's PATH on Raspberry Pi OS.
PATH=$PATH:/usr/local/sbin:/usr/sbin:/sbin

DRY_RUN=0
TEMP_FOLDER=""                                  # for the service file on its way to /etc/systemd/system
MOUNT_FOLDER=""                                 # where the new room is mounted while the history is copied in
MOUNTED=0
STAGE=""                                        # new: <file>.new is being made; swapped: the old folder was set aside
OLD=""                                          # where the old data folder went

say() { printf '%s\n' "$*"; }
would() { say "Would $*"; }
stop() { say "Stopping: $*" >&2; exit 1; }

usage() {
    say "Usage: data_room.sh status | make [--dry-run]"
    exit 2
}

# A path or a command's word, written so it can be pasted into a shell as it is.
quoted() {
    printf '%q' "$1"
}

# ── Reading settings (as install.sh does) ──

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

env_has() {
    [ -f "$1" ] && grep -q "^$2=" "$1"
}

# A whole number of MB.
number() {
    local value
    value=$(setting "$1")
    [[ "$value" =~ ^[0-9]+$ ]] || stop "$1=$value in raspberry_pi/.env isn't a whole number of MB."
    printf '%s' "$value"
}

# A path from .env, which starts from raspberry_pi/, as a full path with no links in it (as findmnt and losetup give
# them). It may not be there yet.
full_path() {
    local path=$1 parent
    case "$path" in
        /*) ;;
        *) path=$PI_DIR/$path ;;
    esac
    if [ -d "$path" ]; then
        (cd -- "$path" && pwd -P)
        return
    fi
    parent=$(dirname -- "$path")
    if [ -d "$parent" ]; then
        printf '%s/%s\n' "$(cd -- "$parent" && pwd -P)" "$(basename -- "$path")"
        return
    fi
    printf '%s\n' "$path"
}

# ── What's there ──

# The loop device, when the data folder is the room's file mounted: findmnt says what's mounted there, and losetup
# which loop devices that file backs.
room_device() {
    local source loop
    [ -e "$IMAGE" ] || return 1
    source=$(findmnt -n -o SOURCE --mountpoint "$DATA" 2> /dev/null) || return 1
    while IFS= read -r loop; do
        if [ -n "$loop" ] && [ "$loop" = "$source" ]; then
            printf '%s' "$loop"
            return 0
        fi
    done < <(losetup -l -n -O NAME -j "$IMAGE" 2> /dev/null || true)
    return 1
}

# The disk a folder is on. One that isn't made yet will be on the disk of the nearest folder above it.
disk_of() {
    local path=$1
    while [ ! -e "$path" ]; do
        path=$(dirname -- "$path")
    done
    stat -c %d "$path"
}

# "elsewhere" is a data folder on another disk than / that isn't the room mounted (a USB drive, say): stat's device
# numbers differ, and findmnt and losetup don't show the room's file mounted there.
status() {
    if room_device > /dev/null; then
        if [ -e "$DATA/$MARKER" ]; then
            say room
            return
        fi
    elif [ "$(disk_of "$DATA")" != "$(disk_of /)" ]; then
        say elsewhere
        return
    fi
    # The room's file without the room mounted (or mounted without its marker) is "unmounted": it may hold the only
    # history, so it's never made again.
    if [ -e "$IMAGE" ]; then
        say unmounted
    else
        say none
    fi
}

free_mb() {
    df -Pm "$(dirname -- "$IMAGE")" | awk 'NR == 2 { print $4 }'
}

used_mb() {
    if [ -d "$DATA" ]; then
        { du -sm -- "$DATA" 2> /dev/null || true; } | awk 'NR == 1 { print $1 }'
    else
        echo 0
    fi
}

# Does the file have all its space set aside on the card, and not only as it's written (a sparse file)?
all_set_aside() {
    local blocks block_size
    read -r blocks block_size < <(stat -c '%b %B' "$1") || return 1
    [[ "$blocks$block_size" =~ ^[0-9]+$ ]] || return 1
    [ "$((blocks * block_size))" -ge "$(($2 * MB))" ]
}

empty_folder() {
    [ ! -e "$1" ] || { [ -d "$1" ] && [ -z "$(ls -A -- "$1")" ]; }
}

# The newest old data folder, set aside by an earlier move.
newest_old_folder() {
    local folder newest=""
    for folder in "$DATA".before-room-*; do
        if [ -d "$folder" ]; then
            newest=$folder
        fi
    done
    printf '%s' "$newest"
}

# ── The service ──

# A path as one word in a service file: systemd reads \ and " in quotes, and % and $ itself.
unit_word() {
    local value=$1
    value=${value//\\/\\\\}
    value=${value//\"/\\\"}
    value=${value//\%/%%}
    value=${value//\$/\$\$}
    printf '%s' "$value"
}

# The file from raspberry_pi/systemd/ with each blank (@NAME@) filled in from the environment name FILL_NAME, as
# install.sh does it. Stops if one is left: a unit with a blank in it would fail at the next boot.
fill_in() {
    local text
    text=$(awk '{
        line = $0
        for (name in ENVIRON) {
            if (index(name, "FILL_") != 1) continue
            mark = "@" substr(name, 6) "@"
            out = ""
            while ((at = index(line, mark)) > 0) {
                out = out substr(line, 1, at - 1) ENVIRON[name]
                line = substr(line, at + length(mark))
            }
            line = out line
        }
        print line
    }' "$1")
    if grep -Eq '@[A-Z_]+@' <<< "$text"; then
        stop "$1 has a blank this script doesn't fill in: $(grep -Eo '@[A-Z_]+@' <<< "$text" | head -n 1)"
    fi
    printf '%s' "$text"
}

unit_text() {
    FILL_IMAGE=$(unit_word "$IMAGE") FILL_DATA=$(unit_word "$DATA") fill_in "$TEMPLATE"
}

# Is $2 there already, with the text $1?
in_place() {
    [ -f "$2" ] && [ "$(cat "$2")" = "$1" ]
}

# Puts the service file in place if it isn't, and switches it on, so the room is mounted on every boot.
set_up_service() {
    local text new
    text=$(unit_text)
    if ! in_place "$text" "$UNIT_FILE"; then
        if [ -z "$TEMP_FOLDER" ]; then
            TEMP_FOLDER=$(mktemp -d)
        fi
        new=$TEMP_FOLDER/$UNIT
        printf '%s\n' "$text" > "$new"
        sudo mkdir -p -- "$(dirname -- "$UNIT_FILE")"
        sudo install -m 644 "$new" "$UNIT_FILE"
        sudo systemctl daemon-reload
        say "Installed $UNIT_FILE, which mounts the room at the data folder on every boot, before Docker starts."
    fi
    sudo systemctl enable "$UNIT"
}

# ── Cleaning up ──

leave() {
    if [ -n "$MOUNT_FOLDER" ]; then
        if [ "$MOUNTED" = 1 ] && ! sudo umount -- "$MOUNT_FOLDER"; then
            say "The new room is still mounted at $MOUNT_FOLDER. Unmount it with: sudo umount $(quoted "$MOUNT_FOLDER")" >&2
        else
            rmdir -- "$MOUNT_FOLDER" 2> /dev/null || true
        fi
    fi
    case "$STAGE" in
        new) rm -f -- "$IMAGE.new" ;;
        swapped) put_back ;;
    esac
    if [ -n "$TEMP_FOLDER" ]; then
        rm -rf -- "$TEMP_FOLDER"
    fi
}
trap leave EXIT

# The room couldn't be swapped in: the old folder goes back, as it was, and the new room goes. Only an empty folder is
# ever removed (rmdir), so if the room is still mounted there, nothing is lost: it says what to do by hand.
put_back() {
    STAGE=""
    sudo systemctl stop "$UNIT" > /dev/null 2>&1 || true
    sudo systemctl disable "$UNIT" > /dev/null 2>&1 || true
    if [ -n "$OLD" ] && [ -d "$OLD" ]; then
        if [ -e "$DATA" ] && ! sudo rmdir -- "$DATA"; then
            say "The old data folder couldn't be put back, because $DATA isn't an empty folder. Your history is safe in $OLD. Put it back by hand with: sudo umount $(quoted "$DATA"); sudo rmdir $(quoted "$DATA"); mv $(quoted "$OLD") $(quoted "$DATA")" >&2
            return
        fi
        if ! mv -- "$OLD" "$DATA"; then
            say "The old data folder couldn't be put back. Your history is safe in $OLD. Put it back by hand with: mv $(quoted "$OLD") $(quoted "$DATA")" >&2
            return
        fi
        say "The old data folder is back at $DATA, as it was." >&2
    fi
    rm -f -- "$IMAGE" "$IMAGE.new"
}

# ── Making, mounting and growing ──

# A move cut short (by a power cut, say) between setting the old folder aside and naming the new file leaves
# <file>.new, an empty data folder and the old folder with the history. That's left for you. Any other <file>.new is from
# a try that stopped before the data folder was touched, and goes.
check_leftover() {
    local old
    [ -e "$IMAGE.new" ] || return 0
    old=$(newest_old_folder)
    if [ -n "$old" ] && empty_folder "$DATA"; then
        local back=""
        if [ -e "$DATA" ]; then
            back="sudo rmdir $(quoted "$DATA"); "
        fi
        stop "an earlier move into Desk Talk's room was cut short, so the data folder $DATA is empty. Your history is safe in $old. Put it back with: ${back}mv $(quoted "$old") $(quoted "$DATA"); rm -f $(quoted "$IMAGE.new")   and run this again."
    fi
    if [ "$DRY_RUN" = 1 ]; then
        would "remove $IMAGE.new, left by an earlier try that stopped part-way."
        return
    fi
    rm -f -- "$IMAGE.new"
}

make_room() {
    local wanted smallest reserve free size used new=$IMAGE.new
    check_leftover
    wanted=$(number DATA_ROOM_SIZE_MB)
    smallest=$(number DATA_ROOM_SMALLEST_MB)
    reserve=$(number INSTALL_MIN_DISK_BUILT_MB)
    free=$(free_mb)
    size=$((free - reserve))
    if [ "$wanted" -lt "$size" ]; then
        size=$wanted
    fi
    if [ "$size" -lt "$smallest" ]; then
        stop "there is $free MB free on the SD card. Desk Talk's own room needs at least $smallest MB, and $reserve MB must stay free for updates, so free $((smallest + reserve - free)) MB more and run this again."
    fi
    used=$(used_mb)
    [ "$used" -lt "$size" ] || stop "the data folder holds $used MB, which doesn't fit in a room of $size MB. Set DATA_ROOM_SIZE_MB in raspberry_pi/.env higher, or free space on the SD card, and run this again."
    if [ "$size" -lt "$wanted" ]; then
        say "There's only room on the SD card for $size MB, not the $wanted MB in DATA_ROOM_SIZE_MB: $reserve MB must stay free for updates."
    fi
    if [ "$DRY_RUN" = 1 ]; then
        would "make Desk Talk's own room on the SD card: a file of $size MB at $IMAGE, mounted at $DATA. It would copy the $used MB in the data folder into it, check the copy, keep the old folder as $DATA.before-room-<date and time>, and install $UNIT_FILE to mount the room on every boot."
        return
    fi
    [ -d "$DATA" ] || mkdir -p -- "$DATA"

    # 1 to 5: a new file, which is all that goes if anything fails.
    STAGE=new
    say "Making Desk Talk's own room on the SD card: $size MB at $IMAGE ..."
    fallocate -l "${size}M" "$new"
    all_set_aside "$new" "$size" || stop "the SD card didn't set aside all of the room's $size MB (it made a sparse file), so it isn't safe to use. The data folder is as it was."
    mkfs.ext4 -q -F -m 0 -E "root_owner=$(id -u):$(id -g)" "$new"
    MOUNT_FOLDER=$(mktemp -d)
    sudo mount -o loop "$new" "$MOUNT_FOLDER"
    MOUNTED=1
    say "Copying the data folder ($used MB) into it ..."
    sudo cp -a -- "$DATA/." "$MOUNT_FOLDER/"
    copy_checks_out || stop "the copy in the new room isn't the same as the data folder, so it isn't used. The data folder is as it was."
    sudo touch -- "$MOUNT_FOLDER/$MARKER"
    sudo umount -- "$MOUNT_FOLDER"
    MOUNTED=0
    rmdir -- "$MOUNT_FOLDER"
    MOUNT_FOLDER=""

    # 6 to 8: the swap. From here a failure puts the old folder back.
    OLD=$DATA.before-room-$(date +%Y-%m-%d-%H%M%S)
    STAGE=swapped
    mv -- "$DATA" "$OLD"
    sudo mkdir -m 755 -- "$DATA"        # root's, so nothing is written to the card here if the room isn't mounted
    mv -- "$new" "$IMAGE"
    set_up_service
    sudo systemctl start "$UNIT" || true
    if [ "$(status)" != room ]; then
        put_back
        stop "the new room couldn't be mounted at $DATA, so it's removed and the old data folder is back where it was. See why with: journalctl -u ${UNIT%.service}"
    fi
    STAGE=""
    say "Desk Talk's own room is ready: $size MB at $IMAGE, mounted at $DATA on every boot. The history was copied into it and checked."
    say "The old data folder is kept at $OLD. Once Desk Talk works, delete it with: rm -rf $(quoted "$OLD")"
}

# The copy is the same as the data folder, but for the new filesystem's own lost+found.
copy_checks_out() {
    local differences
    differences=$(sudo diff -r -- "$DATA" "$MOUNT_FOLDER" 2>&1) || true
    [ -z "$differences" ] || [ "$differences" = "Only in $MOUNT_FOLDER: lost+found" ]
}

mount_room() {
    local old
    if [ "$DRY_RUN" = 1 ]; then
        would "mount Desk Talk's room ($IMAGE) at $DATA, by starting $UNIT."
        return
    fi
    say "Desk Talk's room ($IMAGE) isn't mounted. Mounting it at $DATA ..."
    set_up_service
    sudo systemctl restart "$UNIT" || true
    if [ "$(status)" = room ]; then
        say "Mounted Desk Talk's room at $DATA."
        return
    fi
    old=$(newest_old_folder)
    stop "Desk Talk's room $IMAGE isn't mounted at $DATA (or its marker $MARKER isn't in it). It may hold the only history, so a new room isn't made over it. See why with: journalctl -u ${UNIT%.service}${old:+. An older data folder is kept at $old}"
}

grow_room() {
    local wanted current more free reserve device
    wanted=$(number DATA_ROOM_SIZE_MB)
    current=$(($(stat -c %s "$IMAGE") / MB))
    if [ "$wanted" -lt "$current" ]; then
        say "Desk Talk's room is $current MB, and DATA_ROOM_SIZE_MB is now $wanted. Making it smaller isn't supported, so it stays at $current MB."
        return
    fi
    if [ "$wanted" = "$current" ]; then
        say "Desk Talk's own room: $current MB, mounted at $DATA."
        return
    fi
    reserve=$(number INSTALL_MIN_DISK_BUILT_MB)
    free=$(free_mb)
    more=$((wanted - current))
    if [ "$((free - reserve))" -lt "$more" ]; then
        say "Desk Talk's room can't grow from $current to $wanted MB now: there is $free MB free on the SD card, and $reserve MB must stay free for updates. Free $((more + reserve - free)) MB and run this again. It stays at $current MB."
        return
    fi
    if [ "$DRY_RUN" = 1 ]; then
        would "grow Desk Talk's room from $current to $wanted MB."
        return
    fi
    device=$(room_device)
    fallocate -l "${wanted}M" "$IMAGE"
    all_set_aside "$IMAGE" "$wanted" || stop "the SD card didn't set aside all of the room's new $wanted MB, so it stays at $current MB."
    if ! { sudo losetup -c "$device" && sudo resize2fs "$device"; }; then
        stop "the room's file grew to $wanted MB, but its filesystem didn't. Finish it by hand with: sudo losetup -c $device && sudo resize2fs $device"
    fi
    say "Desk Talk's room grew from $current to $wanted MB."
}

check_not_root() {
    [ "$(id -u)" != 0 ] || stop "run this without sudo, as your normal user. It asks for sudo itself where it needs it."
}

main() {
    local what=${1:-}
    if [ $# -gt 0 ]; then
        shift
    fi
    case "$what" in
        status) [ $# = 0 ] || usage ;;
        make)
            while [ $# -gt 0 ]; do
                case "$1" in
                    --dry-run) DRY_RUN=1 ;;
                    *) usage ;;
                esac
                shift
            done ;;
        *) usage ;;
    esac
    IMAGE=$(full_path "$(setting DATA_ROOM_FILE)")
    DATA=$(full_path "$(setting DESKTALK_DATA)")
    MARKER=$(setting DATA_ROOM_MARKER)
    UNIT_FILE=$(setting INSTALL_DATA_ROOM_SERVICE_FILE)
    UNIT=$(basename -- "$UNIT_FILE")
    if [ "$what" = status ]; then
        status
        return
    fi
    check_not_root
    case "$(status)" in
        room) grow_room ;;
        elsewhere) say "The data folder $DATA is on another disk than the SD card, so it gets no room of its own and is left as it is." ;;
        unmounted) mount_room ;;
        none) make_room ;;
    esac
}

main "$@"
