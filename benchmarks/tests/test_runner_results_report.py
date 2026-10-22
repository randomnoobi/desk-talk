import pytest

from deskbench import report, results
from deskbench.matrix import Combo
from deskbench.runner import run_all

A = Combo('yolo11n', 320, 'pt', 'fp32')
B = Combo('yolo11n', 320, 'ncnn', 'fp16')
C = Combo('yolo11s', 640, 'onnx', 'int8')


def test_rows_are_written_one_at_a_time_and_read_back(tmp_path):
    path = tmp_path / 'run.csv'
    results.append_row(path, {'combo': 'x', 'status': 'ok', 'fps': 3.5})
    results.append_row(path, {'combo': 'y', 'status': 'failed', 'error': 'boom'})
    rows = results.read_rows(path)
    assert [r['combo'] for r in rows] == ['x', 'y']
    assert rows[0]['fps'] == '3.5'
    assert results.finished_keys(path) == {'x'}


def test_resuming_an_older_file_keeps_its_columns_lined_up(tmp_path):
    path = tmp_path / 'run.csv'
    path.write_text('combo,status,throttled,fps\nx,ok,0x0,3.5\n')     # made before the columns changed
    results.append_row(path, {'combo': 'y', 'status': 'ok', 'fps': 4.0, 'cpu_mhz_min': 2400.0})
    rows = results.read_rows(path)
    assert [(r['combo'], r['fps']) for r in rows] == [('x', '3.5'), ('y', '4')]
    assert rows[1]['throttled'] == ''


def test_unknown_column_is_refused(tmp_path):
    with pytest.raises(KeyError):
        results.append_row(tmp_path / 'run.csv', {'combo': 'x', 'colour': 'red'})


def test_a_failed_combination_is_recorded_and_the_run_goes_on(tmp_path):
    path = tmp_path / 'run.csv'

    def run_combo(combo):
        if combo == B:
            raise RuntimeError('ncnn export failed')
        return {'fps': 10.0}

    run_all([A, B, C], run_combo, path, base={'run_id': 'r1', 'device': 'pi'}, cooldown=lambda: {}, log=lambda m: None)
    rows = results.read_rows(path)
    assert [(r['combo'], r['status']) for r in rows] == [(A.key, 'ok'), (B.key, 'failed'), (C.key, 'ok')]
    assert 'ncnn export failed' in rows[1]['error']
    assert rows[2]['format'] == 'onnx' and rows[2]['precision'] == 'int8' and rows[2]['device'] == 'pi'


def test_resume_skips_what_already_worked(tmp_path):
    path = tmp_path / 'run.csv'
    results.append_row(path, {'combo': A.key, 'status': 'ok'})
    results.append_row(path, {'combo': B.key, 'status': 'failed'})
    ran = []
    run_all([A, B], lambda c: ran.append(c) or {}, path, base={}, cooldown=lambda: {}, log=lambda m: None)
    assert ran == [B]


def row(combo, fps, p95, acc, run='r2', device='pi', status='ok'):
    return {'run_id': run, 'device': device, 'combo': combo, 'model': combo.split('-')[0], 'status': status,
            'fps': str(fps), 'total_ms_p95': str(p95), 'map50_95': str(acc)}


def test_best_pick_is_the_fastest_that_is_fast_enough_and_nearly_as_accurate_as_the_best():
    rows = [row('m-640', 1.0, 900, 0.50),      # most accurate, but too slow for 3 pictures a second
            row('s-480', 4.0, 300, 0.47),      # fast enough and within 90% of 0.50
            row('n-320', 20.0, 60, 0.30),      # fastest, but well under 90% of the best accuracy
            row('s-320', 6.0, 200, 0.46)]      # fast enough, within 90%, and faster than s-480 → picked
    best = report.pick_best(rows, camera_fps=3, min_accuracy_share=0.9)
    assert best['combo'] == 's-320'


def test_no_pick_when_nothing_is_fast_enough():
    assert report.pick_best([row('m', 1, 900, 0.5)], camera_fps=3, min_accuracy_share=0.9) is None


def test_without_accuracy_the_pick_is_the_fastest_within_budget():
    rows = [row('a', 4, 250, ''), row('b', 9, 120, '')]
    assert report.pick_best(rows, camera_fps=3, min_accuracy_share=0.9)['combo'] == 'b'


