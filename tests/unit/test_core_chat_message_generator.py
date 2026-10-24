"""MessageGenerator: turns events into chat lines, phrases habit reminders and answers the user.
Built from fakes only: a scripted AI, a recording notifier, a FakeClock."""

import random
from pathlib import Path

import pytest

from desktalk.config import load_config
from desktalk.core.chat.message_generator import ChatDesk, MessageGenerator, UnknownChat
from desktalk.domain import AgentJoined, AgentLeft, AgentProfile, Alert, HabitEvent, Nudge, SeenObject
from desktalk.ports import ChatVoice
from tests.fakes import (
    FakeCharacterMemory,
    FakeChatModel,
    FakeClock,
    FakeDeskScene,
    FakeDeskStatus,
    FakeHabits,
    FakeProfiles,
    FakeSafetyBoard,
    RecordingNotifier,
    StaticRoster,
    SyncEventBus,
)

CONFIG = load_config(environ={}, dotenv=Path('/nonexistent'))


def computer():
    return AgentProfile(id='computer', name='Computer', nickname='Monty', object='laptop', labels=['laptop'],
                        habits=['breaks', 'screens'], tracker='breaks', fragile=True, temperature=0.6,
                        personality='A tired laptop.', too_close='{other} is too close to my keyboard!',
                        hello='Monty here, booting up.')


def cup():
    return AgentProfile(id='cup', name='Cup', object='cup', labels=['cup'], habits=['hydration'],
                        tracker='hydration', temperature=0.8, personality='A bubbly cup.', hello='Hi, I hold water.')


def stapler():
    return AgentProfile(id='stapler', name='Stapler', object='stapler', labels=['stapler'], auto=True,
                        personality='A stapler on the desk who just found out it can talk.', hello='')


def today():
    return {'hydration': {'sips': 3, 'water_ml': 240, 'goal_ml': 2400, 'pace_ml': 900, 'last_sip_ts': None},
            'breaks': {'count': 1, 'last_break_ts': None, 'sitting_sec': 2100, 'sit_limit_min': 30},
            'food': {'entries': []},
            'phone_free': {'counted': True, 'phone_free_sec': 600, 'streak_sec': 60, 'goal_min': 120,
                           'phone_on_desk': False},
            'light': {'sunlight_ts': None, 'lux': None, 'brightness': 120.0}}


def seen(label, x1, y1, x2, y2, conf=0.9):
    """An agent's box on the desk, as the camera side writes it."""
    return SeenObject(label=label, box=(x1, y1, x2, y2), center=((x1 + x2) / 2, (y1 + y2) / 2), confidence=conf)


class FakeChatDesk(FakeCharacterMemory, FakeDeskScene, FakeSafetyBoard):
    """The three desk interfaces the chat uses, in one object (as DeskState is until Task 10)."""

    def __init__(self):
        FakeCharacterMemory.__init__(self)
        FakeDeskScene.__init__(self)
        FakeSafetyBoard.__init__(self)


class HabitLog:
    def __init__(self):
        self.logged = []

    def log(self, habit, action, **details):
        self.logged.append((habit, action, details))


class Rig:
    """A MessageGenerator built from fakes. `reply` / `fail` script the AI."""

    def __init__(self, reply='Hey! All good over here on the desk.', fail='', members=('computer', 'cup'),
                 profiles=None, local_model=False, llm=None, chat=None, agents=None, camera_on=True, prompts=None):
        self.clock = FakeClock()
        self.ai = FakeChatModel(reply=reply, fail=fail)
        self.notifier = RecordingNotifier(self.clock)
        self.roster = StaticRoster(members)
        self.profiles = FakeProfiles(profiles or [computer(), cup(), stapler()], ignored=['person'])
        self.habits = FakeHabits(today())
        self.desk = FakeChatDesk()
        self.status = FakeDeskStatus(connected=camera_on)
        self.habit_log = HabitLog()
        self.gen = MessageGenerator(
            self.ai, self.notifier, self.roster, self.profiles, self.habits, self.desk, self.clock,
            status=self.status, habit_log=self.habit_log, llm=llm or CONFIG.llm, chat=chat or CONFIG.chat,
            agents=agents or CONFIG.agents, sunlight_default_min=10, reply_max_chars=500,
            prompts=prompts or CONFIG.prompts, drink_labels=CONFIG.safety.liquids,
            preview_chars=CONFIG.logging.preview_chars, local_model=local_model, rng=random.Random(4))

    def sent(self, **match):
        return [m for m in self.notifier.sent if all(m.get(k) == v for k, v in match.items())]


