"""The games of the Elo replays (OQ-28, FR-WEB-19): the qualifying kills of one tour, shared by the pilot ratings
(`ingest.ratings`) and the aircraft type ratings (`ingest.aircraft_stats`), so both replay exactly the same games.

A game is a `Kill` row that is a `kill` credit (not an assist, not shared), not friendly, whose killer and victim
sorties are both pilot sorties with the air superiority combat role. The pool of each side is its aircraft's
propulsion; a game with an unknown propulsion on either side is skipped, and so is a game of a mission without a tour
(until the next rebuild gives it one). Kills of a provisional (still running) mission are not games yet (FR-ING-15)."""

from collections.abc import Iterator

from il2ks.core.catalog.loader import is_propulsion
from il2ks.core.ratings.elo import Game
from il2ks.db.models import CombatRole, Kill, KillCredit, Role


def rated_games(tour_id: int) -> Iterator[Game]:
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
