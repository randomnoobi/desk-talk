"""Per-picture lines → "the cup was on the desk from … to …" intervals."""

from __future__ import annotations

from desktalk.adapters.storage.intervals import build_intervals, merge_intervals


def picture(ts, *labels, conf=0.8, center=(100, 100), size=None):
    """A detections.jsonl line."""
    line = {'type': 'detection', 'ts': ts, 'frame_id': f'f{ts}', 'source': 'pi',
            'detected_obj': [{'label': lab, 'conf': conf, 'center': list(center), 'bbox': [0, 0, 1, 1]}
                             for lab in labels]}
    if size is not None:
        line['size'] = list(size)
    return line


def test_intervals_split_on_gaps():
    t = 1000.0
    frames = [picture(t + i / 3, 'cup') for i in range(30)]                  # 10 s of cup
    frames += [picture(t + 20 + i / 3, 'cup', 'laptop') for i in range(9)]   # cup back after a 10 s gap
    ivs = build_intervals(frames, gap_sec=3)
    cups = [i for i in ivs if i['label'] == 'cup']
    assert len(cups) == 2
    assert cups[0]['interval'][0] == t and round(cups[0]['interval'][1], 2) == round(t + 29 / 3, 2)
    assert cups[0]['frames'] == 30 and cups[0]['avg_conf'] == 0.8 and cups[0]['center'] == [100.0, 100.0]
    assert cups[0]['type'] == 'presence' and cups[0]['source'] == 'pi'
    assert [i['label'] for i in ivs].count('laptop') == 1


def test_same_label_twice_in_one_picture_counts_once():
    ivs = build_intervals([{'type': 'detection', 'ts': 1, 'detected_obj': [
        {'label': 'cup', 'conf': 0.5, 'center': [0, 0]}, {'label': 'cup', 'conf': 0.9, 'center': [10, 10]}]}], 3)
    assert ivs[0]['frames'] == 1 and ivs[0]['avg_conf'] == 0.9 and ivs[0]['center'] == [10.0, 10.0]


def test_merge_joins_touching_intervals():
    a = [{'label': 'cup', 'interval': [0, 10], 'frames': 30, 'avg_conf': 0.8, 'center': [0, 0]}]
    b = [{'label': 'cup', 'interval': [11, 20], 'frames': 27, 'avg_conf': 0.6, 'center': [10, 10]},
         {'label': 'cup', 'interval': [60, 70], 'frames': 30, 'avg_conf': 0.7, 'center': [0, 0]}]
    out = merge_intervals(a, b, gap_sec=3)
    assert [i['interval'] for i in out] == [[0, 20], [60, 70]]
    assert out[0]['frames'] == 57
    assert out[0]['avg_conf'] == round((0.8 * 30 + 0.6 * 27) / 57, 3)
    assert out[0]['center'] == [round(10 * 27 / 57, 1), round(10 * 27 / 57, 1)]


def test_merge_leaves_the_existing_intervals_unchanged():
    a = [{'label': 'cup', 'interval': [0, 10], 'frames': 30, 'avg_conf': 0.8, 'center': [0, 0]}]
    merge_intervals(a, [{'label': 'cup', 'interval': [11, 20], 'frames': 3, 'avg_conf': 0.6, 'center': [0, 0]}], 3)
    assert a == [{'label': 'cup', 'interval': [0, 10], 'frames': 30, 'avg_conf': 0.8, 'center': [0, 0]}]


def test_an_interval_keeps_the_picture_size_it_was_first_seen_at():
    frames = [picture(1, 'cup'), picture(2, 'cup', size=(640, 480)), picture(3, 'cup', size=(1280, 720))]
    [cup] = build_intervals(frames, gap_sec=3)
    assert cup['size'] == [640, 480]
    [cup] = build_intervals([picture(1, 'cup'), picture(2, 'cup', size='big')], gap_sec=3)
    assert 'size' not in cup                     # no picture said its size (or not as a width and height)


def test_merge_keeps_the_size():
    a = [{'label': 'cup', 'interval': [0, 10], 'frames': 3, 'avg_conf': 0.8, 'center': [0, 0], 'size': [640, 480]}]
    b = [{'label': 'cup', 'interval': [11, 20], 'frames': 3, 'avg_conf': 0.8, 'center': [0, 0], 'size': [320, 240]}]
    assert merge_intervals(a, b, gap_sec=3)[0]['size'] == [640, 480]
    old = [{'label': 'cup', 'interval': [0, 10], 'frames': 3, 'avg_conf': 0.8, 'center': [0, 0]}]   # saved before
    assert merge_intervals(old, b, gap_sec=3)[0]['size'] == [320, 240]
    assert 'size' not in merge_intervals(old, [dict(old[0], interval=[11, 20])], gap_sec=3)[0]
