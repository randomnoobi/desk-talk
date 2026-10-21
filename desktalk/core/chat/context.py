"""
What the AI is told about the desk, and the small text helpers around chat:
@mentions, who answers in the group, chat history, keeping replies to one speaker,
and refusing dramatic claims the camera didn't see.

Everything here is a plain function: settings and the desk are passed in.
"""

from __future__ import annotations

import random
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from desktalk.config import ChatSettings
from desktalk.desk_state import DeskState
from desktalk.domain import AgentProfile
from desktalk.ports import Profiles
from desktalk.util import format_duration

LENS = 'lens'
_TRAILING_WORDS = ('the', 'a', 'an', 'is', 'are', 'to', 'and', 'but', 'or', 'in', 'of', 'that', 'this')


def _last(items: Sequence, n: int) -> list:
    """The last n items (none when n is 0; items[-0:] would be all of them)."""
    return list(items[-n:]) if n > 0 else []


# ── names ────────────────────────────────────────────────────────────
def mention_words(profiles: Iterable[AgentProfile]) -> dict[str, str]:
    """Every word that can @mention an agent → its id."""
    out: dict[str, str] = {}
    for p in profiles:
        for w in [p.id, p.name, p.nickname, p.object, *p.labels]:
            w = str(w or '').lower().strip()
            if w:
                out.setdefault(w, p.id)
                out.setdefault(w.split('/')[0].strip(), p.id)
    return out


def speaker_names(profiles: Iterable[AgentProfile]) -> dict[str, str]:
    """Lower-case names and ids → agent id, for spotting "Cup: ..." lines (Lens included)."""
    out = {}
    for p in profiles:
        out[p.name.lower()] = p.id
        out[p.id.lower()] = p.id
    out[LENS] = LENS
    return out


def parse_mentions(text: str, words: Mapping[str, str]) -> list[str]:
    """@mentions in the user's text → agent ids (in order, no repeats)."""
    out = []
    for m in re.findall(r'@(\w[\w\s]*\w|\w)', (text or '').lower()):
        m = m.strip()
        pid = words.get(m) or words.get(m.split()[0] if m.split() else m)
        if pid and pid not in out:
            out.append(pid)
    return out


def detect_named(message: str, words: Mapping[str, str]) -> list[str]:
    """Agent names / aliases in plain text (no @ needed)."""
    if not message:
        return []
    low = message.lower()
    found = []
    for keyword, pid in words.items():
        if re.search(r'\b' + re.escape(keyword) + r'\b', low) and pid not in found:
            found.append(pid)
    return found


# Words that introduce an object as the TOPIC of the sentence rather than the
# one being spoken to (e.g. "how do you feel about computer", "stronger than cup").
_TOPIC_PREPS = ('about', 'than', 'regarding', 'concerning', 'like', 'vs', 'versus')


def addressed_agent(message: str, candidates: list[str], words: Mapping[str, str]) -> str:
    """Among several named objects, pick the one being spoken to (not the topic)."""
    low = (message or '').lower()
    kw_by_id: dict[str, list] = {}
    for kw, pid in words.items():
        if pid in candidates:
            kw_by_id.setdefault(pid, []).append(kw)
    prep_alt = '|'.join(_TOPIC_PREPS)
    addressed = [pid for pid in candidates if not any(
        re.search(r'\b(?:' + prep_alt + r')\s+(?:the\s+|my\s+|your\s+|that\s+)?' + re.escape(kw) + r'\b', low)
        for kw in kw_by_id.get(pid, []))]
    return addressed[0] if addressed else candidates[0]


def group_speaker_order(message: str, words: Mapping[str, str], members: Sequence[str], known: Sequence[str],
                        rng: random.Random) -> list[str]:
    """Who answers a message in the group chat.

    @mentions are honored as they are (all of them). Otherwise, if the text names one object it
    answers; if it names several, only the one being spoken to replies (the others are the topic).
    With no name, a random agent that is on the desk answers."""
    speakers = [p for p in parse_mentions(message, words) if p in known]
    if speakers:
        return speakers
    named = [p for p in detect_named(message, words) if p in known]
    if len(named) == 1:
        return named
    if len(named) > 1:
        return [addressed_agent(message, named, words)]
    return [rng.choice(list(members))] if members else []


# ── replies ──────────────────────────────────────────────────────────
def looks_cut_off(text: str, min_words: int) -> bool:
    """Too short, or ends mid-sentence ("...move it to the")."""
    return len(text.split()) < min_words or text.rstrip().endswith(_TRAILING_WORDS)