def test_it_is_a_chat_voice():
    assert isinstance(Rig().gen, ChatVoice)


def test_the_fake_desk_has_what_the_chat_needs():
    assert isinstance(FakeChatDesk(), ChatDesk)


def test_chat_uses_prompt_settings():
    chat = CONFIG.prompts.chat.model_copy(update={'identity': 'You are {name}, a talking {object}.'})
    r = Rig(prompts=CONFIG.prompts.model_copy(update={'chat': chat}))
    r.gen.answer('cup', 'hi', [])
    assert r.ai.calls[0]['messages'][0]['content'].startswith('You are Cup, a talking cup.\n\n---\nLIVE DESK STATUS')


# ── joining and leaving ──────────────────────────────────────────────
def joined(agent='cup', name='Cup', ts=1.0):
    return AgentJoined(ts=ts, agent=agent, name=name, label=agent, conf=0.9)


def test_a_join_is_announced_with_the_agents_card():
    r = Rig()
    r.gen.on_roster(joined())
    note = r.sent(type='roster')[0]
    assert note['action'] == 'joined' and note['text'] == 'Cup joined the chat' and note['quiet'] is False
    assert note['agent'] == {**cup().public(), 'on_desk': True}


def test_a_join_can_be_quiet():
    r = Rig(agents=CONFIG.agents.model_copy(update={'announce': False}))
    r.gen.on_roster(joined())
    assert r.sent(type='roster')[0]['quiet'] is True


def test_a_leave_is_announced_without_a_hello():
    r = Rig()
    r.gen.on_roster(AgentLeft(ts=1.0, agent='cup', name='Cup', label='cup', stayed_sec=60))
    assert [(m['type'], m.get('action'), m['text']) for m in r.notifier.sent] == \
        [('roster', 'left', 'Cup left the chat')]
    assert r.notifier.sent[0]['agent']['on_desk'] is False


def test_an_unknown_agent_is_not_announced():
    r = Rig()
    r.gen.on_roster(joined(agent='ghost', name='Ghost'))
    assert r.notifier.sent == []


def test_a_new_agent_says_hello_but_not_again_too_soon():
    r = Rig(agents=CONFIG.agents.model_copy(update={'greet_on_join': True, 'greet_again_after_min': 30}))
    r.gen.on_roster(joined())
    assert [m['text'] for m in r.sent(hello=True)] == ['Hi, I hold water.']
    assert r.sent(hello=True)[0]['character'] == 'cup' and r.sent(hello=True)[0]['type'] == 'group_message'
    r.clock.advance(29 * 60)
    r.gen.on_roster(joined())
    assert len(r.sent(hello=True)) == 1
    r.clock.advance(60)
    r.gen.on_roster(joined())
    assert len(r.sent(hello=True)) == 2


def test_no_hello_when_greetings_are_off():
    r = Rig(agents=CONFIG.agents.model_copy(update={'greet_on_join': False}))
    r.gen.on_roster(joined())
    assert r.sent(hello=True) == []


def test_an_object_without_its_own_block_gets_a_personality_from_the_ai():
    r = Rig(reply='A tiny bodybuilder who holds papers together and brags about it.',
            agents=CONFIG.agents.model_copy(update={'invent_personality': True}))
    r.gen.on_roster(joined(agent='stapler', name='Stapler'))
    p = r.profiles.get('stapler')
    assert p.learned and 'bodybuilder' in p.personality
    call = r.ai.calls[0]
    assert call['background'] and 'Object: stapler' in call['messages'][-1]['content']
    assert call['max_tokens'] == CONFIG.llm.replies.personality.max_tokens
    assert call['temperature'] == CONFIG.llm.replies.personality.temperature


def test_a_too_short_personality_is_not_used():
    r = Rig(reply='Staples.', agents=CONFIG.agents.model_copy(update={'invent_personality': True}))
    r.gen.on_roster(joined(agent='stapler', name='Stapler'))
    assert not r.profiles.get('stapler').learned


