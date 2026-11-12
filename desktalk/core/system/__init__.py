"""The Pi's system numbers for the developer page's Pi tab: the background job that turns readings into samples and
keeps the last few minutes of them."""

from desktalk.core.system.stats_history import StatsHistory, sample_between

__all__ = ['StatsHistory', 'sample_between']
