#!/usr/bin/env bash
# Installs Desk Talk on a Raspberry Pi. Run it as your normal user, from anywhere:
#
#     bash raspberry_pi/install.sh [--tz Europe/Paris] [--server <address>]
#
# Safe to run again: it only changes what needs it, and never touches the pairing key, the models or your own settings
# (raspberry_pi/mine/settings.mine.yaml). It also sets the Pi up to start Desk Talk by itself when it boots
# (raspberry_pi/systemd/desktalk.service), and to keep its journal, the containers' logs too, on the SD card
# (raspberry_pi/systemd/journald-desktalk.conf). And it gives Desk Talk its own room on the SD card, a file of a fixed
# size mounted at the data folder, so other files filling the card can't stop the history being saved
# (raspberry_pi/data_room.sh, raspberry_pi/systemd/desktalk-data-room.service). The first time, it stops the app and
# the camera, and data_room.sh copies the data folder into the room, checks the copy and keeps the old folder, renamed.
# After that the data folder is left as it is. While the move runs, DATA_ROOM_ON in .env already says yes, so if a power
# cut stops it part-way, the app starts at the next boot saving nothing, never a new history on the empty folder the room
# mounts at. Such a move cut short (data_room.sh status: leftover) is finished before anything else: until it is, the
# app saves nothing, the Pi isn't linked to a cloud server, and no folder is made where the room mounts.
#
#   --tz <zone>        your time zone (normally taken from the Pi)
#   --server <address> link this Pi to your cloud server, using the code from its website. The Pi needs no cloud.
#                      The code goes in an environment name, as the website's command does it:
#                        DESKTALK_LINK_CODE=<code> bash raspberry_pi/install.sh --server <address>
#                      Linking comes first, because the code runs out in minutes and the build takes far longer.
#   --code <code>      the code as a flag, for typing by hand. Anyone on the Pi can see it in the list of running
#                      programs for as long as this runs, which the environment name avoids.
#   --keep-hostname    keep the Pi's name even if it's still the stock one, and remember that
#   --reboot           restart the Pi at the end, if it needs that (it says so either way)
#   --dry-run          do the same checks, say what it would do, and change nothing
#   --update           what update.sh uses: skip Docker's install and the parts that need sudo. Only on a Pi that
#                      doesn't keep its journal on the card, start Desk Talk on boot or give it its own room yet does it
#                      ask for sudo, once, to set those up: the containers log to the journal, so without it their logs
#                      would be lost.
#
# Its numbers are in the "install script" part of .env.example.
# It exits with 1 if the install failed, and with 3 if the install worked but the link to the cloud server didn't.
set -euo pipefail

PI_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(dirname "$PI_DIR")
ENV_FILE=$PI_DIR/.env
EXAMPLE=$PI_DIR/.env.example
MINE_EXAMPLE=$PI_DIR/settings.mine.example.yaml
MINE_FILE=$PI_DIR/mine/settings.mine.yaml      # the user's own settings: not in git, so a git pull never trips on it
CHECKSUMS=$PI_DIR/model_checksums.txt
PROC=${DESKTALK_PROC:-/proc}                # only tests change this
ZONES=/usr/share/zoneinfo
MEMORY_FLAGS="cgroup_enable=memory cgroup_memory=1"
OLD_PROJECT=desktalk-detector               # the detector's own compose project, from before all three ran together
OLD_CAMERA_SERVICE=desk-talk-camera         # the camera program outside Docker, from before that
IMAGES="desktalk-app:local desktalk-camera:local desktalk-detector:local"     # the ones compose.yaml builds
WRITERS="app camera"                        # the app, and the camera that depends on it: stopped while the room is made
LINK_FAILED_EXIT=3

TZ_FLAG=""
SERVER=""
CODE=""
KEEP_HOSTNAME=0
REBOOT_FLAG=0
DRY_RUN=0
UPDATE=0

USE_SUDO=0                                  # Docker needs sudo in this run
DOCKER_READY=1                              # 0 only in a dry run, when Docker can't be asked anything
REBOOT_NEEDED=0
LATER=""                                    # lines kept for the end, so they aren't lost in Docker's build output
NEW_NAMES=" "                               # the names .env didn't have when this run started
LINK_FAILED=0
TEMP_FOLDER=""
SERVICE_CHANGED=0                           # desktalk.service was installed or changed in this run
SETUP_SUDO=0                                # an update run may use sudo, to set up what an older install didn't
START_FAILED=0
ROOM=""                                     # what data_room.sh status said: room, elsewhere, unmounted, leftover or none
ROOM_STOPPED=""                             # why data_room.sh make stopped, in its own words
ROOM_FAILED=0                               # the room wasn't made or mounted: it stops at the end, with ROOM_STOPPED
ROOM_ON=""                                  # the DATA_ROOM_ON this run wrote (or in a dry run would have)
ROOM_ON_CHANGED=0                           # DATA_ROOM_ON in .env changed in this call of put_room_on
WRITERS_STOPPED=0                           # the app and the camera are stopped: if this stops, they start again

say() { printf '%s\n' "$*"; }
would() { say "Would $*"; }
later() { LATER="$LATER$*"$'\n'; }
stop() { say "Stopping: $*" >&2; exit 1; }
# On the way out, however it ends: Ctrl-C, the SSH line dropping, or a stop. Desk Talk is never left stopped.
leave() {
    if [ "$WRITERS_STOPPED" = 1 ]; then
        WRITERS_STOPPED=0
        trap '' HUP INT TERM                # nothing cuts this short
        say "This stopped while the app and the camera were stopped, so they are being started again, as they were ..." || true
        # DATA_ROOM_ON said yes for the move: the app gets what's there now (a move that didn't happen means no).
        put_room_on "$(room_on_for "$(data_room_ status 2> /dev/null || printf '%s' "$ROOM")")" 2> /dev/null || true
        if compose_ up -d --no-build; then
            say "The containers are started again." || true
        else
            say "The containers couldn't be started again. Start them with: cd $(quoted "$PI_DIR") && docker compose up -d --no-build" || true
        fi
    fi
    if [ -n "$TEMP_FOLDER" ]; then
        rm -rf "$TEMP_FOLDER"
    fi
}
trap leave EXIT
trap 'exit 129' HUP                         # each by way of leave
trap 'exit 130' INT
trap 'exit 143' TERM

usage() {
    say "Usage: [DESKTALK_LINK_CODE=<code>] install.sh [--tz <zone>] [--server <address>] [--keep-hostname] [--reboot] [--dry-run]"
    exit 2
}

# A path or address, written so it can be pasted into a shell as it is.
quoted() {
    printf '%q' "$1"
}

