"""Air-to-air Elo (OQ-28, FR-WEB-19). A pure function over a chronological list of games.

Each player has one rating per pool (`prop` / `jet`), all starting at `RatingRules.start`. A game is one air-to-air kill
between two air superiority sorties, named by the winner's and the loser's player and pool. Rules:

- Same pool: standard Elo, `E = 1 / (1 + 10 ** ((loser - winner) / 400))`; the winner gains `k * (1 - E)` and the loser
  loses the same.
- A jet kills a prop aircraft: no change for either player. The jet is expected to win, so it earns nothing, and the
  prop pilot's rating says how they do against other prop pilots.
- A prop aircraft kills a jet: both update as above with `k * cross_pool_weight` instead of `k`, and the expectation
  comes from the killer's prop rating against the victim's jet rating. Only the killer's prop rating and the victim's
  jet rating move.
- Both sides of a game that changes ratings get +1 game in the pool whose rating changed. Games where winner and loser
  are the same player are ignored.

The same games also feed a **per-type rating** for each `(player, aircraft type)` (OQ-49/50: rank a type's top pilots
by skill). It follows exactly the same rules, with each side's rating being the one for the aircraft type it flew, so a
pilot's Be 109 rating and their Spitfire rating are independent. It comes from the same pass over the games
(`compute_all_ratings`), which stays a single replay.

The same games also feed an **aircraft type rating** (maintainer, 2026-10-05): one rating per aircraft type, where each
game is won by the winner's type against the loser's type (`compute_type_ratings`, same pool rules, a type against
itself is no game). It says how the types do against each other, whoever flew them.

The result depends on the order of the games, so the caller passes them in chronological order.
"""

from collections.abc import Hashable, Iterable
from dataclasses import dataclass
from typing import Literal

type Pool = Literal["prop", "jet"]

ELO_SCALE = 400.0  # a 400-point lead means 10:1 odds


@dataclass(frozen=True, slots=True)
class RatingRules:
    """The `[ratings]` config section. The defaults are a reasonable guess, not a tuned value (OQ-28)."""

    start: float = 1500.0  # rating of a player with no games
    k: float = 32.0  # maximum rating change of one game between players of the same pool
    cross_pool_weight: float = 2.0  # a prop kill on a jet moves ratings k * this much
    min_games: int = 5
    """Not a `[ratings]` key: the `[score] min_elo_games` of the boards (config.py). The all-time rating is the best
    final rating among the tours with at least this many rated games (the best tour with any game when none reaches
    it), so one game in a new month cannot set it."""

    @property
    def type_min_games(self) -> int:
        """Games an aircraft type needs in a tour for that tour to qualify for its all-time rating: every kill between
        two types is a game for both, so a type collects far more games than a pilot (`TYPE_MIN_GAMES_FACTOR`)."""
        return max(self.min_games, 1) * TYPE_MIN_GAMES_FACTOR


TYPE_MIN_GAMES_FACTOR = (
    10  # PRODUCT: a type's tour needs `min_games` x this many games (default 50) to set its all-time Elo
)

DEFAULT_RULES = RatingRules()


@dataclass(frozen=True, slots=True)
class Game:
    """One air-to-air kill: who won, who lost, and the pool of each one's aircraft."""

    winner: int  # player id
    winner_pool: Pool
    loser: int
    loser_pool: Pool
    winner_aircraft: int = 0  # GameObject id of the winner's aircraft type (0: unknown, only the pool ratings count)
    loser_aircraft: int = 0
    winner_sortie: int = 0  # id of the winner's sortie (0: unknown); `AllRatings.peaks` reports ratings per sortie


@dataclass(frozen=True, slots=True)
class Rating:
    rating: float
    games: int


type RatingKey = tuple[int, Pool]  # (player id, pool)
type TypeKey = tuple[int, int]  # (player id, aircraft type id)


@dataclass(frozen=True, slots=True)
class AllRatings:
    """The pool ratings and the per-type ratings of one replay; pairs without a rating-changing game are absent."""

    pools: dict[RatingKey, Rating]
    types: dict[TypeKey, Rating]
    peaks: dict[int, float]
    """Winner sortie id -> the highest pool rating its winner held right after one of that sortie's games (achievements,
    doc 17). A loser's rating only falls, so peaks come from wins; sorties without a rating-changing win are absent."""


def expected_score(rating: float, opponent: float) -> float:
    """Probability that the first rating beats the second."""
    return 1.0 / (1.0 + 10.0 ** ((opponent - rating) / ELO_SCALE))


