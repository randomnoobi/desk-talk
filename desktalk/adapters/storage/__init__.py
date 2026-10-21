"""Files on this computer: the event log and small JSON documents."""

from desktalk.adapters.storage.event_log import EventLog, is_day
from desktalk.adapters.storage.json_files import JsonFileStore

__all__ = ['EventLog', 'JsonFileStore', 'is_day']
