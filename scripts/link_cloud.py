#!/usr/bin/env python3
"""
Link this computer to a cloud server, so Desk Talk can send it your habit events in the background.

    uv run python scripts/link_cloud.py --server http://192.168.1.9:8080 --code ABCD-EFGH
    uv run python scripts/link_cloud.py --server http://192.168.1.9:8080 --code ABCD-EFGH --name "My desk"

Get the code from the cloud server's website ("Add a Pi", or "Link again" on a Pi it already knows). A code works
once and runs out after a few minutes. The server's reply holds a token; it is saved in data/cloud.json (settings →
cloud.link_key) and never shown. Linking again the same device keeps the place to send from and the notes on what
was sent of your goals and each day's numbers, so nothing is sent twice and nothing is skipped; a new device starts
from now, so old history is not uploaded by surprise.
A running Desk Talk reads the link every round, so it starts sending without a restart.
"""

from __future__ import annotations

import argparse
import math
import socket
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _number(value: Any, fallback: float) -> float:
    try:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:                               # a whole number too big for float maths
        return fallback
    return float(value) if ok else fallback


def _is_sent_note(value: Any) -> bool:
    """A remembered {sig, ts}: what was sent of the goals or of one day's numbers, and when."""
    return (isinstance(value, dict) and isinstance(value.get('sig'), str)
            and _number(value.get('ts'), math.nan) == value.get('ts'))


def _old_sent_notes(old: dict[str, Any]) -> dict[str, Any]:
    """The notes on what was already sent of the goals and each day's numbers: without them the sender sends all of
    those again under new times. Only what is good is kept."""
    from desktalk.domain import is_day

    notes: dict[str, Any] = {}
    if isinstance(old.get('totals_from'), str) and is_day(old['totals_from']):
        notes['totals_from'] = old['totals_from']
    if isinstance(old.get('totals_sent'), dict):
        notes['totals_sent'] = {day: note for day, note in old['totals_sent'].items()
                                if isinstance(day, str) and is_day(day) and _is_sent_note(note)}
    if _is_sent_note(old.get('goals_sent')):
        notes['goals_sent'] = old['goals_sent']
    return notes


def _old_place(old: Any, device_id: str, now: float) -> dict[str, Any]:
    """Where to send from for this device: kept from an earlier link to the same device (whatever of it is good: a
    damaged one never stops linking), else from now."""
    if not isinstance(old, dict) or old.get('device_id') != device_id:
        return {'place_ts': now, 'start_ts': now, 'recent_ids': []}
    place = _number(old.get('place_ts'), now)
    ids = old.get('recent_ids')
    return {'place_ts': place, 'start_ts': _number(old.get('start_ts'), place),
            'recent_ids': [i for i in ids if isinstance(i, str)] if isinstance(ids, list) else [],
            **_old_sent_notes(old)}


def run(argv: Sequence[str], *, config: Any, store: Any, clock: Any, claim: Callable[..., Any],
        say: Callable[[str], object]) -> int:
    """Link, saying what happened with `say`. Returns the exit code: 0 when linked, 1 when not (nothing is saved)."""
    from desktalk.domain import CloudNotLinked, CloudUnavailable

    parser = argparse.ArgumentParser(description='Link this computer to a cloud server.')
    parser.add_argument('--server', required=True, help='the cloud server\'s address, like http://192.168.1.9:8080')
    parser.add_argument('--code', required=True, help='the one-time code from the cloud server\'s website')
    parser.add_argument('--name', default=socket.gethostname(), help='what to call this computer on the website')
    args = parser.parse_args(list(argv))

    words = config.prompts.cloud
    server = args.server.strip().rstrip('/')
    parts = urlsplit(server)
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        say(words.link_bad_address.format(server=server))
        return 1
    old = store.load(config.cloud.link_key)      # read first: the code works once, so nothing may fail after it is used
    try:
        reply = claim(server, args.code, args.name, timeout_sec=config.cloud.timeout_sec,
                      error_text_chars=config.cloud.error_text_chars)
    except CloudNotLinked as exc:
        reason = ' '.join(str(exc).split())[:config.cloud.error_text_chars]
        say(words.link_refused_because.format(reason=reason) if reason else words.link_code_refused)
        return 1
    except CloudUnavailable as exc:
        say(words.link_no_answer.format(server=server, error=str(exc)))
        return 1

    store.save(config.cloud.link_key, {
        'server': server, 'device_id': reply.device_id, 'token': reply.token, 'stopped': False,
        **_old_place(old, reply.device_id, clock.now())})
    say(words.link_saved.format(server=server, device_id=reply.device_id))
    return 0


def main() -> int:
    from desktalk.adapters.cloud import claim
    from desktalk.adapters.storage import JsonFileStore
    from desktalk.config import load_config
    from desktalk.runtime import SystemClock

    config = load_config()
    store = JsonFileStore(config.resolve(config.storage.folder))
    return run(sys.argv[1:], config=config, store=store, clock=SystemClock(), claim=claim, say=print)


if __name__ == '__main__':
    sys.exit(main())
