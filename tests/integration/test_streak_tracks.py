"""The two ironman tracks (maintainer, 2026-10-05; doc 13): an air run and a ground run per pilot. An attack sortie is
the ground track's, every other sortie the air track's; a death or capture ends only its own track's run. Ingest
rules, tours, incremental == rebuild, the ironman boards, the player list's streak columns, the profile block and the
history."""

import re

import pytest
from django.test import Client

from il2ks.core.replay.result import SortieResult
from il2ks.db.models import (
    CombatRole,
    Player,
    PlayerBestStreak,
    PlayerStreak,
    PlayerStreakRun,
    StreakKind,
    StreakTrack,
)
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.web import columns
from tests.factories import STARTED_AT, meta, mission, save, sortie
from tests.integration.test_killboard_streaks import pk, snapshot
from tests.integration.test_killboard_tours import OCTOBER, tour_named
from tests.simple_reads import assert_simple_reads

pytestmark = pytest.mark.django_db

ATTACK = CombatRole.ATTACK
AIR = CombatRole.AIR_SUPERIORITY


def air(index: int, player: int = 1, **fields: object) -> SortieResult:
    return sortie(index, player, combat_role=AIR, **fields)  # pyright: ignore[reportArgumentType]


def attack(index: int, player: int = 1, **fields: object) -> SortieResult:
    return sortie(index, player, combat_role=ATTACK, **fields)  # pyright: ignore[reportArgumentType]


def died_air(index: int, player: int = 1) -> SortieResult:
    return air(index, player, is_death=True, outcome="shot_down")


def died_attack(index: int, player: int = 1) -> SortieResult:
    return attack(index, player, is_death=True, outcome="shot_down")


def current(player: int, track: str) -> tuple[int, int, int]:
    row = PlayerStreak.objects.filter(player=pk(player), track=track).first()
    if row is None:  # no survived sortie on the track: no row
        return (0, 0, 0)
    return (row.current_sorties, row.current_kills_air, row.current_kills_ground)


def best(player: int, track: str, tour: str | None = None, kind: str = StreakKind.SORTIES) -> tuple[int, int, int]:
    row = PlayerBestStreak.objects.get(
        player=pk(player), track=track, kind=kind, tour=tour_named(tour) if tour else None
    )
    return (row.sorties, row.kills_air, row.kills_ground)


# --- the rule: a loss ends only its own track ---
def test_an_attack_sortie_death_does_not_end_the_air_run() -> None:
    save(mission((air(0), air(1, kills_air=2), attack(2), died_attack(3), air(4))), meta("m1", STARTED_AT))

    assert current(1, StreakTrack.AIR) == (3, 2, 0)  # the ground death left the air run alone
    assert current(1, StreakTrack.GROUND) == (0, 0, 0)
    assert best(1, StreakTrack.GROUND) == (1, 0, 0)  # the one attack sortie it survived before dying


def test_an_air_sortie_death_does_not_end_the_ground_run() -> None:
    save(
        mission((attack(0, kills_ground=3), attack(1), died_air(2), attack(3, kills_ground=1))),
        meta("m1", STARTED_AT),
    )

    assert current(1, StreakTrack.GROUND) == (3, 0, 4)
    assert current(1, StreakTrack.AIR) == (0, 0, 0)


def test_a_sortie_without_a_combat_role_counts_for_the_air_track() -> None:
    save(mission((sortie(0, 1), sortie(1, 1, is_death=True, outcome="shot_down"), attack(2))), meta("m1", STARTED_AT))

    assert not PlayerStreak.objects.filter(player=pk(1), track=StreakTrack.AIR, current_sorties__gt=0).exists()
    assert current(1, StreakTrack.GROUND) == (1, 0, 0)
    assert best(1, StreakTrack.AIR) == (1, 0, 0)


def test_the_ground_track_ranks_its_kills_by_ground_kills() -> None:
    save(
        mission(
            (
                attack(0, kills_ground=2, kills_air=1),
                died_attack(1),
                attack(2, kills_ground=5),
                died_attack(3),
                air(4, kills_air=9),
            )
        ),
        meta("m1", STARTED_AT),
    )

    assert best(1, StreakTrack.GROUND, kind=StreakKind.GROUND_KILLS) == (1, 0, 5)
    assert not PlayerBestStreak.objects.filter(player=pk(1), track=StreakTrack.GROUND, kind="air_kills").exists()
    assert best(1, StreakTrack.AIR, kind=StreakKind.AIR_KILLS) == (1, 9, 0)
    player = Player.objects.get(pk=pk(1))
    assert (player.streak_kills_air, player.streak_kills_ground) == (9, 5)


