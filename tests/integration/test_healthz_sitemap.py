"""`/healthz` (uptime monitors), `/sitemap.xml` and `/robots.txt` (0.2.0 ops items)."""

import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from django.db import OperationalError, connection
from django.http import HttpResponse
from django.test import Client
from django.test.utils import CaptureQueriesContext
from pytest_django.fixtures import Settings

from il2ks.db.models import AircraftStats, DataVersion, GameObject, Mission, ObjectClass, Player
from il2ks.db.site import bump_data_version
from il2ks.serving.djsettings import security_settings
from il2ks.web.views import seo
from tests.factories import SERVER_UID
from tests.ops_helpers import make_instance

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def make_player(n: int, **fields: object) -> Player:
    values: dict[str, object] = {
        "account_uuid": f"00000000-0000-4000-8000-{n:012d}",
        "current_name": f"Pilot{n}",
        "name_lower": f"pilot{n}",
        "first_seen": NOW,
        "last_seen": NOW - timedelta(days=n),
        "sorties": 10,
        **fields,
    }
    return Player.objects.create(**values)


def make_mission(n: int, **fields: object) -> Mission:
    started = NOW - timedelta(days=100 - n)
    values: dict[str, object] = {
        "server_uid": SERVER_UID,
        "mission_uid": f"2026-01-{n:02d}_00-00-00",
        "mission_file": "Multiplayer/Dogfight\\A\\B\\B.msnbin",
        "started_at": started,
        "ended_at": started + timedelta(hours=3),
        "duration_s": 10_800.0,
        "game_date": "1951.9.15",
        "game_time": "13:0:0",
        "game_type": 2,
        "completed_cleanly": True,
        "players_total": 3,
        "sorties_total": 6,
        "redfor_sorties": 3,
        "blufor_sorties": 3,
        **fields,
    }
    return Mission.objects.create(**values)


def make_aircraft(name: str) -> GameObject:
    obj = GameObject.objects.create(log_name=name, display_name=name, cls=ObjectClass.FIGHTER)
    AircraftStats.objects.create(aircraft=obj, sorties=3)
    return obj


def locs(response: HttpResponse) -> list[str]:
    return [e.text or "" for e in ET.fromstring(response.content).findall(".//s:loc", NS)]


@pytest.fixture(autouse=True)
def _public_address(settings: Settings) -> None:
    settings.IL2KS_PUBLIC_URL = "https://stats.example.com"
    seo._cache.clear()  # pyright: ignore[reportPrivateUsage]


# --- /healthz -------------------------------------------------------------------------------------------------------


def test_healthz_is_200_without_login_and_never_cached(client: Client) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.content == b"ok\n"
    assert response["Cache-Control"] == "no-store"
    assert not response.has_header("ETag")
    assert not response.cookies


def test_healthz_ignores_if_none_match_and_the_data_version(client: Client) -> None:
    bump_data_version()
    first = client.get("/healthz", headers={"If-None-Match": '"anything"'})
    assert first.status_code == 200
    with CaptureQueriesContext(connection) as queries:
        client.get("/healthz")
    assert len(queries) == 1, "one tiny database read, nothing else"


def test_healthz_head_and_trailing_slash(client: Client) -> None:
    assert client.head("/healthz").status_code == 200
    assert client.get("/healthz/").status_code == 200
    assert client.post("/healthz").status_code == 405


def test_healthz_is_503_with_a_short_body_when_the_database_read_fails(client: Client) -> None:
    with patch.object(DataVersion.objects, "only", side_effect=OperationalError("disk I/O error")):
        response = client.get("/healthz")
    assert response.status_code == 503
    assert response.content == b"database unavailable\n"
    assert response["Cache-Control"] == "no-store"


def test_healthz_survives_the_other_middleware(client: Client, settings: Settings) -> None:
    """A monitor on the same machine: plain http, Host 127.0.0.1, no cookies, any Accept-Language."""
    settings.ALLOWED_HOSTS = ["stats.example.com", "localhost", "127.0.0.1", "[::1]"]
    response = client.get("/healthz", headers={"Host": "127.0.0.1:8000", "Accept-Language": "ru"})
    assert response.status_code == 200
    assert response["Cache-Control"] == "no-store"


def test_healthz_is_exempt_from_the_https_redirect_in_production_settings(
    client: Client, settings: Settings, tmp_path: Path
) -> None:
    exempt = security_settings(make_instance(tmp_path)).redirect_exempt
    assert any(re.match(rule, "healthz") for rule in exempt)
    settings.SECURE_SSL_REDIRECT = True
    settings.SECURE_REDIRECT_EXEMPT = list(exempt)
    assert client.get("/healthz").status_code == 200  # no redirect
    assert client.get("/missions/").status_code == 301  # everything else still redirects


# --- sitemap --------------------------------------------------------------------------------------------------------


