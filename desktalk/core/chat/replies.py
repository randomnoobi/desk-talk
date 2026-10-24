"""
Checks on an AI reply before it goes into the chat: keep it to one speaker, spot a reply that was cut
off, and refuse dramatic claims that nothing the camera saw (or you said) supports.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from desktalk.domain import AgentProfile

_TRAILING_WORDS = ('the', 'a', 'an', 'is', 'are', 'to', 'and', 'but', 'or', 'in', 'of', 'that', 'this')

_UNSUPPORTED_DANGER_RE = re.compile(
    r'\b(crack(?:ing|ed)?|shatter(?:ing|ed)?|crush(?:ing|ed)?|'
    r'break(?:ing)?|burn(?:ing|ed)?|fire|explode|destroy(?:ed)?|'
    r'at any moment|about to (?:break|fall|shatter|explode))\b',
    re.I,
)
_HAZARD_RE = re.compile(r'\b(too close|spill|danger|hazard|risk|edge|fall(?:ing)? off)\b', re.I)


def looks_cut_off(text: str, min_words: int) -> bool:
    """Too short, or ends mid-sentence ("...move it to the")."""
    return len(text.split()) < min_words or text.rstrip().endswith(_TRAILING_WORDS)


def single_speaker_text(text: str, agent: AgentProfile, all_names: Mapping[str, str], max_chars: int) -> str:
    """Keep one object per AI reply: drop lines the AI wrote for other objects ("Computer: ...").
    `all_names` maps lower-case names and ids → agent id."""
    if not text:
        return ''
    current = (agent.name or '').lower()
    kept = []
    saw_other_speaker = False
    for raw in str(text).splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r'^([A-Za-z][A-Za-z ]{0,30})(?:\s*\(me\))?\s*:\s*(.+)$', line)
        if m:
            speaker = m.group(1).strip().lower()
            body = m.group(2).strip()
            if speaker == current or all_names.get(speaker) == agent.id:
                kept.append(body)
            elif speaker in all_names:
                saw_other_speaker = True
            else:
                kept.append(line)
        else:
            kept.append(line)
    cleaned = ' '.join(kept).strip()
    if saw_other_speaker and not cleaned:
        return ''
    # If everything was stripped but the original had content, keep the original without a leading
    # name (common with local models).
    if not cleaned and text.strip():
        cleaned = re.sub(r'^[A-Za-z][A-Za-z ]{0,30}(?:\s*\(me\))?\s*:\s*', '', text.strip(), count=1)
    return cleaned[:max_chars]


def _history_text(history: Iterable[Any] | None) -> str:
    return ' '.join(str(h.get('text', '')) for h in (history or []) if isinstance(h, dict))


def response_violates_evidence(text: str, context: Mapping[str, Any], user_message: str = '',
                               history: Iterable[Any] | None = None, strict: bool = True) -> bool:
    """Reject dramatic or hazard claims that nothing live (or the user) supports.
    With strict=False only extreme unsupported danger words are blocked."""
    if not text:
        return False
    evidence = ' '.join([context.get('summary', ''), user_message or '', _history_text(history)]).lower()
    low = text.lower()
    if _UNSUPPORTED_DANGER_RE.search(low) and not _UNSUPPORTED_DANGER_RE.search(evidence):
        return True
    said_no_danger = re.search(r'\b(no|not|nothing|without)\b.{0,24}\b(danger|risk|hazard|too close|spill)\b', low)
    return bool(strict and _HAZARD_RE.search(low) and not context.get('dangers') and not said_no_danger)
