"""What every SensitivityReview must do (desktalk.ports.SensitivityReview)."""

from __future__ import annotations

from desktalk.domain import SensitivityVerdict
from desktalk.ports import SensitivityReview

TS = 1_760_000_000.0


class SensitivityReviewContract:
    """Subclass and give a `review` fixture (one that hasn't looked yet, its clock at TS). Set changes_kept to its
    settings → sensitivity_review.changes_kept, and override make_it_move() to make it move the slider."""

    changes_kept: int = 10

    def make_it_move(self, review, times: int) -> None:
        """Make `review` move the slider `times` times, each of its looks after TS."""
        raise NotImplementedError

    def test_is_a_sensitivity_review(self, review):
        assert isinstance(review, SensitivityReview)
        assert isinstance(review.name, str) and review.name

    def test_nothing_before_the_first_look(self, review):
        assert review.last() is None
        assert review.moves() == []

    def test_a_look_says_when_to_look_again(self, review):
        assert review.tick(TS) > 0

    def test_after_a_look_there_is_a_last_one(self, review):
        review.tick(TS)
        last = review.last()
        assert isinstance(last, SensitivityVerdict)
        assert last.ts == TS

    def test_moves_are_newest_first_and_only_changes_kept(self, review):
        self.make_it_move(review, self.changes_kept + 1)
        moves = review.moves()
        assert len(moves) == self.changes_kept
        assert all(m.moved for m in moves)
        assert [m.ts for m in moves] == sorted((m.ts for m in moves), reverse=True)
        assert moves[0] == review.last()
