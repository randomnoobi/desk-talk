"""Small JSON documents in files: preferences, habit days, learned personalities, desk rules."""

from __future__ import annotations

import json
import logging
import os

import pytest

from desktalk.adapters.storage import JsonFileStore
from tests.contracts.key_value_contract import KeyValueContract


class TestJsonFileStore(KeyValueContract):
    @pytest.fixture
    def kv(self, tmp_path):
        return JsonFileStore(tmp_path)


def test_keys_are_the_same_files_the_app_has_always_used(tmp_path):
    (tmp_path / 'habits').mkdir()
    (tmp_path / 'preferences.json').write_text('{\n  "wake_time": "06:30"\n}', encoding='utf-8')
    (tmp_path / 'habits' / '2026-10-20.json').write_text('{\n "day": "2026-10-20",\n "sips": 4\n}',
                                                          encoding='utf-8')
    kv = JsonFileStore(tmp_path)
    assert kv.load('preferences') == {'wake_time': '06:30'}
    assert kv.load('habits/2026-10-20') == {'day': '2026-10-20', 'sips': 4}
    kv.save('habits/2026-10-21', {'day': '2026-10-21', 'sips': 1})
    assert json.loads((tmp_path / 'habits' / '2026-10-21.json').read_text(encoding='utf-8')) == \
        {'day': '2026-10-21', 'sips': 1}


@pytest.mark.parametrize('content', [b'{"wake_time": "06:3', b'', b'\xff\xfe not text'])
def test_a_broken_file_reads_as_missing_and_is_logged(tmp_path, caplog, content):
    (tmp_path / 'preferences.json').write_bytes(content)
    kv = JsonFileStore(tmp_path)
    with caplog.at_level(logging.WARNING):
        assert kv.load('preferences', {'wake_time': '07:00'}) == {'wake_time': '07:00'}
    assert 'preferences.json' in caplog.text


def test_a_value_that_cannot_be_saved_leaves_the_old_file_as_it_was(tmp_path):
    kv = JsonFileStore(tmp_path)
    kv.save('preferences', {'wake_time': '07:00'})
    with pytest.raises(TypeError):
        kv.save('preferences', {'wake_time': object()})
    assert kv.load('preferences') == {'wake_time': '07:00'}


def test_a_save_cut_off_before_the_new_file_is_swapped_in_leaves_the_old_one_whole(tmp_path, monkeypatch):
    kv = JsonFileStore(tmp_path)
    kv.save('preferences', {'wake_time': '07:00'})

    def computer_stops(*_args):
        raise OSError('the disk is full')

    monkeypatch.setattr(os, 'replace', computer_stops)
    with pytest.raises(OSError):
        kv.save('preferences', {'wake_time': '06:00'})
    monkeypatch.undo()
    assert kv.load('preferences') == {'wake_time': '07:00'}
    assert sorted(p.name for p in tmp_path.iterdir()) == ['preferences.json']      # no half-written file left


@pytest.mark.parametrize('key', ['', '../outside', '/etc/passwd', 'habits/../../outside', 'habits//x', 'a\\b'])
def test_keys_that_are_not_plain_names_are_refused(tmp_path, key):
    kv = JsonFileStore(tmp_path / 'data')
    with pytest.raises(ValueError):
        kv.save(key, {})
    with pytest.raises(ValueError):
        kv.load(key)
    assert not (tmp_path / 'outside.json').exists()


def test_keys_leave_out_temp_files_and_files_that_are_not_keys(tmp_path):
    kv = JsonFileStore(tmp_path)
    kv.save('habits/2026-10-21', {})
    (tmp_path / 'habits' / '.2026-10-21.json.k3j9.tmp').write_text('{"half', encoding='utf-8')   # after a crash
    (tmp_path / 'habits' / 'old copy.json').write_text('{}', encoding='utf-8')
    (tmp_path / 'habits' / 'notes.txt').write_text('hi', encoding='utf-8')
    assert kv.keys('habits/') == ['habits/2026-10-21']
