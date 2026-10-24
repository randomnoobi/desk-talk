"""Checks on an AI reply: one speaker only, not cut off, no dramatic claims the camera didn't see."""

from pathlib import Path

import pytest

from desktalk.config import load_config
from desktalk.core.chat import replies
from desktalk.domain import AgentProfile

CHAT = load_config(environ={}, dotenv=Path('/nonexistent')).chat.model_copy(update={'min_reply_words': 4})
CUP = AgentProfile(id='cup', name='Cup', object='cup', labels=['cup', 'mug'], habits=['hydration'])


def test_single_speaker_text():
    names = {'cup': 'cup', 'computer': 'computer'}
    assert replies.single_speaker_text('Cup: I am fine', CUP, names, 500) == 'I am fine'
    assert replies.single_speaker_text('Computer: help!', CUP, names, 500) == ''
    assert replies.single_speaker_text('Just a line', CUP, names, 500) == 'Just a line'
    assert replies.single_speaker_text('Cup: one\nComputer: two\nthree', CUP, names, 500) == 'one three'
    assert replies.single_speaker_text('x' * 600, CUP, names, 500) == 'x' * 500


@pytest.mark.parametrize('text, cut', [
    ('Hi', True), ('I am here for you', False), ('Could you move it to the', True), ('Move me back a bit.', False),
])
def test_cut_off_replies(text, cut):
    assert replies.looks_cut_off(text, CHAT.min_reply_words) is cut


def test_evidence_check():
    calm = {'summary': 'NO ACTIVE DANGERS', 'dangers': []}
    assert replies.response_violates_evidence('The laptop is about to shatter!', calm)
    assert replies.response_violates_evidence('Careful, spill risk!', calm)
    assert not replies.response_violates_evidence('No spill risk today, all good.', calm)
    assert not replies.response_violates_evidence('Nice to meet you!', calm)
    assert not replies.response_violates_evidence('Careful, spill risk!', {'summary': '', 'dangers': [{'label': 'x'}]})
    assert not replies.response_violates_evidence('It might break!', calm, user_message='will it break?')
