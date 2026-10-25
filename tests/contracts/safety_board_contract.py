"""What every SafetyBoard must do (desktalk.ports.SafetyBoard): the dangers on the desk right now."""

from __future__ import annotations

from desktalk.domain import Danger, SafetyNow

SPILL = Danger(pair=('cup', 'laptop'), label='Spill / pressure zone', kind='zone_intrusion', depth_px=12.0)
EDGE = Danger(pair=('cup', 'desk_edge'), label='cup near left edge', kind='edge_danger')
SPILL_PAIR = frozenset({'cup', 'laptop'})


class SafetyBoardContract:
    """Subclass and give a `board` fixture (nothing reported yet)."""

    def test_safe_before_anything_is_reported(self, board):
        assert board.now() == SafetyNow()
        assert board.now().state == 'SAFE' and board.now().dangers == () and board.now().result is None
        assert board.danger_since(SPILL_PAIR) is None
        assert board.recent_alerts(5) == []

    def test_the_latest_report(self, board):
        board.report('DANGEROUS', [SPILL, EDGE], {'risk_level': 3}, 100.0)
        now = board.now()
        assert (now.state, now.dangers) == ('DANGEROUS', (SPILL, EDGE))
        assert now.result == {'risk_level': 3}

    def test_a_danger_counts_from_when_it_started_until_it_goes(self, board):
        board.report('DANGEROUS', [SPILL], {'risk_level': 3}, 100.0)
        board.report('DANGEROUS', [SPILL], {'risk_level': 3}, 105.0)
        assert board.danger_since(SPILL_PAIR) == 100.0
        board.report('SAFE', [], {'risk_level': 0}, 110.0)
        assert board.danger_since(SPILL_PAIR) is None
        board.report('DANGEROUS', [SPILL], {'risk_level': 3}, 120.0)
        assert board.danger_since(SPILL_PAIR) == 120.0

    def test_recent_alerts_keep_the_newest_oldest_first(self, board):
        for n in range(5):
            board.note_alert({'time': float(n), 'type': 'proximity', 'n': n}, keep=3)
        assert [e['n'] for e in board.recent_alerts(10)] == [2, 3, 4]
        assert [e['n'] for e in board.recent_alerts(2)] == [3, 4]
        assert board.recent_alerts(0) == []

    def test_recent_alerts_come_back_as_copies(self, board):
        board.note_alert({'n': 1}, keep=3)
        board.recent_alerts(1)[0]['n'] = 99
        assert board.recent_alerts(1) == [{'n': 1}]
