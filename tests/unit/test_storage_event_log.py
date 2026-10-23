"""The event log on this computer: data/events/<day>/, per-picture lines compacted into intervals."""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

import pytest

from desktalk.adapters.storage import EventLog
from desktalk.config import EventLogSettings
from desktalk.domain import AgentJoined, Alert, BBox, Compaction, Detection, DetectionEvent, Light
from tests.contracts.event_store_contract import EventStoreContract

NOW = 1_760_000_000.0                          # 9 October 2025


def day_of(ts: float) -> str:
    return time.strftime('%Y-%m-%d', time.localtime(ts))


TODAY = day_of(NOW)
DAY_SEC = 24 * 60 * 60


def picture(ts: float, *labels: str, conf: float = 0.8) -> DetectionEvent:
    """An analysed picture with each label in a box centred on (100, 100)."""
    return DetectionEvent(ts=ts, frame_id=f'pi-{ts}', source='pi', size=(640, 480), person=False,
                          light=Light(brightness=120.0),
                          detections=tuple(Detection(lab, conf, BBox(90, 90, 110, 110)) for lab in labels))


def make_log(folder: Path, **changes) -> EventLog:
    values = {'folder': folder, 'compact_every_min': 10, 'merge_gap_sec': 3, 'keep_days': 30,
              'log_empty_frames': True, 'prune_every_min': 60} | changes
    return EventLog(folder, EventLogSettings(**values))


class TestEventLog(EventStoreContract):
    @pytest.fixture
    def store(self, tmp_path):
        return make_log(tmp_path)


def test_write_compact_and_read(tmp_path):
    log = make_log(tmp_path)
    for i in range(30):
        log.write(picture(NOW - 60 + i / 3, 'cup'))
    log.write(AgentJoined(ts=NOW - 59, agent='cup', name='Cup', label='cup', conf=0.9))
    raw = tmp_path / TODAY / 'detections.jsonl'
    assert raw.is_file() and len(raw.read_text().splitlines()) == 30
    assert log.counts(TODAY)['pictures_waiting'] == 30
    assert [e['type'] for e in log.events(TODAY, None, 10)] == ['agent_joined']
    assert len(log.recent_detections(TODAY, 50)) == 30
    assert len(log.presence(TODAY)) == 1                  # the live view already shows the interval

    summary = log.compact(NOW)
    assert summary == {'frames': 30, 'intervals': 1, 'from': round(NOW - 60, 3), 'to': round(NOW - 60 + 29 / 3, 3)}
    assert log.last_compaction == {'ts': NOW, **summary}
    assert not raw.exists()                                # the per-picture lines are gone
    assert len(log.presence(TODAY, include_live=False)) == 1
    newest = log.events(TODAY, None, 10)[0]
    assert newest == Compaction(ts=NOW, id=newest['id'], frames=30, intervals=1, from_ts=summary['from'],
                                to_ts=summary['to']).to_dict()
    assert log.counts(TODAY) == {'pictures_waiting': 0, 'events': {'agent_joined': 1, 'compaction': 1}}

    # more pictures right after: the next compaction extends the same interval
    for i in range(9):
        log.write(picture(NOW - 50 + i / 3, 'cup'))
    log.compact(NOW + 1)
    ivs = log.presence(TODAY, include_live=False)
    assert len(ivs) == 1 and ivs[0]['frames'] == 39


