"""Killboard (FR-WEB-9) and ironman streaks (FR-WEB-23): ingest rules, incremental == rebuild, pages, hiding."""

from dataclasses import replace
from datetime import timedelta

import pytest
from django.test import Client

from il2ks.core.replay.result import MissionResult
from il2ks.db.models import Mission, Player, PlayerKillboard, PlayerStreak
from il2ks.ingest.aggregates import rebuild_aggregates, recompute_players
from tests.factories import STARTED_AT, account, kill, meta, mission, save, sortie
from tests.simple_reads import assert_simple_reads

pytestmark = pytest.mark.django_db

DAY2 = STARTED_AT + timedelta(days=1)
DAY3 = STARTED_AT + timedelta(days=2)


def pk(n: int) -> int:
    return Player.objects.get(account_uuid=account(n)).pk


def board() -> dict[tuple[int, int], tuple[int, int]]:
    """(player number, opponent number) -> (kills, deaths)."""
    numbers = {p.pk: int(p.account_uuid[-12:]) for p in Player.objects.all()}
    return {(numbers[r.player_id], numbers[r.opponent_id]): (r.kills, r.deaths) for r in PlayerKillboard.objects.all()}


def snapshot() -> list[tuple[object, ...]]:
    kb = PlayerKillboard.objects.order_by("player_id", "opponent_id").values_list(
        "player_id", "opponent_id", "kills", "deaths", "last_at", "last_mission_id"
    )
    st = PlayerStreak.objects.order_by("player_id").values_list(
        "player_id",
        "current_sorties",
        "current_kills_air",
        "current_flight_time_s",
        "current_since",
        "current_until",
        "best_sorties",
        "best_kills_air",
        "best_flight_time_s",
        "best_since",
        "best_until",
    )
    return [*kb, *st]


def duel_mission() -> MissionResult:
    """1 shoots 2 down twice (two sorties of 2), 2 shoots 1 down once; the rest must not count."""
    return mission(
        (
            sortie(0, 1, coalition=1),
            sortie(1, 2, coalition=2, aircraft_type="F-86A-5", outcome="shot_down", is_death=True, is_plane_lost=True),
            sortie(2, 2, coalition=2, aircraft_type="F-86A-5", outcome="shot_down", is_death=True, is_plane_lost=True),
            sortie(3, 1, coalition=1, outcome="shot_down", is_death=True, is_plane_lost=True),
            sortie(4, 3, coalition=1),
            sortie(5, 8, coalition=1, role="gunner", aircraft_type="Turret_IL10"),
        ),
        (
            kill(100, 0, 1),
            kill(200, 0, 2),
            kill(300, 1, 3),
            kill(400, 4, 1, credit="assist"),  # assists are not counted
            kill(500, 4, 3, is_friendly=True),  # friendly fire is not counted
            kill(600, 5, 2),  # a gunner's kill is not counted (counters are pilot-only, like Elo)
            kill(700, 0, 3),  # player 1 killing their own earlier sortie: not a pair
        ),
    )


def test_pairs_count_kill_credits_only_in_both_directions() -> None:
    save(duel_mission(), meta("m1", STARTED_AT))

    assert board() == {(1, 2): (2, 1), (2, 1): (1, 2)}
    row = PlayerKillboard.objects.get(player=pk(1), opponent=pk(2))
    assert row.last_mission == Mission.objects.get(mission_uid="m1")


def test_last_encounter_is_the_latest_kill_in_either_direction() -> None:
    save(duel_mission(), meta("m1", STARTED_AT))
    save(
        mission(
            (sortie(0, 2, coalition=2), sortie(1, 1, coalition=1, is_death=True, outcome="shot_down")),
            (kill(100, 0, 1),),
        ),
        meta("m2", DAY2),
    )

    row = PlayerKillboard.objects.get(player=pk(1), opponent=pk(2))
    assert (row.kills, row.deaths) == (2, 2)
    assert row.last_mission.mission_uid == "m2"
    assert PlayerKillboard.objects.get(player=pk(2), opponent=pk(1)).last_mission.mission_uid == "m2"


def test_reprocessing_a_mission_without_the_kills_removes_the_rows() -> None:
    result = duel_mission()
    save(result, meta("m1", STARTED_AT))
    save(replace(result, kills=()), meta("m1", STARTED_AT))

    assert board() == {}


def test_incremental_equals_rebuild() -> None:
    save(duel_mission(), meta("m1", STARTED_AT))
    save(
        mission(
            (sortie(0, 2, coalition=2, kills_air=1), sortie(1, 4, coalition=1, is_death=True, outcome="shot_down")),
            (kill(100, 0, 1),),
        ),
        meta("m2", DAY2),
    )
    save(mission((sortie(0, 3, coalition=1), sortie(1, 1, coalition=1, outcome="not_taken_off"))), meta("m3", DAY3))
    incremental = snapshot()
    assert incremental

    rebuild_aggregates()

    assert snapshot() == incremental
    recompute_players(Player.objects.values_list("pk", flat=True))  # and idempotent
    assert snapshot() == incremental


def test_recomputing_one_player_fixes_the_mirror_row_of_an_unlisted_opponent() -> None:
    save(duel_mission(), meta("m1", STARTED_AT))
    PlayerKillboard.objects.filter(player=pk(2)).update(kills=99)  # corrupt the opponent's mirror row

    recompute_players([pk(1)])

    assert board() == {(1, 2): (2, 1), (2, 1): (1, 2)}