def test_runs_are_stored_per_track() -> None:
    save(mission((air(0), air(1), attack(2), attack(3), died_attack(4), air(5))), meta("m1", STARTED_AT))

    rows = PlayerStreakRun.objects.filter(player=pk(1), tour=None).order_by("track")
    assert [(r.track, r.sorties, r.ended_by) for r in rows] == [("air", 3, "open"), ("ground", 2, "death")]


# --- tours: per tour, and all time = max over the tours ---
def test_per_tour_and_all_time_best_are_kept_per_track() -> None:
    # September: air 3, ground 2; October: air 1, ground 4
    save(mission((air(0), air(1), air(2), attack(3), attack(4))), meta("m1", STARTED_AT))
    save(mission((air(0), attack(1), attack(2), attack(3), attack(4))), meta("m2", OCTOBER))

    assert best(1, StreakTrack.AIR, "September 2026") == (3, 0, 0)
    assert best(1, StreakTrack.AIR, "October 2026") == (1, 0, 0)
    assert best(1, StreakTrack.AIR) == (3, 0, 0)  # all time: the better tour
    assert best(1, StreakTrack.GROUND, "September 2026") == (2, 0, 0)
    assert best(1, StreakTrack.GROUND) == (4, 0, 0)
    assert current(1, StreakTrack.GROUND) == (4, 0, 0)  # the current tour's run
    assert current(1, StreakTrack.AIR) == (1, 0, 0)


def test_a_run_of_a_track_does_not_cross_the_tour_boundary() -> None:
    save(mission((attack(0), attack(1))), meta("m1", STARTED_AT))
    save(mission((attack(0), attack(1))), meta("m2", OCTOBER))

    assert best(1, StreakTrack.GROUND) == (2, 0, 0)  # not 4


# --- incremental == rebuild ---
def test_tracks_incremental_equals_rebuild() -> None:
    save(
        mission((air(0, kills_air=1), attack(1, kills_ground=2), died_attack(2), air(3), attack(4))),
        meta("m1", STARTED_AT),
    )
    save(mission((attack(0, kills_ground=1), died_air(1), air(2, kills_air=3), attack(3))), meta("m2", OCTOBER))
    save(mission((attack(0, 2, kills_ground=4), air(1, 2), attack(2, 2), died_attack(3, 2))), meta("m3", OCTOBER))
    incremental = snapshot()
    assert PlayerBestStreak.objects.filter(track=StreakTrack.GROUND).exists()

    rebuild_aggregates()

    assert snapshot() == incremental


def test_reprocessing_a_mission_moves_a_sortie_to_the_other_track() -> None:
    save(mission((air(0), air(1), air(2))), meta("m1", STARTED_AT))
    save(mission((air(0), attack(1), air(2))), meta("m1", STARTED_AT))  # same mission: sortie 1 is an attack now

    assert best(1, StreakTrack.AIR) == (2, 0, 0)
    assert best(1, StreakTrack.GROUND) == (1, 0, 0)


# --- the ironman boards ---
def seed_boards() -> None:
    save(
        mission(
            (
                air(0, 1, kills_air=2),
                air(1, 1),
                air(2, 1),  # player 1: air run of 3, 2 air kills
                attack(3, 1, kills_ground=7),  # ground run of 1, 7 ground kills
                air(4, 2, kills_air=5),
                air(5, 2),  # player 2: air run of 2, 5 air kills
                attack(6, 2, kills_ground=1),
                attack(7, 2),
                attack(8, 2),
                attack(9, 2, kills_ground=1),  # ground run of 4
                air(10, 3),  # player 3: air run of 1
            )
        ),
        meta("m1", STARTED_AT),
    )


def board_rows(client: Client, path: str) -> list[tuple[int, int]]:
    response = client.get(path)
    assert response.status_code == 200
    return [(r.player.pk, r.stats.sorties) for r in response.context["page_obj"]]


def test_the_air_ironman_board_lists_the_best_air_runs_longest_first(client: Client) -> None:
    seed_boards()

    assert board_rows(client, "/leaderboards/ironman-air/?tour=all") == [(pk(1), 3), (pk(2), 2), (pk(3), 1)]


def test_the_ground_ironman_board_lists_the_best_ground_runs(client: Client) -> None:
    seed_boards()

    assert board_rows(client, "/leaderboards/ironman-ground/?tour=all") == [(pk(2), 4), (pk(1), 1)]