read_flags() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --tz|--server|--code)
                [ $# -ge 2 ] || { say "$1 needs a value."; usage; }
                case "$1" in
                    --tz) TZ_FLAG=$2 ;;
                    --server) SERVER=$2 ;;
                    --code) CODE=$2 ;;
                esac
                shift ;;
            --keep-hostname) KEEP_HOSTNAME=1 ;;
            --reboot) REBOOT_FLAG=1 ;;
            --dry-run) DRY_RUN=1 ;;
            --update) UPDATE=1 ;;
            *) say "I don't know the flag $1."; usage ;;
        esac
        shift
    done
    if [ "$UPDATE" = 1 ] && { [ -n "$SERVER" ] || [ -n "$CODE" ]; }; then
        say "update.sh doesn't link. To link this Pi, use install.sh."
        usage
    fi
    if [ "$UPDATE" = 0 ] && [ -z "$CODE" ]; then
        CODE=${DESKTALK_LINK_CODE:-}
    fi
    unset DESKTALK_LINK_CODE                # so nothing this starts gets it, but the linker
    if { [ -n "$SERVER" ] && [ -z "$CODE" ]; } || { [ -z "$SERVER" ] && [ -n "$CODE" ]; }; then
        say "--server and the code go together: the website gives you both."
        usage
    fi
    if [ -n "$TZ_FLAG" ] && ! good_zone "$TZ_FLAG"; then
        say "$TZ_FLAG isn't a time zone. They look like Europe/Paris; see them with: timedatectl list-timezones"
        exit 2
    fi
}

good_zone() {
    [[ "$1" =~ ^[A-Za-z0-9_+/-]+$ ]] || return 1
    [ ! -d "$ZONES" ] || [ -f "$ZONES/$1" ]       # -f: Europe alone is a folder there
}

# ── Reading settings ──

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

# A value from a settings file, by its place: yaml_value file pi.detector.model. Only for plain `name: value` lines,
# which is all these are. (The Pi's own Python may not have a YAML reader until the containers are built.)
yaml_value() {
    [ -f "$1" ] || return 0
    awk -v path="$2" '
        BEGIN { count = split(path, part, "."); depth = 1 }
        /^[[:space:]]*(#|$)/ { next }
        {
            match($0, /^ */)
            level = RLENGTH / 2 + 1
            if (level < depth) depth = level
            if (level != depth) next
            line = substr($0, RLENGTH + 1)
            if (index(line, part[depth] ":") != 1) next
            if (depth < count) { depth++; next }
            value = substr(line, length(part[depth]) + 2)
            sub(/^ +/, "", value)
            sub(/ +#.*$/, "", value)
            gsub(/["\047]/, "", value)
            print value
            exit
        }' "$1"
}

# From the user's own settings on top of the Pi's, on top of config/settings.yaml, as the app and the camera read them.
pi_setting() {
    local value file
    for file in "$MINE_FILE" "$PI_DIR/settings.pi.yaml" "$ROOT/config/settings.yaml"; do
        value=$(yaml_value "$file" "$1")
        if [ -n "$value" ]; then
            break
        fi
    done
    printf '%s' "$value"
}

# ── Checks ──

check_not_root() {
    [ "$(id -u)" != 0 ] || stop "run this without sudo, as your normal user. It asks for sudo itself where it needs it."
}

check_64_bit() {
    local machine bits
    machine=$(uname -m)
    bits=$(getconf LONG_BIT)
    case "$machine" in
        armv6*|armv7*|armhf|i?86) stop "this is a 32-bit system ($machine). Desk Talk needs Raspberry Pi OS 64-bit." ;;
        aarch64|arm64) ;;
        *) stop "this is for a Raspberry Pi with the 64-bit system (arm64), and this machine is $machine." ;;
    esac
    # A 64-bit processor can run the 32-bit system, and the first line can't tell.
    [ "$bits" = 64 ] || stop "this is a 32-bit system on a 64-bit processor. Desk Talk needs Raspberry Pi OS 64-bit."
}

# Can Docker be asked anything yet? The docker group only starts at the next login, so a second run soon after the first
# may need sudo. A dry run never uses sudo, so there the answer may be no.
can_ask_docker() {
    docker compose version > /dev/null 2>&1 || return 1
    docker info > /dev/null 2>&1 && return 0
    [ "$DRY_RUN" = 0 ] || return 1
    USE_SUDO=1
}

images_built() {
    local image
    can_ask_docker || return 1
    for image in $IMAGES; do
        docker_ image inspect "$image" > /dev/null 2>&1 || return 1
    done
}

# How many MB of images and build steps Docker holds already: a build that stopped part-way doesn't need that room again.
docker_holds_mb() {
    can_ask_docker || { echo 0; return; }
    { docker_ system df --format '{{.Type}}={{.Size}}' 2> /dev/null || true; } | awk -F= '
        $1 == "Images" || $1 == "Build Cache" {
            size = $2 + 0
            if ($2 ~ /kB$/) size /= 1000
            else if ($2 ~ /GB$/) size *= 1000
            else if ($2 ~ /TB$/) size *= 1000000
            else if ($2 !~ /MB$/) size = 0
            total += size
        }
        END { print int(total) }'
}

free_disk() {
    df -Pm "$ROOT" | awk 'NR == 2 { print $4 }'
}

# The first build needs far more room than running this again, or rebuilding what changed.
check_disk() {
    local need free holds
    free=$(free_disk)
    if images_built; then
        need=$(setting INSTALL_MIN_DISK_BUILT_MB)
        [ "$free" -ge "$need" ] || stop "there is $free MB of free disk and it needs $need MB. Free some space and run this again."
        say "Free disk: $free MB (needs $need)."
        return
    fi
    need=$(setting INSTALL_MIN_DISK_MB)
    holds=$(docker_holds_mb)
    if [ "$((free + holds))" -lt "$need" ]; then
        if [ "$holds" -gt 0 ]; then
            stop "the first build needs $need MB. There is $free MB of free disk, and Docker holds $holds MB from earlier builds. Free some space and run this again, or clear those and start the build over with: sudo docker builder prune -af"
        fi
        stop "the first build needs $need MB and there is $free MB of free disk. Free some space, or use a bigger card, and run this again."
    fi
    say "Free disk: $free MB, and Docker holds $holds MB already (needs $need)."
}

check_memory() {
    local need memory good
    need=$(setting INSTALL_MIN_MEMORY_MB)
    good=$(setting INSTALL_GOOD_MEMORY_MB)
    memory=$(awk '/^MemTotal:/ { print int($2 / 1024) }' "$PROC/meminfo")
    [ "$memory" -ge "$need" ] || stop "this Pi has $memory MB of memory and Desk Talk needs $need MB."
    say "Memory: $memory MB (needs $need)."
    if [ "$memory" -lt "$good" ]; then
        later "This Pi has $memory MB of memory. Set DETECTOR_MEMORY_LIMIT in raspberry_pi/.env lower, and use a small model."
    fi
}

check_sound() {
    local cards devices word
    cards=$(setting SOUND_CARDS_FILE)
    devices=$(setting SOUND_DEVICES)
    [ -e "$cards" ] || stop "$cards is missing, so the Pi's sound is switched off and the app can't start. Add dtparam=audio=on to the Pi's boot settings, restart it, and run this again."
    [ -e "$devices" ] || stop "$devices is missing, so the Pi's sound is switched off and the app can't start. Add dtparam=audio=on to the Pi's boot settings, restart it, and run this again."
    word=$(pi_setting notifications.sound_card)
    if ! grep -qi -- "$word" "$cards"; then
        later "No $word speaker is plugged in. That's fine: voice starts when one's plugged in."
    fi
}

check_old_camera_program() {
    if systemctl is-active --quiet "$OLD_CAMERA_SERVICE" 2> /dev/null; then
        later "The old camera program ($OLD_CAMERA_SERVICE) is running outside Docker and holds the camera. Stop it with: sudo systemctl disable --now $OLD_CAMERA_SERVICE"
    fi
}

# ── .env ──

# The number the kernel gave a kind of character device. Block devices come after, and can have the same names.
device_major() {
    awk -v kind="$1" '/^Block devices:/ { exit } $2 == kind { print $1; exit }' "$PROC/devices" 2> /dev/null || true
}

group_number() {
    { getent group "$1" || true; } | cut -d: -f3
}

this_pis_zone() {
    local zone=""
    zone=$(timedatectl show -p Timezone --value 2> /dev/null || true)
    if [ -z "$zone" ] && [ -r /etc/timezone ]; then
        zone=$(head -n 1 /etc/timezone)
    fi
    if good_zone "$zone"; then
        printf '%s' "$zone"
    fi
}

# Each step is checked, as set -e doesn't stop a function called inside an if or an && list: a failed write leaves .env
# as it was and fails.
set_env() {
    local new
    new=$(mktemp "$ENV_FILE.XXXXXX") || return 1
    NEW_VALUE=$2 awk -v name="$1" -F= '$1 == name { print name "=" ENVIRON["NEW_VALUE"]; next } { print }' "$ENV_FILE" \
        > "$new" && chmod 644 "$new" && mv "$new" "$ENV_FILE" || { rm -f -- "$new"; return 1; }
}

# Copy .env.example's names that .env lacks, each with the comment above it. What's already in .env stays.
add_new_names() {
    if [ ! -f "$ENV_FILE" ]; then
        local new
        new=$(mktemp "$ENV_FILE.XXXXXX")
        cp "$EXAMPLE" "$new"
        chmod 644 "$new"
        mv "$new" "$ENV_FILE"
        return
    fi
    local added
    # FILENAME, not NR == FNR: with an empty .env, that would take the example for .env too, and add nothing.
    added=$(awk -v env_file="$ENV_FILE" '
        FILENAME == env_file { if (split($0, kv, "=") > 1) have[kv[1]] = 1; next }
        /^#/ { comment = comment $0 "\n"; next }
        /^[[:space:]]*$/ { comment = ""; next }
        { split($0, kv, "="); if (!(kv[1] in have)) printf "\n%s%s\n", comment, $0; comment = "" }' "$ENV_FILE" "$EXAMPLE")
    if [ -n "$added" ]; then
        if [ -s "$ENV_FILE" ] && [ -n "$(tail -c 1 "$ENV_FILE")" ]; then
            printf '\n' >> "$ENV_FILE"
        fi
        printf '%s\n' "$added" >> "$ENV_FILE"
    fi
}

# Set a name, or in a dry run say it would be.
put() {
    say "  $1=$2"
    if [ "$DRY_RUN" = 1 ]; then
        if [ "$(setting "$1")" != "$2" ]; then
            would "write $1=$2 in .env."
        fi
    else
        set_env "$1" "$2"
    fi
}

# For what this Pi says only the first time: running again keeps what's there.
put_if_new() {
    case "$NEW_NAMES" in
        *" $1 "*) put "$1" "$2" ;;
        *)
            local current
            current=$(setting "$1")
            say "  $1=$current (kept from .env; this Pi says $2)" ;;
    esac
}

