"""Level-2 stats per aircraft type (FR-WEB-8): `AircraftStats`, `TourAircraftStats`, `AircraftMatchup`,
`AircraftPayload`. Aggregation in
`ingest` only (TD-22); the aircraft pages just read these rows.

Like the player aggregates they are always recomputed from lower rows, never adjusted by deltas, so incremental ==
rebuild by construction:

- `AircraftStats` = the sum of the type's `PlayerAircraft` rows (every player, hidden ones too), plus the pilot count,
  the side most sorties were flown for. No ratio is stored: K/D and the like are computed at read time (OQ-98).
  Recompute it after `recompute_players` (it reads their rows).
- `TourAircraftStats` = the same per tour (TD-26) and combat role: `all` rows are summed from the type's
  `PlayerTourAircraft` rows; `air_superiority` / `attack` rows (per tour, and all time with a null tour) are counted
  sorties grouped by `combat_role`, with the distinct pilots. The pilot count and the side are of that scope. Only the
  tours a saved mission touched are recomputed (the all-time role rows always); a rebuild does them all.
- `AircraftPayload` = counted sorties grouped by loadout name and combat role, plus the average pilot Elo of an air
  superiority loadout (`recompute_payload_elo`, after the ratings).
- `AircraftMatchup` = enemy PvP `Kill` rows between pilot sorties, grouped by (killer type, victim type) and scope
  (all time / tour, all kills / intercept fights where both sorties were air superiority).

`save_mission` passes the types, tours and pairs a mission touched (old and new); `rebuild_aggregates` passes
everything.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from django.db.models import Count, Q, QuerySet, Sum

from il2ks.core.catalog.loader import side_of_country
from il2ks.db.models import (
    AircraftCounters,
    AircraftMatchup,
    AircraftPayload,
    AircraftRole,
    AircraftStats,
    CombatRole,
    GameObject,
    Kill,
    KillCredit,
    Player,
    PlayerAircraft,
    PlayerTourAircraft,
    Propulsion,
    Role,
    TourAircraftStats,
)
from il2ks.ingest.counters import (
    COUNTER_FIELDS,
    FLOAT_COUNTERS,
    SCORE_DECIMALS,
    SORTIE_COUNTERS,
    clean_counters,
    counted_sorties,
)
from il2ks.ingest.dbutil import update_rows

CHUNK = 400
ELO_DECIMALS = 3  # averages are rounded so a rebuild can't differ by float noise
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
    """`AircraftStats` and `AircraftPayload` for these types, and their `TourAircraftStats` (per tour in `tour_ids`,
    None = every tour; the all-time role rows always); rows of types without counted sorties are deleted. The payload
    Elo is not here: it needs the ratings (`recompute_payload_elo`, run by `recompute_ratings`)."""
    ids = sorted(set(aircraft_ids))
    tours = None if tour_ids is None else sorted(set(tour_ids))
    for start in range(0, len(ids), CHUNK):
        chunk = ids[start : start + CHUNK]
        groups = _sortie_groups(chunk)
        sides = _sides(groups)
        _recompute_stats(chunk, sides)
        _recompute_payloads(chunk, groups)
        _recompute_tour_stats(chunk, tours, sides)


type _Wanted[K] = dict[K, dict[str, int | float | str]]
type _TourKey = tuple[int, int | None, str]  # aircraft, tour (None = all time), role (`AircraftRole`)
ALL = AircraftRole.ALL.value


def _recompute_stats(chunk: list[int], sides: dict[tuple[int, str], str]) -> None:
    sums = {name: Sum(name) for name in COUNTER_FIELDS}
    wanted: _Wanted[int] = {
        row["aircraft_id"]: _stat_values(row, sides.get((row["aircraft_id"], ALL), ""))
        for row in PlayerAircraft.objects.filter(aircraft_id__in=chunk)
        .values("aircraft_id")
        .annotate(pilots=Count("pk"), **sums)
    }
    existing = {row.aircraft_id: row for row in AircraftStats.objects.filter(aircraft_id__in=chunk)}
    _sync_stats(AircraftStats, wanted, existing, lambda aircraft_id: {"aircraft_id": aircraft_id})


def _recompute_tour_stats(
    chunk: list[int], tour_ids: list[int] | None, alltime_sides: dict[tuple[int, str], str]
) -> None:
    """`TourAircraftStats` of these types: the `all` role per tour from the players' per-tour aircraft rows, and every
    combat role per tour and all time from the counted sorties (`pilots` = distinct players, which no sum of rows can
    give)."""
    rows = PlayerTourAircraft.objects.filter(aircraft_id__in=chunk)
    sorties = counted_sorties().filter(aircraft_id__in=chunk)
    existing_rows = TourAircraftStats.objects.filter(aircraft_id__in=chunk)
    tour_sorties = sorties.filter(mission__tour__isnull=False)
    if tour_ids is not None:
        rows = rows.filter(tour_id__in=tour_ids)
        existing_rows = existing_rows.filter(Q(tour_id__in=tour_ids) | Q(tour__isnull=True))
        tour_sorties = tour_sorties.filter(mission__tour_id__in=tour_ids)
    by_side: dict[tuple[int, int, str], dict[str, int]] = {}
    grouped = tour_sorties.values("aircraft_id", "mission__tour_id", "combat_role", "country").annotate(n=Count("pk"))
    for found in grouped:
        side = side_of_country(found["country"])
        if side is not None:
            for role in (ALL, found["combat_role"]):
                if role is not None:
                    counts = by_side.setdefault((found["aircraft_id"], found["mission__tour_id"], role), {})
                    counts[side] = counts.get(side, 0) + found["n"]
    sides = _majority_sides(by_side)
    sums = {name: Sum(name) for name in COUNTER_FIELDS}
    wanted: _Wanted[_TourKey] = {
        (row["aircraft_id"], row["tour_id"], ALL): _stat_values(
            row, sides.get((row["aircraft_id"], row["tour_id"], ALL), "")
        )
        for row in rows.values("aircraft_id", "tour_id").annotate(pilots=Count("pk"), **sums)
    }
    distinct = {"pilots": Count("player_id", distinct=True), **SORTIE_COUNTERS}
    per_tour = tour_sorties.filter(combat_role__isnull=False).values("aircraft_id", "mission__tour_id", "combat_role")
    for row in per_tour.annotate(**distinct):
        key = (row["aircraft_id"], row["mission__tour_id"], row["combat_role"])
        wanted[key] = _stat_values(row, sides.get((key[0], row["mission__tour_id"], key[2]), ""))
    all_time = sorties.filter(combat_role__isnull=False).values("aircraft_id", "combat_role")
    for row in all_time.annotate(**distinct):
        wanted[(row["aircraft_id"], None, row["combat_role"])] = _stat_values(
            row, alltime_sides.get((row["aircraft_id"], row["combat_role"]), "")
        )
    existing = {(row.aircraft_id, row.tour_id, row.role): row for row in existing_rows}
    _sync_stats(
        TourAircraftStats, wanted, existing, lambda key: {"aircraft_id": key[0], "tour_id": key[1], "role": key[2]}
    )


def _stat_values(total: Mapping[str, object], side: str) -> dict[str, int | float | str]:
    """The stored values of one stats row from an aggregate row: counters, pilots (one source row per player), side."""
    pilots = total["pilots"]
    return {**clean_counters(total), "pilots": pilots if isinstance(pilots, int) else 0, "side": side}


def _sync_stats[K, M: AircraftCounters](
    model: type[M], wanted: _Wanted[K], existing: dict[K, M], identity: Callable[[K], dict[str, int | str | None]]
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


@dataclass(frozen=True, slots=True)
class _Group:
    """The counted sorties of one type, country, loadout and combat role ('' = none recorded), summed."""

    aircraft_id: int
    country: int
    payload_name: str
    role: str
    sorties: int
    kills_air: int
    kills_ground: int
    deaths: int
    kills_air_pvp: int
    flight_time_s: float
    score_ground_attack: float
    time_on_target_s: float


def _sortie_groups(chunk: list[int]) -> list[_Group]:
    """The types' counted sorties grouped by country, payload and role, in ONE pass over all their history (this grows
    with it, so the sides and the payload rows both come from these groups instead of a grouped query each)."""
    rows = (
        counted_sorties()
        .filter(aircraft_id__in=chunk)
        .values("aircraft_id", "country", "payload_name", "combat_role")
        .annotate(
            n=Count("pk"),
            air=Sum("kills_air"),
            ground=Sum("kills_ground"),
            dead=Count("pk", filter=Q(is_death=True)),
            pvp=Sum("kills_air_pvp"),
            flight=Sum("flight_time_s"),
            attack_score=Sum("ground_points", filter=Q(combat_role=CombatRole.ATTACK)),
            tot=Sum("time_on_target_s"),
        )
    )
    return [
        _Group(
            r["aircraft_id"],
            r["country"],
            r["payload_name"],
            r["combat_role"] or "",
            r["n"],
            r["air"] or 0,
            r["ground"] or 0,
            r["dead"],
            r["pvp"] or 0,
            float(r["flight"] or 0.0),
            float(r["attack_score"] or 0.0),
            float(r["tot"] or 0.0),
        )
        for r in rows
    ]


def _sides(groups: list[_Group]) -> dict[tuple[int, str], str]:
    """The side most of each type's counted sorties were flown for, overall (`all`) and per combat role (ties: the
    REDFOR/BLUFOR name that sorts first)."""
    by_side: dict[tuple[int, str], dict[str, int]] = {}
    for g in groups:
        side = side_of_country(g.country)
        if side is not None:
            for role in (ALL, g.role):
                if role:
                    counts = by_side.setdefault((g.aircraft_id, role), {})
                    counts[side] = counts.get(side, 0) + g.sorties
    return _majority_sides(by_side)


def _majority_sides[K](by_side: dict[K, dict[str, int]]) -> dict[K, str]:
    return {key: min(counts, key=lambda s: (-counts[s], s)) for key, counts in by_side.items()}


_PAYLOAD_FIELDS = (
    "sorties",
    "kills_air",
    "kills_ground",
    "deaths",
    "kills_air_pvp",
    "flight_time_s",
    "score_ground_attack",
    "time_on_target_s",
)


def _recompute_payloads(chunk: list[int], groups: list[_Group]) -> None:
    """`AircraftPayload` rows per (type, loadout, role). `elo_avg` is not touched here (see `recompute_payload_elo`)."""
    sums: dict[tuple[int, str, str], list[float]] = {}
    for g in groups:
        total = sums.setdefault((g.aircraft_id, g.payload_name, g.role), [0.0] * len(_PAYLOAD_FIELDS))
        for i, name in enumerate(_PAYLOAD_FIELDS):
            total[i] += getattr(g, name)
    wanted: dict[tuple[int, str, str], dict[str, int | float]] = {
        key: {
            name: round(value, SCORE_DECIMALS) if name in FLOAT_COUNTERS else int(value)
            for name, value in zip(_PAYLOAD_FIELDS, total, strict=True)
        }
        for key, total in sorted(sums.items())  # the order a GROUP BY gives: new rows get their ids in it
    }
    existing = {
        (r.aircraft_id, r.payload_name, r.combat_role): r for r in AircraftPayload.objects.filter(aircraft_id__in=chunk)
    }
    changed: list[AircraftPayload] = []
    new: list[AircraftPayload] = []
    for key, values in wanted.items():
        row = existing.pop(key, None)
        if row is None:
            new.append(AircraftPayload(aircraft_id=key[0], payload_name=key[1], combat_role=key[2], **values))
        elif any(getattr(row, name) != value for name, value in values.items()):
            for name, value in values.items():
                setattr(row, name, value)
            changed.append(row)
    AircraftPayload.objects.filter(pk__in=[row.pk for row in existing.values()]).delete()
    update_rows(AircraftPayload, changed, list(_PAYLOAD_FIELDS))
    AircraftPayload.objects.bulk_create(new)


def average_pilot_elo(group_field: str) -> dict[tuple[int, object], float]:
    """The average Elo of the pilots of air superiority sorties, per (aircraft type, value of the sortie field
    `group_field`) group, one vote per sortie: the reusable half of every "effectiveness by X" table (X = the loadout
    `payload_name`; weapon mods next). A pilot's Elo is the rating in the type if they have games in it
    (`PlayerAircraft.elo_games`), else their pool's rating (the propulsion of the type) if they have games there; a
    pilot with neither is left out, and a group with no rated pilot is absent. Rounded to `ELO_DECIMALS`."""
    type_elo = {
        (player, aircraft): elo
        for player, aircraft, elo, games in PlayerAircraft.objects.values_list(
            "player_id", "aircraft_id", "elo", "elo_games"
        )
        if games > 0
    }
    pool_elo: dict[tuple[int, str], float] = {}
    for pk, prop, prop_games, jet, jet_games in Player.objects.values_list(
        "pk", "elo_prop", "elo_prop_games", "elo_jet", "elo_jet_games"
    ):
        if prop_games > 0:
            pool_elo[(pk, Propulsion.PROP.value)] = prop
        if jet_games > 0:
            pool_elo[(pk, Propulsion.JET.value)] = jet
    propulsion = dict(GameObject.objects.values_list("pk", "propulsion"))
    totals: dict[tuple[int, object], tuple[float, int]] = {}
    pilots = (
        counted_sorties()
        .filter(combat_role=CombatRole.AIR_SUPERIORITY)
        .values("aircraft_id", group_field, "player_id")
        .annotate(n=Count("pk"))
        .order_by("aircraft_id", group_field, "player_id")  # a fixed summing order: a rebuild gives the same floats
    )
    for row in pilots:
        aircraft_id = row["aircraft_id"]
        elo = type_elo.get((row["player_id"], aircraft_id))
        if elo is None:
            elo = pool_elo.get((row["player_id"], propulsion.get(aircraft_id, "")))
        if elo is not None:
            key = (aircraft_id, row[group_field])
            total, n = totals.get(key, (0.0, 0))
            totals[key] = (total + elo * row["n"], n + row["n"])
    return {key: round(total / n, ELO_DECIMALS) for key, (total, n) in totals.items()}


def recompute_payload_elo() -> None:
    """`AircraftPayload.elo_avg` of every air superiority loadout (`average_pilot_elo("payload_name")`); other roles
    and loadouts without a rated pilot stay null. Order-dependent like every Elo, so it is recomputed for ALL loadouts
    after `recompute_ratings` replayed the games (one code path for `save_mission` and a rebuild)."""
    averages = average_pilot_elo("payload_name")
    changed: list[AircraftPayload] = []
    for row in AircraftPayload.objects.all():
        wanted = (
            averages.get((row.aircraft_id, row.payload_name)) if row.combat_role == CombatRole.AIR_SUPERIORITY else None
        )
        if row.elo_avg != wanted:
            row.elo_avg = wanted
            changed.append(row)
    update_rows(AircraftPayload, changed, ["elo_avg"])


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
