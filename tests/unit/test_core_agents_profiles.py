"""config/agents.yaml → agent profiles, plus personalities the AI wrote (desktalk/core/agents/profiles.py)."""

import logging
import shutil
from pathlib import Path

import pytest

from desktalk.config import Agent, AgentsFile, load_config
from desktalk.core.agents.profiles import ProfileBook
from desktalk.ports import Profiles
from tests.fakes import InMemoryKeyValueStore

ROOT = Path(__file__).resolve().parents[2]
MAX_CHARS = 400


@pytest.fixture(scope='module')
def agents_file(tmp_path_factory) -> AgentsFile:
    """config/agents.yaml as it is in the repo."""
    root = tmp_path_factory.mktemp('project')
    (root / 'config').mkdir()
    for name in ('settings.yaml', 'agents.yaml', 'protocols.yaml', 'prompts.yaml'):
        shutil.copy(ROOT / 'config' / name, root / 'config' / name)
    return load_config(root, environ={}).agents_file


@pytest.fixture
def store():
    return InMemoryKeyValueStore()


@pytest.fixture
def book(agents_file, store):
    return ProfileBook(agents_file, store, personality_max_chars=MAX_CHARS)


def test_it_is_a_profiles_view(book):
    assert isinstance(book, Profiles)


def test_known_labels(book):
    assert book.for_label('laptop').name == 'Computer'
    assert book.for_label('keyboard').id == 'computer'
    assert book.for_label('cell phone').name == 'Phone'
    assert book.for_label('earphone').name == 'AirPods'
    assert book.for_label('Laptop').id == 'computer'           # the detector's case doesn't matter
    assert book.for_label('cup').min_confidence is None        # the cup follows the slider like the rest
    assert book.for_label('laptop').min_confidence is None
    assert 'hydration' in book.for_label('mug').habits
    assert book.get('computer').nickname == 'Monty' and book.get('computer').fragile


def test_ignored_labels(book):
    assert book.for_label('person') is None
    assert book.for_label('Chair') is None
    assert book.for_label('') is None
    assert book.is_ignored('desk') and book.is_ignored('Desk') and not book.is_ignored('cup')


def test_unknown_label_gets_a_profile_made_from_default(book):
    p = book.for_label('stapler')
    assert p.auto and not p.learned
    assert (p.id, p.name, p.object, p.labels) == ('stapler', 'Stapler', 'stapler', ['stapler'])
    assert p.personality.startswith('A stapler on the desk who just found out it can talk.')
    assert p.about == 'A stapler that showed up on your desk.'
    assert p.icon == '✨' and p.temperature == 0.8
    assert p.color == 'cobalt'                                 # same color as before the refactor
    assert p.hello_text() == "Oh hi, I'm the stapler. Mind if I hang out here?"
    assert p.too_close_text('Cup') == 'Cup is getting pretty close to me. Could you give us some room?'
    assert book.for_label('stapler') is p                     # made once
    assert book.get('stapler') is p


def test_two_words_for_the_same_id_share_one_profile(book):
    a = book.for_label('paper clip')
    assert (a.id, a.name) == ('paper-clip', 'Paper Clip')
    assert book.for_label('paper-clip') is a


def test_word_with_a_slash_uses_the_first_word(book):
    p = book.for_label('trash/rubbish')
    assert (p.id, p.name, p.object) == ('trash-rubbish', 'Trash', 'trash')


def test_all_lists_the_agents_from_the_file_and_the_ones_made_since(book):
    ids = [p.id for p in book.all()]
    assert 'computer' in ids and 'cup' in ids and 'stapler' not in ids
    book.for_label('stapler')
    assert 'stapler' in [p.id for p in book.all()]


def test_habit_lookups(book):
    assert {'cup', 'bottle', 'mug'} <= book.labels_for_habit('hydration')
    assert book.ids_for_habit('breaks') == ['computer']           # only Computer speaks for breaks
    assert book.ids_for_habit('no such habit') == []


def test_relationship_risk_in_either_order(book):
    assert book.relationship_risk('computer', 'cup').startswith('Cup or bottle')
    assert book.relationship_risk('cup', 'computer').startswith('Cup or bottle')
    assert book.relationship_risk('pen', 'keys') is None


