"""
What the desk looks like right now:

    GET  /api/live         everything the desk cam panel needs, in one request (the app asks every 1.5 s)
    GET  /api/status       a short summary
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from desktalk.bootstrap import DeskTalk
from desktalk.web.deps import Desk, FromHere, ShownLinks, camera_index, uptime_sec

router = APIRouter()


def camera_info(desk: DeskTalk) -> dict[str, Any]:
    source = desk.source
    out = {'source': desk.source_kind, 'connected': desk.desk_status.camera_connected(), 'fps': source.fps,
           'frames_received': getattr(source, 'pictures_received', None),
           'yolo_ms': round(desk.producer.last_detect_ms or 0), 'model': desk.detector.label}
    if desk.pi_source is not None:
        out['pi'] = desk.pi_source.pi_info()
    return out


def detections(desk: DeskTalk) -> dict[str, Any]:
    d = desk.desk
    with d.lock:
        width, height = d.frame_w, d.frame_h
        objects = dict(d.objects)
        seen = dict(d.seen_labels)
        relationships = (d.rel_result or {}).get('relationships') or []
        vision_scan = sorted(d.lost_last_vlm_visible)
        light = dict(d.light)
    habit_check, habit_check_at = desk.vision_habits.latest()
    agents = {}
    for pid, box in objects.items():
        profile = desk.profiles.get(pid)
        if profile:
            agents[pid] = {'name': profile.name, 'icon': profile.icon, 'class': box['class_name'],
                           'confidence': round(box['conf'], 2), 'center': [round(c) for c in box['center']],
                           'bbox': [round(float(x)) for x in box['bbox']], 'cam': box.get('cam', 0)}
    agent_labels = {box['class_name'] for box in objects.values()}
    members = set(desk.roster.member_ids())
    for label in seen:              # e.g. a mouse is part of Computer
        profile = desk.profiles.for_label(label)
        if profile and profile.id in members:
            agent_labels.add(label)
    lost_labels = set().union(*(item['labels'] for item in desk.lost_items.items.values()))
    extras = sorted(({'yolo_class': label, 'confidence': round(box['conf'], 2),
                      'bbox': [round(float(x)) for x in box['bbox']], 'highlight': label in lost_labels,
                      'ignored': desk.profiles.is_ignored(label)}
                     for label, box in seen.items() if label not in agent_labels), key=lambda x: -x['confidence'])
    edges = [{'from': r.get('intruder'), 'to': r.get('target'), 'kind': 'danger_zone', 'label': r.get('zone_label', '')}
             for r in relationships if r.get('type') == 'danger_zone']
    return {'objects': agents, 'extras': extras, 'timestamp': desk.clock.now(), 'frame': {'w': width, 'h': height},
            'relationship_edges': edges, 'vlm_habit': habit_check, 'vlm_habit_ts': habit_check_at,
            'lost_vlm_labels': vision_scan, 'light': light}


def safety(desk: DeskTalk) -> dict[str, Any]:
    d = desk.desk
    with d.lock:
        state, dangers, result = d.safety_state, list(d.safety_dangers), d.rel_result or {}
    return {'state': state, 'dangers': dangers, 'rules_loaded': desk.safety_rules.loaded,
            'risk_level': int(result.get('risk_level', 0)), 'explanations': list(result.get('explanations') or []),
            'risk_events': list(result.get('risk_events') or []),
            'relationships': list(result.get('relationships') or [])}


def ai(desk: DeskTalk) -> dict[str, Any]:
    return {'ready': desk.model.ready, 'label': desk.model.label, 'problem': desk.model.problem()}


@router.get('/api/live')
def live(request: Request, desk: Desk, links: ShownLinks, from_here: FromHere) -> dict[str, Any]:
    phone = desk.phone_source
    out = {'phone_mode': phone is not None, 'camera_source': desk.source_kind,
           'camera': desk.desk_status.camera_connected(), 'camera_index': camera_index(desk),
           'camera_info': camera_info(desk), 'detections': detections(desk), 'safety': safety(desk),
           'camera_issues': desk.camera_health.issues(), 'agents': desk.roster.snapshot(), 'ai': ai(desk)}
    if phone is not None:
        wants_qr = bool(request.query_params.get('qr'))
        out['phone'] = {'fps': phone.fps, 'url': links.phone_url if from_here else '',
                        'qr_svg': links.phone_qr_svg if from_here and wants_qr else None}
    if desk.pi_source is not None and from_here:
        out['pi_command'] = links.pi_command
    return out


@router.get('/api/status')
def status(desk: Desk) -> dict[str, Any]:
    connected = desk.desk_status.camera_connected()
    with desk.desk.lock:
        state, result = desk.desk.safety_state, desk.desk.rel_result or {}
    return {'status': 'running', 'camera': connected, 'camera_source': desk.source_kind,
            'phone_mode': desk.phone_source is not None, 'camera_indices': [camera_index(desk)] if connected else [],
            'llm': desk.model.ready, 'llm_label': desk.model.label, 'agents': desk.roster.member_ids(),
            'safety_state': state, 'safety_rules': desk.safety_rules.loaded,
            'risk_level': result.get('risk_level', 0), 'uptime_sec': uptime_sec(desk)}

