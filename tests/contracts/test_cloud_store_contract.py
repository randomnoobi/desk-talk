from __future__ import annotations

import pytest

from desktalk_cloud.store_memory import MemoryStore
from tests.contracts.cloud_store_contract import CloudStoreContract


class TestMemoryStore(CloudStoreContract):
    @pytest.fixture
    def store(self):
        return MemoryStore()
