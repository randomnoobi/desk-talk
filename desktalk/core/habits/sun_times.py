"""
Sunrise and sunset times, for the light habits.

With latitude / longitude in config/protocols.yaml (schedule:) we work out the
real times for today; without them, schedule.sunset_fallback is used.
The math is the standard sunrise equation (accurate to a couple of minutes).
"""

from __future__ import annotations

import calendar
import datetime
import math
from typing import TypedDict

from desktalk.ports import Clock
from desktalk.util import at_time_today

DAY_SEC = 86400


def _sun_event_utc_hours(day_of_year: int, lat: float, lon: float, rising: bool) -> float | None:
    """UTC hour (0-24) of sunrise / sunset, or None when the sun doesn't rise or set that day."""
    zenith = 90.833
    lng_hour = lon / 15.0
    t = day_of_year + ((6 if rising else 18) - lng_hour) / 24.0
    m = 0.9856 * t - 3.289
    sun_lon = (m + 1.916 * math.sin(math.radians(m)) + 0.020 * math.sin(math.radians(2 * m)) + 282.634) % 360
    ra = math.degrees(math.atan(0.91764 * math.tan(math.radians(sun_lon)))) % 360
    ra += (math.floor(sun_lon / 90) * 90) - (math.floor(ra / 90) * 90)
    ra /= 15.0
    sin_dec = 0.39782 * math.sin(math.radians(sun_lon))
    cos_dec = math.cos(math.asin(sin_dec))
    cos_h = ((math.cos(math.radians(zenith)) - sin_dec * math.sin(math.radians(lat)))
             / (cos_dec * math.cos(math.radians(lat))))
    if cos_h > 1 or cos_h < -1:
        return None
    h = (360 - math.degrees(math.acos(cos_h))) if rising else math.degrees(math.acos(cos_h))
    h /= 15.0
    local_mean = h + ra - 0.06571 * t - 6.622
    return (local_mean - lng_hour) % 24


class SunTimes(TypedDict):
    sunrise: float | None
    sunset: float
    estimated: bool         # the sunset is sunset_fallback ("18:30"), not worked out for your place


def sun_times(now: float, lat: float | None, lon: float | None, sunset_fallback: str, clock: Clock) -> SunTimes:
    """Sunrise (None when the sun doesn't rise) and sunset for the local day containing `now` (the clock's)."""
    if lat is None or lon is None:
        return SunTimes(sunrise=None, sunset=at_time_today(sunset_fallback, now), estimated=True)
    day = clock.local_day(now)
    date = datetime.date.fromisoformat(day)
    utc_midnight = calendar.timegm(date.timetuple())       # 00:00 UTC on this date

    def to_ts(rising: bool) -> float | None:
        hours = _sun_event_utc_hours(date.timetuple().tm_yday, float(lat), float(lon), rising)
        if hours is None:
            return None
        ts = utc_midnight + hours * 3600
        # keep it on the same local day ('2026-10-18' < '2026-10-19', so the names compare as dates)
        while clock.local_day(ts) < day:
            ts += DAY_SEC
        while clock.local_day(ts) > day:
            ts -= DAY_SEC
        return ts

    sunset = to_ts(False)
    return SunTimes(sunrise=to_ts(True), sunset=sunset if sunset else at_time_today(sunset_fallback, now),
                    estimated=sunset is None)
