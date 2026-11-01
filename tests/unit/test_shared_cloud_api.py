"""What may go to the cloud server (desktalk_shared.cloud_api): the send table, one test per row."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import pytest

from desktalk_shared.cloud_api import (
    HOW_WORDS,
    MAX_ID_CHARS,
    ClaimReply,
    IngestReply,
    Refusal,
    daily_totals_event,
    goals_event,
    is_demo,
    to_cloud_event,
)

ROOT = Path(__file__).resolve().parents[2]
BASE = {'id': 'a', 'ts': 1.0}


def ev(type_: str, **fields):
    return {**BASE, 'type': type_, **fields}


# ── the table, row by row ───────────────────────────────────────────
def test_object_state_keeps_its_fields_and_loses_the_box():
    event = ev('object_state', change='appeared', object='cup', prev_state='ABSENT', new_state='PRESENT',
               device_id='pi', conf_avg=0.8, dwell_sec=12.5, bbox=[1, 2, 3, 4])
    assert to_cloud_event(event) == ev('object_state', change='appeared', object='cup', prev_state='ABSENT',
                                       new_state='PRESENT', device_id='pi', conf_avg=0.8, dwell_sec=12.5)


def test_object_state_with_nothing_known_yet_leaves_out_the_empty_numbers():
    event = ev('object_state', change='appeared', object='cup', prev_state='UNKNOWN', new_state='PRESENT',
               device_id='pi', conf_avg=None, dwell_sec=None, bbox=None)
    sent = to_cloud_event(event)
    assert sent is not None and 'conf_avg' not in sent and 'dwell_sec' not in sent and 'bbox' not in sent


def test_habit_keeps_numbers_how_and_healthy_and_never_say():
    event = ev('habit', habit='hydration', action='sip', say='A sip', quiet=True, ml=250, total_ml=750.5,
               how='camera', healthy=True, note='hello')
    assert to_cloud_event(event) == ev('habit', habit='hydration', action='sip', ml=250, total_ml=750.5,
                                       how='camera', healthy=True)


def test_todo_text_never_passes():
    event = {'id': 'a', 'ts': 1.0, 'type': 'habit', 'habit': 'tasks', 'action': 'done',
             'say': 'Done: english essay', 'task': 'english essay'}
    assert to_cloud_event(event) == {'id': 'a', 'ts': 1.0, 'type': 'habit', 'habit': 'tasks', 'action': 'done'}


def test_food_label_stays_home():
    sent = to_cloud_event(ev('habit', habit='food', action='food', label='pizza', healthy=False, how='camera'))
    assert sent == ev('habit', habit='food', action='food', healthy=False, how='camera')


def test_a_how_word_not_on_the_list_is_dropped():
    sent = to_cloud_event(ev('habit', habit='breaks', action='break', how='english essay'))
    assert sent is not None and 'how' not in sent


def test_healthy_must_be_yes_or_no():
    sent = to_cloud_event(ev('habit', habit='food', action='food', healthy='pizza'))
    assert sent is not None and 'healthy' not in sent


def test_nudge_keeps_its_evidence_and_never_its_text():
    event = ev('nudge', habit='hydration', rule='hydration.low', agent='cup', evidence='no sip for 90 minutes',
               text='Drink up!')
    assert to_cloud_event(event) == ev('nudge', habit='hydration', rule='hydration.low', agent='cup',
                                       evidence='no sip for 90 minutes')


def test_tasks_nudge_loses_its_evidence():
    event = {'id': 'a', 'ts': 1.0, 'type': 'nudge', 'habit': 'tasks', 'rule': 'tasks.open', 'agent': 'paper',
             'evidence': 'starting with "english essay"', 'text': 'Oh no'}
    sent = to_cloud_event(event)
    assert sent is not None
    assert 'evidence' not in sent and 'text' not in sent
    assert sent == ev('nudge', habit='tasks', rule='tasks.open', agent='paper')


def test_rule_outcome_keeps_its_fields():
    event = ev('rule_outcome', rule='hydration.low', habit='hydration', result='worked', after_sec=300.0, text='x')
    assert to_cloud_event(event) == ev('rule_outcome', rule='hydration.low', habit='hydration', result='worked',
                                       after_sec=300.0)


def test_alert_keeps_its_fields_and_never_the_wording():
    event = ev('alert', kind='scenario', agent='lens', scenario='lost_object', feature='cup',
               text='Where is your cup?', severity='high')
    assert to_cloud_event(event) == ev('alert', kind='scenario', agent='lens', scenario='lost_object',
                                       feature='cup')


def test_camera_notice_keeps_its_fixed_fields_but_not_the_wording():
    event = ev('camera', key='covered', kind='problem', text='Your camera is covered')
    assert to_cloud_event(event) == ev('camera', key='covered', kind='problem')


def test_goals_event_makes_a_goals_row():
    sent = goals_event(5.0, wake='07:00', bed='22:30', goals={'water_ml': 2000, 'breaks': 6})
    assert sent == {'id': 'goals-5', 'ts': 5.0, 'type': 'goals', 'wake': '07:00', 'bed': '22:30',
                    'goals': {'water_ml': 2000, 'breaks': 6}}
    assert to_cloud_event(sent) == sent


def test_goals_that_are_not_a_list_of_numbers_become_empty():
    sent = to_cloud_event(ev('goals', wake='07:00', goals='water'))
    assert sent is not None and sent['goals'] == {}


def test_goals_event_ids_differ_when_the_time_differs():
    first = goals_event(5.0, wake='07:00', bed='22:30', goals={})
    second = goals_event(6.0, wake='07:00', bed='22:30', goals={})
    assert first['id'] != second['id']


def test_goals_keep_only_numbers():
    sent = to_cloud_event(ev('goals', wake='07:00', bed='22:30',
                             goals={'water_ml': 2000, 'todo': 'english essay', 'on': True}, notes='x'))
    assert sent == ev('goals', wake='07:00', bed='22:30', goals={'water_ml': 2000})


def test_daily_totals_event_is_state_for_one_day():
    sent = daily_totals_event(9.0, '2026-10-28', {'desk_min': 300, 'water_ml': 1500, 'sips': 6})
    assert sent == {'id': 'daily-2026-10-28', 'ts': 9.0, 'type': 'daily_totals', 'day': '2026-10-28',
                    'totals': {'desk_min': 300, 'water_ml': 1500, 'sips': 6}}
    assert to_cloud_event(sent) == sent


def test_daily_totals_keep_only_numbers():
    sent = to_cloud_event(daily_totals_event(9.0, '2026-10-28', {'desk_min': 300, 'todos': ['essay'],
                                                                 'bad': math.nan}))
    assert sent is not None and sent['totals'] == {'desk_min': 300}


def test_daily_totals_with_a_made_up_day_stay_home():
    assert to_cloud_event(ev('daily_totals', day='tomorrow', totals={'desk_min': 1})) is None
    assert to_cloud_event(ev('daily_totals', totals={'desk_min': 1})) is None


def test_daily_totals_id_always_names_the_day():
    sent = to_cloud_event(ev('daily_totals', day='2026-10-28', totals={'desk_min': 1}))
    assert sent is not None and sent['id'] == 'daily-2026-10-28'


# ── what never goes ─────────────────────────────────────────────────
def test_picture_fields_never_pass():
    for name in ('picture', 'jpeg', 'image', 'photo', 'frame'):
        sent = to_cloud_event({'id': 'a', 'ts': 1.0, 'type': 'habit', 'habit': 'breaks', 'action': 'break',
                               name: 'abc'})
        assert sent is not None and name not in sent


def test_picture_fields_do_not_pass_even_as_numbers():
    for name in ('picture', 'jpeg', 'image', 'photo', 'frame'):
        sent = to_cloud_event(ev('habit', habit='breaks', action='break', **{name: 5}))
        assert sent is not None and name not in sent


@pytest.mark.parametrize('type_', ['detection', 'agent_joined', 'agent_left', 'compaction', 'mystery', '', None, 5])
def test_other_types_stay_home(type_):
    assert to_cloud_event({**BASE, 'type': type_, 'habit': 'x'}) is None


def test_an_event_with_no_type_stays_home():
    assert to_cloud_event(dict(BASE)) is None


def test_demo_events_stay_home():
    assert to_cloud_event(ev('alert', kind='scenario', agent='lens', demo=True)) is None
    assert to_cloud_event(ev('nudge', habit='hydration', rule='demo.drink', agent='cup', evidence='x')) is None
    assert to_cloud_event(ev('habit', habit='hydration', action='sip', extra={'demo': True})) is None


def test_is_demo():
    assert is_demo({'demo': True})
    assert is_demo({'rule': 'demo.drink'})
    assert is_demo({'extra': {'demo': True}})
    assert not is_demo({'demo': False})
    assert not is_demo({'demo': 'yes'})
    assert not is_demo({'rule': 'hydration.low'})
    assert not is_demo({'rule': 5})
    assert not is_demo({})


@pytest.mark.parametrize('bad', [True, False, math.nan, math.inf, -math.inf, '5', None, [5], {'a': 1}])
def test_a_number_must_be_a_real_number(bad):
    sent = to_cloud_event(ev('habit', habit='hydration', action='sip', ml=bad))
    assert sent is not None and 'ml' not in sent


def test_bad_numbers_in_named_fields_are_dropped_too():
    sent = to_cloud_event(ev('rule_outcome', rule='r', habit='h', result='worked', after_sec=math.nan))
    assert sent is not None and 'after_sec' not in sent


@pytest.mark.parametrize('missing', ['id', 'ts'])
def test_an_event_without_id_or_ts_stays_home(missing):
    event = ev('habit', habit='hydration', action='sip')
    del event[missing]
    assert to_cloud_event(event) is None


@pytest.mark.parametrize('bad', [None, '', 5, True, math.nan, math.inf, 'x' * (MAX_ID_CHARS + 1)])
def test_an_unusable_id_stays_home(bad):
    assert to_cloud_event({**ev('habit', habit='hydration', action='sip'), 'id': bad}) is None


@pytest.mark.parametrize('bad', [None, '', 'now', True, math.nan, math.inf])
def test_an_unusable_ts_stays_home(bad):
    assert to_cloud_event({**ev('habit', habit='hydration', action='sip'), 'ts': bad}) is None


def test_an_id_as_long_as_allowed_passes():
    sent = to_cloud_event({**ev('habit', habit='hydration', action='sip'), 'id': 'x' * MAX_ID_CHARS})
    assert sent is not None and len(sent['id']) == MAX_ID_CHARS


def test_text_in_a_text_field_must_be_text():
    sent = to_cloud_event(ev('habit', habit=['tasks'], action='done'))
    assert sent is not None and 'habit' not in sent


def test_very_long_text_is_cut_short():
    sent = to_cloud_event(ev('nudge', habit='hydration', rule='r', agent='cup', evidence='e' * 5000))
    assert sent is not None and len(sent['evidence']) < 1000


def test_odd_key_names_are_dropped():
    sent = to_cloud_event(ev('habit', habit='hydration', action='sip', **{'Bad Key': 1, 'ok_key': 2, '': 3}))
    assert sent is not None and 'Bad Key' not in sent and '' not in sent and sent['ok_key'] == 2


def test_the_event_given_is_not_changed():
    event = ev('habit', habit='tasks', action='done', say='Done: essay', task='essay')
    before = dict(event)
    to_cloud_event(event)
    assert event == before


@pytest.mark.parametrize('event', [
    ev('object_state', change='appeared', object='cup', prev_state='A', new_state='P', device_id='pi',
       conf_avg=0.5, dwell_sec=2, bbox=[1, 2, 3, 4]),
    ev('habit', habit='tasks', action='done', say='Done: essay', task='essay', how='button', healthy=True, n=3),
    ev('nudge', habit='tasks', rule='tasks.open', agent='paper', evidence='"essay"', text='x'),
    ev('nudge', habit='hydration', rule='hydration.low', agent='cup', evidence='no sip', text='x'),
    ev('rule_outcome', rule='r', habit='h', result='worked', after_sec=3),
    ev('alert', kind='scenario', agent='lens', scenario='s', feature='cup', text='x'),
    ev('camera', key='k', kind='problem', text='x'),
    goals_event(1.0, wake='07:00', bed='22:30', goals={'water_ml': 2000}),
    daily_totals_event(1.0, '2026-10-28', {'desk_min': 4}),
])
def test_filtering_twice_gives_the_same_result(event):
    once = to_cloud_event(event)
    assert once is not None
    assert to_cloud_event(once) == once


# ── the words the app uses ──────────────────────────────────────────
def how_words_in_the_app() -> set[str]:
    """Every `how` word in the three files that make them, read from the code."""
    words: set[str] = set()
    for name in ('core/habits/habit_reader.py', 'core/habits/tracker.py', 'core/demo/firer.py'):
        text = (ROOT / 'desktalk' / name).read_text(encoding='utf-8')
        words.update(re.findall(r"""how=['"]([^'"]+)['"]""", text))               # how='camera'
        words.update(re.findall(r"""'how':\s*['"]([^'"]+)['"]""", text))          # 'how': 'button'
        # _log_break(.., 'camera off')
        words.update(re.findall(r"""_log_break\([^)]*,\s*['"]([^'"]+)['"]\)""", text))
        # log_sip(.., 'camera', self..)
        words.update(re.findall(r"""log_sip\([^)]*['"]([^'"]+)['"],\s*self""", text))
        # add_break(now, mins, 'button', ..)
        words.update(re.findall(r"""add_break\([^)]*,\s*['"]([^'"]+)['"],""", text))
        # add_food(.., 'camera')
        words.update(re.findall(r"""add_food\([^)]*,\s*['"]([^'"]+)['"]\)""", text))
    # the chat logs sips with how='chat' (core/chat/tracker_actions.py)
    text = (ROOT / 'desktalk' / 'core/chat/tracker_actions.py').read_text(encoding='utf-8')
    words.update(re.findall(r"""how=['"]([^'"]+)['"]""", text))
    return words


def test_how_words_match_the_app():
    assert set(HOW_WORDS) == {'camera', 'button', 'chat', 'left the desk', 'camera off'}
    assert how_words_in_the_app() == set(HOW_WORDS)


# ── what the Pi and the server say to each other ────────────────────
def test_claim_reply_round_trips():
    reply = ClaimReply(device_id='d-1', token='t-1')
    assert reply.to_dict() == {'device_id': 'd-1', 'token': 't-1'}
    assert ClaimReply.from_dict(reply.to_dict()) == reply


def test_ingest_reply_round_trips():
    reply = IngestReply(accepted=3, repeats=1, refused=(Refusal(id='x', why='no ts'),))
    assert reply.to_dict() == {'accepted': 3, 'repeats': 1, 'refused': [{'id': 'x', 'why': 'no ts'}]}
    assert IngestReply.from_dict(reply.to_dict()) == reply


def test_ingest_reply_with_nothing_refused():
    assert IngestReply.from_dict({'accepted': 2, 'repeats': 0}) == IngestReply(accepted=2, repeats=0)
    assert IngestReply(accepted=2, repeats=0).to_dict()['refused'] == []


@pytest.mark.parametrize('data', [None, [], {}, {'device_id': 'd'}, {'device_id': 5, 'token': 't'},
                                  {'device_id': 'd', 'token': ''}])
def test_a_broken_claim_reply_is_an_error(data):
    with pytest.raises(ValueError):
        ClaimReply.from_dict(data)


@pytest.mark.parametrize('data', [None, {}, {'accepted': 'x', 'repeats': 0}, {'accepted': True, 'repeats': 0},
                                  {'accepted': 1, 'repeats': 0, 'refused': 'no'},
                                  {'accepted': 1, 'repeats': 0, 'refused': [{'id': 'x'}]},
                                  {'accepted': 1, 'repeats': 0, 'refused': [{'id': 5, 'why': 'no'}]},
                                  {'accepted': 1, 'repeats': 0, 'refused': ['x']}])
def test_a_broken_ingest_reply_is_an_error(data):
    with pytest.raises(ValueError):
        IngestReply.from_dict(data)


# ── fix round 1: the to-do text in a nudge's rule, and odd values from a bad sender ──
TODO_RULES = ['tasks.due.english essay.today', 'tasks.due.read ch. 3. v2.0.late.2', 'tasks.due.essay.tomorrow',
              'tasks.due.essay.soon.3', 'tasks.due.tasks.open.today', 'tasks.due.', 'tasks.due']


@pytest.mark.parametrize('rule', TODO_RULES)
def test_a_due_nudge_sends_only_the_fixed_part_of_its_rule(rule):
    sent = to_cloud_event(ev('nudge', habit='tasks', rule=rule, agent='paper', evidence='"english essay"'))
    assert sent == ev('nudge', habit='tasks', rule='tasks.due', agent='paper')


@pytest.mark.parametrize('rule', TODO_RULES)
def test_a_due_rule_outcome_sends_only_the_fixed_part_of_its_rule(rule):
    sent = to_cloud_event(ev('rule_outcome', rule=rule, habit='tasks', result='worked', after_sec=5.0))
    assert sent == ev('rule_outcome', rule='tasks.due', habit='tasks', result='worked', after_sec=5.0)


def test_the_open_to_do_check_in_keeps_its_rule():
    sent = to_cloud_event(ev('nudge', habit='tasks', rule='tasks.open', agent='paper', evidence='"essay"'))
    assert sent == ev('nudge', habit='tasks', rule='tasks.open', agent='paper')


def test_a_tasks_rule_is_cut_even_when_the_habit_says_something_else():
    nudge = to_cloud_event(ev('nudge', habit='x', rule='tasks.due.english essay.today', agent='a', evidence='e'))
    assert nudge == ev('nudge', habit='x', rule='tasks.due', agent='a')
    outcome = to_cloud_event(ev('rule_outcome', rule='tasks.due.english essay.today', habit='x', result='missed'))
    assert outcome == ev('rule_outcome', rule='tasks.due', habit='x', result='missed')


def test_a_tasks_nudge_loses_its_evidence_when_only_the_rule_says_tasks():
    sent = to_cloud_event(ev('nudge', habit='x', rule='tasks.open', agent='a', evidence='starting with "essay"'))
    assert sent is not None and 'evidence' not in sent


def test_a_rule_that_is_not_one_of_the_two_to_do_rules_is_cut_to_tasks():
    for habit, rule in (('tasks', 'english essay'), ('tasks', 'tasks.english essay'), ('x', 'tasks.opened')):
        sent = to_cloud_event(ev('nudge', habit=habit, rule=rule, agent='a'))
        assert sent is not None and sent['rule'] == 'tasks'


def test_other_rules_keep_their_whole_key():
    for rule in ('hydration.morning', 'breaks.meal.1', 'food.sitting.1800', 'rule.my_own_rule', 'phone.bedtime'):
        nudge = to_cloud_event(ev('nudge', habit='hydration', rule=rule, agent='cup'))
        outcome = to_cloud_event(ev('rule_outcome', rule=rule, habit='hydration', result='worked'))
        assert nudge is not None and nudge['rule'] == rule
        assert outcome is not None and outcome['rule'] == rule


@pytest.mark.parametrize('how', [['camera'], {'a': 1}, {'camera'}, [], {}, None, 5, 1.5, b'camera'])
def test_a_how_that_is_not_text_is_dropped_and_never_raises(how):
    sent = to_cloud_event(ev('habit', habit='hydration', action='sip', how=how))
    assert sent == ev('habit', habit='hydration', action='sip')


ODD_VALUES = [None, True, False, 0, -1, 1.5, 10**400, -10**400, math.nan, math.inf, '', 'x', 'x' * 5000, 'camera',
              'demo.x', 'tasks.due.essay.today', '2026-10-28', b'camera', [], ['camera'], [1, 2], {}, {'a': 1},
              {'demo': True}, {'camera'}, (1, 2), object()]
FIELDS = ['id', 'ts', 'type', 'extra', 'demo', 'rule', 'habit', 'action', 'how', 'healthy', 'say', 'label', 'text',
          'evidence', 'agent', 'kind', 'scenario', 'feature', 'key', 'change', 'object', 'prev_state', 'new_state',
          'device_id', 'conf_avg', 'dwell_sec', 'bbox', 'result', 'after_sec', 'wake', 'bed', 'goals', 'day',
          'totals', 'ml', 'mystery']
TYPES = ['object_state', 'habit', 'nudge', 'rule_outcome', 'alert', 'camera', 'goals', 'daily_totals', 'detection',
         'mystery']


def a_good_event(kind: str) -> dict[str, Any]:
    """An event of this type with every field filled in, so each field has something to be replaced."""
    event: dict[str, Any] = {name: 'abc' for name in FIELDS}
    event.update(id='a', ts=1.0, type=kind, extra={}, demo=False, rule='hydration.low', how='camera', healthy=True,
                 conf_avg=0.5, dwell_sec=2.0, after_sec=3.0, goals={'water_ml': 2000}, day='2026-10-28',
                 totals={'desk_min': 4}, ml=250)
    event.pop('bbox')
    return event


@pytest.mark.parametrize('kind', TYPES)
@pytest.mark.parametrize('field', FIELDS)
def test_odd_values_in_any_field_never_raise(kind, field):
    for value in ODD_VALUES:
        event = {**a_good_event(kind), field: value}
        sent = to_cloud_event(event)
        assert sent is None or isinstance(sent, dict)
        if sent is not None:
            assert to_cloud_event(sent) == sent


@pytest.mark.parametrize('kind', TYPES)
@pytest.mark.parametrize('field', FIELDS)
def test_a_missing_field_never_raises(kind, field):
    event = a_good_event(kind)
    event.pop(field, None)
    sent = to_cloud_event(event)
    assert sent is None or isinstance(sent, dict)


@pytest.mark.parametrize('event', [None, 5, 'habit', ['habit'], (), b'x', 1.5, object()])
def test_something_that_is_not_an_event_stays_home(event):
    assert to_cloud_event(event) is None


def test_a_huge_whole_number_is_not_a_problem():
    sent = to_cloud_event(ev('habit', habit='hydration', action='sip', ml=10**400))
    assert sent is not None and sent['ml'] == 10**400


def test_a_day_in_other_digits_is_not_a_day():
    assert to_cloud_event(ev('daily_totals', day='٢٠٢٦-١٠-٢٨', totals={'desk_min': 1})) is None
