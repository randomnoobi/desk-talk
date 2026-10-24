"""Day names, like 2026-10-21: what the event log's folders are called and what the Log page asks for."""

from __future__ import annotations

import re

_DAY = re.compile(r'\d{4}-\d{2}-\d{2}')


def is_day(text: str) -> bool:
    """A day's name like 2026-10-21: the only names the log reads, so a name sent to the app can't point
    anywhere else."""
    return bool(_DAY.fullmatch(text))
