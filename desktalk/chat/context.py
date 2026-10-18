"""
What the AI is told about the desk, and the small text helpers around chat:
@mentions, chat history, keeping replies to one speaker, and refusing
dramatic claims the camera didn't see.
"""

from __future__ import annotations

import random
import re
import time

from ..util import format_duration


def parse_mentions(text: str, words: dict) -> list[str]:
    """@mentions in the user's text → agent ids (in order, no repeats)."""
    out = []
    for m in re.findall(r'@(\w[\w\s]*\w|\w)', (text or '').lower()):
        m = m.strip()
        pid = words.get(m) or words.get(m.split()[0] if m.split() else m)
        if pid and pid not in out:
            out.append(pid)
    return out


def sanitize_chat_history(raw) -> list[dict]:
    """Up to 10 prior turns for 1:1 or group: [{role, text, name?}, ...] from client JSON."""
    if not raw or not isinstance(raw, list):
        return []
    out = []
    for item in raw[-10:]:
        if not isinstance(item, dict):
            continue
        role = (item.get('role') or '').strip().lower()
        if role not in ('user', 'assistant'):
            continue
        text = item.get('text', '')
        if not isinstance(text, str):
            text = str(text)
        text = text.strip()[:1200]
        if not text:
            continue
        d = {'role': role, 'text': text}
        name = (item.get('name') or '').strip()[:64]
        if name:
            d['name'] = name
        out.append(d)
    return out


def budget_history(history, verbatim_turns: int = 6, is_ollama: bool = False):
    """Split sanitized history into (recent_verbatim_turns, older_recap_str).

    The recent turns are passed as real message-array turns so the reply stays
    tightly coupled to the latest user message; anything older is condensed into
    a short recap line that goes into the live-context block."""
    k = 4 if is_ollama else verbatim_turns
    if not history:
        return [], ''
    if len(history) <= k:
        return history, ''
    older, recent = history[:-k], history[-k:]
    bits = []
    for turn in older[-8:]:
        who = 'User' if turn.get('role') == 'user' else (turn.get('name') or 'an object')
        snippet = ' '.join(str(turn.get('text', '')).split())
        if len(snippet) > 70:
            snippet = snippet[:70].rstrip() + '…'
        if snippet:
            bits.append(f'{who}: "{snippet}"')
    return recent, ' | '.join(bits)


_UNSUPPORTED_DANGER_RE = re.compile(
    r'\b(crack(?:ing|ed)?|shatter(?:ing|ed)?|crush(?:ing|ed)?|'
    r'break(?:ing)?|burn(?:ing|ed)?|fire|explode|destroy(?:ed)?|'
    r'at any moment|about to (?:break|fall|shatter|explode))\b',
    re.I,
)
_HAZARD_RE = re.compile(r'\b(too close|spill|danger|hazard|risk|edge|fall(?:ing)? off)\b', re.I)


def history_blob(history) -> str:
    return ' '.join(str(h.get('text', '')) for h in (history or []) if isinstance(h, dict))


def response_violates_evidence(text, context, user_message='', history=None, strict=True) -> bool:
    """Reject dramatic or hazard claims that are not supported by live evidence/user text.
    When *strict* is False (e.g. local Ollama), only block extreme unsupported danger language."""
    if not text:
        return False
    evidence = ' '.join([context.get('summary', ''), user_message or '', history_blob(history)]).lower()
    low = text.lower()
    if _UNSUPPORTED_DANGER_RE.search(low) and not _UNSUPPORTED_DANGER_RE.search(evidence):
        return True
    if strict and _HAZARD_RE.search(low) and not context.get('dangers'):
        if not re.search(r'\b(no|not|nothing|without)\b.{0,24}\b(danger|risk|hazard|too close|spill)\b', low):
            return True
    return False


