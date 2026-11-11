"""clock_synced(): has this computer's clock been set from the internet since it started? boot_id(): which run of
the computer is this, so a restart of only the app can be told from a power cut?"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from desktalk.config import ObjectStatesSettings
from desktalk.runtime import boot_id, clock_synced
from tests.unit.test_core_object_states_tracker import SETTINGS


def test_with_no_flag_file_the_clock_is_trusted():
    """A Mac or a computer with a clock battery keeps its time, so settings → object_states names no file."""
    assert clock_synced(None) is True


def test_the_clock_is_synced_once_the_flag_file_is_there(tmp_path):
    flag = tmp_path / 'synchronized'
    assert clock_synced(flag) is False
    flag.touch()
    assert clock_synced(flag) is True


def test_a_missing_folder_means_not_synced(tmp_path):
    assert clock_synced(tmp_path / 'no-such-folder' / 'synchronized') is False


def test_a_settings_file_without_the_flag_file_key_does_not_load():
    """clock_synced_file must be named (null on a Mac), so a file that forgets it fails loudly."""
    without = {k: v for k, v in SETTINGS.items() if k != 'clock_synced_file'}
    with pytest.raises(ValidationError):
        ObjectStatesSettings.model_validate(without)


def test_with_no_boot_id_file_there_is_no_boot_id():
    assert boot_id(None) is None


def test_a_missing_boot_id_file_means_no_boot_id(tmp_path):
    """A Mac has no /proc: it can't say which boot it is in, and trusts its clock as it always did."""
    assert boot_id(tmp_path / 'boot_id') is None


def test_the_boot_id_is_the_file_without_its_newline(tmp_path):
    file = tmp_path / 'boot_id'
    file.write_text('6f1c2a6e-3b9d-4c1e-9f0a-2d5e8b7c4a10\n')
    assert boot_id(file) == '6f1c2a6e-3b9d-4c1e-9f0a-2d5e8b7c4a10'


def test_an_empty_boot_id_file_means_no_boot_id(tmp_path):
    file = tmp_path / 'boot_id'
    file.write_text('\n')
    assert boot_id(file) is None


def test_a_settings_file_without_the_boot_id_file_key_does_not_load():
    without = {k: v for k, v in SETTINGS.items() if k != 'boot_id_file'}
    with pytest.raises(ValidationError):
        ObjectStatesSettings.model_validate(without)
