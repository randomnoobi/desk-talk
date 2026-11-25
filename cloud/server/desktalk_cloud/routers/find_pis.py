"""The website's "Find Pis on my network": GET /api/pis/nearby looks on the home network around PUBLIC_URL for Desk Talk
Pis, and POST /api/pis/link makes a one-time code (as "Add a Pi" does) and offers it to one of them, which then links
itself by using the code, as it would with the command. Both need the person's login.

Only the addresses of the one private network around PUBLIC_URL's address are ever knocked on or offered a code
(pi_network.py), and one search runs at a time on the whole server. The code never goes in a reply or a log."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from desktalk_cloud.deps import current_user, parts_of, read_json
from desktalk_cloud.domain import User
from desktalk_cloud.pi_network import addresses_to_search, in_search_network
from desktalk_cloud.ports import PiOfferFailed
from desktalk_cloud.routers.auth import needs_json, public_address
from desktalk_cloud.routers.site import make_link_code

__all__ = ['router']

router = APIRouter()
log = logging.getLogger(__name__)


@router.get('/api/pis/nearby')
async def nearby(request: Request, _user: Annotated[User, Depends(current_user)]) -> dict[str, Any]:
    parts = parts_of(request)
    config, messages = parts.config, parts.config.messages
    addresses = addresses_to_search(public_address(config), config.find_pis.network_prefix)
    if addresses is None:
        raise HTTPException(409, messages.no_network_to_search)
    if parts.pi_search.locked():                # checked and taken with no wait between, so only one runs
        raise HTTPException(429, messages.pi_search_running)
    async with parts.pi_search:
        found = await parts.pi_finder.find(addresses)
    return {'pis': [{'address': pi.address, 'name': pi.name, 'linked': pi.linked} for pi in found]}


@router.post('/api/pis/link')
async def link(request: Request, user: Annotated[User, Depends(current_user)],
               _json: Annotated[None, Depends(needs_json)]) -> dict[str, bool]:
    parts = parts_of(request)
    config, messages = parts.config, parts.config.messages
    body = await read_json(request, config.limits.claim_body_kb)
    address = body.get('address') if isinstance(body, dict) else None
    zone = body.get('time_zone') if isinstance(body, dict) else None
    if not isinstance(address, str) or not isinstance(zone, str):
        raise HTTPException(400, messages.bad_body)
    server_url = public_address(config)
    if not in_search_network(address, server_url, config.find_pis.network_prefix):
        raise HTTPException(400, messages.pi_not_in_network)
    code, _expires_at = await make_link_code(request, user, zone)
    try:
        await parts.pi_finder.offer(address, server_url, code)
    except PiOfferFailed as failure:            # the code just runs out: nothing was linked
        log.info('Person %s offered a link to the Pi at %s, and it said %s', user.id, address, failure.kind)
        raise HTTPException(*_refusal(request, failure)) from None
    log.info('Person %s linked the Pi at %s from the website', user.id, address)
    return {'linked': True}


def _refusal(request: Request, failure: PiOfferFailed) -> tuple[int, str]:
    messages = parts_of(request).config.messages
    if failure.kind == 'no_answer':
        return 502, messages.pi_no_answer
    status = 409 if failure.kind == 'already_linked' else 502
    if failure.reason is not None:
        return status, messages.pi_refused.format(reason=failure.reason)
    return status, messages.pi_already_linked if status == 409 else messages.pi_refused_no_reason
