"""Level-2 aggregates: Player totals, PlayerName, PlayerAircraft (TD-08). All aggregation happens in `ingest`.

Counters are updated incrementally (`add_mission` / `subtract_mission`) and can always be rebuilt from level 1
(`rebuild_aggregates`). The counter list lives in `ingest.counters` so all paths use one definition (TD-16).

Identity fields (`Player.first_seen`, `last_seen`, `current_name`, `name_lower` and the `PlayerName` history) aren't
summed: `refresh_players` recomputes them from the player's sorties. `add_mission` refreshes the players of the mission,
and `save_mission` refreshes players a re-ingest removed from a mission, so incremental == rebuild holds for them too.
"""

from collections.abc import Iterable
from datetime import datetime

from django.db.models import F, Max, Min, Sum

from il2ks.db.models import Mission, Player, PlayerAircraft, PlayerMission, PlayerName, PlayerSortie
from il2ks.ingest.counters import (
    COUNTER_FIELDS,
    SORTIE_COUNTERS,
    CounterValues,
    clean_counters,
    counted_sorties,
    zero_counters,
)


def add_mission(mission: Mission) -> None:
    """Add one mission's level-1 rows (PlayerMission / PlayerSortie) to the level-2 totals."""
    _apply_mission(mission, sign=1)
    refresh_players(PlayerSortie.objects.filter(mission=mission).values_list("player_id", flat=True))


def subtract_mission(mission: Mission, *, prune: bool = True) -> None:
    """Remove one mission's level-1 contribution from the level-2 totals (before a re-ingest).

    `prune` deletes PlayerAircraft rows left without counted sorties (a rebuild wouldn't create them). `save_mission`
    passes `prune=False` and prunes after adding the new contribution, so rows that still exist keep their PK.
    """
    players = _apply_mission(mission, sign=-1)
    if prune:
        prune_player_aircraft(players)


def prune_player_aircraft(player_ids: Iterable[int]) -> None:
    """Delete the players' PlayerAircraft rows that have no counted sortie left (also drops float residue)."""
    PlayerAircraft.objects.filter(player_id__in=set(player_ids), sorties=0).delete()


def rebuild_aggregates() -> None:
    """Recompute every level-2 table from level 1 (`il2ks rebuild-aggregates`). Must equal the incremental result."""
    Player.objects.update(**zero_counters())
    for row in PlayerMission.objects.values("player_id").annotate(**_sum_counters()):
        Player.objects.filter(pk=row["player_id"]).update(**clean_counters(row))

    PlayerAircraft.objects.all().delete()
    PlayerAircraft.objects.bulk_create(
        PlayerAircraft(player_id=row["player_id"], aircraft_id=row["aircraft_id"], **clean_counters(row))
        for row in counted_sorties().values("player_id", "aircraft_id").annotate(**SORTIE_COUNTERS)
    )
    refresh_players(Player.objects.values_list("pk", flat=True))


def refresh_players(player_ids: Iterable[int]) -> None:
    """Recompute identity fields and the nickname history from the players' sorties (any role).

    `first_seen` = earliest spawn, `last_seen` = latest sortie end, `current_name` = name of the latest spawn (ties: the
    higher sortie PK). A player without sorties keeps the values they had (their row and URL stay, FR-WEB-13).
    """
    for player_id in sorted(set(player_ids)):
        _refresh_player(player_id)


def _refresh_player(player_id: int) -> None:
    sorties = PlayerSortie.objects.filter(player_id=player_id)
    latest = sorties.order_by("-spawned_at", "-pk").values_list("name_at_time", flat=True).first()
    if latest is None:
        return
    span = sorties.aggregate(first=Min("spawned_at"), last=Max("ended_at"))
    first_seen: datetime = span["first"]
    last_seen: datetime = span["last"]
    Player.objects.filter(pk=player_id).update(
        current_name=latest, name_lower=latest.lower(), first_seen=first_seen, last_seen=last_seen
    )

    names = sorties.values("name_at_time").annotate(first=Min("spawned_at"), last=Max("ended_at"))
    seen: set[str] = set()
    for row in names:
        name: str = row["name_at_time"]
        seen.add(name)
        PlayerName.objects.update_or_create(
            player_id=player_id,
            name=name,
            defaults={"name_lower": name.lower(), "first_seen": row["first"], "last_seen": row["last"]},
        )
    PlayerName.objects.filter(player_id=player_id).exclude(name__in=seen).delete()


def _apply_mission(mission: Mission, sign: int) -> set[int]:
    """Add (sign 1) or subtract (sign -1) the mission's counters. Returns the PlayerAircraft players touched."""
    for pm in PlayerMission.objects.filter(mission=mission):
        values: CounterValues = {name: getattr(pm, name) for name in COUNTER_FIELDS}
        Player.objects.filter(pk=pm.player_id).update(**_delta_updates(values, sign))

    rows = counted_sorties().filter(mission=mission).values("player_id", "aircraft_id").annotate(**SORTIE_COUNTERS)
    players: set[int] = set()
    for row in rows:
        key = {"player_id": row["player_id"], "aircraft_id": row["aircraft_id"]}
        players.add(row["player_id"])
        if sign > 0:
            PlayerAircraft.objects.get_or_create(**key)
        PlayerAircraft.objects.filter(**key).update(**_delta_updates(clean_counters(row), sign))
    return players


def _delta_updates(values: CounterValues, sign: int) -> dict[str, F]:
    return {name: F(name) + sign * values[name] for name in COUNTER_FIELDS}


def _sum_counters() -> dict[str, Sum]:
    return {name: Sum(name) for name in COUNTER_FIELDS}
