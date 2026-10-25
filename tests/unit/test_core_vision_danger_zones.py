"""Danger zones: a risky thing (a drink, food, something sharp) inside the zone around a fragile one."""

import dataclasses
import json

import pytest

from desktalk.core.vision import danger_zones as dz
from desktalk.core.vision.findings import Thing
from desktalk.core.vision.rules import make_rules, standing_strip
from desktalk.domain import Zone
from tests.unit.test_core_vision_kit import ROOT, app_config, seen_object

RULES = make_rules(app_config().safety)
SAVED_RULES = make_rules(app_config().safety, json.loads((ROOT / 'config' / 'safety_rules.json').read_text()))
WORDS = app_config().prompts.desk_checks
LAPTOP = (200, 200, 400, 350)        # its base is the bottom half (y 275-350); the zone is that, 80 px wider
NEAR = (410, 250, 450, 320)          # stands 10 px right of the base, so 70 px inside the zone
FAR = (560, 380, 600, 450)


def things(*items):
    return {agent: Thing.from_seen(agent, seen_object(label, box)) for agent, label, box in items}


def test_cup_in_the_laptop_zone_is_a_danger():
    found = dz.zone_risks(things(('computer', 'laptop', LAPTOP), ('cup', 'cup', NEAR)), RULES, dz.ZoneVisits(), 0,
                          words=WORDS)
    assert found.risk_events == [{'type': 'zone_intrusion', 'intruder': 'cup', 'intruder_class': 'cup',
                                  'target': 'computer', 'target_class': 'laptop',
                                  'zone_label': 'Spill / pressure zone', 'depth_px': 70, 'center_dist_px': 130.4,
                                  'zone_expand': 80}]
    assert found.explanations == ["cup (hazard) in laptop's Spill / pressure zone — 70px"]
    assert found.relationships == [{'type': 'danger_zone', 'severity': 'danger', 'intruder': 'cup',
                                    'target': 'computer', 'depth_px': 70, 'zone_label': 'Spill / pressure zone'}]


def test_a_book_near_the_laptop_is_fine():
    found = dz.zone_risks(things(('computer', 'laptop', LAPTOP), ('paper', 'book', NEAR)), RULES, dz.ZoneVisits(), 0,
                          words=WORDS)
    assert found.risk_events == []


def test_a_cup_outside_the_zone_is_fine():
    found = dz.zone_risks(things(('computer', 'laptop', LAPTOP), ('cup', 'cup', FAR)), RULES, dz.ZoneVisits(), 0,
                          words=WORDS)
    assert found.risk_events == []


def test_zone_size_comes_from_the_rules():
    wider = dataclasses.replace(RULES, zones={'laptop': Zone(expand=200, label='Big zone')})
    found = dz.zone_risks(things(('computer', 'laptop', LAPTOP), ('cup', 'cup', FAR)), wider, dz.ZoneVisits(), 0,
                          words=WORDS)
    assert [e['zone_label'] for e in found.risk_events] == ['Big zone']


def zone_hits(rules, *items):
    return [e for e in dz.zone_risks(things(*items), rules, dz.ZoneVisits(), 0, words=WORDS).risk_events
            if e['type'] == 'zone_intrusion']


@pytest.mark.parametrize('rules', [RULES, SAVED_RULES], ids=['built-in', 'saved'])
def test_a_bottle_far_behind_the_laptop_screen_is_fine(rules):
    """The camera looks at the desk at an angle, so the laptop's box reaches the top of the picture
    (its open screen). The bottle stands far behind it; only its top shows above the screen."""
    assert zone_hits(rules, ('computer', 'laptop', (210, 0, 1050, 570)), ('drink', 'bottle', (740, 0, 840, 130))) == []


def test_a_bottle_right_against_the_back_of_the_laptop_is_a_danger():
    """From a real picture from the Pi (1280 x 720): the bottle stands right behind the keyboard."""
    [hit] = zone_hits(SAVED_RULES, ('computer', 'laptop', (210, 78, 978, 672)), ('drink', 'bottle', (585, 0, 712, 250)))
    assert (hit['intruder'], hit['target'], hit['zone_expand']) == ('drink', 'computer', 134)


def test_a_cup_touching_the_laptop_base_is_as_deep_as_the_zone():
    [hit] = zone_hits(RULES, ('computer', 'laptop', LAPTOP), ('cup', 'cup', (400, 300, 440, 350)))
    assert hit['depth_px'] == 80 == RULES.zones['laptop'].expand


def test_a_cup_at_the_edge_of_the_zone_is_hardly_inside():
    [hit] = zone_hits(RULES, ('computer', 'laptop', LAPTOP), ('cup', 'cup', (478, 300, 518, 350)))
    assert hit['depth_px'] == 2


def test_a_tall_bottle_standing_far_from_the_keyboard_is_fine_even_when_its_box_covers_it():
    """The bottle stands in front of the keyboard (lower in the picture), and is tall enough that the top
    of its box reaches over the keyboard's box. Where it stands is far away."""
    keyboard, bottle = (300, 300, 700, 420), (450, 380, 510, 700)
    assert zone_hits(RULES, ('keys', 'keyboard', keyboard), ('drink', 'bottle', bottle)) == []


def test_the_zone_is_drawn_from_the_base_and_the_strip_from_where_a_thing_stands():
    assert dz.zone_area(LAPTOP, RULES.zones['laptop'], RULES) == (120, 195, 480, 430)
    assert standing_strip(NEAR, RULES) == (410, 306, 450, 320)


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
        dz.zone_risks(desk, rules, visits, now, words=WORDS)
        counts += [e['count'] for e in dz.repeated_entries(desk, rules, visits, now, words=WORDS).risk_events]
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
    found = dz.repeated_entries(desk, RULES, visits, 5, words=WORDS)
    assert found.risk_events == [{'type': 'repeated_intrusion', 'intruder': 'cup', 'target': 'computer', 'count': 3}]
    assert found.explanations == ["cup entered laptop's zone 3 times in the last 60 s"]
    assert found.relationships == [{'type': 'repeated_intrusion', 'severity': 'high', 'intruder': 'cup',
                                    'target': 'computer', 'count': 3}]


def test_repeats_need_both_things_in_view():
    visits = dz.ZoneVisits()
    run(visits, [(0, NEAR), (1, FAR), (2, NEAR), (3, FAR), (4, NEAR)])
    only_laptop = things(('computer', 'laptop', LAPTOP))
    assert dz.repeated_entries(only_laptop, RULES, visits, 5, words=WORDS).risk_events == []


def test_repeat_count_and_window_come_from_the_rules():
    fewer = dataclasses.replace(RULES, repeated_times=2, repeated_window_sec=2.5)
    visits = dz.ZoneVisits()
    assert run(visits, [(0, NEAR), (1, FAR), (2, NEAR)], fewer) == [2]
    assert run(visits, [(3, FAR), (4, NEAR)], fewer) == [2]       # the visit at 0 s has dropped out


def test_the_explanation_comes_from_prompts_yaml():
    words = WORDS.model_copy(update={'zone': '{intruder} in the {zone} of {target} by {depth}px'})
    found = dz.zone_risks(things(('computer', 'laptop', LAPTOP), ('cup', 'cup', NEAR)), RULES, dz.ZoneVisits(), 0,
                          words=words)
    assert found.explanations == ['cup in the Spill / pressure zone of laptop by 70px']
