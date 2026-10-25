"""Names in the chat: @mentions, agents named in plain text, and who answers in the group."""

import random

import pytest

from desktalk.core.chat import names
from desktalk.domain import AgentProfile
from tests.fakes import FakeProfiles

COMPUTER = AgentProfile(id='computer', name='Computer', nickname='Monty', object='laptop', labels=['laptop'],
                        habits=['breaks'], fragile=True)
CUP = AgentProfile(id='cup', name='Cup', object='cup', labels=['cup', 'mug'], habits=['hydration'])
PAPER = AgentProfile(id='paper', name='Paper', object='paper', labels=['paper/notebook'])


@pytest.fixture
def profiles():
    return FakeProfiles([COMPUTER, CUP, PAPER], ignored=['person', 'chair'])


@pytest.fixture
def words(profiles):
    return profiles.mention_words()


def test_mention_words_come_from_the_profiles(words):
    assert words['monty'] == 'computer' and words['computer'] == 'computer' and words['laptop'] == 'computer'
    assert words['mug'] == 'cup' and words['paper/notebook'] == 'paper' and words['paper'] == 'paper'


def test_mentions(words):
    assert names.parse_mentions('hey @monty and @cup, also @nobody', words) == ['computer', 'cup']


def test_speaker_names_include_lens(profiles):
    assert names.speaker_names(profiles.all()) == {'computer': 'computer', 'cup': 'cup', 'paper': 'paper',
                                                   'lens': 'lens'}


def test_group_speaker_order(words):
    members, known, rng = ['computer', 'cup'], ['computer', 'cup', 'paper'], random.Random(1)
    assert names.group_speaker_order('@paper how are you', words, members, known, rng) == ['paper']
    assert names.group_speaker_order('@cup @monty hi', words, members, known, rng) == ['cup', 'computer']
    assert names.group_speaker_order('cup, what do you think about the laptop?', words, members, known, rng) == \
        ['cup']
    assert names.group_speaker_order('hello everyone', words, members, known, rng)[0] in members
    assert names.group_speaker_order('hello everyone', words, [], known, rng) == []
    assert names.group_speaker_order('@cup @monty @paper hi', words, members, known, rng, most=2) == \
        ['cup', 'computer']


def test_named_agents_without_an_at(words):
    assert names.detect_named('is the cup next to monty?', words) == ['computer', 'cup']
    assert names.detect_named('is the cupboard open?', words) == []
