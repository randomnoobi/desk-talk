"""
GET /api/health: how every part is doing, in one reply.

    jobs        each background job: running / waiting after error / stopped, its failures and last error
    scheduler   the timed jobs (compact and prune the event log, save the habits)
    events      each event consumer: handled, waiting, dropped, errors
    camera      which camera, is it sending, how fast, pictures received
    pi          what the Pi said lately: upload format, its detector (ok / busy / off and why), detect time,
                model, how far its clock is off (null when the camera isn't the Pi)
    detection   detection on this computer: pictures analysed, how long the last one took, what's wrong
    ai          the AI: ready, which one, the current problem
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from desktalk.web.deps import Desk, uptime_sec
from desktalk.web.routers.live import ai

router = APIRouter()


@router.get('/api/health')
def health(desk: Desk) -> dict[str, Any]:
    source = desk.source
    producer = desk.producer
    return {
        'uptime_sec': uptime_sec(desk),
        'jobs': desk.supervisor.status(),
        'scheduler': desk.scheduler.status(),
        'events': desk.bus.stats(),
        'camera': {'source': desk.source_kind, 'connected': desk.desk_status.camera_connected(), 'fps': source.fps,
                   'pictures_received': getattr(source, 'pictures_received', None)},
        'pi': desk.pi_source.status() if desk.pi_source is not None else None,
        'detection': {'pictures_analysed': producer.pictures_analysed, 'detect_ms': producer.last_detect_ms,
                      'problem': producer.problem, 'model': desk.detector.label},
        'ai': ai(desk),
    }
