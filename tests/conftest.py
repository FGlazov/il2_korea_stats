import os
from pathlib import Path

import pytest
from pytest_django.fixtures import Settings

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


@pytest.fixture(autouse=True)
def _plain_http_in_tests(settings: Settings) -> None:
    """Production settings redirect http to https (TD-23), but the Django test client speaks plain http.

    The production values are tested on `il2ks.serving.djsettings` directly (tests/unit/test_djsettings.py)."""
    settings.SECURE_SSL_REDIRECT = False
    settings.SECURE_HSTS_SECONDS = 0
    settings.SESSION_COOKIE_SECURE = False
    settings.CSRF_COOKIE_SECURE = False
