"""Air-to-air Elo ratings on `Player` and, per aircraft type, on `PlayerAircraft` (OQ-28, OQ-49, FR-WEB-19). All
aggregation happens in `ingest`; the maths is in `il2ks.core.ratings.elo`.

Ratings depend on the order of the games, so they are not a per-player recompute like the level-2 counters:
`recompute_ratings` replays every qualifying kill, oldest first, and writes the players whose stored ratings differ from
the result. It is the one code path for `save_mission`, `reprocess` and `rebuild-aggregates`, so incremental == rebuild
by construction, and running it twice changes nothing.

A game is a `Kill` row that is a `kill` credit (not an assist, not shared), not friendly, whose killer and victim
sorties are both pilot sorties with the air superiority combat role. The pool of each side is its aircraft's
propulsion; a game with an unknown propulsion on either side is skipped.
"""

from collections.abc import Iterator

from il2ks.core.catalog.loader import is_propulsion
from il2ks.core.ratings.elo import DEFAULT_RULES, Game, RatingRules, compute_all_ratings
from il2ks.db.models import CombatRole, Kill, KillCredit, Player, PlayerAircraft, PlayerSortie, Role
from il2ks.ingest.achievements import recompute_achievements
from il2ks.ingest.dbutil import update_partial_rows

CHUNK = 400  # players per achievement batch (SQLite's bound-parameter limit)


def recompute_ratings(rules: RatingRules = DEFAULT_RULES) -> int:
    """Replay all qualifying kills once and update `Player.elo_*` and `PlayerAircraft.elo*` (the rating in each aircraft
    type). Returns the number of games that were replayed.

    Players and player-aircraft rows with no game get `rules.start` and 0 games (so a changed `start` is applied to
    them too). Only rows whose stored values differ from the result are written."""
    games = list(_games())
    computed = compute_all_ratings(games, rules)
    ratings = computed.pools

    changed: list[Player] = []
    stored = Player.objects.values_list("pk", "elo_prop", "elo_prop_games", "elo_jet", "elo_jet_games")
    for pk, prop, prop_games, jet, jet_games in stored:
        want_prop = ratings.get((pk, "prop"))
        want_jet = ratings.get((pk, "jet"))
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

    changed_types: list[PlayerAircraft] = []
    for pk, player_id, aircraft_id, elo, elo_games in PlayerAircraft.objects.values_list(
        "pk", "player_id", "aircraft_id", "elo", "elo_games"
    ):
        found = computed.types.get((player_id, aircraft_id))
        wanted_type = (rules.start, 0) if found is None else (found.rating, found.games)
        if wanted_type != (elo, elo_games):
            changed_types.append(PlayerAircraft(pk=pk, elo=wanted_type[0], elo_games=wanted_type[1]))
    update_partial_rows(PlayerAircraft, changed_types, ["elo", "elo_games"])
    _store_peaks(computed.peaks, {g.winner_sortie: g.winner for g in games})
    return len(games)


def _store_peaks(peaks: dict[int, float], winners: dict[int, int]) -> None:
    """Write `PlayerSortie.elo_peak` (the Elo medal reads it) where it differs, then recompute the medals of every pilot
    whose sorties changed. The holder counts are the caller's to recompute afterwards
    (`recompute_holders`, once per save or rebuild). Incremental == rebuild: the peaks are a pure function of the replayed games."""
    stored = dict(PlayerSortie.objects.filter(elo_peak__gt=0).values_list("pk", "elo_peak"))
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
        recompute_achievements(ids[start : start + CHUNK])


def _games() -> Iterator[Game]:
    """Qualifying kills as games, in chronological order (mission start, kill time, row id)."""
    kills = (
        Kill.objects.filter(
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
