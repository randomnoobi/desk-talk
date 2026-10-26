"""
Loading the config: reading the YAML files and .env, laying settings.local.yaml over settings.yaml, and turning
pydantic's errors into one plain sentence each, naming the file and the setting.
"""

from __future__ import annotations

import difflib
import logging
import os
import types
import typing
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Literal

import annotated_types
import yaml
from pydantic import BaseModel, ValidationError
from pydantic.fields import FieldInfo

from desktalk.config.app import AppConfig
from desktalk.config.base import CONFIG_FOLDER, ConfigError, _got, _number, _Said, _Says
from desktalk.config.prompts import blank_problems, fill_settings, prompt_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

SECRET_KEYS = ('GEMINI_API_KEY', 'OPENAI_API_KEY', 'GROQ_API_KEY', 'OLLAMA_URL')
# Settings that may also be set in .env (kept so an existing .env keeps working).
ENV_OVERRIDES = {
    'OLLAMA_MODEL': ('llm', 'ollama_model'),
    'LOST_VISION_MODEL': ('llm', 'vision_model'),
    'GEMINI_MODEL': ('llm', 'gemini_model'),
    'LLM_MODEL': ('llm', 'openai_model'),
    'GROQ_MODEL': ('llm', 'groq_model'),
    'YOLO_MODEL': ('detector', 'model'),
}
# Old .env settings that moved to config/settings.yaml (startup shows a hint if they're set).
MOVED_ENV = ('CAMERA_SOURCE', 'CAMERA_INDICES', 'PIPELINE_CAMERA', 'EVENT_COOLDOWN', 'APPEAR_FRAMES',
             'DISAPPEAR_FRAMES', 'REMINDER_WARMUP_MIN', 'HABIT_MIN_GAP_SEC', 'IDLE_CHAT_MIN_SEC')
# Settings that are gone. Your own settings.local.yaml may still have them, so they're left out (with a note in the
# log) instead of stopping startup. agents.join_after_sec and agents.min_confidence became agents.sensitivity_steps;
# vision_habits went with the posture checks.
RETIRED_SETTINGS: tuple[tuple[str, ...], ...] = (('agents', 'join_after_sec'), ('agents', 'min_confidence'),
                                                 ('vision_habits',))

log = logging.getLogger(__name__)


Loc = tuple[str | int, ...]          # where a setting is, e.g. ('camera', 'fps')


class _Sources:
    """Which file set each setting, so an error message can name the right file."""

    def __init__(self):
        self.files: dict[Loc, list[str]] = {}

    def record(self, data: dict, file: str, prefix: Loc = ()) -> None:
        for key, value in data.items():
            loc = prefix + (key,)
            self.files.setdefault(loc, []).append(file)
            if isinstance(value, dict):
                self.record(value, file, loc)

    def _nearest(self, loc: Loc) -> list[str]:
        while loc and loc not in self.files:
            loc = loc[:-1]
        return self.files[loc] if loc else ['config/settings.yaml']

    def first(self, loc: Loc) -> str:
        """The file `loc` (or the nearest section above it) comes from: where a missing setting belongs."""
        return self._nearest(loc)[0]

    def last(self, loc: Loc) -> str:
        """The file that last set `loc` (or the nearest section above it)."""
        return self._nearest(loc)[-1]


