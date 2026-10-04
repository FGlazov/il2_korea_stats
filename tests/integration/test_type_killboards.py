"""Killboard by aircraft type (player, FR-WEB-9) and the per-tour / intercept matchups of an aircraft type (FR-WEB-8).

Level-2 tables recomputed at ingest: incremental == rebuild, tour scoping, the intercept filter (both sorties air
superiority), hidden players (counted, never named), and the pages with their query budgets (TD-22)."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from django.db import models
from django.test import Client
from django.urls import reverse

from il2ks.core.replay.result import CombatRole, KillResult, MissionResult, SortieResult
from il2ks.db.models import AircraftMatchup, GameObject, Player, PlayerTypeKillboard, Tour
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.queries.aircraft import MIN_ENCOUNTERS, MatchupTable
from tests.factories import STARTED_AT, account, kill, meta, mission, save, sortie
from tests.integration.test_killboard_streaks import duel_mission
from tests.ops_helpers import make_instance
from tests.simple_reads import PROFILE_READS_ALL_TIME, assert_simple_reads

pytestmark = pytest.mark.django_db

OCTOBER = datetime(2026, 10, 5, 12, tzinfo=UTC)
AIR: CombatRole = "air_superiority"
ATTACK: CombatRole = "attack"
MIG = "MiG-15bis"
SABRE = "F-86A-5"


def pk(n: int) -> int:
    return Player.objects.get(account_uuid=account(n)).pk


def aircraft(log_name: str) -> GameObject:
    return GameObject.objects.get(log_name=log_name)


def type_rows(player: int, tour: Tour | None = None) -> dict[str, tuple[int, int, str | None, str | None]]:
    """enemy type -> (kills, deaths, kills_with, deaths_in) of the player's rows in that scope."""
    rows = PlayerTypeKillboard.objects.filter(player=pk(player), tour=tour).select_related(
        "enemy_aircraft", "kills_with", "deaths_in"
    )
    return {
        r.enemy_aircraft.log_name: (
            r.kills,
            r.deaths,
            r.kills_with.log_name if r.kills_with else None,
            r.deaths_in.log_name if r.deaths_in else None,
        )
        for r in rows
    }


def snapshot() -> dict[str, list[dict[str, object]]]:
    def rows(model: type[models.Model]) -> list[dict[str, object]]:
        found = list(model.objects.order_by("player_id" if model is PlayerTypeKillboard else "pk").values())
        for row in found:
            row.pop("id")
        return found

    return {"types": sorted(rows(PlayerTypeKillboard), key=repr), "matchups": sorted(rows(AircraftMatchup), key=repr)}


def duels(
    spec: list[tuple[str, int, int]], *, mig_role: CombatRole | None = AIR, enemy_role: CombatRole | None = AIR
) -> MissionResult:
    """Player 1 flies MiG-15bis against player 2 in each enemy type of `spec` = (type, MiG kills, MiG losses): every
    kill is a pair of fresh sorties, the victim dying."""
    sorties: list[SortieResult] = []
    kills: list[KillResult] = []
    tick = 100
    for enemy_type, wins, losses in spec:
        for _ in range(wins):
            killer, victim = len(sorties), len(sorties) + 1
            sorties.append(sortie(killer, 1, combat_role=mig_role))
            sorties.append(
                sortie(
                    victim,
                    2,
                    aircraft_type=enemy_type,
                    coalition=2,
                    combat_role=enemy_role,
                    is_death=True,
                    is_plane_lost=True,
                )
            )
            kills.append(kill(tick, killer, victim, victim_type=enemy_type))
            tick += 10
        for _ in range(losses):
            killer, victim = len(sorties), len(sorties) + 1
            sorties.append(sortie(killer, 2, aircraft_type=enemy_type, coalition=2, combat_role=enemy_role))
            sorties.append(sortie(victim, 1, combat_role=mig_role, is_death=True, is_plane_lost=True))
            kills.append(kill(tick, killer, victim, killer_type=enemy_type, victim_type=MIG))
            tick += 10
    return mission(tuple(sorties), tuple(kills))


# --- the player's killboard by aircraft type ---
def test_type_rows_count_both_directions_with_the_own_aircraft() -> None:
    save(duel_mission(), meta("m1", STARTED_AT))

    assert type_rows(1) == {SABRE: (2, 1, MIG, MIG)}  # shot the Sabre down twice; the Sabre shot player 1 down once
    assert type_rows(2) == {MIG: (1, 2, SABRE, SABRE)}
    assert type_rows(3) == {}  # an assist, friendly fire and a gunner's kill are not counted