def test_no_personality_is_invented_when_that_is_off():
    r = Rig(agents=CONFIG.agents.model_copy(update={'invent_personality': False}))
    r.gen.on_roster(joined(agent='stapler', name='Stapler'))
    assert r.ai.calls == []


def test_no_personality_is_invented_for_a_known_or_already_learned_agent():
    learned = stapler()
    learned.learned = True
    r = Rig(profiles=[computer(), cup(), learned], agents=CONFIG.agents.model_copy(update={'invent_personality': True}))
    r.gen.on_roster(joined(agent='stapler', name='Stapler'))
    r.gen.on_roster(joined(agent='cup', name='Cup'))
    assert r.ai.calls == []


def test_with_the_ai_down_a_new_object_still_joins():
    r = Rig(fail='Gemini hit its free usage limit.',
            agents=CONFIG.agents.model_copy(update={'invent_personality': True}))
    r.gen.on_roster(joined(agent='stapler', name='Stapler'))
    assert r.sent(type='roster')[0]['text'] == 'Stapler joined the chat'
    assert not r.profiles.get('stapler').learned


def test_the_generator_listens_to_the_bus():
    r = Rig()
    bus = SyncEventBus()
    r.gen.subscribe_to(bus)
    bus.publish(joined())
    bus.publish(HabitEvent(ts=2.0, habit='hydration', action='sip', say='Sip detected!'))
    bus.publish(Alert(ts=3.0, kind='scenario', agent='cup', text='Cat near the desk!', feature='pet_risk'))
    assert {s['name'] for s in bus.stats() if s['handled']} == {'chat:roster', 'chat:habits', 'chat:alerts'}
    assert r.sent(type='roster') and r.sent(habit='hydration', type='group_message') and r.sent(alert='pet_risk')


def test_an_event_of_another_kind_is_ignored():
    r = Rig()
    r.gen.on_roster(HabitEvent(ts=1.0, habit='hydration', action='sip', say='Sip!'))
    r.gen.on_habit(joined())
    r.gen.on_alert(joined())
    assert r.notifier.sent == []


# ── habits ───────────────────────────────────────────────────────────
def test_a_habit_updates_the_app_and_its_agent_says_it():
    r = Rig()
    r.gen.on_habit(HabitEvent(ts=1.0, habit='hydration', action='sip', say="Sip detected! That's 3 today."))
    assert r.notifier.sent[0] == {**r.notifier.sent[0], 'type': 'habit_update', 'habit': 'hydration', 'action': 'sip'}
    line = r.sent(type='group_message')[0]
    assert (line['character'], line['text'], line['habit']) == ('cup', "Sip detected! That's 3 today.", 'hydration')


def test_lens_speaks_for_a_habit_nobody_in_the_chat_has():
    r = Rig(members=('computer',))
    r.gen.on_habit(HabitEvent(ts=1.0, habit='hydration', action='sip', say='Sip detected!'))
    assert r.sent(type='group_message')[0]['character'] == 'lens'


@pytest.mark.parametrize('event', [HabitEvent(ts=1.0, habit='breaks', action='break', say=''),
                                   HabitEvent(ts=1.0, habit='breaks', action='break', say='Nice', quiet=True)])
def test_a_quiet_habit_only_updates_the_app(event):
    r = Rig()
    r.gen.on_habit(event)
    assert [m['type'] for m in r.notifier.sent] == ['habit_update']


# ── alerts ───────────────────────────────────────────────────────────
def test_an_alert_with_its_own_text_is_sent_as_written():
    r = Rig()
    r.gen.on_alert(Alert(ts=1.0, kind='scenario', agent='cup', text='Cat detected near the desk!',
                         feature='pet_risk', extra={'snapshot': 'snap1'}))
    msg = r.notifier.sent[0]
    assert (msg['type'], msg['character'], msg['text'], msg['alert'], msg['snapshot']) == \
        ('group_message', 'cup', 'Cat detected near the desk!', 'pet_risk', 'snap1')
    assert r.ai.calls == []


def test_an_alert_from_nobody_known_comes_from_lens():
    r = Rig()
    r.gen.on_alert(Alert(ts=1.0, kind='scenario', agent='ghost', text='Too much clutter.', feature='organization'))
    assert r.notifier.sent[0]['character'] == 'lens' and 'snapshot' not in r.notifier.sent[0]


