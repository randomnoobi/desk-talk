"""
Desk Talk: start the app (on the Pi it runs this inside Docker; on a computer you run it yourself).

    uv run python server.py

Then open http://localhost:5000 (on the Pi: the address web.public_address says). Settings are in
config/settings.yaml (yours in config/settings.local.yaml). With the Raspberry Pi as the camera and the app on a
computer, the start-up lines say where the Pi's own Desk Talk is. Ctrl+C stops it.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
from pathlib import Path

from desktalk.bootstrap import build, in_data_folder
from desktalk.config import AppConfig, ConfigError, load_config
from desktalk.domain import Saving
from desktalk.runtime.log_setup import STARTUP_LOGGER, setup_logging
from desktalk.runtime.saving import folder_for, saving_from_environment
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


def address_lines(config: AppConfig, source_kind: str) -> None:
    """Where to open the app and how the camera gets started. These lines reach the log file and `docker compose
    logs`, so they never show the pairing key. The Pi's camera sends its pictures to the Pi's own Desk Talk, so on a
    computer the lines say where that is (dev.pi_address)."""
    web = config.web
    if web.public_address:
        say(f'  → Open http://{web.public_address} on your phone or computer\n')
        return
    if source_kind == 'pi':
        say('\n  📷 Raspberry Pi camera: Desk Talk runs on the Pi, and its camera sends its pictures to the Pi\'s')
        say(f'     own Desk Talk. Open it at http://{config.dev.pi_address} (if your Pi has another name,')
        say('     set dev.pi_address in config/settings.local.yaml).\n')
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
    address_lines(config, desktalk.source_kind)
    with contextlib.suppress(KeyboardInterrupt):     # Ctrl+C: by then the server has stopped the app
        run(desktalk)
    return 0


if __name__ == '__main__':
    sys.exit(main())
