"""
The wording in config/prompts.yaml: every prompt the AI gets, Lens's wording, the habit nudges and the fixed replies.

    config.prompts.replies.needs_an_ai                                  # ready to use
    config.prompts.alert.first.format(situation=..., name=..., object=...)   # fill in its blanks

A text whose path is in BLANKS is a template: call .format() with exactly those blanks (a real brace in it is
still written twice there). Every other text is ready to use as it is. The blanks that come from the settings
({on_waking_ml}, {break_min} ...: see prompt_values) are filled in when the config is loaded, in every text.

The loader stops startup, naming the text, when a text uses a blank its code doesn't fill in, or has a { or }
without its pair (see blank_problems).
"""

from __future__ import annotations

import string
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from desktalk.config.base import Words, _Section

if TYPE_CHECKING:
    from desktalk.config.app import AppConfig

# The blanks each template's code fills in (any other text has none of its own). The settings ones can go in
# any text, see prompt_values.
_AGENT = ('name', 'object', 'personality')
BLANKS: dict[str, tuple[str, ...]] = {
    'alert.system': _AGENT,
    'alert.first': ('situation', 'name', 'object'),
    'alert.follow_up': ('situation', 'previous', 'name', 'urgency'),
    'chat.identity': (*_AGENT, 'habit_area', 'action_instructions'),
    'chat.identity_short': (*_AGENT, 'habit_area', 'action_instructions'),
    'chat.reanchor': ('name', 'object'),
    'habit_reminder.system': (*_AGENT, 'habit_area', 'widget_block', 'signals', 'evidence', 'book_tip'),
    'habit_reminder.system_short': (*_AGENT, 'habit_area', 'widget_block', 'evidence', 'book_tip'),
    'trackers.behavior': ('count',),
    'trackers.todo_list': ('items',),
    'trackers.breaks': ('count', 'last', 'sitting_min'),
    'trackers.water': ('water_ml', 'goal_ml', 'sips', 'pace_ml', 'last'),
    'trackers.food': ('count', 'parts'),
    'trackers.food_healthy': ('labels',),
    'trackers.food_treats': ('labels',),
    'trackers.phone': ('free_min', 'goal_min', 'streak_min', 'where'),
    'trackers.light': ('sun', 'level'),
    'trackers.light_lux': ('lux',),
    'trackers.light_brightness': ('brightness',),
    'habits.hydration_morning.evidence': ('minutes',),
    'habits.hydration_pace.evidence': ('minutes', 'progress'),
    'habits.hydration_pace.text': ('minutes', 'progress'),
    'habits.hydration_pace_cup.evidence': ('minutes', 'progress'),
    'habits.hydration_pace_cup.text': ('minutes', 'progress'),
    'habits.water_progress': ('drunk', 'pace'),
    'habits.breaks_sit.evidence': ('minutes',),
    'habits.breaks_sit.text': ('minutes', 'phone_note'),
    'habits.breaks_meal.evidence': ('food',),
    'habits.breaks_meal.text': ('food', 'phone_note'),
    'habits.light_morning.evidence': ('minutes',),
    'habits.light_afternoon.evidence': ('sunset', 'minutes'),
    'habits.light_afternoon.text': ('sunset',),
    'habits.light_evening.evidence': ('when', 'light'),
    'habits.light_evening.text': ('when',),
    'habits.bed_soon': ('minutes',),
    'habits.light_daytime_dim.evidence': ('light', 'minutes'),
    'habits.light_lux': ('lux',),
    'habits.light_brightness': ('brightness',),
    'habits.screens_bedtime.evidence': ('lead',),
    'habits.screens_bedtime.text': ('lead',),
    'habits.bed_at': ('bed', 'minutes'),
    'habits.phone_bedtime.evidence': ('bed', 'minutes'),
    'habits.phone_bedtime.text': ('bed',),
    'habits.phone_focus.evidence': ('minutes',),
    'habits.phone_focus.text': ('minutes',),
    'habits.food_sitting.evidence': ('food', 'minutes'),
    'habits.food_sitting.text': ('food', 'minutes'),
    'habits.tasks_open.evidence': ('count', 'plural', 'first'),
    'habits.tasks_open.text': ('count', 'plural', 'first'),
    'personality.user': ('object',),
    'small_talk.line_system': (*_AGENT, 'desk_summary'),
    'lost_items.answer_system': ('name', 'object'),
    'lost_items.alert_user': ('item', 'minutes', 'near_hint'),
    'lost_items.scan': ('item_names',),
    'lost_items.in_saved_photo': ('photo', 'item'),
    'lost_items.in_live_picture': ('item',),
    'lost_items.anything': ('question',),
    'daily_recap.user': ('facts',),
    'lens.pi_hot': ('temp',),
    'lens.pi_clock': ('seconds', 'way'),
    'lens.pi_detector': ('why',),
    'lens.pi_detector_why': ('reason',),
    'camera.problems.bumped': ('degrees',),
    'camera.problems.cutoff': ('object', 'side', 'hint'),
    'camera.problems.slow': ('fps',),
    'camera.cutoff_title': ('object', 'side'),
}


