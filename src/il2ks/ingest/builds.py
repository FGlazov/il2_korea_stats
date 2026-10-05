"""Level-2 favourite loadout per player and aircraft type: `PlayerAircraftBuild` (FR-WEB-4, doc 16, OQ-117).

Counted pilot sorties grouped by (player, aircraft, tour, loadout): the loadout (`payload_id` and its name) by sorties.
The per-tour rows (`tour` set) come from that tour's level-1 sorties only; the all-time rows (`tour` NULL) are the SUM
of `sorties` per (player, aircraft, loadout) over the player's tour rows (`ingest.rollup`), never a read of level 1.
(The weapon-mod sets and the gun ammo mix that used to be kept here were dropped: the profile shows the favourite
loadout only. The weapon mods live on the aircraft page, `ingest.aircraft_stats`.)

Always recomputed for the touched players, never adjusted by deltas, so incremental == rebuild by construction.
`aggregates.recompute_players` calls `recompute_builds` (per tour) and `rollup_builds` (all time).
"""

from django.db.models import Count

from il2ks.db.models import BuildKind, PlayerAircraftBuild
from il2ks.ingest.counters import counted_sorties
from il2ks.ingest.dbutil import delete_pks, update_rows
from il2ks.ingest.rollup import rollup

type _Key = tuple[int, int, int, int, str]  # player, aircraft, tour, payload id, name

_PAYLOAD = BuildKind.PAYLOAD.value


def recompute_builds(chunk: list[int], tour_ids: list[int] | None) -> None:
    """The per-tour `PlayerAircraftBuild` rows for these players, limited to `tour_ids` unless that is None, from the
    tours' level-1 sorties. Rows with nothing left are deleted."""
    sorties = counted_sorties().filter(player_id__in=chunk, mission__tour_id__isnull=False)
    existing_rows = PlayerAircraftBuild.objects.filter(player_id__in=chunk, tour_id__isnull=False)
    if tour_ids is not None:
        sorties = sorties.filter(mission__tour_id__in=tour_ids)
        existing_rows = existing_rows.filter(tour_id__in=tour_ids)
    wanted: dict[_Key, int] = {
        (row["player_id"], row["aircraft_id"], row["mission__tour_id"], row["payload_id"], row["payload_name"]): row[
            "n"
        ]
        for row in sorties.values(
            "player_id", "aircraft_id", "mission__tour_id", "payload_id", "payload_name"
        ).annotate(n=Count("pk"))
    }
    existing = {
        (r.player_id, r.aircraft_id, r.tour_id, r.value, r.label): r for r in existing_rows if r.kind == _PAYLOAD
    }
    stale = [r.pk for r in existing_rows if r.kind != _PAYLOAD]
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
                    kind=_PAYLOAD,
                    value=value,
                    label=label,
                    sorties=n,
                )
            )
        elif row.sorties != n:
            row.sorties = n
            changed.append(row)
    delete_pks(PlayerAircraftBuild.objects, [r.pk for r in existing.values()] + stale)
    update_rows(PlayerAircraftBuild, changed, ["sorties"])
    PlayerAircraftBuild.objects.bulk_create(new)


def rollup_builds(chunk: list[int]) -> None:
    """The all-time rows of these players: the SUM of their per-tour rows per (aircraft, loadout)."""
    PlayerAircraftBuild.objects.filter(player_id__in=chunk, tour_id__isnull=True).exclude(kind=_PAYLOAD).delete()
    rollup(
        PlayerAircraftBuild,
        PlayerAircraftBuild.objects.filter(player_id__in=chunk, tour_id__isnull=True, kind=_PAYLOAD),
        PlayerAircraftBuild.objects.filter(player_id__in=chunk, tour_id__isnull=False, kind=_PAYLOAD),
        key=("player_id", "aircraft_id", "value", "label"),
        sums=("sorties",),
        fixed={"kind": _PAYLOAD, "tour_id": None},
    )
