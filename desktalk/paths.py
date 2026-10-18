"""Where things live on disk. Everything is relative to the project folder."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / 'config'
DATA_DIR = PROJECT_ROOT / 'data'          # event log, habit days, preferences (not uploaded to GitHub)
APP_DIR = PROJECT_ROOT / 'app'            # the computer's web UI
PHONE_DIR = PROJECT_ROOT / 'phone'        # the phone camera page
CERTS_DIR = PROJECT_ROOT / 'certs'        # HTTPS certificate + pairing key (not uploaded)
WEIGHTS_DIR = PROJECT_ROOT / 'weights'    # YOLO model files (not uploaded)

SETTINGS_FILE = CONFIG_DIR / 'settings.yaml'
LOCAL_SETTINGS_FILE = CONFIG_DIR / 'settings.local.yaml'
AGENTS_FILE = CONFIG_DIR / 'agents.yaml'
PROTOCOLS_FILE = CONFIG_DIR / 'protocols.yaml'
RULES_FILE = CONFIG_DIR / 'safety_rules.json'
PREFERENCES_FILE = DATA_DIR / 'preferences.json'
PAIR_KEY_FILE = CERTS_DIR / 'pair-key.txt'


def project_path(p: str | Path) -> Path:
    """A path from the settings: absolute paths and ~ stay as they are, others are under the project."""
    p = Path(p).expanduser()
    return p if p.is_absolute() else PROJECT_ROOT / p
