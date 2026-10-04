"""Level-2 stats per aircraft type (FR-WEB-8): `AircraftStats`, `TourAircraftStats`, `AircraftMatchup`,
`AircraftPayload`. Aggregation in
`ingest` only (TD-22); the aircraft pages just read these rows.

Like the player aggregates they are always recomputed from lower rows, never adjusted by deltas, so incremental ==
rebuild by construction:

- `AircraftStats` = the sum of the type's `PlayerAircraft` rows (every player, hidden ones too), plus the pilot count,
  the side most sorties were flown for. No ratio is stored: K/D and the like are computed at read time (OQ-98).
  Recompute it after `recompute_players` (it reads their rows).
- `TourAircraftStats` = the same per tour (TD-26), summed from the type's `PlayerTourAircraft` rows; the pilot count
  and the side are of that tour. Only the tours a saved mission touched are recomputed; a rebuild does them all.
- `AircraftPayload` = counted sorties grouped by loadout name.
- `AircraftMatchup` = enemy PvP `Kill` rows between pilot sorties, grouped by (killer type, victim type) and scope
  (all time / tour, all kills / intercept fights where both sorties were air superiority).

`save_mission` passes the types, tours and pairs a mission touched (old and new); `rebuild_aggregates` passes
everything.
"""

from collections.abc import Callable, Iterable, Mapping

from django.db.models import Count, Q, QuerySet, Sum

from il2ks.core.catalog.loader import side_of_country
from il2ks.db.models import (
    AircraftCounters,
    AircraftMatchup,
    AircraftPayload,
    AircraftStats,
    CombatRole,
    Kill,
    KillCredit,
    PlayerAircraft,
    PlayerTourAircraft,
    Role,
    TourAircraftStats,
)
from il2ks.ingest.counters import COUNTER_FIELDS, clean_counters, counted_sorties
from il2ks.ingest.dbutil import update_rows

CHUNK = 400
PAIR_CHUNK = 60  # pairs per OR-ed query: far below SQLite's expression depth limit

type Pair = tuple[int, int]  # (killer aircraft id, victim aircraft id)
type ScopedPair = tuple[int, int, int | None, bool]  # pair, tour (None = all time), intercept fights only


def matchup_kills() -> QuerySet[Kill]:
    """The kills that count for a matchup: credited, against an enemy, pilot to pilot (as the Elo games, FR-WEB-19)."""
    return Kill.objects.filter(
        credit=KillCredit.KILL,
        is_friendly=False,
        killer_sortie__role=Role.PILOT,
        victim_sortie__role=Role.PILOT,
    )


def mission_pairs(mission_id: int) -> set[Pair]:
    """The (killer type, victim type) pairs of one mission's counted kills."""
    rows = (
        matchup_kills()
        .filter(mission_id=mission_id)
        .values_list("killer_sortie__aircraft_id", "victim_sortie__aircraft_id")
    )
    return set(rows)


def mission_aircraft(mission_id: int) -> set[int]:
    """The aircraft types of one mission's pilot sorties."""
    return set(counted_sorties().filter(mission_id=mission_id).values_list("aircraft_id", flat=True))


def recompute_aircraft_stats(aircraft_ids: Iterable[int], tour_ids: Iterable[int] | None) -> None:
    """`AircraftStats` and `AircraftPayload` for these types, and their `TourAircraftStats` in `tour_ids` (None = every
    tour); rows of types without counted sorties are deleted."""
    ids = sorted(set(aircraft_ids))
    tours = None if tour_ids is None else sorted(set(tour_ids))
    for start in range(0, len(ids), CHUNK):
        chunk = ids[start : start + CHUNK]
        groups = _sortie_groups(chunk)
        _recompute_stats(chunk, _sides(groups))
        _recompute_payloads(chunk, groups)
        if tours is None or tours:
            _recompute_tour_stats(chunk, tours)


type _Wanted[K] = dict[K, dict[str, int | float | str]]


def _recompute_stats(chunk: list[int], sides: dict[int, str]) -> None:
    sums = {name: Sum(name) for name in COUNTER_FIELDS}
    wanted: _Wanted[int] = {
        row["aircraft_id"]: _stat_values(row, sides.get(row["aircraft_id"], ""))
        for row in PlayerAircraft.objects.filter(aircraft_id__in=chunk)
        .values("aircraft_id")
        .annotate(pilots=Count("pk"), **sums)
    }
    existing = {row.aircraft_id: row for row in AircraftStats.objects.filter(aircraft_id__in=chunk)}
    _sync_stats(AircraftStats, wanted, existing, lambda aircraft_id: {"aircraft_id": aircraft_id})


