"""Names the dead code check (vulture) can't see being used. This file is only read by vulture, never run.

Run the check with: uv run vulture
Add a name here only when something really uses it in a way vulture can't follow (a pydantic field read
by name, a field the web page reads from the JSON, a flag another thread reads). Say what uses it.
"""

from uvicorn import Server

from desktalk.config import (
    AiReplies,
    AppConfig,
    CameraSettings,
    DrawingColors,
    DrawingSettings,
    LensWaits,
    LlmSettings,
    LoggingSettings,
    Radii,
    RiskWeights,
    Trail,
    VisionNudgeRepeats,
)
from desktalk.config.prompts import (
    AlertPrompts,
    ByCamera,
    CameraPrompts,
    ChatPrompts,
    HabitAreas,
    HabitNudges,
    HabitReminderPrompts,
    LensPrompts,
    LostItemsPrompts,
    PiFixed,
    Prompts,
    Replies,
    SideHints,
    SmallTalkPrompts,
    TrackerPrompts,
    VisionPrompts,
)
from desktalk.domain import SeenObject
from desktalk.domain.events import AgentLeft, Compaction, DetectionEvent, NudgeSent
from desktalk.ports import (
    CharacterMemory,
    Clock,
    DeskRulesView,
    DeskScene,
    EventStore,
    LivePictures,
    PhoneControls,
    PhotoAlbum,
    PhotoTaker,
    PictureInbox,
    PictureTools,
    PiStatus,
    SafetyBoard,
)
from desktalk.runtime import SystemClock
from desktalk.web.schemas import PhoneSettings, PreferenceChanges, UiPage, UiSettings
from desktalk_shared.wire import PiHealth