def test_a_compaction_cut_off_before_the_new_presence_file_is_swapped_in_loses_nothing(tmp_path, monkeypatch):
    log = make_log(tmp_path)
    for i in range(3):
        log.write(picture(NOW - 100 + i, 'cup'))
    log.compact(NOW - 90)
    presence = tmp_path / TODAY / 'presence.jsonl'
    before = presence.read_text()
    for i in range(3):
        log.write(picture(NOW - 10 + i, 'laptop'))
    real_replace = os.replace

    def computer_stops_at_presence(src, dst):
        if Path(dst).name == 'presence.jsonl':
            raise OSError('the disk is full')
        real_replace(src, dst)

    monkeypatch.setattr(os, 'replace', computer_stops_at_presence)
    with pytest.raises(OSError):
        log.compact(NOW)
    monkeypatch.undo()
    assert presence.read_text() == before                  # the old intervals are whole
    assert sorted(i['label'] for i in log.presence(TODAY)) == ['cup', 'laptop']      # the pictures are still there
    log.write(picture(NOW - 5, 'book'))
    log.compact(NOW)                                       # the next compaction picks them all up
    assert sorted(i['label'] for i in log.presence(TODAY, include_live=False)) == ['book', 'cup', 'laptop']
    assert sorted(p.name for p in presence.parent.iterdir()) == ['events.jsonl', 'presence.jsonl']


def test_two_compactions_at_once_count_each_picture_once(tmp_path, monkeypatch):
    """The scheduler's compaction and the Log page's "compact now" button can run at the same moment."""
    from desktalk.adapters.storage import event_log

    log = make_log(tmp_path)
    for i in range(6):
        log.write(picture(NOW - 60 + i, 'cup'))
    first_inside, release = threading.Event(), threading.Event()
    real_build = event_log.build_intervals

    def slow_build(frames, gap):
        if not first_inside.is_set():          # hold the first compaction half-way through
            first_inside.set()
            release.wait(5)
        return real_build(frames, gap)

    monkeypatch.setattr(event_log, 'build_intervals', slow_build)
    results, errors = [], []

    def compact(now):
        try:
            results.append(log.compact(now))
        except Exception as exc:
            errors.append(exc)

    first = threading.Thread(target=compact, args=(NOW,))
    first.start()
    assert first_inside.wait(5)
    second = threading.Thread(target=compact, args=(NOW + 1,))
    second.start()
    second.join(0.2)                           # give the second one the chance to run into the first
    release.set()
    first.join(5), second.join(5)
    assert errors == []
    assert sum(r['frames'] for r in results) == 6
    assert [iv['frames'] for iv in log.presence(TODAY, include_live=False)] == [6]


def test_empty_pictures_can_be_left_out(tmp_path):
    log = make_log(tmp_path, log_empty_frames=False)
    log.write(picture(NOW))                                # nothing detected
    assert log.days() == []
    log.write(picture(NOW, 'cup'))
    assert len(log.recent_detections(TODAY, 10)) == 1


def test_compaction_with_nothing_to_do(tmp_path):
    log = make_log(tmp_path / 'events')
    assert log.compact(NOW) == {'frames': 0, 'intervals': 0, 'from': None, 'to': None}
    assert log.days() == []


def test_broken_lines_are_skipped_and_logged(tmp_path, caplog):
    log = make_log(tmp_path)
    log.write(AgentJoined(ts=NOW, agent='cup', name='Cup', label='cup', conf=0.9))
    log.write(picture(NOW, 'cup'))
    day = tmp_path / TODAY
    for name in ('events.jsonl', 'detections.jsonl'):
        with open(day / name, 'a', encoding='utf-8') as f:
            f.write('{"type": "alert", "ts": 17\n[1, 2]\n')          # cut off by a crash, and not an event
    with caplog.at_level(logging.WARNING):
        assert [e['type'] for e in log.events(TODAY, None, 10)] == ['agent_joined']
        assert len(log.recent_detections(TODAY, 10)) == 1
        assert log.compact(NOW + 1)['frames'] == 1
        assert [i['label'] for i in log.presence(TODAY)] == ['cup']
    assert 'events.jsonl' in caplog.text and 'detections' in caplog.text and '2 broken lines' in caplog.text


