"""The event log: per-picture events → compacted intervals every 10 minutes."""

import time

from desktalk.events.log import EventLog, build_intervals, merge_intervals
from desktalk.events.stream import make_event
from desktalk.util import local_day


def det(ts, *labels, conf=0.8, center=(100, 100)):
    return make_event('detection', ts=ts, frame_id=f'f{ts}', source='pi',
                      detected_obj=[{'label': l, 'conf': conf, 'center': list(center), 'bbox': [0, 0, 1, 1]} for l in labels])


def test_intervals_split_on_gaps():
    t = 1000.0
    frames = [det(t + i / 3, 'cup') for i in range(30)]              # 10 s of cup
    frames += [det(t + 20 + i / 3, 'cup', 'laptop') for i in range(9)]   # cup back after a 10 s gap
    ivs = build_intervals(frames, gap_sec=3)
    cups = [i for i in ivs if i['label'] == 'cup']
    assert len(cups) == 2
    assert cups[0]['interval'][0] == t and round(cups[0]['interval'][1], 2) == round(t + 29 / 3, 2)
    assert cups[0]['frames'] == 30 and cups[0]['avg_conf'] == 0.8 and cups[0]['center'] == [100.0, 100.0]
    assert [i['label'] for i in ivs].count('laptop') == 1


def test_same_label_twice_in_one_picture_counts_once():
    ivs = build_intervals([make_event('detection', ts=1, detected_obj=[
        {'label': 'cup', 'conf': 0.5, 'center': [0, 0]}, {'label': 'cup', 'conf': 0.9, 'center': [10, 10]}])], 3)
    assert ivs[0]['frames'] == 1 and ivs[0]['avg_conf'] == 0.9


def test_merge_joins_touching_intervals():
    a = [{'label': 'cup', 'interval': [0, 10], 'frames': 30, 'avg_conf': 0.8, 'center': [0, 0]}]
    b = [{'label': 'cup', 'interval': [11, 20], 'frames': 27, 'avg_conf': 0.6, 'center': [10, 10]},
         {'label': 'cup', 'interval': [60, 70], 'frames': 30, 'avg_conf': 0.7, 'center': [0, 0]}]
    out = merge_intervals(a, b, gap_sec=3)
    assert [i['interval'] for i in out] == [[0, 20], [60, 70]]
    assert out[0]['frames'] == 57


def test_write_compact_and_read(tmp_path):
    log = EventLog(tmp_path, merge_gap_sec=3)
    now = time.time()
    day = local_day(now)
    for i in range(30):
        log.write(det(now - 60 + i / 3, 'cup'))
    log.write(make_event('agent_joined', ts=now - 59, agent='cup', name='Cup', label='cup'))
    raw = tmp_path / day / 'detections.jsonl'
    assert raw.is_file() and len(raw.read_text().splitlines()) == 30
    assert log.counts(day)['pictures_waiting'] == 30
    assert [e['type'] for e in log.events(day)] == ['agent_joined']
    # live view before compaction already shows the interval
    assert len(log.presence(day)) == 1

    summary = log.compact(now)
    assert summary['frames'] == 30 and summary['intervals'] == 1
    assert not raw.exists()                                        # per-picture lines are gone
    assert len(log.presence(day, include_live=False)) == 1
    assert log.events(day)[0]['type'] == 'compaction'

    # more pictures right after: the next compaction extends the same interval
    for i in range(9):
        log.write(det(now - 50 + i / 3, 'cup'))
    log.compact(now + 1)
    ivs = log.presence(day, include_live=False)
    assert len(ivs) == 1 and ivs[0]['frames'] == 39


def test_compaction_with_nothing_to_do(tmp_path):
    log = EventLog(tmp_path)
    assert log.compact()['frames'] == 0
    assert log.days() == []


def test_empty_frames_can_be_skipped(tmp_path):
    log = EventLog(tmp_path, log_empty_frames=False)
    log.write(det(time.time()))           # nothing detected
    assert log.days() == []


def test_prune_deletes_old_days(tmp_path):
    log = EventLog(tmp_path, keep_days=2)
    now = time.time()
    for days_ago in (0, 1, 5):
        log.write(make_event('alert', ts=now - days_ago * 86400))
    assert len(log.days()) == 3
    removed = log.prune(now)
    assert removed == [local_day(now - 5 * 86400)]
    assert len(log.days()) == 2