_ = (
    # Settings read by name with getattr (the risk score adds up each weight, Lens looks up each wait).
    RiskWeights.edge_danger, RiskWeights.edge_warning, RiskWeights.zone_intrusion, RiskWeights.crowd,
    RiskWeights.repeated_intrusion,
    AiReplies.alert,
    VisionNudgeRepeats.slouch,
    LensWaits.lost, LensWaits.covered, LensWaits.dark, LensWaits.glare, LensWaits.blurry, LensWaits.bumped,
    LensWaits.slow,
    # Request fields read through model_dump().
    PreferenceChanges.quiet_enabled, PreferenceChanges.quiet_start_hour, PreferenceChanges.quiet_end_hour,
    PreferenceChanges.dnd_enabled, PreferenceChanges.dnd_start_hour, PreferenceChanges.dnd_end_hour,
    # Event and status fields the web page (web_app/app.js) reads from the JSON.
    AgentLeft.stayed_sec, Compaction.from_ts, PiHealth.last_cleanup_ts,
    # Written to the picture log to show where the detector ran (pi or computer); the Log tab's
    # /api/log/detections sends it, and the tests check it to tell a Pi detection from one done here.
    DetectionEvent.detected_on,
    # Written with every habit reminder; the rule engine (PR #6) reads it.
    NudgeSent.rule,
    # Interfaces with no user yet: the web layer starts using them in Task 10 of the cleanup plan.
    EventStore, PictureInbox,
    # Settings added in Task 4 of the cleanup plan for code that later tasks switch over (each says which).
    # Task 5 (the live view drawing):
    AppConfig.drawing, DrawingSettings.colors, DrawingSettings.line_px, DrawingSettings.thin_line_px,
    DrawingSettings.bold_text_px, DrawingSettings.font_scale, DrawingSettings.fill_alpha, DrawingSettings.trail,
    DrawingSettings.arrow_tip, DrawingSettings.badge,
    DrawingColors.box_on, DrawingColors.box_off, DrawingColors.warn, DrawingColors.zone_hit, DrawingColors.moving,
    DrawingColors.soft_text, DrawingColors.soft_back, DrawingColors.badge_back,
    Trail.shade_from, Trail.shade_range, Radii.chain_ring, Radii.trail_dot, Radii.trail_dot_min,
    # Task 6 (the AI's errors) and Task 8 (logging):
    LlmSettings.error_text_chars, LoggingSettings.file_name, LoggingSettings.preview_chars,
    # Task 10 (the phone page's certificate, made in desktalk/web/pairing.py):
    CameraSettings.phone_cert_file, CameraSettings.phone_key_file,
    # The wording in config/prompts.yaml. Every text is already read by name when the config loads
    # (desktalk/config/prompts.py: prompt_texts, fill_settings); the code starts using them in Tasks 5-7.
    # Task 6 (chat, Lens and lost items):
    Replies.needs_an_ai, Replies.empty_reply, Replies.nobody_here, Replies.no_ai_label, Replies.no_picture,
    Replies.ask_for_a_sentence, AlertPrompts.more_worried, AlertPrompts.most_worried, ChatPrompts.identity_short,
    HabitAreas.screens, HabitAreas.no_habits, HabitReminderPrompts.system_short, HabitReminderPrompts.no_evidence,
    SmallTalkPrompts.topic_system, SmallTalkPrompts.director_system, SmallTalkPrompts.line_system,
    LostItemsPrompts.answer_system, LostItemsPrompts.alert_system, LostItemsPrompts.alert_user,
    LostItemsPrompts.scan, LostItemsPrompts.in_saved_photo, LostItemsPrompts.in_live_picture,
    LostItemsPrompts.anything, LostItemsPrompts.triggers, LostItemsPrompts.not_spotted,
    LostItemsPrompts.captures_button, VisionPrompts.habit_scene,
    PiFixed.pi_hot, PiFixed.pi_power, PiFixed.pi_clock,
    LensPrompts.photo_label, LensPrompts.pi_hot, LensPrompts.pi_slowed, LensPrompts.pi_power,
    LensPrompts.pi_clock, LensPrompts.pi_clock_behind, LensPrompts.pi_clock_ahead, LensPrompts.pi_detector_why,
    LensPrompts.pi_fixed, Prompts.habit_areas, Prompts.habit_reminder, Prompts.small_talk,
    # Task 7 (habit nudges and trackers):
    Prompts.trackers, TrackerPrompts.behavior, TrackerPrompts.todo_list, TrackerPrompts.breaks_none_yet,
    TrackerPrompts.water_none_yet, TrackerPrompts.food_empty, TrackerPrompts.food_healthy,
    TrackerPrompts.food_treats, TrackerPrompts.phone_not_counted, TrackerPrompts.phone_on_desk,
    TrackerPrompts.phone_off_desk, TrackerPrompts.light_lux, TrackerPrompts.light_brightness,
    TrackerPrompts.light_unknown, TrackerPrompts.sunlight_logged, TrackerPrompts.sunlight_not_logged,
    HabitNudges.hydration_morning, HabitNudges.hydration_pace, HabitNudges.hydration_pace_cup,
    HabitNudges.water_progress, HabitNudges.breaks_sit, HabitNudges.breaks_meal, HabitNudges.unknown_meal,
    HabitNudges.light_morning, HabitNudges.light_afternoon, HabitNudges.light_evening, HabitNudges.bed_soon,
    HabitNudges.sun_has_set, HabitNudges.light_daytime_dim, HabitNudges.light_lux, HabitNudges.light_brightness,
    HabitNudges.light_bright, HabitNudges.screens_bedtime, HabitNudges.bed_at, HabitNudges.past_bedtime,
    HabitNudges.phone_bedtime, HabitNudges.phone_focus, HabitNudges.food_sitting, HabitNudges.unknown_snack,
    HabitNudges.tasks_open,
    # Task 5 (Lens's camera checks):
    ByCamera.webcam, SideHints.right, SideHints.top, SideHints.bottom,
    CameraPrompts.fixed, CameraPrompts.side_hints, CameraPrompts.other_side_hint, CameraPrompts.unknown_object,
    CameraPrompts.titles, CameraPrompts.lost_title, CameraPrompts.lost_fix, CameraPrompts.cutoff_title,
    CameraPrompts.edge_tip,
    # Reply fields the phone page (phone_camera/camera.js) and the app page (web_app/app.js) read from the JSON
    # of GET /api/phone/config and GET /api/ui_settings.
    PhoneSettings.max_side, PhoneSettings.tilt_max, PhoneSettings.capture_width, PhoneSettings.capture_height,
    UiPage.log_shown, UiPage.photos_shown, UiPage.habit_log_shown,
    UiSettings.lens_card, UiSettings.chat_reply_timeout_ms,
    # Interfaces and methods added in Task 4 of the cleanup plan, for the code later tasks switch over.
    # Task 6 (Lens, the daily summary) and Task 7 (the habits) ask the clock for the local hour:
    Clock.local_hour, SystemClock.local_hour,
    # Task 5 (vision without cv2 in core; the real OpenCV PictureTools) and Task 6 (lost items):
    PictureTools, PictureTools.decode_jpeg, PictureTools.encode_jpeg, PictureTools.shrink, PictureTools.to_gray,
    PictureTools.difference, PictureTools.sharpness, PhotoTaker, DeskRulesView,
    # Task 10 (the web routes and bootstrap take these instead of the Pi's and the phone's classes):
    PiStatus, PhoneControls,
    # Tasks 5, 6, 7 and 10 (the five desk interfaces that replace DeskState; DeskState has the same methods):
    LivePictures, LivePictures.camera_numbers, LivePictures.set_light, LivePictures.newest_light,
    DeskScene, DeskScene.set_members, DeskScene.note_picture, DeskScene.person_seen_at, DeskScene.last_motion,
    DeskScene.session_started_at, DeskScene.set_scan_labels, DeskScene.scan_labels, SeenObject.from_detection,
    SafetyBoard, SafetyBoard.report, SafetyBoard.note_alert, SafetyBoard.recent_alerts,
    CharacterMemory, CharacterMemory.set_widget, CharacterMemory.widgets, CharacterMemory.note_widget_view,
    CharacterMemory.widget_views_of, CharacterMemory.note_user_activity, CharacterMemory.user_active_at,
    CharacterMemory.set_vision_brief, CharacterMemory.vision_brief, PhotoAlbum, PhotoAlbum.newest,
    # Pydantic's own settings attribute, and the flag uvicorn's thread reads to stop.
    PreferenceChanges.model_config, Server(config=None).should_exit,  # type: ignore[arg-type]
)
