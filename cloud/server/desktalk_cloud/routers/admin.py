"""The admin: logging in with the username and password from .env (/auth/admin/login), and a page's worth of facts
about whether things work. The admin sees no events and no report text, only who is linked, when each Pi was last
heard from, and which reports failed. Login tries share the per-address limit of the Pi's codes."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from desktalk_cloud.admins import SESSION_ADMIN, admin_login_ok, admin_set_up, current_admin
from desktalk_cloud.deps import parts_of, read_json
from desktalk_cloud.domain import DONE, NO_DATA, Admin, Device, Report, is_clean_text
from desktalk_cloud.ports import Store
from desktalk_cloud.routers.auth import needs_json

router = APIRouter()
log = logging.getLogger(__name__)


def _address(request: Request) -> str:
    return 'admin:' + (request.client.host if request.client else 'unknown')     # apart from a Pi's tries


@router.post('/auth/admin/login', dependencies=[Depends(needs_json)])
async def admin_login(request: Request) -> dict[str, bool]:
    parts = parts_of(request)
    config, messages = parts.config, parts.config.messages
    if not admin_set_up(config):
        raise HTTPException(404, messages.admin_not_set_up)
    if not parts.claim_limiter.hit(_address(request)):
        log.warning('Too many admin login tries from %s', _address(request).removeprefix('admin:'))
        raise HTTPException(429, messages.too_many_tries)
    body = await read_json(request, config.limits.claim_body_kb)
    username = body.get('username') if isinstance(body, dict) else None
    password = body.get('password') if isinstance(body, dict) else None
    if (not isinstance(username, str) or not isinstance(password, str)
            or not is_clean_text(username) or not is_clean_text(password)):
        raise HTTPException(400, messages.bad_body)
    if not await run_in_threadpool(admin_login_ok, config, parts.store, username, password):
        log.warning('Wrong admin login from %s', _address(request).removeprefix('admin:'))
        raise HTTPException(401, messages.admin_login_failed)
    request.session[SESSION_ADMIN] = username
    log.info('The admin logged in')
    return {'ok': True}


def _report_row(report: Report) -> dict[str, Any]:
    return {'id': report.id, 'device_id': report.device_id, 'week_start': report.week_start, 'status': report.status,
            'model': report.model, 'error': report.error, 'tries': report.tries, 'created_at': report.created_at}


def _device_row(device: Device) -> dict[str, Any]:
    return {'id': device.id, 'user_id': device.user_id, 'name': device.name, 'time_zone': device.time_zone,
            'created_at': device.created_at, 'last_seen_at': device.last_seen_at,
            'caught_up_at': device.caught_up_at, 'removed': device.removed_at is not None}


def _latest_finished(store: Store, devices: list[Device]) -> list[dict[str, Any]]:
    """Each Pi's newest report that was written (done, or no data that week), without its text, so the admin can
    set it to run again after late data. (A failed one is under failed_reports.)"""
    found = []
    for device in devices:
        finished = [r for r in store.list_reports(device.id, with_text=False) if r.status in (DONE, NO_DATA)]
        if finished:
            found.append(finished[0])                       # newest week first
    found.sort(key=lambda r: (r.week_start, r.id), reverse=True)
    return [_report_row(r) for r in found]


@router.get('/api/admin/overview')
def overview(request: Request, _admin: Annotated[Admin, Depends(current_admin)]) -> dict[str, Any]:
    store = parts_of(request).store
    devices = store.list_all_devices()
    counts: dict[str, int] = {}
    for device in devices:
        counts[device.user_id] = counts.get(device.user_id, 0) + 1
    return {
        'users': [{'id': u.id, 'email': u.email, 'name': u.name, 'created_at': u.created_at,
                   'device_count': counts.get(u.id, 0)} for u in store.list_users()],
        'devices': [_device_row(d) for d in devices],
        'failed_reports': [_report_row(r) for r in store.list_failed_reports()],
        'recent_reports': _latest_finished(store, devices),
    }


@router.post('/api/admin/reports/{report_id}/rerun')
def rerun(request: Request, report_id: str, _admin: Annotated[Admin, Depends(current_admin)],
          _json: Annotated[None, Depends(needs_json)]) -> dict[str, Any]:
    parts = parts_of(request)
    found = parts.store.get_report_by_id(report_id) if is_clean_text(report_id) else None
    device = parts.store.get_device(found.device_id) if found is not None else None
    if device is not None and device.removed_at is not None:
        raise HTTPException(409, parts.config.messages.report_pi_removed)
    fresh = parts.store.reset_report(report_id, parts.clock.now()) if device is not None else None
    if fresh is None:
        raise HTTPException(404, parts.config.messages.not_found)
    log.info('The admin set report %s to be written again', fresh.id)
    return _report_row(fresh)
