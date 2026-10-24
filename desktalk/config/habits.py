"""The habit rules in config/protocols.yaml. What each setting means is in that file's comments."""

from __future__ import annotations

from desktalk.config.base import (
    ClockTime,
    Fraction,
    Latitude,
    Level,
    Longitude,
    PositiveInt,
    PositiveNumber,
    ZeroOrMore,
    _Section,
)


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
    name_shown_chars: PositiveInt


class Protocols(_Section):
    schedule: Schedule
    hydration: HydrationRule
    breaks: BreaksRule
    light: LightRule
    phone_free: PhoneFreeRule
    food: FoodRule
    tasks: TasksRule