def test_the_board_sorts_by_the_tracks_kills_and_never_by_an_unknown_column(client: Client) -> None:
    seed_boards()

    by_kills = client.get("/leaderboards/ironman-air/?tour=all&sort=-kills").context["page_obj"]
    ground = client.get("/leaderboards/ironman-ground/?tour=all&sort=-kills").context["page_obj"]

    assert [r.player.pk for r in by_kills][:2] == [pk(2), pk(1)]  # 5 air kills before 2
    assert [r.player.pk for r in ground] == [pk(1), pk(2)]  # 7 ground kills before 2
    assert client.get("/leaderboards/ironman-air/?sort=bogus").context["sort"] == "-sorties"


def test_the_default_columns_show_the_tracks_kills_and_the_extra_columns_the_other_ones(client: Client) -> None:
    seed_boards()

    plain = client.get("/leaderboards/ironman-ground/?tour=all").content.decode()
    extra = client.get("/leaderboards/ironman-ground/?tour=all&cols=kills_air,since").content.decode()
    air_board = client.get("/leaderboards/ironman-air/?tour=all").content.decode()

    assert "Ground kills" in plain
    assert re.search(r"<th[^>]*>.*?sort=-?kills_air", plain, re.S) is None  # not shown by default
    assert re.search(r'value="kills_air"', plain)  # but offered in the picker
    assert re.search(r"<th[^>]*>\s*<a href=\"[^\"]*sort=-?kills_air", extra)
    assert re.search(r"<th[^>]*>\s*<a href=\"[^\"]*sort=-?since", extra)
    assert "Air kills" in air_board
    assert 'value="kills_ground"' in air_board
    assert [c.key for c in columns.IRONMAN_COLUMNS["air"]] == ["kills_ground", "since", "until"]


def test_the_boards_are_tour_aware_and_running_streaks_only_show_in_the_current_tour(client: Client) -> None:
    save(mission((air(0, 1), air(1, 1), attack(2, 2), attack(3, 2), attack(4, 2))), meta("m1", STARTED_AT))
    save(mission((air(0, 1), attack(1, 2))), meta("m2", OCTOBER))
    september = tour_named("September 2026")

    current_tour = client.get("/leaderboards/ironman-air/")
    past = client.get(f"/leaderboards/ironman-air/?tour={september.pk}")

    assert [(r.player.pk, r.stats.sorties) for r in current_tour.context["page_obj"]] == [(pk(1), 1)]
    assert [(r.player.pk, r.stats.sorties) for r in past.context["page_obj"]] == [(pk(1), 2)]
    assert current_tour.context["running_page"] is not None
    assert past.context["running_page"] is None
    assert board_rows(client, f"/leaderboards/ironman-ground/?tour={september.pk}") == [(pk(2), 3)]


def test_hidden_players_are_not_on_the_ironman_boards(client: Client) -> None:
    seed_boards()
    Player.objects.filter(pk=pk(2)).update(is_hidden=True)

    assert [pk_ for pk_, _ in board_rows(client, "/leaderboards/ironman-ground/?tour=all")] == [pk(1)]


def test_the_board_tabs_and_the_old_streak_urls(client: Client) -> None:
    seed_boards()

    html = client.get("/leaderboards/").content.decode()
    assert "/leaderboards/ironman-air/" in html
    assert "/leaderboards/ironman-ground/" in html
    old = client.get("/streaks/?tour=all")
    assert (old.status_code, old["Location"]) == (301, "/leaderboards/ironman-air/?tour=all")
    assert client.get("/streaks/")["Location"] == "/leaderboards/ironman-air/"
    assert client.get("/leaderboards/ironman/")["Location"] == "/leaderboards/ironman-air/"


def test_the_ironman_board_budget(client: Client) -> None:
    seed_boards()

    # context processor 2, tours, count + rows of the board, count + rows of the running list
    assert_simple_reads(client, "/leaderboards/ironman-air/?tour=all", max_queries=8)
    assert_simple_reads(client, "/leaderboards/ironman-ground/?tour=all&cols=kills_air,since,until", max_queries=8)