def test_a_line_cut_off_by_a_crash_does_not_swallow_the_next_event(tmp_path):
    log = make_log(tmp_path)
    (tmp_path / TODAY).mkdir()
    (tmp_path / TODAY / 'events.jsonl').write_text('{"id": "1-a", "ts": 17600', encoding='utf-8')
    join = AgentJoined(ts=NOW, agent='cup', name='Cup', label='cup', conf=0.9)
    log.write(join)
    assert log.events(TODAY, None, 10) == [join.to_dict()]


def test_prune_deletes_days_older_than_keep_days(tmp_path):
    log = make_log(tmp_path, keep_days=2)
    for days_ago in (-1, 0, 1, 2, 5):                      # -1: a day ahead, after the clock was off
        log.write(Alert(ts=NOW - days_ago * DAY_SEC, kind='proximity'))
    (tmp_path / 'notes').mkdir()                           # not a day: left alone
    assert log.prune(NOW) == [day_of(NOW - 5 * DAY_SEC), day_of(NOW - 2 * DAY_SEC)]
    assert log.days() == [day_of(NOW + DAY_SEC), TODAY, day_of(NOW - DAY_SEC)]
    assert (tmp_path / 'notes').is_dir()


def test_a_day_that_is_not_a_date_reads_as_empty(tmp_path):
    (tmp_path / 'secret').mkdir()
    (tmp_path / 'secret' / 'events.jsonl').write_text('{"type": "alert", "ts": 1}\n', encoding='utf-8')
    log = make_log(tmp_path / 'events')
    log.write(Alert(ts=NOW, kind='proximity'))
    assert log.events('../secret', None, 10) == []
    assert log.presence('../secret') == []
    assert log.recent_detections('../secret', 10) == []
    assert log.counts('../secret') == {'pictures_waiting': 0, 'events': {}}


def test_logs_written_by_the_old_app_still_read(tmp_path):
    day = tmp_path / '2026-10-18'
    day.mkdir()
    (day / 'events.jsonl').write_text(
        '{"id":"1-a1b2c3","ts":1792345600.0,"type":"agent_joined","agent":"cup","name":"Cup","label":"cup",'
        '"conf":0.9}\n'
        '{"id":"2-d4e5f6","ts":1792345700.0,"type":"alert","kind":"proximity","agent":"cup","near":"laptop",'
        '"rule_label":"cup near laptop","snapshot":"s-1"}\n', encoding='utf-8')
    (day / 'presence.jsonl').write_text(
        '{"type":"presence","label":"cup","interval":[1792345678.1,1792347461.9],"frames":5310,"source":"pi",'
        '"avg_conf":0.81,"center":[100.2,101.0]}\n', encoding='utf-8')
    (day / 'detections.jsonl').write_text(
        '{"id":"3-aaaaaa","ts":1792347463.0,"type":"detection","frame_id":"pi-000123","source":"pi",'
        '"detected_obj":[{"label":"cup","conf":0.82,"bbox":[90,90,110,110],"center":[100,100]}]}\n',
        encoding='utf-8')
    log = make_log(tmp_path)
    assert log.days() == ['2026-10-18']
    assert [e['id'] for e in log.events('2026-10-18', None, 10)] == ['2-d4e5f6', '1-a1b2c3']
    assert log.events('2026-10-18', {'alert'}, 10)[0]['near'] == 'laptop'
    assert log.presence('2026-10-18') == [
        {'type': 'presence', 'label': 'cup', 'interval': [1792345678.1, 1792347463.0], 'frames': 5311,
         'source': 'pi', 'avg_conf': 0.81, 'center': [100.2, 101.0]}]
    assert log.recent_detections('2026-10-18', 10)[0]['frame_id'] == 'pi-000123'


def test_the_same_broken_line_is_reported_once_however_often_the_page_reads_it(tmp_path, caplog):
    log = make_log(tmp_path)
    log.write(Alert(ts=NOW, kind='proximity'))
    with open(tmp_path / TODAY / 'events.jsonl', 'a', encoding='utf-8') as f:
        f.write('{"cut off\n')
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            assert len(log.events(TODAY, None, 10)) == 1
    assert len(caplog.records) == 1
