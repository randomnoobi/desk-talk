"""The habit rules in config/protocols.yaml (desktalk/config.py: RuleConfig, Condition, Duration ...).

Like the other config tests, these load from copies of the real config files in a temp folder.
"""

from __future__ import annotations

import copy
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import get_args

import pytest
import yaml
from pydantic import ValidationError

from desktalk.config import ConfigError, Habit, RuleConfig, load_config, rule_timezone
from desktalk.domain.habits import HABIT_WORDS

ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILES = ('settings.yaml', 'agents.yaml', 'protocols.yaml')

# Every drink Cup knows (config/agents.yaml), less measuring cup and kettle.
DRINKS = ['cup', 'mug', 'bottle', 'water bottle', 'wine glass']

# The hydration rule from the plan, as it is written in the YAML.
HYDRATION = {
    'id': 'hydration',
    'enabled': True,
    'habit': 'hydration',
    'timezone': 'local',
    'gate': {'state': {'object': 'person', 'is': 'present'}},
    'when': {'all': [
        {'state': {'object': 'person', 'is': 'present', 'for': '60m'}},
        {'no_event': {'object': DRINKS, 'events': ['moved', 'removed'], 'for': '60m'}},
        {'no_habit': {'habit': 'hydration', 'for': '60m'}},
    ]},
    'cooldown': '30m',
    'max_nudges_per_hour': 4,
    'quiet_hours': {'start': '22:00', 'end': '07:00'},
    'message': "You've been at the desk for an hour without a drink. Time for some water?",
    'success': {'within': '5m', 'when': {'any': [
        {'event': {'object': DRINKS, 'events': ['moved', 'removed']}},
        {'habit': {'habit': 'hydration'}},
    ]}},
}


@pytest.fixture
def project(tmp_path):
    """A project folder holding copies of the real config files (no .env, no settings.local.yaml)."""
    (tmp_path / 'config').mkdir()
    for name in CONFIG_FILES:
        shutil.copy(ROOT / 'config' / name, tmp_path / 'config' / name)
    return tmp_path


def write_rules(project, rules):
    """Replace the rules in the copy of protocols.yaml."""
    path = project / 'config' / 'protocols.yaml'
    data = yaml.safe_load(path.read_text())
    data['rules'] = rules
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def rule_with(**change):
    rule = copy.deepcopy(HYDRATION)
    rule.update(change)
    return rule


def load_rules(project, *rules):
    write_rules(project, list(rules))
    return load_config(root=project, environ={}).protocols.rules


def load_rule(project, **change):
    return load_rules(project, rule_with(**change))[0]


def test_hydration_rule_loads_as_written(project):
    rule = load_config(root=project, environ={}).protocols.rules[0]
    assert rule.id == 'hydration' and rule.habit == 'hydration'
    assert rule.enabled is True                        # it took over from the old hydration nudges
    assert rule.cooldown == 1800 and rule.max_nudges_per_hour == 4
    assert rule.quiet_hours.start == '22:00' and rule.quiet_hours.end == '07:00'
    assert rule.gate.state.object == 'person' and rule.gate.state.is_ == 'present'
    first, second, third = rule.when.all
    assert first.state.for_ == 3600
    assert second.no_event.object == ('cup', 'mug', 'bottle', 'water bottle', 'wine glass')
    assert second.no_event.events == ('moved', 'removed') and second.no_event.for_ == 3600
    assert (third.no_habit.habit, third.no_habit.for_) == ('hydration', 3600)
    assert rule.success.within == 300
    moved, sipped = rule.success.when.any
    assert moved.event.object == second.no_event.object and moved.event.events == ('moved', 'removed')
    assert sipped.habit.habit == 'hydration'


def test_one_object_is_kept_as_a_list_of_one(project):
    rule = load_rule(project, when={'no_event': NO_EVENT})
    assert rule.when.no_event.object == ('bottle',)
    success = {'within': '5m', 'when': {'event': {'object': 'bottle', 'events': ['moved']}}}
    assert load_rule(project, success=success).success.when.event.object == ('bottle',)


def test_the_rule_engine_check_time_is_a_setting(project):
    assert load_config(root=project, environ={}).rule_engine.check_every_sec == 60


def test_the_grace_after_a_success_deadline_is_a_setting(project):
    assert load_config(root=project, environ={}).rule_engine.grace_sec == 5


def test_rules_can_be_built_from_the_yaml_keys():
    rule = RuleConfig.model_validate(HYDRATION)
    assert rule.when.all[0].state.for_ == 3600
    assert rule.gate.state.for_ is None


def test_rules_are_read_only():
    rule = RuleConfig.model_validate(HYDRATION)
    with pytest.raises(ValidationError):
        rule.cooldown = 1


