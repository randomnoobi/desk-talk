"""create_app: builds the FastAPI app from the parts and the routers. bootstrap (Task 9) is the only place that
knows the real parts."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from fastapi import APIRouter, FastAPI
from starlette.middleware.sessions import SessionMiddleware

from desktalk_cloud.config import CloudConfig
from desktalk_cloud.ports import ChatForwarder, Clock, GoogleLogin, Store
from desktalk_cloud.rate_limit import RateLimiter

__all__ = ['CloudParts', 'create_app']


@dataclass(frozen=True, kw_only=True)
class CloudParts:
    """What the routes use. `google` and `chat_forwarder` are None when they aren't set up. `claim_limiter` counts
    tries to use a code per address (and admin login tries); `chat_limiter` counts chat requests per Pi."""

    config: CloudConfig
    store: Store
    clock: Clock
    google: GoogleLogin | None
    chat_forwarder: ChatForwarder | None
    claim_limiter: RateLimiter
    chat_limiter: RateLimiter


def create_app(parts: CloudParts, routers: Sequence[APIRouter]) -> FastAPI:
    server = parts.config.server
    app = FastAPI(title='Desk Talk cloud server', docs_url=None, redoc_url=None, openapi_url=None)
    app.state.parts = parts
    app.add_middleware(SessionMiddleware, secret_key=parts.config.secrets.session_secret.get_secret_value(),
                       session_cookie=server.cookie_name, max_age=server.session_days * 24 * 3600,
                       same_site='lax', https_only=server.https_only)
    for router in routers:
        app.include_router(router)

    @app.get('/healthz')
    def healthz() -> dict[str, bool]:        # for Docker's health check
        return {'ok': True}

    return app
