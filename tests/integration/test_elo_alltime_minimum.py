"""All-time Elo and the minimum of rated games (doc 13 Elo; OQ-128 follow-up, PRODUCT default of 2026-10-05): the
all-time rating is the best final rating among the tours in which the pilot had at least `min_games` rated games (the
`[score] min_elo_games` of the boards); only when no tour reaches the minimum, the best tour with any game. So one lucky
win in a new month (1516 after a single game) does not top the all-time board over a season of games. Games stay summed
over the tours. The same for the rating per aircraft type."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from il2ks.core.ratings.elo import DEFAULT_RULES, Game, Rating, RatingKey, RatingRules, compute_all_ratings
from il2ks.db.models import GameObject, Player, PlayerAircraft
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ingest.ratings import best_of_tours
from tests.factories import kill, meta, mission, save, sortie

pytestmark = pytest.mark.django_db

AIR = "air_superiority"
SEPTEMBER = datetime(2026, 9, 19, 20, 0, tzinfo=UTC)
OCTOBER = datetime(2026, 10, 5, 20, 0, tzinfo=UTC)
RULES = replace(DEFAULT_RULES, min_games=2)
TYPE_OF = {1: "MiG-15bis", 2: "F-86A-5"}
SEP = [(1, 2), (2, 1)]  # player 1 wins one and loses one: about 1500 after two games
OCT = [(1, 2)]  # ... and wins his only October game: 1516 after one game


def put(wins: list[tuple[int, int]], at: datetime) -> None:
    index = {1: 0, 2: 1}
    sorties = tuple(sortie(index[p], p, aircraft_type=TYPE_OF[p], coalition=p, combat_role=AIR) for p in (1, 2))
    kills = tuple(kill(100 * (n + 1), index[w], index[v]) for n, (w, v) in enumerate(wins))
    save(mission(sorties, kills), meta(at.strftime("%Y-%m-%d_%H-%M-%S"), at), ratings=RULES)


def september() -> dict[RatingKey, Rating]:
    ids = {name: GameObject.objects.get(log_name=name).pk for name in set(TYPE_OF.values())}
    games = [Game(w, "jet", v, "jet", ids[TYPE_OF[w]], ids[TYPE_OF[v]]) for w, v in SEP]
    return compute_all_ratings(games, RULES).pools


def jet(number: int) -> Player:
    return Player.objects.get(account_uuid__endswith=f"{number:012d}")


def test_one_game_in_a_new_month_does_not_set_the_all_time_rating() -> None:
    put(SEP, SEPTEMBER)
    put(OCT, OCTOBER)
    sept = september()[(1, "jet")]
    assert sept.rating < 1516.0  # the lucky October game alone would be higher

    player = jet(1)
    assert (player.elo_jet, player.elo_jet_games) == (sept.rating, 3)  # September's, the games of both tours


def test_the_per_type_rating_follows_the_same_rule() -> None:
    put(SEP, SEPTEMBER)
    put(OCT, OCTOBER)
    migs = PlayerAircraft.objects.get(player=jet(1), aircraft__log_name="MiG-15bis")
    sept = september()[(1, "jet")]
    assert (migs.elo, migs.elo_games) == (sept.rating, 3)


def test_without_a_qualifying_tour_the_best_tour_with_games_counts() -> None:
    strict = replace(DEFAULT_RULES, min_games=5)  # no tour has five games
    put(SEP, SEPTEMBER)
    put(OCT, OCTOBER)
    rebuild_aggregates(strict)
    assert (jet(1).elo_jet, jet(1).elo_jet_games) == (1516.0, 3)  # the old rule, as nothing reaches the minimum


def test_incremental_equals_rebuild() -> None:
    put(SEP, SEPTEMBER)
    put(OCT, OCTOBER)
    before = (jet(1).elo_jet, jet(1).elo_jet_games, jet(2).elo_jet, jet(2).elo_jet_games)
    rebuild_aggregates(RULES)
    assert (jet(1).elo_jet, jet(1).elo_jet_games, jet(2).elo_jet, jet(2).elo_jet_games) == before


def test_best_of_tours_prefers_tours_that_reach_the_minimum() -> None:
    rows = [("a", 1400.0, 10), ("a", 1516.0, 1), ("a", 1450.0, 5), ("b", 1516.0, 1), ("b", 1490.0, 2), ("c", 1600.0, 4)]
    found = best_of_tours(rows, 5)
    assert found["a"] == Rating(1450.0, 16)  # the best of the tours with 5+ games; games of all tours
    assert found["b"] == Rating(1516.0, 3)  # no tour reaches 5: the best with any game
    assert found["c"] == Rating(1600.0, 4)
    assert best_of_tours(rows, 1)["a"] == Rating(1516.0, 16)  # a minimum of one: the plain best tour
    assert best_of_tours(rows, 0)["a"] == Rating(1516.0, 16)  # at least one game, like the boards


def test_the_rules_carry_the_minimum() -> None:
    assert RatingRules().min_games == 5  # the default of `[score] min_elo_games`
