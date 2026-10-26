"""
The Settings page:

    GET /api/rules       the danger rules and the desk corners (config/safety_rules.json, or the built-in ones)
    PUT /api/rules       change some of them; marking the desk corners also notes how the phone was tilted
    GET /api/ui_settings the numbers and words the app page uses (refresh times, limits, Lens's card ...), so
                         it keeps no copies of its own
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from desktalk.config import AppConfig
from desktalk.domain import LENS
from desktalk.web.deps import App
from desktalk.web.routers.phone import phone_settings
from desktalk.web.schemas import (
    Ok,
    RuleChanges,
    UiChat,
    UiDesk,
    UiHabits,
    UiLensCard,
    UiListSizes,
    UiPage,
    UiPi,
    UiSensitivityStep,
    UiSettings,
)

# The things a danger zone can be set for on the Settings page.
ZONE_OBJECTS = [
    'cell phone', 'laptop', 'keyboard', 'mouse', 'remote', 'book', 'notepaper', 'tablet',
    'bottle', 'cup', 'wine glass', 'fork', 'knife', 'spoon', 'bowl',
    'banana', 'apple', 'sandwich', 'orange', 'pizza', 'donut', 'cake',
    'scissors', 'clock', 'vase', 'tv', 'earphone', 'key', 'watch', 'pen/pencil',
]
RULE_PARTS = ('danger_zones', 'edge_proximity', 'chain_reaction', 'temporal')
TILT_DECIMALS = 2
MS = 1000

router = APIRouter()


@router.get('/api/rules')
def rules(app: App) -> dict[str, Any]:
    return {**app.safety_rules.for_settings_page(), 'available_objects': ZONE_OBJECTS}


@router.put('/api/rules')
def change_rules(app: App, sent: RuleChanges | None = None) -> Ok:
    if sent is None or not sent.sent():
        raise HTTPException(400, 'Empty payload')
    changes = {part: getattr(sent, part) for part in RULE_PARTS if part in sent.sent()}
    if 'desk_outline' in sent.sent():
        try:
            corners = app.safety_rules.clean_outline(sent.desk_outline)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        changes['desk_outline'] = corners
        # How the phone was tilted when the desk was marked, so Lens can tell if it gets bumped.
        tilt = app.phone.recent_gravity() if app.phone is not None else None
        changes['camera_reference'] = {'gravity': [round(v, TILT_DECIMALS) for v in tilt]} if corners and tilt else None
        app.camera_health.forget('bumped')
    app.safety_rules.save(changes)
    return Ok()



@router.get('/api/ui_settings')
def ui_settings(app: App) -> UiSettings:
    return ui_settings_from(app.config)


def ui_settings_from(c: AppConfig) -> UiSettings:
    web, chat, habits, safety = c.web, c.chat, c.habit_tracker, c.safety
    sizes = web.list_sizes
    return UiSettings(
        web=UiPage(refresh_ms=web.refresh_ms, request_timeout_ms=web.request_timeout_ms,
                   live_timeout_ms=web.live_timeout_ms,
                   activity_ping_ms=web.activity_ping_ms, toast_ms=web.toast_ms,
                   camera_note_after_sec=web.camera_note_after_sec, saved_note_sec=web.saved_note_sec,
                   retry_start_sec=web.retry_start_sec, log_shown=web.list_sizes.log,
                   photos_shown=web.list_sizes.photos,
                   chat_kept=web.list_sizes.chat_kept, chat_saved=web.list_sizes.chat_saved,
                   left_chat_shown_min=web.left_chat_shown_min,
                   settings_sliders=web.settings_sliders, new_zone_px=web.new_zone_px,
                   palette=list(web.palette)),
        chat=UiChat(message_max_chars=chat.message_max_chars, history_from_app=chat.history_from_app,
                    todo_max_items=chat.todo_max_items, todo_max_chars=chat.todo_max_chars),
        habits=UiHabits(food_label_max_chars=habits.food_label_max_chars,
                        stop_before_bed_hours=c.protocols.hydration.stop_before_bed_hours,
                        morning_minutes=c.protocols.light.morning_minutes,
                        morning_window_min=c.protocols.light.morning_window_min,
                        evening_dim_before_bed_min=c.protocols.light.evening_dim_before_bed_min,
                        breaks=c.protocols.breaks),
        desk=UiDesk(edge_proximity=safety.edge_proximity, desk_border_share=safety.desk_border_share,
                    min_desk_corners=safety.min_desk_corners, max_desk_corners=safety.max_desk_corners),
        quiet_hours=c.reminders.quiet_hours,
        pi=UiPi(fps=c.pi.fps, delete_every_min=c.pi.delete_every_min),
        phone=phone_settings(c.camera),
        lens_card=UiLensCard(id=LENS, **c.prompts.lens.card.model_dump()),
        list_sizes=UiListSizes(on_desk_shown=sizes.on_desk_shown, camera_issues_shown=sizes.camera_issues_shown,
                               risk_lines_shown=sizes.risk_lines_shown, side_photos_shown=sizes.side_photos_shown,
                               quote_chars=sizes.quote_chars, toast_chars=sizes.toast_chars,
                               mention_max_chars=sizes.mention_max_chars,
                               counter_label_chars=sizes.counter_label_chars),
        starter_todo=c.prompts.ui.starter_todo, starter_counters=list(c.prompts.ui.starter_counters),
        sensitivity_steps=[UiSensitivityStep(**step.model_dump()) for step in c.agents.sensitivity_steps],
        sensitivity_default=c.agents.sensitivity_default,
        chat_reply_timeout_ms=min(most_ai_calls_per_message(c) * round(c.llm.longest_reply_sec() * MS)
                                  + web.request_timeout_ms, web.chat_reply_max_ms),
    )


def most_ai_calls_per_message(c: AppConfig) -> int:
    """The most AI calls one POST /api/chat can make, one after another: a group message (one per agent that
    answers, at most agents.max_agents) or a lost-item question (looks at pictures, then the answer)."""
    return max(c.agents.max_agents, c.lost_items.most_ai_calls())