def test_report_merges_runs_of_a_device():
    rows = [row('old', 1, 100, 0.4, run='2026-10-01_pi'),
            row('both', 1, 100, 0.4, run='2026-10-01_pi'),
            row('new', 2, 100, 0.4, run='2026-10-21_pi'),
            row('both', 7, 100, 0.4, run='2026-10-21_pi'),
            row('lap', 50, 20, 0.4, run='2026-10-21_laptop', device='laptop'),
            row('bad', 0, 0, 0, run='2026-10-21_pi', status='failed')]
    md = report.render(rows, camera_fps=3, min_accuracy_share=0.9)
    assert '## pi' in md and '## laptop' in md
    pi_part = md.split('## pi')[1].split('## ')[0]
    assert '`old`' in pi_part and '`new`' in pi_part                 # both runs side by side
    assert '| `both` | 7.0 |' in pi_part and '| `both` | 1.0 |' not in pi_part   # the newer row wins
    assert '2026-10-01_pi' in md and '2026-10-21_pi' in md.split('## History')[1]
    assert 'failed' in pi_part


def test_merged_rows_keep_the_newest_row_per_combination():
    runs = {'b': [{'combo': 'x', 'fps': '2'}], 'a': [{'combo': 'x', 'fps': '1'}, {'combo': 'y', 'fps': '3'}]}
    assert sorted((r['combo'], r['fps']) for r in report.merged_rows(runs)) == [('x', '2'), ('y', '3')]


def desk(combo, score, your=None, kind='k', **kinds):
    model = combo.rsplit('-', 3)[0]
    return {'combo': combo, 'model': model, 'kind': kind, 'status': 'ok', 'desk_score': str(score),
            'desk_score_low': str(score), 'your_desk_score': '' if your is None else str(your),
            'image_size': combo.rsplit('-', 3)[1], 'format': 'ncnn', 'precision': 'fp16',
            **{f'score_{k}': str(v) for k, v in kinds.items()}}


def test_three_picks_are_fast_enough_and_different_models():
    pi = [row('a-320-ncnn-fp16', 9, 120, ''), row('a-480-ncnn-fp16', 5, 250, ''),
          row('b-320-ncnn-fp16', 9, 120, ''), row('c-640-ncnn-fp16', 1, 900, ''), row('d-320-ncnn-fp16', 4, 300, ''),
          row('e-320-ncnn-fp16', 4, 300, '')]
    desks = [desk('a-320-ncnn-fp16', 0.5), desk('a-480-ncnn-fp16', 0.8), desk('b-320-ncnn-fp16', 0.7),
             desk('c-640-ncnn-fp16', 0.99),            # best on the desk, but far too slow on the Pi
             desk('d-320-ncnn-fp16', 0.6), desk('e-320-ncnn-fp16', 0.1)]
    picks = report.pick_three(desks, pi, camera_fps=3)
    assert [p['combo'] for p in picks] == ['a-480-ncnn-fp16', 'b-320-ncnn-fp16', 'd-320-ncnn-fp16']
    assert picks[0]['fps'] == '5' and picks[0]['total_ms_p95'] == '250'


def test_fewer_than_three_when_fewer_qualify():
    pi = [row('a-320-ncnn-fp16', 9, 120, ''), row('b-320-ncnn-fp16', 1, 900, '')]
    desks = [desk('a-320-ncnn-fp16', 0.5), desk('b-320-ncnn-fp16', 0.9), desk('z-320-ncnn-fp16', 0.9)]
    assert [p['combo'] for p in report.pick_three(desks, pi, camera_fps=3)] == ['a-320-ncnn-fp16']


INFO = {'runs': ['2026-10-22T0600Z_laptop'], 'hardware': 'Mac', 'kinds': ['phone', 'trash'],
        'scenes': ['covering', 'tidy'], 'counts': {'phone': (96, 144), 'trash': (4, 236)}, 'your_photos': 6,
        'gap': 0.15, 'few': 15, 'low': 0.25, 'thresholds': {'phone': 0.45, 'trash': 0.45},
        'words_kinds': ['yoloe-26'], 'pick_device': 'pi', 'pictures': 240, 'weak': 0.4, 'min_photos': 5}


