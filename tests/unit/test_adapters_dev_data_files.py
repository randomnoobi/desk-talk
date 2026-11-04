"""The data folder viewer for the developer page's storage tab (desktalk/adapters/dev/data_files.py: DataFolder).

It lists what's in the data folder and shows the start of a file, and never reads anything outside the folder.
"""

from __future__ import annotations

import os

import pytest

from desktalk.adapters.dev.data_files import DataFolder
from desktalk.domain import DataFile
from desktalk.ports import DataFiles
from tests.contracts.dev_contracts import DataFilesContract
from tests.unit.test_core_vision_kit import app_config

WORDS = app_config().prompts.dev
PREVIEW_BYTES = 64 * 1024


@pytest.fixture
def folder(tmp_path):
    """A data folder like the app's: an event log day, the object state database, a JSON store and a log."""
    data = tmp_path / 'data'
    (data / 'events' / '2026-10-24').mkdir(parents=True)
    (data / 'events' / '2026-10-24' / 'events.jsonl').write_text('{"kind": "alert"}\n')
    (data / 'object_states.sqlite').write_bytes(b'SQLite format 3\x00' + bytes(100))
    (data / 'preferences.json').write_text('{"dark": true}')
    (data / 'logs').mkdir()
    (data / 'logs' / 'notes.txt').write_text('hello desk')
    return data


@pytest.fixture
def viewer(folder):
    return DataFolder(folder, PREVIEW_BYTES, words=WORDS)


def test_it_is_a_data_files(viewer):
    assert isinstance(viewer, DataFiles)


def test_list_shows_files_with_sizes(viewer):
    found = {f.path: f for f in viewer.list()}
    assert set(found) == {'events', 'events/2026-10-24', 'events/2026-10-24/events.jsonl', 'object_states.sqlite',
                          'preferences.json', 'logs', 'logs/notes.txt'}
    assert all(isinstance(f, DataFile) for f in found.values())
    assert found['preferences.json'] == DataFile(path='preferences.json', bytes=len('{"dark": true}'),
                                                 modified=found['preferences.json'].modified, is_folder=False)
    assert found['object_states.sqlite'].bytes == 116
    assert found['events/2026-10-24'].is_folder is True
    assert found['logs/notes.txt'].modified > 0


def test_list_is_in_order(viewer):
    paths = [f.path for f in viewer.list()]
    assert paths == sorted(paths)


def test_a_missing_data_folder_lists_nothing(tmp_path):
    assert DataFolder(tmp_path / 'not_made_yet', PREVIEW_BYTES, words=WORDS).list() == []


def test_list_leaves_out_links_that_point_outside(viewer, folder, tmp_path):
    (tmp_path / 'secret.txt').write_text('the key')
    (folder / 'sneaky.txt').symlink_to(tmp_path / 'secret.txt')
    (folder / 'sneaky_folder').symlink_to(tmp_path)
    (folder / 'notes_again.txt').symlink_to(folder / 'logs' / 'notes.txt')
    paths = {f.path for f in viewer.list()}
    assert 'sneaky.txt' not in paths and 'sneaky_folder' not in paths
    assert 'notes_again.txt' in paths


def test_links_in_a_loop_are_left_out_and_have_no_preview(viewer, folder):
    (folder / 'a').symlink_to(folder / 'b')
    (folder / 'b').symlink_to(folder / 'a')
    paths = {f.path for f in viewer.list()}
    assert 'a' not in paths and 'logs/notes.txt' in paths
    with pytest.raises(FileNotFoundError):
        viewer.preview('a')


def test_preview_reads_the_start_of_a_file(viewer, folder):
    (folder / 'big.log').write_text('x' * 200 * 1024)
    assert viewer.preview('big.log') == 'x' * PREVIEW_BYTES


def test_preview_shows_bytes_that_are_not_text_as_replacement_characters(viewer, folder):
    (folder / 'odd.bin').write_bytes(b'ok\xff\xfeok')
    assert viewer.preview('odd.bin') == 'ok��ok'


