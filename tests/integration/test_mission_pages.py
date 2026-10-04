"""Home, mission list and mission detail (FR-WEB-1, FR-WEB-2, FR-ADM-3, TD-22): reads, hiding, sorting, paging."""

import re
from datetime import UTC, datetime, timedelta

import pytest
from django.test import Client

from il2ks.db.models import Mission, Player, PlayerSortie, SiteSettings
from tests.factories import SERVER_UID, account, kill, mission, save, sortie
from tests.simple_reads import assert_simple_reads

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
FILE = "Multiplayer/Dogfight\\Author\\The_Sinuiju_Bridges_1951\\The_Sinuiju_Bridges_1951.msnbin"
CONTEXT_READS = 2  # the site context processor (SiteSettings and DataVersion)


def make_mission(index: int, *, sorties: int = 4, **fields: object) -> Mission:
    """A mission row with counters only (list and home page tests need no sorties). Newer for a higher `index`."""
    started = NOW - timedelta(days=100 - index)
    values: dict[str, object] = {
        "server_uid": SERVER_UID,
        "mission_uid": f"2026-01-{index:02d}_00-00-00",
        "mission_file": FILE,
        "started_at": started,
        "ended_at": started + timedelta(hours=3),
        "duration_s": 10_800.0,
        "game_date": "1951.9.15",
        "game_time": "13:0:0",
        "game_type": 2,
        "completed_cleanly": True,
        "players_total": 3,
        "sorties_total": sorties,
        "redfor_sorties": sorties // 2,
        "blufor_sorties": sorties - sorties // 2,
        **fields,
    }
    return Mission.objects.create(**values)


def detail_mission() -> Mission:
    """Maverick (REDFOR) kills Goose (BLUFOR), an AI tank and Wingman (friendly fire); Gunny is a REDFOR gunner."""
    return save(
        mission(
            (
                sortie(0, 1, name="Maverick", kills_air=1, kills_ground=1),
                sortie(
                    1, 2, name="Goose", aircraft_type="F-86A-5", coalition=2, outcome="shot_down", is_plane_lost=True
                ),
                sortie(2, 3, name="Gunny", aircraft_type="Turret_IL10", role="gunner"),
                sortie(3, 4, name="Wingman", is_plane_lost=True, outcome="crashed"),
            ),
            (
                kill(20_000, killer=0, victim=1),
                kill(21_000, killer=0, victim=None, victim_type="M46 Patton", victim_kind="ground"),
                kill(23_000, killer=0, victim=3, is_friendly=True, victim_type="MiG-15bis"),
            ),
        )
    )


def body(client: Client, url: str) -> str:
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


# --- home ---------------------------------------------------------------------------------------------------------
def test_home_without_missions_is_a_friendly_page(client: Client) -> None:
    assert_simple_reads(client, "/", max_queries=CONTEXT_READS + 2)  # + the streaks block

    assert "Latest missions" in body(client, "/")


def test_home_shows_latest_missions_and_the_last_missions_top_pilots(client: Client) -> None:
    saved = detail_mission()
    Player.objects.filter(account_uuid=account(1)).update(current_name="Maverick")
    make_mission(5, sorties=0)  # newer than nothing: empty missions stay off the home page

    assert_simple_reads(client, "/", max_queries=CONTEXT_READS + 3)  # + the streaks block

    html = body(client, "/")
    assert "korea test" in html  # "missions/korea_test" from the factory
    assert f"/missions/{saved.pk}/" in html
    assert "Maverick" in html  # top pilot by kills
    assert html.count("/missions/") >= 2  # row link + last mission link (+ "All missions")


def test_home_leaves_out_hidden_missions_and_hidden_pilots(client: Client) -> None:
    saved = detail_mission()
    Player.objects.filter(account_uuid=account(1)).update(is_hidden=True, current_name="Maverick")

    html = body(client, "/")
    assert "Maverick" not in html  # a hidden pilot never appears in the top list

    Mission.objects.filter(pk=saved.pk).update(is_hidden=True)
    html = body(client, "/")
    assert f"/missions/{saved.pk}/" not in html
    assert "No missions yet." in html


