"""The desk analyzer: every desk check on each picture, the risk level, and what moved lately."""

import pytest

from desktalk.core.vision.analyzer import DeskAnalyzer, risk_level
from desktalk.core.vision.findings import Thing
from desktalk.core.vision.rules import SafetyRules
from tests.fakes import InMemoryKeyValueStore
from tests.unit.test_core_vision_kit import app_config, seen_object

SIZE = (640, 480)
LAPTOP = ('computer', 'laptop', (200, 200, 400, 350))
CUP_NEAR = ('cup', 'cup', (410, 250, 450, 320))
CUP_FAR = ('cup', 'cup', (560, 380, 600, 450))


def things(*items):
    return {agent: Thing.from_seen(agent, seen_object(label, box)) for agent, label, box in items}


def analyzer(saved=None, **rules_kw):
    store = InMemoryKeyValueStore({'safety_rules': saved} if saved else {})
    rules = SafetyRules(store, app_config().safety, words=app_config().prompts.desk_rules, **rules_kw)
    return DeskAnalyzer(rules, words=app_config().prompts.desk_checks), rules


def kinds(result):
    return [e['type'] for e in result['risk_events']]


# Ported from test_cup_in_the_laptop_zone_is_an_alert / test_a_book_near_the_laptop_is_fine
def test_cup_in_the_laptop_zone_is_an_alert():
    result = analyzer()[0].analyze(things(LAPTOP, CUP_NEAR), SIZE, now=100)
    zone = [e for e in result['risk_events'] if e['type'] == 'zone_intrusion']
    assert zone and zone[0]['intruder'] == 'cup' and zone[0]['target'] == 'computer'


def test_a_book_near_the_laptop_is_fine():
    result = analyzer()[0].analyze(things(LAPTOP, ('paper', 'book', (410, 250, 450, 320))), SIZE, now=100)
    assert 'zone_intrusion' not in kinds(result)


def test_result_has_the_shape_the_app_reads():
    result = analyzer()[0].analyze(things(('cup', 'cup', (10.04, 20.06, 30.0, 40.0))), SIZE, now=123.5)
    cup = {'id': 'cup', 'class': 'cup', 'center': [20.0, 30.0], 'bbox': [10.0, 20.1, 30.0, 40.0]}
    assert result == {'timestamp': 123.5, 'objects': [cup], 'relationships': [], 'risk_events': [],
                      'explanations': [], 'risk_level': 0}


@pytest.mark.parametrize('score, level', [(0, 0), (1, 1), (2, 1), (3, 2), (5, 2), (6, 3), (9, 3), (10, 4), (30, 4)])
def test_risk_level_from_the_score(score, level):
    events = [{'type': 'edge_warning'}] * score            # an edge warning weighs 1
    assert risk_level(events, app_config().safety.risk_weights.model_dump(), (2, 5, 9)) == level


def test_risk_level_adds_up_the_weights():
    events = [{'type': 'zone_intrusion'}, {'type': 'chain_reaction'}]      # 2 + 3
    assert risk_level(events, {'zone_intrusion': 2, 'chain_reaction': 3}, (2, 5, 9)) == 2
    assert risk_level(events, {'zone_intrusion': 2, 'chain_reaction': 3}, (2, 4, 9)) == 3


def test_a_thing_between_a_danger_pair_is_a_chain():
    mouse = ('mouse', 'mouse', (405, 270, 415, 280))       # between the cup and the laptop
    result = analyzer()[0].analyze(things(LAPTOP, CUP_NEAR, mouse), SIZE, now=100)
    assert 'chain_reaction' in kinds(result)
    assert result['risk_level'] >= 2


def test_things_moving_toward_each_other_over_the_last_pictures():
    an = analyzer()[0]
    seen = []
    for k in range(10):
        cup = ('cup', 'cup', (5 * k, 0, 5 * k + 20, 20))
        result = an.analyze(things(cup, ('apple', 'apple', (100, 0, 120, 20))), SIZE, now=100 + k)
        seen.append('approaching' in kinds(result))
    assert seen == [False] * 8 + [True, True]          # needs 8 pictures before it can compare


