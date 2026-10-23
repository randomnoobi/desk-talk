"""Reading the AI's JSON answers, for the background chat jobs."""

from __future__ import annotations

from desktalk.core.chat.workers.text import json_in


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
