#!/bin/sh
# Runs each time the app's container starts, as root, then hands over to the app without root rights.
# Docker makes a missing data folder as root, so `docker compose up` works on a new Pi with no steps before it, and
# the files in it belong to the Pi's own user, so they can be copied with scp.
set -e

chown "$PI_USER_ID:$PI_GROUP_ID" data /pairing || echo "Could not give data and /pairing to user $PI_USER_ID"

# The desk's danger zones are saved in the data folder here (raspberry_pi/settings.pi.yaml). A new Pi starts from the
# ones in git. All or nothing, and a failure (a full disk) doesn't stop the app: it then uses its built-in ones and
# this is tried again on the next start.
rules=data/safety_rules.json
if [ ! -s "$rules" ]; then
    rm -f "$rules" || true
    if cp config/safety_rules.json "$rules.new" && mv "$rules.new" "$rules"; then
        chown "$PI_USER_ID:$PI_GROUP_ID" "$rules" || true
    else
        rm -f "$rules.new" || true
        echo "Could not copy the danger zones to $rules, so the built-in ones are used for now"
    fi
fi

exec setpriv --reuid="$PI_USER_ID" --regid="$PI_GROUP_ID" --clear-groups "$@"
