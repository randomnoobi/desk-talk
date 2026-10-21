"""
Typed settings: one read-only object built from the files in config/ and from .env.

    config = load_config()
    config.camera.fps                       # 3     (config/settings.yaml)
    config.protocols.hydration.daily_goal_ml
    config.agents_file.agents['cup'].name   # config/agents.yaml
    config.secrets.gemini_api_key.get_secret_value()   # from .env

Each part gets only its own section, e.g. Roster(..., config.agents), so it can't
reach (or depend on) the rest. What each setting means is in the comments of config/*.yaml.
Paths stay as written there; config.resolve(path) gives the full path under the project folder.

Order (later wins):
    config/settings.yaml → config/settings.local.yaml → a few .env values (see ENV_OVERRIDES)

A missing, misspelled or bad setting stops startup with a ConfigError that names the
file, the setting and what's wrong, e.g.
    config/settings.local.yaml: camera.fps must be a number above 0 (got "fast")
"""

from __future__ import annotations

import difflib
import json
import os
import types
import typing
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal

import annotated_types
import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator
from pydantic.fields import FieldInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ConfigError(Exception):
    """A setting is missing, misspelled or has a bad value. The message says which file and setting."""


class _Section(BaseModel):
    """Every part of the config: read-only, and a key it doesn't know is an error (a typo must not be ignored)."""

    model_config = ConfigDict(frozen=True, extra='forbid')
    # (smaller, larger) pairs of settings in this section, e.g. a wait that starts at one and grows to the other
    _in_order: ClassVar[tuple[tuple[str, str], ...]] = ()

    @model_validator(mode='after')
    def _check_order(self):
        for small, large in self._in_order:
            a, b = getattr(self, small), getattr(self, large)
            if a > b:
                raise ValueError(f'{small} ({_number(a)}) must not be more than {large} ({_number(b)})')
        return self


class _Says:
    """Plain words for a value type that its limits alone can't describe (used in error messages)."""

    def __init__(self, words: str):
        self.words = words


# Value types. Their limits also make the error messages ("must be a number above 0").
PositiveNumber = Annotated[float, Field(gt=0)]
ZeroOrMore = Annotated[float, Field(ge=0)]
PositiveInt = Annotated[int, Field(gt=0)]
Count = Annotated[int, Field(ge=0)]
Fraction = Annotated[float, Field(ge=0, le=1)]
Hour = Annotated[int, Field(ge=0, le=23)]
Level = Annotated[float, Field(ge=0, le=255)]                 # picture brightness, 0-255
Port = Annotated[int, Field(ge=1, le=65535)]
JpegQuality = Annotated[int, Field(ge=1, le=100)]
ClockTime = Annotated[str, Field(pattern=r'^\d{1,2}:\d{2}$'), _Says('a time like 07:30')]
Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]
Words = tuple[str, ...]


# ════════════════════════════════════════════════════════════════════
#  config/settings.yaml
# ════════════════════════════════════════════════════════════════════
class CameraSettings(_Section):
    source: Literal['pi', 'phone', 'webcam']
    fps: PositiveNumber
    disconnect_after_sec: PositiveNumber
    webcam_indices: tuple[Count, ...]
    phone_port: Port
    wait_for_picture_sec: PositiveNumber
    jpeg_quality: JpegQuality
    boxes_jpeg_quality: JpegQuality
    light_measure_width: PositiveInt
    moved_within_sec: ZeroOrMore
    fps_gap_max_sec: PositiveNumber
    fps_smoothing: Fraction
    phone_tilt_max_age_sec: ZeroOrMore
    webcam_width: PositiveInt
    webcam_height: PositiveInt
    webcam_warmup_reads: PositiveInt
    webcam_max_index: PositiveInt
    pair_key_bytes: PositiveInt


class PiDetector(_Section):
    """Object detection on the Pi, in Docker. The Pi gets these from /api/pi/config and passes them on."""

    _in_order = (('pause_start_sec', 'pause_max_sec'),)
    model: str
    format: Literal['ncnn', 'pt']
    image_size: PositiveInt
    confidence: Fraction
    half: bool
    url: str
    wait_per_picture_sec: PositiveNumber
    failures_before_pause: PositiveInt
    pause_start_sec: PositiveNumber
    pause_max_sec: PositiveNumber
    health_check_every_sec: PositiveNumber


