"""Which parts of Desk Talk may import what.

desktalk/core and desktalk/domain are the app's own thinking: they talk to pictures, the AI, the web and the
disk only through the interfaces in desktalk/ports.py. So they never import a library that does input or output
(OpenCV, numpy, requests, openai, ultralytics, fastapi), and never the parts that build or serve the app.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NOT_IN_CORE = ('cv2', 'numpy', 'requests', 'openai', 'ultralytics', 'fastapi',
               'desktalk.adapters', 'desktalk.web', 'desktalk.runtime', 'desktalk.bootstrap')

# Files that still import one of those, and the task of the cleanup plan that removes it.
STILL_TO_FIX = {
    'desktalk/core/chat/workers/lost_items.py': 'Task 6 of the cleanup plan removes this import',
}


def imported_names(path: Path) -> set[str]:
    """Every module a Python file imports, at the top or inside a function."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
            names.update(f'{node.module}.{alias.name}' for alias in node.names)
    return names


def forbidden(names: set[str]) -> list[str]:
    return sorted(n for n in names if any(n == bad or n.startswith(bad + '.') for bad in NOT_IN_CORE))


def core_files() -> list:
    files = sorted(p.relative_to(ROOT).as_posix()
                   for folder in ('desktalk/core', 'desktalk/domain') for p in (ROOT / folder).rglob('*.py'))
    return [pytest.param(f, marks=pytest.mark.xfail(strict=True, reason=STILL_TO_FIX[f])) if f in STILL_TO_FIX
            else f for f in files]


@pytest.mark.parametrize('file', core_files())
def test_core_imports_no_io_libraries(file):
    assert forbidden(imported_names(ROOT / file)) == []


def test_the_list_of_files_still_to_fix_names_real_files():
    for file in STILL_TO_FIX:
        assert (ROOT / file).is_file(), file


def test_imports_inside_a_function_count_too(tmp_path):
    sample = tmp_path / 'sample.py'
    sample.write_text('import json\n\ndef f():\n    import cv2\n    from desktalk.web import app\n')
    assert forbidden(imported_names(sample)) == ['cv2', 'desktalk.web', 'desktalk.web.app']
