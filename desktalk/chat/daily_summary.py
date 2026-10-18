"""
End-of-day recap: once a day at settings → daily_summary.hour, an agent on the
desk (or Lens) sums up the day: water, breaks, phone-free time, sunlight,
near-spills, lost items. Only sent if the camera actually ran that day.
"""

from __future__ import annotations

import random
import threading
import time

from . import prompts


def recap_facts(stats: dict) -> str:
    hourly = stats.get('hourly_activity') or [0] * 24
    facts = []
    scan = []
    for h in range(24):
        if hourly[h] > 0:
            if scan and scan[-1][1] == h:
                scan[-1] = (scan[-1][0], h + 1, scan[-1][2] + hourly[h])
            else:
                scan.append((h, h + 1, hourly[h]))
    if scan:
        best = max(scan, key=lambda s: s[2])
        facts.append(f'Busiest period: between {best[0]}:00 and {best[1]}:00.')
    h, b, pf, light = stats['hydration'], stats['breaks'], stats['phone_free'], stats['light']
    facts.append(f'Water: about {h["water_ml"] / 1000:.1f} of {h["goal_ml"] / 1000:.1f} L ({h["sips"]} sips).')
    facts.append(f'Breaks taken: {b["count"]}. Longest stretch sitting: {b["longest_sit_sec"] // 60} minutes.')
    if pf.get('counted'):
        facts.append(f'Phone-free desk time: {pf["phone_free_sec"] // 60} minutes (best streak {pf["best_sec"] // 60}).')
    facts.append('Morning sunlight: logged.' if light.get('sunlight_ts') else 'Morning sunlight: not logged.')
    if stats.get('desk_sec'):
        facts.append(f'Total time at desk: ~{stats["desk_sec"] // 60} minutes.')
    if stats.get('danger_count'):
        facts.append(f'Near-spill or danger alerts: {stats["danger_count"]}.')
    if stats.get('lost_alerts'):
        facts.append(f'Lost-object alerts: {stats["lost_alerts"]}.')
    if stats.get('objects_seen'):
        facts.append(f'Objects seen today: {", ".join(stats["objects_seen"][:8])}.')
    return ' '.join(facts)


class DailySummaryThread(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True, name='DailySummary')
        self.app = app
        self.hour = int(app.settings.daily_summary.get('hour', 21))
        self._last_day = None

    def run(self):
        time.sleep(120)
        while True:
            try:
                self.tick(time.time())
            except Exception as exc:
                print(f'  [daily-summary] Error: {exc!r}')
            time.sleep(60)

    def tick(self, now: float) -> str | None:
        lt = time.localtime(now)
        stats = self.app.habits.today(now)
        if lt.tm_hour != self.hour or self._last_day == stats['day'] or not stats.get('objects_seen'):
            return None
        if self.app.habits.d.get('summary_sent'):
            self._last_day = stats['day']
            return None
        return self.send(stats)

    def send(self, stats: dict) -> str:
        facts = recap_facts(stats)
        text = None
        if self.app.llm.ready:
            text = self.app.llm.chat([{'role': 'system', 'content': prompts.DAILY_SUMMARY_SYSTEM},
                                      {'role': 'user', 'content': f"Here are today's desk stats:\n{facts}\n\nWrite the daily summary."}],
                                     max_tokens=140, temperature=0.7, background=True)
        text = text or f"Today's desk recap: {facts}"
        members = self.app.roster.member_ids()
        speaker = random.choice(members) if members else 'lens'
        self.app.generator.broadcast({'type': 'group_message', 'character': speaker, 'text': text, 'recap': True})
        self.app.habits.d['summary_sent'] = True
        self.app.habits.save(force=True)
        self._last_day = stats['day']
        print(f'  [daily-summary] {speaker}: {text[:100]}')
        return text