def test_an_alert_with_nothing_to_say_is_dropped():
    r = Rig()
    r.gen.on_alert(Alert(ts=1.0, kind='scenario', agent='cup', text=''))
    assert r.notifier.sent == []


def proximity(follow_up=0, near='cup', rule_label='Cup in the laptop spill zone', agent='computer'):
    return Alert(ts=1.0, kind='proximity', agent=agent,
                 extra={'near': near, 'rule_label': rule_label, 'follow_up': follow_up, 'snapshot': 'snap9'})


def test_a_proximity_alert_is_put_in_words_by_the_ai():
    r = Rig(reply='That cup is a little close to my keyboard, could you scoot it back?')
    r.desk.set_members({'computer': seen('laptop', 200, 200, 400, 350), 'cup': seen('cup', 410, 250, 450, 320)},
                       r.clock.now())
    r.gen.on_alert(proximity())
    msg = r.notifier.sent[0]
    assert (msg['character'], msg['alert'], msg['snapshot']) == ('computer', 'proximity', 'snap9')
    assert msg['text'] == 'That cup is a little close to my keyboard, could you scoot it back?'
    assert 'reply_to' not in msg
    call = r.ai.calls[0]
    assert call['background'] and call['max_tokens'] == CONFIG.llm.replies.alert.max_tokens
    assert call['temperature'] == CONFIG.llm.replies.alert.temperature
    system, user = call['messages'][0]['content'], call['messages'][1]['content']
    assert system.startswith('You are Computer, a laptop on a desk.')
    assert '"laptop" (you, Computer) and "cup" (other: Cup)' in user and 'spill risk is real' in user
    assert 'Cup in the laptop spill zone' in user


def test_a_follow_up_replies_to_what_was_said_before():
    r = Rig(reply=['That cup is a little close to my keyboard, could you scoot it back?',
                   'Still worried about that cup next to my keys, please move it.'])
    r.gen.on_alert(proximity())
    r.gen.on_alert(proximity(follow_up=1))
    second = r.notifier.sent[1]
    assert second['reply_to'] == {'character': 'computer',
                                  'text': 'That cup is a little close to my keyboard, could you scoot it back?'}
    assert 'You already said: "That cup is a little close' in r.ai.calls[1]['messages'][1]['content']


@pytest.mark.parametrize('near, rule_label, words', [
    ('cup', 'x', 'Cup is too close to my keyboard!'),
    ('', 'Book on the laptop vents.', 'Heads up: Book on the laptop vents. Could you move me back a bit?'),
    ('', '', 'Something is getting too close. Could you check the desk?'),
])
def test_with_the_ai_down_the_agent_still_warns(near, rule_label, words):
    r = Rig(fail='Gemini hit its free usage limit.')
    r.gen.on_alert(proximity(near=near, rule_label=rule_label))
    assert r.notifier.sent[0]['text'] == words and r.notifier.sent[0]['character'] == 'computer'


@pytest.mark.parametrize('reply', ['Too close!', 'The laptop screen is cracking under the cup!',
                                   'Cup: I am fine, nothing to see here.'])
def test_a_cut_off_dramatic_or_wrong_speaker_ai_line_falls_back(reply):
    r = Rig(reply=reply)
    r.gen.on_alert(proximity())
    assert r.notifier.sent[0]['text'] == 'Cup is too close to my keyboard!'


def test_a_proximity_alert_for_an_unknown_agent_is_dropped():
    r = Rig()
    r.gen.on_alert(proximity(agent='ghost'))
    assert r.notifier.sent == []


# ── habit reminders ──────────────────────────────────────────────────
def nudge():
    return Nudge(habit='breaks', key='sit_limit', evidence='sitting for 35 minutes',
                 text='35 minutes of sitting. Time for a 5-minute walk?', repeat_after_sec=1800,
                 tip='Walk 5 minutes every 30 minutes of sitting.')


def test_the_ai_phrases_a_nudge_in_character():
    r = Rig(reply='Hey, 35 minutes of sitting! Walk five minutes, leave your phone.')
    r.desk.set_vision_brief('posture=slouch')
    assert r.gen.phrase_nudge('computer', nudge()) == 'Hey, 35 minutes of sitting! Walk five minutes, leave your phone.'
    call = r.ai.calls[0]
    assert call['background'] and call['max_tokens'] == CONFIG.llm.replies.nudge.max_tokens
    assert call['temperature'] == 0.6                                # the agent's own
    system = call['messages'][0]['content']
    assert 'Protocols' in system and 'sitting for 35 minutes' in system and 'Walk 5 minutes every 30' in system
    assert "USER'S BREAKS" in system and 'vision note: posture=slouch' in system
    assert '[User said "habit reminder"] → [You replied] Hey, 35 minutes' in r.desk.recent('computer')[0]


