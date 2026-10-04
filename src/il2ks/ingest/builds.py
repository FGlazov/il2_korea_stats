"""Level-2 favourite loadout per player and aircraft type: `PlayerAircraftBuild` (FR-WEB-4, doc 16, OQ-117).

Counted pilot sorties grouped by (player, aircraft, scope) where the scope is all time (`tour` NULL) or one tour, like
`PlayerAircraft` / `PlayerTourAircraft`: the loadout (`payload_id` and its name) by sorties. (The weapon-mod sets and
the gun ammo mix that used to be kept here were dropped: the profile shows the favourite loadout only. The weapon mods
live on the aircraft page, `ingest.aircraft_stats`.)

One GROUP BY over the sortie columns. Always recomputed for the touched players, never adjusted by deltas, so
incremental == rebuild by construction. `aggregates.recompute_players` calls `recompute_builds`.
"""

from collections import defaultdict

from django.db.models import Count

from il2ks.db.models import BuildKind, PlayerAircraftBuild
from il2ks.ingest.counters import counted_sorties
from il2ks.ingest.dbutil import update_rows

type _Key = tuple[int, int, int | None, int, str]  # player, aircraft, tour (None = all time), payload id, name


def recompute_builds(chunk: list[int], tour_ids: list[int] | None) -> None:
    """`PlayerAircraftBuild` for these players: all-time rows always, per-tour rows limited to `tour_ids` unless that is
    None. Rows with nothing left are deleted."""
    wanted: dict[_Key, int] = defaultdict(int)
    sorties = counted_sorties().filter(player_id__in=chunk)
    for row in sorties.values("player_id", "aircraft_id", "mission__tour_id", "payload_id", "payload_name").annotate(
        n=Count("pk")
    ):
        tour = row["mission__tour_id"]
        scopes: tuple[int | None, ...] = (None,)
        if tour is not None and (tour_ids is None or tour in tour_ids):
            scopes = (None, tour)
        for scope in scopes:
            wanted[(row["player_id"], row["aircraft_id"], scope, row["payload_id"], row["payload_name"])] += row["n"]

    payload = BuildKind.PAYLOAD.value
    existing_rows = PlayerAircraftBuild.objects.filter(player_id__in=chunk)
    if tour_ids is not None:
        existing_rows = existing_rows.filter(tour_id__isnull=True) | existing_rows.filter(tour_id__in=tour_ids)
    existing = {
        (r.player_id, r.aircraft_id, r.tour_id, r.value, r.label): r for r in existing_rows if r.kind == payload
    }
    stale = [r.pk for r in existing_rows if r.kind != payload]
    changed: list[PlayerAircraftBuild] = []
    new: list[PlayerAircraftBuild] = []
    for key, n in wanted.items():
        row = existing.pop(key, None)
        if row is None:
            player_id, aircraft_id, tour_id, value, label = key
            new.append(
                PlayerAircraftBuild(
                    player_id=player_id,
                    aircraft_id=aircraft_id,
                    tour_id=tour_id,
                    kind=payload,
                    value=value,
                    label=label,
                    sorties=n,
                )
            )
        elif row.sorties != n:
            row.sorties = n
            changed.append(row)
    PlayerAircraftBuild.objects.filter(pk__in=[r.pk for r in existing.values()] + stale).delete()
    update_rows(PlayerAircraftBuild, changed, ["sorties"])
    PlayerAircraftBuild.objects.bulk_create(new)
