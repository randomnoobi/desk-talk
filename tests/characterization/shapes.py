"""
The "shape" of a JSON reply, and the saved shapes we compare against.

A shape keeps the keys and the kind of each value, not the values themselves:

    {"ok": true, "fps": 2.5, "tip": null, "labels": ["cup", "key"]}
    → {"fps": "number", "labels": ["str"], "ok": "bool", "tip": "null"}

    dict          → the same keys, each with the shape of its value
    list          → the different shapes of its items (an empty list stays [])
    str / bool    → "str" / "bool"
    int / float   → "number" (the browser can't tell 3 from 3.0)
    None          → "null"

List items that differ only in null-vs-value (an agent with no drawing has "art": null) are folded into one
item shape such as "null|str". Items with different keys (a join note and a chat message) stay separate.

Saved shapes live in snapshots/<case id>.json. To record them again on purpose (after a change you meant to
make), run with DESKTALK_RECORD_SNAPSHOTS=1 and review the diff in git before committing.
"""

from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path
from typing import Any

SNAPSHOT_DIR = Path(__file__).resolve().parent / 'snapshots'
RECORD_ENV = 'DESKTALK_RECORD_SNAPSHOTS'


def recording() -> bool:
    return os.environ.get(RECORD_ENV, '') not in ('', '0')


def shape_of(value: Any) -> Any:
    if value is None:
        return 'null'
    if isinstance(value, bool):          # before int: True is an int in Python
        return 'bool'
    if isinstance(value, (int, float)):
        return 'number'
    if isinstance(value, str):
        return 'str'
    if isinstance(value, list):
        return _fold([shape_of(v) for v in value])
    if isinstance(value, dict):
        return {str(k): shape_of(v) for k, v in sorted(value.items())}
    raise TypeError(f'not a JSON value: {value!r}')


def _dump(shape: Any) -> str:
    return json.dumps(shape, sort_keys=True)


def _merge(a: Any, b: Any) -> Any:
    """One shape that covers both a and b, or None if they are too different to fold together."""
    if a == b:
        return a
    if isinstance(a, str) and isinstance(b, str):
        return '|'.join(sorted(set(a.split('|')) | set(b.split('|'))))
    if isinstance(a, dict) and isinstance(b, dict) and a.keys() == b.keys():
        out = {}
        for k in a:
            m = _merge(a[k], b[k])
            if m is None:
                return None
            out[k] = m
        return out
    if isinstance(a, list) and isinstance(b, list):
        return _fold(a + b)
    return None


def _fold(shapes: list) -> list:
    """The different item shapes of a list, in a fixed order."""
    out: list = []
    for s in shapes:
        for i, have in enumerate(out):
            m = _merge(have, s)
            if m is not None:
                out[i] = m
                break
        else:
            out.append(s)
    return sorted(out, key=_dump)


def differences(expected: Any, actual: Any, vary: tuple[str, ...] = (), path: str = '') -> list[str]:
    """Where `actual` doesn't match `expected`, one line per place.

    Paths look like `messages[].reply_to.text` (`[]` = the items of a list). At a path that matches one of the
    `vary` patterns (fnmatch style), a value may also be null or an empty list: use it only where the app
    really does sometimes send one and sometimes the other."""
    if expected == actual:
        return []
    where = path or '(the whole reply)'
    if any(fnmatch.fnmatchcase(path, p) for p in vary) and ('null' in (expected, actual) or [] in (expected, actual)):
        return []
    if isinstance(expected, dict) and isinstance(actual, dict):
        out = []
        missing = sorted(expected.keys() - actual.keys())
        extra = sorted(actual.keys() - expected.keys())
        if missing:
            out.append(f'{where}: missing key(s) {", ".join(missing)}')
        if extra:
            out.append(f'{where}: unexpected key(s) {", ".join(extra)}')
        for k in sorted(expected.keys() & actual.keys()):
            out += differences(expected[k], actual[k], vary, f'{path}.{k}' if path else k)
        return out
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) == 1 and len(actual) == 1:
            return differences(expected[0], actual[0], vary, f'{path}[]')
        want, got = {_dump(s) for s in expected}, {_dump(s) for s in actual}
        out = [f'{path}[]: no item shaped like {s}' for s in sorted(want - got)]
        out += [f'{path}[]: unexpected item shaped like {s}' for s in sorted(got - want)]
        return out or [f'{where}: expected {_dump(expected)}, got {_dump(actual)}']
    return [f'{where}: expected {_dump(expected)}, got {_dump(actual)}']


def check_snapshot(name: str, observed: dict, vary: tuple[str, ...] = ()) -> None:
    """Compare `observed` (content type, headers, JSON shape) with snapshots/<name>.json, or record it."""
    path = SNAPSHOT_DIR / f'{name}.json'
    if recording():
        SNAPSHOT_DIR.mkdir(exist_ok=True)
        path.write_text(json.dumps(observed, indent=2, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8')
        return
    if not path.is_file():
        raise AssertionError(f'No saved shape for "{name}" yet. Record it with {RECORD_ENV}=1, '
                             f'then check snapshots/{name}.json by eye before committing.')
    saved = json.loads(path.read_text(encoding='utf-8'))
    problems = []
    for key in sorted(saved.keys() | observed.keys()):
        if key == 'json':
            continue
        if saved.get(key) != observed.get(key):
            problems.append(f'{key}: expected {saved.get(key)!r}, got {observed.get(key)!r}')
    if 'json' in saved or 'json' in observed:
        problems += [f'json {p}' for p in differences(saved.get('json'), observed.get('json'), vary)]
    if problems:
        raise AssertionError(f'Reply for "{name}" changed:\n  ' + '\n  '.join(problems) +
                             f'\nIf the change is on purpose, record again with {RECORD_ENV}=1 and review the diff.')
