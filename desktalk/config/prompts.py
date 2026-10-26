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
    'alert.grounding': ('self_label', 'other_label', 'name', 'other_name', 'drink_note', 'risk'),
    'alert.other_is_drink': ('other_label',),
    'alert.self_is_drink': ('self_label',),
    'alert.same_area': ('self_label', 'other_label'),
    'alert.heads_up': ('rule',),
    'desk_rules.too_few_corners': ('corners',),
    'replies.ai_errors.rate_limit': ('who',),
    'replies.ai_errors.bad_key': ('who',),
    'replies.ai_errors.no_model': ('who',),
    'replies.ai_errors.too_slow': ('who',),
    'replies.ai_errors.other': ('error',),
    'replies.ai_errors.no_pictures': ('model',),
    'replies.ai_errors.resting': ('who', 'minutes'),
    'replies.ai_errors.left_alone': ('problem', 'failures', 'seconds'),
    'chat.identity': (*_AGENT, 'habit_area', 'action_instructions'),
    'chat.identity_short': (*_AGENT, 'habit_area', 'action_instructions'),
    'chat.reanchor': ('name', 'object'),
    'chat.remember': ('lines',),
    'chat.remember_line': ('line',),
    'chat.earlier': ('recap',),
    'chat.other_said': ('name', 'text'),
    'chat.referenced': ('objects',),
    'chat.referenced_one': ('name', 'status'),
    'live_context.member': ('name', 'object', 'label', 'here_for'),
    'live_context.also_seen': ('labels',),
    'live_context.dangers': ('risk', 'state'),
    'live_context.zone_danger': ('label', 'depth', 'duration'),
    'live_context.edge_danger': ('label', 'duration'),
    'live_context.other_danger': ('label', 'duration'),
    'live_context.no_dangers': ('risk',),
    'live_context.insight': ('text',),
    'live_context.vision_pass': ('labels',),
    'live_context.recent': ('count',),
    'live_context.recent_event': ('age', 'who', 'near', 'label'),
    'live_context.recent_near': ('name',),
    'live_context.habits': ('water_l', 'goal_l', 'sips', 'breaks', 'sitting_min', 'phone_free', 'sunlight'),
    'live_context.habits_phone_free': ('minutes',),
    'live_context.session': ('duration',),
    'live_context.on_desk': ('here_for', 'label'),
    'live_context.on_desk_danger': ('dangers',),
    'live_context.off_desk': ('ago',),
    'memory.you_said': ('said',),
    'memory.you_replied': ('user', 'said'),
    'habit_reminder.system': (*_AGENT, 'habit_area', 'widget_block', 'evidence', 'book_tip'),
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
    'habits.tasks_open.evidence': ('task',),
    'habits.tasks_open.text': ('task',),
    'habits.tasks_due.tomorrow.evidence': ('task',),
    'habits.tasks_due.tomorrow.text': ('task',),
    'habits.tasks_due.today.evidence': ('task',),
    'habits.tasks_due.today.text': ('task',),
    'habits.tasks_due.late.evidence': ('task', 'days', 'plural'),
    'habits.tasks_due.late.text': ('task', 'days', 'plural'),
    'habits.logged.task_done': ('task',),
    'habits.logged.sip_seen': ('sips', 'liters'),
    'habits.logged.sip_button': ('liters',),
    'habits.logged.break_seen': ('minutes',),
    'habits.logged.food_seen_healthy': ('a', 'food'),
    'habits.logged.food_seen_treat': ('a', 'food'),
    'habits.logged.food_button': ('food',),
    'habits.logged.phone_streak': ('minutes',),
    'habits.logged.sunlight': ('minutes', 'when'),
    'personality.user': ('object',),
    'small_talk.line_system': (*_AGENT, 'desk_summary'),
    'small_talk.topic_user': ('summary', 'names', 'memories'),
    'small_talk.line_open': ('topic',),
    'small_talk.line_reply': ('topic', 'transcript'),
    'small_talk.director_user': ('topic', 'names', 'transcript'),
    'small_talk.said': ('name', 'text'),
    'lost_items.answer_system': ('name', 'object'),
    'lost_items.alert_user': ('item', 'minutes', 'near_hint'),
    'lost_items.scan': ('item_names',),
    'lost_items.in_saved_photo': ('photo', 'item'),
    'lost_items.in_live_picture': ('item',),
    'lost_items.anything': ('question',),
    'lost_items.answer_user': ('message', 'facts'),
    'lost_items.near': ('names',),
    'lost_items.never_seen_fact': ('name', 'extra'),
    'lost_items.vision_extra': ('note',),
    'lost_items.scan_extra': ('labels',),
    'lost_items.just_seen_fact': ('name', 'seconds', 'when', 'near'),
    'lost_items.seen_fact': ('name', 'minutes', 'when', 'near'),
    'lost_items.never_seen': ('name', 'more'),
    'lost_items.live_look': ('note',),
    'lost_items.just_seen': ('name', 'when', 'near'),
    'lost_items.seen': ('name', 'when', 'minutes', 'near'),
    'lost_items.saved_photo': ('when', 'label'),
    'lost_items.saved_photo_label': ('label',),
    'lost_items.spotted_photo': ('name',),
    'lost_items.spotted_by_vision_photo': ('name',),
    'lost_items.last_seen_near': ('names',),
    'lost_items.alert_plain': ('name', 'minutes', 'hint'),
    'daily_recap.user': ('facts',),
    'daily_recap.plain': ('facts',),
    'daily_recap.facts.busiest': ('start', 'end'),
    'daily_recap.facts.water': ('water_l', 'goal_l', 'sips'),
    'daily_recap.facts.breaks': ('count', 'longest_min'),
    'daily_recap.facts.phone_free': ('minutes', 'best_min'),
    'daily_recap.facts.desk_time': ('minutes',),
    'daily_recap.facts.dangers': ('count',),
    'daily_recap.facts.lost': ('count',),
    'daily_recap.facts.objects': ('objects',),
    'desk_alerts.pet': ('pet', 'knock'),
    'desk_alerts.pet_knock': ('names',),
    'desk_alerts.clutter': ('count',),
    'desk_alerts.edge_reason': ('edge', 'distance'),
    'desk_alerts.edge_danger': ('object', 'edge'),
    'desk_checks.edge': ('object', 'side'),
    'desk_checks.zone': ('intruder', 'target', 'zone', 'depth'),
    'desk_checks.repeated': ('intruder', 'target', 'count', 'window'),
    'desk_checks.in_between': ('middle', 'a', 'b'),
    'desk_checks.crowd': ('count', 'labels'),
    'desk_checks.approaching': ('a', 'b', 'percent', 'pictures'),
    'lens.pi_hot': ('temp',),
    'lens.pi_clock': ('seconds', 'way'),
    'lens.pi_detector': ('why',),
    'lens.pi_detector_why': ('reason',),
    'camera.problems.bumped': ('degrees',),
    'camera.problems.cutoff': ('object', 'side', 'hint'),
    'camera.problems.slow': ('fps',),
    'camera.cutoff_title': ('object', 'side'),
    'dev.outside_data_folder': ('path',),
    'dev.is_a_folder': ('path',),
    'dev.no_such_file': ('path',),
    'dev.database_file': ('path',),
    'dev.unknown_test': ('test_id',),
    'dev.unknown_run': ('run_id',),
    'dev.cannot_list_tests': ('reason',),
    'dev.unknown_database': ('name',),
    'dev.query_refused': ('reason',),
    'dev.query_failed': ('reason',),
    'dev.query_timed_out': ('seconds',),
}


