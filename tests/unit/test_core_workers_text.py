"""Text helpers the background chat jobs share: reading the AI's JSON, keeping one speaker, hiding action tags."""

from __future__ import annotations

from desktalk.core.chat.workers.text import json_in, single_speaker_text, strip_action_tags
from desktalk.domain import AgentProfile

CUP = AgentProfile(id='cup', name='Cup')
NAMES = {'cup': 'cup', 'computer': 'computer'}


def test_json_in_finds_the_object_inside_other_words():
    assert json_in('Sure! {"continue": true, "next": "Cup"} hope that helps') == {'continue': True, 'next': 'Cup'}


def test_json_in_reads_an_object_over_several_lines():
    assert json_in('{\n "visible": ["keys"]\n}') == {'visible': ['keys']}


def test_json_in_gives_none_for_no_answer_broken_json_or_a_list():
    assert json_in(None) is None
    assert json_in('') is None
    assert json_in('no json here') is None
    assert json_in('{"posture": "good",}') is None
    assert json_in('["keys"]') is None


def test_single_speaker_text_keeps_only_the_agents_own_words():
    assert single_speaker_text('Cup: I am fine', CUP, NAMES) == 'I am fine'
    assert single_speaker_text('Computer: help!', CUP, NAMES) == ''
    assert single_speaker_text('Just a line', CUP, NAMES) == 'Just a line'


def test_single_speaker_text_drops_other_agents_lines_from_a_script():
    script = 'Cup: Morning all!\nComputer: Morning, Cup.\nCup: Coffee time?'
    assert single_speaker_text(script, CUP, NAMES) == 'Morning all! Coffee time?'


def test_strip_action_tags_hides_tags_but_keeps_other_brackets():
    assert strip_action_tags('Done [ADD_TODO:Essay] and [LOG_SUNLIGHT:10] ok [not a tag]') == 'Done and ok [not a tag]'
    assert strip_action_tags('Splash! [LOG_SIP]') == 'Splash!'
