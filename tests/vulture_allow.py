"""Names the dead code check (vulture) can't see being used. This file is only read by vulture, never run.

Run the check with: uv run vulture
Add a name here only when something really uses it in a way vulture can't follow (a pydantic field read
by name, a field the web page reads from the JSON, a flag another thread reads). Say what uses it.
"""

from typing import Any, cast

from uvicorn import Server

from desktalk.config import (
    AiReplies,
    CameraSettings,
    LensWaits,
    LlmSettings,
    LoggingSettings,
    RiskWeights,
    VisionNudgeRepeats,
)
from desktalk.config.prompts import (
    AlertPrompts,
    ByCamera,
    ChatPrompts,
    HabitAreas,
    HabitReminderPrompts,
    LensPrompts,
    LostItemsPrompts,
    PiFixed,
    Prompts,
    Replies,
    SmallTalkPrompts,
    VisionPrompts,
)
from desktalk.core.habits.habit_days import HabitDay
from desktalk.core.habits.sun_times import SunTimes
from desktalk.core.habits.tracker import DayTimes
from desktalk.domain import SeenObject
from desktalk.domain.events import AgentLeft, Compaction, DetectionEvent, NudgeSent
from desktalk.ports import (
    CharacterMemory,
    Clock,
    DeskScene,
    EventStore,
    HabitsLifecycle,
    LivePictures,
    PhoneControls,
    PhotoAlbum,
    PictureInbox,
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
    # Task 6 (the AI's errors, and the log line quoting an AI reply in core/chat/message_generator.py):
    LlmSettings.error_text_chars, LoggingSettings.preview_chars,
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
    # The habit day file's fields: HabitDay.to_dict() writes them to data/habits/<day>.json, and today() reads
    # them back by name for the Habits page (summary_sent is kept for day files written before the cleanup).
    HabitDay.last_break_ts, HabitDay.desk_sec, HabitDay.phone_free_sec, HabitDay.phone_on_desk_sec,
    HabitDay.sunlight_minutes, HabitDay.danger_count, HabitDay.lost_alerts, HabitDay.nudges_sent,
    HabitDay.summary_sent,
    # Keys of the sunrise / sunset and day-times dicts, read by name (tracker.day_times, today()['times']).
    # (TypedDicts have no attributes, hence the casts.)
    cast(Any, SunTimes).sunrise, cast(Any, SunTimes).estimated, cast(Any, DayTimes).sunrise_ts,
    # Only bootstrap saves the habits (Task 10 types its habits field as HabitsLifecycle).
    HabitsLifecycle,
    # Lens's words for a lost webcam: read with model_dump().get(camera) in desktalk/core/vision/camera_health.py.
    ByCamera.webcam,
    # Reply fields the phone page (phone_camera/camera.js) and the app page (web_app/app.js) read from the JSON
    # of GET /api/phone/config and GET /api/ui_settings.
    PhoneSettings.max_side, PhoneSettings.tilt_max, PhoneSettings.capture_width, PhoneSettings.capture_height,
    UiPage.log_shown, UiPage.photos_shown, UiPage.habit_log_shown,
    UiSettings.lens_card, UiSettings.chat_reply_timeout_ms,
    # Interfaces and methods added in Task 4 of the cleanup plan, for the code later tasks switch over.
    # Task 6 (Lens, the daily summary) and Task 7 (the habits) ask the clock for the local hour:
    Clock.local_hour, SystemClock.local_hour,
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
