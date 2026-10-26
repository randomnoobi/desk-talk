"""
PreferencesFile: wake time, bedtime, quiet hours and how picky joining the chat is (sensitivity), chosen in the
app's Settings page.

They win over config/protocols.yaml (schedule) and config/settings.yaml (reminders.quiet_hours,
agents.sensitivity_default), which give the starting values. Saved in the store under "preferences"
(data/preferences.json, the same file and layout as before; a file from before sensitivity existed still loads).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping
from typing import Any

from desktalk.config import QuietHours, Schedule
from desktalk.ports import Clock, KeyValueStore
from desktalk.ports import Preferences as PreferencesView
from desktalk.util import in_hour_window, parse_hhmm

log = logging.getLogger(__name__)

KEY = 'preferences'
HOURS_IN_DAY = 24


class PreferencesFile:
    """Implements the Preferences interface (desktalk/ports.py). `sensitivity_steps`: how many steps the
    sensitivity slider has (settings → agents.sensitivity_steps); `sensitivity_default`: the one used until it's
    moved, and when the saved one is missing or broken."""

    def __init__(self, store: KeyValueStore, schedule: Schedule, quiet_hours: QuietHours, *, sensitivity_steps: int,
                 sensitivity_default: int, clock: Clock):
        self._store = store
        self._clock = clock                    # for quiet_now: the local hour
        self._steps = sensitivity_steps
        self._lock = threading.Lock()          # the web server and the background jobs both read these
        self._data: dict[str, Any] = {
            'wake_time': schedule.wake_time,
            'bed_time': schedule.bed_time,
            'quiet_enabled': quiet_hours.enabled,
            'quiet_start_hour': quiet_hours.start_hour,
            'quiet_end_hour': quiet_hours.end_hour,
            'sensitivity': sensitivity_default,
        }
        saved = store.load(KEY, {})
        if not isinstance(saved, dict):
            log.warning('Saved preferences are not "name: value" pairs; using the defaults')
            saved = {}
        for key, value in saved.items():
            if key not in self._data:
                continue
            try:
                self._data[key] = self._clean(key, value)
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
        Raises ValueError (and changes nothing) if any value is bad; if saving fails, nothing changes either."""
        with self._lock:
            clean = {k: self._clean(k, v) for k, v in changes.items() if k in self._data and v is not None}
            out = {**self._data, **clean}
            self._store.save(KEY, out)
            self._data = out
        return dict(out)

    def quiet_now(self, now: float) -> bool:
        return quiet_hours_now(self, now, self._clock)

    def _clean(self, key: str, value: Any) -> Any:
        """A preference value as it is kept. Raises ValueError for a bad time, hour or step (a list, say, or
        infinity)."""
        if key in ('wake_time', 'bed_time'):
            h, m = parse_hhmm(str(value))
            return f'{h:02d}:{m:02d}'
        if key == 'quiet_enabled':
            return bool(value)
        if key == 'sensitivity':
            try:
                return min(max(int(value), 0), self._steps - 1)        # 9 → the last step, -3 → the first
            except (TypeError, ValueError, OverflowError):
                raise ValueError(f'sensitivity must be a step from 0 to {self._steps - 1} (got {value!r})') from None
        try:
            return int(value) % HOURS_IN_DAY        # quiet_start_hour / quiet_end_hour: 25 → 1
        except (TypeError, OverflowError):
            raise ValueError(f'{key} must be an hour from 0 to 23 (got {value!r})') from None


def quiet_hours_now(prefs: PreferencesView, now: float, clock: Clock) -> bool:
    """Quiet hours are on and `now` (the clock's local time) is inside them; the window may wrap past midnight."""
    if not prefs.get('quiet_enabled'):
        return False
    hour = clock.local_hour(now)
    return in_hour_window(hour, int(prefs.get('quiet_start_hour')), int(prefs.get('quiet_end_hour')))
