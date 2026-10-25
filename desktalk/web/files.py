"""
The pages and their files (web_app/, phone_camera/), as the Flask server sent them: with
Cache-Control: no-cache (the browser checks for a newer one each time), and "; charset=utf-8" on text,
scripts and SVG drawings.

    app.mount('/', PageFiles(directory=folder))     # every file in a folder
    page(folder / 'index.html')                     # one file
    app.mount('/dev', BuiltPage(folder, not_built='...'))   # a page a build step makes (dev_ui/ → npm run build)
"""

from __future__ import annotations

import html
import mimetypes
import os
from pathlib import Path

from starlette.responses import FileResponse, HTMLResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send

NO_CACHE = 'no-cache'
INDEX = 'index.html'
FRONT_PAGE = ('', '.', INDEX)       # what StaticFiles calls the folder's own address (/dev/)
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


class BuiltPage(PageFiles):
    """A page that a build step makes, e.g. the developer page (dev_ui/ → npm run build → dev_ui/dist/).
    Until its index.html is there, its address answers `not_built` (how to build it) and every other file a 404.
    It looks each time, so a page built while the server runs shows up without a restart."""

    def __init__(self, directory: Path, *, not_built: str):
        super().__init__(directory=directory, html=True, check_dir=False)
        self._folder = Path(directory)
        self._not_built = not_built

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (self._folder / INDEX).is_file():
            await super().__call__(scope, receive, send)
            return
        status = 200 if self.get_path(scope) in FRONT_PAGE else 404
        text = f'<!DOCTYPE html>\n<meta charset="utf-8">\n<p>{html.escape(self._not_built)}</p>\n'
        await HTMLResponse(text, status_code=status, headers={'Cache-Control': NO_CACHE})(scope, receive, send)
