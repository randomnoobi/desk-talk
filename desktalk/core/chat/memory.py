"""
What the characters remember, kept in CharacterMemory (desktalk/ports.py). This is the only part of the
chat that writes to it:

    ChatMemory      the lines each agent said (and to what), for its later replies and the small talk
    TrackerPanels   the to-do list the app sends for an agent's panel, and when you open or use the panel
"""

from __future__ import annotations

from typing import Any

from desktalk.config import ChatSettings
from desktalk.core.chat.tracker_actions import normalize_todo
from desktalk.ports import CharacterMemory


class ChatMemory:
    """`keep`: lines kept per agent (settings → llm.memory_per_agent). `reply_chars` / `user_chars`: how much
    of what the agent said and of your message is kept (settings → chat.memory_reply_chars /
    memory_user_chars; a part that never passes a message of yours can leave user_chars out)."""

    def __init__(self, memory: CharacterMemory, *, keep: int, reply_chars: int, user_chars: int = 0):
        self._memory = memory
        self._keep = keep
        self._reply_chars = reply_chars
        self._user_chars = user_chars

    def remember(self, agent_id: str, said: str, user_message: str = '') -> None:
        """Remember what an agent said (and to what), for later replies."""
        snippet = (said or '')[:self._reply_chars].strip()
        if not snippet:
            return
        entry = f'[You said] {snippet}'
        if user_message:
            entry = f'[User said "{user_message[:self._user_chars].strip()}"] → [You replied] {snippet}'
        self._memory.remember(agent_id, entry, self._keep)

    def recent(self, agent_id: str) -> list[str]:
        """What the agent remembers, oldest first."""
        return self._memory.recent(agent_id)


class TrackerPanels:
    """Settings: chat (todo_max_items, todo_max_chars, panel_views_kept, panel_action_max_chars)."""

    def __init__(self, memory: CharacterMemory, chat: ChatSettings):
        self._memory = memory
        self._chat = chat

    def save(self, agent_id: str, raw: Any) -> bool:
        """Keep the to-do list the app sent for an agent's panel. False if the list isn't valid."""
        widget = normalize_todo(raw, self._chat)
        if not widget:
            return False
        self._memory.set_widget(agent_id, widget)
        return True

    def note_view(self, agent_id: str, action: Any, now: float) -> None:
        """You opened (or used) an agent's panel. The action is cut to chat.panel_action_max_chars."""
        self._memory.note_widget_view(agent_id, str(action)[:self._chat.panel_action_max_chars], now,
                                      self._chat.panel_views_kept)