class PiSettings(_Section):
    _in_order = (('upload_retry_start_sec', 'upload_retry_max_sec'),)
    fps: PositiveNumber
    width: PositiveInt
    height: PositiveInt
    jpeg_quality: JpegQuality
    photo_dir: str                    # a folder on the Pi, so it is never resolved on this computer
    delete_every_min: ZeroOrMore      # 0 = never
    keep_last_min: ZeroOrMore
    min_free_mb: ZeroOrMore
    upload_timeout_sec: PositiveNumber
    upload_retry_start_sec: PositiveNumber
    upload_retry_max_sec: PositiveNumber
    hot_warning_c: PositiveNumber
    clock_off_warning_sec: PositiveNumber
    camera_warmup_sec: ZeroOrMore
    pause_after_error_sec: ZeroOrMore
    status_every_sec: PositiveNumber
    health_every_sec: PositiveNumber
    settings_check_every_sec: PositiveNumber
    restart_after_missed_pictures: PositiveInt
    detector: PiDetector


class DetectorSettings(_Section):
    model: str
    confidence: Fraction
    image_size: PositiveInt
    device: str
    end2end: bool
    rect: bool


class AgentsSettings(_Section):
    join_after_sec: ZeroOrMore
    leave_after_sec: ZeroOrMore
    min_confidence: Fraction
    only_on_desk: bool
    max_agents: PositiveInt
    announce: bool
    greet_on_join: bool
    greet_again_after_min: ZeroOrMore
    invent_personality: bool
    personality_max_chars: PositiveInt


class PresenceSettings(_Section):
    appear_after_sec: ZeroOrMore
    disappear_after_sec: ZeroOrMore
    user_active_window_sec: PositiveNumber


class SupervisorSettings(_Section):
    _in_order = (('restart_start_sec', 'restart_max_sec'),)
    restart_start_sec: PositiveNumber
    restart_max_sec: PositiveNumber


class EventStreamSettings(_Section):
    queue_size: PositiveInt


class EventLogSettings(_Section):
    folder: Path
    compact_every_min: PositiveNumber
    merge_gap_sec: ZeroOrMore
    keep_days: PositiveInt
    log_empty_frames: bool
    prune_every_min: PositiveNumber


class AlertsSettings(_Section):
    cooldown_sec: ZeroOrMore
    reset_after_sec: ZeroOrMore
    pet_cooldown_sec: ZeroOrMore
    clutter_threshold: PositiveInt
    clutter_cooldown_sec: ZeroOrMore
    plant_water_every_sec: PositiveNumber
    cooldown_max_times: PositiveNumber
    dangerous_from_risk: Annotated[int, Field(ge=0, le=4)]
    recent_kept: PositiveInt
    pet_labels: Words
    plant_labels: Words


class EdgeProximity(_Section):
    _in_order = (('danger_px', 'warn_px'),)
    danger_px: ZeroOrMore
    warn_px: ZeroOrMore
    objects: Words


class DangerZone(_Section):
    expand: ZeroOrMore
    label: str


class ChainReaction(_Section):
    path_ratio: Fraction
    crowd_radius_px: ZeroOrMore
    crowd_min_objects: PositiveInt
    harmless_crowd: Words


class Temporal(_Section):
    history_length: PositiveInt
    approach_min_frames: PositiveInt
    approach_rate: Fraction
    repeated_intrusion_threshold: PositiveInt
    repeated_window_sec: PositiveNumber


class RiskWeights(_Section):
    edge_danger: Count
    edge_warning: Count
    zone_intrusion: Count
    chain_reaction: Count
    crowd: Count
    approaching: Count
    repeated_intrusion: Count