def test_mention_words(book):
    w = book.mention_words()
    assert w['monty'] == 'computer' and w['laptop'] == 'computer' and w['glug'] == 'cup'
    assert w['pen'] == 'pen' and w['pen/pencil'] == 'pen'


def test_too_close_text(book):
    assert book.get('computer').too_close_text('Cup') == \
        'Cup is getting really close to me. Could you move it back a little?'


def test_an_agent_without_its_own_hello_says_nothing_on_joining(book):
    assert book.get('cable').hello == '' and book.get('cable').hello_text() == ''


def test_missing_lines_in_an_agent_block_come_from_default(agents_file, store):
    small = agents_file.model_copy(update={'agents': {'thing': Agent(labels=('Thing',))}})
    p = ProfileBook(small, store, personality_max_chars=MAX_CHARS).for_label('thing')
    assert (p.id, p.name, p.object, p.labels) == ('thing', 'Thing', 'Thing', ['thing'])
    assert p.icon == '✨' and p.temperature == 0.8 and not p.auto
    assert p.personality.startswith('A Thing on the desk')     # {object} filled in
    assert p.too_close == agents_file.default.too_close and p.hello == ''
    assert p.color == 'mustard'                               # same color as before the refactor


def test_learned_personality_is_remembered(agents_file, store, book):
    book.for_label('stapler')
    book.set_personality('stapler', '  A tiny bodybuilder\n who loves   holding papers together. ')
    assert book.get('stapler').personality == 'A tiny bodybuilder who loves holding papers together.'
    assert book.get('stapler').learned
    again = ProfileBook(agents_file, store, personality_max_chars=MAX_CHARS)
    p = again.for_label('stapler')
    assert p.learned and p.personality == 'A tiny bodybuilder who loves holding papers together.'


def test_learned_personalities_file_from_before_still_loads(agents_file):
    # data/learned_personalities.json as the old app wrote it
    store = InMemoryKeyValueStore({'learned_personalities': {'stapler': 'Grumpy but reliable.'}})
    p = ProfileBook(agents_file, store, personality_max_chars=MAX_CHARS).for_label('stapler')
    assert p.learned and p.personality == 'Grumpy but reliable.'


def test_learned_personalities_are_saved_in_the_same_layout(store, book):
    book.set_personality('stapler', 'Grumpy but reliable.')
    book.set_personality('mug', 'Warm.')
    assert store.data == {'learned_personalities': {'stapler': 'Grumpy but reliable.', 'mug': 'Warm.'}}


def test_a_long_personality_is_cut(book):
    book.set_personality('stapler', 'x' * 500)
    assert book.for_label('stapler').personality == 'x' * MAX_CHARS


def test_an_empty_personality_is_ignored(store, book):
    p = book.for_label('stapler')
    before = p.personality
    book.set_personality('stapler', '   ')
    assert p.personality == before and not p.learned and store.data == {}


def test_a_broken_learned_personalities_value_is_ignored(agents_file):
    store = InMemoryKeyValueStore({'learned_personalities': ['not', 'a', 'dict']})
    p = ProfileBook(agents_file, store, personality_max_chars=MAX_CHARS).for_label('stapler')
    assert not p.learned


class FailingStore(InMemoryKeyValueStore):
    def save(self, key, value):
        raise OSError('disk full')


def test_a_personality_that_cannot_be_saved_is_still_used(agents_file, caplog):
    book = ProfileBook(agents_file, FailingStore(), personality_max_chars=MAX_CHARS)
    with caplog.at_level(logging.WARNING):
        book.set_personality('cup', 'Very calm.')
    assert book.get('cup').personality == 'Very calm.'
    assert 'disk full' in caplog.text


def test_a_new_objects_about_line_comes_from_the_default_block(agents_file, store):
    default = agents_file.default.model_copy(update={'about': 'A new {object} friend.'})
    book = ProfileBook(agents_file.model_copy(update={'default': default}), store, personality_max_chars=MAX_CHARS)
    assert book.for_label('stapler').about == 'A new stapler friend.'