def test_home_shows_the_site_description(client: Client) -> None:
    SiteSettings.objects.create(pk=1, description="We fly on Fridays.")

    assert "We fly on Fridays." in body(client, "/")


# --- mission list -------------------------------------------------------------------------------------------------
def test_list_is_newest_first_and_cheap(client: Client) -> None:
    old, new = make_mission(1), make_mission(2)

    assert_simple_reads(client, "/missions/", max_queries=CONTEXT_READS + 3)  # tours (selector), count, page

    html = body(client, "/missions/")
    assert html.index(f"/missions/{new.pk}/") < html.index(f"/missions/{old.pk}/")
    assert "The Sinuiju Bridges 1951" in html
    assert "1951-09-15 13:00" in html  # the in-game date


def test_empty_missions_are_hidden_unless_asked_for(client: Client) -> None:
    flown, empty = make_mission(1), make_mission(2, sorties=0)

    html = body(client, "/missions/")
    assert f"/missions/{flown.pk}/" in html
    assert f"/missions/{empty.pk}/" not in html

    assert f"/missions/{empty.pk}/" in body(client, "/missions/?empty=1")


def test_hidden_missions_are_not_listed(client: Client) -> None:
    visible, hidden = make_mission(1), make_mission(2, is_hidden=True)

    html = body(client, "/missions/?empty=1")
    assert f"/missions/{visible.pk}/" in html
    assert f"/missions/{hidden.pk}/" not in html


def test_period_name_and_winner_filters(client: Client) -> None:
    recent = make_mission(2, winning_coalition=1, started_at=datetime.now(UTC) - timedelta(days=2))
    old = make_mission(
        1, winning_coalition=2, mission_file="Dogfight\\Other_Map_1950\\Other_Map_1950.msnbin"
    )  # started months before NOW

    assert f"/missions/{old.pk}/" not in body(client, "/missions/?period=7")
    assert f"/missions/{recent.pk}/" in body(client, "/missions/?period=7")
    assert f"/missions/{recent.pk}/" not in body(client, "/missions/?q=other+map")
    assert f"/missions/{old.pk}/" in body(client, "/missions/?q=other+map")
    assert f"/missions/{old.pk}/" not in body(client, "/missions/?winner=redfor")
    assert f"/missions/{old.pk}/" in body(client, "/missions/?winner=blufor")
    assert f"/missions/{old.pk}/" not in body(client, "/missions/?winner=none")


@pytest.mark.parametrize("junk", ["period=1", "period=abc", "winner=green", "empty=yes", "q=%25_"])
def test_unknown_filter_values_are_ignored(client: Client, junk: str) -> None:
    make_mission(1)

    assert client.get(f"/missions/?{junk}").status_code == 200


def test_sorting_uses_a_whitelist(client: Client) -> None:
    small = make_mission(1, kills_air=2, players_total=3)
    big = make_mission(2, kills_air=9, players_total=1)

    by_kills = body(client, "/missions/?sort=air_kills")
    assert by_kills.index(f"/missions/{small.pk}/") < by_kills.index(f"/missions/{big.pk}/")
    by_kills_desc = body(client, "/missions/?sort=-air_kills")
    assert by_kills_desc.index(f"/missions/{big.pk}/") < by_kills_desc.index(f"/missions/{small.pk}/")
    by_players = body(client, "/missions/?sort=-players")
    assert by_players.index(f"/missions/{small.pk}/") < by_players.index(f"/missions/{big.pk}/")

    for junk in ("password", "-pk", "started_at", "mission_file", "air_kills;drop", ""):
        response = client.get("/missions/", {"sort": junk})
        assert response.status_code == 200, junk
        assert response.context["sort"] == "-date", junk  # falls back to the default, never an error


