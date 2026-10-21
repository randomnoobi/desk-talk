"""The danger rules in use: config/settings.yaml → safety, with what the Settings page saved on top."""

import json
import logging

import pytest

from desktalk.core.vision.rules import SafetyRules, Zone, make_rules
from tests.fakes import InMemoryKeyValueStore
from tests.unit.test_core_vision_kit import ROOT, app_config

SAFETY = app_config().safety
SAVED_FILE = json.loads((ROOT / 'config' / 'safety_rules.json').read_text())
OUTLINE = [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9]]


def test_built_in_rules_when_nothing_was_saved():
    rules = make_rules(SAFETY)
    assert rules.zones['laptop'] == Zone(expand=80, label='Spill / pressure zone')
    assert (rules.edge_danger_px, rules.edge_warn_px) == (35, 75)
    assert rules.outline == ()


def test_saved_zone_wins_over_the_built_in_one():
    rules = make_rules(SAFETY, SAVED_FILE)
    assert rules.zones['laptop'] == Zone(expand=134, label='Spill / pressure zone')


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
    assert page['danger_zones']['laptop'] == {'expand': 80, 'label': 'Spill / pressure zone'}
    assert page['edge_proximity'] == {'danger_px': 35, 'warn_px': 75,
                                      'objects': ['bottle', 'cell phone', 'cup', 'laptop', 'mouse', 'remote']}
    assert page['chain_reaction'] == {} and page['temporal'] == {}