def single_speaker_text(text, agent, all_names: dict) -> str:
    """Force one object per model call; remove accidental group-script lines.
    `all_names` maps lower-case names and ids → agent id."""
    if not text:
        return ''
    current = (agent.name or '').lower()
    kept = []
    saw_other_speaker = False
    for raw in str(text).splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r'^([A-Za-z][A-Za-z ]{0,30})(?:\s*\(me\))?\s*:\s*(.+)$', line)
        if m:
            speaker = m.group(1).strip().lower()
            body = m.group(2).strip()
            if speaker == current or all_names.get(speaker) == agent.id:
                kept.append(body)
            elif speaker in all_names:
                saw_other_speaker = True
            else:
                kept.append(line)
        else:
            kept.append(line)
    cleaned = ' '.join(kept).strip()
    if saw_other_speaker and not cleaned:
        return ''
    # If everything was stripped but original text had content, keep the
    # original with any leading name prefix removed (common with local models).
    if not cleaned and text.strip():
        cleaned = re.sub(r'^[A-Za-z][A-Za-z ]{0,30}(?:\s*\(me\))?\s*:\s*', '', text.strip(), count=1)
    return cleaned[:500]


def detect_named(message: str, words: dict) -> list[str]:
    """Agent names/aliases in plain text (no @ required)."""
    if not message:
        return []
    low = message.lower()
    found = []
    for keyword, pid in words.items():
        if re.search(r'\b' + re.escape(keyword) + r'\b', low) and pid not in found:
            found.append(pid)
    return found


# Words that introduce an object as the TOPIC of the sentence rather than the
# party being spoken to (e.g. "how do you feel about computer", "stronger than cup").
_TOPIC_PREPS = ('about', 'than', 'regarding', 'concerning', 'like', 'vs', 'versus')


def addressed_agent(message: str, candidates: list[str], words: dict) -> str:
    """Among several named objects, pick the one being addressed (not the topic)."""
    low = (message or '').lower()
    kw_by_id: dict[str, list] = {}
    for kw, pid in words.items():
        if pid in candidates:
            kw_by_id.setdefault(pid, []).append(kw)
    prep_alt = '|'.join(_TOPIC_PREPS)
    addressed = [pid for pid in candidates if not any(
        re.search(r'\b(?:' + prep_alt + r')\s+(?:the\s+|my\s+|your\s+|that\s+)?' + re.escape(kw) + r'\b', low) for kw in kw_by_id.get(pid, []))]
    return addressed[0] if addressed else candidates[0]


def group_speaker_order(message: str, words: dict, members: list[str], known: list[str]) -> list[str]:
    """Who answers a message in the group chat.

    Explicit @mentions are honored as-is (all of them). Otherwise, if the text
    names one object it answers; if it names several, only the one being
    addressed replies (the others are treated as the topic). With no name,
    a random agent that is on the desk answers."""
    speakers = [p for p in parse_mentions(message, words) if p in known]
    if speakers:
        return speakers
    named = [p for p in detect_named(message, words) if p in known]
    if len(named) == 1:
        return named
    if len(named) > 1:
        return [addressed_agent(message, named, words)]
    pool = members or []
    return [random.choice(pool)] if pool else []