class SafetySettings(_Section):
    """Built-in danger rules. config/safety_rules.json (the Settings page) wins where it has the same setting."""

    edge_proximity: EdgeProximity
    danger_zones: dict[str, DangerZone]
    zone_dangers: dict[str, tuple[Literal['liquids', 'foods', 'sharp'], ...]]
    liquids: Words
    foods: Words
    sharp: Words
    harmless_pairs: tuple[tuple[str, str], ...]
    chain_reaction: ChainReaction
    temporal: Temporal
    risk_weights: RiskWeights
    risk_level_steps: tuple[Count, Count, Count]
    desk_border_share: Fraction
    footprint_share: Fraction
    footprint_min_px: ZeroOrMore
    max_desk_corners: Annotated[int, Field(ge=3)]


class QuietHours(_Section):
    enabled: bool
    start_hour: Hour
    end_hour: Hour


class RemindersSettings(_Section):
    warmup_min: ZeroOrMore
    min_gap_sec: ZeroOrMore
    check_every_sec: PositiveNumber
    quiet_hours: QuietHours
    someone_here_sec: PositiveNumber


class AiReply(_Section):
    max_tokens: PositiveInt
    temperature: Fraction | None = None      # empty = the agent's own temperature (agents.yaml)


class AiReplies(_Section):
    chat: AiReply
    alert: AiReply
    nudge: AiReply
    personality: AiReply
    idle_topic: AiReply
    idle_line: AiReply
    idle_director: AiReply
    lost_alert: AiReply
    lost_answer: AiReply
    daily_recap: AiReply
    vision: AiReply


class LlmSettings(_Section):
    _in_order = (('retry_start_sec', 'retry_max_sec'),)
    gemini_model: str
    openai_model: str
    groq_model: str
    ollama_model: str
    vision_model: str
    min_interval_sec: ZeroOrMore
    max_retries: PositiveInt
    pause_on_limit_sec: ZeroOrMore
    chat_history_turns: Count
    memory_per_agent: Count
    timeout_sec: PositiveNumber
    ollama_timeout_sec: PositiveNumber
    retry_start_sec: PositiveNumber
    retry_max_sec: PositiveNumber
    breaker_failures: PositiveInt
    breaker_open_sec: PositiveNumber
    rate_limit_retry_sec: ZeroOrMore
    ollama_retry_sec: ZeroOrMore
    ollama_warmer_per_retry: Fraction
    vision_retry_sec: ZeroOrMore
    ollama_keepalive_sec: PositiveNumber
    ollama_keepalive_start_sec: ZeroOrMore
    gemini_url: str
    groq_url: str
    gemini_thinking_budget: Count
    replies: AiReplies


class ChatSettings(_Section):
    recent_kept: PositiveInt
    history_from_app: Count
    message_max_chars: PositiveInt
    name_max_chars: PositiveInt
    ollama_history_turns: Count
    summary_turns: Count
    summary_chars: PositiveInt
    memory_reply_chars: PositiveInt
    memory_user_chars: PositiveInt
    reply_max_chars: PositiveInt
    memory_match_chars: PositiveInt
    ollama_memory_lines: Count
    recent_events_kept: Count
    recent_events_shown: Count
    other_objects_shown: Count
    insights_shown: Count
    min_reply_words: Count
    personality_min_words: Count
    todo_max_items: PositiveInt
    todo_max_chars: PositiveInt
    panel_views_kept: PositiveInt
    panel_views_window_sec: PositiveNumber
    panel_views_min: PositiveInt


class IdleChatSettings(_Section):
    _in_order = (('min_sec', 'max_sec'),)
    min_sec: PositiveInt
    max_sec: PositiveInt
    start_delay_sec: ZeroOrMore
    max_turns: Count
    quiet_after_typing_sec: ZeroOrMore
    pause_between_lines_sec: ZeroOrMore
    pause_random_sec: ZeroOrMore
    memory_per_agent: Count
    memory_lines: Count
    topic_min_words: Count
    line_min_words: Count


class LostItem(_Section):
    name: str
    labels: Words
    alert_after_min: PositiveNumber
    words: Words


