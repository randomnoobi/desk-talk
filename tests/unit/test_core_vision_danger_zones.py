"""Danger zones: a risky thing (a drink, food, something sharp) inside the zone around a fragile one."""

import dataclasses

import pytest

from desktalk.core.vision import danger_zones as dz
from desktalk.core.vision.findings import Thing
from desktalk.core.vision.rules import Zone, make_rules
from tests.unit.test_core_vision_kit import app_config, desk_object

RULES = make_rules(app_config().safety)
LAPTOP = (200, 200, 400, 350)        # its zone (80 px wider) is 120, 120, 480, 430
NEAR = (410, 250, 450, 320)          # inside the zone, 40 px deep
FAR = (560, 380, 600, 450)


def things(*items):
    return {agent: Thing.from_desk(agent, desk_object(label, box)) for agent, label, box in items}


def test_cup_in_the_laptop_zone_is_a_danger():
    found = dz.zone_risks(things(('computer', 'laptop', LAPTOP), ('cup', 'cup', NEAR)), RULES, dz.ZoneVisits(), 0)
    assert found.risk_events == [{'type': 'zone_intrusion', 'intruder': 'cup', 'intruder_class': 'cup',
                                  'target': 'computer', 'target_class': 'laptop',
                                  'zone_label': 'Spill / pressure zone', 'depth_px': 40, 'center_dist_px': 130.4,
                                  'zone_expand': 80}]
    assert found.explanations == ["cup (hazard) in laptop's Spill / pressure zone — 40px"]
    assert found.relationships == [{'type': 'danger_zone', 'severity': 'danger', 'intruder': 'cup',
                                    'target': 'computer', 'depth_px': 40, 'zone_label': 'Spill / pressure zone'}]


def test_a_book_near_the_laptop_is_fine():
    found = dz.zone_risks(things(('computer', 'laptop', LAPTOP), ('paper', 'book', NEAR)), RULES, dz.ZoneVisits(), 0)
    assert found.risk_events == []


def test_a_cup_outside_the_zone_is_fine():
    found = dz.zone_risks(things(('computer', 'laptop', LAPTOP), ('cup', 'cup', FAR)), RULES, dz.ZoneVisits(), 0)
    assert found.risk_events == []


def test_zone_size_comes_from_the_rules():
    wider = dataclasses.replace(RULES, zones={'laptop': Zone(expand=200, label='Big zone')})
    found = dz.zone_risks(things(('computer', 'laptop', LAPTOP), ('cup', 'cup', FAR)), wider, dz.ZoneVisits(), 0)
    assert [e['zone_label'] for e in found.risk_events] == ['Big zone']


# Ported from test_new_drink_words_count_as_liquids
@pytest.mark.parametrize('zone, intruder, danger', [
    ('laptop', 'mug', True),
    ('laptop', 'apple', True),
    ('cell phone', 'scissors', True),
    ('laptop', 'pen', False),
    ('laptop', ' Mug ', True),          # the detector's word is tidied before comparing
    ('cup', 'mug', False),              # cups have no zone
])
def test_which_things_are_a_danger_in_a_zone(zone, intruder, danger):
    rules = make_rules(app_config().safety, liquids=['cup', 'mug', 'water bottle'], foods=['apple'])
    assert dz.counts_as_danger(rules, zone, intruder) is danger


def test_a_zone_with_no_danger_list_never_alerts():
    rules = make_rules(app_config().safety, {'danger_zones': {'cup': {'expand': 50, 'label': 'Cup zone'}}})
    assert not dz.counts_as_danger(rules, 'cup', 'bottle')


def run(visits, scenes, rules=RULES):
    """scenes: (time, cup box) pairs with the laptop always there. Returns repeated_intrusion counts."""
    counts = []
    for now, cup in scenes:
        desk = things(('computer', 'laptop', LAPTOP), ('cup', 'cup', cup))
        dz.zone_risks(desk, rules, visits, now)
        counts += [e['count'] for e in dz.repeated_entries(desk, rules, visits, now).risk_events]
    return counts


def test_coming_back_into_a_zone_three_times_in_a_minute_is_noticed():
    visits = dz.ZoneVisits()
    assert run(visits, [(0, NEAR), (1, FAR), (2, NEAR), (3, FAR)]) == []
    assert run(visits, [(4, NEAR)]) == [3]


def test_staying_in_the_zone_counts_as_one_visit():
    assert run(dz.ZoneVisits(), [(t, NEAR) for t in range(10)]) == []


def test_old_visits_are_forgotten():
    visits = dz.ZoneVisits()
    run(visits, [(0, NEAR), (1, FAR), (2, NEAR), (3, FAR)])
    assert run(visits, [(61, NEAR)]) == []          # the visit at 0 s is more than 60 s ago


def test_the_repeat_explanation_names_both_things():
    visits = dz.ZoneVisits()
    run(visits, [(0, NEAR), (1, FAR), (2, NEAR), (3, FAR), (4, NEAR)])
    desk = things(('computer', 'laptop', LAPTOP), ('cup', 'cup', NEAR))
    found = dz.repeated_entries(desk, RULES, visits, 5)
    assert found.risk_events == [{'type': 'repeated_intrusion', 'intruder': 'cup', 'target': 'computer', 'count': 3}]
    assert found.explanations == ["cup entered laptop's zone 3 times in the last 60 s"]
    assert found.relationships == [{'type': 'repeated_intrusion', 'severity': 'high', 'intruder': 'cup',
                                    'target': 'computer', 'count': 3}]


def test_repeats_need_both_things_in_view():
    visits = dz.ZoneVisits()
    run(visits, [(0, NEAR), (1, FAR), (2, NEAR), (3, FAR), (4, NEAR)])
    only_laptop = things(('computer', 'laptop', LAPTOP))
    assert dz.repeated_entries(only_laptop, RULES, visits, 5).risk_events == []


def test_repeat_count_and_window_come_from_the_rules():
    fewer = dataclasses.replace(RULES, repeated_times=2, repeated_window_sec=2.5)
    visits = dz.ZoneVisits()
    assert run(visits, [(0, NEAR), (1, FAR), (2, NEAR)], fewer) == [2]
    assert run(visits, [(3, FAR), (4, NEAR)], fewer) == [2]       # the visit at 0 s has dropped out
