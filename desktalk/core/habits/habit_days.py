"""
One day's habit numbers (HabitDay), and keeping them (HabitDays): loading today's file, saving it now and then,
and starting a new day at midnight.

The day is saved in the store under "habits/<day>" (data/habits/<day>.json). The file's layout is exactly what it
has always been (tests/fixtures/habit_day_before_cleanup.json), so old day files still load and the Habits page
reads the same numbers.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field
from typing import Any, TypedDict

from desktalk.ports import Clock, KeyValueStore

log = logging.getLogger(__name__)

HOURS_IN_DAY = 24
COUNTERS = ('danger_count', 'lost_alerts', 'nudges_sent')     # the counters other parts bump by name

# What happened, for the chat: (habit, action, words, extra fields for the HabitEvent)
Said = tuple[str, str, str, dict[str, Any]]


class Drink(TypedDict):
    ts: float
    ml: int
    how: str           # camera, button or chat


class Break(TypedDict):
    ts: float
    minutes: int
    how: str           # left the desk, camera off or button


class Food(TypedDict):
    label: str
    healthy: bool
    ts: float
    how: str           # camera or button


def day_key(day: str) -> str:
    """The store key for one day's numbers: habits/2026-10-21."""
    return f'habits/{day}'


@dataclass
class HabitDay:
    """Today's numbers. The fields, in this order, are the keys of the day file."""

    day: str
    sips: int = 0
    water_ml: int = 0
    last_sip_ts: float | None = None
    drinks: list[Drink] = field(default_factory=list)
    breaks: int = 0
    last_break_ts: float | None = None
    break_log: list[Break] = field(default_factory=list)
    longest_sit_sec: float = 0
    desk_sec: float = 0.0
    phone_free_sec: float = 0.0
    phone_on_desk_sec: float = 0.0
    best_phone_free_sec: float = 0.0
    food: list[Food] = field(default_factory=list)
    sunlight_ts: float | None = None
    sunlight_minutes: int = 0
    afternoon_light_ts: float | None = None
    hourly_activity: list[float] = field(default_factory=lambda: [0] * HOURS_IN_DAY)   # desk seconds per hour
    objects_seen: list[str] = field(default_factory=list)                              # agent names
    danger_count: int = 0
    lost_alerts: int = 0
    nudges_sent: int = 0
    summary_sent: bool = False

    @classmethod
    def from_saved(cls, day: str, saved: Any) -> HabitDay | None:
        """The day as saved in its file. Keys this version doesn't know are left out, and ones the file doesn't
        have start at zero. None when the file is broken or is for another day."""
        if not isinstance(saved, dict) or saved.get('day') != day:
            return None
        names = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in saved.items() if k in names})

    def to_dict(self) -> dict[str, Any]:
        """The day as it goes in its file (a deep copy)."""
        return dataclasses.asdict(self)

    def bump(self, key: str, amount: int = 1) -> None:
        """Count something other parts notice: danger_count, lost_alerts or nudges_sent."""
        if key not in COUNTERS:
            raise ValueError(f'{key} is not a daily counter (use one of {", ".join(COUNTERS)})')
        setattr(self, key, getattr(self, key) + amount)

    def add_sip(self, now: float, ml: float, how: str, keep: int) -> None:
        """A drink of `ml` ml; only the newest `keep` drinks are listed. ValueError unless 0 < ml < infinity."""
        if not 0 < ml < float('inf'):           # also false for nan
            raise ValueError(f'a drink must be a number of ml above 0 (got {ml})')
        self.sips += 1
        self.water_ml = int(self.water_ml + ml)
        self.last_sip_ts = now
        self.drinks = [*self.drinks, Drink(ts=now, ml=int(ml), how=how)][-keep:]

    def add_break(self, now: float, minutes: int, how: str, keep: int) -> None:
        """A break of `minutes`; only the newest `keep` breaks are listed."""
        self.breaks += 1
        self.last_break_ts = now
        self.break_log = [*self.break_log, Break(ts=now, minutes=minutes, how=how)][-keep:]

    def add_food(self, label: str, healthy: bool, now: float, how: str) -> None:
        self.food.append(Food(label=label, healthy=healthy, ts=now, how=how))


class HabitDays:
    """Keeps today's HabitDay: loads it, saves it when it changed, and starts a new one on a new local day
    (the clock's). A day whose save failed is kept and tried again at every save.

    Not locked on its own: the habit tracker holds its lock around every call."""

    def __init__(self, store: KeyValueStore, clock: Clock):
        self._store = store
        self._clock = clock
        self.today = self._load(clock.local_day(clock.now()))
        self._unsaved: dict[str, HabitDay] = {}     # earlier days whose save failed: kept until it works
        self._dirty = False
        self._last_save = 0.0

    def _load(self, day: str) -> HabitDay:
        saved = self._store.load(day_key(day))
        loaded = HabitDay.from_saved(day, saved)
        if loaded is None and saved is not None:
            log.warning('The habit file for %s is not for that day or is broken; starting the day fresh', day)
        return loaded or HabitDay(day=day)

    def changed(self) -> None:
        """Today's numbers changed: the next save writes them."""
        self._dirty = True

    def save_due(self, now: float, every_sec: float) -> bool:
        """It's been `every_sec` since the last save."""
        return now - self._last_save >= every_sec

    def save(self, force: bool = False) -> None:
        """Save today's numbers if they changed (or always, with force), and any earlier day not saved yet."""
        self._save_earlier_days()
        if not (self._dirty or force):
            return
        data = self.today.to_dict()
        self._dirty = False
        self._last_save = self._clock.now()
        try:
            self._store.save(day_key(self.today.day), data)
        except OSError as exc:
            self._dirty = True              # try again next time
            log.warning('Could not save the habits for %s: %s', self.today.day, exc)

    def _save_earlier_days(self) -> None:
        for day, kept in list(self._unsaved.items()):
            try:
                self._store.save(day_key(day), kept.to_dict())
            except OSError as exc:
                log.debug('Still could not save the habits for %s: %s', day, exc)
                continue
            del self._unsaved[day]
            log.info('Saved the habits for %s after all', day)

    def roll(self, now: float) -> bool:
        """Start the day `now` is in, if it's a new one (saving the old one first). True when it was new."""
        day = self._clock.local_day(now)
        if day == self.today.day:
            return False
        self.save(force=True)
        if self._dirty:
            # The old day couldn't be saved: keep its numbers and try again at every save.
            self._unsaved[self.today.day] = self.today
        self.today = self._unsaved.pop(day, None) or self._load(day)
        return True
