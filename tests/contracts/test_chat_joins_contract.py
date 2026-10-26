"""What every ChatJoins must do (desktalk/ports.py): each kind of object's chat joins, leaves and refusals, for the
developer page's trace. The fake runs it here; the roster (desktalk/core/agents/roster.py) joins as one more test
class in this file, making its two fixtures by feeding it pictures."""

from __future__ import annotations

import pytest

from desktalk.domain import ChatJoin
from desktalk.ports import ChatJoins
from tests.fakes import FakeChatJoins

KINDS = {'joined', 'left', 'refused'}


class ChatJoinsContract:
    """Subclass and give two fixtures: `no_joins`, a ChatJoins that has seen nothing, and `cup_came_and_went`, one
    that saw a cup (detector word 'cup') join the chat and later leave it, and nothing else."""

    @pytest.fixture
    def no_joins(self):
        raise NotImplementedError

    @pytest.fixture
    def cup_came_and_went(self):
        raise NotImplementedError

    def test_it_is_chat_joins(self, no_joins):
        assert isinstance(no_joins, ChatJoins)

    def test_nothing_seen_yet(self, no_joins):
        assert no_joins.joins('cup') == []

    def test_a_join_then_a_leave_oldest_first(self, cup_came_and_went):
        joins = cup_came_and_went.joins('cup')
        assert [j.kind for j in joins] == ['joined', 'left']
        assert joins[0].ts <= joins[1].ts

    def test_each_entry_says_why(self, cup_came_and_went):
        for join in cup_came_and_went.joins('cup'):
            assert isinstance(join, ChatJoin)
            assert join.label == 'cup' and join.agent and join.kind in KINDS
            assert 0 <= join.confidence <= 1 and join.seen_for_sec >= 0
            assert join.on_desk in (True, False, None)
            assert isinstance(join.step, str) and join.step

    def test_other_kinds_of_objects_are_kept_apart(self, cup_came_and_went):
        assert cup_came_and_went.joins('glasses') == []

    def test_what_comes_back_is_a_copy(self, cup_came_and_went):
        cup_came_and_went.joins('cup').clear()
        assert len(cup_came_and_went.joins('cup')) == 2


def a_join(kind: str, ts: float, label: str = 'cup', agent: str = 'cup') -> ChatJoin:
    return ChatJoin(agent=agent, label=label, kind=kind, ts=ts, confidence=0.8, seen_for_sec=3.0, on_desk=None,
                    step='Normal')


class TestFakeChatJoins(ChatJoinsContract):
    @pytest.fixture
    def no_joins(self):
        return FakeChatJoins()

    @pytest.fixture
    def cup_came_and_went(self):
        return FakeChatJoins([a_join('joined', 100.0), a_join('left', 300.0)])


def test_the_fake_keeps_what_is_added_in_time_order():
    fake = FakeChatJoins([a_join('left', 300.0)])
    fake.add(a_join('joined', 100.0))
    fake.add(a_join('refused', 50.0, label='glasses', agent='glasses'))
    assert [j.kind for j in fake.joins('cup')] == ['joined', 'left']
    assert [j.kind for j in fake.joins('glasses')] == ['refused']
