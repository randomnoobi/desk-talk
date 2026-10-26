"""
TodoFile: the to-do list the server keeps (Book's panel), with optional due dates.

The app sends its list (POST /api/chat_todos, /api/chat_todos_sync) and loads it back when it opens
(GET /api/todos), so the reminders know the due dates with no page open. Saved in the store under
settings → habit_tracker.todos_key (data/todos.json) as {"items": [{"text", "done", "due"}]}; a list from
before due dates existed still loads.

Ticking a to-do off publishes a HabitEvent for 'tasks', so it shows in the event log and the chat.
"""

from __future__ import annotations

import logging
import re
import threading
from collections.abc import Sequence
from datetime import date
from typing import Any

from desktalk.domain import HabitEvent, Todo
from desktalk.ports import Clock, EventBus, KeyValueStore

log = logging.getLogger(__name__)

DAY_PATTERN = re.compile(r'\d{4}-\d{2}-\d{2}')       # a day as 2026-10-30, and nothing else


def parse_day(text: Any) -> date | None:
    """The day in a due date like '2026-10-30'; None for anything else ('', 'next friday', '2026-02-30')."""
    if not isinstance(text, str) or not DAY_PATTERN.fullmatch(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def clean_due(text: Any) -> str | None:
    """A due date kept as it came when it's a real day, else None (the to-do stays, with no date)."""
    day = parse_day(text)
    return day.isoformat() if day else None


class TodoFile:
    """Implements TodoList (desktalk/ports.py). `max_items` / `max_chars`: settings → chat.todo_max_items /
    chat.todo_max_chars. `done_words`: what's said when one is ticked off (prompts → habits.logged.task_done,
    blank {task})."""

    def __init__(self, store: KeyValueStore, bus: EventBus, clock: Clock, *, key: str, max_items: int,
                 max_chars: int, done_words: str):
        self._store = store
        self._bus = bus
        self._clock = clock
        self._key = key
        self._max_items = max_items
        self._max_chars = max_chars
        self._done_words = done_words
        self._lock = threading.Lock()          # the web routes and the reminders both come here
        saved = store.load(key)
        self._saved = isinstance(saved, dict) and isinstance(saved.get('items'), list)
        if saved is not None and not self._saved:
            log.warning('The saved to-do list is not {"items": [...]}; starting with an empty one')
        raw = saved.get('items', []) if isinstance(saved, dict) and self._saved else []
        self._items = self._clean([Todo(str(i.get('text', '')), bool(i.get('done')), i.get('due'))
                                   for i in raw if isinstance(i, dict)])

    def items(self) -> list[Todo]:
        with self._lock:
            return list(self._items)

    def has_list(self) -> bool:
        with self._lock:
            return self._saved

    def replace(self, items: Sequence[Todo], now: float) -> list[Todo]:
        kept = self._clean(items)
        with self._lock:
            was_open = {t.text for t in self._items if not t.done}
            self._store.save(self._key, {'items': [{'text': t.text, 'done': t.done, 'due': t.due} for t in kept]})
            self._items = kept
            self._saved = True
        ticked: list[str] = []
        for todo in kept:
            if todo.done and todo.text in was_open and todo.text not in ticked:
                ticked.append(todo.text)
        for text in ticked:
            self._bus.publish(HabitEvent(ts=now, habit='tasks', action='done', say=self._done_words.format(task=text),
                                         extra={'task': text}))
        return list(kept)

    def _clean(self, items: Sequence[Todo]) -> list[Todo]:
        """Text cut to size and trimmed, empty ones left out, a bad due date dropped, at most max_items."""
        out = []
        for t in items:
            text = str(t.text).strip()[:self._max_chars].strip()
            if text:
                out.append(Todo(text, bool(t.done), clean_due(t.due)))
        return out[:self._max_items]
