"""
What the AI is told about the desk right now: who is on it, what else is in the picture, the dangers,
the latest alerts, today's habits, and how the objects you @mention are doing.

It reads the desk through two small interfaces (desktalk/ports.py): the DeskScene (what the camera
sees) and the SafetyBoard (the dangers and the latest alerts).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from desktalk.config import ChatSettings
from desktalk.config.prompts import LiveContextWords
from desktalk.core.chat.history import last
from desktalk.core.chat.names import parse_mentions
from desktalk.ports import DeskScene, Profiles, SafetyBoard
from desktalk.util import format_duration


def build_live_context(scene: DeskScene, safety: SafetyBoard, *, profiles: Profiles, members: Sequence[str],
                       habits_today: Mapping[str, Any], now: float, camera_on: bool, chat: ChatSettings,
                       words: LiveContextWords, user_message: str = '') -> dict[str, Any]:
    """Real-time desk facts for the AI: summary text, detected agents, dangers,
    safety state, and the status of objects @mentioned in `user_message`. `words`: config/prompts.yaml →
    live_context."""
    w = words
    lines: list[str] = []
    detected_info: list[dict] = []
    mentioned: list[dict] = []
    members_seen = scene.members()
    on_desk = scene.on_desk()                   # what the warnings see, in the chat or not
    objects = {**on_desk, **members_seen}
    report = safety.now()
    dangers = [d.to_dict() for d in report.dangers]
    rel_result = report.result
    recent = safety.recent_alerts(chat.recent_events_kept)
    vision_seen = sorted(scene.scan_labels())
    session_start = scene.session_started_at()
    others = {label for label in [*scene.seen(), *(box.label for box in on_desk.values())]
              if not profiles.is_ignored(label)}

    def on_desk_for(pid: str) -> str:
        first = scene.first_seen(pid)
        return format_duration(now - first) if first is not None else w.unknown

    if not camera_on:
        lines.append(w.camera_off)
    if members_seen or members:
        lines.append(w.members)
        for pid in members:
            p = profiles.get(pid)
            det = objects.get(pid)
            if not p:
                continue
            here_for = on_desk_for(pid)
            detected_info.append({'char_id': pid, 'name': p.name, 'object': p.object,
                                  'confidence': round(det.confidence, 2) if det else None, 'on_desk_for': here_for})
            label = det.label if det else p.labels[0] if p.labels else p.object
            lines.append(w.member.format(name=p.name, object=p.object, label=label, here_for=here_for))
    else:
        lines.append(w.nobody)
    member_labels = {objects[m].label for m in members if m in objects}
    extra = [label for label in others if label not in member_labels]
    if extra:
        lines.append(w.also_seen.format(labels=', '.join(sorted(extra)[:chat.other_objects_shown])))

    risk_level = rel_result.get('risk_level', 0) if rel_result else 0
    if dangers:
        lines.append(w.dangers.format(risk=risk_level, state=report.state))
        for d in dangers:
            since = safety.danger_since(frozenset(d['pair']))
            duration = format_duration(now - since) if since is not None else w.just_now
            kind = d.get('type', '')
            if kind == 'zone_intrusion':
                lines.append(w.zone_danger.format(label=d['label'], depth=d.get('depth_px', 0), duration=duration))
            elif kind == 'edge_danger':
                lines.append(w.edge_danger.format(label=d['label'], duration=duration))
            else:
                lines.append(w.other_danger.format(label=d['label'], duration=duration))
    else:
        lines.append(w.no_dangers.format(risk=risk_level))
    if rel_result and rel_result.get('explanations'):
        lines.append(w.insights)
        for exp in rel_result['explanations'][:chat.insights_shown]:
            lines.append(w.insight.format(text=exp))
    if vision_seen:
        lines.append(w.vision_pass.format(labels=', '.join(vision_seen)))
    if recent:
        # Says "last N" (kept) but lists fewer (shown), as the old app did.
        lines.append(w.recent.format(count=len(recent)))
        for ev in last(recent, chat.recent_events_shown):
            age = format_duration(now - ev['time'])
            speaker = profiles.get(ev['character'])
            near = profiles.get(ev.get('near', ''))
            lines.append(w.recent_event.format(
                age=age, who=speaker.name if speaker else w.unknown,
                near=w.recent_near.format(name=near.name) if near else '',
                label=ev.get('rule_label', '') or ev.get('severity', '')))

    h, b, pf = habits_today['hydration'], habits_today['breaks'], habits_today['phone_free']
    lines.append(w.habits.format(
        water_l=h['water_ml'] / 1000, goal_l=h['goal_ml'] / 1000, sips=h['sips'], breaks=b['count'],
        sitting_min=b['sitting_sec'] // 60,
        phone_free=w.habits_phone_free.format(minutes=pf['phone_free_sec'] // 60) if pf['counted'] else '',
        sunlight=w.habits_sunlight if habits_today['light']['sunlight_ts'] else ''))
    if session_start:
        lines.append(w.session.format(duration=format_duration(now - session_start)))

    if user_message:
        um = user_message.lower()
        if any(phrase in um for phrase in w.pushback_words):
            lines.append(w.pushback)
        for pid in parse_mentions(user_message, profiles.mention_words()):
            p = profiles.get(pid)
            if p is None:
                continue
            det = objects.get(pid)
            last_seen = scene.last_seen(pid)
            if det:
                status = w.on_desk.format(here_for=on_desk_for(pid), label=det.label)
                ds = [d for d in dangers if det.label in d['pair']]
                if ds:
                    status += w.on_desk_danger.format(dangers='; '.join(d['label'] for d in ds))
            elif last_seen is not None:
                status = w.off_desk.format(ago=format_duration(now - last_seen))
            else:
                status = w.not_seen
            mentioned.append({'char_id': pid, 'name': p.name, 'object': p.object, 'status': status})

    return {'summary': '\n'.join(lines), 'detected': detected_info, 'dangers': dangers,
            'safety_state': report.state, 'mentioned_objects': mentioned}
