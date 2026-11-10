"""clock_synced(): has this computer's clock been set from the internet since it started?"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from desktalk.config import ObjectStatesSettings
from desktalk.runtime import clock_synced
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
