"""How a weekly report fails, in plain words. Shared by the job and the two writers."""

from __future__ import annotations

from collections.abc import Mapping

from desktalk_cloud.ports import ReportFailed

__all__ = ['ReportWillNotWork', 'failure_for_status']

WRONG_REQUEST_STATUSES = (400, 404, 413, 422)         # the request itself is wrong: trying again won't help
KEY_REFUSED_STATUSES = (401, 403)


class ReportWillNotWork(ReportFailed):
    """A report that trying again won't fix (no key, a key the AI company refused, a request it says is wrong, a
    report too long for reports.max_tokens). The job marks it failed at once. The message is plain words."""


def failure_for_status(status: int, errors: Mapping[str, str]) -> ReportFailed:
    """The failure for an answer with this status. The AI company's own words are never kept."""
    if status in KEY_REFUSED_STATUSES:
        return ReportWillNotWork(errors['key_refused'])
    message = errors['status'].format(status=status)
    return ReportWillNotWork(message) if status in WRONG_REQUEST_STATUSES else ReportFailed(message)