def compute_ratings(games: Iterable[Game], rules: RatingRules) -> dict[RatingKey, Rating]:
    """Rating and number of games per `(player, pool)`, for every pair that played at least one rating-changing game.

    Pairs without a game are not in the result: their rating is `rules.start` with 0 games."""
    return compute_all_ratings(games, rules).pools


def _weight(game: Game, rules: RatingRules) -> float | None:
    """How much a game moves ratings (the pool rules); None = it moves nothing (a jet beating a prop aircraft)."""
    if game.winner_pool == "jet" and game.loser_pool == "prop":
        return None  # a jet beating a prop aircraft is the expected result
    return rules.cross_pool_weight if game.winner_pool == "prop" and game.loser_pool == "jet" else 1.0


def compute_all_ratings(games: Iterable[Game], rules: RatingRules) -> AllRatings:
    """The pool ratings and the per-type ratings in one pass over the (chronological) games. A game whose side has no
    aircraft type (id 0) leaves that side's per-type rating alone."""
    pools = _Elo[RatingKey](rules)
    types = _Elo[TypeKey](rules)
    peaks: dict[int, float] = {}
    for game in games:
        if game.winner == game.loser:
            continue
        weight = _weight(game, rules)
        if weight is None:
            continue
        after = pools.play((game.winner, game.winner_pool), (game.loser, game.loser_pool), weight)
        if game.winner_sortie:
            peaks[game.winner_sortie] = max(peaks.get(game.winner_sortie, after), after)
        if game.winner_aircraft and game.loser_aircraft:
            types.play((game.winner, game.winner_aircraft), (game.loser, game.loser_aircraft), weight)
    return AllRatings(pools.result(), types.result(), peaks)


def compute_type_ratings(games: Iterable[Game], rules: RatingRules) -> dict[int, Rating]:
    """The rating and the number of games of each aircraft type that played at least one rating-changing game: every
    game is won by the winner's type against the loser's type, with the pool rules of the pilot ratings. Games between
    pilots of one player, with an unknown aircraft type on a side or between two aircraft of one type move nothing (a
    type cannot gain from beating itself). Types without a game are not in the result (`rules.start`, 0 games)."""
    types = _Elo[int](rules)
    for game in games:
        if game.winner == game.loser or not (game.winner_aircraft and game.loser_aircraft):
            continue
        if game.winner_aircraft == game.loser_aircraft:
            continue
        weight = _weight(game, rules)
        if weight is not None:
            types.play(game.winner_aircraft, game.loser_aircraft, weight)
    return types.result()


def best_of_tours[K: Hashable](rows: Iterable[tuple[K, float, int]], min_games: int = 1) -> dict[K, Rating]:
    """The all-time rating of each key from its per-tour (key, final rating, games) rows of rated games: the highest
    final rating among the tours with at least `min_games` games (one game at least), the highest of all its tours when
    none reaches that (the key then has no board standing anyway), with the games of all tours added up. A rating
    after one or two games in a new tour is noise, so it must not beat a season of games."""
    minimum = max(min_games, 1)
    best: dict[K, float] = {}  # among the tours that reach the minimum
    anything: dict[K, float] = {}
    games_of: dict[K, int] = {}
    for key, rating, games in rows:
        anything[key] = max(rating, anything.get(key, rating))
        games_of[key] = games_of.get(key, 0) + games
        if games >= minimum:
            best[key] = max(rating, best.get(key, rating))
    return {key: Rating(best.get(key, anything[key]), games) for key, games in games_of.items()}


class _Elo[K]:
    """Ratings and game counts of one kind of key (a pool or an aircraft type)."""

    def __init__(self, rules: RatingRules) -> None:
        self._rules = rules
        self._ratings: dict[K, float] = {}
        self._counts: dict[K, int] = {}

    def play(self, won: K, lost: K, weight: float) -> float:
        """One game; returns the winner's new rating."""
        start = self._rules.start
        winner_rating = self._ratings.get(won, start)
        loser_rating = self._ratings.get(lost, start)
        change = self._rules.k * weight * (1.0 - expected_score(winner_rating, loser_rating))
        self._ratings[won] = winner_rating + change
        self._ratings[lost] = loser_rating - change
        self._counts[won] = self._counts.get(won, 0) + 1
        self._counts[lost] = self._counts.get(lost, 0) + 1
        return self._ratings[won]

    def result(self) -> dict[K, Rating]:
        return {key: Rating(rating, self._counts[key]) for key, rating in self._ratings.items()}
