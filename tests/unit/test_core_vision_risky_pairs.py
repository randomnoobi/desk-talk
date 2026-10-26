"""Risky pairs: something in between two things, crowds, and things moving toward each other."""

import dataclasses

from desktalk.core.vision import risky_pairs as rp
from desktalk.core.vision.findings import Thing
from desktalk.core.vision.rules import make_rules
from tests.unit.test_core_vision_kit import app_config, seen_object

RULES = make_rules(app_config().safety)
WORDS = app_config().prompts.desk_checks


def at(label, cx, cy, agent=None):
    """A 20 x 20 box around (cx, cy)."""
    return Thing.from_seen(agent or label, seen_object(label, (cx - 10, cy - 10, cx + 10, cy + 10)))


def desk(*things):
    return {t.id: t for t in things}


# ── something in between ──
def test_a_thing_on_the_path_between_a_danger_pair_is_a_chain():
    things = desk(at('cup', 430, 285), at('laptop', 300, 275, 'computer'), at('mouse', 365, 280))
    found = rp.things_in_between(things, [('cup', 'computer')], RULES, words=WORDS)
    assert found.risk_events == [{'type': 'chain_reaction', 'chain': ['cup', 'mouse', 'computer'],
                                  'mediator': 'mouse', 'mediator_class': 'mouse', 'endpoints': ['cup', 'laptop'],
                                  'distance_to_path': 0.0}]
    assert found.explanations == ['mouse is between cup and laptop, so bumping one could knock over the others.']
    assert found.relationships == [{'type': 'chain_reaction', 'severity': 'high',
                                    'chain': ['cup', 'mouse', 'computer']}]


def test_a_thing_away_from_the_path_is_not_a_chain():
    things = desk(at('cup', 430, 285), at('laptop', 300, 275, 'computer'), at('mouse', 365, 400))
    assert rp.things_in_between(things, [('cup', 'computer')], RULES, words=WORDS).risk_events == []


def test_how_close_to_the_path_comes_from_the_rules():
    things = desk(at('cup', 430, 285), at('laptop', 300, 275, 'computer'), at('mouse', 365, 400))
    wide = dataclasses.replace(RULES, path_ratio=1.0)
    assert len(rp.things_in_between(things, [('cup', 'computer')], wide, words=WORDS).risk_events) == 1


def test_the_same_three_things_make_one_chain():
    things = desk(at('cup', 430, 285), at('laptop', 300, 275, 'computer'), at('mouse', 365, 280))
    found = rp.things_in_between(things, [('cup', 'computer'), ('computer', 'cup')], RULES, words=WORDS)
    assert len(found.risk_events) == 1


def test_a_pair_in_the_same_place_has_no_path():
    things = desk(at('cup', 300, 275), at('laptop', 300, 275, 'computer'), at('mouse', 300, 275))
    assert rp.things_in_between(things, [('cup', 'computer')], RULES, words=WORDS).risk_events == []


# ── crowds ──
def test_three_things_close_together_are_a_crowd():
    found = rp.crowds(desk(at('cup', 100, 100), at('bottle', 150, 100), at('apple', 100, 150)), RULES, words=WORDS)
    assert found.risk_events == [{'type': 'crowd', 'cluster': ['apple', 'bottle', 'cup'], 'count': 3}]
    assert found.explanations == ['3 things are crowded together (apple, bottle, cup) and could tip over.']
    assert found.relationships == [{'type': 'crowd', 'severity': 'warning', 'cluster': ['apple', 'bottle', 'cup']}]


def test_a_crowd_of_normal_desk_things_is_just_a_desk():
    things = desk(at('book', 100, 100), at('laptop', 150, 100), at('mouse', 100, 150))
    assert rp.crowds(things, RULES, words=WORDS).risk_events == []


def test_two_things_are_not_a_crowd():
    assert rp.crowds(desk(at('cup', 100, 100), at('bottle', 150, 100)), RULES, words=WORDS).risk_events == []


def test_things_far_apart_are_not_a_crowd():
    things = desk(at('cup', 100, 100), at('bottle', 300, 100), at('apple', 100, 300))
    assert rp.crowds(things, RULES, words=WORDS).risk_events == []


def test_crowd_radius_and_size_come_from_the_rules():
    things = desk(at('cup', 100, 100), at('bottle', 150, 100))
    looser = dataclasses.replace(RULES, crowd_min_objects=2)
    assert len(rp.crowds(things, looser, words=WORDS).risk_events) == 1
    tighter = dataclasses.replace(looser, crowd_radius_px=40)
    assert rp.crowds(things, tighter, words=WORDS).risk_events == []


# ── moving toward each other ──
def test_things_getting_closer_are_noticed():
    before = desk(at('cup', 0, 0), at('apple', 100, 0))
    now = desk(at('cup', 20, 0), at('apple', 100, 0))
    found = rp.approaching(now, before, RULES, words=WORDS)
    assert found.risk_events == [{'type': 'approaching', 'object_a': 'cup', 'object_b': 'apple', 'old_dist': 100.0,
                                  'new_dist': 80.0, 'rate_pct': 20.0}]
    assert found.explanations == ['cup is moving toward apple.']
    assert found.relationships == [{'type': 'approaching', 'severity': 'warning', 'from': 'cup', 'to': 'apple',
                                    'rate_pct': 20.0}]


def test_a_little_closer_is_not_approaching():
    before = desk(at('cup', 0, 0), at('apple', 100, 0))
    now = desk(at('cup', 10, 0), at('apple', 100, 0))
    assert rp.approaching(now, before, RULES, words=WORDS).risk_events == []


def test_how_much_closer_comes_from_the_rules():
    before = desk(at('cup', 0, 0), at('apple', 100, 0))
    now = desk(at('cup', 10, 0), at('apple', 100, 0))
    looser = dataclasses.replace(RULES, approach_rate=0.05)
    assert len(rp.approaching(now, before, looser, words=WORDS).risk_events) == 1


def test_things_that_often_sit_together_are_never_approaching():
    before = desk(at('laptop', 0, 0, 'computer'), at('mouse', 100, 0))
    now = desk(at('laptop', 50, 0, 'computer'), at('mouse', 100, 0))
    assert rp.approaching(now, before, RULES, words=WORDS).risk_events == []


def test_a_thing_that_was_not_there_before_is_not_approaching():
    before = desk(at('apple', 100, 0))
    now = desk(at('cup', 90, 0), at('apple', 100, 0))
    assert rp.approaching(now, before, RULES, words=WORDS).risk_events == []


def test_things_that_were_on_top_of_each_other_are_skipped():
    before = desk(at('cup', 100, 0), at('apple', 100, 0))
    now = desk(at('cup', 100, 0), at('apple', 100, 0))
    assert rp.approaching(now, before, RULES, words=WORDS).risk_events == []


def test_harmless_pairs_ignore_case_and_order():
    assert rp.harmless_pair(RULES, 'Mouse ', 'laptop')
    assert not rp.harmless_pair(RULES, 'cup', 'laptop')
    assert not rp.harmless_pair(RULES, '', 'laptop')


def test_the_explanations_come_from_prompts_yaml():
    words = WORDS.model_copy(update={'crowd': '{count} close: {labels}'})
    found = rp.crowds(desk(at('cup', 100, 100), at('bottle', 150, 100), at('apple', 100, 150)), RULES, words=words)
    assert found.explanations == ['3 close: apple, bottle, cup']
