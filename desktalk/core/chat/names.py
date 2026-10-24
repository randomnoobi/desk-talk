"""
Names in the chat: @mentions, agents named in plain text, and who answers a message in the group.

The words that can name an agent come from Profiles.mention_words() (desktalk/ports.py).
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterable, Mapping, Sequence

from desktalk.domain import LENS, AgentProfile

# Words that introduce an object as the TOPIC of the sentence rather than the
# one being spoken to (e.g. "how do you feel about computer", "stronger than cup").
_TOPIC_PREPS = ('about', 'than', 'regarding', 'concerning', 'like', 'vs', 'versus')


def speaker_names(profiles: Iterable[AgentProfile]) -> dict[str, str]:
    """Lower-case names and ids → agent id, for spotting "Cup: ..." lines (Lens included)."""
    out = {}
    for p in profiles:
        out[p.name.lower()] = p.id
        out[p.id.lower()] = p.id
    out[LENS] = LENS
    return out


def parse_mentions(text: str, words: Mapping[str, str]) -> list[str]:
    """@mentions in the user's text → agent ids (in order, no repeats)."""
    out = []
    for m in re.findall(r'@(\w[\w\s]*\w|\w)', (text or '').lower()):
        m = m.strip()
        pid = words.get(m) or words.get(m.split()[0] if m.split() else m)
        if pid and pid not in out:
            out.append(pid)
    return out


def detect_named(message: str, words: Mapping[str, str]) -> list[str]:
    """Agent names / aliases in plain text (no @ needed)."""
    if not message:
        return []
    low = message.lower()
    found = []
    for keyword, pid in words.items():
        if re.search(r'\b' + re.escape(keyword) + r'\b', low) and pid not in found:
            found.append(pid)
    return found


def addressed_agent(message: str, candidates: list[str], words: Mapping[str, str]) -> str:
    """Among several named objects, pick the one being spoken to (not the topic)."""
    low = (message or '').lower()
    kw_by_id: dict[str, list] = {}
    for kw, pid in words.items():
        if pid in candidates:
            kw_by_id.setdefault(pid, []).append(kw)
    prep_alt = '|'.join(_TOPIC_PREPS)
    addressed = [pid for pid in candidates if not any(
        re.search(r'\b(?:' + prep_alt + r')\s+(?:the\s+|my\s+|your\s+|that\s+)?' + re.escape(kw) + r'\b', low)
        for kw in kw_by_id.get(pid, []))]
    return addressed[0] if addressed else candidates[0]


def group_speaker_order(message: str, words: Mapping[str, str], members: Sequence[str], known: Sequence[str],
                        rng: random.Random) -> list[str]:
    """Who answers a message in the group chat.

    @mentions are honored as they are (all of them). Otherwise, if the text names one object it
    answers; if it names several, only the one being spoken to replies (the others are the topic).
    With no name, a random agent that is on the desk answers."""
    speakers = [p for p in parse_mentions(message, words) if p in known]
    if speakers:
        return speakers
    named = [p for p in detect_named(message, words) if p in known]
    if len(named) == 1:
        return named
    if len(named) > 1:
        return [addressed_agent(message, named, words)]
    return [rng.choice(list(members))] if members else []
