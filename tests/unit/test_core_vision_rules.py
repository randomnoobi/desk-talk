"""The danger rules in use: config/settings.yaml → safety, with what the Settings page saved on top."""

import json
import logging

import pytest

from desktalk.core.chat.workers import DeskLayout
from desktalk.core.vision.rules import SafetyRules, Zone, make_rules
from tests.fakes import InMemoryKeyValueStore
from tests.unit.test_core_vision_kit import ROOT, app_config

SAFETY = app_config().safety
SAVED_FILE = json.loads((ROOT / 'config' / 'safety_rules.json').read_text())
OUTLINE = [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9]]


def test_built_in_rules_when_nothing_was_saved():
    rules = make_rules(SAFETY)
    assert rules.zones['laptop'] == Zone(expand=80, label='Spill / pressure zone', base_share=0.5)
    assert (rules.edge_danger_px, rules.edge_warn_px) == (35, 75)
    assert rules.outline == ()


def test_every_built_in_rule_keeps_the_value_the_old_code_had():
    rules = make_rules(SAFETY)
    assert rules.zones == {'laptop': Zone(80, 'Spill / pressure zone', 0.5),
                           'keyboard': Zone(60, 'Crumb / debris zone', 1.0),
                           'book': Zone(45, 'Stacking zone', 1.0), 'cell phone': Zone(35, 'Screen scratch zone', 1.0)}
    assert rules.zone_intruders['cell phone'] == {'cup', 'bottle', 'wine glass', 'scissors', 'knife'}
    assert (rules.crowd_radius_px, rules.crowd_min_objects, rules.path_ratio) == (140, 3, 0.30)
    assert (rules.approach_min_frames, rules.approach_rate, rules.repeated_times) == (8, 0.12, 3)
    assert rules.history_length == 30
    assert rules.risk_weights == {'edge_danger': 2, 'edge_warning': 1, 'zone_intrusion': 2, 'chain_reaction': 3,
                                  'crowd': 1, 'approaching': 2, 'repeated_intrusion': 2}
    assert rules.harmless_crowd == {'book', 'laptop', 'keyboard', 'mouse', 'cell phone', 'remote', 'tv'}
    assert rules.desk_border_share == 0.03
    assert rules.edge_objects == {'cup', 'bottle', 'cell phone', 'laptop', 'remote', 'mouse'}


def test_where_a_thing_stands_is_the_bottom_strip_of_its_box():
    rules = make_rules(SAFETY)                      # footprint_share 0.2, at least footprint_min_px 4
    assert rules.standing_strip((0, 0, 10, 100)) == (0, 80, 10, 100)
    assert rules.standing_strip((0, 0, 10, 10)) == (0, 6, 10, 10)


def test_saved_zone_wins_over_the_built_in_one():
    rules = make_rules(SAFETY, SAVED_FILE)
    assert rules.zones['laptop'] == Zone(expand=134, label='Spill / pressure zone', base_share=0.5)


def test_a_saved_zone_without_base_share_takes_the_built_in_one():
    rules = make_rules(SAFETY, {'danger_zones': {'laptop': {'expand': 100, 'label': 'Mine'}}})
    assert rules.zones['laptop'] == Zone(100, 'Mine', 0.5)


def test_a_saved_base_share_wins():
    rules = make_rules(SAFETY, {'danger_zones': {'laptop': {'expand': 100, 'label': 'Mine', 'base_share': 0.3}}})
    assert rules.zones['laptop'].base_share == 0.3


@pytest.mark.parametrize('bad', [0, -0.5, 1.5, 'half', None, float('nan')])
def test_a_bad_saved_base_share_falls_back_and_is_logged(caplog, bad):
    with caplog.at_level(logging.WARNING):
        rules = make_rules(SAFETY, {'danger_zones': {'laptop': {'expand': 100, 'label': 'Mine', 'base_share': bad},
                                                     'mouse': {'expand': 20, 'label': 'Mouse', 'base_share': bad}}})
    assert rules.zones['laptop'] == Zone(100, 'Mine', 0.5)
    assert rules.zones['mouse'] == Zone(20, 'Mouse', 1.0)
    assert 'base_share' in caplog.text


def test_a_saved_zone_with_no_built_in_one_covers_the_whole_box(caplog):
    with caplog.at_level(logging.WARNING):
        rules = make_rules(SAFETY, {'danger_zones': {'mouse': {'expand': 20, 'label': 'Mouse zone'}}})
    assert rules.zones['mouse'] == Zone(20, 'Mouse zone', 1.0)
    assert caplog.text == ''


def test_built_in_zones_stay_when_the_saved_rules_leave_them_out():
    rules = make_rules(SAFETY, {'danger_zones': {'cup': {'expand': 20, 'label': 'Cup zone'}}})
    assert set(rules.zones) == {'laptop', 'keyboard', 'book', 'cell phone', 'cup'}


