"""The machine's own numbers for the developer page's Pi tab (a SystemStatsReader, desktalk/ports.py): ProcReader
reads them from Linux's files in /proc and /sys."""

from desktalk.adapters.system.proc_reader import ProcReader

__all__ = ['ProcReader']
