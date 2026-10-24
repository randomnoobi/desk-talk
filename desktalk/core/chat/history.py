"""
The chat history the app sends with a message: cleaned up, and the older part squeezed into a recap.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from desktalk.config import ChatSettings


def last(items: Sequence, n: int) -> list:
    """The last n items (none when n is 0; items[-0:] would be all of them)."""
    return list(items[-n:]) if n > 0 else []


def sanitize_chat_history(raw: Any, chat: ChatSettings) -> list[dict]:
    """Earlier turns sent by the app (1-on-1 or group) → [{role, text, name?}, ...]."""
    if not raw or not isinstance(raw, list):
        return []
    out = []
    for item in last(raw, chat.history_from_app):
        if not isinstance(item, dict):
            continue
        role = (item.get('role') or '').strip().lower()
        if role not in ('user', 'assistant'):
            continue
        text = item.get('text', '')
        if not isinstance(text, str):
            text = str(text)
        text = text.strip()[:chat.message_max_chars]
        if not text:
            continue
        d = {'role': role, 'text': text}
        name = (item.get('name') or '').strip()[:chat.name_max_chars]
        if name:
            d['name'] = name
        out.append(d)
    return out


def budget_history(history: list[dict], verbatim_turns: int, chat: ChatSettings, local: bool) -> tuple[list, str]:
    """Split the history into (recent turns sent word for word, a one-line recap of older ones).

    The recent turns go to the AI as real messages so the reply stays tied to the latest message;
    anything older is squeezed into a short recap line. A local AI (Ollama) gets fewer turns."""
    k = chat.ollama_history_turns if local else verbatim_turns
    if not history:
        return [], ''
    if len(history) <= k:
        return history, ''
    older, recent = (history[:-k], history[-k:]) if k > 0 else (history, [])
    bits = []
    for turn in last(older, chat.summary_turns):
        who = 'User' if turn.get('role') == 'user' else (turn.get('name') or 'an object')
        snippet = ' '.join(str(turn.get('text', '')).split())
        if len(snippet) > chat.summary_chars:
            snippet = snippet[:chat.summary_chars].rstrip() + '…'
        if snippet:
            bits.append(f'{who}: "{snippet}"')
    return recent, ' | '.join(bits)