def test_a_local_ai_gets_the_short_nudge_prompt():
    r = Rig(reply='Hey, 35 minutes of sitting! Walk five minutes, leave your phone.', local_model=True)
    r.gen.phrase_nudge('computer', nudge())
    assert r.ai.calls[0]['messages'][0]['content'].startswith('You are Computer, a laptop on a desk. Send a habit')


@pytest.mark.parametrize('rig', [lambda: Rig(fail='Gemini hit its free usage limit.'), lambda: Rig(reply='Walk.')])
def test_without_a_good_ai_line_the_nudge_uses_its_own_words(rig):
    r = rig()
    assert r.gen.phrase_nudge('computer', nudge()) == '35 minutes of sitting. Time for a 5-minute walk?'


def test_a_nudge_for_an_unknown_agent_uses_its_own_words():
    r = Rig()
    assert r.gen.phrase_nudge('ghost', nudge()) == '35 minutes of sitting. Time for a 5-minute walk?'
    assert r.ai.calls == []


# ── answering you ────────────────────────────────────────────────────
def paper():
    return AgentProfile(id='paper', name='Paper', object='notebook', labels=['paper'], habits=['tasks'],
                        tracker='todo', temperature=0.7, personality='An organised notebook.')


def asked(r, n=-1):
    return r.ai.calls[n]['messages']


def test_a_reply_is_grounded_in_the_desk_and_the_tracker():
    r = Rig(reply='Splash, nice one! [LOG_SIP]')
    r.desk.set_members({'cup': seen('cup', 410, 250, 450, 320)}, r.clock.now())
    reply = r.gen.answer('cup', 'I just drank some water', [])
    assert reply['messages'] == [{'character': 'cup', 'text': 'Splash, nice one!'}] and reply['habits_changed']
    call = r.ai.calls[0]
    assert not call['background'] and call['temperature'] == 0.8
    assert call['max_tokens'] == CONFIG.llm.replies.chat.max_tokens
    system = call['messages'][0]['content']
    assert system.startswith('You are Cup, a living cup')
    assert 'LIVE DESK STATUS' in system and 'vision label "cup"' in system and "USER'S WATER" in system
    assert '[LOG_SIP]' in system and 'HABIT FOCUS: Hydration' in system
    last = call['messages'][-1]
    assert last['role'] == 'user' and last['content'].startswith('I just drank some water\n\n(Reply as Cup the cup')


def test_a_local_ai_gets_the_short_chat_prompt():
    r = Rig(local_model=True)
    r.gen.answer('cup', 'hi', [])
    assert asked(r)[0]['content'].startswith("You are Cup, a cup on someone's desk. You text with the desk owner.")


def test_earlier_messages_are_turns_and_other_agents_speak_in_the_third_person():
    history = [{'role': 'user', 'text': 'hi all'}, {'role': 'assistant', 'name': 'Computer', 'text': 'Hello!'},
               {'role': 'assistant', 'name': 'Cup', 'text': 'Hey there!'}]
    r = Rig()
    r.gen.answer('cup', 'how are you?', history)
    assert asked(r)[1:4] == [{'role': 'user', 'content': 'hi all'},
                             {'role': 'assistant', 'content': '[Computer said]: Hello!'},
                             {'role': 'assistant', 'content': 'Hey there!'}]


def test_older_messages_become_a_short_recap():
    history = [{'role': 'user', 'text': w} for w in ('one', 'two', 'three')]
    r = Rig(llm=CONFIG.llm.model_copy(update={'chat_history_turns': 2}))
    r.gen.answer('cup', 'four', history)
    assert 'EARLIER IN THIS THREAD: User: "one"' in asked(r)[0]['content']
    assert [m['content'] for m in asked(r)[1:3]] == ['two', 'three']


