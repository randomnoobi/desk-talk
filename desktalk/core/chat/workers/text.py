"""
Reading the AI's JSON answers, for the background chat jobs.

    json_in(reply)      the {...} object in an AI reply, or None

Keeping a line to one speaker and hiding action tags use the chat's own helpers
(desktalk/core/chat/replies.py and tracker_actions.py).
"""

from __future__ import annotations

import json
import re
from typing import Any

_OBJECT = re.compile(r'\{[\s\S]*\}')


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
