"""
The cloud server link: the Settings page's status line, and the cloud website finding this Pi on the home network
and linking it with one click:

    GET  /api/cloud/status  {linked, stopped, last_sent_ts, waiting}: whether this computer is linked to a cloud
                            server, whether the server said it doesn't know us any more, when something was last
                            sent (null: never) and how many events wait to go. Never the token or the address.
    GET  /api/cloud/hello   {desk_talk: "pi", name, linked}: who this is, for the cloud website looking for Pis from
                            the browser, so any site's page may read it (Access-Control-Allow-Origin: *).
                            Nothing else (no events, no server address, no device id).
    POST /api/cloud/link    {server, code}: link to the cloud server at `server` with its one-time code. 200
                            {linked: true}, or {error, kind}: `error` the plain words, `kind` why, for the cloud
                            server to pick its own words. 400 bad_request (not a web address and a code, or not
                            JSON at all) or refused (the server didn't take the code); 403 not_caller (`server`
                            isn't the home network computer asking); 409 already_linked, busy (another offer
                            going) or not_saved (saving is off); 502 unavailable (the server couldn't be
                            reached). Who asks is the connection's own address, never a header (X-Forwarded-For)
                            anyone could write (desktalk/web/serve.py turns uvicorn's proxy headers off).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StrictStr, ValidationError
from starlette.concurrency import run_in_threadpool

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


# The cloud website looks for Pis from the person's browser (a server in Docker on a Mac can't knock on a whole home
# network), so a page from another site may read this one answer. It holds nothing but the name and "linked", and no
# cookies go with it. Every other route stays this server's own.
ANY_SITE = {'Access-Control-Allow-Origin': '*'}


@router.get('/api/cloud/hello')
def cloud_hello(app: App, response: Response) -> dict[str, Any]:
    hello = app.cloud_linker.hello()
    response.headers.update(ANY_SITE)
    return {'desk_talk': 'pi', 'name': hello.name, 'linked': hello.linked}


def _refused(exc: CloudLinkRefused) -> JSONResponse:
    return JSONResponse({'error': exc.text, 'kind': exc.kind}, status_code=REFUSAL_STATUS.get(exc.kind, 400))


@router.post('/api/cloud/link', response_model=None)
async def cloud_link(request: Request, app: App) -> dict[str, Any] | JSONResponse:
    # The body is read here, not by FastAPI, so a body that isn't an offer (or isn't JSON) says bad_request too.
    try:
        offer = LinkOffer.model_validate_json(await request.body())
    except ValidationError:
        return _refused(CloudLinkRefused('bad_request', app.config.prompts.cloud.offer_bad))
    caller = request.client.host if request.client is not None else ''
    try:      # claiming waits on the cloud server: not on the event loop
        await run_in_threadpool(app.cloud_linker.link, offer.server, offer.code, caller)
    except CloudLinkRefused as exc:
        return _refused(exc)
    return {'linked': True}
