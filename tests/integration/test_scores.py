"""Sortie scores and their sums (FR-WEB-7, FR-ADM-7): stored at ingest, summed per mission, player, tour and aircraft,
and re-applied by `rebuild_aggregates` when the `[score]` rules change, without reprocessing."""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from django.core import management
from django.db.migrations.executor import MigrationExecutor

from il2ks.core.ratings.score import DEFAULT_SCORE_RULES, ScoreRules
from il2ks.db.models import (
    Mission,
    Player,
    PlayerAircraft,
    PlayerMission,
    PlayerSortie,
    PlayerTour,
    PlayerTourAircraft,
)
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ingest.reprocess import rebuild_all
from il2ks.ingest.scoring import FACT_COLUMNS, facts_of
from il2ks.ops.migrate import migrate_if_needed
from tests.factories import STARTED_AT, account, meta, mission, rows, save, sortie
from tests.ops_helpers import make_instance, recording, returning

pytestmark = pytest.mark.django_db

TABLES = (Player, PlayerMission, PlayerAircraft, PlayerTour, PlayerTourAircraft, PlayerSortie)


def seed(rules: ScoreRules = DEFAULT_SCORE_RULES) -> None:
    """One fighter pilot (air kills, a tank and fences, then killed and captured), one attacker, one gunner."""
    fighter = replace(
        sortie(
            0,
            1,
            combat_role="air_superiority",
            kills_air_pvp=2,
            kills_air_ai=1,
            assists=1,
            ground_by_category={"tank": 1, "other": 5},
            is_death=True,
            is_plane_lost=True,
            outcome="shot_down",
        ),
        is_captured=True,
    )
    attacker = sortie(
        1,
        2,
        aircraft_type="Il-10",
        combat_role="attack",
        ground_by_category={"tank": 2, "vehicle": 3},
        time_on_target_s=300.0,
        is_death=True,
        is_plane_lost=True,
        outcome="shot_down",
    )
    gunner = sortie(2, 3, aircraft_type="Turret_IL10", role="gunner", kills_air_pvp=5, kills_air_ai=0, combat_role=None)
    save(mission((fighter, attacker, gunner)), score=rules)


def sortie_score(player: int) -> tuple[float, float]:
    row = PlayerSortie.objects.get(account_uuid=account(player))
    return row.air_points, row.ground_points


def test_scores_are_stored_per_sortie() -> None:
    seed()

    # fighter: 2 * 10 + 2 + 3 = 25 air, minus death 3, plane lost 2, capture 2 = 18; ground 6 + 5 * 0.2 = 7
    assert sortie_score(1) == (18.0, 7.0)
    # attacker: no air points, ground 2 * 6 + 3 * 3 = 21 minus 3 + 2 = 16
    assert sortie_score(2) == (0.0, 16.0)
    assert sortie_score(3) == (0.0, 0.0)  # gunners score nothing, whatever the log credits them with


def test_scores_are_summed_on_every_level() -> None:
    seed()

    fighter, attacker = (Player.objects.get(account_uuid=account(n)) for n in (1, 2))
    assert (fighter.score_air, fighter.score_ground, fighter.score_ground_attack) == (18.0, 7.0, 0.0)
    assert (attacker.score_air, attacker.score_ground, attacker.score_ground_attack) == (0.0, 16.0, 16.0)
    assert PlayerMission.objects.get(player=attacker).score_ground_attack == 16.0
    assert PlayerAircraft.objects.get(player=fighter).score_air == 18.0
    assert PlayerTour.objects.get(player=attacker).score_ground == 16.0
    assert PlayerTourAircraft.objects.get(player=fighter).score_air == 18.0
    assert Player.objects.get(account_uuid=account(3)).score_air == 0.0


def test_ground_score_in_a_fighter_sortie_is_not_attack_score() -> None:
    save(mission((sortie(0, 1, combat_role="air_superiority", ground_by_category={"tank": 1}),)))

    player = Player.objects.get(account_uuid=account(1))
    assert (player.score_ground, player.score_ground_attack) == (6.0, 0.0)


def test_scores_accumulate_over_missions_and_tours() -> None:
    save(mission((sortie(0, 1, kills_air_pvp=1, kills_air_ai=0),)), meta("2026-09-19_22-34-13", STARTED_AT))
    save(
        mission((sortie(0, 1, kills_air_pvp=2, kills_air_ai=0),)),
        meta("2026-10-19_22-34-13", STARTED_AT + timedelta(days=30)),
    )

    player = Player.objects.get(account_uuid=account(1))
    assert player.score_air == 30.0
    assert sorted(PlayerTour.objects.filter(player=player).values_list("score_air", flat=True)) == [10.0, 20.0]


