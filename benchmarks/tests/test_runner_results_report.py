import pytest

from deskbench import report, results
from deskbench.cli import DEFAULT_CONFIG, load_config
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


def row(combo, fps, p95, acc, run='r2', device='pi', status='ok', fmt='ncnn'):
    return {'run_id': run, 'device': device, 'combo': combo, 'model': combo.split('-')[0], 'status': status,
            'format': fmt, 'fps': str(fps), 'total_ms_p95': str(p95), 'map50_95': str(acc)}


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


def test_report_uses_the_latest_run_per_device_and_lists_history():
    rows = [row('old', 1, 100, 0.4, run='2026-10-01_pi'),
            row('new', 2, 100, 0.4, run='2026-10-21_pi'),
            row('lap', 50, 20, 0.4, run='2026-10-21_laptop', device='laptop'),
            row('bad', 0, 0, 0, run='2026-10-21_pi', status='failed')]
    md = report.render(rows, camera_fps=3, min_accuracy_share=0.9)
    assert '## pi' in md and '## laptop' in md
    pi_part = md.split('## pi')[1].split('## ')[0]
    assert 'new' in pi_part and '| old' not in pi_part
    assert '2026-10-01_pi' in md                    # still in the history table
    assert 'failed' in pi_part


def test_the_best_pick_is_only_a_format_the_pis_detector_runs():
    rows = [row('s-480-onnx', 30.0, 40, 0.47, fmt='onnx'),        # fastest, but the Pi can't run onnx
            row('s-480-ncnn', 20.0, 60, 0.47, fmt='ncnn'),
            row('s-480-pt', 10.0, 120, 0.47, fmt='pt')]
    assert report.pick_best(rows, camera_fps=3, min_accuracy_share=0.9)['combo'] == 's-480-onnx'
    best = report.pick_best(rows, camera_fps=3, min_accuracy_share=0.9, pick_formats=('ncnn', 'pt'))
    assert best['combo'] == 's-480-ncnn'


def test_the_report_picks_only_from_the_formats_it_is_given_and_says_so():
    rows = [row('s-480-onnx', 30.0, 40, 0.47, fmt='onnx', run='2026-10-21_laptop', device='laptop'),
            row('s-480-ncnn', 20.0, 60, 0.47, fmt='ncnn', run='2026-10-21_laptop', device='laptop')]
    md = report.render(rows, camera_fps=3, min_accuracy_share=0.9, pick_formats=('ncnn', 'pt'))
    assert '**Best pick: `s-480-ncnn`**' in md
    assert '| `2026-10-21_laptop` | laptop | ? | `?` | 2 / 0 | `s-480-ncnn` |' in md      # the history too
    assert 'ncnn or pt' in md


def test_the_formats_to_pick_from_are_in_the_settings():
    cfg = load_config(DEFAULT_CONFIG)
    assert cfg['report']['pick_formats'] == ['ncnn', 'pt']
