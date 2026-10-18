"""
Settings: one object built from the files in config/.

    settings = load_settings()
    settings.camera.fps                 # 3          (config/settings.yaml)
    settings.protocols.hydration.daily_goal_ml
    settings.agents_file['agents']      # config/agents.yaml as a dict
    settings.secrets.gemini_api_key     # from .env

Order (later wins):
    config/settings.yaml → config/settings.local.yaml → a few .env values (see ENV_OVERRIDES)

User choices made in the app (wake time, bedtime, quiet hours) are saved by
Preferences in data/preferences.json and win over protocols.yaml.
"""

from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any

import yaml

from . import paths

# Settings that may also be set in .env (kept so an existing .env keeps working).
# Everything else lives only in config/settings.yaml.
ENV_OVERRIDES = {
    'OLLAMA_MODEL': ('llm.ollama_model', str),
    'LOST_VISION_MODEL': ('llm.vision_model', str),
    'GEMINI_MODEL': ('llm.gemini_model', str),
    'LLM_MODEL': ('llm.openai_model', str),
    'GROQ_MODEL': ('llm.groq_model', str),
    'YOLO_MODEL': ('detector.model', str),
}
SECRET_KEYS = ('GEMINI_API_KEY', 'OPENAI_API_KEY', 'GROQ_API_KEY', 'OLLAMA_URL')
# Old .env settings that moved to config/settings.yaml (we print a hint if they're set).
MOVED_ENV = ('CAMERA_SOURCE', 'CAMERA_INDICES', 'PIPELINE_CAMERA', 'EVENT_COOLDOWN', 'APPEAR_FRAMES',
             'DISAPPEAR_FRAMES', 'REMINDER_WARMUP_MIN', 'HABIT_MIN_GAP_SEC', 'IDLE_CHAT_MIN_SEC')


class Section(dict):
    """A dict you can also read with dots: section.fps == section['fps']."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(f'setting "{key}" is missing from the config files') from None

    def get_path(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self
        for part in dotted.split('.'):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur


def _sectionize(value):
    if isinstance(value, dict):
        return Section({k: _sectionize(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_sectionize(v) for v in value]
    return value


def deep_merge(base: dict, extra: dict) -> dict:
    """Copy of `base` with `extra` laid on top (nested dicts are merged, not replaced)."""
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def read_yaml(path: Path) -> dict:
    if not path.is_file():
        return {}
    with open(path, encoding='utf-8') as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f'{path} should contain "key: value" settings')
    return data


def load_dotenv_file(path: Path, environ=None) -> None:
    """Read KEY=value lines from .env into the environment (existing values win)."""
    environ = os.environ if environ is None else environ
    if not path.is_file():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and not environ.get(k):
            environ[k] = v


def _set_dotted(d: dict, dotted: str, value) -> None:
    parts = dotted.split('.')
    for p in parts[:-1]:
        d = d.setdefault(p, {})
    d[parts[-1]] = value


def load_settings(config_dir: Path | None = None, environ=None, dotenv: Path | None = None) -> Section:
    """Build the settings object. Tests pass their own folder and environment."""
    config_dir = Path(config_dir) if config_dir else paths.CONFIG_DIR
    environ = os.environ if environ is None else environ
    load_dotenv_file(dotenv if dotenv is not None else paths.PROJECT_ROOT / '.env', environ)

    data = read_yaml(config_dir / 'settings.yaml')
    data = deep_merge(data, read_yaml(config_dir / 'settings.local.yaml'))
    data['protocols'] = read_yaml(config_dir / 'protocols.yaml')
    data['agents_file'] = read_yaml(config_dir / 'agents.yaml')

    applied = {}
    for env_key, (dotted, cast) in ENV_OVERRIDES.items():
        raw = environ.get(env_key, '').strip()
        if raw:
            _set_dotted(data, dotted, cast(raw))
            applied[env_key] = dotted
    data['secrets'] = {k.lower(): environ.get(k, '').strip() for k in SECRET_KEYS}
    data['_env_applied'] = applied
    data['_env_moved'] = [k for k in MOVED_ENV if environ.get(k, '').strip()]
    data['_config_dir'] = str(config_dir)
    return _sectionize(data)


# ════════════════════════════════════════════════════════════════════
#  Preferences: choices made in the app, saved in data/preferences.json
# ════════════════════════════════════════════════════════════════════
class Preferences:
    """Wake time, bedtime and quiet hours chosen in the app's Settings page."""

    def __init__(self, settings: Section, path: Path | None = None):
        self.path = Path(path) if path else paths.PREFERENCES_FILE
        self._lock = threading.Lock()
        sched = settings.protocols.get('schedule', {})
        quiet = settings.reminders.get('quiet_hours', {})
        self._data = {
            'wake_time': sched.get('wake_time', '07:00'),
            'bed_time': sched.get('bed_time', '22:30'),
            'quiet_enabled': bool(quiet.get('enabled', False)),
            'quiet_start_hour': int(quiet.get('start_hour', 22)),
            'quiet_end_hour': int(quiet.get('end_hour', 7)),
        }
        try:
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            if isinstance(saved, dict):
                self._data.update({k: v for k, v in saved.items() if k in self._data})
        except (OSError, ValueError):
            pass

    def get(self, key):
        with self._lock:
            return self._data[key]

    def all(self) -> dict:
        with self._lock:
            return dict(self._data)

    def update(self, **changes) -> dict:
        from .util import parse_hhmm
        with self._lock:
            for k, v in changes.items():
                if k not in self._data or v is None:
                    continue
                if k in ('wake_time', 'bed_time'):
                    h, m = parse_hhmm(str(v))          # raises ValueError on bad input
                    v = f'{h:02d}:{m:02d}'
                elif k == 'quiet_enabled':
                    v = bool(v)
                else:
                    v = int(v) % 24
                self._data[k] = v
            out = dict(self._data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(out, indent=2), encoding='utf-8')
        os.replace(tmp, self.path)
        return out