def test_the_most_used_own_aircraft_wins_and_ties_go_to_the_lowest_id() -> None:
    save(
        mission(
            (
                sortie(0, 1, aircraft_type="MiG-15bis"),
                sortie(1, 1, aircraft_type="MiG-15bis"),
                sortie(2, 1, aircraft_type="La-9"),
                sortie(3, 2, coalition=2, aircraft_type=SABRE, is_death=True),
                sortie(4, 2, coalition=2, aircraft_type=SABRE, is_death=True),
                sortie(5, 2, coalition=2, aircraft_type=SABRE, is_death=True),
            ),
            (kill(100, 0, 3), kill(200, 1, 4), kill(300, 2, 5)),
        ),
        meta("m1", STARTED_AT),
    )

    assert type_rows(1) == {SABRE: (3, 0, MIG, None)}


def test_rows_are_kept_per_tour_and_all_time() -> None:
    save(duel_mission(), meta("m1", STARTED_AT))
    save(duels([(SABRE, 1, 0)]), meta("m2", OCTOBER))
    september, october = Tour.objects.get(title="September 2026"), Tour.objects.get(title="October 2026")

    assert type_rows(1, september) == {SABRE: (2, 1, MIG, MIG)}
    assert type_rows(1, october) == {SABRE: (1, 0, MIG, None)}
    assert type_rows(1) == {SABRE: (3, 1, MIG, MIG)}  # all time is the sum


def test_type_rows_incremental_equals_rebuild_and_follow_a_reingest() -> None:
    first = duel_mission()
    save(first, meta("m1", STARTED_AT))
    save(duels([(SABRE, 3, 2), ("F-51D", 1, 0)]), meta("m2", OCTOBER))
    save(replace(first, kills=first.kills[:1]), meta("m1", STARTED_AT))  # a re-ingest with fewer kills
    incremental = snapshot()
    assert incremental["types"]
    assert incremental["matchups"]

    rebuild_aggregates()

    assert snapshot() == incremental


def test_a_reingest_that_drops_every_kill_removes_the_rows() -> None:
    result = duel_mission()
    save(result, meta("m1", STARTED_AT))
    save(replace(result, kills=()), meta("m1", STARTED_AT))

    assert not PlayerTypeKillboard.objects.exists()
    assert not AircraftMatchup.objects.exists()


def test_hidden_players_count_in_the_type_rows() -> None:
    save(duel_mission(), meta("m1", STARTED_AT))
    Player.objects.filter(pk=pk(2)).update(is_hidden=True)
    rebuild_aggregates()

    assert type_rows(1) == {SABRE: (2, 1, MIG, MIG)}  # the hidden opponent's kills and deaths still count


# --- the player pages ---
def test_profile_and_full_killboard_show_the_types_and_keep_the_tour(client: Client) -> None:
    save(duel_mission(), meta("m1", STARTED_AT))
    save(duels([(SABRE, 1, 0)]), meta("m2", OCTOBER))
    october = Tour.objects.get(title="October 2026")
    profile = f"/players/{pk(1)}/"

    html = client.get(f"{profile}?tour=all").content.decode()
    assert "Killboard by aircraft" in html
    assert f"/players/{pk(1)}/killboard/?tour=all" in html
    assert html.index("Killboard by aircraft") < html.index("<h2>Killboard</h2>")  # before the player table
    assert (
        f"/players/{pk(1)}/killboard/?tour={october.pk}" in client.get(f"{profile}?tour={october.pk}").content.decode()
    )

    full = client.get(f"{profile}killboard/?tour=all").content.decode()
    assert full.index("Aircraft shot down most") < full.index("Opponent")  # above the player-versus-player table
    assert "By player" in full
    assert "F-86A Sabre" in full


def test_the_type_block_stays_out_without_encounters(client: Client) -> None:
    save(mission((sortie(0, 1),)), meta("m1", STARTED_AT))

    html = client.get(f"/players/{pk(1)}/?tour=all").content.decode()

    assert "Killboard by aircraft" not in html


