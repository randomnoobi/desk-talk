"""
The tracked lost items and the words for them (settings → lost_items.items), and spotting a
"have you seen my keys?" question (config/prompts.yaml → lost_items.triggers and other_things).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from desktalk.config import LostItemsSettings
from desktalk.config.prompts import LostItemsPrompts


def words_in(text: str) -> set[str]:
    return set(re.findall(r'[a-z0-9]+', (text or '').lower()))


class TrackedItems:
    """`items`: item id → name, labels (detector words), alert_after (seconds) and words (what you might type).
    `words`: config/prompts.yaml → lost_items (the question words, and the other things you can ask about)."""

    def __init__(self, settings: LostItemsSettings, words: LostItemsPrompts):
        self._triggers = tuple(words.triggers)
        self._other_things = {thing.lower() for thing in words.other_things}     # askable, though not tracked
        self.items: dict[str, dict[str, Any]] = {
            item_id: {'name': item.name, 'labels': {label.lower() for label in item.labels},
                      'alert_after': item.alert_after_min * 60,
                      'words': {word.lower() for word in item.words} or {item_id}}
            for item_id, item in settings.items.items()}

    def in_text(self, text: str) -> list[str]:
        """The tracked items named in what you typed."""
        words = words_in(text)
        return [item_id for item_id, item in self.items.items() if words & item['words']]

    def in_labels(self, labels: Iterable[str]) -> set[str]:
        """Tracked items among detector labels or the vision scan's free text ("car keys", "airpods case")."""
        labels = {str(label).lower() for label in labels}
        found = set()
        for item_id, item in self.items.items():
            if labels & item['labels']:
                found.add(item_id)
                continue
            for label in labels:
                if 'keyboard' in label and item_id == 'keys':
                    continue
                if words_in(label) & item['words']:
                    found.add(item_id)
                    break
        return found

    def is_question(self, text: str) -> bool:
        """The text asks whether something was seen ("where are my ...")."""
        lowered = (text or '').lower()
        return any(trigger in lowered for trigger in self._triggers)

    def is_lost_query(self, text: str) -> bool:
        """A "have you seen my ...?" question about a tracked item or another everyday thing."""
        return self.is_question(text) and (bool(self.in_text(text)) or bool(words_in(text) & self._other_things))
