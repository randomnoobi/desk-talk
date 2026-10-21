"""ResilientChatModel: time limit, trying again with growing waits, resting after a rate limit, and the
circuit breaker. All timing runs on a FakeClock, so nothing really waits."""

from pathlib import Path

import pytest

from desktalk.adapters.llm.errors import AIRefused
from desktalk.adapters.llm.resilient import ResilientChatModel
from desktalk.config import load_config
from desktalk.domain import AIUnavailable
from tests.contracts.chat_model_contract import ChatModelContract
from tests.fakes import FakeChatModel, FakeClock

HELLO = [{'role': 'user', 'content': 'Say hello.'}]
DOWN = AIUnavailable('Could not reach the AI service.')
LIMIT = AIUnavailable('Gemini hit its free usage limit.', rate_limited=True)
BAD_KEY = AIRefused('Gemini rejected the key in .env.')


@pytest.fixture(scope='module')
def base_llm():
    return load_config(environ={}, dotenv=Path('/nonexistent')).llm


@pytest.fixture
def llm(base_llm):
    """The numbers these tests check, written out by hand."""
    return base_llm.model_copy(update={
        'min_interval_sec': 0, 'max_retries': 3, 'retry_start_sec': 2, 'retry_max_sec': 30, 'timeout_sec': 30,
        'rate_limit_retry_sec': 5, 'pause_on_limit_sec': 600, 'breaker_failures': 3, 'breaker_open_sec': 60,
        'vision_retry_sec': 1})


