import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DATA = REPO_ROOT / "sample_data"
FIXTURE_LOGS = REPO_ROOT / "tests" / "fixtures" / "logs"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Postgres and sample-data tests are opt-in (TD-19, doc 08)."""
    postgres = os.environ.get("IL2KS_TEST_DB") == "postgres"
    for item in items:
        if "postgres" in item.keywords and not postgres:
            item.add_marker(pytest.mark.skip(reason="Postgres tests need IL2KS_TEST_DB=postgres"))
        if "sample_data" in item.keywords and not SAMPLE_DATA.is_dir():
            item.add_marker(pytest.mark.skip(reason="sample_data/ not present"))
