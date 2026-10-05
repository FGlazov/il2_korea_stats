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

from collections.abc import Iterable
from typing import cast

from il2ks.core.ratings.elo import DEFAULT_RULES, AllRatings, Pool, RatingRules, best_of_tours, compute_all_ratings
from il2ks.db.models import (
    Player,
    PlayerAircraft,
    PlayerSortie,
    PlayerTourAircraft,
    PlayerTourPool,
    Tour,
)
from il2ks.ingest.aircraft_stats import recompute_payload_elo
from il2ks.ingest.dbutil import update_partial_rows, update_rows
from il2ks.ingest.rating_games import rated_games

POOLS: tuple[Pool, ...] = ("prop", "jet")


def recompute_ratings(
    rules: RatingRules = DEFAULT_RULES,
    tour_ids: Iterable[int] | None = None,
    *,
    payload_elo: bool = True,
    all_time: bool = True,
) -> int:
    """Replay the qualifying kills of each tour in `tour_ids` (None = every tour) alone and update that tour's rows
    (`PlayerTourPool.elo*`, `PlayerTourAircraft.elo*`, `PlayerSortie.elo_peak`), then derive the all-time Elo
    (`Player.elo_*`, `PlayerAircraft.elo*`) from the tour rows of every player (`rollup_ratings`; with
    `all_time=False` the caller does that, for the players it refreshed only). Returns the number of games replayed.

    Rows with no game get `rules.start` and 0 games (so a changed `start` is applied to them too). Only rows whose
    stored values differ from the result are written.

    The medals that read the peaks (Top Rated) are not recomputed here: `aggregates.recompute_players` refreshes each
    tour's medals after this replay. `payload_elo=False`: leave the loadouts' average pilot Elo to the caller (it needs
    the aircraft rows of the same refresh, which come later)."""
    ids = sorted(Tour.objects.values_list("pk", flat=True) if tour_ids is None else set(tour_ids))
    replayed = 0
    for tour_id in ids:
        games = list(rated_games(tour_id))
        computed = compute_all_ratings(games, rules)
        _store_tour_pools(tour_id, computed, rules)
        _store_tour_types(tour_id, computed, rules)
        _store_peaks(tour_id, computed.peaks)
        replayed += len(games)
    if tour_ids is None:  # a rebuild also clears the peaks of sorties outside every tour (no game explains them)
        _store_peaks(None, {})
    if all_time:
        rollup_ratings(rules)
    if payload_elo:
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


def rollup_ratings(rules: RatingRules, player_ids: Iterable[int] | None = None) -> None:
    """`Player.elo_*` and `PlayerAircraft.elo*` of these players (None = everyone) from their per-tour rows (rated
    games only): the best tour's final rating, the games of all tours summed. Only the players' own rows are read, so
    a refresh costs what its players' tours cost, not the number of rated rows of the whole site."""
    if player_ids is None:
        _store_all_time(rules, None)
        return
    ids = sorted(set(player_ids))
    for start in range(0, len(ids), _CHUNK):
        _store_all_time(rules, ids[start : start + _CHUNK])


_CHUNK = 2000  # players per query: far below the bound-parameter limit, as aggregates.CHUNK


def _store_all_time(rules: RatingRules, player_ids: list[int] | None) -> None:
    rated_pools = PlayerTourPool.objects.filter(elo_games__gt=0)
    players = Player.objects.all()
    aircraft = PlayerAircraft.objects.all()
    rated_types = PlayerTourAircraft.objects.filter(elo_games__gt=0)
    if player_ids is not None:
        rated_pools = rated_pools.filter(player_id__in=player_ids)
        players = players.filter(pk__in=player_ids)
        aircraft = aircraft.filter(player_id__in=player_ids)
        rated_types = rated_types.filter(player_id__in=player_ids)
    pools = {
        pool: best_of_tours(
            rated_pools.filter(propulsion=pool).values_list("player_id", "elo", "elo_games"), rules.min_games
        )
        for pool in POOLS
    }
    changed: list[Player] = []
    stored = players.values_list("pk", "elo_prop", "elo_prop_games", "elo_jet", "elo_jet_games")
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
        [
            ((player_id, aircraft_id), elo, games)
            for player_id, aircraft_id, elo, games in rated_types.values_list(
                "player_id", "aircraft_id", "elo", "elo_games"
            )
        ],
        rules.min_games,
    )
    changed_types: list[PlayerAircraft] = []
    for pk, player_id, aircraft_id, elo, elo_games in aircraft.values_list(
        "pk", "player_id", "aircraft_id", "elo", "elo_games"
    ):
        found = types.get((player_id, aircraft_id))
        wanted_type = (rules.start, 0) if found is None else (found.rating, found.games)
        if wanted_type != (elo, elo_games):
            changed_types.append(PlayerAircraft(pk=pk, elo=wanted_type[0], elo_games=wanted_type[1]))
    update_partial_rows(PlayerAircraft, changed_types, ["elo", "elo_games"])


def _store_peaks(tour_id: int | None, peaks: dict[int, float]) -> None:
    """Write `PlayerSortie.elo_peak` (the Elo medal reads it) of the sorties of one tour (None: of no tour) where it
    differs. The medals of the pilots whose peaks changed are recomputed by the caller's refresh, after the replay
    (`aggregates.recompute_players`: the tour's medals, then the all-time roll-up). Incremental == rebuild: the peaks
    are a pure function of the tour's replayed games."""
    scope = PlayerSortie.objects.filter(mission__tour_id=tour_id)
    stored = dict(scope.filter(elo_peak__gt=0).values_list("pk", "elo_peak"))
    changed = [
        PlayerSortie(pk=pk, elo_peak=peaks.get(pk, 0.0))
        for pk in sorted(stored.keys() | peaks.keys())
        if stored.get(pk, 0.0) != peaks.get(pk, 0.0)
    ]
    update_partial_rows(PlayerSortie, changed, ["elo_peak"])
