"""Level-2 aggregates: Player totals, PlayerName, PlayerAircraft (TD-08). All aggregation happens in `ingest`.

Level 2 is always recomputed from level 1, never adjusted by deltas: `recompute_players` rebuilds, for the players it
gets, their totals from their `PlayerMission` rows, their `PlayerAircraft` rows from their counted `PlayerSortie` rows,
and their identity fields from all their sorties. `save_mission` calls it for the players a mission touched (the old
and the new ones) and `rebuild_aggregates` for every player. It is the one code path, so incremental == rebuild by
construction. The counter list lives in `ingest.counters` so all paths use one definition (TD-16).

Players are handled in chunks: a few grouped queries per chunk, then writes only for rows whose values changed.

Cost: a player's recompute reads all of that player's level-1 rows, so it grows with their history. Iteration 2 bounds
it by tour (TD-26).

Identity fields (`Player.first_seen`, `last_seen`, `current_name`, `name_lower` and the `PlayerName` history) aren't
summed: they come from the player's sorties. A player without sorties keeps the identity values they had.
"""

from collections.abc import Iterable
from datetime import datetime

from django.db.models import Max, Min, Sum

from il2ks.db.models import Player, PlayerAircraft, PlayerMission, PlayerName, PlayerSortie
from il2ks.ingest.counters import COUNTER_FIELDS, SORTIE_COUNTERS, CounterValues, clean_counters, counted_sorties

CHUNK = 400  # players per batch: stays far below SQLite's bound-parameter limit


def recompute_players(player_ids: Iterable[int]) -> None:
    """Recompute level 2 for these players from level 1 (TD-08, FR-ING-9).

    - `Player` counters = sum of the player's `PlayerMission` rows (zero when there are none).
    - `PlayerAircraft` = counted sorties grouped by aircraft, upserted by `(player, aircraft)` so existing PKs stay;
      rows without counted sorties are deleted.
    - Identity fields and the `PlayerName` history from all sorties of any role (`_refresh_identity`).
    Players are never deleted."""
    ids = sorted(set(player_ids))
    for start in range(0, len(ids), CHUNK):
        chunk = ids[start : start + CHUNK]
        _recompute_totals(chunk)
        _recompute_aircraft(chunk)
        _refresh_identity(chunk)


def rebuild_aggregates() -> None:
    """Recompute every level-2 row from level 1 (`il2ks rebuild-aggregates`): `recompute_players` for all players."""
    recompute_players(Player.objects.values_list("pk", flat=True))


def _recompute_totals(chunk: list[int]) -> None:
    sums = {n: Sum(n) for n in COUNTER_FIELDS}
    totals = {
        row["player_id"]: row
        for row in PlayerMission.objects.filter(player_id__in=chunk).values("player_id").annotate(**sums)
    }
    changed: list[Player] = []
    for player in Player.objects.filter(pk__in=chunk):
        values = clean_counters(totals.get(player.pk, {}))
        if _assign(player, values):
            changed.append(player)
    Player.objects.bulk_update(changed, list(COUNTER_FIELDS))


def _recompute_aircraft(chunk: list[int]) -> None:
    wanted = {
        (row["player_id"], row["aircraft_id"]): clean_counters(row)
        for row in counted_sorties()
        .filter(player_id__in=chunk)
        .values("player_id", "aircraft_id")
        .annotate(**SORTIE_COUNTERS)
    }
    existing = {(r.player_id, r.aircraft_id): r for r in PlayerAircraft.objects.filter(player_id__in=chunk)}
    changed: list[PlayerAircraft] = []
    new: list[PlayerAircraft] = []
    for key, values in wanted.items():
        row = existing.pop(key, None)
        if row is None:
            new.append(PlayerAircraft(player_id=key[0], aircraft_id=key[1], **values))
        elif _assign(row, values):
            changed.append(row)
    PlayerAircraft.objects.filter(pk__in=[r.pk for r in existing.values()]).delete()  # no counted sorties left
    PlayerAircraft.objects.bulk_update(changed, list(COUNTER_FIELDS))
    PlayerAircraft.objects.bulk_create(new)


def _assign(row: Player | PlayerAircraft, values: CounterValues) -> bool:
    """Set the counter fields on `row`; True if any value changed."""
    changed = False
    for name, value in values.items():
        if getattr(row, name) != value:
            setattr(row, name, value)
            changed = True
    return changed


def _refresh_identity(chunk: list[int]) -> None:
    """Identity fields and nickname history from the players' sorties (any role).

    `first_seen` = earliest spawn, `last_seen` = latest sortie end, `current_name` = name of the latest spawn (one
    account never spawns twice at the same instant: the sortie key has a unique tick per mission). A player without
    sorties keeps the values they had (their row and URL stay, FR-WEB-13). `PlayerName` = one row per distinct name."""
    groups = (
        PlayerSortie.objects.filter(player_id__in=chunk)
        .values("player_id", "name_at_time")
        .annotate(first=Min("spawned_at"), last=Max("ended_at"), spawned=Max("spawned_at"))
    )
    by_player: dict[int, dict[str, tuple[datetime, datetime, datetime]]] = {}
    for row in groups:
        by_player.setdefault(row["player_id"], {})[row["name_at_time"]] = (row["first"], row["last"], row["spawned"])

    players = Player.objects.in_bulk(by_player)
    names = {(n.player_id, n.name): n for n in PlayerName.objects.filter(player_id__in=chunk)}
    changed_players: list[Player] = []
    changed_names: list[PlayerName] = []
    new_names: list[PlayerName] = []
    for player_id, seen in by_player.items():
        player = players[player_id]
        latest = max(seen, key=lambda name: seen[name][2])
        first_seen = min(first for first, _, _ in seen.values())
        last_seen = max(last for _, last, _ in seen.values())
        identity = (latest, latest.lower(), first_seen, last_seen)
        if identity != (player.current_name, player.name_lower, player.first_seen, player.last_seen):
            player.current_name, player.name_lower, player.first_seen, player.last_seen = identity
            changed_players.append(player)
        for name, (first, last, _) in seen.items():
            row = names.pop((player_id, name), None)
            if row is None:
                new_names.append(
                    PlayerName(
                        player_id=player_id, name=name, name_lower=name.lower(), first_seen=first, last_seen=last
                    )
                )
            elif (row.first_seen, row.last_seen) != (first, last):
                row.first_seen, row.last_seen = first, last
                changed_names.append(row)
    # Names left over belong to players with sorties that no longer use them; players without sorties keep theirs.
    PlayerName.objects.filter(pk__in=[n.pk for (pid, _), n in names.items() if pid in by_player]).delete()
    Player.objects.bulk_update(changed_players, ["current_name", "name_lower", "first_seen", "last_seen"])
    PlayerName.objects.bulk_update(changed_names, ["first_seen", "last_seen"])
    PlayerName.objects.bulk_create(new_names)
