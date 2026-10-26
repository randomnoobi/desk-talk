"""
What the desk looks like right now:

    GET  /api/live         what the app page's desk cam panel shows, in one request (the app asks every 1.5 s):
                           the camera, the characters, the other things on the desk, the
                           "Heads up" sentences, camera problems, the AI's state and the Pi / phone links
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
    """The other things on the desk: what the warnings see (the scene's on-desk list) that isn't in the chat, by
    the detector's word, surest first. The chat's members come in `agents`."""
    members = set(app.roster.member_ids())
    others = sorted((box for pid, box in app.scene.on_desk().items() if pid not in members),
                    key=lambda box: -box.confidence)
    return {'extras': [{'yolo_class': box.label, 'ignored': app.profiles.is_ignored(box.label)} for box in others]}


def safety(app: DeskTalk) -> dict[str, Any]:
    """The plain sentences under "Heads up"."""
    result = app.safety_board.now().result or {}
    return {'explanations': list(result.get('explanations') or [])}


def ai(app: DeskTalk) -> dict[str, Any]:
    return {'ready': app.model.ready, 'label': app.model.label, 'problem': app.model.problem()}


@router.get('/api/live')
def live(request: Request, app: App, links: ShownLinks, from_here: FromHere) -> dict[str, Any]:
    """Only what the app page reads (the developer page has its own routes), because it asks every 1.5 s."""
    phone = app.phone
    out = {'camera_source': app.source_kind, 'camera': app.desk_status.camera_connected(),
           'camera_index': camera_index(app), 'detections': detections(app), 'safety': safety(app),
           'camera_issues': app.camera_health.issues(), 'agents': app.roster.snapshot(),
           'ai': {'ready': app.model.ready, 'problem': app.model.problem()}}
    if phone is not None:
        wants_qr = bool(request.query_params.get('qr'))
        out['phone'] = {'url': links.phone_url if from_here else '',
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

