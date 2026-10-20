"""
Sunrise and sunset times, for the light habits.

With latitude / longitude in config/protocols.yaml (schedule:) we work out the
real times for today; without them, schedule.sunset_fallback is used.
The math is the standard sunrise equation (accurate to a couple of minutes).
"""

from __future__ import annotations

import calendar
import math
import time

from ..util import at_time_today


def _sun_event_utc_hours(day_of_year: int, lat: float, lon: float, rising: bool) -> float | None:
    """UTC hour (0-24) of sunrise / sunset, or None when the sun doesn't rise or set that day."""
    zenith = 90.833
    lng_hour = lon / 15.0
    t = day_of_year + ((6 if rising else 18) - lng_hour) / 24.0
    m = 0.9856 * t - 3.289
    l = (m + 1.916 * math.sin(math.radians(m)) + 0.020 * math.sin(math.radians(2 * m)) + 282.634) % 360
    ra = math.degrees(math.atan(0.91764 * math.tan(math.radians(l)))) % 360
    ra += (math.floor(l / 90) * 90) - (math.floor(ra / 90) * 90)
    ra /= 15.0
    sin_dec = 0.39782 * math.sin(math.radians(l))
    cos_dec = math.cos(math.asin(sin_dec))
    cos_h = (math.cos(math.radians(zenith)) - sin_dec * math.sin(math.radians(lat))) / (cos_dec * math.cos(math.radians(lat)))
    if cos_h > 1 or cos_h < -1:
        return None
    h = (360 - math.degrees(math.acos(cos_h))) if rising else math.degrees(math.acos(cos_h))
    h /= 15.0
    local_mean = h + ra - 0.06571 * t - 6.622
    return (local_mean - lng_hour) % 24


def sun_times(now: float, lat: float | None, lon: float | None, sunset_fallback: str = '18:30') -> dict:
    """{'sunrise': ts | None, 'sunset': ts} for the local day containing `now`."""
    if lat is None or lon is None:
        return {'sunrise': None, 'sunset': at_time_today(sunset_fallback, now), 'estimated': True}
    lt = time.localtime(now)
    local_midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    utc_midnight = calendar.timegm((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0))   # 00:00 UTC on this date

    def to_ts(rising):
        hrs = _sun_event_utc_hours(lt.tm_yday, float(lat), float(lon), rising)
        if hrs is None:
            return None
        ts = utc_midnight + hrs * 3600
        # keep it on the same local day
        while ts < local_midnight:
            ts += 86400
        while ts >= local_midnight + 86400:
            ts -= 86400
        return ts

    sunset = to_ts(False)
    return {'sunrise': to_ts(True), 'sunset': sunset if sunset else at_time_today(sunset_fallback, now),
            'estimated': sunset is None}