@pytest.mark.parametrize('outside', ['../.env', '/etc/passwd', 'sneaky.txt'])
def test_paths_outside_are_refused(viewer, folder, tmp_path, outside):
    (tmp_path / '.env').write_text('GEMINI_API_KEY=secret')
    (tmp_path / 'secret.txt').write_text('the key')
    (folder / 'sneaky.txt').symlink_to(tmp_path / 'secret.txt')
    with pytest.raises(ValueError) as refused:
        viewer.preview(outside)
    assert str(refused.value) == WORDS.outside_data_folder.format(path=outside)


def test_a_folder_is_refused(viewer):
    with pytest.raises(ValueError) as refused:
        viewer.preview('logs')
    assert str(refused.value) == WORDS.is_a_folder.format(path='logs')


def test_missing_file(viewer):
    with pytest.raises(FileNotFoundError) as missing:
        viewer.preview('logs/gone.txt')
    assert str(missing.value) == WORDS.no_such_file.format(path='logs/gone.txt')


def test_a_database_is_not_read(viewer):
    assert viewer.preview('object_states.sqlite') == WORDS.database_file.format(path='object_states.sqlite')


class TestDataFolderPassesDataFilesContract(DataFilesContract):
    @pytest.fixture
    def data_files(self, viewer):
        return viewer


# ── files that hold a secret (the cloud link's token) ──
def test_a_hidden_file_is_not_listed_and_has_no_preview(folder):
    (folder / 'cloud.json').write_text('{"token": "super-secret-token"}')
    (folder / 'logs' / 'cloud.json').write_text('{"not": "the same file"}')
    viewer = DataFolder(folder, PREVIEW_BYTES, words=WORDS, hidden=['cloud.json'])
    paths = [f.path for f in viewer.list()]
    assert 'cloud.json' not in paths and 'logs/cloud.json' in paths      # only the one in the data folder itself
    for way in ('cloud.json', './cloud.json', 'logs/../cloud.json'):
        with pytest.raises(FileNotFoundError) as raised:
            viewer.preview(way)
        assert 'super-secret-token' not in str(raised.value)
    assert viewer.preview('logs/cloud.json') == '{"not": "the same file"}'


def test_a_link_to_a_hidden_file_shows_nothing_either(folder):
    (folder / 'cloud.json').write_text('{"token": "super-secret-token"}')
    (folder / 'innocent.txt').symlink_to(folder / 'cloud.json')
    viewer = DataFolder(folder, PREVIEW_BYTES, words=WORDS, hidden=['cloud.json'])
    assert 'innocent.txt' not in [f.path for f in viewer.list()]
    with pytest.raises(FileNotFoundError):
        viewer.preview('innocent.txt')


def test_nothing_is_hidden_unless_asked(folder):
    (folder / 'cloud.json').write_text('{}')
    assert 'cloud.json' in [f.path for f in DataFolder(folder, PREVIEW_BYTES, words=WORDS).list()]


def test_a_hidden_file_is_hidden_whatever_its_letters_or_hard_links(folder):
    """A case-insensitive disk (this Mac's) opens cloud.json as Cloud.json or CLOUD.JSON too, and a hard link is the
    same file under another name."""
    (folder / 'cloud.json').write_text('{"token": "super-secret-token"}')
    os.link(folder / 'cloud.json', folder / 'copy.txt')
    viewer = DataFolder(folder, PREVIEW_BYTES, words=WORDS, hidden=['cloud.json'])
    for way in ('Cloud.json', 'CLOUD.JSON', 'copy.txt'):
        with pytest.raises(FileNotFoundError) as raised:
            viewer.preview(way)
        assert 'super-secret-token' not in str(raised.value)
    listed = [f.path.lower() for f in viewer.list()]
    assert 'cloud.json' not in listed and 'copy.txt' not in listed
