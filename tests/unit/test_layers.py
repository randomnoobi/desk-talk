"""Which parts of Desk Talk may import what.

desktalk/core and desktalk/domain are the app's own thinking: they talk to pictures, the AI, the web and the
disk only through the interfaces in desktalk/ports.py. So they never import a library that does input or output
(OpenCV, numpy, requests, openai, ultralytics, fastapi), and never the parts that build or serve the app.

desktalk/web only serves the app: it reaches the running app through desktalk.bootstrap's DeskTalk, whose
fields are interfaces, and never imports core, adapters or runtime code itself.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NOT_IN_CORE = ('cv2', 'numpy', 'requests', 'openai', 'ultralytics', 'fastapi',
               'desktalk.adapters', 'desktalk.web', 'desktalk.runtime', 'desktalk.bootstrap')

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


def core_files() -> list[str]:
    return sorted(p.relative_to(ROOT).as_posix()
                  for folder in ('desktalk/core', 'desktalk/domain') for p in (ROOT / folder).rglob('*.py'))


@pytest.mark.parametrize('file', core_files())
def test_core_imports_no_io_libraries(file):
    assert forbidden(imported_names(ROOT / file)) == []


def test_imports_inside_a_function_count_too(tmp_path):
    sample = tmp_path / 'sample.py'
    sample.write_text('import json\n\ndef f():\n    import cv2\n    from desktalk.web import app\n')
    assert forbidden(imported_names(sample)) == ['cv2', 'desktalk.web', 'desktalk.web.app']


# ── the web layer ────────────────────────────────────────────────────
WEB_MAY_IMPORT = ('desktalk.ports', 'desktalk.domain', 'desktalk.config', 'desktalk.bootstrap', 'desktalk.web',
                  'desktalk_shared',       # the Pi's upload format (standard library only, like the domain)
                  'fastapi', 'starlette', 'pydantic', 'uvicorn', 'anyio',     # the web servers
                  'qrcode', 'cryptography')                                    # the phone link and its certificate
# The few core names the web still imports, each a constant, an error or one small rule, until they move out of
# core (they live in files the web layer doesn't own): Lens's id, the error for a chat with an unknown agent,
# what the message hub puts last in a dropped page's queue, and whether quiet hours are on now.
WEB_MAY_IMPORT_FROM_CORE = {
    ('desktalk.core.agents.speakers', 'LENS'),
    ('desktalk.core.chat.message_generator', 'UnknownChat'),
    ('desktalk.core.chat.message_hub', 'CLOSED'),
    ('desktalk.core.habits.preferences', 'quiet_hours_now'),
}


def imports(path: Path) -> set[tuple[str, str]]:
    """(module, name) for every import in a file: ('json', '') for `import json`, ('x.y', 'z') for
    `from x.y import z`, at the top or inside a function."""
    found: set[tuple[str, str]] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Import):
            found.update((alias.name, '') for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.update((node.module, alias.name) for alias in node.names)
    return found


def web_forbidden(found: set[tuple[str, str]]) -> list[str]:
    def allowed(module: str, name: str) -> bool:
        top = module.split('.')[0]
        return (top in sys.stdlib_module_names or top == '__future__'
                or any(module == ok or module.startswith(ok + '.') for ok in WEB_MAY_IMPORT)
                or (module, name) in WEB_MAY_IMPORT_FROM_CORE)
    return sorted(f'{module}.{name}' if name else module for module, name in found if not allowed(module, name))


def web_files() -> list[str]:
    return sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / 'desktalk' / 'web').rglob('*.py'))


@pytest.mark.parametrize('file', web_files())
def test_web_imports_only_ports_domain_and_bootstrap(file):
    assert web_forbidden(imports(ROOT / file)) == []


def test_the_web_rule_catches_core_adapters_and_runtime(tmp_path):
    sample = tmp_path / 'sample.py'
    sample.write_text('import json\nfrom desktalk.ports import Clock\nfrom desktalk.core.agents.speakers import LENS\n'
                      'from desktalk.core.agents.speakers import speaker_for\n\ndef f():\n'
                      '    from desktalk.adapters.storage import EventLog\n    import desktalk.runtime\n'
                      '    import requests\n')
    assert web_forbidden(imports(sample)) == ['desktalk.adapters.storage.EventLog', 'desktalk.core.agents.speakers.'
                                              'speaker_for', 'desktalk.runtime', 'requests']
