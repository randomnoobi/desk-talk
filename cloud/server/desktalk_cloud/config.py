"""
The cloud server's settings: cloud/settings.yaml (numbers), cloud/prompts.yaml (words) and the secrets from the
environment (.env). What each setting means is in the comments of cloud/settings.yaml.

load_config() stops with a ConfigError, in plain words, when SESSION_SECRET is missing or short or a setting is wrong.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator

__all__ = ['ENV_NAMES', 'CloudConfig', 'ConfigError', 'load_config']

SETTINGS_FILE = 'settings.yaml'
PROMPTS_FILE = 'prompts.yaml'

# Every name the server reads from the environment (cloud/.env.example lists them all).
ENV_NAMES = ('SESSION_SECRET', 'DATABASE_URL', 'PUBLIC_URL', 'LOGIN_URL', 'GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET',
             'ADMIN_USERNAME', 'ADMIN_PASSWORD', 'REPORT_API_KEY', 'CHAT_API_KEY')

WEEKDAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')

PositiveInt = Annotated[int, Field(gt=0)]
PositiveNumber = Annotated[float, Field(gt=0)]
Hour = Annotated[int, Field(ge=0, le=23)]
Port = Annotated[int, Field(ge=1, le=65535)]


class ConfigError(Exception):
    """A setting is missing, misspelled or has a bad value. The message says which one."""


class _Section(BaseModel):
    """A part of the config: read-only, and a key it doesn't know is an error (a typo must not be ignored)."""

    model_config = ConfigDict(frozen=True, extra='forbid')


class ServerSettings(_Section):
    host: str
    port: Port
    public_url: str
    login_url: str
    https_only: bool
    cookie_name: str
    session_days: PositiveInt
    secret_min_chars: PositiveInt


class GoogleSettings(_Section):
    auth_url: str
    token_url: str
    userinfo_url: str
    scopes: str
    timeout_sec: PositiveNumber


class PairingSettings(_Section):
    code_valid_min: PositiveNumber
    name_max_chars: PositiveInt


class IngestSettings(_Section):
    max_batch: PositiveInt
    max_body_kb: PositiveInt
    max_number: PositiveNumber
    read_default_limit: PositiveInt
    read_max_limit: PositiveInt

    @model_validator(mode='after')
    def _limits_in_order(self):
        if self.read_default_limit > self.read_max_limit:
            raise ValueError('read_default_limit must not be more than read_max_limit')
        return self