def test_a_broken_saved_zone_is_skipped_and_logged(caplog):
    with caplog.at_level(logging.WARNING):
        rules = make_rules(SAFETY, {'danger_zones': {'cup': {'label': 'no size'}}})
    assert 'cup' not in rules.zones
    assert 'cup' in caplog.text


def test_saved_edge_distances_win():
    rules = make_rules(SAFETY, {'edge_proximity': {'danger_px': 20, 'warn_px': 50}})
    assert (rules.edge_danger_px, rules.edge_warn_px) == (20, 50)


def test_saved_edge_objects_are_added_to_the_built_in_ones():
    rules = make_rules(SAFETY, {'edge_proximity': {'objects': ['vase']}})
    assert rules.edge_objects == {'cup', 'bottle', 'cell phone', 'laptop', 'remote', 'mouse', 'vase'}


def test_a_bad_saved_number_keeps_the_built_in_value(caplog):
    with caplog.at_level(logging.WARNING):
        rules = make_rules(SAFETY, {'edge_proximity': {'danger_px': 'close'}})
    assert rules.edge_danger_px == 35
    assert 'danger_px' in caplog.text


def test_saved_chain_and_temporal_settings_win():
    rules = make_rules(SAFETY, {'chain_reaction': {'crowd_radius_px': 99, 'crowd_min_objects': 4},
                                'temporal': {'approach_min_frames': 5, 'approach_rate': 0.2,
                                             'repeated_intrusion_threshold': 2}})
    assert (rules.crowd_radius_px, rules.crowd_min_objects) == (99, 4)
    assert (rules.approach_min_frames, rules.approach_rate, rules.repeated_times) == (5, 0.2, 2)


def test_desk_outline_needs_three_corners():
    assert make_rules(SAFETY, {'desk_outline': OUTLINE}).outline == ((0.1, 0.1), (0.9, 0.1), (0.9, 0.9))
    assert make_rules(SAFETY, {'desk_outline': OUTLINE[:2]}).outline == ()


def test_camera_tilt_is_remembered_only_with_a_desk_outline():
    tilt = {'camera_reference': {'gravity': [0.1, -9.7, 0.4]}}
    assert make_rules(SAFETY, {**tilt, 'desk_outline': OUTLINE}).reference_gravity == (0.1, -9.7, 0.4)
    assert make_rules(SAFETY, tilt).reference_gravity is None


def test_the_rules_say_whether_the_desk_is_marked_and_how_the_phone_was_tilted_for_lens():
    rules = SafetyRules(InMemoryKeyValueStore(), SAFETY)
    assert isinstance(rules, DeskLayout)
    assert not rules.has_outline() and rules.reference_gravity() is None
    rules.save({'desk_outline': OUTLINE, 'camera_reference': {'gravity': [0.1, -9.7, 0.4]}})
    assert rules.has_outline() and rules.reference_gravity() == (0.1, -9.7, 0.4)


def test_drink_and_food_words_from_the_agents_count_too():
    rules = make_rules(SAFETY, liquids={'Mug'}, foods={'cookies'})
    assert {'mug', 'cup', 'cookies', 'banana'} <= rules.zone_intruders['laptop']
    assert 'mug' in rules.zone_intruders['cell phone'] and 'cookies' not in rules.zone_intruders['cell phone']
    assert {'scissors', 'knife'} <= rules.zone_intruders['cell phone']


def test_saved_intruder_list_replaces_one_zone():
    rules = make_rules(SAFETY, {'zone_danger_intruders': {'laptop': ['Cup']}})
    assert rules.zone_intruders['laptop'] == {'cup'}
    assert 'banana' in rules.zone_intruders['keyboard']


# ── SafetyRules: the saved rules go through a KeyValueStore ──
def test_rules_are_read_from_the_store():
    rules = SafetyRules(InMemoryKeyValueStore({'safety_rules': SAVED_FILE}), SAFETY)
    assert rules.loaded and rules.current.zones['laptop'].expand == 134


def test_without_saved_rules_the_built_in_ones_are_used():
    rules = SafetyRules(InMemoryKeyValueStore(), SAFETY)
    assert not rules.loaded and rules.current.zones['laptop'].expand == 80


def test_saving_merges_into_the_saved_rules_and_takes_effect_at_once():
    store = InMemoryKeyValueStore({'safety_rules': SAVED_FILE})
    rules = SafetyRules(store, SAFETY)
    rules.save({'desk_outline': OUTLINE})
    assert rules.current.outline and rules.current.zones['laptop'].expand == 134
    assert store.data['safety_rules']['desk_outline'] == OUTLINE
    assert store.data['safety_rules']['danger_zones'] == SAVED_FILE['danger_zones']


def test_the_first_save_makes_the_rules_loaded():
    rules = SafetyRules(InMemoryKeyValueStore(), SAFETY)
    rules.save({'edge_proximity': {'danger_px': 10}})
    assert rules.loaded and rules.current.edge_danger_px == 10


