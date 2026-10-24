"""config/prompts.yaml holds exactly the wording the vision code has today. (Task 5 owns it.)

Until Tasks 5-7 of the cleanup plan switch the code over to config.prompts, the same words are in both places.
Each check here compares one text from prompts.yaml with what the code says. When a task moves a part of the
code over to config.prompts, it deletes that part's checks here (they would compare the file with itself).
"""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from desktalk.config import load_config
from desktalk.core.vision import camera_health

ROOT = Path(__file__).resolve().parents[2]
CONFIG = load_config(root=ROOT, environ={})
P = CONFIG.prompts
AGENT: Any = SimpleNamespace(name='Cup', object='cup', personality='Calm and kind.', habits=('hydration', 'breaks'),
                        tracker='hydration')
MIN = 60


# ── the camera checks ──
def _health(source_kind='pi'):
    return camera_health.CameraHealth(CONFIG.lens, source_kind=source_kind, moved_within_sec=1.5, ignore_labels=())


def test_camera_tables():
    camera = P.camera
    assert {k: (v['title'], v['fix']) for k, v in camera.titles.model_dump().items()} == camera_health.TITLES
    assert camera.fixed.model_dump() == camera_health.FIXED_TEXT
    assert camera.side_hints.model_dump() == camera_health.SIDE_HINT
    assert camera.lost.model_dump() == camera_health.LOST_TEXT
    assert camera.lost_fix.model_dump() == camera_health.LOST_FIX
    assert camera.edge_tip == camera_health.EDGE_TIP


@pytest.mark.parametrize('key, detail, blanks', [
    ('covered', {}, {}), ('dark', {}, {}), ('glare', {}, {}), ('blurry', {}, {}), ('empty', {}, {}),
    ('bumped', {'degrees': 17}, {'degrees': 17}),
    ('cutoff', {'object': 'cup', 'side': 'top'}, {'object': 'cup', 'side': 'top', 'hint': 'tilt me up a little'}),
    ('cutoff', {'object': 'cup', 'side': 'middle'}, {'object': 'cup', 'side': 'middle',
                                                    'hint': P.camera.other_side_hint}),
    ('cutoff', {}, {'object': P.camera.unknown_object, 'side': 'left', 'hint': 'point me a little more to the left'}),
    ('slow', {'fps': 1.234}, {'fps': 1.234}),
])
def test_camera_problem_texts(key, detail, blanks):
    assert _health()._problem_text(key, detail) == getattr(P.camera.problems, key).format(**blanks)


@pytest.mark.parametrize('source_kind', ['pi', 'phone', 'webcam'])
def test_lost_camera_text(source_kind):
    assert _health(source_kind)._problem_text('lost', {}) == getattr(P.camera.lost, source_kind)


def test_camera_panel_titles():
    health = _health('phone')
    health._lock = threading.RLock()
    health._active = {'lost': {'detail': {}}, 'cutoff': {'detail': {'object': 'cup', 'side': 'left'}}}
    assert health.issues() == [
        {'key': 'lost', 'title': P.camera.lost_title, 'fix': P.camera.lost_fix.phone},
        {'key': 'cutoff', 'title': P.camera.cutoff_title.format(object='Cup', side='left'),
         'fix': P.camera.titles.cutoff.fix}]
