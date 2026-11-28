"""Stand-ins for the Pi tab's parts (desktalk/ports.py: SystemStatsReader, SystemStats, PiStatsLink). They pass the
same contracts as the real ones (tests/contracts/system_stats_contract.py), but read no file and call no Pi."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from typing import Any

from desktalk.domain import RawReading, StatsSample, StatsUnavailable
from desktalk.domain.dev import SmoothingStep

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
    """Answers fetch() with `reply` (one empty sample at 10.0 when not given) and health() with `health_reply` (like
    the Pi's own GET /api/health when not given), or raises StatsUnavailable(problem) for every method when a
    problem is given. `asked` records each `since`.

    For the Live tab, each like the Pi's own reply when not given: states() answers `states_reply` (a cup PRESENT
    since 5.0, one change, the Pi's time 12.0); traced_labels() `labels_reply` ({'labels': ['cup']}); trace(label)
    the asked label with `trace_steps` (one smoothing step) when it's one of labels_reply's, and no steps for any
    other, both with the Pi's `trace_rules` (its smoothing numbers, PI_RULES); story(label) `story_reply` (one
    step and one picture, 'cup-1') with the asked label when it's one of labels_reply's, and the empty story for
    any other; photo(id) the bytes in `photos` ({'cup-1': a few JPEG bytes}), None for an id not there.
    `asked_states`, `asked_traces`, `asked_stories` and `asked_photos` record what each was asked."""

    def __init__(self, reply: dict[str, Any] | None = None, problem: str = '', *,
                 health_reply: dict[str, Any] | None = None, states_reply: dict[str, Any] | None = None,
                 labels_reply: dict[str, Any] | None = None, trace_steps: Sequence[dict[str, Any]] | None = None,
                 trace_rules: dict[str, Any] | None = None, story_reply: dict[str, Any] | None = None,
                 photos: Mapping[str, bytes] | None = None):
        self.reply = reply if reply is not None else {**EMPTY_REPLY, 'samples': [StatsSample(at=10.0).to_dict()]}
        self.health_reply = health_reply if health_reply is not None else _pi_health()
        self.states_reply = states_reply if states_reply is not None else _pi_states()
        self.labels_reply = labels_reply if labels_reply is not None else {'labels': ['cup']}
        self.trace_steps = list(trace_steps) if trace_steps is not None else [_pi_smoothing_step()]
        self.trace_rules = trace_rules if trace_rules is not None else dict(PI_RULES)
        self.story_reply = story_reply if story_reply is not None else _pi_story()
        self.photos = dict(photos) if photos is not None else {PHOTO_ID: PHOTO_BYTES}
        self.problem = problem
        self.asked: list[float | None] = []
        self.asked_states: list[float] = []
        self.asked_traces: list[str] = []
        self.asked_stories: list[str] = []
        self.asked_photos: list[str] = []

    def fetch(self, since: float | None) -> dict[str, Any]:
        self.asked.append(since)
        return self._answer(self.reply)

    def health(self) -> dict[str, Any]:
        return self._answer(self.health_reply)

    def states(self, since: float) -> dict[str, Any]:
        self.asked_states.append(since)
        return self._answer(self.states_reply)

    def traced_labels(self) -> dict[str, Any]:
        return self._answer(self.labels_reply)

    def trace(self, label: str) -> dict[str, Any]:
        self.asked_traces.append(label)
        steps = self.trace_steps if label in self.labels_reply.get('labels', []) else []
        return self._answer({'label': label, 'steps': steps, 'rules': self.trace_rules})

    def story(self, label: str) -> dict[str, Any]:
        self.asked_stories.append(label)
        if label in self.labels_reply.get('labels', []):
            return self._answer({**self.story_reply, 'label': label})
        return self._answer({'label': label, 'timeline': [], 'photos': []})

    def photo(self, photo_id: str) -> bytes | None:
        self.asked_photos.append(photo_id)
        self._answer({})
        return self.photos.get(photo_id)

    def _answer(self, reply: dict[str, Any]) -> dict[str, Any]:
        if self.problem:
            raise StatsUnavailable(self.problem)
        return reply


PHOTO_ID = 'cup-1'
PHOTO_BYTES = b'\xff\xd8\xff\xe0 a small JPEG'
# The Pi's smoothing numbers in a trace (GET /api/objects/smoothing/trace → rules), a little apart from this
# computer's settings, as a Pi with its own settings.local.yaml or sensitivity would be.
PI_RULES = {'on_confidence': 0.55, 'off_confidence': 0.45, 'appear_sec': 4.5, 'gone_sec': 4.5, 'window_pictures': 5,
            'vote_share': 0.5}


def _pi_states() -> dict[str, Any]:
    """Like the Pi's GET /api/objects/states: a cup on the desk since 5.0, and the change that put it there."""
    return {'objects': {'cup': {'state': 'PRESENT', 'since': 5.0}}, 'watched_since': 1.0,
            'changes': [{'ts': 5.0, 'label': 'cup', 'change': 'appeared'}], 'now': 12.0}


