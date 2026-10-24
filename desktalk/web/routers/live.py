"""
What the desk looks like right now:

    GET  /api/live         everything the desk cam panel needs, in one request (the app asks every 1.5 s)
    GET  /api/status       a short summary
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from desktalk.bootstrap import DeskTalk
from desktalk.web.deps import App, FromHere, ShownLinks, camera_index, uptime_sec

router = APIRouter()


def camera_info(app: DeskTalk) -> dict[str, Any]:
    source = app.source
    out = {'source': app.source_kind, 'connected': app.desk_status.camera_connected(), 'fps': source.fps,
           'frames_received': getattr(source, 'pictures_received', None),
           'yolo_ms': round(app.producer.last_detect_ms or 0), 'model': app.detector.label}
    if app.pi is not None:
        out['pi'] = app.pi.pi_info()
    return out


def detections(app: DeskTalk) -> dict[str, Any]:
    # Each part is its own read on purpose (the desk has no one call for all of them), so when a picture lands
    # half-way through, one reply can mix it with the one before: the size, boxes, light and scan words may be
    # one picture apart. The page asks again in a moment.
    size = app.live_pictures.size()
    on_desk = app.scene.members()
    seen = app.scene.seen()
    relationships = (app.safety_board.now().result or {}).get('relationships') or []
    vision_scan = sorted(app.scene.scan_labels())
    light = app.live_pictures.newest_light()
    habit_check, habit_check_at = app.vision_habits.latest()
    agents = {}
    for pid, box in on_desk.items():
        profile = app.profiles.get(pid)
        if profile:
            agents[pid] = {'name': profile.name, 'icon': profile.icon, 'class': box.label,
                           'confidence': round(box.confidence, 2), 'center': [round(c) for c in box.center],
                           'bbox': [round(float(x)) for x in box.box], 'cam': box.cam}
    agent_labels = {box.label for box in on_desk.values()}
    members = set(app.roster.member_ids())
    for label in seen:              # e.g. a mouse is part of Computer
        profile = app.profiles.for_label(label)
        if profile and profile.id in members:
            agent_labels.add(label)
    lost_labels = set().union(*(item['labels'] for item in app.lost_items.items.values()))
    extras = sorted(({'yolo_class': label, 'confidence': round(box.confidence, 2),
                      'bbox': [round(float(x)) for x in box.box], 'highlight': label in lost_labels,
                      'ignored': app.profiles.is_ignored(label)}
                     for label, box in seen.items() if label not in agent_labels), key=lambda x: -x['confidence'])
    edges = [{'from': r.get('intruder'), 'to': r.get('target'), 'kind': 'danger_zone', 'label': r.get('zone_label', '')}
             for r in relationships if r.get('type') == 'danger_zone']
    return {'objects': agents, 'extras': extras, 'timestamp': app.clock.now(),
            'frame': {'w': size.width, 'h': size.height}, 'relationship_edges': edges, 'vlm_habit': habit_check,
            'vlm_habit_ts': habit_check_at, 'lost_vlm_labels': vision_scan, 'light': light.to_dict() if light else {}}


def safety(app: DeskTalk) -> dict[str, Any]:
    now = app.safety_board.now()
    result = now.result or {}
    return {'state': now.state, 'dangers': [d.to_dict() for d in now.dangers], 'rules_loaded': app.safety_rules.loaded,
            'risk_level': int(result.get('risk_level', 0)), 'explanations': list(result.get('explanations') or []),
            'risk_events': list(result.get('risk_events') or []),
            'relationships': list(result.get('relationships') or [])}


def ai(app: DeskTalk) -> dict[str, Any]:
    return {'ready': app.model.ready, 'label': app.model.label, 'problem': app.model.problem()}


@router.get('/api/live')
def live(request: Request, app: App, links: ShownLinks, from_here: FromHere) -> dict[str, Any]:
    phone = app.phone
    out = {'phone_mode': phone is not None, 'camera_source': app.source_kind,
           'camera': app.desk_status.camera_connected(), 'camera_index': camera_index(app),
           'camera_info': camera_info(app), 'detections': detections(app), 'safety': safety(app),
           'camera_issues': app.camera_health.issues(), 'agents': app.roster.snapshot(), 'ai': ai(app)}
    if phone is not None:
        wants_qr = bool(request.query_params.get('qr'))
        out['phone'] = {'fps': app.source.fps, 'url': links.phone_url if from_here else '',
                        'qr_svg': links.phone_qr_svg if from_here and wants_qr else None}
    if app.pi is not None and from_here:
        out['pi_command'] = links.pi_command
    return out


@router.get('/api/status')
def status(app: App) -> dict[str, Any]:
    connected = app.desk_status.camera_connected()
    now = app.safety_board.now()
    state, result = now.state, now.result or {}
    return {'status': 'running', 'camera': connected, 'camera_source': app.source_kind,
            'phone_mode': app.phone is not None, 'camera_indices': [camera_index(app)] if connected else [],
            'llm': app.model.ready, 'llm_label': app.model.label, 'agents': app.roster.member_ids(),
            'safety_state': state, 'safety_rules': app.safety_rules.loaded,
            'risk_level': result.get('risk_level', 0), 'uptime_sec': uptime_sec(app)}