def test_the_store_key_can_be_chosen():
    rules = SafetyRules(InMemoryKeyValueStore({'desk/rules': SAVED_FILE}), SAFETY, key='desk/rules')
    assert rules.loaded


def test_outline_from_the_settings_page_is_cleaned():
    rules = SafetyRules(InMemoryKeyValueStore(), SAFETY)
    assert rules.clean_outline([[-0.2, 0.123456], ['0.5', 1.7], [0.9, 0.9]]) == [[0.0, 0.1235], [0.5, 1.0], [0.9, 0.9]]
    assert rules.clean_outline([]) == []


def test_outline_keeps_at_most_max_desk_corners():
    rules = SafetyRules(InMemoryKeyValueStore(), SAFETY)
    corners = [[i / 20, 0.5] for i in range(15)]
    assert len(rules.clean_outline(corners)) == SAFETY.max_desk_corners == 12


@pytest.mark.parametrize('points, says', [
    ([[0.1, 0.1], [0.5, 0.5]], 'Mark at least 3 corners of the desk'),
    ([[0.1, 'x'], [0.5, 0.5], [0.2, 0.2]], 'desk_outline must be a list of [x, y] points'),
    ([[0.1], [0.5, 0.5], [0.2, 0.2]], 'desk_outline must be a list of [x, y] points'),
])
def test_bad_outline_says_what_is_wrong(points, says):
    rules = SafetyRules(InMemoryKeyValueStore(), SAFETY)
    with pytest.raises(ValueError, match=says.replace('[', r'\[').replace(']', r'\]')):
        rules.clean_outline(points)


def test_settings_page_sees_the_saved_rules():
    page = SafetyRules(InMemoryKeyValueStore({'safety_rules': SAVED_FILE}), SAFETY).for_settings_page()
    assert page['loaded'] is True
    assert page['danger_zones'] == SAVED_FILE['danger_zones']
    assert page['edge_proximity'] == SAVED_FILE['edge_proximity']
    assert page['chain_reaction'] == SAVED_FILE['chain_reaction'] and page['desk_outline'] == []


def test_settings_page_sees_the_built_in_rules_when_nothing_was_saved():
    page = SafetyRules(InMemoryKeyValueStore(), SAFETY).for_settings_page()
    assert page['loaded'] is False
    # No base_share: the page doesn't show it but sends zones back as they are, so it would be saved
    # and a later change to it in settings.yaml would do nothing.
    assert page['danger_zones']['laptop'] == {'expand': 80, 'label': 'Spill / pressure zone'}
    assert page['edge_proximity'] == {'danger_px': 35, 'warn_px': 75,
                                      'objects': ['bottle', 'cell phone', 'cup', 'laptop', 'mouse', 'remote']}
    assert page['chain_reaction'] == {} and page['temporal'] == {}


@pytest.mark.parametrize('section, key, field', [
    ('temporal', 'history_length', 'history_length'),
    ('temporal', 'approach_min_frames', 'approach_min_frames'),
    ('temporal', 'repeated_intrusion_threshold', 'repeated_times'),
    ('chain_reaction', 'crowd_min_objects', 'crowd_min_objects'),
])
@pytest.mark.parametrize('bad', [0, -1])
def test_a_saved_count_below_1_keeps_the_built_in_value(caplog, section, key, field, bad):
    built_in = getattr(make_rules(SAFETY), field)
    with caplog.at_level(logging.WARNING):
        rules = make_rules(SAFETY, {section: {key: bad}})
    assert getattr(rules, field) == built_in
    assert key in caplog.text


@pytest.mark.parametrize('section, key, field', [
    ('edge_proximity', 'danger_px', 'edge_danger_px'),
    ('chain_reaction', 'crowd_radius_px', 'crowd_radius_px'),
    ('temporal', 'repeated_window_sec', 'repeated_window_sec'),
])
@pytest.mark.parametrize('bad', [-5, float('nan'), float('inf')])
def test_a_saved_negative_or_endless_number_keeps_the_built_in_value(caplog, section, key, field, bad):
    built_in = getattr(make_rules(SAFETY), field)
    with caplog.at_level(logging.WARNING):
        rules = make_rules(SAFETY, {section: {key: bad}})
    assert getattr(rules, field) == built_in
    assert key in caplog.text


def test_the_analyzer_starts_and_runs_with_a_saved_history_length_below_1():
    """deque(maxlen=-1) raised on every picture, and again at startup."""
    from desktalk.core.vision.analyzer import DeskAnalyzer

    rules = SafetyRules(InMemoryKeyValueStore({'safety_rules': {'temporal': {'history_length': -1}}}), SAFETY)
    analyzer = DeskAnalyzer(rules)
    assert analyzer.analyze({}, (640, 480), 1.0)['risk_level'] == 0