def _pi_smoothing_step() -> dict[str, Any]:
    """One step of the Pi's GET /api/objects/smoothing/trace, as it comes over the Wi-Fi (center as a list)."""
    step = SmoothingStep(ts=5.0, conf=0.82, box_seen=True, votes_yes=3, votes_total=3, seen=True,
                         center=(320.0, 240.0), phase='PRESENT', heading=None, waiting_sec=None)
    return {**asdict(step), 'center': [320.0, 240.0]}


def _pi_story() -> dict[str, Any]:
    """Like the Pi's GET /api/objects/<label>/story (desktalk/web/stories.py): the cup appeared, with its picture."""
    photo = {'id': PHOTO_ID, 'ts': 5.0, 'why': 'appeared', 'confidence': 0.82, 'box': [280, 200, 360, 280],
             'size': [640, 480]}
    step = {'ts': 5.0, 'source': 'state', 'kind': 'appeared', 'confidence': 0.82, 'seen_for_sec': None,
            'on_desk': None, 'step': None, 'before': 'UNKNOWN', 'after': 'PRESENT', 'photo': photo,
            'what': 'appeared'}
    return {'label': 'cup', 'timeline': [step], 'photos': [photo]}


def _pi_health() -> dict[str, Any]:
    """Like the Pi's GET /api/health (desktalk/web/routers/health.py): its camera sending, nothing wrong. Its keys
    are the real route's (tests/integration/test_web_dev.py checks)."""
    def check(level: str, value: float | None, unit: str) -> dict[str, Any]:
        return {'level': level, 'value': value, 'unit': unit}

    return {
        'uptime_sec': 600.0, 'jobs': [],
        'scheduler': [{'name': 'compact event log', 'every_sec': 3600.0, 'runs': 1, 'last_error': None,
                       'next_in_sec': 1800.0, 'last_run_at': 8.0, 'next_run_at': 11.0}],
        'events': [{'name': 'event log', 'handled': 12, 'waiting': 0, 'dropped': 0, 'errors': 0}],
        'camera': {'source': 'pi', 'connected': True, 'fps': 3.0, 'pictures_received': 1800},
        'pi': {'last_frame_id': 'f-1', 'camera': 'picamera2', 'photos_stored': 3, 'disk_free_mb': 20000,
               'last_cleanup_ts': 9.0, 'cpu_temp_c': 52.0, 'throttled': '0x0', 'upload_format': 2, 'detector': 'ok',
               'detector_reason': '', 'detect_ms': 120.0, 'model': 'yolo11n', 'clock_off_sec': 0.1},
        'detection': {'pictures_analysed': 0, 'detect_ms': None, 'problem': None, 'model': 'on the Pi'},
        'ai': {'ready': False, 'label': '', 'problem': None},
        'checks': {'camera': check('ok', None, ''), 'detections': check('ok', 0.4, 's'),
                   'pi_temperature': check('ok', 52.0, '°C'), 'card': check('ok', 20000.0, 'MB'),
                   'pi_heartbeat': check('ok', 2.0, 's'), 'card_rest': check('ok', 9000.0, 'MB'),
                   'saving': check('ok', None, '')},
    }
