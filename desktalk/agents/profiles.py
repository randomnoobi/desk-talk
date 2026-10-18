"""
Agent profiles: who each detected object becomes (config/agents.yaml).

    book = ProfileBook(settings.agents_file)
    book.for_label('laptop')      # → the "Computer" profile
    book.for_label('stapler')     # → a new profile made from `default` ("Stapler")
    book.for_label('person')      # → None (in the ignore list)

Personalities the AI invents for objects without a block are kept in
data/learned_personalities.json so they stay the same next time.
"""

from __future__ import annotations

import json
import threading
import zlib
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .. import paths
from ..util import slug, title_label

PALETTE = ['cobalt', 'teal', 'tang', 'mustard', 'coral', 'plum', 'moss', 'slate']
HABITS = ('hydration', 'breaks', 'light', 'phone_free', 'screens', 'food', 'tasks')


@dataclass
class AgentProfile:
    id: str
    name: str
    labels: list = field(default_factory=list)
    object: str = ''               # what it is, for the AI ("Cup", "stapler")
    nickname: str = ''
    icon: str = '✨'
    art: str | None = None
    color: str = 'plum'
    about: str = ''
    personality: str = ''
    temperature: float = 0.8
    habits: list = field(default_factory=list)
    tracker: str | None = None
    fragile: bool = False
    min_confidence: float | None = None
    too_close: str = ''
    hello: str = ''
    auto: bool = False             # made on the fly for an object without its own block
    learned: bool = False          # personality written by the AI

    def public(self) -> dict:
        """What the app needs to draw this agent."""
        d = asdict(self)
        d.pop('personality', None)
        d.pop('temperature', None)
        d.pop('too_close', None)
        d.pop('hello', None)
        d.pop('min_confidence', None)
        return d

    def too_close_text(self, other_name: str) -> str:
        return (self.too_close or '{other} is too close. Could you move it back?').format(other=other_name)

    def hello_text(self) -> str:
        return (self.hello or '').format(object=self.object or self.name)


class ProfileBook:
    def __init__(self, agents_cfg: dict | None, learned_path: Path | None = None):
        cfg = dict(agents_cfg or {})
        self.default = dict(cfg.get('default') or {})
        self.ignore = {str(x).lower() for x in (cfg.get('ignore') or [])}
        self.relationships = list(cfg.get('relationships') or [])
        self.learned_path = Path(learned_path) if learned_path else paths.DATA_DIR / 'learned_personalities.json'
        self._lock = threading.Lock()
        self.profiles: dict[str, AgentProfile] = {}
        self.label_to_id: dict[str, str] = {}
        for pid, raw in (cfg.get('agents') or {}).items():
            raw = dict(raw or {})
            labels = [str(x).lower() for x in (raw.get('labels') or [])]
            prof = AgentProfile(
                id=str(pid),
                name=str(raw.get('name') or title_label(pid)),
                labels=labels,
                object=str(raw.get('object') or raw.get('name') or title_label(pid)),
                nickname=str(raw.get('nickname') or ''),
                icon=str(raw.get('icon') or self.default.get('icon', '✨')),
                art=raw.get('art') or None,
                color=str(raw.get('color') or self._color_for(pid)),
                about=str(raw.get('about') or ''),
                personality=str(raw.get('personality') or self.default.get('personality', '')),
                temperature=float(raw.get('temperature', self.default.get('temperature', 0.8))),
                habits=[h for h in (raw.get('habits') or []) if h in HABITS],
                tracker=raw.get('tracker') or None,
                fragile=bool(raw.get('fragile', False)),
                min_confidence=raw.get('min_confidence'),
                too_close=str(raw.get('too_close') or self.default.get('too_close', '')),
                hello=str(raw.get('hello') or ''),
            )
            self.profiles[prof.id] = prof
            for lab in labels:
                self.label_to_id.setdefault(lab, prof.id)
        self._learned = self._load_learned()

    # ---- lookups ----
    @staticmethod
    def _color_for(key: str) -> str:
        return PALETTE[zlib.crc32(str(key).encode()) % len(PALETTE)]

    def is_ignored(self, label: str) -> bool:
        return str(label).lower() in self.ignore

    def for_label(self, label: str) -> AgentProfile | None:
        """The agent this detector word belongs to (made from `default` if it has no block)."""
        lab = str(label).lower()
        if not lab or lab in self.ignore:
            return None
        with self._lock:
            pid = self.label_to_id.get(lab)
            if pid:
                return self.profiles[pid]
            pid = slug(lab)
            if pid in self.profiles:          # another word already made this id
                self.label_to_id[lab] = pid
                return self.profiles[pid]
            name = title_label(lab)
            obj = lab.split('/')[0].strip()
            learned = self._learned.get(pid)
            prof = AgentProfile(
                id=pid, name=name, labels=[lab], object=obj,
                icon=str(self.default.get('icon', '✨')), color=self._color_for(pid),
                about=f'A {obj} that showed up on your desk.',
                personality=learned or str(self.default.get('personality', '')).format(object=obj),
                temperature=float(self.default.get('temperature', 0.8)),
                too_close=str(self.default.get('too_close', '')),
                hello=str(self.default.get('hello', '')),
                auto=True, learned=bool(learned),
            )
            self.profiles[pid] = prof
            self.label_to_id[lab] = pid
            return prof

    def get(self, pid: str) -> AgentProfile | None:
        return self.profiles.get(pid)

    def ids_for_habit(self, habit: str) -> list[str]:
        return [p.id for p in self.profiles.values() if habit in p.habits]

    def labels_for_habit(self, habit: str) -> set[str]:
        out: set[str] = set()
        for p in self.profiles.values():
            if habit in p.habits:
                out.update(p.labels)
        return out

    def relationship_risk(self, a: str, b: str) -> str | None:
        pair = {a, b}
        for r in self.relationships:
            if set(r.get('between') or []) == pair:
                return r.get('risk')
        return None

    def mention_words(self) -> dict[str, str]:
        """Every word that can @mention an agent → its id."""
        out: dict[str, str] = {}
        for p in list(self.profiles.values()):
            for w in [p.id, p.name, p.nickname, p.object, *p.labels]:
                w = str(w or '').lower().strip()
                if w:
                    out.setdefault(w, p.id)
                    out.setdefault(w.split('/')[0].strip(), p.id)
        return out

    # ---- personalities written by the AI ----
    def _load_learned(self) -> dict:
        try:
            data = json.loads(self.learned_path.read_text(encoding='utf-8'))
            return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def set_personality(self, pid: str, text: str) -> None:
        text = ' '.join(str(text).split())[:400]
        if not text:
            return
        with self._lock:
            prof = self.profiles.get(pid)
            if prof:
                prof.personality = text
                prof.learned = True
            self._learned[pid] = text
            data = dict(self._learned)
        try:
            self.learned_path.parent.mkdir(parents=True, exist_ok=True)
            self.learned_path.write_text(json.dumps(data, indent=2), encoding='utf-8')
        except OSError:
            pass