# A kernel update can change these, so they are always filled in.
put_major() {
    local major
    major=$(device_major "$2")
    if [ -n "$major" ]; then
        put "$1" "$major"
    else
        say "  $1=$(setting "$1") (kept: /proc/devices lists no $2 devices yet)"
        later "The Pi lists no $2 devices, so $1 stays as it is. If the camera can't be found, restart the Pi and run this again."
    fi
}

write_env() {
    local name group zone
    while IFS= read -r name; do
        env_has "$ENV_FILE" "$name" || NEW_NAMES="$NEW_NAMES$name "
    done < <(awk -F= '/^[A-Za-z_]/ { print $1 }' "$EXAMPLE")
    if [ "$DRY_RUN" = 1 ]; then
        [ -f "$ENV_FILE" ] || would "make raspberry_pi/.env from .env.example."
    else
        add_new_names
    fi

    say "This Pi has:"
    put_major CAMERA_MEDIA_MAJOR media
    put_major CAMERA_DMA_HEAP_MAJOR dma_heap
    group=$(group_number video)
    if [ -n "$group" ]; then
        put_if_new CAMERA_VIDEO_GROUP "$group"
    fi
    group=$(group_number audio)
    if [ -n "$group" ]; then
        put_if_new PI_AUDIO_GROUP "$group"
    fi
    put_if_new PI_USER_ID "$(id -u)"
    put_if_new PI_GROUP_ID "$(id -g)"
    if [ -n "$TZ_FLAG" ]; then
        put TZ "$TZ_FLAG"
    else
        zone=$(this_pis_zone)
        if [ -n "$zone" ]; then
            put_if_new TZ "$zone"
        else
            say "  TZ=$(setting TZ) (the Pi doesn't say its time zone. Change it with --tz)"
        fi
    fi
}

# ── Your own settings ──

# raspberry_pi/mine/settings.mine.yaml starts as a copy of settings.mine.example.yaml, which has only comments. Once it
# is there it is the user's: nothing here ever changes it, and update.sh's git pull doesn't see it (it is in .gitignore).
make_my_settings() {
    if [ -f "$MINE_FILE" ]; then
        say "Your own settings: raspberry_pi/mine/settings.mine.yaml (kept as it is)."
    elif [ "$DRY_RUN" = 1 ]; then
        would "make raspberry_pi/mine/settings.mine.yaml, for your own settings, from settings.mine.example.yaml."
    else
        mkdir -p "$(dirname "$MINE_FILE")"
        cp "$MINE_EXAMPLE" "$MINE_FILE" || stop "raspberry_pi/mine/settings.mine.yaml can't be made. If Docker made the folder raspberry_pi/mine, run: sudo rmdir raspberry_pi/mine"
        chmod 644 "$MINE_FILE"
        say "Made raspberry_pi/mine/settings.mine.yaml for your own settings, such as llm.ai_url."
    fi
}

# ── Docker ──

docker_() {
    if [ "$USE_SUDO" = 1 ]; then
        sudo docker "$@"
    else
        docker "$@"
    fi
}

compose_() {
    docker_ compose "$@"
}

