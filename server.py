"""
Desk Talk: start the app (on the Pi it runs this inside Docker; on a computer you run it yourself).

    uv run python server.py

Then open http://localhost:5000 (on the Pi: the address web.public_address says). Settings are in
config/settings.yaml (yours in config/settings.local.yaml). With the Raspberry Pi as the camera and the app on a
computer, the page shows the command to run on the Pi (python3 -m raspberry_pi.camera); with the phone, the link and
a QR code to open on the phone. The pairing key is in those, so it is on the page only and never in a log. Ctrl+C
stops it.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
from pathlib import Path

from desktalk.adapters.storage.files import write_atomically
from desktalk.bootstrap import build, in_data_folder
from desktalk.config import AppConfig, ConfigError, load_config
from desktalk.domain import Saving
from desktalk.runtime.log_setup import STARTUP_LOGGER, setup_logging
from desktalk.runtime.saving import folder_for, saving_from_environment
from desktalk.web.pairing import Links, ensure_cert, lan_ip
from desktalk.web.serve import run

RULE = '═' * 60

startup = logging.getLogger(STARTUP_LOGGER)


def start_logging(config: AppConfig, data_folder: Path | None = None) -> Path:
    """Log to the terminal and the log file (settings → logging) before the first start-up line, so those
    lines land in the file too. build() sets the same up again, which changes nothing. The log folder is under
    `data_folder` when there is one, as in build(). Returns the file."""
    logs = config.logging
    return setup_logging(in_data_folder(config, data_folder, logs.folder), logs.level, logs.file_size_mb,
                         logs.files_kept, logs.file_name)


def say(line: str = '') -> None:
    """A start-up line for the person who started the server: in the terminal as it is, and in the log file."""
    startup.info(line)


def settings_lines(config: AppConfig, data_folder: Path | None = None) -> None:
    camera, log = config.camera, config.event_log
    say()
    say(RULE)
    say('  Desk Talk')
    say(RULE)
    say(f'  Camera       : {camera.source}  ({camera.fps:g} pictures a second)')
    events = in_data_folder(config, data_folder, log.folder)
    say(f'  Event log    : {events}  (compacted every {log.compact_every_min:g} min)')
    if config.env_applied:
        say(f'  From .env    : {", ".join(config.env_applied)}')
    if config.env_moved:
        say(f'  Note         : {", ".join(config.env_moved)} in .env are no longer used; '
            f'they live in config/settings.yaml now.')


def address_lines(config: AppConfig, links: Links, source_kind: str) -> None:
    """Where to open the app and how the camera gets started. The pairing key is in the Pi command and the phone
    link, so these lines never show it (they reach the log file and `docker compose logs`): the page shows the
    command and the QR code, on the computer the app runs on. For the Pi's camera started by hand, the lines name
    the file the key is in."""
    web = config.web
    if web.public_address:
        say(f'  → Open {links.web_url} on your phone or computer\n')
        return
    key_file = config.resolve(config.camera.pair_key_file)
    if source_kind == 'pi':
        say('\n  📷 Raspberry Pi camera: the page below shows the command to run on the Pi. The pairing key is not')
        say(f'     printed here: it is in {key_file}. To start the camera by hand, copy that file to the Pi, and')
        say('     there, in the desk-talk folder, run')
        say(f'     {links.pi_command_with_key_file(key_file.name)}\n')
    elif source_kind == 'phone':
        say('  📱 Phone camera: open the page below on this computer, and scan the code it shows with your phone')
        say('     (same Wi-Fi). The phone will warn the connection is "not private" (it\'s this computer\'s own')
        say('     certificate). Tap Show Details → visit this website, then tap Start camera.\n')
    say(f'  → Open http://localhost:{web.port} on this computer\n')


def main() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        # Logging isn't set up yet (its settings are what failed): Python still shows an error in the terminal.
        startup.error('Desk Talk could not start, a setting needs fixing:\n%s', exc)
        return 2
    saving, data_folder = saving_from_environment(os.environ)
    # Worked out once, here: the same folder for the log file and for build().
    data_folder = folder_for(saving, data_folder, config.resolve(config.storage.memory_fallback_folder))
    start_logging(config, data_folder)
    settings_lines(config, data_folder)
    if saving is Saving.OFF:
        say(f"  Saving       : nothing is being saved (Desk Talk's own space is missing; kept for "
            f"this run only, in {data_folder})")
    if config.detector.enabled:
        say('  Loading YOLO model (the first run downloads it)...')
    desktalk = build(config, saving=saving, data_folder=data_folder)
    say(f'  Detector     : {desktalk.detector.label}')
    say(f'  AI           : {desktalk.model.label}')
    say(f'  Danger zones : {desktalk.rules_file if desktalk.safety_rules.loaded else "built-in"}')
    address = lan_ip()
    phone_cert = None
    phone_port = None
    if desktalk.source_kind == 'phone':
        camera = config.camera
        phone_cert = ensure_cert(config.resolve(camera.phone_cert_folder), address, camera, save=write_atomically)
        phone_port = camera.phone_port
    links = Links(address, config.web.port, desktalk.pair_key, phone_port=phone_port,
                  public_address=config.web.public_address)
    address_lines(config, links, desktalk.source_kind)
    with contextlib.suppress(KeyboardInterrupt):     # Ctrl+C: by then the server has stopped the app
        run(desktalk, links, phone_cert)
    return 0


if __name__ == '__main__':
    sys.exit(main())