def single_speaker_text(text: str, agent: AgentProfile, all_names: Mapping[str, str], max_chars: int) -> str:
    """Keep one object per AI reply: drop lines the AI wrote for other objects ("Computer: ...").
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
    # If everything was stripped but the original had content, keep the original without a leading
    # name (common with local models).
    if not cleaned and text.strip():
        cleaned = re.sub(r'^[A-Za-z][A-Za-z ]{0,30}(?:\s*\(me\))?\s*:\s*', '', text.strip(), count=1)
    return cleaned[:max_chars]


_UNSUPPORTED_DANGER_RE = re.compile(
    r'\b(crack(?:ing|ed)?|shatter(?:ing|ed)?|crush(?:ing|ed)?|'
    r'break(?:ing)?|burn(?:ing|ed)?|fire|explode|destroy(?:ed)?|'
    r'at any moment|about to (?:break|fall|shatter|explode))\b',
    re.I,
)
_HAZARD_RE = re.compile(r'\b(too close|spill|danger|hazard|risk|edge|fall(?:ing)? off)\b', re.I)


def history_blob(history: Iterable[Any] | None) -> str:
    return ' '.join(str(h.get('text', '')) for h in (history or []) if isinstance(h, dict))


def response_violates_evidence(text: str, context: Mapping[str, Any], user_message: str = '',
                               history: Iterable[Any] | None = None, strict: bool = True) -> bool:
    """Reject dramatic or hazard claims that nothing live (or the user) supports.
    With strict=False only extreme unsupported danger words are blocked."""
    if not text:
        return False
    evidence = ' '.join([context.get('summary', ''), user_message or '', history_blob(history)]).lower()
    low = text.lower()
    if _UNSUPPORTED_DANGER_RE.search(low) and not _UNSUPPORTED_DANGER_RE.search(evidence):
        return True
    said_no_danger = re.search(r'\b(no|not|nothing|without)\b.{0,24}\b(danger|risk|hazard|too close|spill)\b', low)
    return bool(strict and _HAZARD_RE.search(low) and not context.get('dangers') and not said_no_danger)


# ── chat history from the app ────────────────────────────────────────
def sanitize_chat_history(raw: Any, chat: ChatSettings) -> list[dict]:
    """Earlier turns sent by the app (1-on-1 or group) → [{role, text, name?}, ...]."""
    if not raw or not isinstance(raw, list):
        return []
    out = []
    for item in _last(raw, chat.history_from_app):
        if not isinstance(item, dict):
            continue
        role = (item.get('role') or '').strip().lower()
        if role not in ('user', 'assistant'):
            continue
        text = item.get('text', '')
        if not isinstance(text, str):
            text = str(text)
        text = text.strip()[:chat.message_max_chars]
        if not text:
            continue
        d = {'role': role, 'text': text}
        name = (item.get('name') or '').strip()[:chat.name_max_chars]
        if name:
            d['name'] = name
        out.append(d)
    return out


def budget_history(history: list[dict], verbatim_turns: int, chat: ChatSettings, local: bool) -> tuple[list, str]:
    """Split the history into (recent turns sent word for word, a one-line recap of older ones).

    The recent turns go to the AI as real messages so the reply stays tied to the latest message;
    anything older is squeezed into a short recap line. A local AI (Ollama) gets fewer turns."""
    k = chat.ollama_history_turns if local else verbatim_turns
    if not history:
        return [], ''
    if len(history) <= k:
        return history, ''
    older, recent = (history[:-k], history[-k:]) if k > 0 else (history, [])
    bits = []
    for turn in _last(older, chat.summary_turns):
        who = 'User' if turn.get('role') == 'user' else (turn.get('name') or 'an object')
        snippet = ' '.join(str(turn.get('text', '')).split())
        if len(snippet) > chat.summary_chars:
            snippet = snippet[:chat.summary_chars].rstrip() + '…'
        if snippet:
            bits.append(f'{who}: "{snippet}"')
    return recent, ' | '.join(bits)


# ── the live desk ────────────────────────────────────────────────────
_PUSHBACK = ('nothing next', 'nothing nect', 'not next to you', 'not a cup', "isn't a cup", 'isnt a cup',
             "that's not", 'thats not', 'that is not', 'there is nothing', "there's nothing",
             'no cup', 'wrong', 'not there', "don't see", 'dont see', 'not next', 'makes no sense')


def build_live_context(desk: DeskState, *, profiles: Profiles, members: Sequence[str],
                       habits_today: Mapping[str, Any], now: float, camera_on: bool, chat: ChatSettings,
                       user_message: str = '') -> dict[str, Any]:
    """Real-time desk facts for the AI (and /api/context): summary text, detected agents, dangers,
    safety state, and the status of objects @mentioned in `user_message`."""
    lines: list[str] = []
    detected_info: list[dict] = []
    mentioned: list[dict] = []
    with desk.lock:
        objects = dict(desk.objects)
        dangers = list(desk.safety_dangers)
        safety_state = desk.safety_state
        first_seen = dict(desk.object_first_seen)
        last_seen = dict(desk.object_last_seen)
        danger_since = dict(desk.danger_active_since)
        recent = _last(desk.recent_events, chat.recent_events_kept)
        rel_result = desk.rel_result
        vision_seen = sorted(desk.lost_last_vlm_visible)
        session_start = desk.session_start
        seen_labels = list(desk.seen_labels)
    others = [label for label in seen_labels if not profiles.is_ignored(label)]

    if not camera_on:
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
    extra = [label for label in others
             if not any((objects.get(m) or {}).get('class_name') == label for m in members)]
    if extra:
        lines.append(f'ALSO IN THE PICTURE (not chatting): {", ".join(sorted(extra)[:chat.other_objects_shown])}')

    risk_level = rel_result.get('risk_level', 0) if rel_result else 0
    if dangers:
        lines.append(f'\nACTIVE DANGERS (risk level {risk_level}/4, state: {safety_state}):')
        for d in dangers:
            key = frozenset(d['pair'])
            duration = format_duration(now - danger_since[key]) if key in danger_since else 'just now'
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
        for ev in _last(recent, chat.recent_events_shown):
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
        for pid in parse_mentions(user_message, mention_words(profiles.all())):
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
