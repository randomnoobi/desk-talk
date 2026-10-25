"""What every RuleNudges must do (desktalk/ports.py). The fake FakeRuleNudges and the real RuleRunner run
these tests."""

from __future__ import annotations

from desktalk.domain import Nudge

NOW = 1_780_000_000.0


def a_nudge(key: str) -> Nudge:
    return Nudge(habit='hydration', key=key, evidence='', text='Time for some water?', repeat_after_sec=1800)


class RuleNudgesContract:
    """Subclass this and give one fixture:

    - `rule_nudges`: a RuleNudges, set up so it may have nudges ready at NOW.
    """

    def test_nudges_is_a_list_of_nudges(self, rule_nudges):
        got = rule_nudges.nudges(NOW)
        assert isinstance(got, list)
        assert all(isinstance(n, Nudge) for n in got)

    def test_nudges_hands_out_a_copy(self, rule_nudges):
        before = rule_nudges.nudges(NOW)
        handed_out = rule_nudges.nudges(NOW)
        handed_out.clear()
        handed_out.append(a_nudge('rule.someone-elses'))
        assert rule_nudges.nudges(NOW) == before

    def test_note_fired_takes_any_nudge(self, rule_nudges):
        for nudge in (*rule_nudges.nudges(NOW), a_nudge('rule.not-a-rule'), a_nudge('hydration.pace')):
            rule_nudges.note_fired(nudge, NOW)
        assert isinstance(rule_nudges.nudges(NOW), list)
