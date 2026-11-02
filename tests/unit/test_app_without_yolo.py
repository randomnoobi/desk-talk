"""The app without a detector of its own: on the Pi the detector container does the detecting, so the app
image carries no PyTorch or YOLO. detector.enabled: false builds a NoDetector, and nothing may need
ultralytics or torch to be installed."""

from __future__ import annotations

import re
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import numpy as np
import pytest
import yaml

from desktalk.adapters.detector import NoDetector, UltralyticsDetector
from desktalk.bootstrap import make_detector
from desktalk.config import DetectorSettings
from desktalk.domain import DetectorUnavailable
from tests.contracts.detector_contract import UnavailableDetectorContract
from tests.integration.app_kit import changed, desk_config

ROOT = Path(__file__).resolve().parents[2]
HEAVY = ('ultralytics', 'torch', 'torchvision')


class TestNoDetectorContract(UnavailableDetectorContract):
    @pytest.fixture
    def detector(self):
        return NoDetector()


def test_the_reason_says_the_detector_is_off_and_where_to_turn_it_on():
    with pytest.raises(DetectorUnavailable) as err:
        NoDetector().detect(np.zeros((8, 8, 3), np.uint8))
    assert 'detector.enabled' in str(err.value)


# ── the setting ──────────────────────────────────────────────────────
def test_the_detector_is_on_in_settings_yaml_so_a_computer_still_finds_things():
    data = yaml.safe_load((ROOT / 'config' / 'settings.yaml').read_text())['detector']
    assert data['enabled'] is True
    assert DetectorSettings.model_validate({**data, 'device': 'cpu'}).enabled is True


def test_make_detector_gives_a_no_detector_when_it_is_off(tmp_path):
    config = changed(desk_config(tmp_path), 'detector', enabled=False)
    assert isinstance(make_detector(config), NoDetector)


def test_make_detector_still_makes_the_yolo_one_when_it_is_on(tmp_path, monkeypatch):
    made = []

    class Spy(UltralyticsDetector):
        def __init__(self, *args, **kw):
            made.append(args)

        def warmup(self):
            made.append('warmed up')

    monkeypatch.setattr('desktalk.bootstrap.UltralyticsDetector', Spy)
    detector = make_detector(desk_config(tmp_path))
    assert isinstance(detector, Spy) and made[-1] == 'warmed up'


# ── no PyTorch, no YOLO ──────────────────────────────────────────────
BUILD_WITHOUT_YOLO = textwrap.dedent('''
    import importlib.abc
    import sys
    import tempfile
    from pathlib import Path

    HEAVY = {heavy!r}

    class Block(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if name.split('.')[0] in HEAVY:
                raise ImportError(name + ' is blocked: this app has no detector of its own')

    sys.meta_path.insert(0, Block())

    from desktalk.adapters.detector import NoDetector
    from desktalk.bootstrap import build
    from desktalk.config import load_config
    from tests.integration.app_kit import changed
    from tests.fakes import FakeClock

    config = load_config(root=Path({root!r}), environ={{}}, dotenv=Path('/nonexistent'))
    config = changed(config, 'detector', enabled={enabled})
    with tempfile.TemporaryDirectory() as folder:
        app = build(config, clock=FakeClock(), data_folder=Path(folder), pair_key='k', synchronous_events=True,
                    set_up_logging=False)
        assert isinstance(app.detector, NoDetector), type(app.detector)
        loaded = sorted(m for m in sys.modules if m.split('.')[0] in HEAVY)
        assert not loaded, loaded
    print('built without', ', '.join(HEAVY))
''')


def test_builds_without_ultralytics_or_torch():
    script = BUILD_WITHOUT_YOLO.format(heavy=HEAVY, root=str(ROOT), enabled=False)
    done = subprocess.run([sys.executable, '-c', script], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    assert 'built without' in done.stdout


def test_a_computer_without_the_extra_still_builds_with_the_detector_on_and_says_what_to_do():
    """A fresh clone with a plain `uv sync` and detector.enabled: true: no raw ModuleNotFoundError from build()."""
    script = BUILD_WITHOUT_YOLO.format(heavy=HEAVY, root=str(ROOT), enabled=True)
    done = subprocess.run([sys.executable, '-c', script], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    assert 'built without' in done.stdout
    assert '--extra computer-detector' in done.stderr and 'detector.enabled' in done.stderr


def test_make_detector_says_what_to_do_when_yolo_is_not_installed(tmp_path, monkeypatch, caplog):
    def missing(*args, **kw):
        raise ModuleNotFoundError("No module named 'torch'", name='torch')

    monkeypatch.setattr('desktalk.bootstrap.UltralyticsDetector', missing)
    detector = make_detector(desk_config(tmp_path))
    assert isinstance(detector, NoDetector)
    assert '--extra computer-detector' in caplog.text and 'detector.enabled: false' in caplog.text


def test_the_blocker_in_that_test_really_stops_the_imports():
    script = BUILD_WITHOUT_YOLO.split('from desktalk.adapters')[0] + 'import torch'
    done = subprocess.run([sys.executable, '-c', script.format(heavy=HEAVY, root=str(ROOT), enabled=False)],
                          cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert done.returncode != 0 and 'blocked' in done.stderr


# ── the install extra ────────────────────────────────────────────────
def _names(requirements: list[str]) -> set[str]:
    return {re.split(r'[\[=<>;\s]', r)[0].lower() for r in requirements}


def test_ultralytics_and_torch_are_in_the_computer_detector_extra_not_in_the_plain_install():
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']
    assert not _names(project['dependencies']) & set(HEAVY)
    assert _names(project['optional-dependencies']['computer-detector']) == set(HEAVY)
