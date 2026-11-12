"""saving_from_environment(): the saving mode and the in-memory folder that raspberry_pi/app/start.sh gives the app."""

from __future__ import annotations

import logging
from pathlib import Path

from desktalk.domain import Saving
from desktalk.runtime.saving import folder_for, saving_from_environment


def test_with_nothing_set_the_app_saves_as_before():
    assert saving_from_environment({}) == (Saving.PLAIN, None)


def test_each_mode_start_sh_gives_is_read_with_its_folder():
    assert saving_from_environment({'DESKTALK_SAVING': 'room'}) == (Saving.ROOM, None)
    assert saving_from_environment({'DESKTALK_SAVING': 'off', 'DESKTALK_DATA_FOLDER': '/tmp/memory'}) == (
        Saving.OFF, Path('/tmp/memory'))


def test_a_mode_it_doesnt_know_means_plain_with_a_warning(caplog):
    with caplog.at_level(logging.WARNING):
        assert saving_from_environment({'DESKTALK_SAVING': 'sometimes'}) == (Saving.PLAIN, None)
    assert any('sometimes' in record.getMessage() for record in caplog.records)


def test_off_with_no_folder_says_so_and_leaves_the_folder_to_folder_for(caplog):
    with caplog.at_level(logging.WARNING):
        assert saving_from_environment({'DESKTALK_SAVING': 'off'}) == (Saving.OFF, None)
    assert [record.levelno for record in caplog.records] == [logging.WARNING]


def test_the_folder_given_is_the_folder_used(tmp_path):
    memory = tmp_path / 'memory'
    assert folder_for(Saving.OFF, memory, tmp_path / 'shm') == memory
    assert folder_for(Saving.PLAIN, None, tmp_path / 'shm') is None
    assert folder_for(Saving.ROOM, None, tmp_path / 'shm') is None
    assert not (tmp_path / 'shm').exists()


def test_off_with_no_folder_makes_one_in_the_memory_fallback_folder(tmp_path):
    shm = tmp_path / 'shm'
    shm.mkdir()
    made = folder_for(Saving.OFF, None, shm)
    assert made is not None and made.is_dir() and made.parent == shm


def test_off_with_no_memory_fallback_folder_still_gives_a_folder_and_says_where(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        made = folder_for(Saving.OFF, None, tmp_path / 'no such folder')
    assert made is not None and made.is_dir()
    assert any(str(made) in record.getMessage() for record in caplog.records)
