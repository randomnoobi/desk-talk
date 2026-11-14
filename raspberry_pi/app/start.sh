#!/bin/sh
# Runs each time the app's container starts, as root, then hands over to the app without root rights.
# Docker makes a missing data folder as root, so `docker compose up` works on a new Pi with no steps before it, and
# the files in it belong to the Pi's own user, so they can be copied with scp.
set -e

# Where the data folder comes from (set by compose.yaml). Desk Talk keeps its own fixed-size space on the SD card,
# mounted at data. With no DATA_ROOM (a new Pi, `docker compose up` as is) the data folder is plain, as before.
# If the space should be there but its marker file is missing, it isn't mounted: the app then saves nothing to the
# card and keeps what it must in memory, and the bare mount point is left alone.
mode=plain
if [ "$DATA_ROOM" = "yes" ]; then
    if [ -n "$DATA_ROOM_MARKER" ] && [ -e "data/$DATA_ROOM_MARKER" ]; then
        mode=room
    else
        mode=off
    fi
fi
export DESKTALK_SAVING="$mode"

if [ "$mode" = "off" ]; then
    echo "Desk Talk's own space on the SD card isn't mounted, so nothing is saved until it is. See raspberry_pi/README.md, \"If Desk Talk's space is missing\"."
    # compose.yaml gives a folder in memory. Without one (a plain `docker run`), one is made in /dev/shm, which every
    # container has in memory, never in /tmp: that's Docker's layer on the SD card. If even that fails, the app still
    # starts, and this says where it writes.
    if [ -z "$MEMORY_DATA_FOLDER" ]; then
        if MEMORY_DATA_FOLDER=$(mktemp -d /dev/shm/desktalk-XXXXXX); then
            echo "No memory folder was given, so $MEMORY_DATA_FOLDER is used (in memory)"
        else
            MEMORY_DATA_FOLDER=$(mktemp -d) || MEMORY_DATA_FOLDER=""      # empty: the app makes one itself
            echo "No memory folder was given and none could be made in memory (/dev/shm), so ${MEMORY_DATA_FOLDER:-a folder the app makes} is used: that may be on the SD card"
        fi
    fi
    export DESKTALK_DATA_FOLDER="$MEMORY_DATA_FOLDER"
    chown "$PI_USER_ID:$PI_GROUP_ID" /pairing "$DESKTALK_DATA_FOLDER" || echo "Could not give /pairing and the memory folder to user $PI_USER_ID"
else
    chown "$PI_USER_ID:$PI_GROUP_ID" data /pairing || echo "Could not give data and /pairing to user $PI_USER_ID"
fi

# The desk's danger zones are saved in the data folder here (raspberry_pi/settings.pi.yaml). A new Pi starts from the
# ones in git. All or nothing, and a failure (a full disk) doesn't stop the app: it then uses its built-in ones and
# this is tried again on the next start.
rules=data/safety_rules.json
if [ "$mode" != "off" ] && [ ! -s "$rules" ]; then
    rm -f "$rules" || true
    if cp config/safety_rules.json "$rules.new" && mv "$rules.new" "$rules"; then
        chown "$PI_USER_ID:$PI_GROUP_ID" "$rules" || true
    else
        rm -f "$rules.new" || true
        echo "Could not copy the danger zones to $rules, so the built-in ones are used for now"
    fi
fi

# Its one extra group is the Pi's audio group, which may use the sound card (/dev/snd).
exec setpriv --reuid="$PI_USER_ID" --regid="$PI_GROUP_ID" --groups="$PI_AUDIO_GROUP" "$@"
