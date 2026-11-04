"""The website's data, for the person in the login cookie: who they are, their Pis (and a code to link one), and their
weekly reports. A person only reaches their own things; anything else is a 404, the same as for something that
isn't there. A Pi's token does not open these routes. Deleting a person removes everything kept for them."""

from __future__ import annotations

import shlex
from string import Formatter
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from desktalk_cloud.config import CloudConfig, ConfigError
from desktalk_cloud.deps import current_user, parts_of, read_json
from desktalk_cloud.domain import Device, Report, User, is_clean_text
from desktalk_cloud.routers.auth import needs_json, public_address
from desktalk_cloud.tokens import hash_secret, new_code

__all__ = ['check_site_words', 'router']

router = APIRouter()

SITE_WORDS = ('setup_command', 'needs_json')                       # in cloud/prompts.yaml → site
SETUP_BLANKS = ('server_url', 'code')


def check_site_words(config: CloudConfig) -> None:
    """Stop the start (ConfigError, in plain words) when cloud/prompts.yaml → site is missing a wording or the setup
    command has a blank the code does not fill in, instead of failing on the first use."""
    words = config.prompts.get('site')
    if not isinstance(words, dict):
        raise ConfigError("prompts.yaml: 'site' is missing (it holds the setup command and the JSON-only wording)")
    for key in SITE_WORDS:
        if not isinstance(words.get(key), str) or not words[key].strip():
            raise ConfigError(f"prompts.yaml: site → {key} is missing or is not text")
    try:
        blanks = {name for _, name, _, _ in Formatter().parse(words['setup_command']) if name is not None}
    except ValueError:
        blanks = set()
    if blanks != set(SETUP_BLANKS):
        raise ConfigError("prompts.yaml: site → setup_command must use exactly the blanks {server_url} and {code} "
                          "(and braces only for those)")


def _device_row(device: Device) -> dict[str, Any]:
    return {'id': device.id, 'name': device.name, 'time_zone': device.time_zone, 'created_at': device.created_at,
            'last_seen_at': device.last_seen_at, 'caught_up_at': device.caught_up_at,
            'removed': device.removed_at is not None}


def _report_row(report: Report) -> dict[str, Any]:
    return {'id': report.id, 'device_id': report.device_id, 'week_start': report.week_start, 'status': report.status,
            'model': report.model, 'error': report.error, 'created_at': report.created_at}


def _my_device(request: Request, user: User, device_id: str) -> Device:
    """This person's Pi (a removed one too); 404 for anyone else's or one that isn't there."""
    parts = parts_of(request)
    device = parts.store.get_device(device_id) if is_clean_text(device_id) else None
    if device is None or device.user_id != user.id:
        raise HTTPException(404, parts.config.messages.not_found)
    return device


def my_device(request: Request, device_id: str, user: Annotated[User, Depends(current_user)]) -> Device:
    return _my_device(request, user, device_id)


def _time_zone_ok(name: str) -> bool:
    if not is_clean_text(name):
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):        # not a name, an odd path, or a folder
        return False
    return True


def _clean_name(request: Request, body: Any) -> str:
    parts = parts_of(request)
    max_chars = parts.config.pairing.name_max_chars
    name = body.get('name') if isinstance(body, dict) else None
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > max_chars or not is_clean_text(name):
        raise HTTPException(400, parts.config.messages.bad_name.format(max=max_chars))
    return name.strip()


# ── you ──────────────────────────────────────────────────────────────

@router.get('/api/me')
def me(user: Annotated[User, Depends(current_user)]) -> dict[str, Any]:
    return {'id': user.id, 'email': user.email, 'name': user.name, 'created_at': user.created_at}


@router.delete('/api/me')
def delete_me(request: Request, user: Annotated[User, Depends(current_user)]) -> dict[str, bool]:
    parts_of(request).store.delete_user(user.id)
    request.session.clear()
    return {'ok': True}


# ── Pis ──────────────────────────────────────────────────────────────

@router.get('/api/devices')
def devices(request: Request, user: Annotated[User, Depends(current_user)]) -> list[dict[str, Any]]:
    return [_device_row(d) for d in parts_of(request).store.list_devices(user.id)]