def test_list_paginates(client: Client) -> None:
    for index in range(1, 28):
        make_mission(index)

    first = body(client, "/missions/")
    second = body(client, "/missions/?page=2")

    assert len(re.findall(r'href="/missions/\d+/"', first)) == 25
    assert len(re.findall(r'href="/missions/\d+/"', second)) == 2
    assert "page=2" in first
    assert client.get("/missions/?page=999").status_code == 200  # Django's get_page clamps


def test_htmx_requests_get_a_page_that_contains_the_results_region(client: Client) -> None:
    make_mission(1)

    response = client.get("/missions/?sort=duration", HTTP_HX_REQUEST="true")

    assert response.status_code == 200
    assert 'id="results"' in response.content.decode()


# --- mission detail -----------------------------------------------------------------------------------------------
def test_detail_lists_sorties_by_coalition_and_the_kill_list(client: Client) -> None:
    saved = detail_mission()
    maverick = Player.objects.get(account_uuid=account(1))
    sortie_1 = PlayerSortie.objects.get(mission=saved, account_uuid=account(1))

    assert_simple_reads(client, f"/missions/{saved.pk}/", max_queries=CONTEXT_READS + 3)  # mission, sorties, kills

    html = body(client, f"/missions/{saved.pk}/")
    assert "korea test" in html
    assert "REDFOR" in html
    assert "BLUFOR" in html
    assert f"/players/{maverick.pk}/" in html
    assert f"/sorties/{sortie_1.pk}/" in html
    assert html.index("Maverick") < html.index("Goose")  # REDFOR first
    assert "Gunner" in html  # gunner sorties are marked
    assert "Friendly fire" in html  # the friendly kill in the kill list
    assert "Kills" in html
    assert "Shot down" in html


def test_detail_anonymises_hidden_players_everywhere(client: Client) -> None:
    saved = detail_mission()
    goose = Player.objects.get(account_uuid=account(2))
    goose_sortie = PlayerSortie.objects.get(mission=saved, account_uuid=account(2))
    Player.objects.filter(pk=goose.pk).update(is_hidden=True)

    html = body(client, f"/missions/{saved.pk}/")

    assert "Goose" not in html  # neither the sortie row nor the victim in the kill list
    assert f"/players/{goose.pk}/" not in html
    assert f"/sorties/{goose_sortie.pk}/" not in html  # their sortie page answers 404
    assert "Hidden player" in html
    assert "Maverick" in html  # everyone else is unaffected


def test_hidden_and_missing_missions_are_404(client: Client) -> None:
    saved = detail_mission()
    assert client.get(f"/missions/{saved.pk}/").status_code == 200

    Mission.objects.filter(pk=saved.pk).update(is_hidden=True)

    assert client.get(f"/missions/{saved.pk}/").status_code == 404
    assert client.get("/missions/999999/").status_code == 404


def test_detail_of_an_empty_mission_renders(client: Client) -> None:
    empty = make_mission(1, sorties=0, completed_cleanly=False)

    html = body(client, f"/missions/{empty.pk}/")

    assert "Nobody flew for this side." in html
    assert "No player-versus-player kills" in html
    assert "Log incomplete" in html


def test_detail_shows_the_winner(client: Client) -> None:
    won = make_mission(1, winning_coalition=2, countries={"501": 1, "601": 2})

    html = body(client, f"/missions/{won.pk}/")

    assert "Winner" in html
    assert "badge badge--blufor" in html


def test_site_emblems_show_on_the_mission_pages(client: Client) -> None:
    won = make_mission(1, winning_coalition=1, countries={"501": 1, "601": 2})
    plain = body(client, f"/missions/{won.pk}/")
    assert 'fill="#c8312b"' not in plain  # neutral emblems are single-colour outlines

    SiteSettings.objects.create(pk=1, redfor_emblem="vvs", blufor_emblem="un")

    html = body(client, f"/missions/{won.pk}/")
    assert 'fill="#c8312b"' in html  # the VVS star
    assert 'fill="#4f8fdc"' in html  # the UN-style roundel
