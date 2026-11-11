"""clock_synced(): has this computer's clock been set from the internet since it started? boot_id(): which run of
the computer is this, so a restart of only the app can be told from a power cut? ClockCheck: both questions the
app asks of its clock while it runs, and is the clock believable at all (not, say, set to 1990)?"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from desktalk.config import ClockSettings, ObjectStatesSettings
from desktalk.runtime import ClockCheck, boot_id, clock_synced
from tests.fakes import FakeClock
from tests.unit.test_core_object_states_tracker import SETTINGS

ROOT = Path(__file__).resolve().parents[2]
EARLIEST = date(2025, 1, 1)
EARLIEST_TS = datetime(2025, 1, 1, tzinfo=UTC).timestamp()
IN_1990 = datetime(1990, 1, 1, 12, tzinfo=UTC).timestamp()     # the meeting's hand test
NOW = FakeClock().now()


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


# ── is the clock believable? ──
def test_the_earliest_believable_date_is_in_the_settings_and_before_the_tests_own_clock():
    data = yaml.safe_load((ROOT / 'config' / 'settings.yaml').read_text())
    earliest = ClockSettings.model_validate(data['clock']).earliest_believable_date
    assert date(1990, 1, 1) < earliest <= datetime.fromtimestamp(NOW, UTC).date()


def test_a_settings_file_without_the_earliest_believable_date_does_not_load():
    with pytest.raises(ValidationError):
        ClockSettings.model_validate({})


def test_a_clock_before_the_earliest_believable_date_is_not_believable():
    check = ClockCheck(None, EARLIEST)
    assert check.believable(NOW) is True
    assert check.believable(EARLIEST_TS) is True
    assert check.believable(EARLIEST_TS - 1) is False
    assert check.believable(IN_1990) is False


def test_an_unbelievable_clock_is_warned_about_once_until_it_is_right_again(caplog):
    check = ClockCheck(None, EARLIEST)
    with caplog.at_level(logging.INFO, logger='desktalk.runtime.clock'):
        for _ in range(3):
            check.believable(IN_1990)
        check.believable(NOW)
        check.believable(NOW)
        check.believable(IN_1990)
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2
    assert '1990-01-01' in warnings[0] and 'clock.earliest_believable_date' in warnings[0]
    assert 'nothing is deleted by date' in warnings[0]
    assert 'looks right again' in caplog.text


# ── synced: set from the internet, and believable ──
def test_with_no_flag_file_a_believable_clock_is_synced():
    check = ClockCheck(None, EARLIEST)
    assert check.synced(NOW) is True
    assert check.synced(IN_1990) is False          # set to 1990 by hand: not to be trusted, flag or no flag


def test_synced_looks_at_the_flag_file_again_until_it_is_there(tmp_path):
    flag = tmp_path / 'synchronized'
    check = ClockCheck(flag, EARLIEST)
    assert check.synced(NOW) is False
    flag.touch()
    assert check.synced(NOW) is True
    assert check.synced(IN_1990) is False


def test_once_the_flag_file_was_seen_it_is_not_looked_at_again(tmp_path):
    """It stays there until the next boot; once seen, asking costs nothing."""
    flag = tmp_path / 'synchronized'
    flag.touch()
    check = ClockCheck(flag, EARLIEST)
    assert check.synced(NOW) is True
    flag.unlink()
    assert check.synced(NOW) is True
