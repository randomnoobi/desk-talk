"""The prompts' words are in config/prompts.yaml; only filling in their blanks is tested here."""

from pathlib import Path

from desktalk.config import load_config
from desktalk.core.chat import prompts
from desktalk.domain import AgentProfile

P = load_config(environ={}, dotenv=Path('/nonexistent')).prompts


def test_habit_area_names_the_agents_habits():
    cup = AgentProfile(id='cup', name='Cup', habits=['hydration', 'breaks', 'juggling'])
    assert prompts.habit_area(P.habit_areas, cup) == 'Hydration, Movement breaks, juggling'
    assert prompts.habit_area(P.habit_areas, AgentProfile(id='pen', name='Pen')) == 'desk habits'


def test_a_follow_up_alert_sounds_more_worried_each_time():
    cup = AgentProfile(id='cup', name='Cup', object='cup')
    first = prompts.event_user(P.alert, cup, 'Cup near laptop', None, 0)
    second = prompts.event_user(P.alert, cup, 'Cup near laptop', 'Scoot me back?', 1)
    third = prompts.event_user(P.alert, cup, 'Cup near laptop', 'Scoot me back?', 2)
    assert 'already said' not in first and 'SITUATION: Cup near laptop' in first
    assert 'You already said: "Scoot me back?"' in second and 'a bit more worried' in second
    assert 'genuinely concerned' in third


def test_a_reminder_with_no_reason_says_it_is_a_check_in_on_a_small_ai():
    cup = AgentProfile(id='cup', name='Cup', object='cup', personality='Bubbly.')
    short = prompts.habit_system(P.habit_reminder, cup, 'Hydration', '', '', 'Drink water.', short=True)
    assert 'WHY YOU ARE SPEAKING (true, mention it): routine check-in' in short
    full = prompts.habit_system(P.habit_reminder, cup, 'Hydration', '', 'seen', 'Drink water.', short=False)
    assert 'mention it): seen' in full and 'Bubbly.' in full and 'SIGNALS' not in full


def test_the_words_come_from_the_settings():
    changed = P.alert.model_copy(update={'more_worried': 'quite cross'})
    cup = AgentProfile(id='cup', name='Cup', object='cup')
    assert 'Be quite cross.' in prompts.event_user(changed, cup, 'Cup near laptop', 'Move me?', 1)
