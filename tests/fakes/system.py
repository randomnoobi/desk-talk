"""Stand-ins for the Pi tab's parts (desktalk/ports.py: SystemStatsReader, SystemStats, PiStatsLink). They pass the
same contracts as the real ones (tests/contracts/system_stats_contract.py), but read no file and call no Pi."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import replace
from typing import Any

from desktalk.domain import RawReading, StatsSample, StatsUnavailable

EMPTY_REPLY = {'collecting': True, 'every_sec': 2.0, 'keep_min': 30.0, 'info': {}, 'samples': []}


class FakeStatsReader:
    """Gives `readings` one by one (each with at= the time asked), then the last one again and again. With none,
    an empty reading. `asked` records each time read() was called with."""

    def __init__(self, readings: Sequence[RawReading] = ()):
        self._readings = list(readings)
        self._next = 0
        self.asked: list[float] = []

    def read(self, now: float) -> RawReading:
        self.asked.append(now)
        if not self._readings:
            return RawReading(at=now)
        reading = self._readings[min(self._next, len(self._readings) - 1)]
        self._next += 1
        return replace(reading, at=now)


class FakeStats:
    """A SystemStats whose every tick adds an empty sample at that moment, `every_sec` apart."""

    name = 'system stats'

    def __init__(self, every_sec: float = 2.0, collecting: bool = True):
        self.every_sec = every_sec
        self.collecting = collecting
        self.samples: list[StatsSample] = []

    def tick(self, now: float) -> float:
        self.samples.append(StatsSample(at=now))
        return self.every_sec

    def history(self, since: float | None) -> dict[str, Any]:
        newer_than_all = bool(self.samples) and since is not None and since > self.samples[-1].at
        kept = [s.to_dict() for s in self.samples if since is None or newer_than_all or s.at > since]
        return {**EMPTY_REPLY, 'collecting': self.collecting, 'every_sec': self.every_sec, 'samples': kept}


class FakePiStatsLink:
    """Answers fetch() with `reply` (one empty sample at 10.0 when not given), health() with `health_reply` and
    log_status() with `log_status_reply` (each like the Pi's own route when not given), or raises
    StatsUnavailable(problem) for all three when a problem is given. `asked` records each `since`."""

    def __init__(self, reply: dict[str, Any] | None = None, problem: str = '', *,
                 health_reply: dict[str, Any] | None = None, log_status_reply: dict[str, Any] | None = None):
        self.reply = reply if reply is not None else {**EMPTY_REPLY, 'samples': [StatsSample(at=10.0).to_dict()]}
        self.health_reply = health_reply if health_reply is not None else _pi_health()
        self.log_status_reply = log_status_reply if log_status_reply is not None else _pi_log_status()
        self.problem = problem
        self.asked: list[float | None] = []

    def fetch(self, since: float | None) -> dict[str, Any]:
        self.asked.append(since)
        return self._answer(self.reply)

    def health(self) -> dict[str, Any]:
        return self._answer(self.health_reply)

    def log_status(self) -> dict[str, Any]:
        return self._answer(self.log_status_reply)

    def _answer(self, reply: dict[str, Any]) -> dict[str, Any]:
        if self.problem:
            raise StatsUnavailable(self.problem)
        return reply


_SCHEDULER_JOB = {'name': 'compact event log', 'every_sec': 3600.0, 'runs': 1, 'last_error': None,
                  'next_in_sec': 1800.0}
_EVENT_STREAM = [{'name': 'event log', 'handled': 12, 'waiting': 0, 'dropped': 0, 'errors': 0}]
_PI_CAMERA = {'camera': 'ok', 'disk_free_mb': 20000, 'last_cleanup_ts': 9.0, 'last_frame_id': 'f-1',
              'photos_stored': 3}


def _pi_health() -> dict[str, Any]:
    """Like the Pi's GET /api/health (desktalk/web/routers/health.py): its camera sending, nothing wrong."""
    def check(level: str, value: float | None, unit: str) -> dict[str, Any]:
        return {'level': level, 'value': value, 'unit': unit}

    return {
        'uptime_sec': 600.0, 'jobs': [], 'scheduler': [{**_SCHEDULER_JOB, 'last_run_at': 8.0, 'next_run_at': 11.0}],
        'events': deepcopy(_EVENT_STREAM),
        'camera': {'source': 'pi', 'connected': True, 'fps': 3.0, 'pictures_received': 1800},
        'pi': {**deepcopy(_PI_CAMERA), 'upload_format': 2, 'detector': 'ok', 'detector_reason': '', 'detect_ms': 120.0,
               'model': 'yolo11n', 'clock_off_sec': 0.1},
        'detection': {'pictures_analysed': 0, 'detect_ms': None, 'problem': None, 'model': 'on the Pi'},
        'ai': {'ready': False, 'label': '', 'problem': None},
        'checks': {'camera': check('ok', None, ''), 'detections': check('ok', 0.4, 's'),
                   'pi_temperature': check('ok', 52.0, '°C'), 'card': check('ok', 20000.0, 'MB'),
                   'pi_heartbeat': check('ok', 2.0, 's'), 'card_rest': check('ok', 9000.0, 'MB'),
                   'saving': check('ok', None, '')},
    }


def _pi_log_status() -> dict[str, Any]:
    """Like the Pi's GET /api/log/status (desktalk/web/routers/log.py): a few events saved today."""
    return {
        'folder': '/data/desktalk/events', 'today': {'events': {'object_state': 4, 'alert': 1}, 'pictures_waiting': 0},
        'last_compaction': None, 'scheduler': [{**_SCHEDULER_JOB}], 'stream': deepcopy(_EVENT_STREAM),
        'camera': {'source': 'pi', 'connected': True, 'fps': 3.0, 'frames_received': 1800, 'yolo_ms': 120,
                   'model': 'on the Pi', 'pi': {**_PI_CAMERA}},
    }
