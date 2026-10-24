"""Day names like 2026-10-21 (desktalk/domain/days.py): the event log's folders and the Log page's ?day=."""

import pytest

from desktalk.domain import is_day


@pytest.mark.parametrize('text', ['2026-10-21', '1999-01-01'])
def test_a_day_name(text):
    assert is_day(text)


@pytest.mark.parametrize('text', ['', 'today', '2026-1-21', '2026-10-21x', '../2026-10-21', '2026/10/21'])
def test_anything_else_is_not_a_day(text):
    assert not is_day(text)