class ReportSettings(_Section):
    day: Literal['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
    hour: Hour
    check_every_min: PositiveNumber
    wait_for_pi_days: Annotated[float, Field(ge=0)]
    max_tries: PositiveInt
    retry_first_wait_min: PositiveNumber
    retry_growth: Annotated[float, Field(ge=1)]
    provider: Literal['anthropic', 'openai_compatible']
    model: str
    url: str
    max_tokens: PositiveInt
    timeout_sec: PositiveNumber

    @property
    def weekday(self) -> int:
        """`day` as a number, Monday = 0 (like datetime.weekday())."""
        return WEEKDAYS.index(self.day)

    @model_validator(mode='after')
    def _url_for_openai_compatible(self):
        if self.provider == 'openai_compatible' and not self.url:
            raise ValueError('reports.url must be set when reports.provider is openai_compatible')
        return self


class ChatSettings(_Section):
    url: str
    model: str
    timeout_sec: PositiveNumber
    max_body_kb: PositiveInt
    limit_requests: PositiveInt
    limit_window_sec: PositiveNumber


class RetentionSettings(_Section):
    events_days: PositiveInt
    check_every_hours: PositiveNumber


class LimitSettings(_Section):
    claim_tries: PositiveInt
    claim_window_sec: PositiveNumber
    claim_body_kb: PositiveInt


class Messages(_Section):
    """The words in cloud/prompts.yaml → messages. A message with {max} is filled in with .format(max=...)."""

    refused_not_an_object: str
    refused_not_kept: str
    refused_number_too_big: str
    refused_failed: str
    bad_token: str
    bad_code: str
    too_many_tries: str
    body_too_big: str
    batch_too_big: str
    bad_body: str
    bad_name: str
    bad_query: str
    not_found: str
    not_logged_in: str
    default_pi_name: str
    google_not_set_up: str
    google_failed: str
    bad_state: str
    email_not_verified: str
    bad_time_zone: str
    admin_login_failed: str
    admin_not_set_up: str
    chat_no_key: str
    chat_no_images: str
    chat_too_many: str
    chat_failed: str
    report_no_key: str


class Secrets(_Section):
    """What comes from the environment. A secret is a SecretStr, so printing the config never shows it."""

    session_secret: SecretStr
    database_url: SecretStr | None = None
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    admin_username: str | None = None
    admin_password: SecretStr | None = None
    report_api_key: SecretStr | None = None
    chat_api_key: SecretStr | None = None


class CloudConfig(_Section):
    server: ServerSettings
    google: GoogleSettings
    pairing: PairingSettings
    ingest: IngestSettings
    reports: ReportSettings
    chat: ChatSettings
    retention: RetentionSettings
    limits: LimitSettings
    secrets: Secrets
    messages: Messages
    prompts: dict[str, Any]          # all of prompts.yaml as it is (the report job reads its own part)


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8'))
    except OSError as error:
        raise ConfigError(f'Cannot read {path}: {error.strerror or error}') from error
    except yaml.YAMLError as error:
        raise ConfigError(f'{path} is not valid YAML: {error}') from error
    if not isinstance(data, dict):
        raise ConfigError(f'{path} must hold settings (a mapping), but it does not')
    return data


def _describe(error: ValidationError, file_name: str) -> str:
    lines = []
    for item in error.errors():
        where = '.'.join(str(part) for part in item['loc'])
        if item['type'] == 'extra_forbidden':
            lines.append(f"{file_name}: '{where}' is not a setting the server knows")
        elif item['type'] == 'missing':
            lines.append(f"{file_name}: '{where}' is missing")
        else:
            lines.append(f"{file_name}: {where}: {item['msg'].removeprefix('Value error, ')}")
    return '\n'.join(lines)


def _env(environ: Mapping[str, str], name: str) -> str | None:
    value = environ.get(name, '').strip()
    return value or None


def _secrets(environ: Mapping[str, str], min_chars: int) -> Secrets:
    session = _env(environ, 'SESSION_SECRET')
    if session is None:
        raise ConfigError('SESSION_SECRET is not set. Put a long random text in cloud/.env (see .env.example), '
                          'then start the server again.')
    if len(session) < min_chars:
        raise ConfigError(f'SESSION_SECRET is too short: it needs at least {min_chars} characters. '
                          'Make a longer one (see .env.example).')
    return Secrets.model_validate({
        'session_secret': session, 'database_url': _env(environ, 'DATABASE_URL'),
        'google_client_id': _env(environ, 'GOOGLE_CLIENT_ID'),
        'google_client_secret': _env(environ, 'GOOGLE_CLIENT_SECRET'),
        'admin_username': _env(environ, 'ADMIN_USERNAME'), 'admin_password': _env(environ, 'ADMIN_PASSWORD'),
        'report_api_key': _env(environ, 'REPORT_API_KEY'), 'chat_api_key': _env(environ, 'CHAT_API_KEY')})


def load_config(root: Path, environ: Mapping[str, str]) -> CloudConfig:
    """Read `root`/settings.yaml and `root`/prompts.yaml, and the secrets from `environ`."""
    settings = _read_yaml(root / SETTINGS_FILE)
    prompts = _read_yaml(root / PROMPTS_FILE)
    server = settings.get('server')
    if isinstance(server, dict):                    # the environment's addresses win over the file's
        for key, name in (('public_url', 'PUBLIC_URL'), ('login_url', 'LOGIN_URL')):
            if _env(environ, name) is not None:
                server[key] = _env(environ, name)
    try:
        sections = {name: settings.get(name) for name in
                    ('server', 'google', 'pairing', 'ingest', 'reports', 'chat', 'retention', 'limits')}
        unknown = sorted(set(settings) - set(sections))
        if unknown:
            raise ConfigError(f"{SETTINGS_FILE}: '{unknown[0]}' is not a section the server knows")
        words = prompts.get('messages')
        parsed = _Parts.model_validate(sections)
    except ValidationError as error:
        raise ConfigError(_describe(error, SETTINGS_FILE)) from error
    try:
        messages = Messages.model_validate(words)
    except ValidationError as error:
        raise ConfigError(_describe(error, f'{PROMPTS_FILE} → messages')) from error
    secrets = _secrets(environ, parsed.server.secret_min_chars)
    return CloudConfig(secrets=secrets, messages=messages, prompts=prompts, **dict(parsed))


class _Parts(_Section):
    """Just the sections of settings.yaml, checked together so one error message can name every bad setting."""

    server: ServerSettings
    google: GoogleSettings
    pairing: PairingSettings
    ingest: IngestSettings
    reports: ReportSettings
    chat: ChatSettings
    retention: RetentionSettings
    limits: LimitSettings