def build_live_context(app, user_message: str = '') -> dict:
    """Structured real-time desk facts for the AI (and /api/context)."""
    desk, profiles = app.desk, app.profiles
    now = time.time()
    lines, detected_info, mentioned = [], [], []
    with desk.lock:
        objects = dict(desk.objects)
        dangers = list(desk.safety_dangers)
        safety_state = desk.safety_state
        first_seen = dict(desk.object_first_seen)
        last_seen = dict(desk.object_last_seen)
        danger_since = dict(desk.danger_active_since)
        recent = list(desk.recent_events[-10:])
        rel_result = desk.rel_result
        vlmv = sorted(desk.lost_last_vlm_visible)
        session_start = desk.session_start
        others = [l for l in desk.seen_labels if not profiles.is_ignored(l)]
    members = app.roster.member_ids()

    if not app.camera_connected():
        lines.append('THE CAMERA IS OFF RIGHT NOW: nothing below is live.')
    if objects or members:
        lines.append('OBJECTS IN THE CHAT / ON THE DESK (vision can be wrong or noisy):')
        for pid in members:
            p = profiles.get(pid)
            det = objects.get(pid)
            if not p:
                continue
            on_desk_for = format_duration(now - first_seen[pid]) if pid in first_seen else '?'
            detected_info.append({'char_id': pid, 'name': p.name, 'object': p.object,
                                  'confidence': round(det['conf'], 2) if det else None, 'on_desk_for': on_desk_for})
            label = det['class_name'] if det else p.labels[0] if p.labels else p.object
            lines.append(f'  - {p.name} (you play this as: {p.object}): vision label "{label}", here ~{on_desk_for}')
    else:
        lines.append('NOTHING IS IN THE CHAT YET (the camera has not confirmed any object on the desk).')
    extra = [l for l in others if not any((objects.get(m) or {}).get('class_name') == l for m in members)]
    if extra:
        lines.append(f'ALSO IN THE PICTURE (not chatting): {", ".join(sorted(extra)[:12])}')

    risk_level = rel_result.get('risk_level', 0) if rel_result else 0
    if dangers:
        lines.append(f'\nACTIVE DANGERS (risk level {risk_level}/4, state: {safety_state}):')
        for d in dangers:
            key = frozenset(d['pair'])
            duration = format_duration(now - danger_since[key]) if key in danger_since else 'just now'
            dtype = d.get('type', '')
            if dtype == 'zone_intrusion':
                lines.append(f'  - {d["label"]} — intrusion {d.get("depth_px", 0)}px deep, active for {duration}')
            elif dtype == 'edge_danger':
                lines.append(f'  - {d["label"]} — object near desk edge, active for {duration}')
            else:
                lines.append(f'  - {d["label"]} — active for {duration}')
    else:
        lines.append(f'\nNO ACTIVE DANGERS — desk is safe (risk {risk_level}/4).')
    if rel_result and rel_result.get('explanations'):
        lines.append('\nRELATIONSHIP INSIGHTS:')
        for exp in rel_result['explanations'][:6]:
            lines.append(f'  - {exp}')
    if vlmv:
        lines.append(f'\nLAST VISION PASS (Ollama lost-item scan) mentioned: {", ".join(vlmv)}. '
                     'Use for small items (e.g. AirPods) that YOLO may miss; do not claim a cup if not listed.')
    if recent:
        lines.append(f'\nRECENT EVENTS (last {len(recent)}):')
        for ev in recent[-5:]:
            age = format_duration(now - ev['time'])
            speaker = profiles.get(ev['character'])
            who = speaker.name if speaker else '?'
            near = profiles.get(ev.get('near', ''))
            label = ev.get('rule_label', '') or ev.get('severity', '')
            lines.append(f'  - {age} ago: {who}{" near " + near.name if near else ""} ({label})')

    habits = app.habits.today(now)
    h, b, pf = habits['hydration'], habits['breaks'], habits['phone_free']
    lines.append(f'\nHABITS TODAY: water ~{h["water_ml"] / 1000:.1f} of {h["goal_ml"] / 1000:.1f} L '
                 f'({h["sips"]} sips); {b["count"]} breaks; sitting now {b["sitting_sec"] // 60} min'
                 + (f'; phone-free desk time {pf["phone_free_sec"] // 60} min' if pf['counted'] else '')
                 + ('; morning sunlight logged' if habits['light']['sunlight_ts'] else ''))
    if session_start:
        lines.append(f'Session active for {format_duration(now - session_start)}.')

    if user_message:
        um = user_message.lower()
        if any(phrase in um for phrase in (
                'nothing next', 'nothing nect', 'not next to you', 'not a cup', "isn't a cup", 'isnt a cup',
                "that's not", 'thats not', 'that is not', 'there is nothing', "there's nothing",
                'no cup', 'wrong', 'not there', "don't see", 'dont see', 'not next', 'makes no sense')):
            lines.append('\nUSER IS PUSHING BACK ON THE SCENE: believe them about what is physically there. '
                         'Do not repeat confidence numbers, "risk level", or argue with their eyes.')
        for pid in parse_mentions(user_message, profiles.mention_words()):
            p = profiles.get(pid)
            det = objects.get(pid)
            if det:
                on_desk = format_duration(now - first_seen[pid]) if pid in first_seen else '?'
                status = f'On desk ~{on_desk}; vision label {det["class_name"]} (camera can be off)'
                ds = [d for d in dangers if det['class_name'] in d['pair']]
                if ds:
                    status += '. DANGER: ' + '; '.join(d['label'] for d in ds)
            elif pid in last_seen:
                status = f'Not currently on desk (last seen {format_duration(now - last_seen[pid])} ago)'
            else:
                status = 'Not detected this session'
            mentioned.append({'char_id': pid, 'name': p.name, 'object': p.object, 'status': status})

    return {'summary': '\n'.join(lines), 'detected': detected_info, 'dangers': dangers,
            'safety_state': safety_state, 'mentioned_objects': mentioned}
