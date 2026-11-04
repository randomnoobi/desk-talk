"""The Pi's own file for the user's changes, config/mine/settings.mine.yaml: laid over settings.local.yaml, and fine
when it isn't there (raspberry_pi/compose.yaml mounts the git-ignored raspberry_pi/mine/ folder there)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from desktalk.config import ConfigError, load_config
from tests.unit.test_config import CONFIG_FILES

ROOT = Path(__file__).resolve().parents[2]


def project(tmp_path: Path, local: str | None = None, mine: str | None = None) -> Path:
    (tmp_path / 'config').mkdir()
    for name in CONFIG_FILES:
        shutil.copy(ROOT / 'config' / name, tmp_path / 'config' / name)
    if local is not None:
        (tmp_path / 'config' / 'settings.local.yaml').write_text(local, encoding='utf-8')
    if mine is not None:
        (tmp_path / 'config' / 'mine').mkdir()
        (tmp_path / 'config' / 'mine' / 'settings.mine.yaml').write_text(mine, encoding='utf-8')
    return tmp_path


def test_without_the_file_nothing_changes(tmp_path):
    assert load_config(root=project(tmp_path), environ={}).llm.ai_url == ''


def test_a_folder_with_no_file_in_it_is_fine(tmp_path):
    (project(tmp_path) / 'config' / 'mine').mkdir()
    assert load_config(root=tmp_path, environ={}).llm.ai_url == ''


def test_a_file_with_only_comments_is_fine(tmp_path):
    root = project(tmp_path, mine='# nothing of mine yet\n')
    assert load_config(root=root, environ={}).llm.ai_url == ''


def test_the_file_is_laid_over_the_pi_settings(tmp_path):
    root = project(tmp_path, local='llm:\n  ai_url: http://pi-file:1/v1\n  send_pictures: false\n',
                   mine='llm:\n  ai_url: http://my-laptop.local:11434/v1\n')
    config = load_config(root=root, environ={})
    assert config.llm.ai_url == 'http://my-laptop.local:11434/v1'
    assert config.llm.send_pictures is False         # what the Pi's file set and mine did not touch


def test_a_mistake_in_it_names_that_file(tmp_path):
    root = project(tmp_path, mine='camera:\n  fps: fast\n')
    with pytest.raises(ConfigError, match=r'config/mine/settings\.mine\.yaml: camera\.fps'):
        load_config(root=root, environ={})
