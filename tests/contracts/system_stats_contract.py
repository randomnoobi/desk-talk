"""What every SystemStatsReader, SystemStats and PiStatsLink must do (desktalk/ports.py, the Pi tab's numbers).
The fakes run these in test_system_stats_fakes_contract.py; the real ones in test_system_stats_reader_contract.py
(ProcReader), test_system_stats_contract.py (StatsHistory) and test_pi_stats_link_contract.py (HttpPiStatsLink)."""

from __future__ import annotations

import pytest

from desktalk.domain import RawReading, StatsUnavailable
from desktalk.ports import PiStatsLink, SystemStats, SystemStatsReader

REPLY_KEYS = {'collecting', 'every_sec', 'keep_min', 'info', 'samples'}


class SystemStatsReaderContract:
    """Subclass this and give a `reader` fixture: a SystemStatsReader (one reading a folder with nothing in it is
    fine)."""

    def test_it_is_a_reader(self, reader):
        assert isinstance(reader, SystemStatsReader)

    def test_a_reading_is_for_the_moment_asked(self, reader):
        reading = reader.read(1234.5)
        assert isinstance(reading, RawReading)
        assert reading.at == 1234.5

    def test_reading_twice_works(self, reader):
        reader.read(1.0)
        assert reader.read(2.0).at == 2.0


class SystemStatsContract:
    """Subclass this and give a `stats` fixture: a SystemStats that has had no tick yet, whose reader gives a
    reading each tick, `every_sec` apart."""

    def test_it_is_system_stats(self, stats):
        assert isinstance(stats, SystemStats)
        assert stats.name

    def test_before_any_reading_there_are_no_samples(self, stats):
        reply = stats.history(None)
        assert set(reply) >= REPLY_KEYS
        assert reply['samples'] == []

    def test_a_tick_says_when_to_come_back(self, stats):
        assert stats.tick(100.0) > 0

    def test_since_keeps_only_newer_samples(self, stats):
        wait = stats.tick(100.0)
        stats.tick(100.0 + wait)
        stats.tick(100.0 + 2 * wait)
        everything = stats.history(None)['samples']
        assert everything, 'three readings make at least one sample'
        newest = everything[-1]['at']
        assert stats.history(newest)['samples'] == []
        assert [s['at'] for s in stats.history(newest - 0.001)['samples']] == [newest]

    def test_a_since_newer_than_the_newest_kept_sample_gets_them_all(self, stats):
        """After the clock steps back, the page's since is newer than every sample: it gets them all again."""
        wait = stats.tick(100.0)
        stats.tick(100.0 + wait)
        stats.tick(100.0 + 2 * wait)
        everything = stats.history(None)['samples']
        assert everything, 'three readings make at least one sample'
        assert stats.history(everything[-1]['at'] + 1000.0)['samples'] == everything


class PiStatsLinkContract:
    """Subclass this and give `link` (its Pi answers with one sample) and `dead_link` (its Pi doesn't answer)."""

    def test_it_is_a_link(self, link):
        assert isinstance(link, PiStatsLink)

    def test_it_brings_back_the_pi_reply(self, link):
        reply = link.fetch(None)
        assert isinstance(reply, dict)
        assert isinstance(reply['samples'], list)

    def test_since_can_be_given(self, link):
        assert isinstance(link.fetch(12.5)['samples'], list)

    def test_a_pi_that_does_not_answer_raises(self, dead_link):
        with pytest.raises(StatsUnavailable) as caught:
            dead_link.fetch(None)
        assert str(caught.value)
