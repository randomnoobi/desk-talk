"""The only place that knows the real parts: it builds them from the settings and connects them. main.py (the website's
server) and reports_main.py (the weekly report and the clean-up) start from here.

Also the logging, because both programs need the same rule: what Postgres says about an error can hold the values of a
row (its DETAIL line), so a database error is logged by its kind only, whichever logger or library reports it."""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import IO
from urllib.parse import urlsplit

import psycopg
from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError

from desktalk_cloud.app import CloudParts, create_app
from desktalk_cloud.chat_forwarder import HttpChatForwarder
from desktalk_cloud.clock import SystemClock
from desktalk_cloud.config import CloudConfig, ConfigError
from desktalk_cloud.google_login import HttpGoogleLogin
from desktalk_cloud.llm_anthropic import AnthropicReportWriter
from desktalk_cloud.llm_openai import OpenAIReportWriter
from desktalk_cloud.ports import Clock, ReportWriter, Store
from desktalk_cloud.rate_limit import RateLimiter
from desktalk_cloud.report_data import load_report_words
from desktalk_cloud.report_job import ReportJob
from desktalk_cloud.retention import RetentionJob
from desktalk_cloud.routers import admin, auth, chat_v1, devices_v1, events_v1, site
from desktalk_cloud.store_postgres import PostgresStore, is_postgres_address

__all__ = ['ROUTERS', 'Server', 'build', 'build_app', 'build_jobs', 'check_settings', 'database_url', 'log_handler',
           'set_up_logging']

ROUTERS = (auth.router, site.router, admin.router, chat_v1.router, devices_v1.router, events_v1.router)
LOG_FORMAT = '%(asctime)s %(levelname)s %(name)s: %(message)s'
WEB_SCHEMES = ('http', 'https')
_DATABASE_ERRORS = (SQLAlchemyError, psycopg.Error)


# ── settings ───────────────────────────────────────────────────────────
def database_url(config: CloudConfig) -> str:
    url = config.secrets.database_url
    if url is None:
        raise ConfigError('DATABASE_URL is not set. compose.yaml sets it for you; when you run the server by hand, '
                          'put the Postgres address in the environment (see cloud/.env.example).')
    if not is_postgres_address(url.get_secret_value()):     # the words never repeat it: it holds the password
        raise ConfigError('DATABASE_URL is not a Postgres address the server can read. It should look like '
                          'postgresql+psycopg://user:password@host:5432/desktalk. With Docker, compose.yaml makes it '
                          'from POSTGRES_PASSWORD, which must be letters and digits only (see cloud/.env.example).')
    return url.get_secret_value()


def _check_login_url(address: str) -> None:
    """Google sends a person back here and the browser lands here, so it has to be a web address or nothing."""
    if not address:
        return
    problem = ConfigError(f'LOGIN_URL (settings.yaml: server.login_url) is {address!r}. It must be empty, or a web '
                          'address like http://localhost:8080 or https://desk.example.com.')
    if any(character.isspace() or not character.isprintable() for character in address):
        raise problem
    try:
        parts = urlsplit(address)
        has_port_or_none = parts.port is None or parts.port > 0
    except ValueError:
        raise problem from None
    if parts.scheme not in WEB_SCHEMES or not parts.hostname or not has_port_or_none or parts.username is not None:
        raise problem


def check_settings(config: CloudConfig) -> None:
    """Stops the start with a ConfigError, in plain words, for a setting that would only fail later."""
    site.check_site_words(config)
    _check_login_url(config.server.login_url)


# ── the website's server ───────────────────────────────────────────────
def build_parts(config: CloudConfig, store: Store, clock: Clock) -> CloudParts:
    secrets = config.secrets
    google = None
    if secrets.google_client_id is not None and secrets.google_client_secret is not None:
        google = HttpGoogleLogin(config.google, secrets.google_client_id, secrets.google_client_secret)
    chat_forwarder = None
    if secrets.chat_api_key is not None:
        chat_forwarder = HttpChatForwarder(config.chat, secrets.chat_api_key.get_secret_value(), clock)
    return CloudParts(
        config=config, store=store, clock=clock, google=google, chat_forwarder=chat_forwarder,
        claim_limiter=RateLimiter(config.limits.claim_tries, config.limits.claim_window_sec, clock),
        chat_limiter=RateLimiter(config.chat.limit_requests, config.chat.limit_window_sec, clock),
        chat_slots=threading.BoundedSemaphore(config.chat.max_at_once))


def build_app(parts: CloudParts, *, lifespan=None) -> FastAPI:
    return create_app(parts, ROUTERS, lifespan=lifespan)


@dataclass(frozen=True)
class Server:
    app: FastAPI
    store: PostgresStore


def build(config: CloudConfig) -> Server:
    """The real server. Nothing connects to the database until the first call to it."""
    store = PostgresStore(database_url(config))

    @asynccontextmanager
    async def close_the_database_at_the_end(_app: FastAPI) -> AsyncIterator[None]:
        yield
        store.dispose()

    parts = build_parts(config, store, SystemClock())
    return Server(app=build_app(parts, lifespan=close_the_database_at_the_end), store=store)


# ── the weekly report and the clean-up ─────────────────────────────────
def build_jobs(config: CloudConfig, store: Store, clock: Clock,
               should_stop: Callable[[], bool]) -> Sequence[ReportJob | RetentionJob]:
    words = load_report_words(config.prompts, config.messages)
    key = config.secrets.report_api_key
    writer: ReportWriter
    if config.reports.provider == 'anthropic':
        writer = AnthropicReportWriter(config.reports, key, words.errors)
    else:
        writer = OpenAIReportWriter(config.reports, key, words.errors)
    return (ReportJob(store, writer, clock, config.reports, words, should_stop), RetentionJob(store, config.retention))


# ── logs ───────────────────────────────────────────────────────────────
def _database_error_in(error: object) -> BaseException | None:
    """The database error in `error`, or behind it (an error raised while handling one, or from one)."""
    waiting, seen = [error], set()
    while waiting:
        item = waiting.pop()
        if not isinstance(item, BaseException) or id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, _DATABASE_ERRORS):
            return item
        waiting += [item.__cause__, item.__context__, *getattr(item, 'exceptions', ())]
    return None


class _HideDatabaseWords(logging.Filter):
    """Puts a database error's kind in the line, in place of its text and traceback. It sits on the handler, so it
    covers every logger, uvicorn's "Exception in ASGI application" and report_job's log.exception included, and an
    error passed as the message itself (log.error(error))."""

    def filter(self, record: logging.LogRecord) -> bool:
        in_message = _database_error_in(record.msg)
        if in_message is not None:
            record.msg, record.args = type(in_message).__name__, None
        if isinstance(record.args, tuple):
            record.args = tuple(type(arg).__name__ if _database_error_in(arg) else arg for arg in record.args)
        hidden = _database_error_in(record.exc_info[1]) if isinstance(record.exc_info, tuple) else None
        if hidden is not None:
            record.msg = f'{record.getMessage()} ({type(hidden).__name__})'
            record.args = None
            record.exc_info = record.exc_text = record.stack_info = None
        return True


def log_handler(stream: IO[str]) -> logging.Handler:
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(_HideDatabaseWords())
    return handler


def set_up_logging() -> None:
    logging.basicConfig(level=logging.INFO, handlers=[log_handler(sys.stderr)], force=True)
    logging.getLogger('httpx').setLevel(logging.WARNING)        # at INFO it logs every whole address it calls
