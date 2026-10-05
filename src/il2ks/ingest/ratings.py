"""Air-to-air Elo, per tour (OQ-28, OQ-49, OQ-128, FR-WEB-19). All aggregation happens in `ingest`; the maths is in
`il2ks.core.ratings.elo`.

A new tour is a clean slate (maintainer, 2026-10-05): ratings are never carried from one tour to the next. Every tour is
replayed alone, oldest game first, with everyone starting at `[ratings] start`; the result is the tour's *final* rating
(not its peak) on `PlayerTourPool` (per pool) and `PlayerTourAircraft` (per aircraft type). The all-time Elo is derived
from those rows: `Player.elo_*` / `PlayerAircraft.elo` = the highest final rating of any tour, `*_games` = the games of
all tours added up (the sample size behind the minimum-games rules). `PlayerSortie.elo_peak` (the Top Rated medal) is
the highest rating held right after a win, from the sortie's own tour replay.

Because a tour's games are independent of every other tour, `recompute_ratings(tour_ids)` replays only those tours (a
late import into an old tour changes that tour and the all-time maxima, nothing else), and `tour_ids=None` replays all
of them (a rebuild). It is the one code path for `save_mission`, `reprocess` and `rebuild-aggregates`, so incremental ==
rebuild by construction, and running it twice changes nothing.

A game is a `Kill` row that is a `kill` credit (not an assist, not shared), not friendly, whose killer and victim
sorties are both pilot sorties with the air superiority combat role. The pool of each side is its aircraft's
propulsion; a game with an unknown propulsion on either side is skipped, and so is a game of a mission without a tour
(until the next rebuild gives it one). Kills of a provisional (still running) mission are not games yet (FR-ING-15):
the ratings only move when the mission's final save has cleared `is_live`.
"""

from collections.abc import Hashable, Iterable, Iterator
from typing import cast

from il2ks.core.catalog.loader import is_propulsion
from il2ks.core.ratings.elo import DEFAULT_RULES, AllRatings, Game, Pool, Rating, RatingRules, compute_all_ratings
from il2ks.db.models import (
    CombatRole,
    Kill,
    KillCredit,
    Player,
    PlayerAircraft,
    PlayerSortie,
    PlayerTourAircraft,
    PlayerTourPool,
    Role,
    Tour,
)
from il2ks.ingest.achievements import recompute_achievements
from il2ks.ingest.aircraft_stats import recompute_payload_elo
from il2ks.ingest.dbutil import update_partial_rows, update_rows

CHUNK = 400  # players per achievement batch (SQLite's bound-parameter limit)
POOLS: tuple[Pool, ...] = ("prop", "jet")


def recompute_ratings(rules: RatingRules = DEFAULT_RULES, tour_ids: Iterable[int] | None = None) -> int:
    """Replay the qualifying kills of each tour in `tour_ids` (None = every tour) alone and update that tour's rows
    (`PlayerTourPool.elo*`, `PlayerTourAircraft.elo*`, `PlayerSortie.elo_peak`), then derive the all-time Elo
    (`Player.elo_*`, `PlayerAircraft.elo*`) from the rows of every tour. Returns the number of games replayed.

    Rows with no game get `rules.start` and 0 games (so a changed `start` is applied to them too). Only rows whose
    stored values differ from the result are written."""
    ids = sorted(Tour.objects.values_list("pk", flat=True) if tour_ids is None else set(tour_ids))
    replayed = 0
    for tour_id in ids:
        games = list(_games(tour_id))
        computed = compute_all_ratings(games, rules)
        _store_tour_pools(tour_id, computed, rules)
        _store_tour_types(tour_id, computed, rules)
        _store_peaks(tour_id, computed.peaks, {g.winner_sortie: g.winner for g in games})
        replayed += len(games)
    if tour_ids is None:  # a rebuild also clears the peaks of sorties outside every tour (no game explains them)
        _store_peaks(None, {}, {})
    _store_all_time(rules)
    recompute_payload_elo()  # the loadouts' average pilot Elo follows the new ratings
    return replayed


def _store_tour_pools(tour_id: int, computed: AllRatings, rules: RatingRules) -> None:
    changed: list[PlayerTourPool] = []
    for row in PlayerTourPool.objects.filter(tour_id=tour_id):
        found = computed.pools.get((row.player_id, cast("Pool", row.propulsion)))
        wanted = (rules.start, 0) if found is None else (found.rating, found.games)
        if wanted != (row.elo, row.elo_games):
            row.elo, row.elo_games = wanted
            changed.append(row)
    update_rows(PlayerTourPool, changed, ["elo", "elo_games"])


def _store_tour_types(tour_id: int, computed: AllRatings, rules: RatingRules) -> None:
    changed: list[PlayerTourAircraft] = []
    for row in PlayerTourAircraft.objects.filter(tour_id=tour_id):
        found = computed.types.get((row.player_id, row.aircraft_id))
        wanted = (rules.start, 0) if found is None else (found.rating, found.games)
        if wanted != (row.elo, row.elo_games):
            row.elo, row.elo_games = wanted
            changed.append(row)
    update_rows(PlayerTourAircraft, changed, ["elo", "elo_games"])


def best_of_tours[K: Hashable](rows: Iterable[tuple[K, float, int]]) -> dict[K, Rating]:
    """The all-time rating of each key from its per-tour (key, final rating, games) rows of rated games: the highest
    tour rating, with the games of all tours added up."""
    found: dict[K, Rating] = {}
    for key, rating, games in rows:
        old = found.get(key)
        found[key] = Rating(rating, games) if old is None else Rating(max(old.rating, rating), old.games + games)
    return found


