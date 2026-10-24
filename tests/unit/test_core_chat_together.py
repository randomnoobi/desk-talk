"""The real chat parts together: the AI connection (with a fake OpenAI client) inside ResilientChatModel,
the MessageGenerator and the MessageHub, driven through the event bus. No network, no waiting."""

import queue
import random
from pathlib import Path

import pytest

from desktalk.adapters.llm import OpenAICompatibleChatModel, ResilientChatModel
from desktalk.config import Secrets, load_config
from desktalk.core.chat.message_generator import MessageGenerator
from desktalk.core.chat.message_hub import MessageHub
from desktalk.domain import AgentJoined, AgentProfile, AIUnavailable
from tests.conftest import FakeAI
from tests.fakes import FakeClock, FakeDeskStatus, FakeHabits, FakeProfiles, StaticRoster, SyncEventBus
from tests.unit.test_core_chat_message_generator import FakeChatDesk

CONFIG = load_config(environ={}, dotenv=Path('/nonexistent'))
CUP = AgentProfile(id='cup', name='Cup', object='cup', labels=['cup'], habits=['hydration'], tracker='hydration',
                   hello='Hi, I hold water.')
TODAY = {'hydration': {'sips': 3, 'water_ml': 240, 'goal_ml': 2400, 'pace_ml': 900, 'last_sip_ts': None},
         'breaks': {'count': 1, 'last_break_ts': None, 'sitting_sec': 600, 'sit_limit_min': 30},
         'phone_free': {'counted': False}, 'light': {'sunlight_ts': None}}


class HabitLog:
    def __init__(self):
        self.logged = []

    def log(self, habit, action, **details):
        self.logged.append((habit, action))


def build(reply):
    clock = FakeClock()
    clients = []

    def factory(**kw):
        clients.append(FakeAI(reply=reply, **kw))
        return clients[-1]
    llm = CONFIG.llm.model_copy(update={'min_interval_sec': 0})
    ai = OpenAICompatibleChatModel(llm, Secrets(gemini_api_key='g', openai_api_key='', groq_api_key='', ollama_url=''),
                                   clock, client_factory=factory, replies=CONFIG.prompts.replies)
    model = ResilientChatModel(ai, clock, llm, time_limit_sec=ai.time_limit_sec)
    hub = MessageHub(clock, recent_kept=CONFIG.chat.recent_kept, queue_size=CONFIG.web.live_queue_size)
    habit_log = HabitLog()
    gen = MessageGenerator(model, hub, StaticRoster(['cup']), FakeProfiles([CUP]), FakeHabits(TODAY), FakeChatDesk(),
                           clock, status=FakeDeskStatus(), habit_log=habit_log, llm=llm, chat=CONFIG.chat,
                           agents=CONFIG.agents, sunlight_default_min=CONFIG.habit_tracker.sunlight_default_min,
                           reply_max_chars=500, prompts=CONFIG.prompts, drink_labels=CONFIG.safety.liquids,
                           preview_chars=CONFIG.logging.preview_chars, local_model=ai.is_local, rng=random.Random(0))
    bus = SyncEventBus()
    gen.subscribe_to(bus)
    return clients, model, hub, gen, bus, habit_log


def drain(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


def test_a_join_and_a_group_reply_reach_an_open_page():
    clients, _, hub, gen, bus, habit_log = build('Splash, nice one! [LOG_SIP]')
    page = hub.subscribe()
    bus.publish(AgentJoined(ts=1.0, agent='cup', name='Cup', label='cup', conf=0.9))
    out = gen.answer('group', 'I just drank some water')
    live = drain(page)
    assert [m['type'] for m in live] == ['roster', 'group_message', 'group_message']
    assert live[1]['hello'] and live[2] == out['messages'][0] and live[2]['text'] == 'Splash, nice one!'
    assert [m['text'] for m in hub.recent()] == ['Cup joined the chat', 'Hi, I hold water.', 'Splash, nice one!']
    assert habit_log.logged == [('hydration', 'sip')]
    request = clients[-1].requests[-1]
    system = request['messages'][0]['content']
    assert 'You are Cup' in system and 'LIVE DESK STATUS' in system and "USER'S WATER" in system
    assert request['timeout'] == CONFIG.llm.timeout_sec


def test_with_the_ai_down_the_chat_says_why_and_then_stops_asking():
    clients, model, _, gen, _, _ = build(ConnectionError('Connection refused'))
    for _ in range(CONFIG.llm.breaker_failures):
        reply = gen.answer('cup', 'hi')
        assert reply['messages'] == [] and 'Check the internet connection' in reply['error']
    asked = len(clients[-1].requests)
    assert asked == CONFIG.llm.breaker_failures * CONFIG.llm.max_retries
    assert 'leaving it alone' in gen.answer('cup', 'hi')['error']
    assert len(clients[-1].requests) == asked and model.problem()


def test_an_ollama_that_keeps_answering_with_nothing_is_asked_at_most_max_retries_times():
    """The AI connection already asks Ollama again after an empty reply; the wrapper must not ask all over again."""
    clock = FakeClock()
    clients = []

    def factory(**kw):
        clients.append(FakeAI(reply='', **kw))
        return clients[-1]
    llm = CONFIG.llm.model_copy(update={'min_interval_sec': 0, 'max_retries': 3})
    ai = OpenAICompatibleChatModel(llm, Secrets(gemini_api_key='', openai_api_key='', groq_api_key='',
                                                ollama_url='http://ollama.test/v1'), clock, client_factory=factory,
                                   replies=CONFIG.prompts.replies)
    model = ResilientChatModel(ai, clock, llm, time_limit_sec=ai.time_limit_sec)
    with pytest.raises(AIUnavailable) as err:
        model.chat([{'role': 'user', 'content': 'hi'}], max_tokens=50, temperature=0.5)
    assert 'empty reply' in err.value.reason
    assert sum(len(c.requests) for c in clients) == 3