def test_coming_back_into_a_zone_uses_the_time_given():
    an = analyzer()[0]
    found = []
    for t, cup in enumerate([CUP_NEAR, CUP_FAR, CUP_NEAR, CUP_FAR, CUP_NEAR]):
        found += kinds(an.analyze(things(LAPTOP, cup), SIZE, now=1000 + 100 * t))
    assert 'repeated_intrusion' not in found           # 3 visits, but not within a minute
    an = analyzer()[0]
    for t, cup in enumerate([CUP_NEAR, CUP_FAR, CUP_NEAR, CUP_FAR, CUP_NEAR]):
        found = kinds(an.analyze(things(LAPTOP, cup), SIZE, now=1000 + t))
    assert 'repeated_intrusion' in found


def test_edge_warnings_use_the_picture_size_given():
    saved = {'desk_outline': [[0, 0], [1, 0], [1, 0.8], [0, 0.8]]}
    an = analyzer(saved)[0]
    cup = ('cup', 'cup', (400, 480, 470, 570))          # near the front edge of a 960 x 720 picture
    assert 'edge_danger' in kinds(an.analyze(things(cup), (960, 720), now=1))
    assert 'edge_danger' not in kinds(an.analyze(things(cup), (960, 1440), now=2))


def test_saved_rules_take_effect_at_the_next_picture():
    an, rules = analyzer()
    assert 'zone_intrusion' not in kinds(an.analyze(things(LAPTOP, CUP_FAR), SIZE, now=1))
    rules.save({'danger_zones': {'laptop': {'expand': 200, 'label': 'Big zone'}}})
    assert 'zone_intrusion' in kinds(an.analyze(things(LAPTOP, CUP_FAR), SIZE, now=2))


def test_agents_drink_words_count():
    an = analyzer(liquids=['mug'])[0]
    result = an.analyze(things(LAPTOP, ('cup', 'mug', (410, 250, 450, 320))), SIZE, now=1)
    assert 'zone_intrusion' in kinds(result)


def test_the_latest_result_and_the_history_are_kept():
    an = analyzer()[0]
    assert an.latest is None and an.history() == []
    for k in range(35):
        last = an.analyze(things(('cup', 'cup', (k, 0, k + 20, 20))), SIZE, now=k)
    assert an.latest is last
    history = an.history()
    assert len(history) == app_config().safety.temporal.history_length == 30
    assert history[-1]['cup'].box == (34, 0, 54, 20)


def test_every_check_runs_in_order_on_one_picture():
    """Edges, zones, things in between, crowds, things moving closer, repeat visits: in that order."""
    an = analyzer({'desk_outline': [[0, 0], [1, 0], [1, 0.8], [0, 0.8]]})[0]       # front edge at y = 384
    mouse = ('mouse', 'mouse', (420, 270, 430, 280))
    apple = ('snack', 'apple', (470, 330, 500, 360))
    bottle = ('drink', 'bottle', (100, 360, 140, 400))                              # on the front edge
    cup_far = ('cup', 'cup', (560, 300, 600, 370))
    for k, cup in enumerate([cup_far] * 4 + [CUP_NEAR, cup_far, CUP_NEAR, cup_far, CUP_NEAR]):
        result = an.analyze(things(LAPTOP, cup, mouse, apple, bottle), SIZE, now=1000 + k)
    assert kinds(result) == ['edge_danger', 'edge_warning', 'edge_danger'] + ['zone_intrusion'] * 3 + \
        ['chain_reaction'] * 3 + ['crowd'] * 3 + ['approaching'] * 4 + ['repeated_intrusion']


def test_a_zone_saved_on_the_settings_page_is_checked_like_the_built_in_ones():
    an = analyzer({'danger_zones': {'mouse': {'expand': 40, 'label': 'Mouse zone'}},
                   'zone_danger_intruders': {'mouse': ['cup']}})[0]
    result = an.analyze(things(('mouse', 'mouse', (300, 300, 340, 330)), ('cup', 'cup', (350, 300, 380, 340))),
                        SIZE, now=1)
    [event] = result['risk_events']
    assert (event['type'], event['target'], event['intruder'], event['zone_label'], event['depth_px']) == (
        'zone_intrusion', 'mouse', 'cup', 'Mouse zone', 30)
    assert result['explanations'] == ["cup is in mouse's Mouse zone."]


def test_how_many_pictures_back_to_compare_can_be_saved():
    an = analyzer({'temporal': {'approach_min_frames': 4}})[0]
    seen = []
    for k in range(7):
        cup = ('cup', 'cup', (5 * k, 0, 5 * k + 20, 20))
        seen.append('approaching' in kinds(an.analyze(things(cup, ('apple', 'apple', (100, 0, 120, 20))), SIZE,
                                                       now=100 + k)))
    assert seen == [False] * 4 + [True] * 3            # from the 5th picture on