class LostItemsSettings(_Section):
    check_every_sec: PositiveNumber
    scene_change: Level
    rescan_every_sec: PositiveNumber
    snapshot_every_sec: ZeroOrMore
    spotted_photo_cooldown_sec: ZeroOrMore
    start_delay_sec: ZeroOrMore
    compare_size: PositiveInt
    min_confidence: Fraction
    just_seen_sec: ZeroOrMore
    detector_fresh_sec: ZeroOrMore
    nearby_kept: Count
    nearby_mentioned: Count
    answer_min_words: Count
    items: dict[str, LostItem]


class VisionNudgeRepeats(_Section):
    too_close: ZeroOrMore
    slouch: ZeroOrMore
    phone: ZeroOrMore


class VisionHabitsSettings(_Section):
    every_sec: PositiveNumber
    slouch_log_min_sec: ZeroOrMore
    nail_log_cooldown_sec: ZeroOrMore
    max_log_events: PositiveInt
    start_delay_sec: ZeroOrMore
    min_every_sec: PositiveNumber
    repeat_after_sec: VisionNudgeRepeats


class StorageSettings(_Section):
    folder: Path


class LensWaits(_Section):
    """How long each camera problem must last before Lens says something."""

    lost: ZeroOrMore
    covered: ZeroOrMore
    dark: ZeroOrMore
    glare: ZeroOrMore
    blurry: ZeroOrMore
    bumped: ZeroOrMore
    empty: ZeroOrMore
    cutoff: ZeroOrMore
    slow: ZeroOrMore
    pi: ZeroOrMore


class LensSettings(_Section):
    sample_every_sec: PositiveNumber
    covered_below: Level
    dark_below: Level
    glare_above: Fraction
    blur_below: ZeroOrMore
    motion_above: ZeroOrMore
    bump_degrees: ZeroOrMore
    slow_fps_below: ZeroOrMore
    speak_gap_sec: ZeroOrMore
    repeat_after_min: ZeroOrMore
    check_every_sec: PositiveNumber
    measure_width: PositiveInt
    white_level: Level
    edge_touch_px: ZeroOrMore
    fills_picture: Fraction
    fixed_after_sec: ZeroOrMore
    edge_tip_after_sec: ZeroOrMore
    reading_fresh_sec: PositiveNumber
    cutoff_forget_sec: ZeroOrMore
    cutoff_repeat_times: ZeroOrMore
    wait_before_saying_sec: LensWaits


class DailySummarySettings(_Section):
    hour: Hour
    start_delay_sec: ZeroOrMore
    check_every_sec: PositiveNumber
    objects_listed: Count


class SnapshotsSettings(_Section):
    max_kept: PositiveInt
    crop_padding: ZeroOrMore
    crop_min_px: ZeroOrMore
    jpeg_quality: JpegQuality


class HabitTrackerSettings(_Section):
    save_every_sec: ZeroOrMore
    save_check_every_sec: PositiveNumber
    gap_counted_max_sec: ZeroOrMore
    breaks_kept: PositiveInt
    breaks_shown: Count
    drinks_kept: PositiveInt
    glass_ml: PositiveNumber
    button_break_min: PositiveNumber
    sunlight_default_min: PositiveNumber
    afternoon_light_hours: ZeroOrMore
    food_label_max_chars: PositiveInt


class ListSize(_Section):
    _in_order = (('default', 'max'),)
    default: PositiveInt
    max: PositiveInt


class ListSizes(_Section):
    habit_log: ListSize
    log_events: ListSize
    log_detections: ListSize


class WebSettings(_Section):
    port: Port
    max_request_mb: PositiveNumber
    pi_upload_max_mb: PositiveNumber
    live_retry_ms: PositiveInt
    live_keepalive_sec: PositiveNumber
    live_queue_size: PositiveInt
    video_resend_sec: PositiveNumber
    video_check_every_sec: PositiveNumber
    photo_cache_sec: Count
    list_sizes: ListSizes


class LoggingSettings(_Section):
    folder: Path
    level: Literal['DEBUG', 'INFO', 'WARNING', 'ERROR']
    file_size_mb: PositiveNumber
    files_kept: Count


