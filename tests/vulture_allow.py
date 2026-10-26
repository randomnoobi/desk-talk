"""Names the dead code check (vulture) can't see being used. This file is only read by vulture, never run.

Run the check with: uv run vulture
Add a name here only when something really uses it in a way vulture can't follow (a pydantic field read
by name, a field the web page reads from the JSON, a flag another thread reads). Say what uses it.
"""

from typing import Any, cast

from uvicorn import Server

import desktalk.adapters.dev
from desktalk.adapters.dev import pytest_report
from desktalk.bootstrap import DeskTalk
from desktalk.config import (
    AgentsSettings,
    DevHealthLimits,
    DevSettings,
    HabitTrackerSettings,
    LensWaits,
    ListSizes,
    NotificationsSettings,
    RiskWeights,
    WebSettings,
)
from desktalk.config.habits import HydrationRule, TasksRule, _ComputerZone
from desktalk.config.prompts import (
    ByCamera,
    HabitAreas,
    HabitLogged,
    HabitNudges,
    NotificationWords,
    Prompts,
    TasksDue,
    UiWords,
)
from desktalk.core.habits.habit_days import HabitDay
from desktalk.core.habits.sun_times import SunTimes
from desktalk.core.habits.tracker import DayTimes
from desktalk.domain.agents import ChatJoin
from desktalk.domain.dev import DataFile, QueryResult, RunStatus, SmoothingStep, TestOutcome
from desktalk.domain.events import AgentLeft, Compaction, DetectionEvent, NudgeSent
from desktalk.ports import ChatJoins, DesktopNotifications, TodoList
from desktalk.web.schemas import PhoneSettings, PreferenceChanges, UiPage, UiSettings
from desktalk_shared.wire import PiHealth

_ = (
    # Settings read by name with getattr (the risk score adds up each weight, Lens looks up each wait).
    RiskWeights.edge_warning, RiskWeights.zone_intrusion, RiskWeights.repeated_intrusion,
    LensWaits.covered, LensWaits.dark, LensWaits.blurry,
    # Request fields read through model_dump().
    PreferenceChanges.quiet_enabled, PreferenceChanges.quiet_start_hour, PreferenceChanges.quiet_end_hour,
    PreferenceChanges.sensitivity,
    PreferenceChanges.dnd_enabled, PreferenceChanges.dnd_start_hour, PreferenceChanges.dnd_end_hour,
    # Event and status fields the web page (web_app/app.js) reads from the JSON.
    AgentLeft.stayed_sec, Compaction.from_ts, PiHealth.last_cleanup_ts,
    # Written to the picture log to show where the detector ran (pi or computer); the Log tab's
    # /api/log/detections sends it, and the tests check it to tell a Pi detection from one done here.
    DetectionEvent.detected_on,
    # Written with every habit reminder; the rule engine (PR #6) reads it.
    NudgeSent.rule,
    # Wording in config/prompts.yaml, read by name when the config loads (desktalk/config/prompts.py: prompt_texts).
    HabitAreas.screens,
    # The habit day file's fields: HabitDay.to_dict() writes them to data/habits/<day>.json, and today() reads
    # them back by name for the Habits page (summary_sent is kept for day files written before the cleanup).
    HabitDay.last_break_ts, HabitDay.desk_sec, HabitDay.phone_free_sec, HabitDay.phone_on_desk_sec,
    HabitDay.sunlight_minutes, HabitDay.danger_count, HabitDay.lost_alerts, HabitDay.nudges_sent,
    HabitDay.summary_sent,
    # Keys of the sunrise / sunset and day-times dicts, read by name (tracker.day_times, today()['times']).
    # (TypedDicts have no attributes, hence the casts.)
    cast(Any, SunTimes).sunrise, cast(Any, SunTimes).estimated, cast(Any, DayTimes).sunrise_ts,
    # Lens's words for a lost webcam: read with model_dump().get(camera) in desktalk/core/vision/camera_health.py.
    ByCamera.webcam,
    # Reply fields the phone page (phone_camera/camera.js) and the app page (web_app/app.js) read from the JSON
    # of GET /api/phone/config and GET /api/ui_settings.
    PhoneSettings.max_side, PhoneSettings.tilt_max, PhoneSettings.capture_width, PhoneSettings.capture_height,
    UiPage.log_shown, UiPage.photos_shown, UiPage.habit_log_shown,
    UiSettings.lens_card, UiSettings.chat_reply_timeout_ms,
    # tzinfo methods the datetime module calls on the rules' "local" time zone (desktalk/config/habits.py).
    _ComputerZone.dst, _ComputerZone.fromutc,
    # Pydantic's own settings attribute, and the flag uvicorn's thread reads to stop.
    PreferenceChanges.model_config, Server(config=None).should_exit,  # type: ignore[arg-type]
)