# --- streaks --------------------------------------------------------------------------------------------------------
def test_streak_rules_over_missions() -> None:
    save(mission((sortie(0, 1, kills_air=2), sortie(1, 1, kills_air=1, flight_time_s=300.0))), meta("m1", STARTED_AT))
    save(
        mission((sortie(0, 1, outcome="shot_down", is_death=True, is_plane_lost=True, kills_air=9),)), meta("m2", DAY2)
    )
    save(
        mission((sortie(0, 1, outcome="not_taken_off", flight_time_s=0.0), sortie(1, 1, kills_air=4))),
        meta("m3", DAY3),
    )

    streak = PlayerStreak.objects.get(player=pk(1))
    assert (streak.best_sorties, streak.best_kills_air, streak.best_flight_time_s) == (2, 3, 900.0)
    assert (streak.current_sorties, streak.current_kills_air) == (1, 4)  # the grounded sortie was skipped
    assert streak.best_since is not None
    assert streak.best_since.date() == STARTED_AT.date()


def test_capture_and_mission_end_cut_off() -> None:
    captured = replace(sortie(0, 1), is_captured=True, pilot_status="captured")
    save(
        mission((sortie(0, 2), sortie(1, 2, ended_by_mission_end=True), sortie(2, 2, outcome="ditched"))),
        meta("m1", STARTED_AT),
    )
    save(mission((captured,)), meta("m2", DAY2))

    assert PlayerStreak.objects.get(player=pk(2)).current_sorties == 3  # cut off and ditched but alive
    assert not PlayerStreak.objects.filter(player=pk(1)).exists()  # captured: no survived sortie at all


def test_gunner_sorties_do_not_count() -> None:
    save(mission((sortie(0, 5, role="gunner", aircraft_type="Turret_IL10"),)))

    assert not PlayerStreak.objects.exists()


# --- pages ----------------------------------------------------------------------------------------------------------
def seed_pages() -> None:
    save(duel_mission(), meta("m1", STARTED_AT))
    save(mission((sortie(0, 1), sortie(1, 3, coalition=1))), meta("m2", DAY2))


def test_profile_shows_killboard_and_streak(client: Client) -> None:
    seed_pages()

    html = client.get(f"/players/{pk(1)}/").content.decode()

    assert "Shot down most" in html
    assert "Player-2" in html
    assert f"/players/{pk(1)}/killboard/" in html
    assert "Current streak" in html


def test_profile_budget_and_killboard_page(client: Client) -> None:
    seed_pages()

    assert_simple_reads(client, f"/players/{pk(1)}/", max_queries=11)  # + tours selector, stat thresholds
    # context processor 2, player, count, rows
    assert_simple_reads(client, f"/players/{pk(1)}/killboard/", max_queries=6)
    assert_simple_reads(client, f"/players/{pk(1)}/killboard/?sort=-last", max_queries=6)
    assert_simple_reads(client, "/streaks/", max_queries=6)


def test_killboard_sort_is_whitelisted_and_ordered(client: Client) -> None:
    seed_pages()

    response = client.get(f"/players/{pk(1)}/killboard/?sort=bogus;drop")

    assert response.status_code == 200
    assert response.context["sort"] == "-kills"
    assert [r.kills for r in response.context["page_obj"]] == [2]


def test_hidden_opponent_is_anonymised_but_counted(client: Client) -> None:
    seed_pages()
    Player.objects.filter(pk=pk(2)).update(is_hidden=True)

    html = client.get(f"/players/{pk(1)}/killboard/").content.decode()

    assert "Hidden player" in html
    assert "Player-2" not in html
    assert f"/players/{pk(2)}/" not in html
    assert board()[(1, 2)] == (2, 1)  # counts are not recomputed (FR-ADM-3)
    assert client.get(f"/players/{pk(2)}/killboard/").status_code == 404
    assert "Player-2" not in client.get(f"/players/{pk(1)}/").content.decode()


def test_hidden_mission_is_counted_but_not_linked(client: Client) -> None:
    seed_pages()
    mission_pk = Mission.objects.get(mission_uid="m1").pk

    assert f"/missions/{mission_pk}/" in client.get(f"/players/{pk(1)}/killboard/").content.decode()
    Mission.objects.filter(pk=mission_pk).update(is_hidden=True)
    html = client.get(f"/players/{pk(1)}/killboard/").content.decode()

    assert f"/missions/{mission_pk}/" not in html
    assert "2026-09-19" in html  # the date stays, the numbers include the hidden mission


def test_streak_list_and_home_block_skip_hidden_and_stale(client: Client) -> None:
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    save(mission((sortie(0, 1), sortie(1, 2), sortie(2, 3))), meta("now", now - timedelta(hours=1)))
    save(mission((sortie(0, 4),)), meta("old", now - timedelta(days=90)))
    Player.objects.filter(pk=pk(2)).update(is_hidden=True)

    for url in ("/streaks/", "/"):
        html = client.get(url).content.decode()
        assert "Player-1" in html
        assert "Player-3" in html
        assert "Player-2" not in html  # hidden
        assert "Player-4" not in html  # flew 90 days ago: not running
    assert [r.player_id for r in client.get("/streaks/").context["page_obj"]] == sorted([pk(1), pk(3)])
    assert client.get("/streaks/?sort=nonsense").status_code == 200
