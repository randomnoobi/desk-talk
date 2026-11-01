"""Fakes and helpers for the cloud server's tests: a config built from the real cloud/settings.yaml, fake Google,
fake report writer and fake chat forwarder, and a quick way to link a Pi without going through the routes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from desktalk_cloud.app import CloudParts
from desktalk_cloud.config import CloudConfig, load_config
from desktalk_cloud.domain import Device, GoogleUser, User
from desktalk_cloud.ports import ChatFailed, ChatForwarder, Clock, GoogleFailed, GoogleLogin, ReportFailed, Store
from desktalk_cloud.rate_limit import RateLimiter
from desktalk_cloud.store_memory import MemoryStore
from desktalk_cloud.tokens import hash_secret, new_code, new_token
from tests.fakes.clock import FakeClock

CLOUD_FOLDER = Path(__file__).resolve().parents[2] / 'cloud'
TEST_SESSION_SECRET = 'a-long-enough-secret-for-the-tests-0123456789'


def cloud_config(env: Mapping[str, str] | None = None, **changes: Mapping[str, Any]) -> CloudConfig:
    """The real cloud/settings.yaml. `changes` replaces settings in a section: cloud_config(ingest={'max_batch': 3})."""
    config = load_config(CLOUD_FOLDER, {'SESSION_SECRET': TEST_SESSION_SECRET, **(env or {})})
    for section, values in changes.items():
        config = config.model_copy(update={section: getattr(config, section).model_copy(update=dict(values))})
    return config


class FakeGoogleLogin:
    """Google that knows a few people: `people['code'] = GoogleUser(...)`. Any other code fails."""

    def __init__(self) -> None:
        self.people: dict[str, GoogleUser] = {}
        self.exchanged: list[tuple[str, str]] = []

    def login_url(self, state: str, redirect_uri: str) -> str:
        return f'https://google.test/login?state={state}&redirect_uri={redirect_uri}'

    def exchange(self, code: str, redirect_uri: str) -> GoogleUser:
        self.exchanged.append((code, redirect_uri))
        if code not in self.people:
            raise GoogleFailed('Google did not accept that code')
        return self.people[code]


class FakeReportWriter:
    """Writes `reply`, or raises ReportFailed(`fail_with`) when that is set. Remembers what it was asked."""

    def __init__(self, reply: str = '# A fake week') -> None:
        self.reply = reply
        self.fail_with: str | None = None
        self.calls: list[tuple[str, str]] = []

    def write(self, prompt: str, data: str) -> str:
        self.calls.append((prompt, data))
        if self.fail_with is not None:
            raise ReportFailed(self.fail_with)
        return self.reply


class FakeChatForwarder:
    """Answers in the OpenAI shape, or raises ChatFailed(`fail_with`). Remembers what it was asked."""

    def __init__(self, reply: str = 'Hello from the fake') -> None:
        self.reply = reply
        self.fail_with: str | None = None
        self.calls: list[tuple[Sequence[Mapping[str, Any]], int | None, float | None]] = []

    def complete(self, messages: Sequence[Mapping[str, Any]], max_tokens: int | None,
                 temperature: float | None) -> dict[str, Any]:
        self.calls.append((messages, max_tokens, temperature))
        if self.fail_with is not None:
            raise ChatFailed(self.fail_with)
        return {'id': 'chat-1', 'object': 'chat.completion', 'model': 'fake',
                'choices': [{'index': 0, 'finish_reason': 'stop',
                             'message': {'role': 'assistant', 'content': self.reply}}]}


def make_parts(config: CloudConfig | None = None, *, store: Store | None = None, clock: Clock | None = None,
               google: GoogleLogin | None = None, chat_forwarder: ChatForwarder | None = None) -> CloudParts:
    """Everything the server needs, built from fakes and the in-memory store."""
    config = config or cloud_config()
    clock = clock or FakeClock()
    return CloudParts(
        config=config, store=store or MemoryStore(), clock=clock, google=google, chat_forwarder=chat_forwarder,
        claim_limiter=RateLimiter(config.limits.claim_tries, config.limits.claim_window_sec, clock),
        chat_limiter=RateLimiter(config.chat.limit_requests, config.chat.limit_window_sec, clock))


@dataclass(frozen=True)
class Linked:
    user: User
    device: Device
    token: str


def link_device(parts: CloudParts, who: str = 'ann', *, time_zone: str = 'UTC') -> Linked:
    """A person (made if new) with a linked Pi, as if the Pi had used a code. Gives the Pi's token."""
    now = parts.clock.now()
    user = parts.store.upsert_user(f'google-{who}', f'{who}@example.com', who.title(), now)
    code, token = new_code(), new_token()
    parts.store.add_code(hash_secret(code), user.id, None, time_zone, now + 600)
    device = parts.store.claim_code(hash_secret(code), f"{who}'s Pi", hash_secret(token), now)
    assert device is not None
    return Linked(user=user, device=device, token=token)
