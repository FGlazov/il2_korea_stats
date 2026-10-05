"""TEST ORACLE: the aircraft-type level-2 computation as it was before all-time rows became the SUM of the tour rows
(every all-time row read the type's whole history). `tests/integration/test_aircraft_rollup.py` runs it over a database
the new code filled and checks that nothing changed; no production code imports this. Copied verbatim from
`ingest/aircraft_stats.py`, `ingest/aircraft_mods.py` and `ingest/aggregates.py` (ammo section) at the merge of the
level-2 rework; the new side counters of `TourAircraftStats` are not written by it (the comparison ignores them)."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

from django.db import models
from django.db.models import Count, Q, QuerySet, Sum

from il2ks.core.catalog.loader import mod_filter_patterns, side_of_country
from il2ks.db.models import (
    AircraftAmmoMixStats,
    AircraftAmmoStats,
    AircraftCounters,
    AircraftEffectiveness,
    AircraftMatchup,
    AircraftMods,
    AircraftPayload,
    AircraftRole,
    AircraftStats,
    CombatRole,
    Kill,
    KillCredit,
    MissionAircraftAmmo,
    MissionAircraftAmmoMix,
    PlayerAircraft,
    PlayerAircraftScope,
    PlayerTourAircraft,
    Role,
    TourAircraftStats,
)
from il2ks.ingest.aircraft_mods import scopes_of, significant_mods
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
type ScopedPair = tuple[int, int, int | None, bool, str, str, str]
# pair, tour (None = all time), intercept fights only, scoped side ('' / 'killer' / 'victim'), role, mod pattern


def matchup_kills() -> QuerySet[Kill]:
    """The kills that count for a matchup: credited, against an enemy, pilot to pilot (as the Elo games, FR-WEB-19)."""
    return Kill.objects.filter(
        credit=KillCredit.KILL,
        is_friendly=False,
        killer_sortie__role=Role.PILOT,
        victim_sortie__role=Role.PILOT,
    )


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
        significant = significant_mods(chunk)
        _recompute_effectiveness(chunk, groups, significant)
        _recompute_tour_stats(chunk, tours, sides, significant)
        _recompute_player_scopes(chunk, tours, significant)


type _Wanted[K] = dict[K, dict[str, int | float | str]]
type _TourKey = tuple[int, int | None, str, str]  # aircraft, tour (None = all time), role (`AircraftRole`), mod pattern
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
    chunk: list[int],
    tour_ids: list[int] | None,
    alltime_sides: dict[tuple[int, str], str],
    significant: dict[int, tuple[int, ...]],
) -> None:
    """`TourAircraftStats` of these types: the `all` role per tour from the players' per-tour aircraft rows, and every
    combat role per tour and all time from the counted sorties (`pilots` = distinct players, which no sum of rows can
    give); and, for a type with significant weapon mods, every filter pattern of those scopes (`aircraft_mods`)."""
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
        (row["aircraft_id"], row["tour_id"], ALL, ""): _stat_values(
            row, sides.get((row["aircraft_id"], row["tour_id"], ALL), "")
        )
        for row in rows.values("aircraft_id", "tour_id").annotate(pilots=Count("pk"), **sums)
    }
    distinct = {"pilots": Count("player_id", distinct=True), **SORTIE_COUNTERS}
    per_tour = tour_sorties.filter(combat_role__isnull=False).values("aircraft_id", "mission__tour_id", "combat_role")
    for row in per_tour.annotate(**distinct):
        key = (row["aircraft_id"], row["mission__tour_id"], row["combat_role"], "")
        wanted[key] = _stat_values(row, sides.get((key[0], row["mission__tour_id"], key[2]), ""))
    all_time = sorties.filter(combat_role__isnull=False).values("aircraft_id", "combat_role")
    for row in all_time.annotate(**distinct):
        wanted[(row["aircraft_id"], None, row["combat_role"], "")] = _stat_values(
            row, alltime_sides.get((row["aircraft_id"], row["combat_role"]), "")
        )
    for key, (totals, pilots, side) in pattern_stats(significant, tour_ids).items():
        wanted[key] = _stat_values({**totals, "pilots": pilots}, side)
    existing = {(row.aircraft_id, row.tour_id, row.role, row.mod_pattern): row for row in existing_rows}
    _sync_stats(
        TourAircraftStats,
        wanted,
        existing,
        lambda key: {"aircraft_id": key[0], "tour_id": key[1], "role": key[2], "mod_pattern": key[3]},
    )


def _recompute_player_scopes(
    chunk: list[int], tour_ids: list[int] | None, significant: dict[int, tuple[int, ...]]
) -> None:
    """`PlayerAircraftScope` rows of these types (the top pilots of the aircraft page): every player's counters per
    scope but the all-time, every-role, unfiltered one (`PlayerAircraft`). Only the tours in `tour_ids` (None = all)
    and all time are rewritten."""
    wanted = player_scope_stats(chunk, significant, tour_ids)
    existing_rows = PlayerAircraftScope.objects.filter(aircraft_id__in=chunk)
    if tour_ids is not None:
        existing_rows = existing_rows.filter(Q(tour_id__in=tour_ids) | Q(tour__isnull=True))
    existing = {(r.aircraft_id, r.player_id, r.tour_id, r.role, r.mod_pattern): r for r in existing_rows}
    changed: list[PlayerAircraftScope] = []
    new: list[PlayerAircraftScope] = []
    for key, totals in wanted.items():
        values = clean_counters(totals)
        row = existing.pop(key, None)
        if row is None:
            new.append(
                PlayerAircraftScope(
                    aircraft_id=key[0], player_id=key[1], tour_id=key[2], role=key[3], mod_pattern=key[4], **values
                )
            )
        elif any(getattr(row, name) != value for name, value in values.items()):
            for name, value in values.items():
                setattr(row, name, value)
            changed.append(row)
    PlayerAircraftScope.objects.filter(pk__in=[r.pk for r in existing.values()]).delete()
    update_rows(PlayerAircraftScope, changed, list(COUNTER_FIELDS))
    PlayerAircraftScope.objects.bulk_create(new)


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
    """The counted sorties of one type, tour, country, loadout, weapon mods and combat role ('' = none), summed."""

    aircraft_id: int
    tour: int | None
    country: int
    payload_name: str
    mods: int
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
    """The types' counted sorties grouped by tour, country, payload, weapon mods and role, in ONE pass over all their
    history (this grows with it, so the sides and the loadout and mods rows all come from these groups instead of a
    grouped query each)."""
    rows = (
        counted_sorties()
        .filter(aircraft_id__in=chunk)
        .values("aircraft_id", "mission__tour_id", "country", "payload_name", "weapon_mods", "combat_role")
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
            r["mission__tour_id"],
            r["country"],
            r["payload_name"],
            r["weapon_mods"],
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


_EFFECTIVENESS_FIELDS = (
    "sorties",
    "kills_air",
    "kills_ground",
    "deaths",
    "kills_air_pvp",
    "flight_time_s",
    "score_ground_attack",
    "time_on_target_s",
)

type _GroupKey = tuple[int, int | None, str | int, str, str]
# aircraft, tour (None = all time), loadout name or WM, role, mod pattern ('' = unfiltered)


def _recompute_effectiveness(chunk: list[int], groups: list[_Group], significant: dict[int, tuple[int, ...]]) -> None:
    """`AircraftPayload` rows per (type, tour or all time, loadout, role, mod pattern) and `AircraftMods` rows (type,
    tour or all time, weapon-mod set, role, mod pattern), from the same groups. `elo_avg` is not touched here
    (see `recompute_payload_elo`)."""
    payloads: dict[_GroupKey, list[float]] = {}
    mods: dict[_GroupKey, list[float]] = {}
    for g in groups:
        for tour in (None,) if g.tour is None else (None, g.tour):
            for pattern in ("", *mod_filter_patterns(g.mods, significant.get(g.aircraft_id, ()))):
                for sums, value in ((payloads, g.payload_name), (mods, g.mods)):
                    total = sums.setdefault(
                        (g.aircraft_id, tour, value, g.role, pattern), [0.0] * len(_EFFECTIVENESS_FIELDS)
                    )
                    for i, name in enumerate(_EFFECTIVENESS_FIELDS):
                        total[i] += getattr(g, name)
    _sync_effectiveness(
        AircraftPayload,
        AircraftPayload.objects.filter(aircraft_id__in=chunk),
        payloads,
        lambda r: (r.aircraft_id, r.tour_id, r.payload_name, r.combat_role, r.mod_pattern),
        lambda k: {
            "aircraft_id": k[0],
            "tour_id": k[1],
            "payload_name": k[2],
            "combat_role": k[3],
            "mod_pattern": k[4],
        },
    )
    _sync_effectiveness(
        AircraftMods,
        AircraftMods.objects.filter(aircraft_id__in=chunk),
        mods,
        lambda r: (r.aircraft_id, r.tour_id, r.weapon_mods, r.combat_role, r.mod_pattern),
        lambda k: {"aircraft_id": k[0], "tour_id": k[1], "weapon_mods": k[2], "combat_role": k[3], "mod_pattern": k[4]},
    )


def _sync_effectiveness[M: AircraftEffectiveness](
    model: type[M],
    existing_rows: QuerySet[M],
    sums: dict[_GroupKey, list[float]],
    key_of: Callable[[M], _GroupKey],
    identity: Callable[[_GroupKey], dict[str, int | str | None]],
) -> None:
    wanted: dict[_GroupKey, dict[str, int | float]] = {
        key: {
            name: round(value, SCORE_DECIMALS) if name in FLOAT_COUNTERS else int(value)
            for name, value in zip(_EFFECTIVENESS_FIELDS, total, strict=True)
        }
        # a fixed order (all time first): new rows get their ids in it
        for key, total in sorted(sums.items(), key=lambda item: (item[0][0], item[0][1] or 0, *item[0][2:]))
    }
    existing = {key_of(r): r for r in existing_rows}
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
    update_rows(model, changed, list(_EFFECTIVENESS_FIELDS))
    model.objects.bulk_create(new)


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
    return (
        row.killer_aircraft_id,
        row.victim_aircraft_id,
        row.tour_id,
        row.intercept,
        row.scoped_side,
        row.combat_role,
        row.mod_pattern,
    )


def _scope_counts(kills: QuerySet[Kill]) -> dict[ScopedPair, int]:
    """The kills counted into every scope they belong to: all time, their tour, and (when both sorties were air
    superiority) the same two for intercept fights; and, for each side, the role and modification scopes of that side's
    sortie (`scopes_of`, minus the unscoped one)."""
    rows = list(
        kills.values(
            "killer_sortie__aircraft_id",
            "victim_sortie__aircraft_id",
            "mission__tour_id",
            "killer_sortie__combat_role",
            "victim_sortie__combat_role",
            "killer_sortie__weapon_mods",
            "victim_sortie__weapon_mods",
        ).annotate(n=Count("pk"))
    )
    significant = significant_mods(
        {r[side] for r in rows for side in ("killer_sortie__aircraft_id", "victim_sortie__aircraft_id")}
    )
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
        scopes: list[tuple[str, str, str]] = [("", ALL, "")]
        for side, aircraft in (("killer", killer), ("victim", victim)):
            for _, role, pattern in scopes_of(
                None,
                row[f"{side}_sortie__combat_role"] or "",
                row[f"{side}_sortie__weapon_mods"],
                significant.get(aircraft, ()),
            ):
                if (role, pattern) != (ALL, ""):
                    scopes.append((side, role, pattern))
        tours: tuple[int | None, ...] = (None,) if tour is None else (None, tour)
        for scope_tour in tours:
            for intercept in (False, True) if both_air else (False,):
                for side, role, pattern in scopes:
                    key = (killer, victim, scope_tour, intercept, side, role, pattern)
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
                    killer_aircraft_id=key[0],
                    victim_aircraft_id=key[1],
                    tour_id=key[2],
                    intercept=key[3],
                    scoped_side=key[4],
                    combat_role=key[5],
                    mod_pattern=key[6],
                    kills=kills,
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
        | set(AircraftMods.objects.values_list("aircraft_id", flat=True))
        | set(PlayerAircraftScope.objects.values_list("aircraft_id", flat=True))
    )
    recompute_aircraft_stats(ids, None)
    recompute_matchups(None)


type CellKey = tuple[int, int | None, str, int]  # aircraft, tour (None = all time), role (`AircraftRole`), WM
type PatternKey = tuple[int, int | None, str, str]  # aircraft, tour (None = all time), role, pattern
type Totals = dict[str, int | float]


@dataclass(slots=True)
class _Cell:
    totals: Totals = field(
        default_factory=lambda: {name: 0.0 if name in FLOAT_COUNTERS else 0 for name in COUNTER_FIELDS}
    )
    players: set[int] = field(default_factory=set[int])
    sides: dict[str, int] = field(default_factory=dict[str, int])  # side -> sorties


def _fold(sums: Totals, row: dict[str, object]) -> None:
    for name in COUNTER_FIELDS:
        value = row[name]
        if isinstance(value, int | float):
            sums[name] += value


def pattern_stats(
    significant: dict[int, tuple[int, ...]], tour_ids: list[int] | None
) -> dict[PatternKey, tuple[Totals, int, str]]:
    """(counters, pilots, side) of every filter pattern scope of the types in `significant`: per tour (only those in
    `tour_ids`, None = every tour) and all time, per role (`all` and each combat role that has sorties)."""
    if not significant:
        return {}
    wanted_tours = None if tour_ids is None else set(tour_ids)
    rows = (
        counted_sorties()
        .filter(aircraft_id__in=sorted(significant))
        .values("aircraft_id", "mission__tour_id", "combat_role", "weapon_mods", "player_id", "country")
        .annotate(**SORTIE_COUNTERS)
        .order_by("aircraft_id", "mission__tour_id", "combat_role", "weapon_mods", "player_id", "country")
    )
    cells: dict[CellKey, _Cell] = {}
    for row in rows:
        aircraft, tour, mods = row["aircraft_id"], row["mission__tour_id"], row["weapon_mods"]
        side = side_of_country(row["country"])
        roles = (ALL,) if row["combat_role"] is None else (ALL, row["combat_role"])
        scopes: tuple[int | None, ...] = (None,) if tour is None else (None, tour)
        for scope in scopes:
            if scope is not None and wanted_tours is not None and scope not in wanted_tours:
                continue
            for role in roles:
                cell = cells.setdefault((aircraft, scope, role, mods), _Cell())
                _fold(cell.totals, row)
                cell.players.add(row["player_id"])
                if side is not None:
                    cell.sides[side] = cell.sides.get(side, 0) + row["sorties"]
    return _fold_patterns(cells, significant)


def _fold_patterns(
    cells: dict[CellKey, _Cell], significant: dict[int, tuple[int, ...]]
) -> dict[PatternKey, tuple[Totals, int, str]]:
    merged: dict[PatternKey, _Cell] = {}
    for (aircraft, tour, role, mods), cell in sorted(cells.items(), key=_cell_order):
        for pattern in mod_filter_patterns(mods, significant[aircraft]):
            target = merged.setdefault((aircraft, tour, role, pattern), _Cell())
            for name, value in cell.totals.items():
                target.totals[name] += value
            target.players |= cell.players
            for side, n in cell.sides.items():
                target.sides[side] = target.sides.get(side, 0) + n
    return {
        key: (cell.totals, len(cell.players), min(cell.sides, key=lambda s: (-cell.sides[s], s)) if cell.sides else "")
        for key, cell in merged.items()
    }


def _cell_order(item: tuple[CellKey, _Cell]) -> tuple[int, int, str, int]:
    """A fixed order to add the cells in (all time first): a rebuild sums floats like an incremental run."""
    aircraft, tour, role, mods = item[0]
    return aircraft, -1 if tour is None else tour, role, mods


type PlayerScopeKey = tuple[int, int, int | None, str, str]  # aircraft, player, tour (None = all time), role, pattern


def player_scope_stats(
    aircraft_ids: Iterable[int], significant: dict[int, tuple[int, ...]], tour_ids: list[int] | None
) -> dict[PlayerScopeKey, Totals]:
    """The counters of every player in each of these types per scope (`scopes_of`): tours in `tour_ids` (None = every
    tour) and all time, every role and each combat role, unfiltered and each mod pattern, except the all-time, `all`,
    unfiltered scope (that is `PlayerAircraft`). One grouped query over the types' history, in a fixed order so a
    rebuild sums the floats like an incremental run."""
    wanted_tours = None if tour_ids is None else set(tour_ids)
    rows = (
        counted_sorties()
        .filter(aircraft_id__in=sorted(set(aircraft_ids)))
        .values("aircraft_id", "player_id", "mission__tour_id", "combat_role", "weapon_mods")
        .annotate(**SORTIE_COUNTERS)
        .order_by("aircraft_id", "player_id", "mission__tour_id", "combat_role", "weapon_mods")
    )
    found: dict[PlayerScopeKey, Totals] = {}
    for row in rows:
        aircraft, player = row["aircraft_id"], row["player_id"]
        for tour, role, pattern in scopes_of(
            row["mission__tour_id"], row["combat_role"] or "", row["weapon_mods"], significant.get(aircraft, ())
        ):
            if (tour is None and role == ALL and not pattern) or (
                tour is not None and wanted_tours is not None and tour not in wanted_tours
            ):
                continue
            _fold(
                found.setdefault(
                    (aircraft, player, tour, role, pattern),
                    {name: 0.0 if name in FLOAT_COUNTERS else 0 for name in COUNTER_FIELDS},
                ),
                row,
            )
    return found


type _AmmoKey = tuple[int, int | None, str, str, str, str]  # aircraft, tour, role, pattern, mix ('' = per ammo), ammo


def recompute_aircraft_ammo(aircraft_ids: Iterable[int]) -> None:
    """Hits to destroy (FR-WEB-18): `AircraftAmmoStats` and `AircraftAmmoMixStats` for these victim aircraft types =
    the sums of their `MissionAircraftAmmo` / `MissionAircraftAmmoMix` rows over the missions of every scope (all time
    and each tour, every role and each combat role, no filter and each modification pattern of the types with
    significant mods: the role and mods are those of the destroyed aircraft's sortie, a victim that was not a player
    sortie counts for `all` roles without a filter only); rows with nothing left are deleted. Cheap enough to run for
    every type (a few dozen rows per mission), so `save_mission` and the rebuild share it."""
    ids = sorted(set(aircraft_ids))
    for start in range(0, len(ids), CHUNK):
        chunk = ids[start : start + CHUNK]
        significant = significant_mods(chunk)
        singles = _scoped_ammo(
            MissionAircraftAmmo.objects.filter(aircraft_id__in=chunk)
            .values("aircraft_id", "mission__tour_id", "combat_role", "weapon_mods", "ammo")
            .annotate(n=Sum("kills"), h=Sum("hits")),
            significant,
            None,
        )
        _sync_ammo(AircraftAmmoStats, AircraftAmmoStats.objects.filter(aircraft_id__in=chunk), singles, "")
        mixes = _scoped_ammo(
            MissionAircraftAmmoMix.objects.filter(aircraft_id__in=chunk)
            .values("aircraft_id", "mission__tour_id", "combat_role", "weapon_mods", "mix", "ammo")
            .annotate(n=Sum("kills"), h=Sum("hits")),
            significant,
            "mix",
        )
        _sync_ammo(AircraftAmmoMixStats, AircraftAmmoMixStats.objects.filter(aircraft_id__in=chunk), mixes, "mix")


def _scoped_ammo(
    rows: Iterable[Mapping[str, object]], significant: dict[int, tuple[int, ...]], mix_field: str | None
) -> dict[_AmmoKey, tuple[int, int]]:
    """The (kills, hits) of every scope: the level-1 rows summed per (type, tour, role, WM, mix, ammo), each added to
    every scope its destroyed aircraft's sortie belongs to (`aircraft_mods.scopes_of`)."""
    wanted: dict[_AmmoKey, tuple[int, int]] = {}
    for row in rows:
        aircraft, tour_id = int(str(row["aircraft_id"])), row["mission__tour_id"]
        mix = str(row[mix_field]) if mix_field else ""
        scopes = scopes_of(
            None if tour_id is None else int(str(tour_id)),
            str(row["combat_role"]),
            int(str(row["weapon_mods"])),
            significant.get(aircraft, ()),
        )
        for tour, role, pattern in scopes:
            key = (aircraft, tour, role, pattern, mix, str(row["ammo"]))
            kills, hits = wanted.get(key, (0, 0))
            wanted[key] = (kills + int(str(row["n"])), hits + int(str(row["h"])))
    return wanted


