"""
Starts the detector service: python -m desktalk_detector (the Docker image runs this).
The settings come from the environment: see raspberry_pi/detector_service/.env and README.md.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping

import uvicorn

from desktalk_detector.bootstrap import build_app
from desktalk_detector.settings import ServiceSettings, SettingsError

log = logging.getLogger('desktalk_detector')


def main(environ: Mapping[str, str] = os.environ) -> int:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    try:
        settings = ServiceSettings.from_env(environ)
    except SettingsError as exc:
        log.error('cannot start: %s', exc)
        return 2
    # No log line per request: at 3 pictures a second they would fill the Pi's SD card. Problems are still logged.
    uvicorn.run(build_app(settings), host=settings.host, port=settings.port, access_log=False)
    return 0


if __name__ == '__main__':
    sys.exit(main())