download() {
    curl -fsSL --retry "$(setting INSTALL_DOWNLOAD_TRIES)" --connect-timeout "$(setting INSTALL_DOWNLOAD_CONNECT_SEC)" \
        --max-time "$(setting INSTALL_DOWNLOAD_MAX_SEC)" --speed-limit "$(setting INSTALL_DOWNLOAD_SLOWEST_BYTES_SEC)" \
        --speed-time "$(setting INSTALL_DOWNLOAD_STALL_SEC)" -o "$1" "$2"
}

install_docker() {
    local url script
    url=$(setting INSTALL_DOCKER_SCRIPT)
    if [ "$DRY_RUN" = 1 ]; then
        would "install Docker with $url."
        return
    fi
    say "Installing Docker with $url ..."
    make_temp_folder
    script=$TEMP_FOLDER/get-docker.sh
    download "$script" "$url" || stop "Docker couldn't be downloaded from $url. Check the internet and run this again."
    sudo sh "$script" || stop "Docker's install didn't finish. Its messages are above."
}

add_to_docker_group() {
    local me
    me=$(id -un)
    case " $(id -nG "$me") " in
        *" docker "*) return ;;
    esac
    if [ "$DRY_RUN" = 1 ]; then
        would "add $me to the docker group."
        return
    fi
    sudo usermod -aG docker "$me"
    later "You were added to the docker group. That starts at your next login: until then, docker commands need sudo."
}

find_docker() {
    if ! docker compose version > /dev/null 2>&1; then
        [ "$UPDATE" = 0 ] || stop "Docker isn't installed here, so there's nothing to update. Run raspberry_pi/install.sh first."
        install_docker
        if [ "$DRY_RUN" = 1 ]; then
            would "add $(id -un) to the docker group."
            DOCKER_READY=0
            return
        fi
    fi
    if [ "$UPDATE" = 0 ]; then
        add_to_docker_group
    fi
    if docker info > /dev/null 2>&1; then
        return
    fi
    # The docker group only starts at the next login, so this run may have to use sudo.
    if [ "$DRY_RUN" = 1 ]; then
        would "use sudo for docker in this run: this login can't use it by itself."
        DOCKER_READY=0
        return
    fi
    USE_SUDO=1
    docker_ info > /dev/null 2>&1 || stop "Docker isn't running. Try: sudo systemctl start docker"
}

# ── Starting on boot, and the journal ──

# A file from raspberry_pi/systemd/ with each blank (@NAME@) filled in from the environment name FILL_NAME. Stops if
# one is left: a unit with a blank in it would fail at the next boot.
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

# A size or a time for systemd, like 200M or 30day.
plain_setting() {
    local value
    value=$(setting "$1")
    [[ "$value" =~ ^[0-9]+[A-Za-z]*$ ]] || stop "$1=$value in raspberry_pi/.env isn't a number with its unit, like 200M or 30day."
    printf '%s' "$value"
}

# These run inside $(...), where bash doesn't stop on errors by itself: hence each `|| exit 1`, so a bad value stops the
# install and isn't left out. systemd reads %h, %u ... in a unit as names to fill in, and %% as a plain %.
service_text() {
    local wait retry
    wait=$(plain_setting SERVICE_START_WAIT_SEC) || exit 1
    retry=$(plain_setting SERVICE_RETRY_SEC) || exit 1
    FILL_PI_USER=$(id -un) FILL_COMPOSE_FOLDER=${PI_DIR//\%/%%} FILL_DOCKER=$(command -v docker || true) \
        FILL_SERVICE_START_WAIT_SEC=$wait FILL_SERVICE_RETRY_SEC=$retry fill_in "$PI_DIR/systemd/desktalk.service"
}

journal_text() {
    local most keep span
    most=$(plain_setting JOURNAL_MAX_USE) || exit 1
    keep=$(plain_setting JOURNAL_KEEP) || exit 1
    span=$(plain_setting JOURNAL_FILE_SPAN) || exit 1
    FILL_JOURNAL_MAX_USE=$most FILL_JOURNAL_KEEP=$keep FILL_JOURNAL_FILE_SPAN=$span \
        fill_in "$PI_DIR/systemd/journald-desktalk.conf"
}

# Is $2 there already, with the text $1?
in_place() {
    [ -f "$2" ] && [ "$(cat "$2")" = "$1" ]
}

# Puts the text $1 at $2, owned by root, for systemd to read.
put_system_file() {
    local new
    make_temp_folder
    new=$TEMP_FOLDER/$(basename "$2")
    printf '%s\n' "$1" > "$new"
    sudo mkdir -p "$(dirname "$2")"
    sudo install -m 644 "$new" "$2"
}

# The containers' own restart rule brings them back at boot too, as long as Docker starts: so Docker and containerd
# are switched on here as well (Docker's install normally does that already).
set_up_start_on_boot() {
    local text=$1 file unit off=""
    file=$(setting INSTALL_SERVICE_FILE)
    if [ "$UPDATE" = 1 ] && [ -f "$file" ]; then
        in_place "$text" "$file" || later "Starting by itself on boot isn't set up the way this version wants ($file). To set it up, run once: bash $(quoted "$PI_DIR/install.sh")"
        return
    fi
    if [ "$UPDATE" = 1 ] && [ "$SETUP_SUDO" = 0 ]; then
        later "IMPORTANT: Desk Talk isn't set up to start by itself when the Pi boots, because sudo didn't work. After a power cut the containers come back only if Docker brings them back by itself. To set it up, run once: bash $(quoted "$PI_DIR/install.sh")"
        return
    fi
    for unit in docker.service containerd.service "$(basename "$file")"; do
        systemctl is-enabled --quiet "$unit" 2> /dev/null || off="$off $unit"
    done
    if [ "$DRY_RUN" = 1 ]; then
        in_place "$text" "$file" || would "install $file, which starts the containers when the Pi boots."
        if [ -n "$off" ]; then
            would "switch on$off, so they start when the Pi boots."
        fi
        return
    fi
    if ! in_place "$text" "$file"; then
        put_system_file "$text" "$file"
        sudo systemctl daemon-reload
        SERVICE_CHANGED=1
        say "Installed $file, which starts the containers when the Pi boots."
    fi
    if [ -n "$off" ]; then
        # shellcheck disable=SC2086    # one name each
        if sudo systemctl enable $off; then
            say "Switched on$off, so they start when the Pi boots."
        else
            later "systemd couldn't switch on$off, so Desk Talk may not start by itself after a power cut. Try: sudo systemctl enable$off"
        fi
    fi
}

# Once the containers are built: the service runs as this user with no build, so this shows it may use Docker without
# sudo, as it must at boot.
try_start_on_boot() {
    local unit how=start
    [ "$DRY_RUN" = 0 ] || return 0
    [ "$UPDATE" = 0 ] || [ "$SERVICE_CHANGED" = 1 ] || return 0       # an update tries it only when it installed it
    unit=$(basename "$(setting INSTALL_SERVICE_FILE)")
    if systemctl is-active --quiet "$unit" 2> /dev/null; then
        [ "$SERVICE_CHANGED" = 1 ] || return 0
        how=restart                         # to try the new one
    fi
    if sudo systemctl "$how" "$unit"; then
        say "$unit started the containers, as it will when the Pi boots."
    else
        START_FAILED=1
        later "$unit couldn't start the containers, so after a power cut they may not come back by themselves. See why with: journalctl -u $unit"
    fi
}

# Raspberry Pi OS keeps the journal in memory only, so the logs of the boot before a power cut are gone. This keeps it
# on the card, within the size and the age in .env.
keep_the_journal() {
    local text=$1 file
    file=$(setting INSTALL_JOURNALD_FILE)
    in_place "$text" "$file" && return
    if [ "$UPDATE" = 1 ] && [ -f "$file" ]; then
        later "The journal isn't kept on the SD card the way this version wants ($file). To set it up, run once: bash $(quoted "$PI_DIR/install.sh")"
        return
    fi
    if [ "$UPDATE" = 1 ] && [ "$SETUP_SUDO" = 0 ]; then
        later "IMPORTANT: the logs aren't kept on the SD card, because sudo didn't work. Raspberry Pi OS keeps them in memory only, so the containers' logs from before a restart or a power cut are lost. To keep them, run once: bash $(quoted "$PI_DIR/install.sh")"
        return
    fi
    if [ "$DRY_RUN" = 1 ]; then
        would "install $file, so the journal, the containers' logs too, stays on the SD card: at most $(setting JOURNAL_MAX_USE), for $(setting JOURNAL_KEEP)."
        return
    fi
    put_system_file "$text" "$file"
    if ! sudo systemctl restart systemd-journald; then
        later "The journal couldn't be restarted, so it keeps its old settings until the Pi restarts."
        return
    fi
    sudo journalctl --flush || true          # what's in memory so far goes to the card too
    say "Installed $file: the journal, the containers' logs too, stays on the SD card, at most $(setting JOURNAL_MAX_USE), for $(setting JOURNAL_KEEP)."
}

# A Pi set up by an older install.sh may have neither file. An update sets them up then, as install.sh would, so it
# can't leave the logs worse than before: compose.yaml sends the containers' logs to the journal, and without the
# journal file Raspberry Pi OS keeps that in memory only. That needs sudo, so it is asked for once, and only then.
sudo_to_set_up() {
    local what=""
    [ -f "$(setting INSTALL_SERVICE_FILE)" ] || what="start Desk Talk by itself when it boots"
    [ -f "$(setting INSTALL_JOURNALD_FILE)" ] || what="${what:+$what, and to }keep its logs on the SD card (Raspberry Pi OS keeps them in memory only, so they'd be lost at a restart or a power cut)"
    case "$ROOM" in
        none|unmounted) what="${what:+$what, and to }give Desk Talk its own room on the SD card (so other files filling the card can't stop its history being saved)" ;;
        leftover) what="${what:+$what, and to }finish moving Desk Talk's history into its own room on the SD card (a move that was cut short)" ;;
    esac
    [ -n "$what" ] || return 0
    if [ "$DRY_RUN" = 1 ]; then
        would "ask for sudo once, to set this Pi up to $what."
        SETUP_SUDO=1
        return
    fi
    say "This Pi isn't set up yet to $what. That needs sudo, this once, so it may ask for your password. Later updates need none."
    if sudo -v; then
        SETUP_SUDO=1
    fi
}

