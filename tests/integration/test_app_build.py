"""bootstrap.build(): the whole app from the settings, what it takes from the settings and the overrides,
which background jobs start() runs, and what stop() leaves behind."""

from __future__ import annotations

import json
import logging
import shutil

import pytest

from desktalk.adapters.camera import PhoneFrameSource, PiFrameSource, WebcamSource
from desktalk.adapters.storage import JsonFileStore
from desktalk.bootstrap import build
from desktalk.domain import AgentJoined
from tests.fakes import FakeClock, FakeDetector, FakeFrameSource
from tests.integration.app_kit import (
    AFTERNOON,
    CUP_FAR,
    LAPTOP,
    PAIR_KEY,
    ROOT,
    DeskRig,
    changed,
    desk_config,
    restore_logging,
)


def build_quietly(config, tmp_path, **kw):
    """build() with fakes and nothing written outside tmp_path (no log files, a fixed pair key)."""
    kw = {'clock': FakeClock(AFTERNOON), 'detector': FakeDetector(), 'data_folder': tmp_path / 'run',
          'pair_key': PAIR_KEY, 'set_up_logging': False, 'synchronous_events': True, **kw}
    return build(config, **kw)


# ── the camera ───────────────────────────────────────────────────────
@pytest.mark.parametrize('kind, cls', [('pi', PiFrameSource), ('phone', PhoneFrameSource), ('webcam', WebcamSource)])
def test_the_camera_is_the_one_the_settings_name(tmp_path, kind, cls):
    app = build_quietly(changed(desk_config(tmp_path), 'camera', source=kind), tmp_path)
    assert isinstance(app.source, cls) and app.source.kind == kind
    assert app.pi_source is (app.source if kind == 'pi' else None)
    assert app.phone_source is (app.source if kind == 'phone' else None)


def test_a_camera_source_passed_in_is_used(tmp_path):
    source = FakeFrameSource('webcam')
    app = build_quietly(desk_config(tmp_path), tmp_path, source=source)
    assert app.source is source and app.pi_source is None and app.phone_source is None


def test_the_phone_as_camera_never_counts_as_a_phone_on_the_desk(tmp_path):
    rig = DeskRig(tmp_path, config=changed(desk_config(tmp_path), 'camera', source='phone'))
    rig.watch(4, LAPTOP)
    assert rig.app.habits.today()['phone_free']['counted'] is False


def test_the_pair_key_is_kept_in_the_pair_key_file(tmp_path):
    config = desk_config(tmp_path)
    first = build_quietly(config, tmp_path, pair_key=None).pair_key
    assert (config.resolve(config.camera.pair_key_file)).read_text().strip() == first
    assert build_quietly(config, tmp_path, pair_key=None).pair_key == first
    assert len(first) >= config.camera.pair_key_bytes


# ── where things are kept ────────────────────────────────────────────
def test_everything_the_app_writes_goes_in_the_data_folder(tmp_path):
    rig = DeskRig(tmp_path)
    rig.app.preferences.update({'wake_time': '06:45'})
    rig.watch(1, LAPTOP)
    rig.app.habits.save(force=True)
    data = tmp_path / 'run' / rig.config.storage.folder
    assert json.loads((data / 'preferences.json').read_text())['wake_time'] == '06:45'
    assert (data / 'habits' / '2026-10-21.json').is_file()
    assert (tmp_path / 'run' / rig.config.event_log.folder / '2026-10-21' / 'detections.jsonl').is_file()
    assert rig.app.store.load('preferences')['wake_time'] == '06:45'


def test_preferences_and_habits_saved_before_are_read_back(tmp_path):
    rig = DeskRig(tmp_path)
    rig.app.preferences.update({'bed_time': '22:15'})
    rig.app.habits.log('hydration', 'glass')
    rig.app.habits.save(force=True)
    again = DeskRig(tmp_path)
    assert again.app.preferences.get('bed_time') == '22:15'
    assert again.app.habits.today()['hydration']['water_ml'] == rig.config.habit_tracker.glass_ml


def test_the_danger_rules_come_from_the_config_store(tmp_path):
    rig = DeskRig(tmp_path)
    assert rig.app.safety_rules.loaded and rig.app.analyzer.rules.zones['laptop'].expand == 134
    rig.app.safety_rules.save({'desk_outline': [[0, 0], [1, 0], [1, 0.8], [0, 0.8]]})
    assert rig.app.config_store.load('safety_rules')['desk_outline'][2] == [1, 0.8]


def test_without_a_config_store_the_rules_are_safety_rules_json_in_the_config_folder(tmp_path):
    config = desk_config(tmp_path)
    shutil.copy(ROOT / 'config' / 'safety_rules.json', config.root / 'config' / 'safety_rules.json')
    app = build_quietly(config, tmp_path)
    assert isinstance(app.config_store, JsonFileStore)
    assert app.safety_rules.loaded and app.analyzer.rules.zones['laptop'].expand == 134


def test_things_off_the_marked_desk_never_join(tmp_path):
    rig = DeskRig(tmp_path)
    rig.app.safety_rules.save({'desk_outline': [[0, 0], [0.5, 0], [0.5, 1], [0, 1]]})     # the left half
    rig.watch(4, LAPTOP)                  # it stands at x 200-400 of 640: on the desk
    rig.watch(4, LAPTOP, CUP_FAR)         # the cup is at x 560-600: off the desk
    assert rig.app.roster.member_ids() == ['computer']