def test_the_python_names_are_not_yaml_keys():
    gate = {'state': {'object': 'person', 'is_': 'present'}}
    with pytest.raises(ValidationError, match='is_'):
        RuleConfig.model_validate(rule_with(gate=gate))


def test_a_rule_without_quiet_hours_or_success_is_fine():
    rule = copy.deepcopy(HYDRATION)
    del rule['quiet_hours'], rule['success']
    built = RuleConfig.model_validate(rule)
    assert built.quiet_hours is None and built.success is None


@pytest.mark.parametrize('text, seconds', [('30s', 30), ('5m', 300), ('1.5h', 5400), ('0s', 0)])
def test_durations_become_seconds(project, text, seconds):
    assert load_rule(project, cooldown=text).cooldown == seconds


@pytest.mark.parametrize('bad', [60, 60.5, '60', 'soon', '5 minutes', '-5m', '', None, True])
def test_a_duration_that_is_not_a_time_like_30m_is_refused(project, bad):
    with pytest.raises(ConfigError) as err:
        load_rule(project, cooldown=bad)
    assert 'rules[0].cooldown must be a time like 30m' in str(err.value)


NO_EVENT = {'object': 'bottle', 'events': ['moved'], 'for': '60m'}


@pytest.mark.parametrize('change, words', [
    ({'when': {'no_evnt': NO_EVENT}}, ['config/protocols.yaml', 'rules[0]', 'no_evnt']),
    ({'gate': {'state': {'object': 'person', 'is': 'presnt'}}}, ['rules[0].gate', 'presnt']),
    ({'cooldown': 60}, ['rules[0].cooldown', 'a time like 30m']),
    ({'timezone': 'Mars/Base'}, ['rules[0].timezone', 'Mars/Base']),
    ({'quiet_hours': {'start': '22:00', 'end': '22:00'}}, ['rules[0].quiet_hours']),
    ({'when': {'event': {'object': 'bottle', 'events': ['moved']}}},
     ['rules[0].when', 'only works inside success']),
    ({'when': {'no_habit': {'habit': 'hydration', 'action': 'sip', 'for': '60m'}}},
     ['rules[0].when.no_habit.action', 'action']),
    ({'success': {'within': '5m', 'when': {'habit': {'habit': 'hydration', 'action': 'sip'}}}},
     ['rules[0].success.when.habit.action', 'action']),
    ({'when': {'habit': {'habit': 'hydration'}}},
     ['rules[0].when', 'only works inside success']),
    ({'when': {'no_habit': {'habit': 'hydrate', 'for': '60m'}}},
     ['rules[0].when.no_habit.habit', 'hydrate']),
    ({'success': {'within': '5m', 'when': {'habit': {'habit': 'sleep'}}}},
     ['rules[0].success.when.habit.habit', 'sleep']),
    ({'when': {'no_event': {'object': [], 'events': ['moved'], 'for': '60m'}}},
     ['rules[0].when.no_event.object']),
    ({'success': {'within': '5m', 'when': {'event': {'object': [], 'events': ['moved']}}}},
     ['rules[0].success.when.event.object']),
])
def test_rule_typos_name_the_rule_and_the_word(project, change, words):
    with pytest.raises(ConfigError) as err:
        load_rule(project, **change)
    assert all(w in str(err.value) for w in words), str(err.value)


def test_habit_is_refused_deep_inside_gate_and_under_not_in_success(project):
    sip = {'habit': {'habit': 'hydration'}}
    with pytest.raises(ConfigError) as err:
        load_rule(project, gate={'any': [sip]})
    assert 'rules[0].gate: habit only works inside success' in str(err.value)
    with pytest.raises(ConfigError) as err:
        load_rule(project, success={'within': '5m', 'when': {'not': sip}})
    assert 'rules[0].success.when' in str(err.value) and 'no_habit' in str(err.value)


def test_a_rule_names_only_the_habit(project):
    for habit in get_args(Habit):
        rule = load_rule(project, when={'no_habit': {'habit': habit, 'for': '5m'}})
        assert rule.when.no_habit.habit == habit


def test_an_old_action_key_is_refused_and_names_the_rule(project):
    with pytest.raises(ConfigError) as err:
        load_rule(project, when={'no_habit': {'habit': 'hydration', 'action': 'sip', 'for': '60m'}})
    assert 'rules[0].when.no_habit.action' in str(err.value) and 'action' in str(err.value)
    with pytest.raises(ConfigError) as err:
        load_rule(project, success={'within': '5m', 'when': {'habit': {'habit': 'hydration', 'action': 'sip'}}})
    assert 'rules[0].success.when.habit.action' in str(err.value)


