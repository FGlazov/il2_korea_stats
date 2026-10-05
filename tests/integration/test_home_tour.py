"""The home page follows the tour (OQ-79, TD-26), shows six boards as a 3x2 grid (play time added) and calls Elo games
"encounters". Synthetic data only."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from django.test import Client, override_settings

from il2ks.config import LeaderboardConfig
from il2ks.db.models import Mission, Player, Tour
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.queries.activity import recent_activity
from tests.factories import STARTED_AT, kill, meta, mission, save, sortie

pytestmark = pytest.mark.django_db

OCTOBER = datetime(2026, 10, 5, 12, tzinfo=UTC)
LOW = LeaderboardConfig(min_sorties=1, min_elo_games=1, min_attack_sorties=1, min_time_on_target_minutes=1.0)
AIR = "air_superiority"
STYLESHEET = Path(__file__).parents[2] / "src/il2ks/web/static/il2ks/leaderboards.css"


def seed_two_tours() -> tuple[Tour, Tour]:
    """September: Veteran flew 2 h and killed Newbie. October: Newbie flew 1 h (Veteran did not fly)."""
    save(
        mission(
            (
                sortie(0, 1, name="Veteran", combat_role=AIR, kills_air_pvp=1, kills_air_ai=0, flight_time_s=7200.0),
                sortie(1, 2, name="Newbie", coalition=2, combat_role=AIR, flight_time_s=600.0, is_death=True),
            ),
            (kill(100, 0, 1),),
        ),
        meta("2026-09-19_22-34-13", STARTED_AT),
    )
    save(
        mission((sortie(0, 2, name="Newbie", coalition=2, combat_role=AIR, flight_time_s=3600.0),)),
        meta("2026-10-05_12-00-00", OCTOBER),
    )
    september, october = Tour.objects.order_by("started_at")
    return september, october


def played(client: Client, url: str) -> dict[str, list[str]]:
    boards = client.get(url).context["boards"]
    return {b.key: [r.player.current_name for r in b.rows] for b in boards}


def started_months(client: Client, url: str) -> list[int]:
    return [m.started_at.month for m in client.get(url).context["latest"]]


def test_home_defaults_to_the_current_tour_and_all_time_is_explicit(client: Client) -> None:
    september, october = seed_two_tours()

    default = client.get("/")
    assert default.context["tour"] == october
    assert [m.started_at.month for m in default.context["latest"]] == [10]
    assert default.context["last_mission"].started_at.month == 10
    assert started_months(client, "/?tour=all") == [10, 9]
    assert started_months(client, f"/?tour={september.pk}") == [9]
    assert started_months(client, "/?tour=junk") == [10]  # a stale or malformed value is the current tour
    assert client.get("/?tour=all").context["tour"] is None


def test_home_has_the_tour_selector_and_its_links_keep_the_scope(client: Client) -> None:
    september, october = seed_two_tours()

    default = client.get("/").content.decode()
    all_time = client.get("/?tour=all").content.decode()

    assert '<option value="all">All time</option>' in default
    assert '<option value="" selected>' in default  # the current tour (OQ-78 dropdown)
    assert '<option value="all" selected>All time</option>' in all_time
    assert f'href="/missions/?tour={october.pk}"' in default
    assert 'href="/missions/?tour=all"' in all_time
    assert f"?tour={october.pk}" in client.get(f"/?tour={october.pk}").content.decode()
    assert f"?tour={september.pk}" in client.get(f"/?tour={september.pk}").content.decode()


def test_home_top_pilots_of_the_last_mission_follow_the_tour(client: Client) -> None:
    september, _ = seed_two_tours()

    html = client.get(f"/?tour={september.pk}").content.decode()

    assert client.get(f"/?tour={september.pk}").context["last_mission"].started_at.month == 9
    assert "Veteran" in html


def test_home_activity_follows_the_tour(client: Client) -> None:
    september, _ = seed_two_tours()

    assert client.get("/?tour=all").context["activity"] is not None
    assert [d.day.month for d in recent_activity()] == [9, 10]
    assert [d.day.month for d in recent_activity(tour=september)] == [9]
    assert [d.day.month for d in recent_activity(tour=Tour.objects.get(title="October 2026"))] == [10]


@override_settings(IL2KS_LEADERBOARDS=LOW)
def test_home_boards_follow_the_tour_elo_included(client: Client) -> None:
    september, _ = seed_two_tours()
    rebuild_aggregates()

    october = played(client, "/")
    all_time = played(client, "/?tour=all")
    sept = played(client, f"/?tour={september.pk}")

    assert october["play-time"] == ["Newbie"]  # Veteran did not fly in October
    assert all_time["play-time"] == ["Veteran", "Newbie"]  # 2 h before 1 h 10 min
    assert sept["play-time"] == ["Veteran", "Newbie"]
    # Elo resets every tour (OQ-128): nobody had a rated game in October, so its board is empty there
    assert october["elo-jet"] == []
    assert all_time["elo-jet"] == sept["elo-jet"] == ["Veteran", "Newbie"]


@override_settings(IL2KS_LEADERBOARDS=LOW)
def test_home_shows_six_boards_in_a_grid_with_play_time(client: Client) -> None:
    seed_two_tours()

    response = client.get("/?tour=all")
    html = response.content.decode()

    keys = [b.key for b in response.context["boards"]]
    assert keys == ["elo-jet", "elo-prop", "interception", "ground-hour", "tank-busting", "play-time"]
    assert html.count('<div class="home-boards">') == 1
    assert html.count("board-table") >= 6
    assert 'href="/leaderboards/play-time/?tour=all"' in html
    assert "2 h" in html  # Veteran's flight time in the play time board
    stylesheet = STYLESHEET.read_text(encoding="utf-8")
    assert "repeat(3, minmax(0, 1fr))" in stylesheet
    assert "repeat(2, minmax(0, 1fr))" in stylesheet


@override_settings(IL2KS_LEADERBOARDS=LOW)
def test_play_time_board_page_is_tour_aware_and_has_an_icon(client: Client) -> None:
    september, october = seed_two_tours()

    default = client.get("/leaderboards/play-time/")
    all_time = client.get("/leaderboards/play-time/?tour=all")

    assert default.status_code == 200
    assert [r.player.current_name for r in default.context["page_obj"].object_list] == ["Newbie"]
    assert [r.player.current_name for r in all_time.context["page_obj"].object_list] == ["Veteran", "Newbie"]
    assert default.context["tabs"][-1][:4] == ("play-time", "Flight time", "/leaderboards/play-time/", True)
    assert default.context["tabs"][-1][4] == "stat/play-time"
    assert [g for g, _ in default.context["tab_groups"]] == ["Air", "Ground", "General"]
    assert "Flight time" in all_time.content.decode()
    assert september.pk != october.pk


def test_the_home_etag_depends_on_the_full_url(client: Client) -> None:
    seed_two_tours()

    default = client.get("/")
    all_time = client.get("/?tour=all")

    assert default["ETag"] != all_time["ETag"]
    assert client.get("/", HTTP_IF_NONE_MATCH=default["ETag"]).status_code == 304
    assert client.get("/?tour=all", HTTP_IF_NONE_MATCH=default["ETag"]).status_code == 200


def test_home_has_no_streak_block_and_keeps_its_blocks_in_order(client: Client) -> None:
    """The ironman streaks moved to the leaderboards; the home page runs: online now, last mission, recently earned."""
    seed_two_tours()

    for url in ("/", "/?tour=all"):
        html = client.get(url).content.decode()
        assert "Longest ironman streaks" not in html
        assert 'id="streaks-heading"' not in html
        assert html.index('id="online-now-title"') < html.index('id="last-heading"')


def test_home_tour_selector_sits_in_the_banner_next_to_the_search(client: Client) -> None:
    seed_two_tours()

    html = client.get("/").content.decode()
    hero = html[html.index('class="hero"') : html.index("</section>", html.index('class="hero"'))]

    assert 'role="search"' in hero
    assert 'id="f-tour"' in hero


# --- wording: Elo "games" are "encounters" ---------------------------------------------------------------------------
@override_settings(IL2KS_LEADERBOARDS=LOW)
def test_elo_games_are_called_encounters_everywhere(client: Client) -> None:
    seed_two_tours()
    rebuild_aggregates()
    veteran = Player.objects.get(current_name="Veteran")
    pages = [
        f"/players/{veteran.pk}/?tour=all",
        "/leaderboards/elo-jet/",
        "/leaderboards/elo-prop/",
        "/",
    ]

    for url in pages:
        html = client.get(url).content.decode()
        assert "rated game" not in html.lower(), url
        assert "Rated games" not in html, url

    profile = client.get(f"/players/{veteran.pk}/?tour=all").content.decode()
    assert "1 encounter" in profile
    board = client.get("/leaderboards/elo-jet/").content.decode()
    assert "Encounters" in board
    assert "Pilots with at least 1 encounter." in board
    assert Mission.objects.count() == 2
