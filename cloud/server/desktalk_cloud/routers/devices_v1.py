"""Routes a Pi uses to link itself and the ones that read a Pi: POST /v1/devices/claim, and
GET /v1/devices/{id}/state and /events (with the Pi's token, or the owner's login cookie)."""

from __future__ import annotations

import logging
import math
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from desktalk_cloud.deps import owner_or_device, parts_of, read_json
from desktalk_cloud.domain import Device
from desktalk_cloud.tokens import hash_secret, new_token, normal_code
from desktalk_shared.cloud_api import ClaimReply

router = APIRouter()
log = logging.getLogger(__name__)


def _address(request: Request) -> str:
    return request.client.host if request.client else 'unknown'


@router.post('/v1/devices/claim')
async def claim(request: Request) -> dict[str, Any]:
    parts = parts_of(request)
    config, messages = parts.config, parts.config.messages
    if not parts.claim_limiter.hit(_address(request)):
        log.warning('Too many tries to use a code from %s', _address(request))
        raise HTTPException(429, messages.too_many_tries)
    body = await read_json(request, config.limits.claim_body_kb)
    if not isinstance(body, dict) or not isinstance(body.get('code'), str) or not body['code'].strip():
        raise HTTPException(400, messages.bad_body)
    name = body.get('name', messages.default_pi_name)
    max_chars = config.pairing.name_max_chars
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > max_chars:
        raise HTTPException(400, messages.bad_name.format(max=max_chars))
    token = new_token()
    device = parts.store.claim_code(hash_secret(normal_code(body['code'])), name.strip(), hash_secret(token),
                                    parts.clock.now())
    if device is None:
        raise HTTPException(400, messages.bad_code)          # wrong, used and expired codes all get this
    log.info('Linked Pi %s', device.id)
    return ClaimReply(device_id=device.id, token=token).to_dict()


@router.get('/v1/devices/{device_id}/state')
def state(request: Request, device: Annotated[Device, Depends(owner_or_device)]) -> dict[str, Any]:
    store = parts_of(request).store
    return {
        'device': {'id': device.id, 'name': device.name, 'time_zone': device.time_zone,
                   'removed': device.removed_at is not None},
        'last_seen_at': device.last_seen_at,
        'goals': store.latest_event(device.id, 'goals'),
        'last_camera': store.latest_event(device.id, 'camera'),
        'objects': [{'label': s.label, 'state': s.state, 'since': s.since, 'updated_at': s.updated_at}
                    for s in store.object_states(device.id)],
    }


@router.get('/v1/devices/{device_id}/events')
def events(request: Request, device: Annotated[Device, Depends(owner_or_device)], since: float = 0.0,
           after_id: str | None = None, limit: Annotated[int | None, Query(ge=1)] = None) -> dict[str, Any]:
    parts = parts_of(request)
    settings = parts.config.ingest
    if not math.isfinite(since):
        raise HTTPException(400, parts.config.messages.bad_query)
    count = min(limit or settings.read_default_limit, settings.read_max_limit)
    found = parts.store.events_after(device.id, since, after_id, count)
    last = found[-1] if found else None
    return {'events': found, 'next_since': last['ts'] if last else since,
            'next_after_id': last['id'] if last else after_id}
