"""
Filling in the blanks of the chat's prompts. The words themselves are in config/prompts.yaml
(config.prompts, see desktalk/config/prompts.py); this only knows which blanks each one has.

Each function takes its part of config.prompts and returns the text (or messages) to send.
`a` is an AgentProfile (desktalk.domain): a.name, a.object, a.personality.
"""

from __future__ import annotations

from desktalk.config.prompts import (
    AlertPrompts,
    ChatPrompts,
    DailyRecapPrompts,
    HabitAreas,
    HabitReminderPrompts,
    LostItemsPrompts,
    PersonalityPrompts,
    SmallTalkPrompts,
)
from desktalk.domain import AgentProfile


def _agent(a: AgentProfile) -> dict[str, str]:
    return {'name': a.name, 'object': a.object, 'personality': a.personality}


def habit_area(areas: HabitAreas, a: AgentProfile) -> str:
    """The habits an agent speaks for, in words ("Hydration, Movement breaks")."""
    named = areas.model_dump(exclude={'no_habits'})
    return ', '.join(named.get(h, h) for h in a.habits) or areas.no_habits


# ── An agent reacting to something the camera saw (cup near laptop...) ──
def event_system(p: AlertPrompts, a: AgentProfile) -> str:
    return p.system.format(**_agent(a))


def event_user(p: AlertPrompts, a: AgentProfile, situation: str, previous: str | None = None,
               follow_up: int = 0) -> str:
    if previous and follow_up > 0:
        urgency = p.more_worried if follow_up == 1 else p.most_worried
        return p.follow_up.format(situation=situation, previous=previous, name=a.name, urgency=urgency)
    return p.first.format(situation=situation, name=a.name, object=a.object)


# ── Chatting with the user (1-on-1 or in the group) ──
def chat_identity(p: ChatPrompts, a: AgentProfile, habit_area: str, action_instructions: str, short: bool) -> str:
    """`short`: the shorter rules, for AIs on this computer (Ollama)."""
    text = p.identity_short if short else p.identity
    return text.format(**_agent(a), habit_area=habit_area, action_instructions=action_instructions)


def reanchor(p: ChatPrompts, a: AgentProfile) -> str:
    return p.reanchor.format(name=a.name, object=a.object)


# ── Habit reminders (the habit rules decide WHEN; this phrases it) ──
def habit_system(p: HabitReminderPrompts, a: AgentProfile, habit_area: str, widget_block: str,
                 evidence: str, book_tip: str, short: bool) -> str:
    if short:
        return p.system_short.format(**_agent(a), habit_area=habit_area, widget_block=widget_block,
                                     evidence=evidence or p.no_evidence, book_tip=book_tip)
    return p.system.format(**_agent(a), habit_area=habit_area, widget_block=widget_block,
                           evidence=evidence, book_tip=book_tip)


# ── Inventing a personality for an object without its own block in agents.yaml ──
def invent_personality(p: PersonalityPrompts, obj: str) -> list[dict[str, str]]:
    return [{'role': 'system', 'content': p.system}, {'role': 'user', 'content': p.user.format(object=obj)}]


# ── Lost items ("have you seen my keys?") ──
def lost_answer_system(p: LostItemsPrompts, name: str, obj: str) -> str:
    return p.answer_system.format(name=name, object=obj)


def lost_alert(p: LostItemsPrompts, item_name: str, gone_mins: int, near_hint: str) -> list[dict[str, str]]:
    return [{'role': 'system', 'content': p.alert_system},
            {'role': 'user', 'content': p.alert_user.format(item=item_name, minutes=gone_mins, near_hint=near_hint)}]


def lost_scan(p: LostItemsPrompts, item_names: str) -> str:
    """The vision scan that looks for small things the detector misses."""
    return p.scan.format(item_names=item_names)


def lost_in_saved_photo(p: LostItemsPrompts, photo: str, item_name: str) -> str:
    return p.in_saved_photo.format(photo=photo, item=item_name)


def lost_in_live_picture(p: LostItemsPrompts, item_name: str) -> str:
    return p.in_live_picture.format(item=item_name)


def lost_anything(p: LostItemsPrompts, question: str) -> str:
    return p.anything.format(question=question)


# ── Small talk between agents ──
def idle_line_system(p: SmallTalkPrompts, a: AgentProfile, desk_summary: str) -> str:
    return p.line_system.format(**_agent(a), desk_summary=desk_summary)


# ── End-of-day recap ──
def daily_summary_user(p: DailyRecapPrompts, facts: str) -> str:
    return p.user.format(facts=facts)
