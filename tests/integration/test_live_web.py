"""Online now, web side: `/live/`, the component and the read helper (FR-ING-12, FR-WEB-15, TD-28)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from django.db import OperationalError
from django.http import HttpRequest
from django.template import Context, Template
from django.test import Client

from il2ks.db.models import DataVersion, LiveMission, LivePlayer, LiveState, Player, SiteSettings
from il2ks.queries import live
from tests.simple_reads import assert_simple_reads

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 9, 19, 21, 30, tzinfo=UTC)
SERVER = uuid.UUID("11111111-2222-3333-4444-555555555555")


def make_mission(*, updated: datetime | None = None, running: bool = True, interval_s: float = 30.0) -> LiveMission:
    return LiveMission.objects.create(
        server_uid=SERVER,
        mission_uid="2026-09-19_21-00-00",
        mission_file="Multiplayer/Dogfight\\Alonzo\\Operation_Ripper_1950\\Operation_Ripper_1950.msnbin",
        started_at=NOW - timedelta(minutes=30),
        game_date="1951.4.24",
        game_time="12:0:0",
        elapsed_s=1830.0,
        updated_at=updated or datetime.now(UTC),
        interval_s=interval_s,
        is_running=running,
    )


def make_player(
    mission: LiveMission,
    name: str,
    *,
    country: int = 501,
    state: str = LiveState.IN_FLIGHT,
    aircraft: str = "MiG-15bis",
    known: Player | None = None,
    kills: tuple[int, int] = (0, 0),
) -> LivePlayer:
    return LivePlayer.objects.create(
        mission=mission,
        player=known,
        account_uuid=str(uuid.uuid4()),
        name=name,
        coalition=1 if country // 100 == 5 else 2,
        country=country,
        aircraft_type=aircraft if state != LiveState.CONNECTED else "",
        aircraft_name="MiG-15bis" if state != LiveState.CONNECTED else "",
        propulsion="jet" if state != LiveState.CONNECTED else "",
        state=state,
        sortie_started_at=NOW,
        flight_time_s=754.0 if state != LiveState.CONNECTED else 0.0,
        kills_air=kills[0],
        kills_ground=kills[1],
    )


def known_player(name: str, *, hidden: bool = False) -> Player:
    return Player.objects.create(
        account_uuid=str(uuid.uuid4()),
        current_name=name,
        name_lower=name.lower(),
        first_seen=NOW,
        last_seen=NOW,
        is_hidden=hidden,
    )


def fetch(client: Client, **headers: str) -> tuple[int, str]:
    response = client.get("/live/", headers=headers or None)
    return response.status_code, response.content.decode()


def test_the_fragment_lists_counts_and_players(client: Client) -> None:
    mission = make_mission()
    make_player(mission, "Alpha", country=501, kills=(2, 5))
    make_player(mission, "Bravo", country=601, state=LiveState.ON_GROUND)
    make_player(mission, "Charlie", country=601, state=LiveState.CONNECTED)

    response = client.get("/live/")
    html = response.content.decode()

    assert response.status_code == 200
    assert "3 players online" in html
    assert "REDFOR" in html
    assert "BLUFOR" in html
    for name in ("Alpha", "Bravo", "Charlie"):
        assert name in html
    assert "Operation_Ripper_1950" in html  # the map, not the file path
    assert "In flight" in html
    assert "On the ground" in html
    assert "In the lobby" in html
    assert "12 min" in html  # 754 s in the air
    assert "2 / 5" in html  # air / ground kills
    assert "<svg" in html  # aircraft and state icons
    assert html.count("<tr") == 4  # header + 3 players


def test_the_fragment_has_its_own_short_cache_and_no_data_version_etag(client: Client) -> None:
    make_mission()
    DataVersion.objects.create(pk=1, version=7)

    response = client.get("/live/")

    cache = response["Cache-Control"]
    assert "max-age=15" in cache
    assert "public" in cache
    assert not response.has_header("ETag")
    assert "Accept-Language" in response["Vary"]
    assert "Cookie" in response["Vary"]  # the language cookie changes the body


def test_a_busy_database_gives_a_retryable_503_that_htmx_will_not_swap(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    def busy(now: datetime | None = None) -> live.OnlineNow:
        raise OperationalError("database is locked")

    monkeypatch.setattr(live, "current", busy)

    response = client.get("/live/")

    assert response.status_code == 503
    assert response["Retry-After"] == "5"
    assert "no-store" in response["Cache-Control"]


def test_a_conditional_request_never_gets_a_304(client: Client) -> None:
    make_mission()
    response = client.get("/live/", headers={"If-None-Match": '"anything"', "HX-Request": "true"})
    assert response.status_code == 200
    assert not response.has_header("ETag")


def test_a_player_hidden_by_the_admin_shows_as_hidden_player(client: Client) -> None:
    mission = make_mission()
    secret = known_player("SecretName", hidden=True)
    shown = known_player("ShownName")
    make_player(mission, "SecretName", known=secret)
    make_player(mission, "ShownName", known=shown, country=601)

    _, html = fetch(client)

    assert "SecretName" not in html
    assert "Hidden player" in html
    assert "ShownName" in html
    assert f"/players/{shown.pk}/" in html
    assert f"/players/{secret.pk}/" not in html
    assert "2 players online" in html  # hidden players still count


def test_hiding_takes_effect_at_once_without_a_new_snapshot(client: Client) -> None:
    mission = make_mission()
    player = known_player("Soon Hidden")
    make_player(mission, "Soon Hidden", known=player)
    assert "Soon Hidden" in fetch(client)[1]

    player.is_hidden = True
    player.save()

    assert "Soon Hidden" not in fetch(client)[1]


def test_a_player_still_joining_has_no_name_yet(client: Client) -> None:
    mission = make_mission()
    make_player(mission, "", state=LiveState.CONNECTED)
    _, html = fetch(client)
    assert "Joining" in html
    assert "1 player online" in html


def test_a_running_mission_with_nobody_on_says_so(client: Client) -> None:
    make_mission()
    _, html = fetch(client)
    assert "0 players online" in html
    assert "Nobody is on the server right now." in html


def test_stale_data_reads_as_no_mission_running_with_last_seen(client: Client) -> None:
    mission = make_mission(updated=datetime.now(UTC) - timedelta(seconds=3 * 30 + 20))
    make_player(mission, "Ghost")

    _, html = fetch(client)

    assert "No mission running" in html
    assert "Last seen" in html
    assert "Ghost" not in html


def test_data_just_inside_three_intervals_is_still_live(client: Client) -> None:
    mission = make_mission(updated=datetime.now(UTC) - timedelta(seconds=3 * 30 - 10))
    make_player(mission, "StillHere")
    assert "StillHere" in fetch(client)[1]


def test_a_finished_mission_reads_as_no_mission_running(client: Client) -> None:
    make_mission(running=False)
    _, html = fetch(client)
    assert "No mission running" in html
    assert "Last seen" in html


def test_nothing_ever_seen(client: Client) -> None:
    _, html = fetch(client)
    assert "No mission running" in html
    assert "No mission has been seen on the server yet." in html
    assert "Last seen" not in html


def test_stale_depends_on_the_interval_the_snapshot_was_written_with() -> None:
    mission = make_mission(updated=NOW - timedelta(seconds=100), interval_s=60.0)
    assert not live.is_stale(mission, NOW)
    assert live.is_stale(mission, NOW + timedelta(seconds=81))
    assert live.current(NOW).running
    assert not live.current(NOW + timedelta(seconds=81)).running


def test_players_are_ordered_by_side_then_state(client: Client) -> None:
    mission = make_mission()
    make_player(mission, "Zulu", country=601, state=LiveState.CONNECTED)
    make_player(mission, "Yankee", country=601, state=LiveState.IN_FLIGHT)
    make_player(mission, "Xray", country=501, state=LiveState.ON_GROUND)
    make_player(mission, "Walt", country=501, state=LiveState.IN_FLIGHT)

    names = [row.name for row in live.current().rows]

    assert names == ["Walt", "Xray", "Yankee", "Zulu"]


def test_side_names_follow_the_site_settings(client: Client) -> None:
    SiteSettings.objects.create(pk=1, redfor_name="Reds", blufor_name="Blues")
    mission = make_mission()
    make_player(mission, "Alpha", country=501)
    _, html = fetch(client)
    assert "Reds" in html
    assert "Blues" in html


def test_mission_name_helper() -> None:
    assert live.mission_name("Multiplayer/Dogfight\\A\\B\\Map_1950.msnbin", "uid") == "Map_1950"
    assert live.mission_name("", "2026-09-19_21-00-00") == "2026-09-19_21-00-00"
    assert live.mission_name("plain", "uid") == "plain"


def test_fragment_query_budget_is_simple_reads(client: Client) -> None:
    mission = make_mission()
    for n in range(12):
        make_player(mission, f"Pilot {n}", known=known_player(f"Pilot {n}"))

    # data version (middleware), site settings (context processor), the live mission, its players with their accounts
    assert_simple_reads(client, "/live/", max_queries=5)


def test_the_idle_fragment_needs_two_reads_at_most(client: Client) -> None:
    assert_simple_reads(client, "/live/", max_queries=3)


# --- the component ---------------------------------------------------------------------------------------------------


def render_component() -> str:
    request = HttpRequest()
    request.method = "GET"
    template = Template("{% load il2ks_live %}{% online_now %}")
    return template.render(Context({"request": request, "site": SiteSettings()}))


def test_component_polls_the_endpoint_and_carries_a_static_snapshot() -> None:
    mission = make_mission()
    make_player(mission, "Alpha")

    html = render_component()

    assert 'hx-get="/live/"' in html
    assert 'hx-trigger="load, every 30s"' in html
    assert 'hx-swap="innerHTML"' in html
    assert "Online now" in html
    assert "Alpha" in html  # rendered on the server: works without JavaScript
    assert "<table" in html


def test_component_without_a_mission_still_renders_and_polls() -> None:
    html = render_component()
    assert 'hx-get="/live/"' in html
    assert "No mission running" in html


def test_component_poll_period_follows_the_snapshot_interval_but_not_below_the_cache() -> None:
    make_mission(interval_s=60.0)
    assert 'hx-trigger="load, every 60s"' in render_component()
    LiveMission.objects.all().delete()
    make_mission(interval_s=5.0)
    assert 'hx-trigger="load, every 15s"' in render_component()