# Both files are filled in first, so a value systemd couldn't read stops this before either is installed.
set_up_systemd() {
    local service journal
    service=$(service_text)
    journal=$(journal_text)
    if [ "$UPDATE" = 1 ]; then
        sudo_to_set_up
    fi
    set_up_start_on_boot "$service"
    keep_the_journal "$journal"
}

# ── The boot settings ──

# Swaps the whole file in, after keeping a copy, so a power cut can't leave it empty: an empty cmdline.txt stops the Pi
# booting. $2 is its new text.
replace_boot_file() {
    local file=$1 new="$1.new"
    [ -e "$file.before-desktalk" ] || sudo cp "$file" "$file.before-desktalk"
    printf '%s\n' "$2" | sudo tee "$new" > /dev/null
    sudo sync
    sudo mv "$new" "$file"
    sudo sync
}

set_memory_flags() {
    local file line flag added="" running
    file=$(setting INSTALL_BOOT_CMDLINE)
    if [ ! -f "$file" ]; then
        later "$file is missing, so the containers' memory limits may not work on this Pi."
        return
    fi
    line=$(head -n 1 "$file")
    for flag in $MEMORY_FLAGS; do
        case " $line " in
            *" $flag "*) ;;
            *) line="$line $flag"; added="$added $flag" ;;
        esac
    done
    if [ -n "$added" ]; then
        if [ "$DRY_RUN" = 1 ]; then
            would "add$added to $file, and the Pi would need a restart."
        else
            replace_boot_file "$file" "$line"
            say "Added$added to $file, for the containers' memory limits."
        fi
        REBOOT_NEEDED=1
        return
    fi
    running=$(cat "$PROC/cmdline" 2> /dev/null || true)
    for flag in $MEMORY_FLAGS; do
        case " $running " in
            *" $flag "*) ;;
            *) REBOOT_NEEDED=1 ;;
        esac
    done
}

# On by default on Raspberry Pi OS since Bookworm, so this normally changes nothing. It counts only a line that applies
# to this Pi: a line after [cm5], say, applies to that board alone. A camera set by hand is the owner's choice.
enable_camera() {
    local file
    file=$(setting INSTALL_BOOT_CONFIG)
    [ -f "$file" ] || return 0
    if awk '/^\[/ { for_all = ($0 ~ /^\[all\]/); seen = 1 }
            /^camera_auto_detect=1/ && (for_all || !seen) { found = 1 }
            END { exit !found }' "$file"; then
        return
    fi
    if grep -q '^camera_auto_detect=0' "$file" || grep -Eq '^dtoverlay=(imx|ov)' "$file"; then
        later "The camera is set by hand in $file (camera_auto_detect=0 or a dtoverlay= camera line), so that's left as it is."
        return
    fi
    if [ "$DRY_RUN" = 1 ]; then
        would "add camera_auto_detect=1 under [all] in $file, and the Pi would need a restart."
    else
        replace_boot_file "$file" "$(cat "$file")"$'\n[all]\ncamera_auto_detect=1'
        say "Switched the camera on in $file."
    fi
    REBOOT_NEEDED=1
}

# ── The name ──

the_address() {
    printf 'http://%s.local:%s' "$1" "$(setting APP_HOST_PORT)"
}

# Without any domain after a dot: on the Wi-Fi the Pi is always <this>.local.
short_hostname() {
    local name
    name=$(hostname)
    printf '%s' "${name%%.*}"
}