def test_pick_flags_worse_on_your_desk_and_says_how_to_try_it():
    pi = [row('a-480-ncnn-fp16', 5, 250, '', device='pi'), row('e-480-ncnn-fp16', 5, 250, '', device='pi')]
    desks = [desk('a-480-ncnn-fp16', 0.8, your=0.5, phone=0.9, trash=0.0),
             desk('e-480-ncnn-fp16', 0.7, your=0.7, kind='yoloe-26', phone=0.8, trash=0.4)]
    md = report.render(pi, 3, 0.9, desk_rows=desks, desk_info=INFO)
    picks = md.split('## Three picks to try')[1].split('\n## ')[0]
    first, second = picks.split('### ')[1:3]
    assert 'worse on your desk' in first and 'worse on your desk' not in second
    assert 'model: models/bench/a.pt' in first and 'image_size: 480' in first
    assert 'model: models/bench/e-desk.pt' in second
    assert 'Best at: phone' in first and 'trash' in first


def test_desk_section_says_when_there_are_few_photos():
    md = report.render([], 3, 0.9, desk_rows=[desk('a-480-ncnn-fp16', 0.8, phone=0.9, trash=0.0)], desk_info=INFO)
    part = md.split('## Desk test')[1]
    assert 'trash 4 / 236 (only 4 photos)' in part and 'phone 96 / 144' in part
    assert '(only 96' not in part


def test_no_picks_yet_says_so():
    md = report.render([], 3, 0.9, desk_rows=[desk('a-480-ncnn-fp16', 0.8)], desk_info=INFO)
    assert 'Not timed on the Pi yet' in md.split('## Three picks to try')[1]


def test_report_has_no_doubled_blank_lines():
    pi = [row('a-480-ncnn-fp16', 5, 250, '', device='pi')]
    desks = [desk('a-480-ncnn-fp16', 0.8, phone=0.9, trash=0.0), desk('z-480-ncnn-fp16', 0.7, phone=0.5, trash=0.1)]
    assert '\n\n\n' not in report.render(pi, 3, 0.9, desk_rows=desks, desk_info=INFO)


def test_merged_rows_prefer_the_newest_ok_row():
    runs = {'1': [{'combo': 'x', 'status': 'ok', 'fps': '2'}, {'combo': 'y', 'status': 'failed'}],
            '2': [{'combo': 'x', 'status': 'failed'}, {'combo': 'y', 'status': 'failed', 'error': 'again'}]}
    merged = {r['combo']: r for r in report.merged_rows(runs)}
    assert merged['x']['status'] == 'ok' and merged['x']['fps'] == '2'    # a later failure doesn't hide it
    assert merged['y'].get('error') == 'again'                            # no ok row: the newest failure shows


def test_picks_show_their_own_score_for_every_kind_and_scene():
    pi = [row('a-480-ncnn-fp16', 5, 250, '', device='pi')]
    d = {**desk('a-480-ncnn-fp16', 0.8, phone=0.9, trash=0.05), 'scene_covering': '0.61', 'scene_tidy': '0.7'}
    picks = report.render(pi, 3, 0.9, desk_rows=[d], desk_info=INFO).split('## Three picks to try')[1]
    picks = picks.split('\n## ')[0]
    assert '| pick | phone | trash | covering | tidy |' in picks
    assert '| 1. `a-480-ncnn-fp16` | 0.90 | 0.05 | 0.61 | 0.70 |' in picks


def test_every_model_weak_at_a_kind_is_said_in_words():
    desks = [desk('a-480-ncnn-fp16', 0.8, phone=0.9, trash=0.12), desk('b-480-ncnn-fp16', 0.7, phone=0.8, trash=0.02)]
    part = report.render([], 3, 0.9, desk_rows=desks, desk_info={**INFO, 'weak': 0.4}).split('## Desk test')[1]
    assert 'Every model is weak at trash: the best is 0.12 (`a`).' in part
    assert 'weak at phone' not in part


def test_scene_note_uses_the_configured_photo_minimum():
    part = report.render([], 3, 0.9, desk_rows=[desk('a-480-ncnn-fp16', 0.8)], desk_info={**INFO, 'min_photos': 7})
    assert 'needs 7 photos' in part
