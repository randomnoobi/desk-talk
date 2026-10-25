"""The real developer page tools pass the same contracts as their fakes (tests/contracts/dev_contracts.py).

Each class below belongs to one task of docs/plans/2026-10-24-developer-ui-plan.md, which fills it in (so the
tasks can be done at the same time without two of them editing the same lines). Until then it only has a skipped
placeholder test. When you fill one in: make it a subclass of its contract, give the contract's fixture, and
delete the placeholder test.
"""

from __future__ import annotations

import pytest


class TestPytestRunnerPassesRunnerContract:
    """PytestRunner (desktalk/adapters/dev/test_runner.py) → RunnerContract. Filled in by Task 2."""

    @pytest.mark.skip(reason='filled in by Task 2 of the developer page plan')
    def test_placeholder(self):
        pass


class TestSqliteBrowserPassesDatabaseBrowserContract:
    """SqliteBrowser (desktalk/adapters/dev/sqlite_browser.py) → DatabaseBrowserContract. Filled in by Task 3."""

    @pytest.mark.skip(reason='filled in by Task 3 of the developer page plan')
    def test_placeholder(self):
        pass


class TestDataFolderPassesDataFilesContract:
    """DataFolder (desktalk/adapters/dev/data_files.py) → DataFilesContract. Filled in by Task 4."""

    @pytest.mark.skip(reason='filled in by Task 4 of the developer page plan')
    def test_placeholder(self):
        pass


class TestObjectStateTrackerPassesSmoothingTraceContract:
    """ObjectStateTracker (desktalk/core/object_states/tracker.py) → SmoothingTraceContract. Filled in by Task 5."""

    @pytest.mark.skip(reason='filled in by Task 5 of the developer page plan')
    def test_placeholder(self):
        pass
