#!/usr/bin/env bash
# Installs Desk Talk on a Raspberry Pi. Run it as your normal user, from anywhere:
#
#     bash raspberry_pi/install.sh [--tz Europe/Paris] [--server <address>]
#
# Safe to run again: it only changes what needs it, and never touches the data folder, the pairing key or the models.
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
#   --update           what update.sh uses: skip the parts that need sudo, and Docker's install
#
# Its numbers are in the "install script" part of .env.example.
# It exits with 1 if the install failed, and with 3 if the install worked but the link to the cloud server didn't.
set -euo pipefail

PI_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(dirname "$PI_DIR")
ENV_FILE=$PI_DIR/.env
EXAMPLE=$PI_DIR/.env.example
CHECKSUMS=$PI_DIR/model_checksums.txt
PROC=${DESKTALK_PROC:-/proc}                # only tests change this
ZONES=/usr/share/zoneinfo
MEMORY_FLAGS="cgroup_enable=memory cgroup_memory=1"
OLD_PROJECT=desktalk-detector               # the detector's own compose project, from before all three ran together
OLD_CAMERA_SERVICE=desk-talk-camera         # the camera program outside Docker, from before that
IMAGES="desktalk-app:local desktalk-camera:local desktalk-detector:local"     # the ones compose.yaml builds
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

say() { printf '%s\n' "$*"; }
would() { say "Would $*"; }
later() { LATER="$LATER$*"$'\n'; }
stop() { say "Stopping: $*" >&2; exit 1; }
leave() { if [ -n "$TEMP_FOLDER" ]; then rm -rf "$TEMP_FOLDER"; fi; }
trap leave EXIT

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

# From the Pi's settings on top of config/settings.yaml, as the app and the camera read them.
pi_setting() {
    local value
    value=$(yaml_value "$PI_DIR/settings.pi.yaml" "$1")
    if [ -z "$value" ]; then
        value=$(yaml_value "$ROOT/config/settings.yaml" "$1")
    fi
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

images_built() {
    local image ask=(docker)
    docker compose version > /dev/null 2>&1 || return 1
    if ! docker info > /dev/null 2>&1; then
        # The docker group only starts at the next login, so a second run soon after the first may need sudo.
        [ "$DRY_RUN" = 0 ] || return 1
        ask=(sudo docker)
    fi
    for image in $IMAGES; do
        "${ask[@]}" image inspect "$image" > /dev/null 2>&1 || return 1
    done
}

# The first build needs far more room than running this again, or rebuilding what changed.
check_disk() {
    local need free
    if images_built; then
        need=$(setting INSTALL_MIN_DISK_BUILT_MB)
    else
        need=$(setting INSTALL_MIN_DISK_MB)
    fi
    free=$(df -Pm "$ROOT" | awk 'NR == 2 { print $4 }')
    [ "$free" -ge "$need" ] || stop "there is $free MB of free disk and it needs $need MB. Free some space and run this again."
    say "Free disk: $free MB (needs $need)."
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

set_env() {
    local new
    new=$(mktemp "$ENV_FILE.XXXXXX")
    NEW_VALUE=$2 awk -v name="$1" -F= '$1 == name { print name "=" ENVIRON["NEW_VALUE"]; next } { print }' "$ENV_FILE" > "$new"
    chmod 644 "$new"
    mv "$new" "$ENV_FILE"
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
        --max-time "$(setting INSTALL_DOWNLOAD_MAX_SEC)" -o "$1" "$2"
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

# Only a Pi still called the stock name gets renamed. Anyone who named theirs has chosen.
set_hostname() {
    local now wanted
    now=$(hostname)
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

# On the Pi's own Python, before Docker and the build: the code runs out in minutes, and they take far longer. The
# code reaches the linker in its environment, which the list of running programs doesn't show, and never as an argument.
# The file it saves is this user's, the same user the app runs as, in the folder compose gives the app.
link_to_cloud() {
    [ -n "$SERVER" ] || return 0
    if [ "$DRY_RUN" = 1 ]; then
        would "link this Pi to $SERVER, with the code you gave, before anything else."
        return
    fi
    local said data status=0
    data=$(cd "$PI_DIR" && mkdir -p -- "$(setting DESKTALK_DATA)" && cd "$(setting DESKTALK_DATA)" && pwd)
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
    (cd "$PI_DIR" && mkdir -p -- "$(setting DESKTALK_DATA)")
    say "Building and starting the containers. The first time takes a while ..."
    if ! compose_ up -d --build; then
        if [ "$REBOOT_NEEDED" = 1 ]; then
            say "The Pi needs a restart for its new boot settings, which may be why. Restart it and run this again."
        fi
        stop "the containers didn't build or start. If a download failed above, running this again is often all it takes. Otherwise see why with: cd $(quoted "$PI_DIR") && docker compose logs"
    fi
    clear_old_builds
}

# After a good build only, so the card can't fill up over many updates. Old images go, and the build steps used longest
# ago, down to a set amount. Clearing them all would mean downloading PyTorch and the voice again on every update, and a
# failed download would then stop an update that only changed the code. Volumes are never touched.
clear_old_builds() {
    docker_ image prune -f > /dev/null 2>&1 || say "Old images couldn't be cleared. It's safe to go on."
    docker_ builder prune -af --reserved-space "$(setting INSTALL_BUILD_CACHE_KEEP_MB)MB" > /dev/null 2>&1 || say "Old build steps couldn't be cleared. It's safe to go on."
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
        return
    fi
    compose_ ps
    say "Open $(the_address "$PI_NAME") on a phone or computer on the same Wi-Fi."
    if [ "$REBOOT_NEEDED" = 1 ]; then
        if [ "$REBOOT_FLAG" = 1 ]; then
            say "Restarting the Pi now for its new boot settings. The containers start again by themselves."
            sudo reboot
        else
            say "The Pi needs a restart for its new boot settings. Run: sudo reboot. The containers start again by themselves."
        fi
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
    PI_NAME=$(hostname)
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
    bring_down_old_setup
    start_containers
    carry_old_models
    place_model
    finish
}

main "$@"
