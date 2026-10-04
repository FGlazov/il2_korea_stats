"""The pure air-to-air Elo function (OQ-28, FR-WEB-19)."""

import pytest

from il2ks.core.ratings.elo import Game, Pool, Rating, RatingRules, compute_all_ratings, compute_ratings, expected_score

RULES = RatingRules(start=1500.0, k=32.0, cross_pool_weight=2.0)


def prop(winner: int, loser: int) -> Game:
    return Game(winner, "prop", loser, "prop")


def jet(winner: int, loser: int) -> Game:
    return Game(winner, "jet", loser, "jet")


def test_expected_score_is_the_standard_curve() -> None:
    assert expected_score(1500, 1500) == pytest.approx(0.5)
    assert expected_score(1900, 1500) == pytest.approx(10 / 11)
    assert expected_score(1500, 1900) == pytest.approx(1 / 11)


def test_no_games_no_ratings() -> None:
    assert compute_ratings([], RULES) == {}


def test_same_pool_equal_ratings_moves_half_k() -> None:
    result = compute_ratings([prop(1, 2)], RULES)
    assert result == {(1, "prop"): Rating(1516.0, 1), (2, "prop"): Rating(1484.0, 1)}


def test_same_pool_is_zero_sum_and_pools_are_independent() -> None:
    result = compute_ratings([jet(1, 2), jet(1, 2), prop(2, 1)], RULES)
    assert result[(1, "jet")].rating + result[(2, "jet")].rating == pytest.approx(3000.0)
    assert result[(1, "prop")].rating + result[(2, "prop")].rating == pytest.approx(3000.0)
    assert result[(1, "jet")].games == 2
    assert result[(1, "prop")].games == 1


def test_beating_a_stronger_player_pays_more() -> None:
    result = compute_ratings([prop(1, 2), prop(1, 2), prop(3, 1)], RULES)
    # Player 1 was above 1500 when player 3 (1500) beat them: more than the 16 points between equals.
    assert result[(3, "prop")].rating - 1500.0 > 16.0


def test_jet_kills_prop_changes_nothing() -> None:
    assert compute_ratings([Game(1, "jet", 2, "prop")], RULES) == {}


def test_jet_kill_on_prop_does_not_count_as_a_game() -> None:
    result = compute_ratings([Game(1, "jet", 2, "prop"), prop(1, 2)], RULES)
    assert result[(1, "prop")] == Rating(1516.0, 1)
    assert (1, "jet") not in result
    assert (2, "jet") not in result


def test_prop_kills_jet_is_weighted_and_moves_killer_prop_and_victim_jet() -> None:
    result = compute_ratings([Game(1, "prop", 2, "jet")], RULES)
    # K = 32 * 2, equal ratings so the expectation is 0.5: +-32
    assert result == {(1, "prop"): Rating(1532.0, 1), (2, "jet"): Rating(1468.0, 1)}


def test_prop_kills_jet_uses_the_killers_prop_and_the_victims_jet_rating() -> None:
    # Player 2 builds a jet rating of 1516 / 1484 against player 3; player 1 builds a prop rating against player 4.
    setup = [jet(2, 3), prop(1, 4)]
    result = compute_ratings([*setup, Game(1, "prop", 2, "jet")], RULES)
    prop_1, jet_2 = 1516.0, 1516.0
    change = 64.0 * (1.0 - expected_score(prop_1, jet_2))
    assert result[(1, "prop")].rating == pytest.approx(prop_1 + change)
    assert result[(2, "jet")].rating == pytest.approx(jet_2 - change)
    assert result[(1, "prop")].games == 2
    assert result[(2, "jet")].games == 2


def test_cross_pool_weight_and_k_are_configurable() -> None:
    rules = RatingRules(start=1000.0, k=10.0, cross_pool_weight=3.0)
    result = compute_ratings([Game(1, "prop", 2, "jet")], rules)
    assert result == {(1, "prop"): Rating(1015.0, 1), (2, "jet"): Rating(985.0, 1)}


def test_order_matters() -> None:
    a_first = compute_ratings([prop(1, 2), prop(2, 3), prop(3, 1)], RULES)
    b_first = compute_ratings([prop(3, 1), prop(2, 3), prop(1, 2)], RULES)
    assert a_first != b_first
    assert {k: v.games for k, v in a_first.items()} == {k: v.games for k, v in b_first.items()}


def test_same_input_same_output() -> None:
    games = [prop(1, 2), Game(2, "prop", 3, "jet"), jet(3, 4), prop(4, 1)]
    assert compute_ratings(games, RULES) == compute_ratings(iter(games), RULES)


def test_a_player_beating_themselves_is_ignored() -> None:
    assert compute_ratings([prop(1, 1)], RULES) == {}


# --- per-type ratings (OQ-49) ---


def typed(winner: int, winner_type: int, loser: int, loser_type: int, pool: Pool = "prop") -> Game:
    return Game(winner, pool, loser, pool, winner_type, loser_type)


def test_per_type_ratings_follow_the_same_rules_per_player_and_type() -> None:
    result = compute_all_ratings([typed(1, 10, 2, 20)], RULES)
    assert result.types == {(1, 10): Rating(1516.0, 1), (2, 20): Rating(1484.0, 1)}
    assert result.pools == compute_ratings([typed(1, 10, 2, 20)], RULES)


def test_a_pilots_types_are_rated_independently() -> None:
    games = [typed(1, 10, 2, 20), typed(1, 11, 2, 20), typed(2, 21, 1, 10)]
    types = compute_all_ratings(games, RULES).types
    # Player 1 in type 11 starts at 1500 again; player 2 in type 21 has no earlier rating either.
    assert types[(1, 11)].games == 1
    assert types[(1, 10)].games == 2  # won once, then lost to type 21
    # Type 21 (fresh 1500) beat type 10 (1516): a bit more than the 16 points between equals, and it is zero-sum.
    gain = types[(2, 21)].rating - 1500.0
    assert gain > 16.0
    assert types[(1, 10)].rating == pytest.approx(1516.0 - gain)


def test_per_type_ratings_apply_the_pool_rules() -> None:
    jet_on_prop = Game(1, "jet", 2, "prop", 10, 20)
    prop_on_jet = Game(3, "prop", 4, "jet", 30, 40)
    types = compute_all_ratings([jet_on_prop, prop_on_jet], RULES).types
    assert (1, 10) not in types
    assert types == {(3, 30): Rating(1532.0, 1), (4, 40): Rating(1468.0, 1)}


def test_a_game_without_aircraft_types_only_moves_the_pools() -> None:
    result = compute_all_ratings([prop(1, 2), typed(1, 10, 3, 0)], RULES)
    assert result.types == {}
    assert result.pools[(1, "prop")].games == 2
