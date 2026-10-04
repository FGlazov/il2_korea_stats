"""Level-2 "what do they fly with" tallies per player and aircraft type: `PlayerAircraftBuild` (FR-WEB-4, doc 16).

Counted pilot sorties grouped by (player, aircraft, scope) where the scope is all time (`tour` NULL) or one tour, like
`PlayerAircraft` / `PlayerTourAircraft`. Per group three kinds of rows:

- `PAYLOAD`: the loadout (`payload_id` and its name) by sorties,
- `MODS`: the `WM` weapon-modification bitmask by sorties (no names are known, OQ-25),
- `AMMO`: the gun ammo the player's hits were made with, by hit lines and by the sorties that hit with it. This is hits
  by ammo, not the belt the player picked: the log has no belt field.

Two GROUP BYs, no JSON parsing: one over the sortie columns (`payload_id`, `payload_name`, `weapon_mods`), one over the
level-1 `SortieGunHits` rows (written by `persist`, `gun_hit_rows`). Always recomputed for the touched players, never
adjusted by deltas, so incremental == rebuild by construction. `aggregates.recompute_players` calls `recompute_builds`.
"""

from collections import defaultdict
from collections.abc import Iterable

from django.db.models import Count, Sum

from il2ks.core.replay.model import is_gun_ammo
from il2ks.core.replay.result import SortieResult
from il2ks.db.models import BuildKind, PlayerAircraftBuild, SortieGunHits
from il2ks.ingest.counters import counted_sorties
from il2ks.ingest.dbutil import update_rows

type _Key = tuple[int, int, int | None, str, int, str]  # player, aircraft, tour (None = all time), kind, value, label
type _Tally = list[int]  # [sorties, hits]


def gun_hit_rows(sorties: Iterable[SortieResult]) -> dict[int, dict[str, int]]:
    """Hit lines given per gun ammo log name, per sortie index (ordnance lines and ammo without hits left out)."""
    out: dict[int, dict[str, int]] = {}
    for s in sorties:
        hits: dict[str, int] = {}
        for h in s.ammo_hits:
            if h.hits_given > 0 and is_gun_ammo(h.ammo):
                hits[h.ammo] = hits.get(h.ammo, 0) + h.hits_given
        if hits:
            out[s.index] = hits
    return out


def recompute_builds(chunk: list[int], tour_ids: list[int] | None) -> None:
    """`PlayerAircraftBuild` for these players: all-time rows always, per-tour rows limited to `tour_ids` unless that is
    None. Rows with nothing left are deleted."""
    wanted: dict[_Key, _Tally] = defaultdict(lambda: [0, 0])

    def add(player: int, aircraft: int, tour: int | None, kind: str, value: int, label: str, n: int, hits: int) -> None:
        scopes: tuple[int | None, ...] = (None,)
        if tour is not None and (tour_ids is None or tour in tour_ids):
            scopes = (None, tour)
        for scope in scopes:
            tally = wanted[(player, aircraft, scope, kind, value, label)]
            tally[0] += n
            tally[1] += hits

    sorties = counted_sorties().filter(player_id__in=chunk)
    for row in sorties.values(
        "player_id", "aircraft_id", "mission__tour_id", "payload_id", "payload_name", "weapon_mods"
    ).annotate(n=Count("pk")):
        base = (row["player_id"], row["aircraft_id"], row["mission__tour_id"])
        add(*base, BuildKind.PAYLOAD.value, row["payload_id"], row["payload_name"], row["n"], 0)
        add(*base, BuildKind.MODS.value, row["weapon_mods"], "", row["n"], 0)
    for row in (
        SortieGunHits.objects.filter(sortie__in=sorties)
        .values("sortie__player_id", "sortie__aircraft_id", "sortie__mission__tour_id", "ammo")
        .annotate(n=Count("pk"), total=Sum("hits"))
    ):
        base = (row["sortie__player_id"], row["sortie__aircraft_id"], row["sortie__mission__tour_id"])
        add(*base, BuildKind.AMMO.value, 0, row["ammo"], row["n"], row["total"])

    existing_rows = PlayerAircraftBuild.objects.filter(player_id__in=chunk)
    if tour_ids is not None:
        existing_rows = existing_rows.filter(tour_id__isnull=True) | existing_rows.filter(tour_id__in=tour_ids)
    existing = {(r.player_id, r.aircraft_id, r.tour_id, r.kind, r.value, r.label): r for r in existing_rows}
    changed: list[PlayerAircraftBuild] = []
    new: list[PlayerAircraftBuild] = []
    for key, (n, hits) in wanted.items():
        row = existing.pop(key, None)
        if row is None:
            player_id, aircraft_id, tour_id, kind, value, label = key
            new.append(
                PlayerAircraftBuild(
                    player_id=player_id,
                    aircraft_id=aircraft_id,
                    tour_id=tour_id,
                    kind=kind,
                    value=value,
                    label=label,
                    sorties=n,
                    hits=hits,
                )
            )
        elif (row.sorties, row.hits) != (n, hits):
            row.sorties, row.hits = n, hits
            changed.append(row)
    PlayerAircraftBuild.objects.filter(pk__in=[r.pk for r in existing.values()]).delete()
    update_rows(PlayerAircraftBuild, changed, ["sorties", "hits"])
    PlayerAircraftBuild.objects.bulk_create(new)
