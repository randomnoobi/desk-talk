"""Settings the Phase 2 parts asked for (see docs/plans/2026-10-21-phase3-notes.md)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from desktalk.config import ConfigError, load_config
from tests.unit.test_config import CONFIG_FILES

ROOT = Path(__file__).resolve().parents[2]


def test_settings_the_new_parts_need_are_in_settings_yaml():
    c = load_config(root=ROOT, environ={})
    assert (c.detector.end2end, c.detector.rect) == (False, False)
    assert c.camera.pair_key_bytes == 6
    assert c.storage.folder == Path('data')
    assert c.agents.personality_max_chars == 400
    assert c.chat.reply_max_chars == 500
    assert c.lens.wait_before_saying_sec.pi == 10
    assert (c.pi.camera_warmup_sec, c.pi.pause_after_error_sec, c.pi.status_every_sec, c.pi.health_every_sec,
            c.pi.settings_check_every_sec) == (1, 0.5, 30, 3, 60)


def test_the_producers_old_error_pause_is_gone():
    assert not hasattr(load_config(root=ROOT, environ={}).camera, 'pause_after_error_sec')


def test_the_public_address_is_empty_unless_set_and_has_no_http(tmp_path):
    (tmp_path / 'config').mkdir()
    for name in CONFIG_FILES:
        shutil.copy(ROOT / 'config' / name, tmp_path / 'config' / name)
    assert load_config(root=tmp_path, environ={}).web.public_address == ''
    local = tmp_path / 'config' / 'settings.local.yaml'
    local.write_text(yaml.safe_dump({'web': {'public_address': ' desktalk.local:5000 '}}))
    assert load_config(root=tmp_path, environ={}).web.public_address == 'desktalk.local:5000'
    local.write_text(yaml.safe_dump({'web': {'public_address': 'http://desktalk.local:5000'}}))
    with pytest.raises(ConfigError, match='public_address'):
        load_config(root=tmp_path, environ={})
