"""Killboard assists toggle (OQ-56), per-tour killboard and streaks (TD-26), and the best-streaks page (OQ-57/58).

Companion of test_killboard_streaks.py (the all-time rules); incremental == rebuild is checked with its `snapshot()`."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from django.test import Client

from il2ks.core.killboard import KillboardRules
from il2ks.db.models import (
    Player,
    PlayerBestStreak,
    PlayerKillboard,
    PlayerStreak,
    PlayerTourKillboard,
    SiteSettings,
    Tour,
)
from il2ks.ingest.aggregates import rebuild_aggregates, recompute_players
from tests.factories import STARTED_AT, kill, meta, mission, save, sortie
from tests.integration.test_killboard_streaks import board, duel_mission, pk, snapshot
from tests.simple_reads import PROFILE_READS_TOUR, assert_simple_reads

pytestmark = pytest.mark.django_db

OCTOBER = datetime(2026, 10, 5, 12, tzinfo=UTC)
ASSISTS_ON = KillboardRules(assists=True)


def numbers() -> dict[int, int]:
    return {p.pk: int(p.account_uuid[-12:]) for p in Player.objects.all()}


def assists_board() -> dict[tuple[int, int], tuple[int, int, int]]:
    """(player number, opponent number) -> (kills, deaths, assists)."""
    n = numbers()
    return {(n[r.player_id], n[r.opponent_id]): (r.kills, r.deaths, r.assists) for r in PlayerKillboard.objects.all()}


def tour_named(title: str) -> Tour:
    return Tour.objects.get(title=title)


def tour_board(title: str) -> dict[tuple[int, int], tuple[int, int]]:
    n = numbers()
    return {
        (n[r.player_id], n[r.opponent_id]): (r.kills, r.deaths)
        for r in PlayerTourKillboard.objects.filter(tour=tour_named(title))
    }


def streak_rows(player_number: int, title: str | None = None) -> dict[str, tuple[int, int, float]]:
    tour = None if title is None else tour_named(title)
    rows = PlayerBestStreak.objects.filter(player=pk(player_number), tour=tour)
    return {r.kind: (r.sorties, r.kills_air, r.flight_time_s) for r in rows}


# --- assists ---
def test_assists_are_off_by_default_and_not_stored() -> None:
    save(duel_mission(), meta("m1", STARTED_AT))

    assert not SiteSettings.objects.filter(killboard_assists=True).exists()
    assert assists_board() == {(1, 2): (2, 1, 0), (2, 1): (1, 2, 0)}  # player 3's assist on 2 left no pair


def test_assists_on_get_their_own_column_and_never_change_the_kills() -> None:
    save(duel_mission(), meta("m1", STARTED_AT))
    before = board()

    rebuild_aggregates(board=ASSISTS_ON)

    assert SiteSettings.objects.get(pk=1).killboard_assists
    assert board() == {**before, (3, 2): (0, 0), (2, 3): (0, 0)}  # a pair with only an assist now has a row
    assert assists_board()[(3, 2)] == (0, 0, 1)  # player 3 assisted on player 2's lost sortie
    assert assists_board()[(2, 3)] == (0, 0, 0)  # the mirror row has no assist of its own
    assert assists_board()[(1, 2)] == (2, 1, 0)


def test_the_stored_setting_decides_for_new_missions_until_the_next_rebuild() -> None:
    rebuild_aggregates(board=ASSISTS_ON)
    save(duel_mission(), meta("m1", STARTED_AT))
    assert assists_board()[(3, 2)] == (0, 0, 1)

    rebuild_aggregates()  # the default rules: off again, the assist-only rows go
    assert (3, 2) not in assists_board()
    assert not SiteSettings.objects.get(pk=1).killboard_assists
    save(duel_mission(), meta("m1", STARTED_AT))  # a re-ingest follows the stored setting
    assert (3, 2) not in assists_board()


def test_assists_incremental_equals_rebuild_and_count_per_tour() -> None:
    rebuild_aggregates(board=ASSISTS_ON)
    save(duel_mission(), meta("m1", STARTED_AT))
    save(duel_mission(), meta("m2", OCTOBER))
    incremental = snapshot()

    rebuild_aggregates(board=ASSISTS_ON)

    assert snapshot() == incremental
    assert PlayerTourKillboard.objects.filter(assists__gt=0).count() == 2  # one per tour


# --- per tour ---
def seed_tours() -> None:
    save(duel_mission(), meta("m1", STARTED_AT))  # September: 1 vs 2 is 2:1
    save(
        mission(
            (sortie(0, 2, coalition=2), sortie(1, 1, coalition=1, is_death=True, outcome="shot_down")),
            (kill(100, 0, 1),),
        ),
        meta("m2", OCTOBER),
    )


def test_killboard_is_kept_per_tour_and_all_time() -> None:
    seed_tours()

    assert tour_board("September 2026") == {(1, 2): (2, 1), (2, 1): (1, 2)}
    assert tour_board("October 2026") == {(1, 2): (0, 1), (2, 1): (1, 0)}
    assert board() == {(1, 2): (2, 2), (2, 1): (2, 2)}  # all-time is the sum
    october = PlayerTourKillboard.objects.get(tour=tour_named("October 2026"), player=pk(1), opponent=pk(2))
    assert october.last_mission.mission_uid == "m2"


def test_tour_rows_incremental_equals_rebuild_and_follow_a_reingest() -> None:
    seed_tours()
    incremental = snapshot()

    rebuild_aggregates()

    assert snapshot() == incremental
    save(replace(duel_mission(), kills=()), meta("m1", STARTED_AT))  # the September kills vanish on a re-ingest
    assert tour_board("September 2026") == {}
    assert tour_board("October 2026") == {(1, 2): (0, 1), (2, 1): (1, 0)}


def test_recomputing_one_tour_leaves_the_others_alone() -> None:
    seed_tours()
    PlayerTourKillboard.objects.filter(tour=tour_named("September 2026")).update(kills=99)

    recompute_players(Player.objects.values_list("pk", flat=True), [tour_named("October 2026").pk])

    assert PlayerTourKillboard.objects.filter(tour=tour_named("September 2026"), kills=99).exists()
    recompute_players(Player.objects.values_list("pk", flat=True))
    assert not PlayerTourKillboard.objects.filter(kills=99).exists()


# --- best streaks ---
def test_best_streaks_by_sorties_air_kills_and_flight_time() -> None:
    # Player 1: a 3-sortie run (1 air kill), a death, a 2-sortie run with 5 air kills, a death, then one long flight.
    save(
        mission(
            (
                sortie(0, 1, kills_air=1, flight_time_s=600.0),
                sortie(1, 1, flight_time_s=600.0),
                sortie(2, 1, flight_time_s=600.0),
                sortie(3, 1, is_death=True, outcome="shot_down", flight_time_s=60.0),
                sortie(4, 1, kills_air=2, flight_time_s=600.0),
                sortie(5, 1, kills_air=3, flight_time_s=600.0),
                sortie(6, 1, is_death=True, outcome="shot_down", flight_time_s=60.0),
                sortie(7, 1, flight_time_s=7200.0),
            )
        ),
        meta("m1", STARTED_AT),
    )

    assert streak_rows(1) == {
        "sorties": (3, 1, 1800.0),
        "air_kills": (2, 5, 1200.0),
        "flight_time": (1, 0, 7200.0),
    }
    assert streak_rows(1, "September 2026") == streak_rows(1)  # one tour: the same runs
    assert PlayerStreak.objects.get(player=pk(1)).best_sorties == 3


def test_no_air_kills_means_no_air_kills_row_and_no_survival_means_no_rows() -> None:
    save(mission((sortie(0, 1), sortie(1, 2, is_death=True, outcome="shot_down"))), meta("m1", STARTED_AT))

    assert set(streak_rows(1)) == {"sorties", "flight_time"}
    assert streak_rows(2) == {}


def test_a_streak_does_not_span_tours_all_time_is_the_best_of_the_tours() -> None:
    save(mission((sortie(0, 1, kills_air=1), sortie(1, 1, kills_air=1))), meta("m1", STARTED_AT))
    save(mission((sortie(0, 1, kills_air=1),)), meta("m2", OCTOBER))

    assert streak_rows(1)["sorties"][0] == 2  # all time: the best of the tours, never 3 across the boundary
    assert streak_rows(1, "September 2026")["sorties"][0] == 2
    assert streak_rows(1, "October 2026")["sorties"][0] == 1


def test_best_streak_rows_incremental_equals_rebuild() -> None:
    save(
        mission((sortie(0, 1, kills_air=2), sortie(1, 1), sortie(2, 3, is_death=True, outcome="shot_down"))),
        meta("m1", STARTED_AT),
    )
    save(mission((sortie(0, 1, kills_air=1), sortie(1, 3))), meta("m2", OCTOBER))
    incremental = snapshot()
    assert PlayerBestStreak.objects.exists()

    rebuild_aggregates()

    assert snapshot() == incremental


# --- pages ---
def test_killboard_page_shows_assists_only_when_counted(client: Client) -> None:
    save(duel_mission(), meta("m1", STARTED_AT))
    url = f"/players/{pk(3)}/killboard/"

    html = client.get(url).content.decode()
    assert "Assists are not counted." in html
    assert "sort=-assists" not in html
    rebuild_aggregates(board=ASSISTS_ON)

    html = client.get(url).content.decode()
    assert "sort=-assists" in html
    assert "Player-2" in html  # the pair with only an assist is listed
    assert [r.assists for r in client.get(f"{url}?sort=-assists").context["page_obj"]] == [1]


def test_profile_shows_killboard_and_streaks_of_the_selected_tour(client: Client) -> None:
    seed_tours()
    october = tour_named("October 2026")

    html = client.get(f"/players/{pk(1)}/?tour={october.pk}").content.decode()

    assert "Shot down by most" in html
    assert f"/players/{pk(1)}/killboard/?tour={october.pk}" in html
    two = client.get(f"/players/{pk(2)}/?tour={october.pk}").content.decode()  # player 2 survived October
    assert f"/players/{pk(2)}/streaks/?tour={october.pk}" in two
    assert "Best streak" in two
    assert "Current streak" not in two  # a current streak is not per tour
    assert "Current streak" in client.get(f"/players/{pk(1)}/?tour=all").content.decode()
    nobody = client.get(f"/players/{pk(2)}/?tour={october.pk}").content.decode()
    assert "Never shot down by a player." in nobody  # player 2 flew in October and nobody shot them down


def test_killboard_page_follows_the_tour(client: Client) -> None:
    seed_tours()
    october = tour_named("October 2026")

    response = client.get(f"/players/{pk(1)}/killboard/?tour={october.pk}")

    assert response.context["tour"] == october
    assert [(r.kills, r.deaths) for r in response.context["page_obj"]] == [(0, 1)]
    all_time = client.get(f"/players/{pk(1)}/killboard/?tour=all").context["page_obj"]
    assert [(r.kills, r.deaths) for r in all_time] == [(2, 2)]
    assert client.get(f"/players/{pk(1)}/killboard/").context["tour"] == october  # no ?tour: the current (newest) tour
    assert client.get(f"/players/{pk(1)}/killboard/?tour=999999").context["tour"] == october  # unknown: current tour


def test_best_streaks_page(client: Client) -> None:
    save(
        mission(
            (
                sortie(0, 1, kills_air=2),
                sortie(1, 1, is_death=True, outcome="shot_down"),
                sortie(2, 1, flight_time_s=7200.0),
                sortie(3, 2, is_death=True, outcome="shot_down"),
            )
        ),
        meta("m1", STARTED_AT),
    )
    september = tour_named("September 2026")

    response = client.get(f"/players/{pk(1)}/streaks/")

    assert response.status_code == 200
    assert [r.kind for r in response.context["streaks"]] == ["sorties", "air_kills", "flight_time"]
    html = response.content.decode()
    assert "Best streaks of Player-1" in html
    assert "Sorties survived" in html
    in_tour = client.get(f"/players/{pk(1)}/streaks/?tour={september.pk}")
    assert in_tour.context["tour"] == september
    assert len(in_tour.context["streaks"]) == 3
    assert "No streak yet" in client.get(f"/players/{pk(2)}/streaks/").content.decode()


def test_best_streaks_page_of_a_hidden_or_unknown_player_is_404(client: Client) -> None:
    save(mission((sortie(0, 1),)), meta("m1", STARTED_AT))
    Player.objects.filter(pk=pk(1)).update(is_hidden=True)

    assert client.get(f"/players/{pk(1)}/streaks/").status_code == 404
    assert client.get("/players/999999/streaks/").status_code == 404


def test_tour_budgets(client: Client) -> None:
    seed_tours()
    october = tour_named("October 2026").pk

    assert_simple_reads(
        client, f"/players/{pk(1)}/?tour={october}", max_queries=PROFILE_READS_TOUR
    )  # the tour adds its PlayerTour row
    # context processor 2, player, tours, count, rows
    assert_simple_reads(client, f"/players/{pk(1)}/killboard/?tour={october}", max_queries=8)
    # context processor 2, player, tours, best streaks
    assert_simple_reads(client, f"/players/{pk(1)}/streaks/", max_queries=5)
    assert_simple_reads(client, f"/players/{pk(1)}/streaks/?tour={october}", max_queries=5)


@pytest.mark.parametrize("sort", ["opponent", "-opponent"])
def test_killboard_opponent_sort_puts_hidden_players_last(client: Client, sort: str) -> None:
    """FR-ADM-3: a hidden opponent's place in the order must not hint at their name."""
    save(
        mission(
            (
                sortie(0, 1),
                sortie(1, 2, is_death=True, outcome="shot_down"),
                sortie(2, 3, is_death=True, outcome="shot_down"),
            ),
            (kill(100, 0, 1), kill(200, 0, 2)),
        ),
        meta("m1", STARTED_AT),
    )
    Player.objects.filter(pk=pk(2)).update(is_hidden=True)  # "Player-2" sorts between 1 and 3 by name

    rows = client.get(f"/players/{pk(1)}/killboard/?sort={sort}").context["page_obj"]

    assert [r.opponent_id for r in rows] == [pk(3), pk(2)]