# The developer page (docs/plans/2026-10-24-developer-ui-plan.md): its settings, wording, plain data and
# interfaces. Task 1 of the plan adds them; Tasks 2 to 6 use them (the test runner, the database browser, the data
# folder viewer, the smoothing trace and the /api/dev routes). Take a name off once its task uses it.
_developer_page = (
    # pytest calls a plugin's hooks by name: desktalk/adapters/dev/pytest_report.py, loaded with -p.
    pytest_report.pytest_runtest_logreport,
    # Python calls a package's __getattr__ for a name it doesn't have yet: the developer page's tools are only
    # imported when first asked for, so loading the plugin above brings in nothing else.
    desktalk.adapters.dev.__getattr__,
    # Sent to the developer page whole (GET /api/dev/settings → health, with model_dump()), read by dev_ui/.
    DevHealthLimits.pi_cpu_warn_c, DevHealthLimits.pi_cpu_bad_c, DevHealthLimits.pi_disk_warn_mb,
    DevHealthLimits.detect_warn_ms,
    # Plain data the /api/dev routes send as JSON (dataclasses.asdict) and only dev_ui/ reads.
    RunStatus.output_end, SmoothingStep.votes_yes, SmoothingStep.votes_total, SmoothingStep.waiting_sec,
    QueryResult.truncated, QueryResult.took_ms, DataFile.modified,
)

# The desk fixes (docs/superpowers/specs/2026-10-25-desk-fixes-design.md): Task 1 of the plan adds these settings,
# wording, plain data and interfaces; Tasks 2 to 9 use them (sips, who joins the chat, the trace, Mac notifications,
# to-dos with due dates, the app page). Take a name off once its task uses it.
_desk_fixes = (
    AgentsSettings.can_join, AgentsSettings.on_desk_after_sec, AgentsSettings.on_desk_min_confidence,
    HabitTrackerSettings.todos_key,
    ListSizes.on_desk_shown, ListSizes.camera_issues_shown, ListSizes.risk_lines_shown, ListSizes.side_photos_shown,
    ListSizes.quote_chars, ListSizes.toast_chars, ListSizes.mention_max_chars, ListSizes.counter_label_chars,
    WebSettings.camera_note_after_sec, WebSettings.saved_note_sec, NotificationsSettings.warnings,
    DevSettings.trace_photos_folder, DevSettings.trace_photos_kept, DevSettings.trace_photo_every_sec,
    DevSettings.trace_photo_quality, DevSettings.trace_events_kept,
    HydrationRule.sip_lift_share, HydrationRule.sip_back_share, HydrationRule.sip_cover_share,
    TasksRule.due_soon_days, TasksRule.late_repeat_hours,
    HabitLogged.task_done, TasksDue.tomorrow, TasksDue.late, HabitNudges.tasks_due, UiWords.starter_todo,
    UiWords.starter_counters, NotificationWords.sip_title, Prompts.ui, Prompts.notifications,
    DeskTalk.chat_joins, DeskTalk.trace_photos, ChatJoin.seen_for_sec, DesktopNotifications,
    DesktopNotifications.show, TodoList.has_list, ChatJoins.joins,
)
# A test's result, sent to the developer page as JSON (dataclasses.asdict) and shown by dev_ui/ (the Checks tab).
_ = TestOutcome.duration_sec
