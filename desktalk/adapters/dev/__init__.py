"""The developer page's tools (http://localhost:5000/dev/): the test runner, the read-only database browser
and the data folder viewer.

The classes are only imported when first asked for: every pytest the runner starts loads the plugin in
pytest_report.py from this folder, which runs this file first, and it must not bring in the settings or pydantic
there (that would slow every run down, and happen before the coverage check starts counting)."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from desktalk.adapters.dev.data_files import DataFolder
    from desktalk.adapters.dev.sqlite_browser import SqliteBrowser
    from desktalk.adapters.dev.test_runner import PytestRunner

__all__ = ['DataFolder', 'PytestRunner', 'SqliteBrowser']

_WHERE = {
    'DataFolder': 'desktalk.adapters.dev.data_files',
    'PytestRunner': 'desktalk.adapters.dev.test_runner',
    'SqliteBrowser': 'desktalk.adapters.dev.sqlite_browser',
}


def __getattr__(name: str) -> Any:
    if name not in _WHERE:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
    return getattr(importlib.import_module(_WHERE[name]), name)
