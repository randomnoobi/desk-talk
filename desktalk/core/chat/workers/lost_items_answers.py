"""
Answering "have you seen my keys?" (/api/chat sends a lost-item question here).

The answer uses what the lost-items job saw (its sightings), the last vision scan's words, and, for an
item never spotted, a look by the vision AI at a saved photo tagged with it or at the live picture. The
chat's AI then puts the facts into words as the agent who speaks for tasks (or Lens); without an AI the
facts are said plainly. A button under the answer opens the desk photos (Captures).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from desktalk.config import AiReplies, LostItemsSettings
from desktalk.config.prompts import LostItemsPrompts
from desktalk.core.agents.speakers import speaker_for
from desktalk.core.chat import prompts as fill
from desktalk.core.chat.workers.ai import ask, look
from desktalk.core.chat.workers.lost_item_words import TrackedItems
from desktalk.domain import Photo
from desktalk.ports import ChatModel, Clock, DeskScene, LivePictures, PhotoAlbum, Profiles, RosterView
from desktalk.util import format_local_ts

CAPTURES = 'captures'           # what the app does when the button under the answer is pressed


@runtime_checkable
class LostItemsDesk(LivePictures, DeskScene, PhotoAlbum, Protocol):
    """What lost items use of the desk: the newest picture, what is in it (and what the last vision scan saw),
    and the proof photos. (bootstrap passes WholeDesk from desktalk/core/desk/, which is all three.)"""


class LostItemAnswers:
    """`sightings()`: item id → last_seen, alerted, source, last_near (the lost-items job's). `vision`: the AI that
    looks at pictures, or None. `photos_kept`: how many proof photos there can be (settings → snapshots.max_kept),
    all of them searched for one tagged with the item."""

    def __init__(self, clock: Clock, desk: LostItemsDesk, roster: RosterView, profiles: Profiles, chat: ChatModel,
                 vision: ChatModel | None, items: TrackedItems, sightings: Callable[[], dict[str, dict[str, Any]]],
                 settings: LostItemsSettings, replies: AiReplies, words: LostItemsPrompts, *,
                 default_temperature: float, photos_kept: int):
        self._clock = clock
        self._desk = desk
        self._roster = roster
        self._profiles = profiles
        self._chat = chat
        self._vision = vision
        self._items = items
        self._sightings = sightings
        self._settings = settings
        self._replies = replies
        self._words = words
        self._default_temperature = default_temperature
        self._photos_kept = photos_kept

    def answer(self, user_message: str) -> dict[str, Any]:
        """The chat's reply: {'speaker', 'cta', 'text'}. It makes at most settings.most_ai_calls() AI calls."""
        now = self._clock.now()
        wanted = self._items.in_text(user_message)
        records = self._sightings()
        scan_words = sorted(self._desk.scan_labels())
        speaker = speaker_for(('tasks',), self._roster.member_ids(), self._profiles)
        button = {'label': self._words.captures_button, 'action': CAPTURES}
        if not wanted:
            note = look(self._vision, self._desk.plain(),
                        fill.lost_anything(self._words, user_message[:self._settings.question_max_chars]),
                        background=False, what='a lost-item question')
            return {'speaker': speaker, 'cta': button, 'text': note or self._words.not_spotted}
        notes = {}
        for item_id in wanted:
            if (records.get(item_id) or {}).get('last_seen') is None:
                note = self._vision_note(item_id)
                if note:
                    notes[item_id] = note
        facts, plain = [], []
        for item_id in wanted:
            fact, words = self._what_we_know(item_id, records.get(item_id), notes.get(item_id), scan_words, now)
            facts.append(fact)
            plain.append(words)
        profile = self._profiles.get(speaker)
        lens = self._words.answer_as
        system = fill.lost_answer_system(self._words, profile.name if profile else lens.name,
                                         profile.object if profile else lens.object)
        user = self._words.answer_user.format(message=user_message[:self._settings.message_max_chars],
                                              facts='\n'.join(facts))
        text = ask(self._chat, [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                   self._replies.lost_answer, background=False, what='a lost-item answer',
                   own_temperature=profile.temperature if profile else self._default_temperature)
        return {'speaker': speaker, 'cta': button, 'text': (text or '\n\n'.join(plain)).strip()}

    def _what_we_know(self, item_id: str, record: dict[str, Any] | None, note: str | None, scan_words: list[str],
                      now: float) -> tuple[str, str]:
        """(a fact for the AI, the same in words for you) about one item."""
        w = self._words
        name = self._items.items[item_id]['name']
        last_seen = (record or {}).get('last_seen')
        if record is None or last_seen is None:
            if note:
                extra = w.vision_extra.format(note=note)
            else:
                extra = w.scan_extra.format(labels=', '.join(scan_words)) if scan_words else ''
            more = w.live_look.format(note=note) if note else w.see_captures
            return w.never_seen_fact.format(name=name, extra=extra), w.never_seen.format(name=name, more=more)
        ago = int(now - last_seen)
        when = format_local_ts(last_seen, utc_offset_sec=self._clock.utc_offset_sec(last_seen))
        shown = record['last_near'][:self._settings.nearby_mentioned]
        near = w.near.format(names=', '.join(shown)) if shown else ''
        if ago < self._settings.just_seen_sec:
            return (w.just_seen_fact.format(name=name, seconds=ago, when=when, near=near),
                    w.just_seen.format(name=name, when=when, near=near))
        minutes = max(1, ago // 60)
        return (w.seen_fact.format(name=name, minutes=minutes, when=when, near=near),
                w.seen.format(name=name, when=when, minutes=minutes, near=near))

    def _photo_with(self, item_id: str) -> Photo | None:
        """The newest saved photo tagged with the item (by the detector or the vision scan)."""
        for photo in self._desk.newest(self._photos_kept):
            if item_id in self._items.in_labels(set(photo.labels) | set(photo.vlm_labels)):
                return photo
        return None

    def _vision_note(self, item_id: str) -> str | None:
        """What the vision AI sees of an item never spotted: in a saved photo tagged with it, else the live picture."""
        if self._vision is None:
            return None
        name = self._items.items[item_id]['name']
        photo = self._photo_with(item_id)
        if photo:
            label = self._words.saved_photo_label.format(label=photo.label) if photo.label else ''
            when = format_local_ts(photo.ts, utc_offset_sec=self._clock.utc_offset_sec(photo.ts))
            about = self._words.saved_photo.format(when=when, label=label)
            note = look(self._vision, photo.jpeg, fill.lost_in_saved_photo(self._words, about, name),
                        background=False, what='a lost-item photo')
            if note and len(note.split()) >= self._settings.answer_min_words:
                return note.strip()[:self._settings.photo_note_max_chars]
        note = look(self._vision, self._desk.plain(), fill.lost_in_live_picture(self._words, name), background=False,
                    what='a lost-item look')
        return note.strip()[:self._settings.live_note_max_chars] if note else None
