"""
The link to a cloud server as it is saved in the data folder (settings → cloud.link_key, data/cloud.json), and what
the Pi says about itself when the cloud server looks for Pis on the home network:

    store.save(key, link_record(server, reply.device_id, reply.token, store.load(key), now))
    is_active_link(store.load(key))       # True: linked, and the server hasn't said it doesn't know us
    CloudHello(name='desktalk', linked=False)

scripts/link_cloud.py (and so raspberry_pi/link_pi.py, on the Pi's own Python with no packages) and the Pi's own
linker (desktalk/core/cloud/linker.py) both save the link with link_record, so this file is standard library only.
Linking again the same device keeps the place to send from and the notes on what was sent of your goals and each
day's numbers, so nothing is sent twice and nothing is skipped; a new device starts from now, so old history is
not uploaded by surprise.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from desktalk.domain.days import is_day


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


def link_record(server: str, device_id: str, token: str, old: Any, now: float) -> dict[str, Any]:
    """What to save for a new link to `server`, given the link saved before (`old`, anything: None, damaged ...)."""
    return {'server': server, 'device_id': device_id, 'token': token, 'stopped': False,
            **_old_place(old, device_id, now)}


def is_active_link(saved: Any) -> bool:
    """Is this saved link one in use: a server, a device id and a token, and not stopped (the server said it doesn't
    know us)? A damaged or stopped one isn't, so the Pi may be linked again."""
    return (isinstance(saved, dict) and saved.get('stopped') is not True
            and all(isinstance(saved.get(name), str) and saved[name] for name in ('server', 'device_id', 'token')))
