"""
Preferences: wake time, bedtime and quiet hours chosen in the app's Settings page.

They win over config/protocols.yaml (schedule) and config/settings.yaml (reminders.quiet_hours),
which give the starting values. Saved in the store under "preferences"
(data/preferences.json, the same file and layout as before).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Mapping
from typing import Any

from desktalk.config import QuietHours, Schedule
from desktalk.ports import KeyValueStore
from desktalk.ports import Preferences as PreferencesView
from desktalk.util import in_hour_window, parse_hhmm

log = logging.getLogger(__name__)

KEY = 'preferences'
HOURS_IN_DAY = 24


def _clean(key: str, value: Any) -> Any:
    """A preference value as it is kept. Raises ValueError for a bad time or hour."""
    if key in ('wake_time', 'bed_time'):
        h, m = parse_hhmm(str(value))
        return f'{h:02d}:{m:02d}'
    if key == 'quiet_enabled':
        return bool(value)
    return int(value) % HOURS_IN_DAY            # quiet_start_hour / quiet_end_hour: 25 → 1


class Preferences:
    """Implements the Preferences interface (desktalk/ports.py)."""

    def __init__(self, store: KeyValueStore, schedule: Schedule, quiet_hours: QuietHours):
        self._store = store
        self._lock = threading.Lock()          # the web server and the background jobs both read these
        self._data: dict[str, Any] = {
            'wake_time': schedule.wake_time,
            'bed_time': schedule.bed_time,
            'quiet_enabled': quiet_hours.enabled,
            'quiet_start_hour': quiet_hours.start_hour,
            'quiet_end_hour': quiet_hours.end_hour,
        }
        saved = store.load(KEY, {})
        if not isinstance(saved, dict):
            log.warning('Saved preferences are not "name: value" pairs; using the defaults')
            saved = {}
        for key, value in saved.items():
            if key not in self._data:
                continue
            try:
                self._data[key] = _clean(key, value)
            except (TypeError, ValueError):
                log.warning('Saved preference %s = %r is not valid; using %r', key, value, self._data[key])

    def get(self, key: str) -> Any:
        with self._lock:
            return self._data[key]

    def all(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data)

    def update(self, changes: Mapping[str, Any]) -> dict[str, Any]:
        """Change some preferences and save them. Unknown names and None are ignored.
        Raises ValueError (and changes nothing) if any value is bad."""
        with self._lock:
            clean = {k: _clean(k, v) for k, v in changes.items() if k in self._data and v is not None}
            self._data.update(clean)
            out = dict(self._data)
            self._store.save(KEY, out)
        return dict(out)


def quiet_hours_now(prefs: PreferencesView, now: float) -> bool:
    """Quiet hours are on and `now` (local time) is inside them; the window may wrap past midnight."""
    if not prefs.get('quiet_enabled'):
        return False
    hour = time.localtime(now).tm_hour
    return in_hour_window(hour, int(prefs.get('quiet_start_hour')), int(prefs.get('quiet_end_hour')))