def test_every_habit_the_config_knows_has_plain_words_for_the_evidence():
    assert set(HABIT_WORDS) == set(get_args(Habit))


def test_the_drink_list_is_written_once_and_both_places_get_all_five(project):
    text = (project / 'config' / 'protocols.yaml').read_text()
    assert text.count('wine glass') == 1                          # one anchor, one alias
    rule = load_config(root=project, environ={}).protocols.rules[0]
    assert rule.when.all[1].no_event.object == tuple(DRINKS)
    assert rule.success.when.any[0].event.object == tuple(DRINKS)


def test_a_misspelled_condition_says_what_was_meant(project):
    with pytest.raises(ConfigError) as err:
        load_rule(project, when={'no_evnt': NO_EVENT})
    assert 'did you mean no_event' in str(err.value)


@pytest.mark.parametrize('when', [
    {},
    5,
    {'state': {'object': 'person', 'is': 'present'}, 'no_event': NO_EVENT},
], ids=['empty', 'not a group', 'two kinds'])
def test_a_condition_has_exactly_one_kind(project, when):
    with pytest.raises(ConfigError) as err:
        load_rule(project, when=when)
    assert 'rules[0].when: a condition needs exactly one of' in str(err.value)


@pytest.mark.parametrize('kind', ['all', 'any'])
def test_all_and_any_need_something_in_them(project, kind):
    with pytest.raises(ConfigError) as err:
        load_rule(project, when={kind: []})
    assert f'rules[0].when.{kind}' in str(err.value)


def test_a_misspelled_key_inside_a_check_is_named(project):
    with pytest.raises(ConfigError) as err:
        load_rule(project, when={'no_event': {**NO_EVENT, 'fro': '5m'}})
    text = str(err.value)
    assert 'rules[0].when.no_event.fro is not a known setting' in text
    assert 'did you mean rules[0].when.no_event.for' in text


def test_a_no_event_check_needs_a_time_and_at_least_one_event(project):
    with pytest.raises(ConfigError) as err:
        load_rule(project, when={'no_event': {'object': 'bottle', 'events': ['moved']}})
    assert 'rules[0].when.no_event.for is missing' in str(err.value)
    with pytest.raises(ConfigError) as err:
        load_rule(project, success={'within': '5m', 'when': {'event': {'object': 'bottle', 'events': []}}})
    assert 'rules[0].success.when.event.events' in str(err.value)


def test_an_unknown_kind_of_change_is_refused(project):
    with pytest.raises(ConfigError) as err:
        load_rule(project, success={'within': '5m', 'when': {'event': {'object': 'bottle', 'events': ['spilled']}}})
    assert 'spilled' in str(err.value) and 'appeared, removed, moved' in str(err.value)


def test_event_is_refused_deep_inside_gate_or_when_but_fine_inside_success(project):
    event = {'event': {'object': 'bottle', 'events': ['moved']}}
    with pytest.raises(ConfigError) as err:
        load_rule(project, gate={'all': [{'not': event}]})
    assert 'rules[0].gate: event only works inside success' in str(err.value)
    nested = {'any': [{'all': [event]}]}
    assert load_rule(project, success={'within': '5m', 'when': nested}).success.within == 300


@pytest.mark.parametrize('when', [
    {'not': {'event': {'object': 'cell phone', 'events': ['appeared']}}},
    {'all': [{'state': {'object': 'person', 'is': 'present'}},
             {'not': {'any': [{'event': {'object': 'cell phone', 'events': ['appeared']}}]}}]},
])
def test_event_under_not_in_success_is_refused_and_no_event_is_suggested(project, when):
    with pytest.raises(ConfigError) as err:
        load_rule(project, success={'within': '10m', 'when': when})
    text = str(err.value)
    assert 'rules[0].success.when' in text and 'not' in text and 'no_event' in text


def test_not_around_a_state_in_success_is_fine(project):
    when = {'not': {'state': {'object': 'person', 'is': 'present'}}}
    assert load_rule(project, success={'within': '2m', 'when': when}).success.within == 120


def test_two_rules_with_the_same_id_is_an_error(project):
    with pytest.raises(ConfigError) as err:
        load_rules(project, rule_with(), rule_with(cooldown='1h'))
    assert 'hydration' in str(err.value) and 'config/protocols.yaml' in str(err.value)


def test_two_rules_with_different_ids_load_in_order(project):
    rules = load_rules(project, rule_with(), rule_with(id='breaks', habit='breaks'))
    assert [r.id for r in rules] == ['hydration', 'breaks']


def test_no_rules_is_fine(project):
    assert load_rules(project) == ()


def test_the_error_names_the_second_rule(project):
    with pytest.raises(ConfigError) as err:
        load_rules(project, rule_with(), rule_with(id='breaks', habit='breaks', cooldown=5))
    assert 'rules[1].cooldown' in str(err.value)


