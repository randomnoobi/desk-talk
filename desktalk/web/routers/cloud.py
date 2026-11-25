"""
The cloud server link: the Settings page's status line, and the cloud website finding this Pi on the home network
and linking it with one click:

    GET  /api/cloud/status  {linked, stopped, last_sent_ts, waiting}: whether this computer is linked to a cloud
                            server, whether the server said it doesn't know us any more, when something was last
                            sent (null: never) and how many events wait to go. Never the token or the address.
    GET  /api/cloud/hello   {desk_talk: "pi", name, linked}: who this is, for the cloud server looking for Pis.
                            Nothing else (no events, no server address, no device id).
    POST /api/cloud/link    {server, code}: link to the cloud server at `server` with its one-time code. 200
                            {linked: true}; 400 a bad offer or the server didn't take the code; 403 `server` isn't
                            the home network computer asking; 409 linked already, another offer going, or saving
                            is off; 502 the server couldn't be reached. Who asks is the connection's own address,
                            never a header (X-Forwarded-For) anyone could write.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, StrictStr

from desktalk.domain import CloudLinkRefused
from desktalk.web.deps import App

router = APIRouter()

# The status each way the Pi says no to a link offer gets.
REFUSAL_STATUS = {'bad_request': 400, 'refused': 400, 'not_caller': 403, 'already_linked': 409, 'busy': 409,
                  'not_saved': 409, 'unavailable': 502}


class LinkOffer(BaseModel):
    """POST /api/cloud/link"""

    server: StrictStr
    code: StrictStr


@router.get('/api/cloud/status')
def cloud_status(app: App) -> dict[str, Any]:
    return app.cloud.status()


@router.get('/api/cloud/hello')
def cloud_hello(app: App) -> dict[str, Any]:
    hello = app.cloud_linker.hello()
    return {'desk_talk': 'pi', 'name': hello.name, 'linked': hello.linked}


@router.post('/api/cloud/link')
def cloud_link(offer: LinkOffer, request: Request, app: App) -> dict[str, Any]:
    caller = request.client.host if request.client is not None else ''
    try:
        app.cloud_linker.link(offer.server, offer.code, caller)
    except CloudLinkRefused as exc:
        raise HTTPException(REFUSAL_STATUS.get(exc.kind, 400), exc.text) from None
    return {'linked': True}
