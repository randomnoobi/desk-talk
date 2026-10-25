"""What every DeskRulesView must do (desktalk.ports.DeskRulesView)."""

from __future__ import annotations

import pytest

from desktalk.domain import DeskRules

OUTLINE = [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9]]
TILT = {'gravity': [0.1, -9.7, 0.4]}


class DeskRulesContract:
    """Subclass and give a `rules` fixture with nothing saved yet."""

    def test_only_the_built_in_rules_before_anything_is_saved(self, rules):
        assert isinstance(rules.current, DeskRules)
        assert not rules.loaded
        assert not rules.has_outline() and rules.current.outline == ()
        assert rules.reference_gravity() is None

    def test_saved_corners_and_tilt_are_used_at_once(self, rules):
        rules.save({'desk_outline': OUTLINE, 'camera_reference': TILT})
        assert rules.loaded and rules.has_outline()
        assert rules.current.outline == ((0.1, 0.1), (0.9, 0.1), (0.9, 0.9))
        assert rules.reference_gravity() == (0.1, -9.7, 0.4)

    def test_a_save_keeps_the_parts_it_was_not_given(self, rules):
        rules.save({'desk_outline': OUTLINE})
        rules.save({'temporal': {'history_length': 12}})
        assert rules.has_outline()
        assert rules.for_settings_page()['temporal'] == {'history_length': 12}

    def test_the_settings_page_sees_what_was_saved(self, rules):
        page = rules.for_settings_page()
        assert {'loaded', 'desk_outline', 'danger_zones', 'edge_proximity', 'chain_reaction', 'temporal'} <= set(page)
        assert page['loaded'] is False and page['desk_outline'] == []
        rules.save({'desk_outline': OUTLINE})
        page = rules.for_settings_page()
        assert page['loaded'] is True and page['desk_outline'] == OUTLINE

    def test_corners_are_cut_to_the_picture(self, rules):
        assert rules.clean_outline([[-1, 0.5], [0.5, 2], [0.3, 0.3]]) == [[0.0, 0.5], [0.5, 1.0], [0.3, 0.3]]
        assert rules.clean_outline([]) == []

    @pytest.mark.parametrize('bad', [[[0.1, 0.1], [0.2, 0.2]], 'not corners', [['a', 'b'], [1, 1], [0, 0]]])
    def test_something_that_is_not_corners_is_refused(self, rules, bad):
        with pytest.raises(ValueError):
            rules.clean_outline(bad)