def test_type_killboard_pages_stay_within_the_budget(client: Client) -> None:
    save(duel_mission(), meta("m1", STARTED_AT))

    assert_simple_reads(client, f"/players/{pk(1)}/?tour=all", max_queries=PROFILE_READS_ALL_TIME)
    assert_simple_reads(client, f"/players/{pk(1)}/killboard/?tour=all", max_queries=8)


# --- the matchups of an aircraft type ---
def matchup_table(client: Client, query: str = "") -> MatchupTable:
    response = client.get(reverse("web:aircraft-detail", args=[aircraft(MIG).pk]) + query)
    assert response.status_code == 200
    return response.context["matchups"]


def test_matchups_are_scoped_by_tour_and_intercept() -> None:
    save(duels([(SABRE, 2, 1)]), meta("m1", STARTED_AT))
    save(duels([(SABRE, 1, 0)], enemy_role=ATTACK), meta("m2", OCTOBER))  # an attack sortie is not an intercept
    september, october = Tour.objects.get(title="September 2026"), Tour.objects.get(title="October 2026")

    def kills(tour: Tour | None, intercept: bool) -> int:
        return sum(
            m.kills
            for m in AircraftMatchup.objects.filter(
                tour=tour, intercept=intercept, killer_aircraft=aircraft(MIG), victim_aircraft=aircraft(SABRE)
            )
        )

    assert (kills(None, False), kills(None, True)) == (3, 2)
    assert (kills(september, False), kills(september, True)) == (2, 2)
    assert (kills(october, False), kills(october, True)) == (1, 0)
    assert not AircraftMatchup.objects.filter(tour=october, intercept=True).exists()  # no row without such kills


def test_the_intercept_filter_needs_air_superiority_on_both_sides() -> None:
    save(duels([(SABRE, 1, 1)], mig_role=AIR, enemy_role=ATTACK), meta("m1", STARTED_AT))
    save(duels([(SABRE, 1, 1)], mig_role=ATTACK, enemy_role=AIR), meta("m2", STARTED_AT.replace(day=20)))
    save(duels([(SABRE, 1, 1)], mig_role=None, enemy_role=None), meta("m3", STARTED_AT.replace(day=21)))
    save(duels([(SABRE, 2, 3)]), meta("m4", STARTED_AT.replace(day=22)))

    both = AircraftMatchup.objects.filter(intercept=True, tour=None)
    assert {(m.killer_aircraft.log_name, m.victim_aircraft.log_name): m.kills for m in both} == {
        (MIG, SABRE): 2,
        (SABRE, MIG): 3,
    }
    everything = AircraftMatchup.objects.filter(intercept=False, tour=None)
    assert {(m.killer_aircraft.log_name, m.victim_aircraft.log_name): m.kills for m in everything} == {
        (MIG, SABRE): 5,
        (SABRE, MIG): 6,
    }


def test_matchup_rows_incremental_equals_rebuild_and_repair_drift() -> None:
    save(duels([(SABRE, 2, 1)]), meta("m1", STARTED_AT))
    save(duels([(SABRE, 1, 2)], enemy_role=ATTACK), meta("m2", OCTOBER))
    good = snapshot()
    AircraftMatchup.objects.filter(intercept=True).delete()
    AircraftMatchup.objects.filter(tour=None).update(kills=99)

    rebuild_aggregates()

    assert snapshot() == good


def test_hidden_players_count_in_the_matchups() -> None:
    save(duels([(SABRE, 2, 1)]), meta("m1", STARTED_AT))
    Player.objects.update(is_hidden=True)

    rebuild_aggregates()

    row = AircraftMatchup.objects.get(tour=None, intercept=False, killer_aircraft=aircraft(MIG))
    assert row.kills == 2


# --- the aircraft page ---
def seed_matchups() -> None:
    """September: MiG vs Sabre 6:4 (10 fights), vs F-51D 1:9 (10), vs Il-10 3:0 (3 fights, too few). October: MiG vs
    Sabre 1:0, of which the attack-role part is no intercept."""
    save(duels([(SABRE, 6, 4), ("F-51D", 1, 9), ("Il-10", 3, 0)]), meta("m1", STARTED_AT))
    save(duels([(SABRE, 1, 0)], enemy_role=ATTACK), meta("m2", OCTOBER))


