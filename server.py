"""
Desk Talk: start everything on this computer.

    uv run python server.py

Then open http://localhost:5000. Settings are in config/settings.yaml (yours in config/settings.local.yaml).
With the Raspberry Pi as the camera, this shows the command to run on the Pi (python3 -m raspberry_pi.camera);
with the phone, the link (and a QR code) to open on the phone. Ctrl+C stops it.
"""

from __future__ import annotations

import contextlib
import logging
import sys
from pathlib import Path

from desktalk.adapters.storage.files import write_atomically
from desktalk.bootstrap import build
from desktalk.config import AppConfig, ConfigError, load_config
from desktalk.runtime.log_setup import STARTUP_LOGGER, setup_logging
from desktalk.web.pairing import Links, ensure_cert, lan_ip, qr_for_terminal
from desktalk.web.serve import run

RULE = '═' * 60

startup = logging.getLogger(STARTUP_LOGGER)


def start_logging(config: AppConfig) -> Path:
    """Log to the terminal and the log file (settings → logging) before the first start-up line, so those
    lines land in the file too. build() sets the same up again, which changes nothing. Returns the file."""
    logs = config.logging
    return setup_logging(config.resolve(logs.folder), logs.level, logs.file_size_mb, logs.files_kept, logs.file_name)


def say(line: str = '') -> None:
    """A start-up line for the person who started the server: in the terminal as it is, and in the log file."""
    startup.info(line)


def settings_lines(config: AppConfig) -> None:
    camera, log = config.camera, config.event_log
    say()
    say(RULE)
    say('  Desk Talk')
    say(RULE)
    say(f'  Camera       : {camera.source}  ({camera.fps:g} pictures a second)')
    say(f'  Event log    : {config.resolve(log.folder)}  (compacted every {log.compact_every_min:g} min)')
    if config.env_applied:
        say(f'  From .env    : {", ".join(config.env_applied)}')
    if config.env_moved:
        say(f'  Note         : {", ".join(config.env_moved)} in .env are no longer used; '
            f'they live in config/settings.yaml now.')


def main() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        # Logging isn't set up yet (its settings are what failed): Python still shows an error in the terminal.
        startup.error('Desk Talk could not start, a setting needs fixing:\n%s', exc)
        return 2
    start_logging(config)
    settings_lines(config)
    say('  Loading YOLO model (the first run downloads it)...')
    desktalk = build(config)
    say(f'  YOLO model   : {desktalk.detector.label}')
    say(f'  AI           : {desktalk.model.label}')
    say(f'  Danger zones : {"config/safety_rules.json" if desktalk.safety_rules.loaded else "built-in"}')
    address = lan_ip()
    phone_cert = None
    phone_port = None
    if desktalk.source_kind == 'phone':
        camera = config.camera
        phone_cert = ensure_cert(config.resolve(camera.phone_cert_folder), address, camera, save=write_atomically)
        phone_port = camera.phone_port
    links = Links(address, config.web.port, desktalk.pair_key, phone_port=phone_port)
    if desktalk.source_kind == 'pi':
        say('\n  📷 Raspberry Pi camera: on the Pi, in the desk-talk folder, run')
        say(f'     {links.pi_command}\n')
    elif desktalk.source_kind == 'phone':
        say('  📱 Phone camera: scan this with your phone (same Wi-Fi), or open:')
        say(f'     {links.phone_url}\n')
        say(qr_for_terminal(links.phone_url))
        say('  The phone will warn the connection is "not private" (it\'s this computer\'s own certificate).')
        say('  Tap Show Details → visit this website, then tap Start camera.\n')
    say(f'  → Open http://localhost:{config.web.port} on this computer\n')
    with contextlib.suppress(KeyboardInterrupt):     # Ctrl+C: by then the server has stopped the app
        run(desktalk, links, phone_cert)
    return 0


if __name__ == '__main__':
    sys.exit(main())
