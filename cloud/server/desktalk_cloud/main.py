"""The website's server: `python -m desktalk_cloud.main`, run from the folder that holds settings.yaml and prompts.yaml
(/config in the Docker image). It brings the database up to date, makes the admin, and serves.

There's one process on purpose: the rate limits are counted in its memory.

Addresses: nginx sits in front, so uvicorn takes the client's address from X-Forwarded-For, but only when the request
really comes from nginx: uvicorn reads FORWARDED_ALLOW_IPS itself (compose.yaml sets it to nginx's address; with nothing
set it trusts only 127.0.0.1). There's no access log, because Google's code and state arrive in an address."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import uvicorn

from desktalk_cloud.admins import ensure_admin
from desktalk_cloud.bootstrap import build, check_settings, database_url, set_up_logging
from desktalk_cloud.config import ConfigError, load_config
from desktalk_cloud.store_postgres import run_migrations

log = logging.getLogger(__name__)


def main() -> int:
    set_up_logging()
    try:
        config = load_config(Path.cwd(), os.environ)
        check_settings(config)
        server = build(config)
    except ConfigError as error:
        log.error('%s', error)
        return 1
    try:
        run_migrations(database_url(config))
        ensure_admin(config, server.store)
    except Exception as error:                  # its text can hold what Postgres said: only the kind is logged
        log.error('Could not get the database ready (%s). Is Postgres running?', type(error).__name__)
        server.store.dispose()
        return 1
    uvicorn.run(server.app, host=config.server.host, port=config.server.port, proxy_headers=True, access_log=False,
                log_config=None, server_header=False)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
