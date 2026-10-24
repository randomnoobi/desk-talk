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
from desktalk.core.chat.history import last
from desktalk.core.chat.names import parse_mentions
from desktalk.ports import DeskScene, Profiles, SafetyBoard
from desktalk.util import format_duration

_PUSHBACK = ('nothing next', 'nothing nect', 'not next to you', 'not a cup', "isn't a cup", 'isnt a cup',
             "that's not", 'thats not', 'that is not', 'there is nothing', "there's nothing",
             'no cup', 'wrong', 'not there', "don't see", 'dont see', 'not next', 'makes no sense')


def build_live_context(scene: DeskScene, safety: SafetyBoard, *, profiles: Profiles, members: Sequence[str],
                       habits_today: Mapping[str, Any], now: float, camera_on: bool, chat: ChatSettings,
                       user_message: str = '') -> dict[str, Any]:
    """Real-time desk facts for the AI: summary text, detected agents, dangers,
    safety state, and the status of objects @mentioned in `user_message`."""
    lines: list[str] = []
    detected_info: list[dict] = []
    mentioned: list[dict] = []
    objects = scene.members()
    report = safety.now()
    dangers = [d.to_dict() for d in report.dangers]
    rel_result = report.result
    recent = safety.recent_alerts(chat.recent_events_kept)
    vision_seen = sorted(scene.scan_labels())
    session_start = scene.session_started_at()
    others = [label for label in scene.seen() if not profiles.is_ignored(label)]

    def on_desk_for(pid: str) -> str:
        first = scene.first_seen(pid)
        return format_duration(now - first) if first is not None else '?'

    if not camera_on:
        lines.append('THE CAMERA IS OFF RIGHT NOW: nothing below is live.')
    if objects or members:
        lines.append('OBJECTS IN THE CHAT / ON THE DESK (vision can be wrong or noisy):')
        for pid in members:
            p = profiles.get(pid)
            det = objects.get(pid)
            if not p:
                continue
            here_for = on_desk_for(pid)
            detected_info.append({'char_id': pid, 'name': p.name, 'object': p.object,
                                  'confidence': round(det.confidence, 2) if det else None, 'on_desk_for': here_for})
            label = det.label if det else p.labels[0] if p.labels else p.object
            lines.append(f'  - {p.name} (you play this as: {p.object}): vision label "{label}", here ~{here_for}')
    else:
        lines.append('NOTHING IS IN THE CHAT YET (the camera has not confirmed any object on the desk).')
    member_labels = {objects[m].label for m in members if m in objects}
    extra = [label for label in others if label not in member_labels]
    if extra:
        lines.append(f'ALSO IN THE PICTURE (not chatting): {", ".join(sorted(extra)[:chat.other_objects_shown])}')

    risk_level = rel_result.get('risk_level', 0) if rel_result else 0
    if dangers:
        lines.append(f'\nACTIVE DANGERS (risk level {risk_level}/4, state: {report.state}):')
        for d in dangers:
            since = safety.danger_since(frozenset(d['pair']))
            duration = format_duration(now - since) if since is not None else 'just now'
            kind = d.get('type', '')
            if kind == 'zone_intrusion':
                lines.append(f'  - {d["label"]} — intrusion {d.get("depth_px", 0)}px deep, active for {duration}')
            elif kind == 'edge_danger':
                lines.append(f'  - {d["label"]} — object near desk edge, active for {duration}')
            else:
                lines.append(f'  - {d["label"]} — active for {duration}')
    else:
        lines.append(f'\nNO ACTIVE DANGERS — desk is safe (risk {risk_level}/4).')
    if rel_result and rel_result.get('explanations'):
        lines.append('\nRELATIONSHIP INSIGHTS:')
        for exp in rel_result['explanations'][:chat.insights_shown]:
            lines.append(f'  - {exp}')
    if vision_seen:
        lines.append(f'\nLAST VISION PASS (Ollama lost-item scan) mentioned: {", ".join(vision_seen)}. '
                     'Use for small items (e.g. AirPods) that YOLO may miss; do not claim a cup if not listed.')
    if recent:
        # Says "last N" (kept) but lists fewer (shown), as the old app did.
        lines.append(f'\nRECENT EVENTS (last {len(recent)}):')
        for ev in last(recent, chat.recent_events_shown):
            age = format_duration(now - ev['time'])
            speaker = profiles.get(ev['character'])
            who = speaker.name if speaker else '?'
            near = profiles.get(ev.get('near', ''))
            label = ev.get('rule_label', '') or ev.get('severity', '')
            lines.append(f'  - {age} ago: {who}{" near " + near.name if near else ""} ({label})')

    h, b, pf = habits_today['hydration'], habits_today['breaks'], habits_today['phone_free']
    lines.append(f'\nHABITS TODAY: water ~{h["water_ml"] / 1000:.1f} of {h["goal_ml"] / 1000:.1f} L '
                 f'({h["sips"]} sips); {b["count"]} breaks; sitting now {b["sitting_sec"] // 60} min'
                 + (f'; phone-free desk time {pf["phone_free_sec"] // 60} min' if pf['counted'] else '')
                 + ('; morning sunlight logged' if habits_today['light']['sunlight_ts'] else ''))
    if session_start:
        lines.append(f'Session active for {format_duration(now - session_start)}.')

    if user_message:
        um = user_message.lower()
        if any(phrase in um for phrase in _PUSHBACK):
            lines.append('\nUSER IS PUSHING BACK ON THE SCENE: believe them about what is physically there. '
                         'Do not repeat confidence numbers, "risk level", or argue with their eyes.')
        for pid in parse_mentions(user_message, profiles.mention_words()):
            p = profiles.get(pid)
            if p is None:
                continue
            det = objects.get(pid)
            last_seen = scene.last_seen(pid)
            if det:
                status = f'On desk ~{on_desk_for(pid)}; vision label {det.label} (camera can be off)'
                ds = [d for d in dangers if det.label in d['pair']]
                if ds:
                    status += '. DANGER: ' + '; '.join(d['label'] for d in ds)
            elif last_seen is not None:
                status = f'Not currently on desk (last seen {format_duration(now - last_seen)} ago)'
            else:
                status = 'Not detected this session'
            mentioned.append({'char_id': pid, 'name': p.name, 'object': p.object, 'status': status})

    return {'summary': '\n'.join(lines), 'detected': detected_info, 'dangers': dangers,
            'safety_state': report.state, 'mentioned_objects': mentioned}