def test_profile_links_keep_the_all_time_view(client: Client) -> None:
    """Without a tour parameter a link means the current tour, so every all-time link must say `?tour=all`."""
    seed_tours()
    october = tour_named("October 2026")

    html = client.get(f"/players/{pk(1)}/?tour=all").content.decode()
    assert f"/players/{pk(1)}/killboard/?tour=all" in html
    assert f"/players/{pk(2)}/streaks/?tour=all" in client.get(f"/players/{pk(2)}/?tour=all").content.decode()
    streaks = client.get(f"/players/{pk(1)}/streaks/?tour=all")
    assert f'href="/players/{pk(1)}/?tour=all"' in streaks.content.decode()
    assert f"/players/{pk(1)}/?tour=all" in [url for _label, url in streaks.context["crumbs"] if url]
    killboard = client.get(f"/players/{pk(1)}/killboard/?tour=all")
    assert f"/players/{pk(1)}/?tour=all" in [url for _label, url in killboard.context["crumbs"] if url]
    in_tour = client.get(f"/players/{pk(1)}/killboard/?tour={october.pk}")
    assert f"/players/{pk(1)}/?tour={october.pk}" in [url for _label, url in in_tour.context["crumbs"] if url]
    sorties = client.get(f"/players/{pk(1)}/sorties/?tour=all")
    assert f"/players/{pk(1)}/?tour=all" in [url for _label, url in sorties.context["crumbs"] if url]
