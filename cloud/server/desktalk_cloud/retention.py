"""The daily clean-up: events older than settings → retention.events_days are deleted. Reports stay."""

from __future__ import annotations

import logging

from desktalk_cloud.config import RetentionSettings
from desktalk_cloud.ports import Store

__all__ = ['RetentionJob']

log = logging.getLogger(__name__)

SECONDS_IN_DAY, SECONDS_IN_HOUR = 86_400, 3_600


class RetentionJob:
    """A PeriodicWorker: tick() does one clean-up and says how many seconds until the next."""

    name = 'retention'

    def __init__(self, store: Store, settings: RetentionSettings) -> None:
        self._store, self._settings = store, settings

    def tick(self, now: float) -> float:
        deleted = self._store.delete_events_before(now - self._settings.events_days * SECONDS_IN_DAY)
        log.info('Deleted %d events older than %g days', deleted, self._settings.events_days)
        return self._settings.check_every_hours * SECONDS_IN_HOUR
