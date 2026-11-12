"""saving_from_environment(): the saving mode and the in-memory folder that raspberry_pi/app/start.sh gives the app."""

from __future__ import annotations

import logging
from pathlib import Path

from desktalk.domain import Saving
from desktalk.runtime.saving import saving_from_environment


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


def test_off_with_no_folder_says_so_and_leaves_the_folder_to_build(caplog):
    with caplog.at_level(logging.WARNING):
        assert saving_from_environment({'DESKTALK_SAVING': 'off'}) == (Saving.OFF, None)
    assert [record.levelno for record in caplog.records] == [logging.WARNING]
