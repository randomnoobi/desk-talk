"""Idle chat: now and then the agents on the desk make small talk, one line per round."""

from __future__ import annotations

import time

from desktalk.config import AiReplies, AiReply, IdleChatSettings
from desktalk.core.chat.workers.idle_chat import IdleChat
from desktalk.desk_state import DeskState
from desktalk.domain import AgentProfile
from tests.fakes import FakeChatModel, FakeClock, FakeDeskStatus, FakeProfiles, RecordingNotifier, StaticRoster

SETTINGS = IdleChatSettings(min_sec=720, max_sec=1500, start_delay_sec=15, max_turns=3, quiet_after_typing_sec=10,
                            pause_between_lines_sec=3, pause_random_sec=3, memory_per_agent=2, memory_lines=6,
                            topic_min_words=2, line_min_words=3)
REPLIES = AiReplies.model_construct(idle_topic=AiReply(max_tokens=200, temperature=0.9),
                                    idle_line=AiReply(max_tokens=200),
                                    idle_director=AiReply(max_tokens=200, temperature=0.4))
PROFILES = FakeProfiles([AgentProfile(id='cup', name='Cup', object='mug', temperature=0.75),
                         AgentProfile(id='computer', name='Computer', object='laptop', temperature=0.6),
                         AgentProfile(id='paper', name='Paper', object='notepad', temperature=0.5)])
SUMMARY = 'Cup and Computer are on the desk. Nothing risky.'
TOPIC = 'Who has been on this desk the longest?'
OPENING = 'Computer, how long have you been here?'
ANSWER = 'Since 7 this morning. My fans remember every minute.'
GO_ON_COMPUTER = '{"continue": true, "next": "Computer"}'
STOP = '{"continue": false}'


class Dice:
    """Randomness the test can predict: the shortest wait, the middle of a pause, the first choice."""

    def randint(self, low, high):
        return low

    def random(self):
        return 0.5

    def choice(self, options):
        return options[0]


class Rig:
    def __init__(self, replies=(), members=('cup', 'computer'), status=None, ai=None, settings=SETTINGS,
                 reply_max_chars=500):
        self.clock = FakeClock(time.mktime((2026, 10, 21, 14, 0, 0, 0, 0, -1)))
        self.replies = list(replies)
        self.ai = ai or FakeChatModel(lambda messages: self.replies.pop(0))
        self.notifier = RecordingNotifier(self.clock)
        self.status = status or FakeDeskStatus()
        self.desk = DeskState()
        self.idle = IdleChat(self.ai, self.notifier, self.status, StaticRoster(members), PROFILES, self.desk,
                             lambda: SUMMARY, settings, REPLIES, memory_kept=8, memory_chars=120,
                             default_temperature=0.8, reply_max_chars=reply_max_chars, rng=Dice())
        assert self.idle.tick(self.clock.now()) == 720     # the first round only sets the time of the next one

    def tick(self, after=0.0):
        self.clock.advance(after)
        return self.idle.tick(self.clock.now())

    def lines(self):
        return [(m['character'], m['text']) for m in self.notifier.sent]


def test_the_first_round_only_waits():
    rig = Rig()
    assert rig.ai.calls == [] and rig.notifier.sent == []


def test_a_topic_an_opening_question_then_the_director_picks_who_answers():
    rig = Rig([TOPIC, OPENING, GO_ON_COMPUTER, ANSWER, STOP])
    assert rig.tick(after=720) == 4.5                  # 3 seconds plus half of the 3 random ones
    assert rig.lines() == [('cup', OPENING)]
    assert rig.tick(after=4.5) == 4.5
    assert rig.tick(after=4.5) == 720
    assert rig.lines() == [('cup', OPENING), ('computer', ANSWER)]
    first, second = rig.notifier.sent
    assert first['type'] == 'group_message' and first['idle'] and 'reply_to' not in first
    assert second['reply_to'] == {'character': 'cup', 'text': OPENING}
    assert rig.replies == []


def test_what_the_ai_is_asked():
    rig = Rig([TOPIC, OPENING, GO_ON_COMPUTER, ANSWER, STOP])
    rig.desk.char_memory['cup'] = ['[You said] one', '[You said] two', '[You said] three']
    rig.tick(after=720)
    rig.tick(after=4.5)
    topic, opening, director, answer = rig.ai.calls[:4]
    assert (topic['max_tokens'], topic['temperature'], topic['background']) == (200, 0.9, True)
    assert topic['messages'][0]['content'].startswith('You set up small talk')
    assert topic['messages'][1]['content'] == (f'DESK STATUS:\n{SUMMARY}\n\nOBJECTS PRESENT: Cup, Computer\n'
                                               'RECENT MEMORY:\nCup: [You said] two\nCup: [You said] three\n\nTopic:')
    assert opening['temperature'] == 0.75 and opening['background']
    assert SUMMARY in opening['messages'][0]['content'] and 'You are Cup, a mug' in opening['messages'][0]['content']
    assert f'starting a group chat about: {TOPIC}' in opening['messages'][1]['content']
    assert director['temperature'] == 0.4
    assert director['messages'][1]['content'] == (f'TOPIC: {TOPIC}\nOBJECTS THAT COULD SPEAK NEXT: Computer\n'
                                                  f'RECENT MESSAGES:\nCup: {OPENING}\n\nJSON:')
    assert answer['temperature'] == 0.6
    assert f'RECENT MESSAGES:\nCup: {OPENING}' in answer['messages'][1]['content']


