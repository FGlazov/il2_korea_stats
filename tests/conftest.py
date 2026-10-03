import os
from pathlib import Path

import pytest
from pytest_django.fixtures import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DATA = REPO_ROOT / "sample_data"
FIXTURE_LOGS = REPO_ROOT / "tests" / "fixtures" / "logs"
E2E_DIR = REPO_ROOT / "tests" / "e2e"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Postgres, browser (e2e) and sample-data tests are opt-in (TD-19, doc 08)."""
    postgres = os.environ.get("IL2KS_TEST_DB") == "postgres"
    e2e = os.environ.get("IL2KS_TEST_E2E") == "1"
    for item in items:
        if "postgres" in item.keywords and not postgres:
            item.add_marker(pytest.mark.skip(reason="Postgres tests need IL2KS_TEST_DB=postgres"))
        if E2E_DIR in item.path.parents:
            item.add_marker(pytest.mark.e2e)  # everything under tests/e2e is a browser test: `-m e2e` selects it
        if "e2e" in item.keywords and not e2e:
            item.add_marker(pytest.mark.skip(reason="browser tests need IL2KS_TEST_E2E=1 (and: playwright install)"))
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
    # The manifest storage needs `collectstatic` (which `il2ks web` runs); tests render templates from the source files.
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