def test_mentioned_objects_are_described_with_the_message():
    r = Rig()
    r.desk.set_members({'computer': seen('laptop', 200, 200, 400, 350)}, r.clock.now())
    r.gen.answer('cup', 'is @monty ok?', [])
    assert '[Referenced objects — @Computer: On desk' in asked(r)[-1]['content']


def test_a_reply_is_remembered_and_given_back_later():
    r = Rig()
    r.gen.answer('cup', 'hello cup', [])
    assert r.desk.recent('cup') == ['[User said "hello cup"] → [You replied] Hey! All good over here on the desk.']
    r.gen.answer('cup', 'and now?', [])
    assert 'WHAT YOU REMEMBER' in asked(r)[0]['content'] and 'hello cup' in asked(r)[0]['content']


def test_memory_is_kept_short():
    r = Rig(reply=['first reply', 'second reply', 'third reply'],
            llm=CONFIG.llm.model_copy(update={'memory_per_agent': 2}),
            chat=CONFIG.chat.model_copy(update={'memory_reply_chars': 5, 'memory_user_chars': 3}))
    for word in ('first', 'second', 'third'):
        r.gen.answer('cup', f'{word} message', [])
    assert r.desk.recent('cup') == ['[User said "sec"] → [You replied] secon',
                                    '[User said "thi"] → [You replied] third']


def test_a_local_ai_only_gets_the_newest_memories():
    r = Rig(local_model=True, chat=CONFIG.chat.model_copy(update={'ollama_memory_lines': 1}))
    r.desk.remember('cup', '[You said] old memory line', 10)
    r.desk.remember('cup', '[You said] new memory line', 10)
    r.gen.answer('cup', 'hi', [])
    assert 'new memory line' in asked(r)[0]['content'] and 'old memory line' not in asked(r)[0]['content']


def test_with_the_ai_down_there_is_no_reply_and_the_reason_is_plain():
    r = Rig(fail='Gemini hit its free usage limit.')
    assert r.gen.answer('cup', 'hi', []) == {'messages': [], 'error': 'Gemini hit its free usage limit.'}


def test_with_no_ai_set_up_the_reason_says_how_to_add_one():
    r = Rig()
    r.ai.ready = False
    reply = r.gen.answer('cup', 'hi', [])
    assert reply['messages'] == [] and reply['error'] == CONFIG.prompts.replies.needs_an_ai and r.ai.calls == []


def test_no_reply_for_a_line_for_someone_else():
    r = Rig(reply='Computer: I am the laptop, not the cup.')
    assert r.gen.answer('cup', 'hi', [])['messages'] == []


# ── answer(): what /api/chat returns ─────────────────────────────────
def test_a_one_on_one_reply_has_no_id():
    r = Rig()
    assert r.gen.answer('cup', 'hi') == {
        'messages': [{'character': 'cup', 'text': 'Hey! All good over here on the desk.'}]}
    assert r.notifier.sent == []


def test_a_one_on_one_habit_action_is_logged_and_reported():
    r = Rig(reply='Splash, nice one! [LOG_SIP]')
    history = [{'role': 'user', 'text': 'morning'}]
    assert r.gen.answer('cup', 'I just drank some water', history) == {
        'messages': [{'character': 'cup', 'text': 'Splash, nice one!'}], 'actions': [], 'habits_changed': True}
    assert r.habit_log.logged == [('hydration', 'sip', {'how': 'chat', 'quiet': True})]
    assert asked(r)[1] == {'role': 'user', 'content': 'morning'}


def test_a_todo_action_goes_back_to_the_app():
    r = Rig(reply='On it! [ADD_TODO:Essay]', profiles=[computer(), cup(), paper()])
    assert r.gen.answer('paper', 'remind me about my essay') == {
        'messages': [{'character': 'paper', 'text': 'On it!'}],
        'actions': [{'action': 'add_todo', 'text': 'Essay'}], 'habits_changed': False}


def test_the_todo_list_sent_with_a_message_is_kept_and_used():
    r = Rig(profiles=[computer(), cup(), paper()])
    r.gen.answer('paper', 'what is left?', widget={'type': 'todo', 'items': [{'text': 'Essay'}]})
    assert r.desk.widget('paper') == {'type': 'todo', 'items': [{'text': 'Essay', 'done': False}]}
    assert '- [ ] Essay' in asked(r)[0]['content']