def _recompute_tour_stats(chunk: list[int], tour_ids: list[int] | None) -> None:
    """`TourAircraftStats` of these types in these tours (None = all), from the players' per-tour aircraft rows."""
    rows = PlayerTourAircraft.objects.filter(aircraft_id__in=chunk)
    sorties = counted_sorties().filter(aircraft_id__in=chunk, mission__tour__isnull=False)
    existing_rows = TourAircraftStats.objects.filter(aircraft_id__in=chunk)
    if tour_ids is not None:
        rows = rows.filter(tour_id__in=tour_ids)
        sorties = sorties.filter(mission__tour_id__in=tour_ids)
        existing_rows = existing_rows.filter(tour_id__in=tour_ids)
    by_side: dict[tuple[int, int], dict[str, int]] = {}
    for found in sorties.values("aircraft_id", "mission__tour_id", "country").annotate(n=Count("pk")):
        side = side_of_country(found["country"])
        if side is not None:
            counts = by_side.setdefault((found["aircraft_id"], found["mission__tour_id"]), {})
            counts[side] = counts.get(side, 0) + found["n"]
    sides = _majority_sides(by_side)
    sums = {name: Sum(name) for name in COUNTER_FIELDS}
    wanted: _Wanted[tuple[int, int]] = {
        (row["aircraft_id"], row["tour_id"]): _stat_values(row, sides.get((row["aircraft_id"], row["tour_id"]), ""))
        for row in rows.values("aircraft_id", "tour_id").annotate(pilots=Count("pk"), **sums)
    }
    existing = {(row.aircraft_id, row.tour_id): row for row in existing_rows}
    _sync_stats(TourAircraftStats, wanted, existing, lambda key: {"aircraft_id": key[0], "tour_id": key[1]})


def _stat_values(total: Mapping[str, object], side: str) -> dict[str, int | float | str]:
    """The stored values of one stats row from an aggregate row: counters, pilots (one source row per player), side."""
    pilots = total["pilots"]
    return {**clean_counters(total), "pilots": pilots if isinstance(pilots, int) else 0, "side": side}


def _sync_stats[K, M: AircraftCounters](
    model: type[M], wanted: _Wanted[K], existing: dict[K, M], identity: Callable[[K], dict[str, int]]
) -> None:
    """Make the rows equal `wanted` (new ones created, changed ones updated, the others deleted)."""
    changed: list[M] = []
    new: list[M] = []
    for key, values in wanted.items():
        row = existing.pop(key, None)
        if row is None:
            new.append(model(**identity(key), **values))
        elif any(getattr(row, name) != value for name, value in values.items()):
            for name, value in values.items():
                setattr(row, name, value)
            changed.append(row)
    model.objects.filter(pk__in=[row.pk for row in existing.values()]).delete()
    update_rows(model, changed, [*COUNTER_FIELDS, "pilots", "side"])
    model.objects.bulk_create(new)


type _Group = tuple[int, int, str, int, int, int, int]  # aircraft, country, payload name, sorties, air, ground, deaths


def _sortie_groups(chunk: list[int]) -> list[_Group]:
    """The types' counted sorties grouped by country and payload, in ONE pass over all their history (this grows with
    it, so the sides and the payload rows both come from these groups instead of a grouped query each)."""
    rows = (
        counted_sorties()
        .filter(aircraft_id__in=chunk)
        .values("aircraft_id", "country", "payload_name")
        .annotate(
            n=Count("pk"),
            air=Sum("kills_air"),
            ground=Sum("kills_ground"),
            dead=Count("pk", filter=Q(is_death=True)),
        )
    )
    return [
        (r["aircraft_id"], r["country"], r["payload_name"], r["n"], r["air"] or 0, r["ground"] or 0, r["dead"])
        for r in rows
    ]


def _sides(groups: list[_Group]) -> dict[int, str]:
    """The side most of each type's counted sorties were flown for (ties: the REDFOR/BLUFOR name that sorts first)."""
    by_side: dict[int, dict[str, int]] = {}
    for aircraft_id, country, _, n, _, _, _ in groups:
        side = side_of_country(country)
        if side is not None:
            counts = by_side.setdefault(aircraft_id, {})
            counts[side] = counts.get(side, 0) + n
    return _majority_sides(by_side)


def _majority_sides[K](by_side: dict[K, dict[str, int]]) -> dict[K, str]:
    return {key: min(counts, key=lambda s: (-counts[s], s)) for key, counts in by_side.items()}


def _recompute_payloads(chunk: list[int], groups: list[_Group]) -> None:
    sums: dict[tuple[int, str], tuple[int, int, int, int]] = {}
    for aircraft_id, _, payload_name, n, air, ground, dead in groups:
        a, b, c, d = sums.get((aircraft_id, payload_name), (0, 0, 0, 0))
        sums[(aircraft_id, payload_name)] = (a + n, b + air, c + ground, d + dead)
    wanted = dict(sorted(sums.items()))  # the order a GROUP BY gives: new rows get their ids in it
    existing = {(r.aircraft_id, r.payload_name): r for r in AircraftPayload.objects.filter(aircraft_id__in=chunk)}
    changed: list[AircraftPayload] = []
    new: list[AircraftPayload] = []
    for key, values in wanted.items():
        row = existing.pop(key, None)
        if row is None:
            new.append(AircraftPayload(aircraft_id=key[0], payload_name=key[1], **_payload_fields(values)))
        elif (row.sorties, row.kills_air, row.kills_ground, row.deaths) != values:
            for name, value in _payload_fields(values).items():
                setattr(row, name, value)
            changed.append(row)
    AircraftPayload.objects.filter(pk__in=[row.pk for row in existing.values()]).delete()
    update_rows(AircraftPayload, changed, ["sorties", "kills_air", "kills_ground", "deaths"])
    AircraftPayload.objects.bulk_create(new)


