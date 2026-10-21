"""The prompts are plain text; only the bits of logic in them are tested."""

from desktalk.core.chat import prompts
from desktalk.domain import AgentProfile


def test_habit_area_names_the_agents_habits():
    cup = AgentProfile(id='cup', name='Cup', habits=['hydration', 'breaks', 'juggling'])
    assert prompts.habit_area(cup) == 'Hydration, Movement breaks, juggling'
    assert prompts.habit_area(AgentProfile(id='pen', name='Pen')) == 'desk habits'


def test_a_follow_up_alert_sounds_more_worried_each_time():
    cup = AgentProfile(id='cup', name='Cup', object='cup')
    first = prompts.event_user(cup, 'Cup near laptop', None, 0)
    second = prompts.event_user(cup, 'Cup near laptop', 'Scoot me back?', 1)
    third = prompts.event_user(cup, 'Cup near laptop', 'Scoot me back?', 2)
    assert 'already said' not in first
    assert 'You already said: "Scoot me back?"' in second and 'a bit more worried' in second
    assert 'genuinely concerned' in third
