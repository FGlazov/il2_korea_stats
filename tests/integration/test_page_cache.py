"""The server-side page cache (TD-28 step 2, `il2ks.web.page_cache`): what is cached, what makes it fresh again, and
the warmer."""

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.core.cache import caches
from django.core.signals import request_finished, request_started
from django.core.wsgi import get_wsgi_application
from django.db import close_old_connections, connection
from django.http import HttpRequest, HttpResponse
from django.test import Client
from django.test.utils import CaptureQueriesContext
from pytest_django.fixtures import Settings

from il2ks.db.models import Player
from il2ks.db.site import get_site_settings
from il2ks.web import page_cache
from tests.factories import mission, save, sortie
from tests.integration.test_admin import settings_form, settings_url

pytestmark = pytest.mark.django_db

application = get_wsgi_application()  # not `il2ks.wsgi`: that one starts the front-page image poller

HEADER = page_cache.STATUS_HEADER


MEMORY = page_cache.MEMORY_ALIAS
FILES = page_cache.FILES_ALIAS


@pytest.fixture(autouse=True)
def memory_cache(settings: Settings, tmp_path: Path) -> Iterator[None]:
    """Memory by default; the file tier goes to this test's own folder, never the data dir."""
    settings.IL2KS_PAGE_CACHE = "memory"
    settings.CACHES = {
        **settings.CACHES,
        FILES: {"BACKEND": "django.core.cache.backends.filebased.FileBasedCache", "LOCATION": tmp_path / "pages"},
    }
    caches[MEMORY].clear()
    page_cache._memory_version = None  # pyright: ignore[reportPrivateUsage]
    yield
    caches[MEMORY].clear()