# ── the file's layout ────────────────────────────────────────────────
class AiErrors(_Section):
    """What went wrong with the AI, in plain words."""

    the_ai: str
    rate_limit: str
    bad_key: str
    no_model: str
    too_slow: str
    ollama_too_slow: str
    no_connection: str
    ollama_no_connection: str
    other: str
    no_pictures: str
    resting: str
    left_alone: str
    keeps_failing: str
    pictures_failing: str


class Replies(_Section):
    needs_an_ai: str
    empty_reply: str
    nobody_here: str
    no_ai_label: str
    no_picture: str
    ask_for_a_sentence: str
    ai_errors: AiErrors


class AlertPrompts(_Section):
    system: str
    first: str
    follow_up: str
    more_worried: str
    most_worried: str
    grounding: str
    other_is_drink: str
    self_is_drink: str
    no_drink: str
    same_area: str
    unknown_label: str
    unknown_other: str
    heads_up: str
    too_close: str


class ChatPrompts(_Section):
    identity: str
    identity_short: str
    reanchor: str
    live_desk: str
    remember: str
    remember_line: str
    earlier: str
    other_said: str
    referenced: str
    referenced_one: str


class LiveContextWords(_Section):
    """The desk right now, as the chat's AI is told it."""

    camera_off: str
    members: str
    member: str
    nobody: str
    also_seen: str
    dangers: str
    zone_danger: str
    edge_danger: str
    other_danger: str
    just_now: str
    no_dangers: str
    insights: str
    insight: str
    vision_pass: str
    recent: str
    recent_event: str
    recent_near: str
    unknown: str
    habits: str
    habits_phone_free: str
    habits_sunlight: str
    session: str
    pushback: str
    pushback_words: Words
    on_desk: str
    on_desk_danger: str
    off_desk: str
    not_seen: str