# ════════════════════════════════════════════════════════════════════
#  config/protocols.yaml
# ════════════════════════════════════════════════════════════════════
class Schedule(_Section):
    wake_time: ClockTime
    bed_time: ClockTime
    latitude: Latitude | None
    longitude: Longitude | None
    sunset_fallback: ClockTime


class HydrationRule(_Section):
    enabled: bool
    on_waking_ml: ZeroOrMore
    daily_goal_ml: ZeroOrMore
    goal_window_hours: PositiveNumber
    ml_per_sip: PositiveNumber
    sip_gone_min_sec: ZeroOrMore
    sip_gone_max_sec: ZeroOrMore
    remind_after_min: ZeroOrMore
    behind_by_ml: ZeroOrMore
    stop_before_bed_hours: ZeroOrMore
    on_waking_window_min: ZeroOrMore
    on_waking_done_share: Fraction


class BreaksRule(_Section):
    enabled: bool
    sit_limit_min: PositiveNumber
    break_min: PositiveNumber
    repeat_after_min: ZeroOrMore
    walk_after_meal: bool
    meal_min_on_desk_min: ZeroOrMore
    no_phone_on_breaks: bool
    walk_window_after_meal_min: ZeroOrMore
    walk_repeat_after_hours: ZeroOrMore


class MorningMinutes(_Section):
    clear: ZeroOrMore
    partly_cloudy: ZeroOrMore
    overcast: ZeroOrMore


class LightRule(_Section):
    enabled: bool
    morning_window_min: ZeroOrMore
    morning_minutes: MorningMinutes
    afternoon: bool
    afternoon_start_min_before_sunset: ZeroOrMore
    afternoon_end_min_before_sunset: ZeroOrMore
    evening_dim_before_bed_min: ZeroOrMore
    screens_off_before_bed_min: ZeroOrMore
    night_bright_lux: ZeroOrMore
    day_dim_lux: ZeroOrMore
    night_bright_level: Level
    day_dim_level: Level
    day_dim_min: ZeroOrMore
    repeat_after_min: ZeroOrMore
    morning_grace_min: ZeroOrMore
    evening_bright_min: ZeroOrMore
    bed_soon_min: ZeroOrMore
    day_dim_margin_min: ZeroOrMore
    sunset_guess_before_bed_hours: ZeroOrMore
    screens_until_after_bed_hours: ZeroOrMore


class PhoneFreeRule(_Section):
    enabled: bool
    nudge_after_min: ZeroOrMore
    repeat_after_min: ZeroOrMore
    goal_min_per_day: ZeroOrMore
    celebrate_every_min: ZeroOrMore      # 0 = never cheer
    before_bed_min: ZeroOrMore
    before_bed_on_desk_min: ZeroOrMore
    before_bed_until_after_bed_hours: ZeroOrMore


class FoodRule(_Section):
    enabled: bool
    remind_after_min: ZeroOrMore
    repeat_after_hours: ZeroOrMore


class TasksRule(_Section):
    enabled: bool
    remind_after_min: ZeroOrMore


class Protocols(_Section):
    schedule: Schedule
    hydration: HydrationRule
    breaks: BreaksRule
    light: LightRule
    phone_free: PhoneFreeRule
    food: FoodRule
    tasks: TasksRule


# ════════════════════════════════════════════════════════════════════
#  config/agents.yaml
# ════════════════════════════════════════════════════════════════════
Color = Literal['cobalt', 'teal', 'tang', 'mustard', 'coral', 'plum', 'moss', 'slate']
Habit = Literal['hydration', 'breaks', 'light', 'phone_free', 'screens', 'food', 'tasks']
Tracker = Literal['hydration', 'breaks', 'food', 'todo', 'phone_free', 'light']


class AgentDefaults(_Section):
    """The `default` block: used for any object without its own block."""

    personality: str
    temperature: Fraction
    icon: str
    too_close: str
    hello: str


class Agent(_Section):
    """One character. Anything left out comes from `default` (or is simply not used)."""

    name: str | None = None
    object: str | None = None
    nickname: str | None = None
    labels: Words
    icon: str | None = None
    art: str | None = None
    color: Color | None = None
    about: str | None = None
    personality: str | None = None
    temperature: Fraction | None = None
    habits: tuple[Habit, ...] = ()
    tracker: Tracker | None = None
    fragile: bool = False
    min_confidence: Fraction | None = None
    too_close: str | None = None
    hello: str | None = None


