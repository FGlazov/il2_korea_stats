"""Level-2 stats per aircraft type (FR-WEB-8): `AircraftStats`, `AircraftMatchup`, `AircraftPayload`. Aggregation in
`ingest` only (TD-22); the aircraft pages just read these rows.

Like the player aggregates they are always recomputed from lower rows, never adjusted by deltas, so incremental ==
rebuild by construction:

- `AircraftStats` = the sum of the type's `PlayerAircraft` rows (every player, hidden ones too), plus the pilot count,
  the side most sorties were flown for, and the four stored ratios used for sorting. Recompute it after
  `recompute_players` (it reads their rows).
- `AircraftPayload` = counted sorties grouped by loadout name.
- `AircraftMatchup` = enemy PvP `Kill` rows between pilot sorties, grouped by (killer type, victim type).

`save_mission` passes the types and pairs a mission touched (old and new); `rebuild_aggregates` passes everything.
"""

from collections.abc import Iterable

from django.db.models import Count, Q, QuerySet, Sum

from il2ks.core.catalog.loader import side_of_country
from il2ks.db.models import (
    AircraftMatchup,
    AircraftPayload,
    AircraftStats,
    Kill,
    KillCredit,
    PlayerAircraft,
    Role,
)
from il2ks.ingest.counters import COUNTER_FIELDS, clean_counters, counted_sorties
from il2ks.ingest.dbutil import update_rows

CHUNK = 400
PAIR_CHUNK = 60  # pairs per OR-ed query: far below SQLite's expression depth limit

type Pair = tuple[int, int]  # (killer aircraft id, victim aircraft id)


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


def recompute_aircraft_stats(aircraft_ids: Iterable[int]) -> None:
    """`AircraftStats` and `AircraftPayload` for these types; rows of types without counted sorties are deleted."""
    ids = sorted(set(aircraft_ids))
    for start in range(0, len(ids), CHUNK):
        chunk = ids[start : start + CHUNK]
        _recompute_stats(chunk)
        _recompute_payloads(chunk)


def _ratio(top: int, bottom: int) -> float:
    return top / bottom if bottom else 0.0


def _recompute_stats(chunk: list[int]) -> None:
    sums = {name: Sum(name) for name in COUNTER_FIELDS}
    totals = {
        row["aircraft_id"]: row
        for row in PlayerAircraft.objects.filter(aircraft_id__in=chunk)
        .values("aircraft_id")
        .annotate(pilots=Count("pk"), **sums)
    }
    sides = _sides(chunk)
    existing = {row.aircraft_id: row for row in AircraftStats.objects.filter(aircraft_id__in=chunk)}
    changed: list[AircraftStats] = []
    new: list[AircraftStats] = []
    for aircraft_id, total in totals.items():
        values = clean_counters(total)
        sorties, deaths = int(values["sorties"]), int(values["deaths"])
        wanted: dict[str, int | float | str] = {
            **values,
            "pilots": int(total["pilots"]),
            "side": sides.get(aircraft_id, ""),
            "kd": _ratio(int(values["kills_air"]), deaths),
            "kl": _ratio(int(values["kills_air"]), int(values["planes_lost"])),
            "survival": _ratio(max(sorties - deaths, 0), sorties),
            "attack_share": _ratio(int(values["attack_sorties"]), sorties),
        }
        row = existing.pop(aircraft_id, None)
        if row is None:
            new.append(AircraftStats(aircraft_id=aircraft_id, **wanted))
        elif any(getattr(row, name) != value for name, value in wanted.items()):
            for name, value in wanted.items():
                setattr(row, name, value)
            changed.append(row)
    AircraftStats.objects.filter(pk__in=[row.pk for row in existing.values()]).delete()
    fields = [*COUNTER_FIELDS, "pilots", "side", "kd", "kl", "survival", "attack_share"]
    update_rows(AircraftStats, changed, fields)
    AircraftStats.objects.bulk_create(new)


def _sides(chunk: list[int]) -> dict[int, str]:
    """The side most of each type's counted sorties were flown for (ties: the REDFOR/BLUFOR name that sorts first)."""
    by_side: dict[int, dict[str, int]] = {}
    rows = counted_sorties().filter(aircraft_id__in=chunk).values("aircraft_id", "country").annotate(n=Count("pk"))
    for row in rows:
        side = side_of_country(row["country"])
        if side is not None:
            counts = by_side.setdefault(row["aircraft_id"], {})
            counts[side] = counts.get(side, 0) + row["n"]
    return {aircraft_id: min(counts, key=lambda s: (-counts[s], s)) for aircraft_id, counts in by_side.items()}


def _recompute_payloads(chunk: list[int]) -> None:
    wanted = {
        (row["aircraft_id"], row["payload_name"]): (row["n"], row["air"] or 0, row["ground"] or 0, row["dead"])
        for row in counted_sorties()
        .filter(aircraft_id__in=chunk)
        .values("aircraft_id", "payload_name")
        .annotate(
            n=Count("pk"),
            air=Sum("kills_air"),
            ground=Sum("kills_ground"),
            dead=Count("pk", filter=Q(is_death=True)),
        )
    }
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
    """`AircraftMatchup` for these (killer type, victim type) pairs from the `Kill` rows, or for all pairs (None, a
    rebuild). Rows without kills left are deleted."""
    if pairs is None:
        counts = _pair_counts(matchup_kills())
        existing = {(r.killer_aircraft_id, r.victim_aircraft_id): r for r in AircraftMatchup.objects.all()}
        _sync_matchups(counts, existing)
        return
    ordered = sorted(set(pairs))
    for start in range(0, len(ordered), PAIR_CHUNK):
        chunk = ordered[start : start + PAIR_CHUNK]
        match = _any_of([Q(killer_sortie__aircraft_id=k, victim_sortie__aircraft_id=v) for k, v in chunk])
        counts = _pair_counts(matchup_kills().filter(match))
        found = _any_of([Q(killer_aircraft_id=k, victim_aircraft_id=v) for k, v in chunk])
        existing = {(r.killer_aircraft_id, r.victim_aircraft_id): r for r in AircraftMatchup.objects.filter(found)}
        _sync_matchups(counts, existing)


def _any_of(conditions: list[Q]) -> Q:
    """The conditions OR-ed together (nothing matches for an empty list)."""
    combined = Q(pk__in=[])
    for condition in conditions:
        combined |= condition
    return combined


def _pair_counts(kills: QuerySet[Kill]) -> dict[Pair, int]:
    rows = kills.values("killer_sortie__aircraft_id", "victim_sortie__aircraft_id").annotate(n=Count("pk"))
    return {(row["killer_sortie__aircraft_id"], row["victim_sortie__aircraft_id"]): row["n"] for row in rows}


def _sync_matchups(counts: dict[Pair, int], existing: dict[Pair, AircraftMatchup]) -> None:
    changed: list[AircraftMatchup] = []
    new: list[AircraftMatchup] = []
    for pair, kills in counts.items():
        row = existing.pop(pair, None)
        if row is None:
            new.append(AircraftMatchup(killer_aircraft_id=pair[0], victim_aircraft_id=pair[1], kills=kills))
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
        | set(AircraftPayload.objects.values_list("aircraft_id", flat=True))
    )
    recompute_aircraft_stats(ids)
    recompute_matchups(None)