def _payload_fields(values: tuple[int, int, int, int]) -> dict[str, int]:
    return dict(zip(("sorties", "kills_air", "kills_ground", "deaths"), values, strict=True))


def recompute_matchups(pairs: Iterable[Pair] | None = None) -> None:
    """`AircraftMatchup` (every scope: all time and per tour, all kills and intercept fights only) for these
    (killer type, victim type) pairs from the `Kill` rows, or for all pairs (None, a rebuild). Rows without kills left
    are deleted."""
    if pairs is None:
        _sync_matchups(_scope_counts(matchup_kills()), {_key_of(r): r for r in AircraftMatchup.objects.all()})
        return
    ordered = sorted(set(pairs))
    for start in range(0, len(ordered), PAIR_CHUNK):
        chunk = ordered[start : start + PAIR_CHUNK]
        match = _any_of([Q(killer_sortie__aircraft_id=k, victim_sortie__aircraft_id=v) for k, v in chunk])
        found = _any_of([Q(killer_aircraft_id=k, victim_aircraft_id=v) for k, v in chunk])
        _sync_matchups(
            _scope_counts(matchup_kills().filter(match)), {_key_of(r): r for r in AircraftMatchup.objects.filter(found)}
        )


def _any_of(conditions: list[Q]) -> Q:
    """The conditions OR-ed together (nothing matches for an empty list)."""
    combined = Q(pk__in=[])
    for condition in conditions:
        combined |= condition
    return combined


def _key_of(row: AircraftMatchup) -> ScopedPair:
    return (row.killer_aircraft_id, row.victim_aircraft_id, row.tour_id, row.intercept)


def _scope_counts(kills: QuerySet[Kill]) -> dict[ScopedPair, int]:
    """The kills counted into every scope they belong to: all time, their tour, and (when both sorties were air
    superiority) the same two for intercept fights."""
    rows = kills.values(
        "killer_sortie__aircraft_id",
        "victim_sortie__aircraft_id",
        "mission__tour_id",
        "killer_sortie__combat_role",
        "victim_sortie__combat_role",
    ).annotate(n=Count("pk"))
    counts: dict[ScopedPair, int] = {}
    for row in rows:
        killer, victim, tour = (
            row["killer_sortie__aircraft_id"],
            row["victim_sortie__aircraft_id"],
            row["mission__tour_id"],
        )
        both_air = (
            row["killer_sortie__combat_role"] == CombatRole.AIR_SUPERIORITY
            and row["victim_sortie__combat_role"] == CombatRole.AIR_SUPERIORITY
        )
        tours: tuple[int | None, ...] = (None,) if tour is None else (None, tour)
        for scope_tour in tours:
            for intercept in (False, True) if both_air else (False,):
                key = (killer, victim, scope_tour, intercept)
                counts[key] = counts.get(key, 0) + row["n"]
    return counts


def _sync_matchups(counts: dict[ScopedPair, int], existing: dict[ScopedPair, AircraftMatchup]) -> None:
    changed: list[AircraftMatchup] = []
    new: list[AircraftMatchup] = []
    for key, kills in counts.items():
        row = existing.pop(key, None)
        if row is None:
            new.append(
                AircraftMatchup(
                    killer_aircraft_id=key[0], victim_aircraft_id=key[1], tour_id=key[2], intercept=key[3], kills=kills
                )
            )
        elif row.kills != kills:
            row.kills = kills
            changed.append(row)
    AircraftMatchup.objects.filter(pk__in=[row.pk for row in existing.values()]).delete()
    update_rows(AircraftMatchup, changed, ["kills"])
    AircraftMatchup.objects.bulk_create(new)


def rebuild_aircraft_stats() -> None:
    """Every aircraft-type row from scratch (`il2ks rebuild-aggregates`); needs the player rows rebuilt first."""
    ids = (
        set(PlayerAircraft.objects.values_list("aircraft_id", flat=True))
        | set(AircraftStats.objects.values_list("aircraft_id", flat=True))
        | set(TourAircraftStats.objects.values_list("aircraft_id", flat=True))
        | set(AircraftPayload.objects.values_list("aircraft_id", flat=True))
    )
    recompute_aircraft_stats(ids, None)
    recompute_matchups(None)