def _read_yaml(path: Path, shown: str) -> dict:
    if not path.is_file():
        raise ConfigError(f'{shown} is missing')
    try:
        with open(path, encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        mark = getattr(exc, 'problem_mark', None)
        where = f' near line {mark.line + 1}' if mark else ''
        raise ConfigError(f'{shown}: not valid YAML{where} ({getattr(exc, "problem", None) or exc})') from None
    if not isinstance(data, dict):
        raise ConfigError(f'{shown} should contain "key: value" settings')
    return data


def _read_dotenv(path: Path) -> dict[str, str]:
    """KEY=value lines from .env (blank lines and # comments skipped, quotes removed)."""
    if not path.is_file():
        return {}
    out = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key:
            out[key] = value
    return out


def _merge(base: dict, extra: dict) -> None:
    """Lay `extra` on top of `base`, in place. Nested sections are merged, not replaced."""
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value


# ── turning pydantic's errors into one plain sentence each ──
_UNIONS = (typing.Union, types.UnionType)


def _field_named(model: type[BaseModel], key: str | int) -> FieldInfo | None:
    """The field written as `key` in the file (a field may be written under another name: is_ is "is")."""
    return next((f for name, f in model.model_fields.items() if (f.alias or name) == key), None)


def _unwrap(annotation: Any, metadata: list) -> tuple[Any, list]:
    """Look through `X | None` and Annotated[X, limits] to X and its limits."""
    while True:
        origin = typing.get_origin(annotation)
        if origin is Annotated:
            annotation, *extra = typing.get_args(annotation)
            for item in extra:      # Field(...) keeps its limits in .metadata
                metadata = metadata + (list(item.metadata) if isinstance(item, FieldInfo) else [item])
        elif origin in _UNIONS:
            options = [a for a in typing.get_args(annotation) if a is not type(None)]
            if len(options) > 1:        # a real choice (a rule's condition): _field_at picks by the key
                return annotation, metadata
            annotation = options[0]
        else:
            return annotation, metadata


def _field_at(loc: Loc) -> tuple[Any, list]:
    """The type and limits of the setting at `loc` (as pydantic reports it)."""
    annotation, metadata = AppConfig, []
    for part in loc:
        annotation, metadata = _unwrap(annotation, metadata)
        origin, args = typing.get_origin(annotation), typing.get_args(annotation)
        if origin in _UNIONS:       # one of several sections: the one that has this key
            annotation, metadata = _unwrap(next(a for a in args if _field_named(_unwrap(a, [])[0], part)), [])
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            field = _field_named(annotation, part)
            assert field is not None
            annotation, metadata = field.annotation, list(field.metadata)
        elif origin is dict:
            annotation, metadata = args[1], []
        elif origin is tuple:
            annotation, metadata = (args[0] if args[-1] is Ellipsis else args[int(part)]), []
    return _unwrap(annotation, metadata)


def _describe(annotation: Any, metadata: list) -> str:
    """What a setting should be, in plain words: 'a number above 0'."""
    for m in metadata:
        if isinstance(m, _Says):
            return m.words
    if typing.get_origin(annotation) is Literal:
        return 'one of: ' + ', '.join(str(a) for a in typing.get_args(annotation))
    if annotation is bool:
        return 'true or false'
    if annotation is str:
        return 'text'
    if typing.get_origin(annotation) is tuple:
        if any(isinstance(m, annotated_types.MinLen) and m.min_length > 0 for m in metadata):
            return 'a list with at least one item, like [a, b]'
        return 'a list, like [a, b]'
    if typing.get_origin(annotation) is dict or (isinstance(annotation, type) and issubclass(annotation, BaseModel)):
        return 'a group of settings'
    if annotation not in (int, float):
        return 'a path' if annotation is Path else str(annotation)
    words = 'a whole number' if annotation is int else 'a number'
    limits = {}
    for m in metadata:
        for name in ('gt', 'ge', 'le'):
            if isinstance(m, getattr(annotated_types, name.title())):
                limits[name] = _number(getattr(m, name))
    if 'ge' in limits and 'le' in limits:
        return f"{words} from {limits['ge']} to {limits['le']}"
    if 'gt' in limits and 'le' in limits:
        return f"{words} above {limits['gt']} and at most {limits['le']}"
    if 'gt' in limits:
        return f"{words} above {limits['gt']}"
    if 'ge' in limits:
        return f"{words}, {limits['ge']} or more"
    return words


def _dotted(loc: Loc) -> str:
    """('camera', 'webcam_indices', 1) → 'camera.webcam_indices[1]'."""
    out = ''
    for part in loc:
        out += f'[{part}]' if isinstance(part, int) else (f'.{part}' if out else str(part))
    return out


_OTHER_FILES = ('protocols', 'agents_file', 'prompts')
# Parts of AppConfig that don't come from settings.yaml, so settings.yaml may not set them.
_NOT_FROM_SETTINGS = ('protocols', 'agents_file', 'prompts', 'secrets', 'env_applied', 'env_moved', 'root')
_SECRET_NAMES = {'secrets', *(k.lower() for k in SECRET_KEYS)}


def _unknown(file: str, loc: Loc, shown: Loc) -> str:
    """Message for a key nobody reads: most likely a typo, or an API key in the wrong place."""
    if str(loc[-1]).lower() in _SECRET_NAMES:
        return f'{file}: {_dotted(shown)} is not a known setting (API keys go in .env, see .env.example)'
    known = [f.alias or k for k, f in _field_at(loc[:-1])[0].model_fields.items()
             if loc[:-1] or k not in _NOT_FROM_SETTINGS]
    close = difflib.get_close_matches(str(loc[-1]), known, n=1)
    hint = f' (did you mean {_dotted(shown[:-1] + (close[0],))}?)' if close else ''
    return f'{file}: {_dotted(shown)} is not a known setting{hint}'


def _without_tags(loc: Loc) -> Loc:
    """Pydantic names the kind of condition in the path, then its key again: when.state.state.is → when.state.is."""
    if loc[:2] != ('protocols', 'rules'):
        return loc
    out, skipped = [], False
    for part in loc:
        skipped = bool(out) and part == out[-1] and not skipped and isinstance(part, str)
        if not skipped:
            out.append(part)
    return tuple(out)


def _length_after_a_bad_item(err: Mapping[str, Any]) -> bool:
    """Pydantic also says "too short" for a list whose items were all bad; the bad items already explain it."""
    return err['type'] == 'too_short' and err['ctx']['actual_length'] == 0 and bool(err['input'])


def _problem(err: Mapping[str, Any], sources: _Sources) -> str:
    loc = _without_tags(tuple(err['loc']))
    file = sources.last(loc)
    shown = loc[1:] if loc[0] in _OTHER_FILES else loc     # as written in that file
    if err['type'] == 'extra_forbidden':
        return _unknown(file, loc, shown)
    if err['type'] == 'missing':
        return f'{sources.first(loc)}: {_dotted(shown)} is missing'
    if err['type'] == 'value_error' and (isinstance(err['input'], dict) or isinstance(err['ctx']['error'], _Said)):
        # a check across two settings, or a sentence that says it all
        return f'{file}: {_dotted(shown)}: {err["ctx"]["error"]}'
    return f'{file}: {_dotted(shown)} must be {_describe(*_field_at(loc))} (got {_got(err["input"])})'


def _drop_retired(data: dict) -> None:
    """Leave out the settings in RETIRED_SETTINGS, saying so in the log."""
    for path in RETIRED_SETTINGS:
        section = data
        for key in path[:-1]:
            section = section.get(key) if isinstance(section, dict) else None
        if isinstance(section, dict) and path[-1] in section:
            del section[path[-1]]
            log.warning('Settings: %s is no longer used, so it was left out (remove it from your settings.local.yaml)',
                        '.'.join(path))


def load_config(root: Path | None = None, environ: Mapping[str, str] | None = None,
                dotenv: Path | None = None) -> AppConfig:
    """Build the config from <root>/config/ and <root>/.env. Tests pass a temp folder and environ={}.

    A value already set in the environment wins over the same line in .env."""
    root = Path(root or PROJECT_ROOT)
    environ = os.environ if environ is None else environ
    env = _read_dotenv(Path(dotenv) if dotenv is not None else root / '.env')
    env.update({k: v for k, v in environ.items() if v})
    config_dir = root / CONFIG_FOLDER
    sources = _Sources()

    def read(name: str, prefix: Loc = ()) -> dict:
        shown = (config_dir / name).relative_to(root).as_posix()
        data = _read_yaml(config_dir / name, shown)
        sources.record(data, shown, prefix)
        return data

    data = read('settings.yaml')
    if (config_dir / 'settings.local.yaml').is_file():
        _merge(data, read('settings.local.yaml'))
    _drop_retired(data)
    problems = [_unknown(sources.last((key,)), (key,), (key,)) for key in data if key in _NOT_FROM_SETTINGS]
    data['protocols'] = read('protocols.yaml', ('protocols',))
    data['agents_file'] = read('agents.yaml', ('agents_file',))
    data['prompts'] = read('prompts.yaml', ('prompts',))
    applied = {}
    for name, (section, key) in ENV_OVERRIDES.items():
        value = env.get(name, '').strip()
        if value:
            data.setdefault(section, {})[key] = value
            sources.files[(section, key)] = [f'.env or the environment ({name})']
            applied[name] = f'{section}.{key}'
    data['env_applied'] = applied
    data['env_moved'] = tuple(k for k in MOVED_ENV if env.get(k, '').strip())
    data['secrets'] = {k.lower(): env.get(k, '').strip() for k in SECRET_KEYS}
    data['root'] = root
    try:
        config = AppConfig.model_validate(data)
    except ValidationError as exc:
        problems += [_problem(e, sources) for e in exc.errors() if not _length_after_a_bad_item(e)]
    if problems:
        raise ConfigError('\n'.join(problems))
    return _with_settings_in_prompts(config)


def _with_settings_in_prompts(config: AppConfig) -> AppConfig:
    """Check every text in prompts.yaml uses only blanks that get filled in, then fill in the settings ones."""
    values = prompt_values(config)
    problems = blank_problems(config.prompts, values)
    if problems:
        raise ConfigError('\n'.join(f'config/prompts.yaml: {p}' for p in problems))
    return config.model_copy(update={'prompts': fill_settings(config.prompts, values)})