@pytest.mark.parametrize('quiet', [
    {'start': '25:00', 'end': '07:00'},
    {'start': '22:00', 'end': '07:60'},
    {'start': '7:00', 'end': '07:00'},
    {'start': '22:00'},
])
def test_quiet_hours_must_be_real_and_different_clock_times(project, quiet):
    with pytest.raises(ConfigError) as err:
        load_rule(project, quiet_hours=quiet)
    assert 'rules[0].quiet_hours' in str(err.value)


def test_quiet_hours_may_be_left_empty(project):
    assert load_rule(project, quiet_hours=None).quiet_hours is None


def test_rule_timezone_kinds():
    july = datetime(2026, 7, 1, 12, 0)
    assert rule_timezone('local').utcoffset(july) == july.astimezone().utcoffset()
    assert rule_timezone('America/Los_Angeles').utcoffset(july) == timedelta(hours=-7)
    assert rule_timezone('+08:00').utcoffset(july) == timedelta(hours=8)
    assert rule_timezone('-03:30').utcoffset(july) == timedelta(hours=-3, minutes=-30)


def test_a_place_name_follows_daylight_saving_and_an_offset_does_not():
    january = datetime(2026, 1, 15, 12, 0)
    assert rule_timezone('America/Los_Angeles').utcoffset(january) == timedelta(hours=-8)
    assert rule_timezone('-07:00').utcoffset(january) == timedelta(hours=-7)


def test_local_time_follows_the_computers_daylight_saving():
    zone = rule_timezone('local')
    for when in (datetime(2026, 1, 15, 12, 0), datetime(2026, 7, 1, 12, 0)):
        assert zone.utcoffset(when) == when.astimezone().utcoffset()
        moment = when.astimezone(UTC)                  # the same moment, in UTC
        assert moment.astimezone(zone).replace(tzinfo=None) == when


@pytest.mark.parametrize('name', ['Mars/Base', '', '8:00', '+8:00', '+25:00', '+08:60', 'LOCAL'])
def test_a_bad_timezone_is_refused(project, name):
    with pytest.raises(ConfigError) as err:
        load_rule(project, timezone=name)
    assert 'rules[0].timezone' in str(err.value)


def test_a_rule_timezone_is_worked_out_once_per_name():
    assert rule_timezone('+08:00') is rule_timezone('+08:00')
    assert rule_timezone('local') is rule_timezone('local')


def test_rule_timezone_refuses_a_bad_name():
    with pytest.raises(ValueError):
        rule_timezone('Mars/Base')


def test_any_all_not_nest(project):
    state = {'state': {'object': 'person', 'is': 'present'}}
    when = {'all': [{'any': [state, {'state': {'object': 'cup', 'is': 'unknown', 'for': '5m'}}]},
                    {'not': {'state': {'object': 'phone', 'is': 'present'}}}]}
    rule = load_rule(project, when=when)
    any_, not_ = rule.when.all
    assert len(any_.any) == 2 and any_.any[1].state.for_ == 300
    assert not_.not_.state.object == 'phone' and not_.not_.state.is_ == 'present'


def test_a_wrong_state_in_a_deep_condition_lists_the_choices(project):
    when = {'all': [{'not': {'state': {'object': 'phone', 'is': 'there'}}}]}
    with pytest.raises(ConfigError) as err:
        load_rule(project, when=when)
    assert ('rules[0].when.all[0].not.state.is must be one of: present, absent, unknown (got "there")'
            in str(err.value))


def test_a_missing_rule_setting_names_the_file(project):
    rule = rule_with()
    del rule['cooldown']
    with pytest.raises(ConfigError) as err:
        load_rules(project, rule)
    assert 'config/protocols.yaml: rules[0].cooldown is missing' in str(err.value)


def test_max_nudges_per_hour_must_be_a_whole_number_above_0(project):
    with pytest.raises(ConfigError) as err:
        load_rule(project, max_nudges_per_hour=0)
    assert 'rules[0].max_nudges_per_hour must be a whole number above 0' in str(err.value)


def test_an_unknown_habit_is_refused(project):
    with pytest.raises(ConfigError) as err:
        load_rule(project, habit='hydraton')
    assert 'rules[0].habit' in str(err.value) and 'hydraton' in str(err.value)


def test_a_bad_item_in_a_list_is_not_also_reported_as_an_empty_list(project):
    with pytest.raises(ConfigError) as err:
        load_rule(project, when={'all': [{'state': {'object': 'person', 'is': 'presnt'}}]})
    lines = str(err.value).splitlines()
    assert len(lines) == 1 and 'rules[0].when.all[0].state.is' in lines[0]
