"""
The pages and their files (web_app/, phone_camera/), as the Flask server sent them: with
Cache-Control: no-cache (the browser checks for a newer one each time), and "; charset=utf-8" on text,
scripts and SVG drawings.

    app.mount('/', PageFiles(directory=folder))     # every file in a folder
    page(folder / 'index.html')                     # one file
"""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path

from starlette.responses import FileResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

NO_CACHE = 'no-cache'
UTF8_TYPES = ('application/javascript', 'application/json', 'application/xml')


def media_type(path: str | os.PathLike) -> str:
    kind = mimetypes.guess_type(str(path))[0] or 'application/octet-stream'
    if kind.startswith('text/') or kind.endswith('+xml') or kind in UTF8_TYPES:
        kind += '; charset=utf-8'
    return kind


def page(path: Path) -> FileResponse:
    return FileResponse(path, media_type=media_type(path), headers={'Cache-Control': NO_CACHE})


class PageFiles(StaticFiles):
    """A folder of files for the browser (404 for anything that isn't in it)."""

    def file_response(self, full_path: str | os.PathLike, stat_result: os.stat_result, scope: Scope,
                      status_code: int = 200) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers['Cache-Control'] = NO_CACHE
        if isinstance(response, FileResponse):
            response.headers['Content-Type'] = media_type(full_path)
        return response