class MemoryWords(_Section):
    """What an agent remembers saying."""

    you_said: str
    you_replied: str


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
    ask: str


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


class HabitLogged(_Section):
    """What the habit tracker says when it logs something."""

    sip_seen: str
    sip_button: str
    break_seen: str
    break_button: str
    food_seen_healthy: str
    food_seen_treat: str
    food_button: str
    phone_streak: str
    sunlight: str
    sunlight_morning: str
    sunlight_afternoon: str
    task_done: str


class TasksDue(_Section):
    """A to-do with a due date: the day before, on the day, and while it's late."""

    tomorrow: NudgeWords
    today: NudgeWords
    late: NudgeWords


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
    tasks_due: TasksDue
    logged: HabitLogged


class PersonalityPrompts(_Section):
    system: str
    user: str


class SmallTalkPrompts(_Section):
    topic_system: str
    director_system: str
    line_system: str
    topic_user: str
    line_open: str
    line_reply: str
    director_user: str
    said: str
    nothing_yet: str
    no_memory: str


class AnswerAs(_Section):
    name: str
    object: str


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
    other_things: Words
    answer_as: AnswerAs
    answer_user: str
    near: str
    never_seen_fact: str
    vision_extra: str
    scan_extra: str
    just_seen_fact: str
    seen_fact: str
    never_seen: str
    live_look: str
    see_captures: str
    just_seen: str
    seen: str
    saved_photo: str
    saved_photo_label: str
    spotted_photo: str
    spotted_by_vision_photo: str
    desk_scan_photo: str
    last_seen_near: str
    alert_plain: str


class RecapFacts(_Section):
    busiest: str
    water: str
    breaks: str
    phone_free: str
    sunlight_logged: str
    sunlight_not_logged: str
    desk_time: str
    dangers: str
    lost: str
    objects: str


class DailyRecapPrompts(_Section):
    system: str
    user: str
    plain: str
    facts: RecapFacts


class DeskAlertWords(_Section):
    """The desk monitor's alerts."""

    photo_label: str
    pet: str
    pet_knock: str
    clutter: str
    plant: str
    edge_reason: str
    edge_danger: str


class DeskCheckWords(_Section):
    """What the desk checks found, in words."""

    edge: str
    zone: str
    repeated: str
    in_between: str
    crowd: str
    approaching: str


class DeskRuleWords(_Section):
    """What the desk rules say on the Settings page."""

    too_few_corners: str


class PhotoWords(_Section):
    """The proof photos' wording (the Captures page)."""

    no_label: str


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


class UiWords(_Section):
    """The app page's ready-made lists (sent with GET /api/ui_settings)."""

    starter_todo: str
    starter_counters: Words


class NotificationWords(_Section):
    """The Mac's own pop-up notifications."""

    sip_title: str


class DevWords(_Section):
    """The developer page's server messages (desktalk/web/routers/dev.py and the parts behind it)."""

    run_already_going: str
    other_computer: str
    other_web_page: str
    outside_data_folder: str
    is_a_folder: str
    no_such_file: str
    database_file: str
    unknown_test: str
    nothing_to_run: str
    unknown_run: str
    cannot_list_tests: str
    unknown_database: str
    query_not_one_statement: str
    query_refused: str
    query_failed: str
    query_timed_out: str
    not_built: str


class Prompts(_Section):
    replies: Replies
    alert: AlertPrompts
    chat: ChatPrompts
    live_context: LiveContextWords
    memory: MemoryWords
    habit_areas: HabitAreas
    habit_reminder: HabitReminderPrompts
    trackers: TrackerPrompts
    habits: HabitNudges
    personality: PersonalityPrompts
    small_talk: SmallTalkPrompts
    lost_items: LostItemsPrompts
    daily_recap: DailyRecapPrompts
    lens: LensPrompts
    camera: CameraPrompts
    desk_alerts: DeskAlertWords
    desk_rules: DeskRuleWords
    photos: PhotoWords
    desk_checks: DeskCheckWords
    ui: UiWords
    notifications: NotificationWords
    dev: DevWords


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
