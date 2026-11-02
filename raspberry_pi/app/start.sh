#!/bin/sh
# Runs each time the app's container starts, as root, then hands over to the app without root rights.
# Docker makes a missing data folder as root, so `docker compose up` works on a new Pi with no steps before it, and
# the files in it belong to the Pi's own user, so they can be copied with scp.
set -e

chown "$PI_USER_ID:$PI_GROUP_ID" data /pairing || echo "Could not give data and /pairing to user $PI_USER_ID"

# The desk's danger zones are saved in the data folder here (raspberry_pi/settings.pi.yaml). A new Pi starts from the
# ones in git.
if [ ! -e data/safety_rules.json ]; then
    cp config/safety_rules.json data/safety_rules.json
    chown "$PI_USER_ID:$PI_GROUP_ID" data/safety_rules.json || true
fi

exec setpriv --reuid="$PI_USER_ID" --regid="$PI_GROUP_ID" --clear-groups "$@"