# --- the player list ---
def test_the_player_list_has_the_new_default_columns_and_both_streak_columns(client: Client) -> None:
    seed_boards()

    response = client.get("/players/")
    html = response.content.decode()

    for label in (
        "Flight time",
        "Elo",
        "Attack proficiency",
        "K/L",
        "Longest kill streak",
        "Longest ground kill streak",
    ):
        assert label in html
    for key in ("flight_time_s", "elo", "ground_hour", "kl", "streak_kills_air", "streak_kills_ground"):
        assert f"sort={key}" in html or f"sort=-{key}" in html
    assert "sort=sorties" not in html  # now an optional column
    assert "sort=deaths" not in html
    assert [c.key for c in columns.PLAYER_DEFAULT_COLUMNS] == [
        "flight_time_s",
        "elo",
        "ground_hour",
        "kl",
        "streak_kills_air",
        "streak_kills_ground",
    ]


def test_the_streak_columns_sort_the_player_list(client: Client) -> None:
    seed_boards()

    def order(sort: str) -> list[int]:
        return [h.player.pk for h in client.get("/players/", {"sort": sort}).context["page_obj"].object_list]

    assert order("-streak_kills_air")[:2] == [pk(2), pk(1)]  # 5 air kills in a run, 2
    assert order("-streak_kills_ground")[:2] == [pk(1), pk(2)]  # 7 ground kills in a run, 2
    assert order("streak_kills_ground")[0] == pk(3)  # none
    # the old default columns stay available
    assert [c.key for c in columns.PLAYER_COLUMNS][:5] == [
        "sorties",
        "kills_air",
        "kills_ground",
        "deaths",
        "last_seen",
    ]
    assert order("-elo")  # accepted


def test_the_best_elo_column_ignores_unrated_pools_and_sorts_last_without_games(client: Client) -> None:
    seed_boards()
    Player.objects.filter(pk=pk(1)).update(elo_jet=1600.0, elo_jet_games=3, elo_prop=1500.0, elo_prop_games=0)
    Player.objects.filter(pk=pk(2)).update(elo_prop=1550.0, elo_prop_games=2)

    ordered = [h.player.pk for h in client.get("/players/", {"sort": "-elo"}).context["page_obj"].object_list]

    assert ordered[:2] == [pk(1), pk(2)]
    assert ordered[-1] == pk(3)  # no rated game: undefined, last
    html = client.get("/players/").content.decode()
    assert ">1,600<" in html
    assert ">1,500<" not in html  # an unrated 1500 is not shown


def test_the_player_list_budget_is_unchanged(client: Client) -> None:
    seed_boards()

    # context processor 2, count, rows: the two streak columns are copies on the Player row (no join)
    assert_simple_reads(client, "/players/", max_queries=4)
    assert_simple_reads(client, "/players/?q=pilot&sort=-streak_kills_air&cols=sorties,kd", max_queries=4)


# --- the profile and the history ---
def test_the_profile_block_shows_both_tracks(client: Client) -> None:
    seed_boards()

    html = client.get(f"/players/{pk(1)}/?tour=all").content.decode()

    assert "Current air streak" in html
    assert "Current ground streak" in html
    assert "Best air streak" in html
    assert "Best ground streak" in html
    tour = tour_named("September 2026")
    in_tour = client.get(f"/players/{pk(1)}/?tour={tour.pk}").content.decode()
    assert "Best air streak" in in_tour
    assert "Best ground streak" in in_tour


def test_the_best_streaks_page_lists_both_tracks_and_the_history_has_a_track_switch(client: Client) -> None:
    seed_boards()

    best_page = client.get(f"/players/{pk(1)}/streaks/?tour=all")
    assert [(r.track, r.kind) for r in best_page.context["streaks"]][:1] == [("air", "sorties")]
    assert {r.track for r in best_page.context["streaks"]} == {"air", "ground"}

    air_history = client.get(f"/players/{pk(1)}/streaks/history/?tour=all")
    ground_history = client.get(f"/players/{pk(1)}/streaks/history/?tour=all&track=ground")
    bogus = client.get(f"/players/{pk(1)}/streaks/history/?tour=all&track=bogus")

    assert air_history.context["track"] == "air"
    assert ground_history.context["track"] == "ground"
    assert bogus.context["track"] == "air"
    assert [r.sorties for r in air_history.context["page_obj"]] == [3]
    assert list(ground_history.context["page_obj"]) == []  # a single attack sortie is not a listed run
    assert "track=ground" in air_history.content.decode()


def test_a_ground_run_is_listed_in_the_ground_history(client: Client) -> None:
    seed_boards()

    rows = client.get(f"/players/{pk(2)}/streaks/history/?tour=all&track=ground").context["page_obj"]

    assert [(r.sorties, r.kills_ground) for r in rows] == [(4, 2)]
