"""What every PiLog must do (desktalk/ports.py): the developer page's Pi log tab. FakePiLog (tests/fakes/dev.py)
runs these in test_fakes_pass_contracts.py, and the real SshPiLog in tests/unit/test_adapters_dev_pi_log.py (with
`sh -c` standing in for ssh, so the "Pi" is this computer).

(The class isn't named Test... so pytest only runs it through a subclass that gives the fixture.)"""

from __future__ import annotations

import time

import pytest

from desktalk.domain import PiLogLine, PiLogPage, PiLogSource

STATES = ('connecting', 'connected', 'waiting', 'stopped')
KINDS = ('line', 'problem', 'note')
LONGEST_WAIT_SEC = 10          # a real reader gets its lines in well under a second; this only stops a stuck test


def read_until(pi_log, source: str, after: int, wanted: list[str]) -> tuple[PiLogPage, list[PiLogLine]]:
    """Ask for `source` after `after` until the Pi's lines `wanted` have all come (in that order, maybe among
    others). Returns the last page and every line that came."""
    give_up = time.monotonic() + LONGEST_WAIT_SEC
    got: list[PiLogLine] = []
    while True:
        page = pi_log.read(source, after)
        got += page.lines
        after = page.end
        texts = [line.text for line in got if line.kind == 'line']
        if all(text in texts for text in wanted):
            return page, got
        assert time.monotonic() < give_up, f'still waiting for {wanted}, got {texts}'
        time.sleep(0.02)


class PiLogContract:
    """Subclass this and give a `pi_log` fixture: a PiLog with at least two sources, where the first prints the
    lines 'one', 'two' and 'three' and the second prints 'four', each then staying connected with nothing more."""

    def test_sources_are_listed(self, pi_log):
        listed = pi_log.sources()
        assert len(listed) >= 2 and all(isinstance(source, PiLogSource) for source in listed)
        assert len({source.name for source in listed}) == len(listed)
        assert all(source.label for source in listed)

    def test_the_list_is_a_copy(self, pi_log):
        pi_log.sources().clear()
        assert len(pi_log.sources()) >= 2

    def test_an_unknown_source_is_refused(self, pi_log):
        with pytest.raises(KeyError):
            pi_log.read('no such log', 0)

    def test_the_lines_come_numbered_in_order(self, pi_log):
        first = pi_log.sources()[0].name
        page, got = read_until(pi_log, first, 0, ['one', 'two', 'three'])
        assert isinstance(page, PiLogPage)
        assert page.source == first
        assert page.state in STATES
        assert all(isinstance(line, PiLogLine) and line.kind in KINDS for line in got)
        numbers = [line.number for line in got]
        assert numbers == sorted(numbers) and len(set(numbers)) == len(numbers) and numbers[0] >= 1
        assert page.end == numbers[-1]
        assert [line.text for line in got if line.kind == 'line'] == ['one', 'two', 'three']

    def test_it_says_it_is_connected_once_the_lines_come(self, pi_log):
        first = pi_log.sources()[0].name
        page, _ = read_until(pi_log, first, 0, ['three'])
        assert page.state == 'connected'
        assert page.problem == '' and page.retry_at is None

    def test_asking_after_the_end_gives_nothing_again(self, pi_log):
        first = pi_log.sources()[0].name
        page, _ = read_until(pi_log, first, 0, ['three'])
        again = pi_log.read(first, page.end)
        assert again.lines == () and again.end == page.end and not again.dropped

    def test_asking_from_the_start_gives_everything_kept(self, pi_log):
        first = pi_log.sources()[0].name
        page, got = read_until(pi_log, first, 0, ['three'])
        assert pi_log.read(first, 0).lines == tuple(got)

    def test_another_source_lets_the_first_one_go(self, pi_log):
        first, second = (source.name for source in pi_log.sources()[:2])
        page, _ = read_until(pi_log, first, 0, ['three'])
        switched, got = read_until(pi_log, second, page.end, ['four'])
        assert switched.source == second
        assert [line.text for line in got if line.kind == 'line'] == ['four']
        assert all(line.number > page.end for line in pi_log.read(second, 0).lines)    # the first one's are gone

    def test_close_twice_and_read_again(self, pi_log):
        first = pi_log.sources()[0].name
        page, _ = read_until(pi_log, first, 0, ['three'])
        pi_log.close()
        pi_log.close()
        _, got = read_until(pi_log, first, page.end, ['one', 'two', 'three'])     # started again
        assert all(line.number > page.end for line in got)