class Relationship(_Section):
    between: tuple[str, str]
    risk: str


class AgentsFile(_Section):
    default: AgentDefaults
    ignore: Words
    agents: dict[str, Agent]
    relationships: tuple[Relationship, ...]


# ════════════════════════════════════════════════════════════════════
#  .env (never YAML)
# ════════════════════════════════════════════════════════════════════
class Secrets(_Section):
    """API keys and the Ollama address. Empty when not set. Keys are hidden when printed."""

    gemini_api_key: SecretStr
    openai_api_key: SecretStr
    groq_api_key: SecretStr
    ollama_url: str


SECRET_KEYS = ('GEMINI_API_KEY', 'OPENAI_API_KEY', 'GROQ_API_KEY', 'OLLAMA_URL')
# Settings that may also be set in .env (kept so an existing .env keeps working).
ENV_OVERRIDES = {
    'OLLAMA_MODEL': ('llm', 'ollama_model'),
    'LOST_VISION_MODEL': ('llm', 'vision_model'),
    'GEMINI_MODEL': ('llm', 'gemini_model'),
    'LLM_MODEL': ('llm', 'openai_model'),
    'GROQ_MODEL': ('llm', 'groq_model'),
    'YOLO_MODEL': ('detector', 'model'),
}
# Old .env settings that moved to config/settings.yaml (startup shows a hint if they're set).
MOVED_ENV = ('CAMERA_SOURCE', 'CAMERA_INDICES', 'PIPELINE_CAMERA', 'EVENT_COOLDOWN', 'APPEAR_FRAMES',
             'DISAPPEAR_FRAMES', 'REMINDER_WARMUP_MIN', 'HABIT_MIN_GAP_SEC', 'IDLE_CHAT_MIN_SEC')


# ════════════════════════════════════════════════════════════════════
#  Everything together
# ════════════════════════════════════════════════════════════════════
class AppConfig(_Section):
    camera: CameraSettings
    pi: PiSettings
    detector: DetectorSettings
    agents: AgentsSettings
    presence: PresenceSettings
    supervisor: SupervisorSettings
    event_stream: EventStreamSettings
    event_log: EventLogSettings
    alerts: AlertsSettings
    safety: SafetySettings
    reminders: RemindersSettings
    llm: LlmSettings
    chat: ChatSettings
    idle_chat: IdleChatSettings
    lost_items: LostItemsSettings
    vision_habits: VisionHabitsSettings
    lens: LensSettings
    daily_summary: DailySummarySettings
    snapshots: SnapshotsSettings
    habit_tracker: HabitTrackerSettings
    web: WebSettings
    logging: LoggingSettings
    storage: StorageSettings
    protocols: Protocols
    agents_file: AgentsFile
    secrets: Secrets
    env_applied: dict[str, str]       # .env name → the setting it changed, e.g. {'OLLAMA_MODEL': 'llm.ollama_model'}
    env_moved: tuple[str, ...]        # old .env names that are set but no longer used
    root: Path                        # the project folder; relative paths in the settings are under it

    def resolve(self, path: str | Path) -> Path:
        """A path from the settings: absolute paths and ~ stay as they are, others are under the project folder."""
        path = Path(path).expanduser()
        return path if path.is_absolute() else self.root / path


# ════════════════════════════════════════════════════════════════════
#  Loading
# ════════════════════════════════════════════════════════════════════
Loc = tuple[str | int, ...]          # where a setting is, e.g. ('camera', 'fps')


