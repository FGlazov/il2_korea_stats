"""Elo per tour (OQ-128, maintainer 2026-10-05: "a new tour is a clean slate"): ratings are replayed per tour from the
initial rating, the all-time Elo is the best final rating of a tour (games summed), a late import only changes its own
tour, incremental == rebuild, and the Elo boards read the tour rows in a tour and the all-time rows otherwise."""

from datetime import UTC, datetime

import pytest

from il2ks.config import LeaderboardConfig
from il2ks.core.ratings.elo import DEFAULT_RULES, Game, Rating, compute_all_ratings
from il2ks.core.replay.result import MissionResult
from il2ks.db.models import GameObject, Player, PlayerAircraft, PlayerSortie, PlayerTourAircraft, PlayerTourPool, Tour
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ingest.ratings import recompute_ratings
from il2ks.queries.leaderboards import BOARDS, board_page
from tests.factories import kill, meta, mission, save, sortie

pytestmark = pytest.mark.django_db

AIR = "air_superiority"
SEPTEMBER = datetime(2026, 9, 19, 20, 0, tzinfo=UTC)
LATER_SEPTEMBER = datetime(2026, 9, 25, 20, 0, tzinfo=UTC)
OCTOBER = datetime(2026, 10, 5, 20, 0, tzinfo=UTC)
TYPE_OF = {1: "MiG-15bis", 2: "F-86A-5", 3: "MiG-15bis", 4: "F-86A-5"}  # player number -> aircraft log name
SIDE_OF = {1: 1, 2: 2, 3: 1, 4: 2}  # coalitions: odd players against even ones, so every win is against an enemy
SEP_WINS = [(1, 2), (2, 3)]
OCT_WINS = [(2, 1), (4, 1)]


def duels(wins: list[tuple[int, int]]) -> MissionResult:
    """A mission of jet pilots (each in a fixed type); `wins` = (winner, loser) player numbers in kill order."""
    players = sorted({p for pair in wins for p in pair})
    index = {p: i for i, p in enumerate(players)}
    return mission(
        tuple(sortie(index[p], p, aircraft_type=TYPE_OF[p], coalition=SIDE_OF[p], combat_role=AIR) for p in players),
        tuple(kill(100 * (n + 1), index[w], index[v]) for n, (w, v) in enumerate(wins)),
    )


def put(wins: list[tuple[int, int]], started_at: datetime) -> None:
    save(duels(wins), meta(started_at.strftime("%Y-%m-%d_%H-%M-%S"), started_at))


def games(wins: list[tuple[int, int]]) -> list[Game]:
    ids = {name: GameObject.objects.get(log_name=name).pk for name in set(TYPE_OF.values())}
    return [Game(w, "jet", v, "jet", ids[TYPE_OF[w]], ids[TYPE_OF[v]]) for w, v in wins]


def tour_at(moment: datetime) -> Tour:
    return Tour.objects.get(started_at__lte=moment, ended_at__gt=moment)


def tour_pool(moment: datetime) -> dict[int, Rating]:
    """The jet Elo rows of the tour containing `moment`, keyed by player number."""
    rows = PlayerTourPool.objects.filter(tour=tour_at(moment), propulsion="jet", elo_games__gt=0)
    return {int(r.player.account_uuid[-12:]): Rating(r.elo, r.elo_games) for r in rows.select_related("player")}


def expected(wins: list[tuple[int, int]]) -> dict[int, Rating]:
    return {k[0]: v for k, v in compute_all_ratings(games(wins), DEFAULT_RULES).pools.items()}


def all_time() -> dict[int, Rating]:
    return {
        int(p.account_uuid[-12:]): Rating(p.elo_jet, p.elo_jet_games)
        for p in Player.objects.filter(elo_jet_games__gt=0)
    }