# Only a Pi still called the stock name gets renamed. Anyone who named theirs has chosen.
set_hostname() {
    local now wanted
    now=$(short_hostname)
    wanted=$(setting INSTALL_HOSTNAME)
    [[ "$wanted" =~ ^[A-Za-z0-9-]+$ ]] || stop "INSTALL_HOSTNAME=$wanted isn't a name a Pi can have (letters, numbers and - only)."
    PI_NAME=$now
    if [ "$now" != "$(setting INSTALL_STOCK_HOSTNAME)" ] || [ "$now" = "$wanted" ]; then
        return
    fi
    if [ "$KEEP_HOSTNAME" = 1 ]; then
        if [ "$DRY_RUN" = 1 ]; then
            would "keep the name $now, and remember it in .env."
        else
            set_env INSTALL_HOSTNAME "$now"
        fi
        return
    fi
    PI_NAME=$wanted
    if [ "$DRY_RUN" = 1 ]; then
        would "rename this Pi from $now to $wanted: its address becomes $(the_address "$wanted"), and $now.local stops working."
        return
    fi
    sudo hostnamectl set-hostname "$wanted"
    sudo sed -i -E "s/^(127\\.0\\.1\\.1[[:space:]]+).*/\\1$wanted/" /etc/hosts
    sudo systemctl try-restart avahi-daemon || true
    later "This Pi is now called $wanted. Its address is $(the_address "$wanted"), and $now.local no longer works: change it anywhere you saved it, such as your Mac's ~/.ssh/config."
}

# The app can't see the Pi's name from inside Docker, so it's written down each time for compose to hand over.
write_public_name() {
    put PUBLIC_NAME "$PI_NAME.local"
}

# ── The old setup ──

old_models_volume() {
    docker_ volume ls -q --filter "label=com.docker.compose.project=$OLD_PROJECT" --filter label=com.docker.compose.volume=models
}

bring_down_old_setup() {
    [ "$DOCKER_READY" = 1 ] || return 0
    [ -n "$(docker_ ps -aq --filter "label=com.docker.compose.project=$OLD_PROJECT")" ] || return 0
    if [ "$DRY_RUN" = 1 ]; then
        would "stop the old $OLD_PROJECT containers, and keep their models."
        return
    fi
    say "Stopping the old $OLD_PROJECT containers. Their models stay."
    # From / so compose goes by the project's name, not by the compose.yaml in this folder.
    (cd / && compose_ -p "$OLD_PROJECT" down --remove-orphans)
}

carry_old_models() {
    local volume
    [ "$DOCKER_READY" = 1 ] || return 0
    volume=$(old_models_volume)
    [ -n "$volume" ] || return 0
    if [ "$DRY_RUN" = 1 ]; then
        would "copy any models missing here from the old volume $volume, and leave that volume as it is."
        return
    fi
    say "Copying any models missing here from the old volume $volume. That volume stays as it is."
    compose_ run --rm --no-deps -T -u 0 -v "$volume:/from:ro" --entrypoint sh detector \
        -c 'cp -an /from/. /models/ || true' < /dev/null \
        || say "Those models couldn't be copied. The next step fetches the one that's needed."
}

# ── The model ──

checksum_of_listed() {
    awk -v name="$1" '$2 == name { print $1; exit }' "$CHECKSUMS" 2> /dev/null || true
}

model_retry_line() {
    later "The model $1 isn't on the Pi yet: $2 Everything else works. To try again, run: bash $(quoted "$PI_DIR/install.sh")"
}

fetch_model() {
    local model=$1 url file wanted got
    url="$(setting INSTALL_MODEL_URL)/$model"
    make_temp_folder
    file=$TEMP_FOLDER/$model
    say "Fetching the model $model. This may take a few minutes ..."
    if ! download "$file" "$url"; then
        model_retry_line "$model" "it couldn't be downloaded from $url."
        return
    fi
    wanted=$(checksum_of_listed "$model")
    if [ -n "$wanted" ]; then
        got=$(sha256sum "$file" | awk '{ print $1 }')
        if [ "$got" != "$wanted" ]; then
            model_retry_line "$model" "the download's checksum isn't the one in raspberry_pi/model_checksums.txt, so it wasn't used."
            rm -f "$file"
            return
        fi
    else
        say "No checksum is listed for $model in raspberry_pi/model_checksums.txt, so it can't be checked."
    fi
    chmod 644 "$file"
    # Under another name first, so the detector can't open half a file.
    if compose_ cp "$file" "detector:/models/.$model.part" \
            && compose_ exec -T detector mv "/models/.$model.part" "/models/$model" < /dev/null; then
        say "The model is in place. The detector converts it the first time, which takes a few minutes."
    else
        model_retry_line "$model" "it couldn't be copied into the detector."
    fi
    rm -f "$file"
}

