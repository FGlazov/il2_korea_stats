"""Player identity from the sorties, per tour and rolled up (`Player.first_seen`, `last_seen`, `current_name`,
`name_lower`, the `PlayerName` nickname history; doc 14 "Level 2 is per tour").

Per tour (`recompute_tour_names`): one `PlayerTourName` row per (player, tour, name used), from the sorties of ALL roles
in that tour's missions (a gunner-only player has identity rows though they have no `PlayerTour`): the earliest spawn,
the latest sortie end and the latest spawn under that name. It reads only the tours' sorties.

All time (`rollup_identity`): `first_seen` = MIN and `last_seen` = MAX over the player's tour rows, `current_name` = the
name with the latest `last_spawn` (one account never spawns twice at the same instant: the sortie key has a unique tick
per mission), `PlayerName` = one row per distinct name with MIN / MAX of the same. It never reads `PlayerSortie`.

A player without a tour row keeps the identity values they had and their nickname history (their row and URL stay,
FR-WEB-13). Players are never deleted.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import cast

from django.db.models import Max, Min, QuerySet

from il2ks.db.models import Player, PlayerName, PlayerSortie, PlayerTourName
from il2ks.ingest.dbutil import update_rows
from il2ks.ingest.rollup import Row, rollup

type _Key = tuple[int, int, str]  # player, tour, name
type _Values = tuple[datetime, datetime, datetime]  # first spawn, last end, last spawn


def tour_sorties(chunk: list[int], tour_ids: list[int] | None) -> QuerySet[PlayerSortie]:
    """The sorties (any role) of these players in the missions of `tour_ids` (None = every tour)."""
    sorties = PlayerSortie.objects.filter(player_id__in=chunk, mission__tour_id__isnull=False)
    return sorties if tour_ids is None else sorties.filter(mission__tour_id__in=tour_ids)


def recompute_tour_names(chunk: list[int], tour_ids: list[int] | None) -> None:
    """`PlayerTourName` for these players, limited to `tour_ids` unless that is None, from the tours' sorties."""
    sorties = tour_sorties(chunk, tour_ids)
    existing_rows = PlayerTourName.objects.filter(player_id__in=chunk)
    if tour_ids is not None:
        existing_rows = existing_rows.filter(tour_id__in=tour_ids)
    wanted: dict[_Key, _Values] = {
        (row["player_id"], row["mission__tour_id"], row["name_at_time"]): (row["first"], row["last"], row["spawned"])
        for row in sorties.values("player_id", "mission__tour_id", "name_at_time").annotate(
            first=Min("spawned_at"), last=Max("ended_at"), spawned=Max("spawned_at")
        )
    }
    existing = {(r.player_id, r.tour_id, r.name): r for r in existing_rows}
    changed: list[PlayerTourName] = []
    new: list[PlayerTourName] = []
    for key, (first, last, spawned) in wanted.items():
        row = existing.pop(key, None)
        if row is None:
            new.append(
                PlayerTourName(
                    player_id=key[0], tour_id=key[1], name=key[2], first_seen=first, last_seen=last, last_spawn=spawned
                )
            )
        elif (row.first_seen, row.last_seen, row.last_spawn) != (first, last, spawned):
            row.first_seen, row.last_seen, row.last_spawn = first, last, spawned
            changed.append(row)
    PlayerTourName.objects.filter(pk__in=[r.pk for r in existing.values()]).delete()  # no sortie under this name left
    update_rows(PlayerTourName, changed, ["first_seen", "last_seen", "last_spawn"])
    PlayerTourName.objects.bulk_create(new)


def rollup_identity(chunk: list[int]) -> None:
    """The identity fields and the `PlayerName` history of these players from their `PlayerTourName` rows."""
    with_rows = sorted(
        PlayerTourName.objects.filter(player_id__in=chunk).values_list("player_id", flat=True).distinct()
    )
    source = PlayerTourName.objects.filter(player_id__in=with_rows)

    def latest(rows: Sequence[Row]) -> str:
        return cast(str, max(rows, key=lambda row: cast(datetime, row["last_spawn"]))["name"])

    rollup(
        Player,
        Player.objects.filter(pk__in=with_rows),
        source,
        key=("player_id",),
        model_key=("pk",),
        mins=("first_seen",),
        maxes=("last_seen",),
        derived={"current_name": latest, "name_lower": lambda rows: latest(rows).lower()},
        source_fields=("name", "last_spawn"),
        update_only=True,
    )
    rollup(
        PlayerName,
        PlayerName.objects.filter(player_id__in=with_rows),
        source,
        key=("player_id", "name"),
        mins=("first_seen",),
        maxes=("last_seen",),
        derived={"name_lower": lambda rows: cast(str, rows[0]["name"]).lower()},
    )