def every_elo_field() -> dict[str, list[tuple[float, ...]]]:
    players = Player.objects.order_by("account_uuid")
    return {
        "player": list(players.values_list("elo_prop", "elo_prop_games", "elo_jet", "elo_jet_games")),
        "aircraft": list(
            PlayerAircraft.objects.order_by("player__account_uuid", "aircraft_id").values_list("elo", "elo_games")
        ),
        "tour_pool": list(
            PlayerTourPool.objects.order_by("player__account_uuid", "tour_id", "propulsion").values_list(
                "elo", "elo_games"
            )
        ),
        "tour_aircraft": list(
            PlayerTourAircraft.objects.order_by("player__account_uuid", "tour_id", "aircraft_id").values_list(
                "elo", "elo_games"
            )
        ),
        "peaks": [(p,) for p in PlayerSortie.objects.order_by("pk").values_list("elo_peak", flat=True)],
    }


def test_elo_resets_at_a_new_tour() -> None:
    put(SEP_WINS, SEPTEMBER)
    put(OCT_WINS, OCTOBER)

    assert tour_pool(SEPTEMBER) == expected(SEP_WINS)
    assert tour_pool(OCTOBER) == expected(OCT_WINS)  # not continued from September: everyone starts at 1500 again
    assert tour_pool(OCTOBER)[1].rating < 1500.0 < tour_pool(OCTOBER)[2].rating


def test_all_time_elo_is_the_best_final_rating_of_a_tour_and_games_are_summed() -> None:
    put(SEP_WINS, SEPTEMBER)
    put(OCT_WINS, OCTOBER)
    september, october = expected(SEP_WINS), expected(OCT_WINS)

    found = all_time()

    for player in (1, 2):
        best = max(september[player].rating, october[player].rating)
        assert found[player] == Rating(best, september[player].games + october[player].games)
    assert found[2].rating == october[2].rating > september[2].rating  # the loser of September, best in October
    assert found[3] == september[3]  # played one tour only


def test_per_type_elo_is_per_tour_and_all_time_is_the_best_tour() -> None:
    put(SEP_WINS, SEPTEMBER)
    put(OCT_WINS, OCTOBER)
    sabre = GameObject.objects.get(log_name="F-86A-5").pk
    sep = compute_all_ratings(games(SEP_WINS), DEFAULT_RULES).types
    octo = compute_all_ratings(games(OCT_WINS), DEFAULT_RULES).types

    tour_rows = {
        (int(r.player.account_uuid[-12:]), r.tour.started_at.month, r.aircraft_id): Rating(r.elo, r.elo_games)
        for r in PlayerTourAircraft.objects.filter(elo_games__gt=0).select_related("player", "tour")
    }
    assert tour_rows[(2, 9, sabre)] == sep[(2, sabre)]
    assert tour_rows[(2, 10, sabre)] == octo[(2, sabre)]
    row = PlayerAircraft.objects.get(player__account_uuid__endswith="000000000002", aircraft_id=sabre)
    assert row.elo == max(sep[(2, sabre)].rating, octo[(2, sabre)].rating)
    assert row.elo_games == sep[(2, sabre)].games + octo[(2, sabre)].games


def test_a_late_import_into_an_old_tour_changes_only_that_tour() -> None:
    put(OCT_WINS, OCTOBER)
    october = tour_pool(OCTOBER)

    put(SEP_WINS, SEPTEMBER)  # imported afterwards, belongs to the earlier tour

    assert tour_pool(OCTOBER) == october
    assert tour_pool(SEPTEMBER) == expected(SEP_WINS)
    assert all_time()[2].rating == max(expected(SEP_WINS)[2].rating, october[2].rating)


def test_recomputing_one_tour_leaves_the_others_alone() -> None:
    put(SEP_WINS, SEPTEMBER)
    put(OCT_WINS, OCTOBER)
    october = tour_at(OCTOBER)
    PlayerTourPool.objects.filter(tour=october).update(elo=1.0, elo_games=9)

    recompute_ratings(tour_ids=[tour_at(SEPTEMBER).pk])

    assert set(PlayerTourPool.objects.filter(tour=october).values_list("elo", "elo_games")) == {(1.0, 9)}
    recompute_ratings(tour_ids=[october.pk])
    assert tour_pool(OCTOBER) == expected(OCT_WINS)


