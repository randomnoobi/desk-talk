"""bootstrap.build(): the whole app from the settings, what it takes from the settings and the overrides,
which background jobs start() runs, and what stop() leaves behind."""

from __future__ import annotations

import dataclasses
import json
import logging
import shutil
import typing

import pytest

from desktalk.adapters.camera import PhoneFrameSource, PiFrameSource, WebcamSource
from desktalk.adapters.dev import DataFolder, PytestRunner, SqliteBrowser
from desktalk.adapters.notify.mac import MacNotifications
from desktalk.adapters.storage import JsonFileStore
from desktalk.bootstrap import DeskTalk, build
from desktalk.domain import AgentJoined
from desktalk.ports import SmoothingTraceView
from tests.fakes import FakeClock, FakeDesktopNotifications, FakeDetector, FakeFrameSource, FakeTestRunner
from tests.integration.app_kit import (
    AFTERNOON,
    CUP_FAR,
    LAPTOP,
    PAIR_KEY,
    ROOT,
    DeskRig,
    changed,
    desk_config,
    event_log_file,
    restore_logging,
)


def build_quietly(config, tmp_path, **kw):
    """build() with fakes and nothing written outside tmp_path (no log files, a fixed pair key, no Mac pop-ups)."""
    kw = {'clock': FakeClock(AFTERNOON), 'detector': FakeDetector(), 'data_folder': tmp_path / 'run',
          'pair_key': PAIR_KEY, 'set_up_logging': False, 'synchronous_events': True,
          'desktop': FakeDesktopNotifications(), **kw}
    return build(config, **kw)


# ── the camera ───────────────────────────────────────────────────────
def test_the_build_helper_never_makes_real_mac_pop_ups(tmp_path):
    app = build_quietly(desk_config(tmp_path), tmp_path)
    assert not isinstance(app.desktop, MacNotifications)


@pytest.mark.parametrize('kind, cls', [('pi', PiFrameSource), ('phone', PhoneFrameSource), ('webcam', WebcamSource)])
def test_the_camera_is_the_one_the_settings_name(tmp_path, kind, cls):
    app = build_quietly(changed(desk_config(tmp_path), 'camera', source=kind), tmp_path)
    assert isinstance(app.source, cls) and app.source.kind == kind
    assert app.pi_inbox is app.pi is (app.source if kind == 'pi' else None)
    assert app.phone_inbox is app.phone is (app.source if kind == 'phone' else None)


def test_a_camera_source_passed_in_is_used(tmp_path):
    source = FakeFrameSource('webcam')
    app = build_quietly(desk_config(tmp_path), tmp_path, source=source)
    assert app.source is source
    assert (app.pi_inbox, app.pi, app.phone_inbox, app.phone) == (None, None, None, None)


@pytest.mark.parametrize('kind', ['pi', 'phone', 'webcam'])
def test_every_part_has_the_interface_its_field_names(tmp_path, kind):
    app = build_quietly(changed(desk_config(tmp_path), 'camera', source=kind), tmp_path)
    hints = typing.get_type_hints(DeskTalk)
    checked = []
    for field in dataclasses.fields(DeskTalk):
        value = getattr(app, field.name)
        kinds = [k for k in typing.get_args(hints[field.name]) or [hints[field.name]] if k is not type(None)]
        interfaces = [k for k in kinds if getattr(k, '_is_protocol', False)]
        if value is None or not interfaces:
            continue
        assert any(isinstance(value, k) for k in interfaces), f'{field.name} is not a {interfaces}'
        checked.append(field.name)
    assert {'live_pictures', 'scene', 'safety_board', 'character_memory', 'photo_album', 'bus', 'event_log',
            'hub', 'generator', 'habits', 'producer', 'supervisor'} <= set(checked)


def test_the_phone_as_camera_never_counts_as_a_phone_on_the_desk(tmp_path):
    rig = DeskRig(tmp_path, config=changed(desk_config(tmp_path), 'camera', source='phone'))
    rig.watch(4, LAPTOP)
    assert rig.app.habits.today(rig.clock.now())['phone_free']['counted'] is False


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
    assert again.app.habits.today(again.clock.now())['hydration']['water_ml'] == rig.config.habit_tracker.glass_ml


def test_the_danger_rules_come_from_the_config_store(tmp_path):
    rig = DeskRig(tmp_path)
    assert rig.app.safety_rules.loaded and rig.app.safety_rules.current.zones['laptop'].expand == 134
    rig.app.safety_rules.save({'desk_outline': [[0, 0], [1, 0], [1, 0.8], [0, 0.8]]})
    assert rig.app.config_store.load('safety_rules')['desk_outline'][2] == [1, 0.8]


def test_without_a_config_store_the_rules_are_safety_rules_json_in_the_config_folder(tmp_path):
    config = desk_config(tmp_path)
    shutil.copy(ROOT / 'config' / 'safety_rules.json', config.root / 'config' / 'safety_rules.json')
    app = build_quietly(config, tmp_path)
    assert isinstance(app.config_store, JsonFileStore)
    assert app.safety_rules.loaded and app.safety_rules.current.zones['laptop'].expand == 134


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
        assert app.log_file is not None and app.log_file == tmp_path / 'run' / 'data' / 'logs' / 'desktalk.log'
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
    assert event_log_file(rig.app).recent_detections(day, 10) == []
    assert {i['label'] for i in event_log_file(rig.app).presence(day, include_live=False)} == {'laptop', 'person'}
    assert rig.app.store.load('habits/2026-10-21')['day'] == '2026-10-21'


def test_stop_saves_the_habits_compacts_the_log_and_closes_the_event_stream(tmp_path):
    rig = DeskRig(tmp_path)
    rig.watch(4, LAPTOP)
    assert rig.app.stop(timeout=1) == []
    day = rig.app.event_log.days()[0]
    assert rig.app.store.load('habits/2026-10-21')['objects_seen'] == ['Computer']
    assert event_log_file(rig.app).recent_detections(day, 10) == []
    assert {i['label'] for i in event_log_file(rig.app).presence(day, include_live=False)} == {'laptop', 'person'}
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


# ── the developer page's tools (settings → dev) ──────────────────────
def test_the_developer_tools_are_built_when_the_developer_page_is_on(tmp_path):
    config = desk_config(tmp_path)
    app = build_quietly(config, tmp_path)
    assert isinstance(app.test_runner, PytestRunner)
    assert isinstance(app.databases, SqliteBrowser) and isinstance(app.data_files, DataFolder)
    assert app.databases.databases() == ['object_states']
    assert app.databases.tables('object_states')            # the object states database in the data folder
    app.store.save('preferences', {'wake_time': '06:45'})
    assert 'preferences.json' in {f.path for f in app.data_files.list()}
    assert isinstance(app.object_states, SmoothingTraceView)


def test_with_the_developer_page_off_there_are_no_developer_tools(tmp_path):
    app = build_quietly(changed(desk_config(tmp_path), 'dev', enabled=False), tmp_path)
    assert (app.test_runner, app.databases, app.data_files) == (None, None, None)
    assert app.stop(timeout=1) == []


def test_stop_stops_a_test_run_still_going(tmp_path):
    runner = FakeTestRunner()
    app = build_quietly(desk_config(tmp_path), tmp_path, test_runner=runner)
    assert app.test_runner is runner
    app.stop(timeout=1)
    assert runner.closed