def test_a_one_on_one_with_the_ai_down_says_why():
    r = Rig(fail='Could not reach the AI service.')
    assert r.gen.answer('cup', 'hi') == {'messages': [], 'error': 'Could not reach the AI service.'}


def test_an_unknown_chat_is_refused():
    with pytest.raises(UnknownChat):
        Rig().gen.answer('ghost', 'hi')


def test_the_group_with_nobody_on_the_desk():
    r = Rig(members=())
    assert r.gen.answer('group', 'hello?') == {
        'messages': [], 'error': 'Nobody is on the desk yet. Put something in front of the camera, '
                                 'or @mention someone who was here earlier.'}


def test_in_the_group_each_mentioned_agent_answers_in_turn():
    r = Rig(reply=['Cup here, all good.', 'Monty here, fans spinning.'])
    out = r.gen.answer('group', '@cup @monty how are you?', [{'role': 'user', 'text': 'morning'}])
    first, second = out['messages']
    assert out['streamed'] is True and 'error' not in out
    assert (first['type'], first['character'], first['text']) == ('group_message', 'cup', 'Cup here, all good.')
    assert (second['character'], second['text']) == ('computer', 'Monty here, fans spinning.')
    assert 'reply_to' not in first and second['reply_to'] == {'character': 'cup', 'text': 'Cup here, all good.'}
    assert first['id'] != second['id'] and first['ts'] == r.clock.now()
    assert r.notifier.sent == [first, second]                                   # live on the page right away
    assert {'role': 'assistant', 'content': '[Cup said]: Cup here, all good.'} in asked(r)


def test_in_the_group_someone_on_the_desk_answers_a_message_for_nobody_in_particular():
    r = Rig()
    out = r.gen.answer('group', 'hello?')
    assert [m['character'] for m in out['messages']] in (['computer'], ['cup'])


def test_in_the_group_a_habit_action_is_logged_too():
    r = Rig(reply='Splash! [LOG_SIP]')
    out = r.gen.answer('group', '@cup I drank water')
    assert out['messages'][0]['text'] == 'Splash!' and r.habit_log.logged[0][:2] == ('hydration', 'sip')


def test_in_the_group_with_the_ai_down_nobody_answers_and_it_says_why():
    r = Rig(fail='Could not reach the AI service.')
    assert r.gen.answer('group', '@cup hi') == {'messages': [], 'streamed': True,
                                                 'error': 'Could not reach the AI service.'}


# ── the tracker panel ────────────────────────────────────────────────
def test_a_todo_list_is_saved_for_a_known_agent_only():
    r = Rig(profiles=[computer(), cup(), paper()])
    assert r.gen.save_widget('paper', {'type': 'todo', 'items': [{'text': 'Essay', 'done': True}]})
    assert r.desk.widget('paper') == {'type': 'todo', 'items': [{'text': 'Essay', 'done': True}]}
    assert not r.gen.save_widget('ghost', {'type': 'todo', 'items': []})
    assert not r.gen.save_widget('paper', 'junk')
    assert r.desk.widget('ghost') is None


def test_panel_views_are_remembered_up_to_the_limit():
    r = Rig(chat=CONFIG.chat.model_copy(update={'panel_views_kept': 2}))
    for action in ('view', 'view', 'close'):
        r.gen.note_panel_event('cup', action)
        r.clock.advance(1)
    start = FakeClock().now()
    assert r.desk.widget_views_of('cup') == [{'ts': start + 1, 'action': 'view'}, {'ts': start + 2, 'action': 'close'}]


def test_a_panel_event_for_an_unknown_agent_is_refused():
    r = Rig()
    assert r.gen.note_panel_event('ghost', 'view') is False
    assert r.desk.widget_views_of('ghost') == []
    assert r.gen.note_panel_event('cup', 'view') is True


def test_a_long_panel_action_is_cut_short():
    r = Rig(chat=CONFIG.chat.model_copy(update={'panel_action_max_chars': 8}))
    r.gen.note_panel_event('cup', 'v' * 5000)
    assert r.desk.widget_views_of('cup')[0]['action'] == 'v' * 8


def test_the_live_context_knows_when_the_camera_is_off():
    assert Rig(camera_on=False).gen.live_context()['summary'].startswith('THE CAMERA IS OFF RIGHT NOW')
    assert 'THE CAMERA IS OFF' not in Rig(camera_on=True).gen.live_context()['summary']