def test_incremental_equals_rebuild_for_every_elo_field() -> None:
    put(OCT_WINS, OCTOBER)
    put(SEP_WINS, SEPTEMBER)
    put([(3, 2), (1, 2)], LATER_SEPTEMBER)
    incremental = every_elo_field()
    assert incremental["tour_aircraft"]

    rebuild_aggregates()

    assert every_elo_field() == incremental


def test_peaks_come_from_the_sortie_own_tour() -> None:
    """`PlayerSortie.elo_peak` (the Top Rated medal) comes from the sortie's tour replay."""
    put(SEP_WINS, SEPTEMBER)
    put(OCT_WINS, OCTOBER)
    peaks = {
        (s.player.account_uuid[-12:], s.mission.started_at.month): s.elo_peak
        for s in PlayerSortie.objects.select_related("player", "mission")
    }
    assert peaks[("000000000002", 10)] == expected(OCT_WINS)[2].rating  # a win from 1500, not from September's rating
    assert 1500.0 < peaks[("000000000002", 9)] < peaks[("000000000002", 10)]


def test_elo_boards_show_the_tours_rating_in_a_tour_and_the_best_of_them_all_time() -> None:
    put(SEP_WINS, SEPTEMBER)
    put(OCT_WINS, OCTOBER)
    rules = LeaderboardConfig(min_elo_games=1)

    def board(tour: Tour | None) -> dict[str, float]:
        page = board_page(BOARDS["elo-jet"], "-rating", 1, rules, tour=tour)
        return {r.player.account_uuid[-12:]: r.rating for r in page.object_list}

    in_october = board(tour_at(OCTOBER))
    assert in_october["000000000002"] == expected(OCT_WINS)[2].rating
    assert in_october["000000000001"] < 1500.0
    assert "000000000003" not in in_october  # did not play in October
    assert board(tour_at(SEPTEMBER))["000000000001"] == expected(SEP_WINS)[1].rating
    best = board(None)
    assert best["000000000002"] == expected(OCT_WINS)[2].rating
    assert best["000000000001"] == expected(SEP_WINS)[1].rating  # September was its best tour


def test_aircraft_page_top_pilots_use_the_tours_per_type_elo_and_all_time_the_best() -> None:
    from il2ks.queries.aircraft import top_elo

    put(SEP_WINS, SEPTEMBER)
    put(OCT_WINS, OCTOBER)
    rules = LeaderboardConfig(min_elo_games=1)
    sabre = GameObject.objects.get(log_name="F-86A-5")
    sep = compute_all_ratings(games(SEP_WINS), DEFAULT_RULES).types
    octo = compute_all_ratings(games(OCT_WINS), DEFAULT_RULES).types
    sabre_id = sabre.pk

    in_october = {r.player.account_uuid[-12:]: r.elo for r in top_elo(sabre, rules, tour_at(OCTOBER))}
    in_september = {r.player.account_uuid[-12:]: r.elo for r in top_elo(sabre, rules, tour_at(SEPTEMBER))}
    best = {r.player.account_uuid[-12:]: r.elo for r in top_elo(sabre, rules)}

    assert in_october["000000000002"] == octo[(2, sabre_id)].rating
    assert in_september["000000000002"] == sep[(2, sabre_id)].rating
    assert best["000000000002"] == max(sep[(2, sabre_id)].rating, octo[(2, sabre_id)].rating)


def test_the_profile_shows_the_tours_elo_in_a_tour_and_the_best_one_all_time() -> None:
    from il2ks.queries.players import elo_shown

    put(SEP_WINS, SEPTEMBER)
    put(OCT_WINS, OCTOBER)
    player = Player.objects.get(account_uuid__endswith="000000000002")

    assert elo_shown(player, tour_at(OCTOBER)).elo_jet == expected(OCT_WINS)[2].rating
    assert elo_shown(player, tour_at(SEPTEMBER)).elo_jet == expected(SEP_WINS)[2].rating
    shown = elo_shown(player, None)
    assert (shown.elo_jet, shown.elo_jet_games) == (player.elo_jet, player.elo_jet_games)
    assert elo_shown(player, tour_at(OCTOBER)).elo_prop_games == 0