def _sync_ammo[M: models.Model](
    model: type[M], existing_rows: QuerySet[M], wanted: dict[_AmmoKey, tuple[int, int]], mix_field: str
) -> None:
    """Make the scoped hits-to-destroy rows equal `wanted` (new created, changed updated, the others deleted)."""
    existing = {
        (
            getattr(r, "aircraft_id"),  # noqa: B009
            getattr(r, "tour_id"),  # noqa: B009
            getattr(r, "role"),  # noqa: B009
            getattr(r, "mod_pattern"),  # noqa: B009
            getattr(r, mix_field) if mix_field else "",
            getattr(r, "ammo"),  # noqa: B009
        ): r
        for r in existing_rows
    }
    changed: list[M] = []
    new: list[M] = []
    for key, (kills, hits) in sorted(wanted.items(), key=lambda item: (item[0][0], item[0][1] or 0, *item[0][2:])):
        row = existing.pop(key, None)
        if row is None:
            extra = {mix_field: key[4]} if mix_field else {}
            new.append(
                model(
                    aircraft_id=key[0],
                    tour_id=key[1],
                    role=key[2],
                    mod_pattern=key[3],
                    ammo=key[5],
                    kills=kills,
                    hits=hits,
                    **extra,
                )
            )
        elif (getattr(row, "kills"), getattr(row, "hits")) != (kills, hits):  # noqa: B009
            setattr(row, "kills", kills)  # noqa: B010
            setattr(row, "hits", hits)  # noqa: B010
            changed.append(row)
    model.objects.filter(pk__in=[r.pk for r in existing.values()]).delete()
    update_rows(model, changed, ["kills", "hits"])
    model.objects.bulk_create(new)
