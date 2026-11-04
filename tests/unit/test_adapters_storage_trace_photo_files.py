"""TracePhotoFiles (desktalk/adapters/storage/trace_photo_files.py): the trace pictures on disk, one folder per kind
of object, each with a small index, keeping only the newest few timed pictures and, apart from them, the newest few
step pictures. The TracePhotos contract runs on it in
tests/contracts/test_trace_photos_contract.py; these are the parts only files have."""

from __future__ import annotations

import json

import pytest

from desktalk.adapters.storage.trace_photo_files import TracePhotoFiles
from tests.contracts.test_trace_photos_contract import JPEG, a_photo


def test_files_keep_only_newest(tmp_path):
    files = TracePhotoFiles(tmp_path, timed_kept=2, steps_kept=2)
    for n in range(3):
        files.add(a_photo(f'p{n}', 100.0 + n, why='every'), JPEG + bytes([n]))
    assert [p.id for p in files.photos('cup')] == ['p1', 'p2']
    assert sorted(p.name for p in (tmp_path / 'cup').glob('*.jpg')) == ['p1.jpg', 'p2.jpg']
    assert files.jpeg('p0') is None
    assert files.jpeg('p2') == JPEG + bytes([2])


def test_the_pictures_are_still_there_after_a_restart(tmp_path):
    TracePhotoFiles(tmp_path, timed_kept=3, steps_kept=3).add(a_photo('p1', 100.0, why='joined'), JPEG)
    again = TracePhotoFiles(tmp_path, timed_kept=3, steps_kept=3)
    assert again.photos('cup') == [a_photo('p1', 100.0, why='joined')]
    assert again.jpeg('p1') == JPEG


def test_a_word_with_a_slash_or_a_space_gets_a_plain_folder(tmp_path):
    files = TracePhotoFiles(tmp_path, timed_kept=3, steps_kept=3)
    files.add(a_photo('p1', 100.0, label='pen/pencil'), JPEG)
    files.add(a_photo('p2', 100.0, label='cell phone'), JPEG)
    assert (tmp_path / 'pen-pencil' / 'p1.jpg').is_file()
    assert (tmp_path / 'cell-phone' / 'p2.jpg').is_file()
    assert [p.label for p in files.photos('pen/pencil')] == ['pen/pencil']


def test_an_id_that_is_not_a_plain_name_finds_nothing(tmp_path):
    (tmp_path / 'secret.jpg').write_bytes(JPEG)
    files = TracePhotoFiles(tmp_path / 'photos', timed_kept=3, steps_kept=3)
    assert files.jpeg('../secret') is None
    assert files.jpeg('') is None


def test_a_broken_index_counts_as_no_pictures(tmp_path, caplog):
    files = TracePhotoFiles(tmp_path, timed_kept=3, steps_kept=3)
    files.add(a_photo('p1', 100.0), JPEG)
    (tmp_path / 'cup' / 'index.json').write_text('{not json')
    assert files.photos('cup') == []
    files.add(a_photo('p2', 200.0), JPEG)                   # it starts a new index...
    assert [p.id for p in files.photos('cup')] == ['p2']
    assert [p.name for p in (tmp_path / 'cup').glob('*.jpg')] == ['p2.jpg']     # ...and the lost picture goes
    assert 'index.json' in caplog.text


def test_an_entry_that_does_not_read_is_skipped(tmp_path):
    files = TracePhotoFiles(tmp_path, timed_kept=3, steps_kept=3)
    files.add(a_photo('p1', 100.0), JPEG)
    index = tmp_path / 'cup' / 'index.json'
    index.write_text(json.dumps([*json.loads(index.read_text()), {'id': 'p2', 'box': 'not a box'}]))
    assert [p.id for p in files.photos('cup')] == ['p1']


def test_a_picture_id_must_be_a_plain_name(tmp_path):
    with pytest.raises(ValueError, match='plain'):
        TracePhotoFiles(tmp_path, timed_kept=3, steps_kept=3).add(a_photo('../p1', 100.0), JPEG)


def test_files_keep_the_steps_apart_from_the_timed_pictures(tmp_path):
    files = TracePhotoFiles(tmp_path, timed_kept=1, steps_kept=1)
    files.add(a_photo('a', 100.0, why='appeared'), JPEG)
    files.add(a_photo('e1', 110.0, why='every'), JPEG)
    files.add(a_photo('e2', 120.0, why='every'), JPEG)
    assert [p.id for p in files.photos('cup')] == ['a', 'e2']
    assert sorted(p.name for p in (tmp_path / 'cup').glob('*.jpg')) == ['a.jpg', 'e2.jpg']
