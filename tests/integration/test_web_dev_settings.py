"""GET /api/dev/settings (desktalk/web/routers/dev.py) sends the developer page the numbers it trims and
refreshes with, so the page keeps no copies of its own."""

from __future__ import annotations

from tests.fakes import FakeTestRunner
from tests.integration.app_kit import DeskRig
from tests.integration.web_kit import web_client


def test_settings_give_the_page_its_trimming_and_counting_numbers(tmp_path):
    rig = DeskRig(tmp_path, test_runner=FakeTestRunner([]))
    settings = web_client(rig.app).get('/api/dev/settings').json()
    dev = rig.config.dev
    assert settings['output_keep_kb'] == dev.output_keep_kb
    assert settings['flips_window_min'] == dev.flips_window_min
    assert settings['table_counts_every_sec'] == dev.table_counts_every_sec


def test_a_test_list_that_cant_be_made_says_why(tmp_path):
    """A missing `uv` or a mistyped dev.test_command gives the reason, not a bare server error."""
    broken = FakeTestRunner([], collect_error=RuntimeError("Couldn't list the tests: no such program"))
    reply = web_client(DeskRig(tmp_path, test_runner=broken).app).get('/api/dev/tests')
    assert reply.status_code == 503
    assert reply.json() == {'error': "Couldn't list the tests: no such program"}