# ── the file's layout ────────────────────────────────────────────────
class Replies(_Section):
    needs_an_ai: str
    empty_reply: str
    nobody_here: str
    no_ai_label: str
    no_picture: str
    ask_for_a_sentence: str


class AlertPrompts(_Section):
    system: str
    first: str
    follow_up: str
    more_worried: str
    most_worried: str


class ChatPrompts(_Section):
    identity: str
    identity_short: str
    reanchor: str


class HabitAreas(_Section):
    hydration: str
    breaks: str
    screens: str
    light: str
    phone_free: str
    food: str
    tasks: str
    no_habits: str


class HabitReminderPrompts(_Section):
    system: str
    system_short: str
    no_evidence: str


class TrackerActions(_Section):
    todo: str
    breaks: str
    hydration: str
    food: str
    light: str


class TrackerPrompts(_Section):
    actions: TrackerActions
    behavior: str
    todo_list: str
    breaks: str
    breaks_none_yet: str
    water: str
    water_none_yet: str
    food_empty: str
    food: str
    food_healthy: str
    food_treats: str
    phone_not_counted: str
    phone: str
    phone_on_desk: str
    phone_off_desk: str
    light: str
    light_lux: str
    light_brightness: str
    light_unknown: str
    sunlight_logged: str
    sunlight_not_logged: str


class SaidWords(_Section):
    evidence: str             # why the agent speaks (seen by the camera)
    text: str                 # what it says when no AI rewrites it


class NudgeWords(SaidWords):
    tip: str                  # the book's advice, for the AI to put in the agent's own words


class HabitNudges(_Section):
    hydration_morning: NudgeWords
    hydration_pace: NudgeWords
    hydration_pace_cup: SaidWords
    water_progress: str
    breaks_sit: NudgeWords
    breaks_meal: NudgeWords
    phone_note: str
    unknown_meal: str
    light_morning: NudgeWords
    light_afternoon: NudgeWords
    light_evening: NudgeWords
    bed_soon: str
    sun_has_set: str
    light_daytime_dim: NudgeWords
    light_lux: str
    light_brightness: str
    light_bright: str
    screens_bedtime: NudgeWords
    bed_at: str
    past_bedtime: str
    phone_bedtime: NudgeWords
    phone_focus: NudgeWords
    food_sitting: NudgeWords
    unknown_snack: str
    tasks_open: NudgeWords


class PersonalityPrompts(_Section):
    system: str
    user: str


class SmallTalkPrompts(_Section):
    topic_system: str
    director_system: str
    line_system: str


class LostItemsPrompts(_Section):
    answer_system: str
    alert_system: str
    alert_user: str
    scan: str
    in_saved_photo: str
    in_live_picture: str
    anything: str
    triggers: Words
    not_spotted: str
    captures_button: str


class DailyRecapPrompts(_Section):
    system: str
    user: str


class VisionNudges(_Section):
    too_close: str
    slouch: str
    phone: str


class VisionPrompts(_Section):
    habit_scene: str
    nudges: VisionNudges


class LensCard(_Section):
    name: str
    art: str
    color: str
    about: str


class PiFixed(_Section):
    pi_hot: str
    pi_power: str
    pi_clock: str
    pi_detector: str


class LensPrompts(_Section):
    card: LensCard
    photo_label: str
    pi_hot: str
    pi_slowed: str
    pi_power: str
    pi_clock: str
    pi_clock_behind: str
    pi_clock_ahead: str
    pi_detector: str
    pi_detector_why: str
    pi_fixed: PiFixed


class CameraProblems(_Section):
    covered: str
    dark: str
    glare: str
    blurry: str
    bumped: str
    empty: str
    cutoff: str
    slow: str


class ByCamera(_Section):
    """One text for each camera (settings → camera.source)."""

    pi: str
    phone: str
    webcam: str


class CameraFixed(_Section):
    lost: str
    covered: str
    dark: str
    glare: str
    blurry: str
    bumped: str
    empty: str


class SideHints(_Section):
    left: str
    right: str
    top: str
    bottom: str


class Title(_Section):
    title: str
    fix: str


class CameraTitles(_Section):
    lost: Title
    covered: Title
    dark: Title
    glare: Title
    blurry: Title
    bumped: Title
    empty: Title
    cutoff: Title
    slow: Title


class CameraPrompts(_Section):
    problems: CameraProblems
    lost: ByCamera
    fixed: CameraFixed
    side_hints: SideHints
    other_side_hint: str
    unknown_object: str
    titles: CameraTitles
    lost_title: str
    lost_fix: ByCamera
    cutoff_title: str
    edge_tip: str


