"""Air-to-air Elo on `Player` (OQ-28, FR-WEB-19): which kills count, rebuild == incremental, idempotence."""

from datetime import timedelta

import pytest

from il2ks.core.ratings.elo import Game, Rating, RatingKey, RatingRules, compute_ratings
from il2ks.core.replay.result import MissionResult
from il2ks.db.models import GameObject, Player
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ingest.persist import MissionMeta
from il2ks.ingest.ratings import recompute_ratings
from tests.factories import STARTED_AT, account, kill, meta, mission, save, sortie

pytestmark = pytest.mark.django_db

AIR = "air_superiority"


def first_mission() -> tuple[MissionResult, list[Game]]:
    """Two jets and two prop aircraft; four qualifying kills (one of them a jet on a prop aircraft: no change)."""
    result = mission(
        (
            sortie(0, 1, aircraft_type="MiG-15bis", coalition=1, combat_role=AIR),
            sortie(1, 2, aircraft_type="F-86A-5", coalition=2, combat_role=AIR),
            sortie(2, 3, aircraft_type="F-51D", coalition=1, combat_role=AIR),
            sortie(3, 4, aircraft_type="F-51D", coalition=2, combat_role=AIR),
        ),
        (
            kill(100, 0, 1),  # jet beats jet
            kill(200, 2, 3),  # prop beats prop
            kill(300, 2, 1),  # prop beats jet (weighted)
            kill(400, 0, 3),  # jet beats prop (no change)
        ),
    )
    games = [Game(1, "jet", 2, "jet"), Game(3, "prop", 4, "prop"), Game(3, "prop", 2, "jet"), Game(1, "jet", 4, "prop")]
    return result, games


def second_mission() -> tuple[MissionResult, list[Game]]:
    """One qualifying kill among a lot that must not count."""
    result = mission(
        (
            sortie(0, 1, aircraft_type="MiG-15bis", coalition=1, combat_role=AIR),
            sortie(1, 2, aircraft_type="F-86A-5", coalition=2, combat_role=AIR),
            sortie(2, 5, aircraft_type="MiG-15bis", coalition=1, combat_role="attack"),
            sortie(3, 6, aircraft_type="F-86A-5", coalition=2, combat_role=AIR),
            sortie(4, 7, aircraft_type="Brand-New Jet", coalition=1, combat_role=AIR),
            sortie(5, 8, aircraft_type="Turret_IL10", coalition=1, role="gunner"),
            sortie(6, 9, aircraft_type="F-86A-5", coalition=1, combat_role=AIR),
        ),
        (
            kill(100, 1, 0),  # qualifies: jet beats jet
            kill(200, 0, 1, credit="assist"),  # assists are not games
            kill(300, 0, 6, is_friendly=True),  # friendly fire is not a game
            kill(400, 2, 3),  # the killer flew an attack sortie
            kill(500, 3, 2),  # the victim flew an attack sortie
            kill(600, 4, 1),  # unknown aircraft: no pool
            kill(700, 0, 4),
            kill(800, 5, 1),  # a gunner scored it
        ),
    )
    return result, [Game(2, "jet", 1, "jet")]


def second_started() -> MissionMeta:
    return meta("2026-09-20_22-00-00", STARTED_AT + timedelta(days=1))


def ratings_by_account() -> dict[RatingKey, Rating]:
    found: dict[RatingKey, Rating] = {}
    for p in Player.objects.all():
        number = int(p.account_uuid[-12:])
        if p.elo_prop_games:
            found[(number, "prop")] = Rating(p.elo_prop, p.elo_prop_games)
        if p.elo_jet_games:
            found[(number, "jet")] = Rating(p.elo_jet, p.elo_jet_games)
    return found


def all_player_rows() -> list[dict[str, object]]:
    return list(Player.objects.order_by("account_uuid").values())


def test_players_without_games_have_the_start_rating() -> None:
    save(mission((sortie(0, 1), sortie(1, 2, coalition=2)), (kill(100, 0, 1),)))  # no combat role: no game
    for p in Player.objects.all():
        assert (p.elo_prop, p.elo_jet, p.elo_prop_games, p.elo_jet_games) == (1500.0, 1500.0, 0, 0)


def test_only_qualifying_kills_count() -> None:
    m1, games1 = first_mission()
    m2, games2 = second_mission()
    save(m1)
    save(m2, second_started())

    expected = compute_ratings(games1 + games2, RatingRules())

    assert ratings_by_account() == expected
    # Spot checks of the rules: prop pilot 3 beat a jet and a prop; jet pilot 1 got nothing from beating a prop.
    assert expected[(3, "prop")].games == 2
    assert (1, "prop") not in expected
    assert Player.objects.get(account_uuid=account(8)).elo_jet_games == 0  # the gunner
    assert Player.objects.get(account_uuid=account(7)).elo_jet_games == 0  # unknown aircraft, no pool
    assert Player.objects.get(account_uuid=account(5)).elo_jet_games == 0  # attack sortie
    assert Player.objects.get(account_uuid=account(9)).elo_jet_games == 0  # only a friendly victim


def test_order_is_mission_start_not_save_order() -> None:
    m1, games1 = first_mission()
    m2, games2 = second_mission()
    save(m2, second_started())
    save(m1)  # older mission saved last

    assert ratings_by_account() == compute_ratings(games1 + games2, RatingRules())


def test_unknown_pool_is_skipped() -> None:
    """A known aircraft with no propulsion (here a gameobject edited by an admin) leaves no game."""
    m1, _ = first_mission()
    save(m1)
    before = all_player_rows()
    GameObject.objects.filter(log_name="F-51D").update(propulsion="")

    recompute_ratings()

    after = ratings_by_account()
    assert after == compute_ratings([Game(1, "jet", 2, "jet")], RatingRules())
    assert all_player_rows() != before


def test_rebuild_equals_incremental() -> None:
    m1, _ = first_mission()
    m2, _ = second_mission()
    save(m1)
    save(m2, second_started())
    incremental = all_player_rows()

    rebuild_aggregates()

    assert all_player_rows() == incremental


def test_recompute_is_idempotent_and_repairs_drift() -> None:
    m1, _ = first_mission()
    save(m1)
    good = all_player_rows()

    assert recompute_ratings() == 4
    assert recompute_ratings() == 4
    assert all_player_rows() == good

    Player.objects.update(elo_prop=1.0, elo_jet_games=99)
    recompute_ratings()
    assert all_player_rows() == good


def test_rules_are_applied_and_start_reaches_players_without_games() -> None:
    m1, games1 = first_mission()
    save(m1)
    save(mission((sortie(0, 20, coalition=1),)), meta("2026-09-21_01-00-00", STARTED_AT + timedelta(days=2)))
    rules = RatingRules(start=1000.0, k=10.0, cross_pool_weight=3.0)

    rebuild_aggregates(rules)

    assert ratings_by_account() == compute_ratings(games1, rules)
    idle = Player.objects.get(account_uuid=account(20))
    assert (idle.elo_prop, idle.elo_jet, idle.elo_prop_games, idle.elo_jet_games) == (1000.0, 1000.0, 0, 0)


def test_save_mission_recomputes_ratings_unless_deferred() -> None:
    m1, games1 = first_mission()
    save(m1, ratings=None)
    assert ratings_by_account() == {}

    recompute_ratings()

    assert ratings_by_account() == compute_ratings(games1, RatingRules())