class _Sources:
    """Which file set each setting, so an error message can name the right file."""

    def __init__(self):
        self.files: dict[Loc, list[str]] = {}

    def record(self, data: dict, file: str, prefix: Loc = ()) -> None:
        for key, value in data.items():
            loc = prefix + (key,)
            self.files.setdefault(loc, []).append(file)
            if isinstance(value, dict):
                self.record(value, file, loc)

    def _nearest(self, loc: Loc) -> list[str]:
        while loc and loc not in self.files:
            loc = loc[:-1]
        return self.files[loc] if loc else ['config/settings.yaml']

    def first(self, loc: Loc) -> str:
        """The file `loc` (or the nearest section above it) comes from: where a missing setting belongs."""
        return self._nearest(loc)[0]

    def last(self, loc: Loc) -> str:
        """The file that last set `loc` (or the nearest section above it)."""
        return self._nearest(loc)[-1]


def _read_yaml(path: Path, shown: str) -> dict:
    if not path.is_file():
        raise ConfigError(f'{shown} is missing')
    try:
        with open(path, encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        mark = getattr(exc, 'problem_mark', None)
        where = f' near line {mark.line + 1}' if mark else ''
        raise ConfigError(f'{shown}: not valid YAML{where} ({getattr(exc, "problem", None) or exc})') from None
    if not isinstance(data, dict):
        raise ConfigError(f'{shown} should contain "key: value" settings')
    return data


def _read_dotenv(path: Path) -> dict[str, str]:
    """KEY=value lines from .env (blank lines and # comments skipped, quotes removed)."""
    if not path.is_file():
        return {}
    out = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key:
            out[key] = value
    return out


def _merge(base: dict, extra: dict) -> None:
    """Lay `extra` on top of `base`, in place. Nested sections are merged, not replaced."""
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value


# ── turning pydantic's errors into one plain sentence each ──
def _unwrap(annotation: Any, metadata: list) -> tuple[Any, list]:
    """Look through `X | None` and Annotated[X, limits] to X and its limits."""
    while True:
        origin = typing.get_origin(annotation)
        if origin is Annotated:
            annotation, *extra = typing.get_args(annotation)
            for item in extra:      # Field(...) keeps its limits in .metadata
                metadata = metadata + (list(item.metadata) if isinstance(item, FieldInfo) else [item])
        elif origin in (typing.Union, types.UnionType):
            annotation = next(a for a in typing.get_args(annotation) if a is not type(None))
        else:
            return annotation, metadata


def _field_at(loc: Loc) -> tuple[Any, list]:
    """The type and limits of the setting at `loc` (as pydantic reports it)."""
    annotation, metadata = AppConfig, []
    for part in loc:
        annotation, metadata = _unwrap(annotation, metadata)
        origin, args = typing.get_origin(annotation), typing.get_args(annotation)
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            field = annotation.model_fields[part]
            annotation, metadata = field.annotation, list(field.metadata)
        elif origin is dict:
            annotation, metadata = args[1], []
        elif origin is tuple:
            annotation, metadata = (args[0] if args[-1] is Ellipsis else args[int(part)]), []
    return _unwrap(annotation, metadata)


def _number(x: float) -> str:
    return f'{x:g}'


def _describe(annotation: Any, metadata: list) -> str:
    """What a setting should be, in plain words: 'a number above 0'."""
    for m in metadata:
        if isinstance(m, _Says):
            return m.words
    if typing.get_origin(annotation) is Literal:
        return 'one of: ' + ', '.join(str(a) for a in typing.get_args(annotation))
    if annotation is bool:
        return 'true or false'
    if annotation is str:
        return 'text'
    if typing.get_origin(annotation) is tuple:
        return 'a list, like [a, b]'
    if typing.get_origin(annotation) is dict or (isinstance(annotation, type) and issubclass(annotation, BaseModel)):
        return 'a group of settings'
    if annotation not in (int, float):
        return 'a path' if annotation is Path else str(annotation)
    words = 'a whole number' if annotation is int else 'a number'
    limits = {}
    for m in metadata:
        for name in ('gt', 'ge', 'le'):
            if isinstance(m, getattr(annotated_types, name.title())):
                limits[name] = _number(getattr(m, name))
    if 'ge' in limits and 'le' in limits:
        return f"{words} from {limits['ge']} to {limits['le']}"
    if 'gt' in limits:
        return f"{words} above {limits['gt']}"
    if 'ge' in limits:
        return f"{words}, {limits['ge']} or more"
    return words


def _got(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= 60 else text[:57] + '...'


def _dotted(loc: Loc) -> str:
    """('camera', 'webcam_indices', 1) → 'camera.webcam_indices[1]'."""
    out = ''
    for part in loc:
        out += f'[{part}]' if isinstance(part, int) else (f'.{part}' if out else str(part))
    return out


_OTHER_FILES = ('protocols', 'agents_file')
# Parts of AppConfig that don't come from settings.yaml, so settings.yaml may not set them.
_NOT_FROM_SETTINGS = ('protocols', 'agents_file', 'secrets', 'env_applied', 'env_moved', 'root')
_SECRET_NAMES = {'secrets', *(k.lower() for k in SECRET_KEYS)}


def _unknown(file: str, loc: Loc, shown: Loc) -> str:
    """Message for a key nobody reads: most likely a typo, or an API key in the wrong place."""
    if str(loc[-1]).lower() in _SECRET_NAMES:
        return f'{file}: {_dotted(shown)} is not a known setting (API keys go in .env, see .env.example)'
    known = [k for k in _field_at(loc[:-1])[0].model_fields if loc[:-1] or k not in _NOT_FROM_SETTINGS]
    close = difflib.get_close_matches(str(loc[-1]), known, n=1)
    hint = f' (did you mean {_dotted(shown[:-1] + (close[0],))}?)' if close else ''
    return f'{file}: {_dotted(shown)} is not a known setting{hint}'


def _problem(err: dict, sources: _Sources) -> str:
    loc = tuple(err['loc'])
    file = sources.last(loc)
    shown = loc[1:] if loc[0] in _OTHER_FILES else loc     # as written in that file
    if err['type'] == 'extra_forbidden':
        return _unknown(file, loc, shown)
    if err['type'] == 'missing':
        return f'{sources.first(loc)}: {_dotted(shown)} is missing'
    if err['type'] == 'value_error' and isinstance(err['input'], dict):     # a check across two settings
        return f'{file}: {_dotted(shown)}: {err["ctx"]["error"]}'
    return f'{file}: {_dotted(shown)} must be {_describe(*_field_at(loc))} (got {_got(err["input"])})'


def load_config(root: Path | None = None, environ: Mapping[str, str] | None = None,
                dotenv: Path | None = None) -> AppConfig:
    """Build the config from <root>/config/ and <root>/.env. Tests pass a temp folder and environ={}.

    A value already set in the environment wins over the same line in .env."""
    root = Path(root or PROJECT_ROOT)
    environ = os.environ if environ is None else environ
    env = _read_dotenv(Path(dotenv) if dotenv is not None else root / '.env')
    env.update({k: v for k, v in environ.items() if v})
    config_dir = root / 'config'
    sources = _Sources()

    def read(name: str, prefix: Loc = ()) -> dict:
        shown = (config_dir / name).relative_to(root).as_posix()
        data = _read_yaml(config_dir / name, shown)
        sources.record(data, shown, prefix)
        return data

    data = read('settings.yaml')
    if (config_dir / 'settings.local.yaml').is_file():
        _merge(data, read('settings.local.yaml'))
    problems = [_unknown(sources.last((key,)), (key,), (key,)) for key in data if key in _NOT_FROM_SETTINGS]
    data['protocols'] = read('protocols.yaml', ('protocols',))
    data['agents_file'] = read('agents.yaml', ('agents_file',))
    applied = {}
    for name, (section, key) in ENV_OVERRIDES.items():
        value = env.get(name, '').strip()
        if value:
            data.setdefault(section, {})[key] = value
            sources.files[(section, key)] = [f'.env or the environment ({name})']
            applied[name] = f'{section}.{key}'
    data['env_applied'] = applied
    data['env_moved'] = tuple(k for k in MOVED_ENV if env.get(k, '').strip())
    data['secrets'] = {k.lower(): env.get(k, '').strip() for k in SECRET_KEYS}
    data['root'] = root
    try:
        config = AppConfig.model_validate(data)
    except ValidationError as exc:
        problems += [_problem(e, sources) for e in exc.errors()]
    if problems:
        raise ConfigError('\n'.join(problems))
    return config