class Scripted:
    """An AI that answers from a script: each entry is a reply, or an AIUnavailable to raise.
    The last entry repeats. Each call takes `takes` seconds on the clock."""

    label = 'Scripted AI'

    def __init__(self, clock, *script, takes=0.0, ready=True, local_vision=False):
        self.clock, self.script, self.takes, self.ready = clock, list(script), takes, ready
        self.local_vision = local_vision
        self.calls = []

    def _answer(self, kind, background):
        self.calls.append((kind, background, self.clock.monotonic()))
        self.clock.advance(self.takes)
        item = self.script[min(len(self.calls), len(self.script)) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    def chat(self, messages, *, max_tokens, temperature, background=False):
        return self._answer('chat', background)

    def vision(self, jpeg, prompt, *, background=True):
        return self._answer('vision', background)

    def problem(self):
        return None


def chat(model, background=False):
    return model.chat(HELLO, max_tokens=50, temperature=0.7, background=background)


def fails(model, background=False):
    with pytest.raises(AIUnavailable) as err:
        chat(model, background)
    return err.value


# ── passing things through ───────────────────────────────────────────
def test_label_and_ready_come_from_the_ai(llm):
    clock = FakeClock()
    model = ResilientChatModel(Scripted(clock, 'Hi', ready=False), clock, llm)
    assert model.label == 'Scripted AI' and not model.ready


def test_the_request_is_passed_on_as_it_is(llm):
    clock = FakeClock()
    inner = FakeChatModel('Hi!')
    model = ResilientChatModel(inner, clock, llm)
    assert model.chat(HELLO, max_tokens=80, temperature=0.4, background=True) == 'Hi!'
    assert inner.calls[0] == {'kind': 'chat', 'messages': HELLO, 'max_tokens': 80, 'temperature': 0.4,
                              'background': True}


def test_no_ai_set_up_is_said_plainly_and_never_trips_the_breaker(llm):
    clock = FakeClock()
    inner = FakeChatModel(fail='Add a key to .env.', ready=False)
    model = ResilientChatModel(inner, clock, llm)
    for _ in range(5):
        assert fails(model).reason == 'Add a key to .env.'
    assert len(inner.calls) == 5 and clock.slept == []


# ── trying again ─────────────────────────────────────────────────────
def test_a_failed_call_is_tried_again_after_growing_waits(llm):
    clock = FakeClock()
    inner = Scripted(clock, DOWN, DOWN, 'Back!')
    model = ResilientChatModel(inner, clock, llm)
    assert chat(model) == 'Back!'
    assert clock.slept == [2, 4] and model.problem() is None


def test_the_waits_stop_growing_at_the_longest_wait(llm):
    clock = FakeClock()
    model = ResilientChatModel(Scripted(clock, DOWN), clock,
                               llm.model_copy(update={'max_retries': 5, 'retry_max_sec': 5}))
    fails(model)
    assert clock.slept == [2, 4, 5, 5]


def test_after_the_last_try_the_problem_is_kept(llm):
    clock = FakeClock()
    inner = Scripted(clock, DOWN)
    model = ResilientChatModel(inner, clock, llm)
    assert fails(model) is DOWN
    assert len(inner.calls) == 3 and model.problem() == DOWN.reason


def test_a_refusal_is_not_tried_again(llm):
    clock = FakeClock()
    inner = Scripted(clock, BAD_KEY)
    model = ResilientChatModel(inner, clock, llm)
    assert fails(model) is BAD_KEY
    assert len(inner.calls) == 1 and clock.slept == []


def test_no_new_try_starts_after_the_time_limit(llm):
    clock = FakeClock()
    inner = Scripted(clock, DOWN, takes=20)       # each try hangs for 20 s before failing
    model = ResilientChatModel(inner, clock, llm, time_limit_sec=30)
    fails(model)
    # try 1: 0-20 s; wait 2 s; try 2: 22-42 s; a third would start after the 30 s limit
    assert len(inner.calls) == 2


def test_the_time_limit_defaults_to_the_settings(llm):
    clock = FakeClock()
    inner = Scripted(clock, DOWN, takes=29)
    fails(ResilientChatModel(inner, clock, llm.model_copy(update={'timeout_sec': 30})))
    assert len(inner.calls) == 1


# ── rate limits ──────────────────────────────────────────────────────
def test_when_you_are_waiting_a_rate_limit_gets_one_more_try(llm):
    clock = FakeClock()
    inner = Scripted(clock, LIMIT, 'Phew, here I am.')
    model = ResilientChatModel(inner, clock, llm)
    assert chat(model) == 'Phew, here I am.'
    assert clock.slept == [5]


def test_only_one_more_try_after_a_rate_limit(llm):
    clock = FakeClock()
    inner = Scripted(clock, LIMIT)
    model = ResilientChatModel(inner, clock, llm)
    assert fails(model).rate_limited
    assert len(inner.calls) == 2


def test_background_calls_never_try_again_after_a_rate_limit(llm):
    clock = FakeClock()
    inner = Scripted(clock, LIMIT)
    fails(ResilientChatModel(inner, clock, llm), background=True)
    assert len(inner.calls) == 1 and clock.slept == []


def test_after_a_rate_limit_background_calls_rest_but_yours_still_go(llm):
    clock = FakeClock()
    inner = Scripted(clock, LIMIT, 'For you, always.')
    model = ResilientChatModel(inner, clock, llm)
    fails(model, background=True)
    resting = fails(model, background=True)
    assert resting.rate_limited and len(inner.calls) == 1          # not asked: saving the quota
    assert chat(model) == 'For you, always.'
    clock.advance(599)
    fails(model, background=True)
    assert len(inner.calls) == 2
    clock.advance(1)
    assert chat(model, background=True) == 'For you, always.'


# ── the circuit breaker ──────────────────────────────────────────────
def test_after_repeated_failures_the_ai_is_left_alone_for_a_while(llm):
    clock = FakeClock()
    inner = Scripted(clock, DOWN)
    model = ResilientChatModel(inner, clock, llm.model_copy(update={'max_retries': 1}))
    for _ in range(3):
        fails(model)
    err = fails(model)
    assert len(inner.calls) == 3                                    # the 4th call never reached the AI
    assert DOWN.reason in err.reason and model.problem() == err.reason


def test_after_the_pause_one_call_checks_whether_the_ai_is_back(llm):
    clock = FakeClock()
    inner = Scripted(clock, DOWN, DOWN, DOWN, 'I am back!')
    model = ResilientChatModel(inner, clock, llm.model_copy(update={'max_retries': 1}))
    for _ in range(3):
        fails(model)
    clock.advance(59)
    fails(model)
    assert len(inner.calls) == 3
    clock.advance(1)
    assert chat(model) == 'I am back!' and model.problem() is None
    assert chat(model) == 'I am back!'


def test_a_failed_check_leaves_the_ai_alone_again_with_only_one_try(llm):
    clock = FakeClock()
    inner = Scripted(clock, DOWN)
    model = ResilientChatModel(inner, clock, llm)
    for _ in range(3):
        fails(model)
    assert len(inner.calls) == 9
    clock.advance(60)
    fails(model)
    assert len(inner.calls) == 10                                   # just the one check
    clock.advance(59)
    fails(model)
    assert len(inner.calls) == 10


def test_a_working_call_starts_the_count_again(llm):
    clock = FakeClock()
    inner = Scripted(clock, DOWN, DOWN, 'ok', DOWN, DOWN, 'ok again')
    model = ResilientChatModel(inner, clock, llm.model_copy(update={'max_retries': 1}))
    fails(model)
    fails(model)
    chat(model)
    fails(model)
    fails(model)
    assert chat(model) == 'ok again'


def test_an_unexpected_error_is_still_ai_unavailable_and_never_jams_the_breaker(llm):
    clock = FakeClock()
    bug = ValueError('bug in the AI library')
    inner = Scripted(clock, bug, bug, bug, 'Fine now.')
    model = ResilientChatModel(inner, clock, llm.model_copy(update={'max_retries': 1}))
    for _ in range(3):
        assert 'bug in the AI library' in fails(model).reason
    clock.advance(60)
    assert chat(model) == 'Fine now.'


# ── not too often ────────────────────────────────────────────────────
def test_calls_are_spaced_out(llm):
    clock = FakeClock()
    model = ResilientChatModel(Scripted(clock, 'Hi'), clock, llm.model_copy(update={'min_interval_sec': 6}))
    chat(model)
    clock.advance(2)
    chat(model)
    assert clock.slept == [4]
    clock.advance(10)
    chat(model)
    assert clock.slept == [4]


# ── pictures ─────────────────────────────────────────────────────────
def look(model, background=True):
    return model.vision(b'\xff\xd8jpeg', 'What is on the desk?', background=background)


def test_a_failed_picture_question_is_tried_again_after_longer_waits(llm):
    clock = FakeClock()
    model = ResilientChatModel(Scripted(clock, DOWN, DOWN, 'A cup.'), clock, llm)
    assert look(model) == 'A cup.'
    assert clock.slept == [1, 2]


def test_a_picture_question_never_tries_again_after_a_rate_limit(llm):
    clock = FakeClock()
    inner = Scripted(clock, LIMIT)
    model = ResilientChatModel(inner, clock, llm)
    with pytest.raises(AIUnavailable):
        look(model, background=False)
    assert len(inner.calls) == 1 and model.problem() == LIMIT.reason


def test_cloud_picture_questions_rest_after_a_rate_limit(llm):
    clock = FakeClock()
    inner = Scripted(clock, LIMIT, 'A cup.')
    model = ResilientChatModel(inner, clock, llm)
    fails(model, background=True)
    with pytest.raises(AIUnavailable):
        look(model)
    assert len(inner.calls) == 1


def test_picture_questions_on_this_computer_carry_on_after_a_rate_limit(llm):
    clock = FakeClock()
    inner = Scripted(clock, LIMIT, 'A cup.', local_vision=True)
    model = ResilientChatModel(inner, clock, llm)
    assert model.local_vision
    fails(model, background=True)
    assert look(model) == 'A cup.'


def test_failing_picture_questions_do_not_stop_the_chat(llm):
    clock = FakeClock()
    inner = Scripted(clock, DOWN, DOWN, DOWN, 'Chat still works.')
    model = ResilientChatModel(inner, clock, llm.model_copy(update={'max_retries': 1}))
    for _ in range(3):
        with pytest.raises(AIUnavailable):
            look(model)
    with pytest.raises(AIUnavailable):
        look(model)                                                 # pictures are left alone for a while...
    assert len(inner.calls) == 3
    assert chat(model) == 'Chat still works.'                       # ...but the chat isn't
    assert model.problem() is None


# ── the ChatModel contract ───────────────────────────────────────────
class TestResilientChatModel(ChatModelContract):
    @pytest.fixture
    def model(self, llm):
        clock = FakeClock()
        return ResilientChatModel(FakeChatModel('Hello there!'), clock, llm)

    @pytest.fixture
    def broken_model(self, llm):
        clock = FakeClock()
        return ResilientChatModel(FakeChatModel(fail='Could not reach the AI service.'), clock, llm)