def test_a_changed_rule_is_applied_by_rebuild_without_reprocessing() -> None:
    seed()
    sorties_before = [r.pk for r in PlayerSortie.objects.order_by("pk")]
    rules = ScoreRules(air_kill_pvp=100.0, ground_tank=0.0, penalty_death=0.0, penalty_plane_lost=0.0)

    rebuild_aggregates(score=rules)

    # fighter: 200 + 2 + 3 = 205 minus capture 2; ground 0 + 1.0
    assert sortie_score(1) == (203.0, 1.0)
    assert sortie_score(2) == (0.0, 9.0)  # attacker: 3 vehicles, no penalties left
    assert Player.objects.get(account_uuid=account(1)).score_air == 203.0
    assert PlayerMission.objects.get(player__account_uuid=account(2)).score_ground_attack == 9.0
    assert PlayerAircraft.objects.get(player__account_uuid=account(1)).score_air == 203.0
    assert PlayerTour.objects.get(player__account_uuid=account(2)).score_ground == 9.0
    assert PlayerTourAircraft.objects.get(player__account_uuid=account(2)).score_ground_attack == 9.0
    assert [r.pk for r in PlayerSortie.objects.order_by("pk")] == sorties_before


def test_rebuild_with_the_same_rules_changes_nothing_and_equals_incremental() -> None:
    seed()
    before = {t: rows(t) for t in TABLES}

    rebuild_aggregates()

    assert {t: rows(t) for t in TABLES} == before


def test_rebuild_after_a_rule_change_equals_saving_under_those_rules() -> None:
    rules = ScoreRules(air_kill_ai=7.5, ground_other=0.3, penalty_capture=1.1)
    seed(rules)
    saved_under_rules = _comparable()
    Mission.objects.all().delete()  # sorties, kills and player-mission rows go with it
    Player.objects.all().delete()  # and the level-2 rows

    seed()
    rebuild_aggregates(score=rules)

    assert _comparable() == saved_under_rules


def _comparable() -> dict[str, list[dict[str, object]]]:
    """All score-bearing tables without the ids and keys that differ between two seedings."""
    skip = {"id", "player_id", "mission_id", "tour_id", "aircraft_id", "killer_sortie_id", "victim_sortie_id"}
    return {t.__name__: [{k: v for k, v in row.items() if k not in skip} for row in rows(t)] for t in TABLES}


def test_the_score_reads_only_the_listed_columns() -> None:
    """`facts_of` needs exactly `FACT_COLUMNS` (a rebuild reads nothing else from the sortie rows)."""
    facts = facts_of(dict.fromkeys(FACT_COLUMNS, 0))
    assert facts.attack is False
    assert facts.kills_ground == dict.fromkeys(facts.kills_ground, 0)


def test_migrating_a_database_from_before_scores_scores_the_old_sorties(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the schema update (new columns all 0), existing sorties get their scores under the configured rules."""
    seed()
    expected = _comparable()
    PlayerSortie.objects.update(air_points=0.0, ground_points=0.0)
    for table in (Player, PlayerMission, PlayerAircraft, PlayerTour, PlayerTourAircraft):
        table.objects.update(score_air=0.0, score_ground=0.0, score_ground_attack=0.0)
    monkeypatch.setattr(MigrationExecutor, "migration_plan", returning([("fake", False)]))
    monkeypatch.setattr(management, "call_command", recording([], "migrate"))

    migrate_if_needed(make_instance(tmp_path), "ingest", wait=None)

    assert _comparable() == expected


def test_the_score_config_reaches_save_and_rebuild_through_the_pipeline(tmp_path: Path) -> None:
    """`[score]` values flow from the config file into `rebuild_all` (`il2ks rebuild-aggregates`)."""
    cfg = make_instance(tmp_path, extra_toml="[score]\nair_kill_pvp = 50\n")
    seed()

    rebuild_all(cfg)

    assert sortie_score(1)[0] == 50 * 2 + 2 + 3 - 3 - 2 - 2


def test_the_migration_backfills_pass_every_configured_section(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both backfills go through one helper that hands `[marks]` and `[score]` to `rebuild_aggregates`."""
    import il2ks.ingest.aggregates as aggregates
    from il2ks.ops import migrate

    seed()
    PlayerSortie.objects.update(air_points=0.0, ground_points=0.0)
    cfg = make_instance(tmp_path, extra_toml="[marks]\nmin_sorties = 7\n")
    calls: list[dict[str, object]] = []

    def fake(*args: object, **kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(aggregates, "rebuild_aggregates", fake)

    migrate._backfill_scores(cfg)  # pyright: ignore[reportPrivateUsage]

    assert calls == [{"marks": cfg.marks, "score": cfg.score}]
    assert cfg.marks.min_sorties == 7