def test_sitemap_lists_visible_players_missions_and_aircraft_with_absolute_urls(client: Client) -> None:
    shown = make_player(1)
    hidden = make_player(2, is_hidden=True)
    open_mission = make_mission(1)
    hidden_mission = make_mission(2, is_hidden=True)
    empty_mission = make_mission(3, sorties_total=0)
    jet = make_aircraft("F-86F-30")

    response = client.get("/sitemap.xml")

    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/xml")
    found = locs(response)
    assert "https://stats.example.com/" in found
    assert f"https://stats.example.com/players/{shown.pk}/" in found
    assert f"https://stats.example.com/missions/{open_mission.pk}/" in found
    assert f"https://stats.example.com/aircraft/{jet.pk}/" in found
    joined = "\n".join(found)
    assert f"/players/{hidden.pk}/" not in joined
    assert f"/missions/{hidden_mission.pk}/" not in joined
    assert f"/missions/{empty_mission.pk}/" not in joined
    assert "/sorties/" not in joined.replace(
        "https://stats.example.com/sorties/\n", ""
    )  # the list only, no sortie page
    assert "/p/" not in joined  # Markdown pages are not built yet
    assert all(u.startswith("https://stats.example.com/") for u in found)


def test_sitemap_without_a_configured_address_uses_the_request_host(client: Client, settings: Settings) -> None:
    settings.IL2KS_PUBLIC_URL = ""
    make_player(1)
    found = locs(client.get("/sitemap.xml", headers={"Host": "localhost"}))
    assert "http://localhost/" in found


def test_sitemap_escapes_and_has_lastmod(client: Client) -> None:
    player = make_player(1)
    body = client.get("/sitemap.xml").content.decode()
    assert f"/players/{player.pk}/</loc><lastmod>{player.last_seen.date().isoformat()}</lastmod>" in body


def test_a_big_site_gets_a_sitemap_index_with_paged_files(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(seo, "PAGE_SIZE", 3)
    players = [make_player(n) for n in range(1, 8)]  # 7 players -> 3 files
    make_player(8, is_hidden=True)
    mission = make_mission(1)

    index = client.get("/sitemap.xml")
    names = [u.removeprefix("https://stats.example.com") for u in locs(index)]
    assert ET.fromstring(index.content).tag.endswith("sitemapindex")
    assert names == [
        "/sitemap-site.xml",
        "/sitemap-missions-1.xml",
        "/sitemap-players-1.xml",
        "/sitemap-players-2.xml",
        "/sitemap-players-3.xml",
    ]
    seen: list[str] = []
    for name in names:
        part = client.get(name)
        assert part.status_code == 200, name
        seen += locs(part)
    assert len(seen) == len(set(seen))
    for player in players:
        assert f"https://stats.example.com/players/{player.pk}/" in seen
    assert f"https://stats.example.com/missions/{mission.pk}/" in seen
    assert client.get("/sitemap-players-4.xml").status_code == 404
    assert client.get("/sitemap-players-0.xml").status_code == 404


def test_sitemap_is_revalidated_by_the_data_version_and_rendered_once_per_version(client: Client) -> None:
    make_player(1)
    first = client.get("/sitemap.xml")
    assert first["ETag"]
    assert "must-revalidate" in first["Cache-Control"]
    with CaptureQueriesContext(connection) as queries:
        again = client.get("/sitemap.xml", headers={"If-None-Match": first["ETag"]})
    assert again.status_code == 304
    assert len(queries) == 1

    with CaptureQueriesContext(connection) as queries:
        client.get("/sitemap.xml")  # a new client without the ETag: served from memory
    assert not [q for q in queries.captured_queries if "il2ks_db_player" in q["sql"]]

    make_player(2)
    bump_data_version()
    assert len(locs(client.get("/sitemap.xml"))) == len(locs(first)) + 1


def test_sitemap_query_budget_does_not_grow_with_the_number_of_rows(client: Client) -> None:
    """A big database must cost a handful of reads: counts and one slice per kind, never a query per row."""
    Player.objects.bulk_create(
        [
            Player(
                account_uuid=f"00000000-0000-4000-8000-{n:012d}",
                current_name=f"P{n}",
                name_lower=f"p{n}",
                first_seen=NOW,
                last_seen=NOW,
            )
            for n in range(1, 301)
        ]
    )
    with CaptureQueriesContext(connection) as queries:
        response = client.get("/sitemap.xml")
    assert len(locs(response)) >= 300
    assert len(queries) <= 8, [q["sql"] for q in queries.captured_queries]
    for q in queries.captured_queries:
        assert "GROUP BY" not in q["sql"].upper()


# --- robots.txt -----------------------------------------------------------------------------------------------------


def test_robots_allows_public_pages_blocks_admin_and_names_the_sitemap(client: Client) -> None:
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain")
    lines = response.content.decode().splitlines()
    assert lines[0] == "User-agent: *"
    assert "Disallow: /admin/" in lines
    assert "Disallow: /*?*sort=" in lines
    assert "Disallow: /" not in lines  # the site itself stays crawlable
    assert lines[-1] == "Sitemap: https://stats.example.com/sitemap.xml"
    assert "must-revalidate" in response["Cache-Control"]