def test_the_page_opens_on_the_current_tour_and_all_time_is_one_click_away(client: Client) -> None:
    seed_matchups()

    current = matchup_table(client)  # no ?tour: the newest tour, October
    assert [(m.enemy.log_name, m.kills, m.losses) for m in current.rows] == [(SABRE, 1, 0)]
    everything = matchup_table(client, "?tour=all")
    assert {m.enemy.log_name: (m.kills, m.losses) for m in everything.rows} == {
        SABRE: (7, 4),
        "F-51D": (1, 9),
        "Il-10": (3, 0),
    }
    september = Tour.objects.get(title="September 2026")
    assert len(matchup_table(client, f"?tour={september.pk}").rows) == 3


def test_ratio_needs_enough_fights_and_the_hint_names_best_and_worst(client: Client) -> None:
    seed_matchups()
    assert MIN_ENCOUNTERS == 10

    table = matchup_table(client, "?tour=all")
    rated = {m.enemy.log_name: m.rated for m in table.rows}
    assert rated == {SABRE: True, "F-51D": True, "Il-10": False}  # 11, 10 and 3 fights
    assert table.best is not None
    assert table.worst is not None
    assert (table.best.enemy.log_name, table.worst.enemy.log_name) == (SABRE, "F-51D")  # Il-10: too few to name

    body = client.get(reverse("web:aircraft-detail", args=[aircraft(MIG).pk]) + "?tour=all").content.decode()
    assert "Best exchange: against" in body
    assert "Worst exchange: against" in body
    assert "no losses" not in body  # the unrated Il-10 shows a dash, not "no losses"


def test_no_hint_with_a_single_rated_matchup(client: Client) -> None:
    save(duels([(SABRE, 6, 4), ("Il-10", 3, 0)]), meta("m1", STARTED_AT))

    table = matchup_table(client, "?tour=all")

    assert (table.best, table.worst) == (None, None)


def test_intercept_flights_only(client: Client) -> None:
    seed_matchups()

    assert {m.enemy.log_name: (m.kills, m.losses) for m in matchup_table(client, "?tour=all&intercept=1").rows} == {
        SABRE: (6, 4),  # October's attack-role kill is left out
        "F-51D": (1, 9),
        "Il-10": (3, 0),
    }
    body = client.get(
        reverse("web:aircraft-detail", args=[aircraft(MIG).pk]) + "?tour=all&intercept=1"
    ).content.decode()
    assert "Intercept flights only" in body
    assert 'aria-current="true">Intercept flights only' in body
    plain = client.get(reverse("web:aircraft-detail", args=[aircraft(MIG).pk]) + "?tour=all").content.decode()
    assert 'aria-current="true">All fights' in plain


def test_matchups_sort_and_ignore_a_bad_key(client: Client) -> None:
    seed_matchups()

    by_kills = matchup_table(client, "?tour=all&sort=-kills")
    assert [m.enemy.log_name for m in by_kills.rows] == [SABRE, "Il-10", "F-51D"]
    by_ratio = matchup_table(client, "?tour=all&sort=-ratio")
    assert [m.enemy.log_name for m in by_ratio.rows] == [SABRE, "F-51D", "Il-10"]  # unrated last
    by_name = matchup_table(client, "?tour=all&sort=enemy")
    names = [m.enemy.display_name.casefold() for m in by_name.rows]
    assert names == sorted(names)
    default = matchup_table(client, "?tour=all&sort=bogus;drop")
    assert [m.encounters for m in default.rows] == sorted((m.encounters for m in default.rows), reverse=True)


def test_aircraft_page_budget_with_the_matchup_scopes(client: Client) -> None:
    seed_matchups()
    url = reverse("web:aircraft-detail", args=[aircraft(MIG).pk])

    assert_simple_reads(client, url, max_queries=9)
    assert_simple_reads(client, url + "?tour=all&intercept=1&sort=-ratio", max_queries=9)


def test_the_migration_backfill_builds_the_type_rows_of_an_old_database(tmp_path: Path) -> None:
    """A database from before the type killboard has only the all-time all-kills matchups: a rebuild fills the rest."""
    from il2ks.ops import migrate

    save(duels([(SABRE, 2, 1)]), meta("m1", STARTED_AT))
    good = snapshot()
    PlayerTypeKillboard.objects.all().delete()
    AircraftMatchup.objects.exclude(tour=None, intercept=False).delete()

    migrate._run_backfills(make_instance(tmp_path), [migrate.BACKFILL_TYPE_KILLBOARD])  # pyright: ignore[reportPrivateUsage]

    assert snapshot() == good