def test_each_line_is_remembered_by_the_agent_who_said_it():
    rig = Rig([TOPIC, OPENING, GO_ON_COMPUTER, ANSWER, STOP])
    rig.desk.char_memory['cup'] = [f'[You said] old {n}' for n in range(8)]
    rig.tick(after=720)
    rig.tick(after=4.5)
    assert rig.desk.char_memory['computer'] == [f'[You said] {ANSWER}']
    assert len(rig.desk.char_memory['cup']) == 8 and rig.desk.char_memory['cup'][-1] == f'[You said] {OPENING}'


def test_it_waits_while_you_type():
    rig = Rig([TOPIC, OPENING, STOP])
    rig.desk.last_user_activity = rig.clock.now() + 716
    assert rig.tick(after=720) == 6
    assert rig.ai.calls == []
    assert rig.tick(after=6) == 4.5
    assert rig.lines() == [('cup', OPENING)]


def test_the_chat_stops_when_you_start_typing():
    rig = Rig([TOPIC, OPENING])
    rig.tick(after=720)
    rig.desk.last_user_activity = rig.clock.now() + 2
    assert rig.tick(after=4.5) == 720
    assert len(rig.ai.calls) == 2 and rig.lines() == [('cup', OPENING)]


def test_no_small_talk_when_reminders_wait_nobody_is_here_or_there_is_no_ai():
    for status, ai in ((FakeDeskStatus(ready=False), None), (FakeDeskStatus(active=False), None),
                       (None, FakeChatModel(TOPIC, ready=False))):
        rig = Rig(status=status, ai=ai)
        assert rig.tick(after=720) == 720
        assert rig.ai.calls == [] and rig.notifier.sent == []


def test_no_small_talk_with_fewer_than_two_agents():
    rig = Rig(members=('cup',))
    assert rig.tick(after=720) == 720
    assert rig.ai.calls == []


def test_a_topic_too_short_means_no_chat_this_round():
    rig = Rig(['Mugs'])
    assert rig.tick(after=720) == 720
    assert rig.notifier.sent == []


def test_the_topic_is_the_first_line_without_quotes():
    rig = Rig([f'"{TOPIC}"\nOr maybe the weather.', OPENING, STOP])
    rig.tick(after=720)
    assert f'starting a group chat about: {TOPIC}\n' in rig.ai.calls[1]['messages'][1]['content']


def test_an_ai_that_cannot_answer_means_no_chat_this_round():
    rig = Rig(ai=FakeChatModel(fail='Gemini is out of quota for today'))
    assert rig.tick(after=720) == 720
    assert rig.notifier.sent == []


def test_lines_are_cleaned_up_and_too_short_ones_end_the_chat():
    rig = Rig([TOPIC, f'Cup: {OPENING} [LOG_SIP]\nComputer: Since this morning!', GO_ON_COMPUTER, 'Computer: Yes.'])
    rig.tick(after=720)
    assert rig.lines() == [('cup', OPENING)]
    assert rig.tick(after=4.5) == 720
    assert rig.lines() == [('cup', OPENING)]


def test_a_runaway_line_is_cut_to_the_longest_reply():
    rig = Rig([TOPIC, 'Computer, tell me more about your fans please', STOP], reply_max_chars=20)
    rig.tick(after=720)
    assert rig.lines() == [('cup', 'Computer, tell me mo')]


def test_the_director_can_end_the_chat_or_name_someone_unknown():
    rig = Rig([TOPIC, OPENING, 'no idea'], members=('cup', 'computer', 'paper'))
    rig.tick(after=720)
    assert rig.tick(after=4.5) == 720
    other = Rig([TOPIC, OPENING, '{"continue": true, "next": "Lamp"}', ANSWER, STOP],
                members=('cup', 'computer', 'paper'))
    other.tick(after=720)
    other.tick(after=4.5)
    assert other.lines()[1][0] == 'computer'          # someone else in the chat, not the one who just spoke


def test_the_chat_ends_after_the_most_replies():
    rig = Rig([TOPIC, OPENING, GO_ON_COMPUTER, ANSWER], settings=SETTINGS.model_copy(update={'max_turns': 1}))
    rig.tick(after=720)
    assert rig.tick(after=4.5) == 720
    assert len(rig.lines()) == 2 and len(rig.ai.calls) == 4
    no_replies = Rig([TOPIC, OPENING], settings=SETTINGS.model_copy(update={'max_turns': 0}))
    assert no_replies.tick(after=720) == 720
    assert no_replies.lines() == [('cup', OPENING)]
