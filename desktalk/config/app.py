"""
The settings in config/settings.yaml, the API keys from .env, and AppConfig: everything together.
What each setting means is in the comments of config/settings.yaml.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr

from desktalk.config.agents import AgentsFile
from desktalk.config.base import (
    CONFIG_FOLDER,
    Count,
    Fraction,
    Hour,
    JpegQuality,
    Level,
    Port,
    PositiveInt,
    PositiveNumber,
    Words,
    ZeroOrMore,
    _Section,
)
from desktalk.config.habits import Protocols
from desktalk.config.prompts import Prompts


class CameraSettings(_Section):
    source: Literal['pi', 'phone', 'webcam']
    fps: PositiveNumber
    disconnect_after_sec: PositiveNumber
    webcam_indices: tuple[Count, ...]
    phone_port: Port
    phone_cert_folder: Path
    phone_cert_days: PositiveInt
    phone_cert_file: str              # file names in phone_cert_folder
    phone_key_file: str
    phone_max_side: PositiveInt
    phone_jpeg_quality: JpegQuality
    phone_tilt_max: PositiveNumber
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
    pair_key_file: Path
    keep_newer_picture_sec: ZeroOrMore
    pi_clock_pictures: PositiveInt


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
    scheduler_longest_wait_sec: PositiveNumber
    stop_wait_sec: PositiveNumber


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
    error_text_chars: PositiveInt
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
    panel_action_max_chars: PositiveInt


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
    question_max_chars: PositiveInt
    message_max_chars: PositiveInt
    photo_note_max_chars: PositiveInt
    live_note_max_chars: PositiveInt
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
    pi_detector: ZeroOrMore


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


ColorPart = Annotated[int, Field(ge=0, le=255)]
Bgr = tuple[ColorPart, ColorPart, ColorPart]       # [blue, green, red], OpenCV's order
Thickness = Annotated[int, Field(ge=1)]
Dash = tuple[PositiveInt, PositiveInt]             # [dash, gap] in pixels


class DrawingColors(_Section):
    box_on: Bgr
    box_off: Bgr
    danger: Bgr
    warn: Bgr
    zone: Bgr
    zone_hit: Bgr
    chain: Bgr
    moving: Bgr
    text: Bgr
    soft_text: Bgr
    soft_back: Bgr
    badge_back: Bgr
    risk: tuple[Bgr, Bgr, Bgr, Bgr, Bgr]          # risk level 0-4


class FontScales(_Section):
    box: PositiveNumber
    label: PositiveNumber
    zone: PositiveNumber
    chain: PositiveNumber
    crowd: PositiveNumber
    moving: PositiveNumber
    count: PositiveNumber
    risk: PositiveNumber
    risk_events: PositiveNumber


class FillAlpha(_Section):
    zone: Fraction
    crowd: Fraction


class Trail(_Section):
    start_after: PositiveInt
    keep: PositiveInt
    shade_from: ColorPart
    shade_range: ColorPart


class Dashes(_Section):
    zone: Dash
    chain: Dash


class Radii(_Section):
    _in_order = (('chain', 'chain_ring'), ('trail_dot_min', 'trail_dot'))
    chain: PositiveInt
    chain_ring: PositiveInt
    count: PositiveInt
    trail_dot: PositiveInt
    trail_dot_min: PositiveInt


class DrawingSettings(_Section):
    """How the live view with boxes is drawn (read from Task 5 of the cleanup plan on)."""

    colors: DrawingColors
    line_px: Thickness
    thin_line_px: Thickness
    bold_text_px: Thickness
    font_scale: FontScales
    fill_alpha: FillAlpha
    trail: Trail
    dash: Dashes
    arrow_tip: Fraction
    radius: Radii
    badge: tuple[Count, Count, Count, Count]


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
    log: PositiveInt              # these two: how many the app page asks for or shows
    photos: PositiveInt


class PageRefresh(_Section):
    """How often the app page asks for new numbers, in thousandths of a second."""

    live: PositiveInt
    habits: PositiveInt
    photos: PositiveInt
    log: PositiveInt


class WebSettings(_Section):
    host: str
    port: Port
    app_folder: Path
    phone_folder: Path
    stop_wait_sec: PositiveNumber
    max_request_mb: PositiveNumber
    pi_upload_max_mb: PositiveNumber
    live_retry_ms: PositiveInt
    live_keepalive_sec: PositiveNumber
    live_queue_size: PositiveInt
    video_resend_sec: PositiveNumber
    video_check_every_sec: PositiveNumber
    photo_cache_sec: Count
    list_sizes: ListSizes
    refresh_ms: PageRefresh
    request_timeout_ms: PositiveInt
    live_timeout_ms: PositiveInt
    first_check_timeout_ms: PositiveInt
    activity_ping_ms: PositiveInt
    toast_ms: PositiveInt
    palette: Words


class LoggingSettings(_Section):
    folder: Path
    level: Literal['DEBUG', 'INFO', 'WARNING', 'ERROR']
    file_size_mb: PositiveNumber
    files_kept: Count
    file_name: str
    preview_chars: PositiveInt


# ════════════════════════════════════════════════════════════════════
#  .env (never YAML)
# ════════════════════════════════════════════════════════════════════
class Secrets(_Section):
    """API keys and the Ollama address. Empty when not set. Keys are hidden when printed."""

    gemini_api_key: SecretStr
    openai_api_key: SecretStr
    groq_api_key: SecretStr
    ollama_url: str



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
    drawing: DrawingSettings
    habit_tracker: HabitTrackerSettings
    web: WebSettings
    logging: LoggingSettings
    storage: StorageSettings
    protocols: Protocols
    agents_file: AgentsFile
    prompts: Prompts                  # config/prompts.yaml, with the settings blanks filled in
    secrets: Secrets
    env_applied: dict[str, str]       # .env name → the setting it changed, e.g. {'OLLAMA_MODEL': 'llm.ollama_model'}
    env_moved: tuple[str, ...]        # old .env names that are set but no longer used
    root: Path                        # the project folder; relative paths in the settings are under it

    def resolve(self, path: str | Path) -> Path:
        """A path from the settings: absolute paths and ~ stay as they are, others are under the project folder."""
        path = Path(path).expanduser()
        return path if path.is_absolute() else self.root / path

    @property
    def config_folder(self) -> Path:
        """The folder the config files came from (config/safety_rules.json is kept there too)."""
        return self.root / CONFIG_FOLDER