# ── the AI ───────────────────────────────────────────────────────────
def test_the_ai_is_the_one_with_a_key_wrapped_so_it_never_stalls_the_app(tmp_path):
    rig = DeskRig(tmp_path, ai_reply='Hello from the fake AI!')
    assert rig.app.model.ready and rig.app.model.label == f'Gemini {rig.config.llm.gemini_model}'
    assert [c.api_key for c in rig.ai_clients] == ['fake-key']
    assert rig.app.generator.answer('group', 'hi')['error']      # nobody on the desk yet, but no crash
    rig.watch(4, LAPTOP)
    reply = rig.app.generator.answer('computer', 'How are you?')
    assert reply['messages'] == [{'character': 'computer', 'text': 'Hello from the fake AI!'}]


def test_without_a_key_the_chat_says_how_to_add_one(tmp_path):
    rig = DeskRig(tmp_path)
    rig.watch(4, LAPTOP)
    assert not rig.app.model.ready
    assert '.env' in rig.app.generator.answer('computer', 'How are you?')['error']


# ── logging ──────────────────────────────────────────────────────────
def test_log_messages_go_to_the_log_folder(tmp_path):
    level = logging.getLogger().level
    try:
        app = build_quietly(changed(desk_config(tmp_path), 'logging', level='INFO'), tmp_path,
                            set_up_logging=True)
        logging.getLogger('desktalk.test').info('hello from the test')
        assert app.log_file == tmp_path / 'run' / 'data' / 'logs' / 'desktalk.log'
        assert 'hello from the test' in app.log_file.read_text()
    finally:
        restore_logging(level)


# ── start() and stop() ───────────────────────────────────────────────
def test_start_runs_every_background_job_after_its_first_wait(tmp_path):
    config = desk_config(tmp_path)
    jobs = {job.name: start_after for job, start_after in build_quietly(config, tmp_path).background_jobs()}
    assert jobs == {
        'detection': 0, 'scheduler': 0,
        'rule engine': config.rule_engine.check_every_sec,
        'habit reminders': config.reminders.check_every_sec,
        'idle chat': config.idle_chat.start_delay_sec,
        'lost items': config.lost_items.start_delay_sec,
        'lens': config.lens.check_every_sec,
        'daily recap': config.daily_summary.start_delay_sec,
        'vision habits': config.vision_habits.start_delay_sec,
    }


def test_with_ollama_a_job_keeps_its_model_loaded(tmp_path):
    config = desk_config(tmp_path, ollama=True)
    jobs = dict((job.name, start_after) for job, start_after in build_quietly(config, tmp_path).background_jobs())
    assert jobs['ollama keep-alive'] == config.llm.ollama_keepalive_start_sec


def test_the_scheduler_compacts_the_log_and_saves_the_habits(tmp_path):
    rig = DeskRig(tmp_path)
    rig.watch(3, LAPTOP)
    every = {job['name']: job['every_sec'] for job in rig.app.scheduler.status()}
    assert every == {'compact event log': rig.config.event_log.compact_every_min * 60,
                     'delete old event-log days': rig.config.event_log.prune_every_min * 60,
                     'save habit day': rig.config.habit_tracker.save_check_every_sec,
                     'save object states': rig.config.object_states.snapshot_every_sec}
    rig.tick(rig.app.scheduler, after=rig.config.event_log.compact_every_min * 60)
    day = rig.app.event_log.days()[0]
    assert rig.app.event_log.recent_detections(day, 10) == []
    assert {i['label'] for i in rig.app.event_log.presence(day, include_live=False)} == {'laptop', 'person'}
    assert rig.app.store.load('habits/2026-10-21')['day'] == '2026-10-21'


def test_stop_saves_the_habits_compacts_the_log_and_closes_the_event_stream(tmp_path):
    rig = DeskRig(tmp_path)
    rig.watch(4, LAPTOP)
    assert rig.app.stop(timeout=1) == []
    day = rig.app.event_log.days()[0]
    assert rig.app.store.load('habits/2026-10-21')['objects_seen'] == ['Computer']
    assert rig.app.event_log.recent_detections(day, 10) == []
    assert {i['label'] for i in rig.app.event_log.presence(day, include_live=False)} == {'laptop', 'person'}
    joined = len(rig.app.event_log.events(day, {'agent_joined'}, 10))
    rig.app.bus.publish(AgentJoined(ts=rig.clock.now(), agent='cup', name='Cup', label='cup', conf=0.9))
    assert len(rig.app.event_log.events(day, {'agent_joined'}, 10)) == joined      # closed: nothing more is logged


def test_a_failing_compaction_at_stop_still_closes_the_event_stream(tmp_path, caplog):
    rig = DeskRig(tmp_path)
    rig.watch(1, LAPTOP)

    def broken(now):
        raise OSError('disk full')
    rig.app.event_log.compact = broken
    with caplog.at_level(logging.ERROR):
        rig.app.stop(timeout=1)
    assert 'disk full' in caplog.text
    rig.app.bus.publish(AgentJoined(ts=rig.clock.now(), agent='cup', name='Cup', label='cup', conf=0.9))
    day = rig.app.event_log.days()[0]
    assert rig.app.event_log.events(day, {'agent_joined'}, 10) == []
