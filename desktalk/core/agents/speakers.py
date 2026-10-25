"""Who says something about a habit in the chat: an agent that speaks for it, or Lens (the camera itself)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from desktalk.domain import LENS
from desktalk.ports import Profiles


def speaker_for(habits: Iterable[str], member_ids: Sequence[str], profiles: Profiles) -> str:
    """The first agent in the chat with the first habit that has one, else Lens."""
    for habit in habits:
        for agent_id in member_ids:
            profile = profiles.get(agent_id)
            if profile and habit in profile.habits:
                return agent_id
    return LENS