place_model() {
    local model
    model=$(pi_setting pi.detector.model)
    [[ "$model" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || stop "pi.detector.model in the settings isn't a plain file name: $model"
    say "The chosen model is $model (pi.detector.model in the settings)."
    if [ "$DOCKER_READY" = 1 ] && compose_ exec -T detector test -s "/models/$model" < /dev/null > /dev/null 2>&1; then
        say "It's already on the Pi."
        return
    fi
    if [ "$DRY_RUN" = 1 ]; then
        would "fetch it from $(setting INSTALL_MODEL_URL) and copy it into the detector's models volume."
        return
    fi
    fetch_model "$model"
}

# ── Linking to the cloud ──

# Stops with the reason if it can't be made: the app can't start without it.
data_folder() {
    local wanted
    wanted=$(setting DESKTALK_DATA)
    # The mount point of a room that isn't mounted (or of a move cut short) is data_room.sh's (and its unit's) to make,
    # as root's, so the app can't write a second history on the card there.
    if [ "$ROOM" = unmounted ] || [ "$ROOM" = leftover ]; then
        (cd "$PI_DIR" && if [ -d "$wanted" ]; then cd -- "$wanted" && pwd; else printf '%s\n' "$wanted"; fi)
        return
    fi
    (cd "$PI_DIR" && mkdir -p -- "$wanted" && cd -- "$wanted" && pwd) \
        || stop "the data folder $wanted (DESKTALK_DATA in raspberry_pi/.env, from raspberry_pi) can't be made."
}

# On the Pi's own Python, before Docker and the build: the code runs out in minutes, and they take far longer. The
# code reaches the linker in its environment, which the list of running programs doesn't show, and never as an argument.
# The file it saves is this user's, the same user the app runs as, in the folder compose gives the app.
link_to_cloud() {
    [ -n "$SERVER" ] || return 0
    if [ "$DRY_RUN" = 1 ]; then
        would "link this Pi to $SERVER before anything else, with the code you gave, unless it's already linked there."
        return
    fi
    local said data status=0
    # The link is saved in the data folder: with the room not mounted that's the bare mount point, and a file there
    # would stop the room being mounted at all (it's never mounted over files).
    if [ "$ROOM" = unmounted ] || [ "$ROOM" = leftover ]; then
        LINK_FAILED=1
        say "This Pi is NOT linked to $SERVER yet: Desk Talk's own room on the SD card isn't mounted, and the link is saved in it. The rest still installs, and tries to mount it."
        later "This Pi is NOT linked to $SERVER, because Desk Talk's own room on the SD card wasn't mounted (see above). Once it is, get a new code from the website and run: DESKTALK_LINK_CODE=<the code> bash $(quoted "$PI_DIR/install.sh") --server $(quoted "$SERVER")"
        return
    fi
    data=$(data_folder)
    say "Linking this Pi to $SERVER ..."
    said=$(DESKTALK_LINK_CODE=$CODE python3 "$PI_DIR/link_pi.py" --data "$data" --server "$SERVER" --name "$PI_NAME" 2>&1) || status=$?
    say "${said//"$CODE"/(the code)}"
    if [ "$status" != 0 ]; then
        LINK_FAILED=1
        say "This Pi is NOT linked to $SERVER. The rest still installs: the Pi works without the cloud."
        later "This Pi is NOT linked to $SERVER. It works fine without it. To link it, get a new code from the website and run: DESKTALK_LINK_CODE=<the code> bash $(quoted "$PI_DIR/install.sh") --server $(quoted "$SERVER")"
    fi
}

# ── Starting ──

make_temp_folder() {
    if [ -z "$TEMP_FOLDER" ]; then
        TEMP_FOLDER=$(mktemp -d)
    fi
}

start_containers() {
    if [ "$DRY_RUN" = 1 ]; then
        would "build and start the three containers: docker compose up -d --build"
        return
    fi
    # Made here so it belongs to the Pi's user, not to Docker. Its place is relative to this folder, as in compose.
    data_folder > /dev/null
    say "Building and starting the containers. The first time takes a while ..."
    if ! compose_ up -d --build; then
        if [ "$REBOOT_NEEDED" = 1 ]; then
            say "The Pi needs a restart for its new boot settings, which may be why. Restart it and run this again."
        fi
        stop "the containers didn't build or start. If a download failed above, running this again is often all it takes. Otherwise see why with: cd $(quoted "$PI_DIR") && docker compose logs"
    fi
    clear_old_builds
}

# ── Desk Talk's own room on the SD card ──

data_room_() {
    bash "$PI_DIR/data_room.sh" "$@"
}

# What the app is told (DATA_ROOM_ON in .env, which start.sh reads): yes while the room's file is there, mounted or not,
# or a move into it was cut short. A room that isn't mounted may hold the only history, so the app then saves nothing,
# and says so, rather than start a second history beside it.
room_on_for() {
    case "$1" in
        room|unmounted|leftover) printf yes ;;
        *) printf no ;;
    esac
}

# Writes DATA_ROOM_ON, or in a dry run says it would, unless it's already that. ROOM_ON_CHANGED says if it did.
put_room_on() {
    ROOM_ON_CHANGED=0
    [ "${ROOM_ON:-$(setting DATA_ROOM_ON)}" != "$1" ] || return 0
    if [ "$DRY_RUN" = 1 ]; then
        would "write DATA_ROOM_ON=$1 in .env."
    else
        if ! set_env DATA_ROOM_ON "$1"; then
            ROOM_STOPPED="DATA_ROOM_ON=$1 couldn't be written in .env (is the SD card full?), so Desk Talk's own room wasn't set up."
            say "$ROOM_STOPPED" >&2
            return 1
        fi
        say "Wrote DATA_ROOM_ON=$1 in .env, for the app."
    fi
    ROOM_ON=$1                              # only once it's written, so a failed write is tried again
    ROOM_ON_CHANGED=1
}

# Runs data_room.sh make (with --dry-run in a dry run, and any flags given), showing what it says as it goes. Its
# IMPORTANT lines, what it left for lack of sudo, and where it kept the old data folder go in the closing notes too. If
# it stops, its reason goes in ROOM_STOPPED.
data_room_make() {
    local said line code=0
    make_temp_folder
    said=$TEMP_FOLDER/data_room.txt
    if [ "$DRY_RUN" = 1 ]; then
        data_room_ make --dry-run "$@" 2>&1 | tee "$said" || code=$?
    else
        data_room_ make "$@" 2>&1 | tee "$said" || code=$?
    fi
    while IFS= read -r line; do
        case "$line" in
            "IMPORTANT: "*|"The old data folder is kept at "*|"Desk Talk's room needs sudo to "*) later "$line" ;;
        esac
    done < "$said"
    [ "$code" = 0 ] && return 0
    ROOM_STOPPED=$(sed -n 's/^Stopping: //p' "$said" | tail -n 1)
    ROOM_STOPPED=${ROOM_STOPPED:-"data_room.sh didn't finish: its messages are above."}
    return 1
}

# The containers that write in the data folder are stopped while the room is made or mounted, so nothing is written to
# the old folder half-way. Answers 1 if they couldn't be.
stop_the_writers() {
    if [ "$DRY_RUN" = 1 ]; then
        would "stop the app and the camera while the room is set up: docker compose stop $WRITERS"
        return 0
    fi
    say "Stopping the app and the camera while Desk Talk's own room is set up ..."
    WRITERS_STOPPED=1                       # from here, leave starts them again if this stops
    # shellcheck disable=SC2086    # one name each
    compose_ stop $WRITERS && return 0
    ROOM_STOPPED="the app and the camera couldn't be stopped, so Desk Talk's own room wasn't set up."
    return 1
}

# data_room.sh puts its service file in place when it makes or mounts the room. Once the room is there, a newer one from
# git is put in place here, the way set_up_start_on_boot does it for desktalk.service.
check_room_service() {
    local text file unit on=1
    file=$(setting INSTALL_DATA_ROOM_SERVICE_FILE)
    unit=$(basename "$file")
    text=$(data_room_ unit-text) || exit 1          # data_room.sh has said why
    systemctl is-enabled --quiet "$unit" 2> /dev/null || on=0
    if in_place "$text" "$file" && [ "$on" = 1 ]; then
        return
    fi
    if [ "$UPDATE" = 1 ]; then
        if [ -f "$file" ] && [ "$on" = 1 ]; then
            later "Mounting Desk Talk's own room on boot isn't set up the way this version wants ($file). To set it up, run once: bash $(quoted "$PI_DIR/install.sh")"
        else
            later "IMPORTANT: Desk Talk's own room isn't set up to be mounted when the Pi boots ($unit), so after a restart the app would save nothing. To set it up, run once: bash $(quoted "$PI_DIR/install.sh")"
        fi
        return
    fi
    if [ "$DRY_RUN" = 1 ]; then
        in_place "$text" "$file" || would "install the new $file, which mounts Desk Talk's own room when the Pi boots."
        [ "$on" = 1 ] || would "switch on $unit, so the room is mounted when the Pi boots."
        return
    fi
    if ! in_place "$text" "$file"; then
        put_system_file "$text" "$file"
        sudo systemctl daemon-reload
        say "Installed the new $file, which mounts Desk Talk's own room when the Pi boots."
    fi
    if [ "$on" = 0 ]; then
        if sudo systemctl enable "$unit"; then
            say "Switched on $unit, so the room is mounted when the Pi boots."
        else
            later "IMPORTANT: systemd couldn't switch on $unit, so after a restart the app would save nothing. Try: sudo systemctl enable $unit"
        fi
    fi
}