@router.post('/api/devices/code')
async def make_code(request: Request, user: Annotated[User, Depends(current_user)],
                    _json: Annotated[None, Depends(needs_json)]) -> dict[str, Any]:
    """A one-time code, in the browser's time zone. With a `device_id` it links that same Pi again."""
    parts = parts_of(request)
    config, messages = parts.config, parts.config.messages
    body = await read_json(request, config.limits.claim_body_kb)
    if not isinstance(body, dict):
        raise HTTPException(400, messages.bad_body)
    zone, device_id = body.get('time_zone'), body.get('device_id')
    if not isinstance(zone, str) or not (device_id is None or isinstance(device_id, str)):
        raise HTTPException(400, messages.bad_body)
    if not _time_zone_ok(zone):
        raise HTTPException(400, messages.bad_time_zone)
    if device_id is not None:
        await run_in_threadpool(_my_device, request, user, device_id)
    code = new_code()
    expires_at = parts.clock.now() + config.pairing.code_valid_min * 60
    try:
        await run_in_threadpool(parts.store.add_code, hash_secret(code), user.id, device_id, zone, expires_at)
    except LookupError:         # the Pi or the person went away since we looked
        raise HTTPException(404, messages.not_found) from None
    server_url = public_address(config)
    command = config.prompts['site']['setup_command'].format(server_url=shlex.quote(server_url), code=code)
    return {'code': code, 'expires_at': expires_at, 'server_url': server_url, 'command': command}


@router.patch('/api/devices/{device_id}')
async def rename(request: Request, device: Annotated[Device, Depends(my_device)],
                 _json: Annotated[None, Depends(needs_json)]) -> dict[str, Any]:
    parts = parts_of(request)
    name = _clean_name(request, await read_json(request, parts.config.limits.claim_body_kb))
    renamed = await run_in_threadpool(parts.store.rename_device, device.id, name)
    if renamed is None:
        raise HTTPException(404, parts.config.messages.not_found)
    return _device_row(renamed)


@router.delete('/api/devices/{device_id}')
def remove(request: Request, device: Annotated[Device, Depends(my_device)]) -> dict[str, Any]:
    """Cancel the Pi's token and keep its history."""
    parts = parts_of(request)
    parts.store.remove_device(device.id, parts.clock.now())
    found = parts.store.get_device(device.id)
    if found is None:
        raise HTTPException(404, parts.config.messages.not_found)
    return _device_row(found)


@router.delete('/api/devices/{device_id}/data')
def delete_with_data(request: Request, device: Annotated[Device, Depends(my_device)]) -> dict[str, bool]:
    parts = parts_of(request)
    if not parts.store.delete_device(device.id):
        raise HTTPException(404, parts.config.messages.not_found)
    return {'ok': True}


# ── reports ──────────────────────────────────────────────────────────

@router.get('/api/reports')
def reports(request: Request, user: Annotated[User, Depends(current_user)],
            device_id: str | None = None) -> list[dict[str, Any]]:
    """The reports of one of my Pis, or of all of them. Newest week first. No report text: that is /api/reports/{id}."""
    store = parts_of(request).store
    mine = [_my_device(request, user, device_id)] if device_id is not None else store.list_devices(user.id)
    found = [(report, device.name) for device in mine for report in store.list_reports(device.id, with_text=False)]
    found.sort(key=lambda row: (row[0].week_start, row[0].id), reverse=True)
    return [{**_report_row(report), 'device_name': name} for report, name in found]


@router.get('/api/reports/{report_id}')
def report(request: Request, report_id: str, user: Annotated[User, Depends(current_user)]) -> dict[str, Any]:
    parts = parts_of(request)
    found = parts.store.get_report_by_id(report_id) if is_clean_text(report_id) else None
    owner = parts.store.get_device(found.device_id) if found is not None else None
    if found is None or owner is None or owner.user_id != user.id:
        raise HTTPException(404, parts.config.messages.not_found)
    return {**_report_row(found), 'markdown': found.markdown}