class Prompts(_Section):
    replies: Replies
    alert: AlertPrompts
    chat: ChatPrompts
    habit_areas: HabitAreas
    habit_reminder: HabitReminderPrompts
    trackers: TrackerPrompts
    habits: HabitNudges
    personality: PersonalityPrompts
    small_talk: SmallTalkPrompts
    lost_items: LostItemsPrompts
    daily_recap: DailyRecapPrompts
    vision: VisionPrompts
    lens: LensPrompts
    camera: CameraPrompts


# ── the blanks that come from the settings ───────────────────────────
def _plain_number(x: float) -> int | float:
    """600.0 → 600, so it reads "600 ml", not "600.0 ml"."""
    return int(x) if float(x).is_integer() else x


def prompt_values(config: AppConfig) -> dict[str, Any]:
    """The blanks any text may use, filled from the loaded settings: {'on_waking_ml': 600, 'break_min': 5 ...}."""
    hydration, breaks, light = config.protocols.hydration, config.protocols.breaks, config.protocols.light
    numbers = {
        'on_waking_ml': hydration.on_waking_ml,
        'break_min': breaks.break_min,
        'sit_limit_min': breaks.sit_limit_min,
        'morning_clear_min': light.morning_minutes.clear,
        'morning_partly_cloudy_min': light.morning_minutes.partly_cloudy,
        'morning_overcast_min': light.morning_minutes.overcast,
        'evening_bright_min': light.evening_bright_min,
        'sunlight_default_min': config.habit_tracker.sunlight_default_min,
    }
    return {**{k: _plain_number(v) for k, v in numbers.items()},
            'on_waking_l': f'{hydration.on_waking_ml / 1000:.1f}',
            'daily_goal_l': f'{hydration.daily_goal_ml / 1000:.1f}'}


# ── reading the texts ────────────────────────────────────────────────
def prompt_texts(prompts: BaseModel, where: str = '') -> Iterator[tuple[str, str]]:
    """Every text, with where it is: ('alert.first', 'SITUATION: {situation}...'), ('lost_items.triggers[0]', ...)."""
    for name in type(prompts).model_fields:
        here = f'{where}.{name}' if where else name
        value = getattr(prompts, name)
        if isinstance(value, BaseModel):
            yield from prompt_texts(value, here)
        elif isinstance(value, tuple):
            for i, text in enumerate(value):
                yield f'{here}[{i}]', text
        else:
            yield here, value


def _blanks_in(text: str) -> list[str]:
    """The blanks a text uses, as written: 'SITUATION: {situation}' → ['situation']. ValueError for a lone brace."""
    return [field for _, field, _, _ in string.Formatter().parse(text) if field is not None]


def _listed(names: tuple[str, ...]) -> str:
    shown = [f'{{{n}}}' for n in names]
    return shown[0] if len(shown) == 1 else ', '.join(shown[:-1]) + ' or ' + shown[-1]


def blank_problems(prompts: Prompts, settings: Mapping[str, Any]) -> list[str]:
    """One sentence for each text with a blank its code doesn't fill in, or a { or } without its pair."""
    problems = []
    for where, text in prompt_texts(prompts):
        own = BLANKS.get(where.split('[')[0], ())
        try:
            used = _blanks_in(text)
        except ValueError:
            problems.append(f'{where} has a {{ or }} without its pair (write {{{{ or }}}} for a real brace)')
            continue
        for blank in dict.fromkeys(used):
            if blank in own or blank in settings:
                continue
            can = (f'it can use {_listed(own)}, or the settings ones at the top of the file' if own
                   else 'it can only use the settings ones at the top of the file')
            problems.append(f"{where} uses the blank {{{blank}}}, which isn't filled in there ({can})")
    return problems


# ── filling in the settings ──────────────────────────────────────────
def _fill_some(text: str, values: Mapping[str, Any]) -> str:
    """Fill in only the blanks in `values`; the others, and braces written twice, stay as they are."""
    out = []
    for literal, field, spec, conversion in string.Formatter().parse(text):
        out.append(literal.replace('{', '{{').replace('}', '}}'))
        if field is None:
            continue
        if field in values:
            out.append(format(values[field], spec or '').replace('{', '{{').replace('}', '}}'))
        else:
            out.append('{' + field + (f'!{conversion}' if conversion else '') + (f':{spec}' if spec else '') + '}')
    return ''.join(out)


def fill_settings(prompts: BaseModel, values: Mapping[str, Any], where: str = '') -> Any:
    """A copy of `prompts` with the settings blanks filled in. Texts without blanks of their own come out
    ready to use; templates keep their own blanks (see BLANKS)."""
    changes: dict[str, Any] = {}
    for name in type(prompts).model_fields:
        here = f'{where}.{name}' if where else name
        value = getattr(prompts, name)
        if isinstance(value, BaseModel):
            changes[name] = fill_settings(value, values, here)
        elif isinstance(value, tuple):
            changes[name] = tuple(_fill(text, values, here) for text in value)
        else:
            changes[name] = _fill(value, values, here)
    return prompts.model_copy(update=changes)


def _fill(text: str, values: Mapping[str, Any], where: str) -> str:
    return _fill_some(text, values) if where in BLANKS else text.format(**values)
