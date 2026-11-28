"""
The link to a cloud server as it is saved in the data folder (settings → cloud.link_key, data/cloud.json), and what
the Pi says about itself when the cloud server looks for Pis on the home network:

    store.save(key, link_record(server, reply.device_id, reply.token, store.load(key), now, clock.local_day))
    is_usable_link(store.load(key), clock.local_day)    # True: a link the sender can work with (maybe stopped)
    is_active_link(store.load(key), clock.local_day)    # True: usable, and the server hasn't said it doesn't know us
    CloudHello(name='desktalk', linked=False)

The sender (desktalk/core/cloud/uploader.py) and the Pi's hello and link offers (desktalk/core/cloud/linker.py) all
ask is_usable_link / is_active_link, so they never disagree about whether the Pi is linked: a damaged link (a bad
address or token, odd numbers, a time no clock can use) is not one, and the Pi may be linked again.

scripts/link_cloud.py (and so raspberry_pi/link_pi.py, on the Pi's own Python with no packages) and the Pi's own
linker (desktalk/core/cloud/linker.py) both save the link with link_record, so this file is standard library only.
Linking again the same device keeps the place to send from and the notes on what was sent of your goals and each
day's numbers, so nothing is sent twice and nothing is skipped; a new device starts from now, so old history is
not uploaded by surprise.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from desktalk.domain.days import is_day

USABLE_TOKEN = re.compile(r'[\x21-\x7e]+')     # printable ASCII, no space or line break: fit for a header
TIMES = ('place_ts', 'start_ts', 'last_sent_ts')


@dataclass(frozen=True)
class CloudHello:
    """What the Pi tells anyone on the home network who asks who it is: its name, and whether it is linked."""

    name: str
    linked: bool


def _number(value: Any, fallback: float) -> float:
    try:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:                               # a whole number too big for float maths
        return fallback
    return float(value) if ok else fallback


def _has_a_day(local_day: Callable[[float], str], ts: float) -> bool:
    try:
        return is_day(local_day(ts))
    except (OverflowError, OSError, ValueError, TypeError):      # a time far too big or small for the clock
        return False


def _is_sent_note(value: Any) -> bool:
    """A remembered {sig, ts}: what was sent of the goals or of one day's numbers, and when."""
    return (isinstance(value, dict) and isinstance(value.get('sig'), str)
            and _number(value.get('ts'), math.nan) == value.get('ts'))


def _old_sent_notes(old: dict[str, Any]) -> dict[str, Any]:
    """The notes on what was already sent of the goals and each day's numbers: without them the sender sends all of
    those again under new times. Only what is good is kept."""
    notes: dict[str, Any] = {}
    if isinstance(old.get('totals_from'), str) and is_day(old['totals_from']):
        notes['totals_from'] = old['totals_from']
    if isinstance(old.get('totals_sent'), dict):
        notes['totals_sent'] = {day: note for day, note in old['totals_sent'].items()
                                if isinstance(day, str) and is_day(day) and _is_sent_note(note)}
    if _is_sent_note(old.get('goals_sent')):
        notes['goals_sent'] = old['goals_sent']
    return notes


def _old_time(value: Any, fallback: float, local_day: Callable[[float], str]) -> float:
    """A remembered time, if it is a real number the clock can turn into a day (else the sender couldn't use the
    new link either), else `fallback`."""
    when = _number(value, math.nan)
    return when if when == when and _has_a_day(local_day, when) else fallback


def _old_place(old: Any, device_id: str, now: float, local_day: Callable[[float], str]) -> dict[str, Any]:
    """Where to send from for this device: kept from an earlier link to the same device (whatever of it is good: a
    damaged one never stops linking), else from now."""
    if not isinstance(old, dict) or old.get('device_id') != device_id:
        return {'place_ts': now, 'start_ts': now, 'recent_ids': []}
    place = _old_time(old.get('place_ts'), now, local_day)
    ids = old.get('recent_ids')
    return {'place_ts': place, 'start_ts': _old_time(old.get('start_ts'), place, local_day),
            'recent_ids': [i for i in ids if isinstance(i, str)] if isinstance(ids, list) else [],
            **_old_sent_notes(old)}


def link_record(server: str, device_id: str, token: str, old: Any, now: float,
                local_day: Callable[[float], str]) -> dict[str, Any]:
    """What to save for a new link to `server`, given the link saved before (`old`, anything: None, damaged ...).
    local_day: the clock's Clock.local_day, to tell a remembered time it can't use."""
    return {'server': server, 'device_id': device_id, 'token': token, 'stopped': False,
            **_old_place(old, device_id, now, local_day)}


def _is_web_address(server: Any) -> bool:
    if not isinstance(server, str) or server != server.strip():
        return False
    try:
        parts = urlsplit(server)
        parts.port                                      # noqa: B018  (a bad port raises ValueError here)
    except ValueError:
        return False
    return parts.scheme in ('http', 'https') and bool(parts.hostname)


def is_well_formed_link(saved: Any) -> bool:
    """A saved link the sender can work with, as far as can be told without a clock: a web address, a device id, a
    token fit for a header, times that are real numbers, a list of ids and a yes/no `stopped`. (A link made by
    link_record always is; this is for one that was damaged or edited.)"""
    if not isinstance(saved, dict) or not _is_web_address(saved.get('server')):
        return False
    device_id, token = saved.get('device_id'), saved.get('token')
    if not (isinstance(device_id, str) and device_id and isinstance(token, str) and USABLE_TOKEN.fullmatch(token)):
        return False
    if any(name in saved and _number(saved[name], math.nan) != saved[name] for name in TIMES):
        return False
    ids = saved.get('recent_ids', [])
    return (isinstance(ids, list) and all(isinstance(i, str) for i in ids)
            and isinstance(saved.get('stopped', False), bool))


def is_usable_link(saved: Any, local_day: Callable[[float], str]) -> bool:
    """A well-formed link whose times the clock (`local_day`, Clock.local_day) can turn into days. It may be
    stopped (the server said it doesn't know us)."""
    return is_well_formed_link(saved) and all(_has_a_day(local_day, saved[name]) for name in TIMES if name in saved)


def is_active_link(saved: Any, local_day: Callable[[float], str]) -> bool:
    """Is this saved link one in use: usable, and not stopped? A damaged or stopped one isn't, so the Pi may be
    linked again."""
    return is_usable_link(saved, local_day) and saved.get('stopped') is not True