def _store_all_time(rules: RatingRules) -> None:
    """`Player.elo_*` and `PlayerAircraft.elo*` from the per-tour rows of every tour (rows with rated games only)."""
    rated_pools = PlayerTourPool.objects.filter(elo_games__gt=0)
    pools = {
        pool: best_of_tours(rated_pools.filter(propulsion=pool).values_list("player_id", "elo", "elo_games"))
        for pool in POOLS
    }
    changed: list[Player] = []
    stored = Player.objects.values_list("pk", "elo_prop", "elo_prop_games", "elo_jet", "elo_jet_games")
    for pk, prop, prop_games, jet, jet_games in stored:
        want_prop = pools["prop"].get(pk)
        want_jet = pools["jet"].get(pk)
        wanted = (
            rules.start if want_prop is None else want_prop.rating,
            0 if want_prop is None else want_prop.games,
            rules.start if want_jet is None else want_jet.rating,
            0 if want_jet is None else want_jet.games,
        )
        if wanted != (prop, prop_games, jet, jet_games):
            changed.append(
                Player(
                    pk=pk,
                    elo_prop=wanted[0],
                    elo_prop_games=wanted[1],
                    elo_jet=wanted[2],
                    elo_jet_games=wanted[3],
                )
            )
    update_partial_rows(Player, changed, ["elo_prop", "elo_prop_games", "elo_jet", "elo_jet_games"])

    types = best_of_tours(
        ((player_id, aircraft_id), elo, games)
        for player_id, aircraft_id, elo, games in PlayerTourAircraft.objects.filter(elo_games__gt=0).values_list(
            "player_id", "aircraft_id", "elo", "elo_games"
        )
    )
    changed_types: list[PlayerAircraft] = []
    for pk, player_id, aircraft_id, elo, elo_games in PlayerAircraft.objects.values_list(
        "pk", "player_id", "aircraft_id", "elo", "elo_games"
    ):
        found = types.get((player_id, aircraft_id))
        wanted_type = (rules.start, 0) if found is None else (found.rating, found.games)
        if wanted_type != (elo, elo_games):
            changed_types.append(PlayerAircraft(pk=pk, elo=wanted_type[0], elo_games=wanted_type[1]))
    update_partial_rows(PlayerAircraft, changed_types, ["elo", "elo_games"])


def _store_peaks(tour_id: int | None, peaks: dict[int, float], winners: dict[int, int]) -> None:
    """Write `PlayerSortie.elo_peak` (the Elo medal reads it) of the sorties of one tour (None: of no tour) where it
    differs, then recompute the medals of every pilot whose sorties changed, all time and in that tour only (the other
    tours' medals do not read this tour's peaks). The holder counts are the caller's to recompute afterwards
    (`recompute_holders`, once per save or rebuild). Incremental == rebuild: the peaks are a pure function of the
    tour's replayed games."""
    scope = PlayerSortie.objects.filter(mission__tour_id=tour_id)
    stored = dict(scope.filter(elo_peak__gt=0).values_list("pk", "elo_peak"))
    changed: list[PlayerSortie] = []
    players: set[int] = set()
    cleared: list[int] = []  # sorties whose peak goes: no game explains them, so `winners` does not know their pilot
    for pk in stored.keys() | peaks.keys():
        want = peaks.get(pk, 0.0)
        if stored.get(pk, 0.0) != want:
            changed.append(PlayerSortie(pk=pk, elo_peak=want))
            if pk in winners:
                players.add(winners[pk])
            else:
                cleared.append(pk)
    for start in range(0, len(cleared), CHUNK):
        players.update(
            PlayerSortie.objects.filter(pk__in=cleared[start : start + CHUNK]).values_list("player_id", flat=True)
        )
    update_partial_rows(PlayerSortie, changed, ["elo_peak"])
    ids = sorted(players)
    for start in range(0, len(ids), CHUNK):
        recompute_achievements(ids[start : start + CHUNK], [] if tour_id is None else [tour_id])


def _games(tour_id: int) -> Iterator[Game]:
    """The qualifying kills of the missions of one tour as games, in chronological order (mission start, kill time,
    row id)."""
    kills = (
        Kill.objects.filter(
            mission__tour_id=tour_id,
            mission__is_live=False,  # a running mission counts when it ends (FR-ING-15), not before
            credit=KillCredit.KILL,
            is_friendly=False,
            killer_sortie__role=Role.PILOT,
            victim_sortie__role=Role.PILOT,
            killer_sortie__combat_role=CombatRole.AIR_SUPERIORITY,
            victim_sortie__combat_role=CombatRole.AIR_SUPERIORITY,
        )
        .order_by("mission__started_at", "time", "pk")
        .values_list(
            "killer_sortie__player_id",
            "killer_sortie__aircraft__propulsion",
            "victim_sortie__player_id",
            "victim_sortie__aircraft__propulsion",
            "killer_sortie__aircraft_id",
            "victim_sortie__aircraft_id",
            "killer_sortie_id",
        )
    )
    for winner, winner_pool, loser, loser_pool, winner_aircraft, loser_aircraft, sortie in kills.iterator():
        if is_propulsion(winner_pool) and is_propulsion(loser_pool):
            yield Game(winner, winner_pool, loser, loser_pool, winner_aircraft, loser_aircraft, sortie)
