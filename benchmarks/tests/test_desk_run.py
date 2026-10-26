import pytest

from deskbench import results
from deskbench.cli import desk_row, desk_rows, public_pictures, your_pictures
from deskbench.matrix import Combo
from deskbench.runner import run_all

DESK = {'kinds': {'phone': {'coco': ['cell phone']}, 'AirPods': {'coco': []}},
        'low_confidence': 0.25, 'min_photos_per_cell': 1}
COCO = {'names': 'coco'}


def test_desk_columns_order_and_slugs():
    cols = results.desk_columns(['water bottle', 'phone'], ['covering', 'small things'])
    assert cols[:5] == ['run_id', 'device', 'hardware', 'commit', 'combo']
    assert cols.index('score_water_bottle') < cols.index('found_water_bottle') < cols.index('score_phone')
    assert cols.index('there_phone') < cols.index('scene_covering') < cols.index('scene_small_things')
    assert cols[-1] == 'combo_time_s'


def test_desk_row_scores_at_app_threshold_and_low():
    pictures = [{'id': 1, 'answers': {'phone': 'there', 'AirPods': 'not there'}, 'scenes': ['tidy']},
                {'id': 2, 'answers': {'phone': 'not there', 'AirPods': 'there'}, 'scenes': ['tidy']}]
    detections = {'1': [['cell phone', 0.3]], '2': [['person', 0.9]]}
    row = desk_row(COCO, detections, pictures, [], DESK, {'phone': 0.45, 'AirPods': 0.45})
    assert row['pictures'] == 2
    assert row['score_phone'] == 0 and row['found_phone'] == 0
    assert row['desk_score'] == 0                    # phone missed at 0.45, AirPods can't be named
    assert row['desk_score_low'] == pytest.approx(0.5)  # phone found at 0.25, AirPods still 0
    assert row['score_airpods'] == 0 and row['there_airpods'] == 1
    assert row['scene_tidy'] == 0                   # same as the desk score: both kinds score 0 there
    assert row['your_desk_score'] is None


def test_desk_row_leaves_out_a_second_name_for_the_same_box():
    kinds = {'kinds': {'laptop': {'coco': ['laptop']}, 'book': {'coco': ['book']}},
             'low_confidence': 0.25, 'min_photos_per_cell': 1}
    pictures = [{'id': 1, 'answers': {'laptop': 'there', 'book': 'not there'}, 'scenes': ['tidy']}]
    detections = {'1': [['laptop', 0.95, 574, 183, 1094, 713], ['book', 0.52, 574, 180, 1126, 716]]}
    thresholds = {'laptop': 0.45, 'book': 0.45}
    row = desk_row(COCO, detections, pictures, [], kinds, thresholds, one_name=(0.7, frozenset()))
    assert row['false_book'] == 0 and row['found_laptop'] == 1
    assert desk_row(COCO, detections, pictures, [], kinds, thresholds)['false_book'] == 1    # without the rule


def test_desk_row_scores_your_own_photos_separately():
    yours = [{'id': 'desk_01.jpg', 'answers': {'phone': 'there', 'AirPods': 'not there'}, 'scenes': ['your desk']}]
    row = desk_row(COCO, {'desk_01.jpg': [['cell phone', 0.9]]}, [], yours, DESK, {'phone': 0.45, 'AirPods': 0.45})
    assert row['your_desk_score'] == 1.0
    assert row['pictures'] == 0 and row['desk_score'] is None


def test_missing_picture_is_skipped_and_counted(tmp_path):
    (tmp_path / '1.jpg').write_bytes(b'x')
    pictures = [{'id': 1, 'answers': {}}, {'id': 2, 'answers': {}}]
    logged = []
    used = public_pictures(pictures, tmp_path, logged.append)
    assert [p['id'] for p in used] == [1] and used[0]['path'] == tmp_path / '1.jpg'
    assert '1 of 2' in logged[0]


def test_unknown_column_is_refused_with_other_columns(tmp_path):
    with pytest.raises(KeyError):
        results.append_row(tmp_path / 'run.csv', {'combo': 'x', 'fps': 1.0}, columns=['combo', 'status'])


def test_all_rows_skips_the_desk_folder(tmp_path):
    results.append_row(tmp_path / 'pi' / 'a.csv', {'combo': 'x', 'status': 'ok'})
    results.append_row(tmp_path / 'desk' / 'b.csv', {'combo': 'y', 'status': 'ok', 'desk_score': 0.5},
                       columns=results.desk_columns(['phone'], ['tidy']))
    assert [r['combo'] for r in results.all_rows(tmp_path)] == ['x']


def test_run_all_writes_the_columns_it_is_given(tmp_path):
    path = tmp_path / 'desk.csv'
    cols = results.desk_columns(['phone'], ['tidy'])
    run_all([Combo('yolo11n', 320, 'pt', 'fp32')], lambda c: {'desk_score': 0.75}, path, base={'run_id': 'r'},
            cooldown=lambda: {}, log=lambda m: None, columns=cols)
    [row] = results.read_rows(path)
    assert row['desk_score'] == '0.75' and list(row) == cols


def test_desk_rows_merge_every_desk_run(tmp_path):
    cols = results.desk_columns(['phone'], ['tidy'])
    first, second = tmp_path / 'desk' / '2026-10-01_laptop.csv', tmp_path / 'desk' / '2026-10-02_laptop.csv'
    results.append_row(first, {'combo': 'a', 'status': 'ok', 'desk_score': 0.1}, cols)
    results.append_row(first, {'combo': 'b', 'status': 'ok', 'desk_score': 0.2}, cols)
    results.append_row(second, {'combo': 'b', 'status': 'ok', 'desk_score': 0.9}, cols)
    rows, runs = desk_rows(tmp_path)
    assert sorted((r['combo'], r['desk_score']) for r in rows) == [('a', '0.1'), ('b', '0.9')]
    assert runs == ['2026-10-01_laptop', '2026-10-02_laptop']


def test_missing_photo_of_your_own_is_skipped(tmp_path):
    (tmp_path / 'desk_01.jpg').write_bytes(b'x')
    yours = [{'id': 'desk_01.jpg', 'path': tmp_path / 'desk_01.jpg'},
             {'id': 'desk_7.jpg', 'path': tmp_path / 'desk_7.jpg'}]
    logged = []
    assert [p['id'] for p in your_pictures(yours, logged.append)] == ['desk_01.jpg']
    assert 'desk_7.jpg' in logged[0]


def test_no_desk_photos_at_all_stops_the_run(tmp_path):
    with pytest.raises(RuntimeError, match='desk prepare'):
        public_pictures([{'id': 1, 'answers': {}}], tmp_path, lambda m: None)
