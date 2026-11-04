"""POST /v1/events: a Pi uploads a batch of events with its token."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from desktalk_cloud.deps import device_from_token, parts_of, read_json
from desktalk_cloud.domain import Device
from desktalk_cloud.ingest import ingest, number_reader

router = APIRouter()


@router.post('/v1/events')
async def post_events(request: Request, device: Annotated[Device, Depends(device_from_token)]) -> dict[str, Any]:
    parts = parts_of(request)
    config = parts.config
    body = await read_json(request, config.ingest.max_body_kb, parse_int=number_reader(config.ingest))
    raw = body.get('events') if isinstance(body, dict) else None
    caught_up = body.get('caught_up', False) if isinstance(body, dict) else None
    if not isinstance(raw, list) or not isinstance(caught_up, bool):
        raise HTTPException(400, config.messages.bad_body)
    if len(raw) > config.ingest.max_batch:
        raise HTTPException(413, config.messages.batch_too_big.format(max=config.ingest.max_batch))
    reply = await run_in_threadpool(ingest, parts.store, parts.clock, config.ingest, config.messages, device.id, raw,
                                    caught_up)
    return reply.to_dict()
