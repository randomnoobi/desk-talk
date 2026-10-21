"""
Small text helpers the background chat jobs share.

    json_in(reply)                       the {...} object in an AI reply, or None
    single_speaker_text(text, a, names)  only agent `a`'s own words (the AI sometimes writes a whole script)
    strip_action_tags(text)              hide [LOG_SIP]-style tags that slipped into a line

single_speaker_text and strip_action_tags are the same as in desktalk/chat/context.py and
tracker_actions.py; Phase 3 can point these jobs at the message generator's copies instead.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from desktalk.domain import AgentProfile

_OBJECT = re.compile(r'\{[\s\S]*\}')
_SPEAKER_LINE = re.compile(r'^([A-Za-z][A-Za-z ]{0,30})(?:\s*\(me\))?\s*:\s*(.+)$')
_NAME_PREFIX = re.compile(r'^[A-Za-z][A-Za-z ]{0,30}(?:\s*\(me\))?\s*:\s*')
_ACTION_TAG = re.compile(r'\[(?:LOG_[A-Z_]+|(?:ADD|DONE|DEL)_TODO)(?::[^\]]*)?\]')
LINE_MAX_CHARS = 500        # a runaway reply is cut to this many letters


def json_in(reply: str | None) -> dict[str, Any] | None:
    """The JSON object in an AI reply (models like to add words around it). None if there isn't a good one."""
    if not reply:
        return None
    match = _OBJECT.search(reply)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def single_speaker_text(text: str | None, agent: AgentProfile, all_names: Mapping[str, str]) -> str:
    """Keep one agent's words: drop lines the AI wrote for other agents and the agent's own "Name:" prefix.
    `all_names` maps lower-case names and ids to agent ids."""
    if not text:
        return ''
    current = (agent.name or '').lower()
    kept = []
    saw_other_speaker = False
    for raw in str(text).splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _SPEAKER_LINE.match(line)
        if match:
            speaker = match.group(1).strip().lower()
            body = match.group(2).strip()
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
    if not cleaned and text.strip():
        # Everything was dropped but there were words: keep them, without a leading "Name:" (local models do this).
        cleaned = _NAME_PREFIX.sub('', text.strip(), count=1)
    return cleaned[:LINE_MAX_CHARS]


def strip_action_tags(text: str | None) -> str:
    """Remove action tags (e.g. Computer copying Cup's [LOG_SIP]) so they never show in the chat."""
    return re.sub(r'\s{2,}', ' ', _ACTION_TAG.sub('', text or '')).strip()