@pytest.fixture(autouse=True)
def keep_the_test_connection() -> Iterator[None]:
    """The warmer calls the WSGI app itself: like Django's test client, keep `request_finished` from closing the
    connection that holds the test's transaction (it does on Postgres)."""
    for signal in (request_started, request_finished):
        signal.disconnect(close_old_connections)
    yield
    for signal in (request_started, request_finished):
        signal.connect(close_old_connections)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Wall-clock seconds for the stored expiries and Django's own cache timeouts."""
    now = [1_000_000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    return now


def test_a_list_page_is_rendered_once_then_served_from_the_cache(client: Client) -> None:
    first = client.get("/missions/")
    with CaptureQueriesContext(connection) as queries:
        second = client.get("/missions/")

    assert (first[HEADER], second[HEADER]) == ("miss", "hit")
    assert second.status_code == 200
    assert second.content == first.content
    assert second["Content-Type"] == first["Content-Type"]
    assert second["ETag"] == first["ETag"]
    assert {"max-age=0", "must-revalidate"} == {d.strip() for d in second["Cache-Control"].split(",")}
    assert len(queries) == 1, "only the data version is read"


def test_every_listed_page_is_cached(client: Client) -> None:
    save(mission((sortie(0, 1),)))
    paths = page_cache.page_paths()
    aircraft = client.get("/aircraft/").content.decode()
    paths += page_cache._AIRCRAFT_LINK.findall(aircraft)[:1]  # pyright: ignore[reportPrivateUsage]
    assert any(p.startswith("/aircraft/") and p != "/aircraft/" for p in paths)
    for path in paths:
        client.get(path)
        assert client.get(path)[HEADER] == "hit", path


def test_query_strings_languages_and_htmx_are_separate_entries(client: Client) -> None:
    plain = client.get("/missions/")
    sorted_ = client.get("/missions/?sort=name")
    german = client.get("/missions/", headers={"Accept-Language": "de"})
    fragment = client.get("/missions/", headers={"HX-Request": "true"})

    assert {r[HEADER] for r in (plain, sorted_, german, fragment)} == {"miss"}
    assert client.get("/missions/", headers={"Accept-Language": "de"}).content == german.content
    assert german.content != plain.content


def test_the_host_is_part_of_the_key(client: Client, settings: Settings) -> None:
    settings.ALLOWED_HOSTS = ["a.example.com", "b.example.com"]
    client.get("/", headers={"Host": "a.example.com"})

    assert client.get("/", headers={"Host": "b.example.com"})[HEADER] == "miss"
    assert client.get("/", headers={"Host": "a.example.com"})[HEADER] == "hit"


def test_a_data_change_is_never_served_stale(client: Client) -> None:
    client.get("/missions/")
    save(mission((sortie(0, 1),)))  # bumps the data version

    response = client.get("/missions/")

    assert response[HEADER] == "miss"
    assert client.get("/missions/")[HEADER] == "hit"


@pytest.mark.parametrize("path", ["/", "/missions/", "/leaderboards/", "/aircraft/"])
def test_an_admin_save_shows_on_the_next_request(client: Client, path: str) -> None:
    """Anything an admin changes is on the cached pages at once (maintainer, 2026-11-06): every admin save of
    something the pages show bumps the data version (test_admin, test_notice_banner), which is part of the key."""
    get_site_settings()
    visitor = Client()
    visitor.get(path)
    assert visitor.get(path)[HEADER] == "hit"
    client.force_login(User.objects.create_superuser("boss", "boss@example.org", "x"))

    saved = client.post(
        settings_url(), settings_form(site_title="Night Raid", notice_text="Server maintenance tonight")
    )

    assert saved.status_code == 302
    response = visitor.get(path)
    assert response[HEADER] == "miss"
    assert b"Server maintenance tonight" in response.content
    assert b"Night Raid" in response.content


def test_detail_pages_searches_and_logged_in_visitors_are_not_cached(client: Client) -> None:
    save(mission((sortie(0, 1),)))
    player = Player.objects.first()
    assert player is not None
    for path in (f"/players/{player.pk}/", "/players/?q=Pilot", "/sorties/?q=Pilot"):
        client.get(path)
        assert not client.get(path).has_header(HEADER), path

    client.cookies["sessionid"] = "whatever"
    client.get("/missions/")
    assert not client.get("/missions/").has_header(HEADER)


@pytest.mark.parametrize("mode", ["memory", "files", "tiered"])
def test_the_entry_expires(client: Client, settings: Settings, clock: list[float], mode: str) -> None:
    settings.IL2KS_PAGE_CACHE = mode
    settings.IL2KS_PAGE_CACHE_SECONDS = 600
    client.get("/missions/")
    clock[0] += 599
    assert client.get("/missions/")[HEADER] == "hit"
    clock[0] += 2
    assert client.get("/missions/")[HEADER] == "miss"


def test_a_broken_entry_is_a_miss(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    client.get("/missions/")

    def broken(*_args: object, **_kwargs: object) -> object:
        raise EOFError("half-written file")

    monkeypatch.setattr(caches[MEMORY], "get", broken)
    response = client.get("/missions/")

    assert response.status_code == 200
    assert response[HEADER] == "miss"


def test_off_caches_nothing(client: Client, settings: Settings) -> None:
    settings.IL2KS_PAGE_CACHE = "off"
    client.get("/missions/")

    assert not client.get("/missions/").has_header(HEADER)


def test_the_file_backend_serves_what_another_worker_stored(client: Client, settings: Settings, tmp_path: Path) -> None:
    settings.IL2KS_PAGE_CACHE = "files"
    first = client.get("/missions/")
    assert list((tmp_path / "pages").iterdir()), "written to disk"

    second = client.get("/missions/")

    assert (first[HEADER], second[HEADER]) == ("miss", "hit")
    assert second.content == first.content


def test_the_warmer_fills_the_cache_for_the_default_variant(client: Client) -> None:
    save(mission((sortie(0, 1),)))

    pages, _seconds = page_cache.warm(application)

    assert pages >= len(page_cache.page_paths()) + 1  # the lists, the boards and at least one aircraft
    for path in ("/", "/missions/", "/players/", "/leaderboards/", "/aircraft/"):
        assert client.get(path, headers={"Host": "localhost"})[HEADER] == "hit", path


def test_the_warmer_also_warms_recent_visitors_variants(client: Client) -> None:
    client.get("/missions/", headers={"Accept-Language": "de"})
    caches[MEMORY].clear()

    page_cache.warm(application)

    assert client.get("/leaderboards/", headers={"Accept-Language": "de"})[HEADER] == "hit"


def test_the_warmer_waits_until_the_version_holds(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `reprocess` bumps the version every mission: warm once it is done, not after every mission."""
    calls = [0]
    warmed_at: list[int] = []

    def version() -> int:
        calls[0] += 1
        return min(calls[0], 6)  # changes for the first polls, then holds

    def warm(_app: object) -> tuple[int, float]:
        warmed_at.append(version())
        return (1, 0.0)

    monkeypatch.setattr(page_cache, "_current_version", version)
    monkeypatch.setattr(page_cache, "warm", warm)
    warmer = page_cache.Warmer(lambda _env, _start: [], poll_s=0.01, settle_s=0.1)
    warmer.start()
    try:
        deadline = time.monotonic() + 5
        while not warmed_at and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.3)
    finally:
        warmer.stop.set()
        warmer.join(5)

    assert warmed_at == [6], "one warm-up, for the settled version"


def test_a_process_from_an_earlier_start_cannot_take_the_warm_up(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seen in the load test: a worker left over from an earlier run claimed the shared warm-up, filled the cache under
    its own start ID (which no visitor's ETag has) and the running server skipped its own warm-up."""
    settings.IL2KS_PAGE_CACHE = "files"
    claim = page_cache._claim  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(page_cache, "_BOOT_ID", "an-earlier-start")
    assert claim(7, 1)
    monkeypatch.setattr(page_cache, "_BOOT_ID", "this-start")

    assert claim(7, 1)
    assert not claim(7, 1), "the next worker of this start leaves it to the first"


def test_tiered_copies_a_shared_hit_into_the_workers_memory(client: Client, settings: Settings, tmp_path: Path) -> None:
    """Another worker rendered the page: this one reads it from the files once, then from its own memory."""
    settings.IL2KS_PAGE_CACHE = "tiered"
    first = client.get("/missions/")
    caches[MEMORY].clear()  # as in a worker that has not seen the page yet

    from_files = client.get("/missions/")
    for entry in (tmp_path / "pages").iterdir():
        entry.unlink()
    from_memory = client.get("/missions/")

    assert (first[HEADER], from_files[HEADER], from_memory[HEADER]) == ("miss", "hit", "hit")
    assert from_memory.content == first.content


def test_tiered_copy_keeps_the_remaining_lifetime(client: Client, settings: Settings, clock: list[float]) -> None:
    """A page copied into memory at minute 9 must not live another 10 minutes there."""
    settings.IL2KS_PAGE_CACHE = "tiered"
    settings.IL2KS_PAGE_CACHE_SECONDS = 600
    client.get("/missions/")
    caches[MEMORY].clear()
    clock[0] += 540
    assert client.get("/missions/")[HEADER] == "hit"  # copied from the files, 60 s left

    clock[0] += 61

    assert client.get("/missions/")[HEADER] == "miss"


def test_the_warmers_requests_always_render(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """Found while adding the tiers: the re-warm before the lifetime runs out was served from the cache it should
    refresh, so the entries still expired on time and the next visitor rendered the page."""
    stored: list[str] = []
    original = page_cache.store

    def counting(request: HttpRequest, etag: str, response: HttpResponse) -> None:
        stored.append(request.path)
        original(request, etag, response)

    monkeypatch.setattr(page_cache, "store", counting)
    page_cache.warm(application)
    first = len(stored)
    page_cache.warm(application)

    assert first > 0
    assert len(stored) == 2 * first


def test_warming_keeps_the_test_transactions_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression (CI, Postgres): `render` calls the WSGI app, whose `request_started` and `request_finished` run
    `close_old_connections`, and inside a test transaction that closes the connection (SQLite's in-memory test database
    ignores the close, so only Postgres failed). Django's test client disconnects that receiver; these tests do too."""
    closed: list[str] = []
    real_close = connection.close

    def close() -> None:
        closed.append("close")
        real_close()

    monkeypatch.setattr(connection, "close", close)
    page_cache.warm(application)
    assert closed == []