# After the build: data_room.sh sizes the room so that a rebuild still has its room on the card. Once the room is there
# this only checks it (and grows it if DATA_ROOM_SIZE_MB went up), with nothing stopped. An update that didn't ask for
# sudo leaves what needs it (a grow) to install.sh, and says so.
set_up_data_room() {
    local after=$ROOM stopped=0 failed=0 flag=""
    case "$ROOM" in
        room)
            if [ "$UPDATE" = 1 ] && [ "$SETUP_SUDO" = 0 ]; then
                flag=--no-sudo
            fi
            if ! data_room_make ${flag:+"$flag"}; then
                later "IMPORTANT: Desk Talk's own room couldn't be checked or grown: $ROOM_STOPPED It still works at the size it has."
            fi
            check_room_service ;;
        elsewhere)
            say "The data folder is on another disk than the SD card, so it gets no room of its own and is left as it is." ;;
        none|unmounted|leftover)
            if [ "$UPDATE" = 1 ] && [ "$SETUP_SUDO" = 0 ]; then
                if [ "$ROOM" = none ]; then
                    later "IMPORTANT: Desk Talk doesn't have its own room on the SD card yet, because sudo didn't work, so other files filling the card could stop its history being saved. To give it one, run once: bash $(quoted "$PI_DIR/install.sh")"
                elif [ "$ROOM" = leftover ]; then
                    later "IMPORTANT: a move of Desk Talk's history into its own room on the SD card was cut short, and finishing it needs sudo, which didn't work, so the app saves nothing. Your history is safe. To finish the move, run once: bash $(quoted "$PI_DIR/install.sh")"
                else
                    later "IMPORTANT: Desk Talk's own room on the SD card isn't mounted, because sudo didn't work, so the app saves nothing. To mount it, run once: bash $(quoted "$PI_DIR/install.sh")"
                fi
            elif [ "$DRY_RUN" = 0 ] && ! sudo -v; then
                # Asked here, while Desk Talk still runs, so a password prompt can't keep it stopped.
                later "IMPORTANT: Desk Talk doesn't have its own room on the SD card set up, because sudo didn't work, so the app and the camera weren't stopped for it. To set it up, run once: bash $(quoted "$PI_DIR/install.sh")"
            else
                stopped=1
                # yes while it moves: if a power cut stops the move, the app starts saving nothing at the next boot,
                # never a new history on the empty folder the room mounts at. Put right just below if it didn't happen.
                if stop_the_writers && put_room_on yes && data_room_make; then
                    after=room
                else
                    failed=1
                fi
                if [ "$DRY_RUN" = 0 ]; then
                    after=$(data_room_ status 2> /dev/null) || after=$ROOM
                fi
            fi ;;
    esac
    # The containers get a DATA_ROOM_ON that changed, and those that were stopped start again, as they were.
    put_room_on "$(room_on_for "$after")"
    if [ "$ROOM_ON_CHANGED" = 1 ] || [ "$stopped" = 1 ]; then
        if [ "$DRY_RUN" = 1 ]; then
            would "start the containers again with it: docker compose up -d --no-build"
        else
            compose_ up -d --no-build || stop "the containers didn't start again. See why with: cd $(quoted "$PI_DIR") && docker compose logs"
            WRITERS_STOPPED=0
        fi
    fi
    # The rest of the install still runs (the model, starting on boot): it stops at the very end, in finish.
    ROOM_FAILED=$failed
}

# After the closing notes, so what data_room.sh said is there to read.
stop_if_the_room_failed() {
    [ "$ROOM_FAILED" = 1 ] || return 0
    if [ "$DRY_RUN" = 1 ]; then
        stop "$ROOM_STOPPED"
    fi
    stop "$ROOM_STOPPED The containers are running again, as they were."
}

# After a good build only, so the card can't fill up over many updates. Old images always go. Docker's build steps stay
# until the card runs low: measured on a Pi, clearing them after every build, even only the unused ones or down to a set
# size, means each update takes 12 to 21 minutes, downloads PyTorch again and restarts the containers, even with no new
# code. Volumes are never touched.
clear_old_builds() {
    docker_ image prune -f > /dev/null 2>&1 || say "Old images couldn't be cleared. It's safe to go on."
    if [ "$(free_disk)" -lt "$(setting INSTALL_CLEAR_BUILDS_BELOW_MB)" ]; then
        say "The card is getting full, so Docker's saved build steps are being cleared. The next update takes longer."
        docker_ builder prune -af > /dev/null 2>&1 || say "They couldn't be cleared. It's safe to go on."
    fi
}

finish() {
    if [ -n "$LATER" ]; then
        say ""
        printf '%s' "$LATER"
    fi
    say ""
    if [ "$DRY_RUN" = 1 ]; then
        if [ "$REBOOT_NEEDED" = 1 ]; then
            say "The Pi would need a restart for its boot settings."
        fi
        say "This was a dry run: nothing was changed."
        stop_if_the_room_failed
        return
    fi
    compose_ ps
    say "Open $(the_address "$PI_NAME") on a phone or computer on the same Wi-Fi."
    stop_if_the_room_failed
    if [ "$REBOOT_NEEDED" = 1 ]; then
        if [ "$REBOOT_FLAG" = 1 ]; then
            say "Restarting the Pi now for its new boot settings. The containers start again by themselves."
            sudo reboot
        else
            say "The Pi needs a restart for its new boot settings. Run: sudo reboot. The containers start again by themselves."
        fi
    fi
    if [ "$START_FAILED" = 1 ]; then
        exit 1
    fi
    if [ "$LINK_FAILED" = 1 ]; then
        exit "$LINK_FAILED_EXIT"
    fi
}

main() {
    read_flags "$@"
    cd "$PI_DIR"
    check_not_root
    check_64_bit
    check_disk
    if [ "$UPDATE" = 0 ]; then
        check_memory
    fi
    check_sound
    check_old_camera_program
    write_env
    make_my_settings
    ROOM=$(data_room_ status) || exit 1     # a bad setting: data_room.sh has said why
    PI_NAME=$(short_hostname)
    if [ "$UPDATE" = 0 ]; then
        set_hostname
        link_to_cloud
    fi
    write_public_name
    if [ "$UPDATE" = 0 ]; then
        set_memory_flags
        enable_camera
    fi
    find_docker
    set_up_systemd
    bring_down_old_setup
    put_room_on "$(room_on_for "$ROOM")"     # before the build starts the app, so it never starts on the wrong one
    start_containers
    set_up_data_room
    carry_old_models
    place_model
    try_start_on_boot
    finish
}

main "$@"
