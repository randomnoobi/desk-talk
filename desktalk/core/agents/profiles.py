"""
Agent profiles: who each detected object becomes (config/agents.yaml).

    book = ProfileBook(config.agents_file, store, personality_max_chars=400)
    book.for_label('laptop')      # → the "Computer" profile
    book.for_label('stapler')     # → a new profile made from `default` ("Stapler")
    book.for_label('person')      # → None (in the ignore list)

Personalities the AI invents for objects without a block are kept in the store under
"learned_personalities" (data/learned_personalities.json, as before), so they stay the same next time.
"""

from __future__ import annotations

import logging
import threading
import zlib

from desktalk.config import AgentsFile
from desktalk.domain import HABITS, AgentProfile
from desktalk.ports import KeyValueStore
from desktalk.util import slug, title_label

log = logging.getLogger(__name__)

LEARNED_KEY = 'learned_personalities'
# Colors for agents without their own; the order keeps each agent's color the same as before.
PALETTE = ('cobalt', 'teal', 'tang', 'mustard', 'coral', 'plum', 'moss', 'slate')


def color_for(key: str) -> str:
    return PALETTE[zlib.crc32(str(key).encode()) % len(PALETTE)]


class ProfileBook:
    """Every agent: the ones in config/agents.yaml, and ones made on the fly for other objects."""

    def __init__(self, agents_file: AgentsFile, store: KeyValueStore, *, personality_max_chars: int):
        self._default = agents_file.default
        self._ignore = {str(x).lower() for x in agents_file.ignore}
        self._relationships = [(frozenset(r.between), r.risk) for r in agents_file.relationships]
        self._store = store
        self._max_chars = int(personality_max_chars)
        self._lock = threading.Lock()          # the roster and the web server both make profiles
        self._profiles: dict[str, AgentProfile] = {}
        self._label_to_id: dict[str, str] = {}
        for pid, agent in agents_file.agents.items():
            labels = [str(x).lower() for x in agent.labels]
            name = agent.name or title_label(pid)
            obj = agent.object or agent.name or title_label(pid)
            prof = AgentProfile(
                id=str(pid), name=name, labels=labels, object=obj,
                nickname=agent.nickname or '',
                icon=agent.icon or self._default.icon,
                art=agent.art or None,
                color=agent.color or color_for(pid),
                about=agent.about or '',
                personality=agent.personality or self._default.personality.format(object=obj),
                temperature=agent.temperature if agent.temperature is not None else self._default.temperature,
                habits=[h for h in agent.habits if h in HABITS],
                tracker=agent.tracker,
                fragile=agent.fragile,
                min_confidence=agent.min_confidence,
                too_close=agent.too_close or self._default.too_close,
                hello=agent.hello or '',
            )
            self._profiles[prof.id] = prof
            for label in labels:
                self._label_to_id.setdefault(label, prof.id)
        self._learned = self._load_learned()

    # ── lookups ──
    def is_ignored(self, label: str) -> bool:
        return str(label).lower() in self._ignore

    def for_label(self, label: str) -> AgentProfile | None:
        """The agent this detector word belongs to (made from `default` if it has no block)."""
        word = str(label).lower()
        if not word or word in self._ignore:
            return None
        with self._lock:
            pid = self._label_to_id.get(word)
            if pid:
                return self._profiles[pid]
            pid = slug(word)
            if pid in self._profiles:          # another word already made this id
                self._label_to_id[word] = pid
                return self._profiles[pid]
            obj = word.split('/')[0].strip()
            learned = self._learned.get(pid)
            prof = AgentProfile(
                id=pid, name=title_label(word), labels=[word], object=obj,
                icon=self._default.icon, color=color_for(pid),
                about=self._default.about.format(object=obj),
                personality=learned or self._default.personality.format(object=obj),
                temperature=self._default.temperature,
                too_close=self._default.too_close,
                hello=self._default.hello,
                auto=True, learned=bool(learned),
            )
            self._profiles[pid] = prof
            self._label_to_id[word] = pid
            return prof

    def get(self, agent_id: str) -> AgentProfile | None:
        return self._profiles.get(agent_id)

    def all(self) -> list[AgentProfile]:
        with self._lock:
            return list(self._profiles.values())

    def ids_for_habit(self, habit: str) -> list[str]:
        return [p.id for p in self.all() if habit in p.habits]

    def labels_for_habit(self, habit: str) -> set[str]:
        return {label for p in self.all() if habit in p.habits for label in p.labels}

    def relationship_risk(self, a: str, b: str) -> str | None:
        pair = frozenset((a, b))
        return next((risk for between, risk in self._relationships if between == pair), None)

    def mention_words(self) -> dict[str, str]:
        """Every word that can @mention an agent → its id."""
        out: dict[str, str] = {}
        for p in self.all():
            for w in [p.id, p.name, p.nickname, p.object, *p.labels]:
                w = str(w or '').lower().strip()
                if w:
                    out.setdefault(w, p.id)
                    out.setdefault(w.split('/')[0].strip(), p.id)
        return out

    # ── personalities written by the AI ──
    def _load_learned(self) -> dict[str, str]:
        data = self._store.load(LEARNED_KEY, {})
        if not isinstance(data, dict):
            log.warning('Learned personalities are not a list of names and texts; starting without them')
            return {}
        return {str(k): str(v) for k, v in data.items()}

    def set_personality(self, agent_id: str, text: str) -> None:
        text = ' '.join(str(text).split())[:self._max_chars]
        if not text:
            return
        with self._lock:
            prof = self._profiles.get(agent_id)
            if prof:
                prof.personality = text
                prof.learned = True
            self._learned[agent_id] = text
            data = dict(self._learned)
        try:
            self._store.save(LEARNED_KEY, data)
        except OSError as exc:
            log.warning('Could not save the personality for %s: %s', agent_id, exc)
