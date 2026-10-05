import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from django.utils import translation
from pytest_django.fixtures import Settings

from il2ks.queries import paging

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DATA = REPO_ROOT / "sample_data"
FIXTURE_LOGS = REPO_ROOT / "tests" / "fixtures" / "logs"
E2E_DIR = REPO_ROOT / "tests" / "e2e"
PERF_DIR = REPO_ROOT / "tests" / "perf"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Postgres, browser (e2e) and sample-data tests are opt-in (TD-19, doc 08)."""
    postgres = os.environ.get("IL2KS_TEST_DB") == "postgres"
    e2e = os.environ.get("IL2KS_TEST_E2E") == "1"
    for item in items:
        if "postgres" in item.keywords and not postgres:
            item.add_marker(pytest.mark.skip(reason="Postgres tests need IL2KS_TEST_DB=postgres"))
        if "sqlite_only" in item.keywords and postgres:
            item.add_marker(pytest.mark.skip(reason="SQLite only (restore, WAL settings); not run against Postgres"))
        if E2E_DIR in item.path.parents:
            item.add_marker(pytest.mark.e2e)  # everything under tests/e2e is a browser test: `-m e2e` selects it
        if PERF_DIR in item.path.parents:
            item.add_marker(pytest.mark.perf)  # everything under tests/perf; `-m "not perf"` skips it
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


@pytest.fixture(autouse=True)
def _reset_active_language(settings: Settings) -> Iterator[None]:
    """`LocaleMiddleware` activates the request's language on the thread and never switches it off (each real request
    activates its own, so production is fine), so a test client call with `Accept-Language: de` would leave German
    active for the next test on the same worker (it showed under xdist as "Filter zurücksetzen" in unrelated tests)."""
    translation.activate(settings.LANGUAGE_CODE)
    yield
    translation.activate(settings.LANGUAGE_CODE)


@pytest.fixture
def list_every_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """The aircraft page lists a mix, loadout or modification set from `paging.MIN_EVENTS_LISTED` kills / sorties on
    (maintainer 2026-10-05); the fixtures of most tests hold a handful of sorties, so they list every row."""
    monkeypatch.setattr(paging, "MIN_EVENTS_LISTED", 1)
