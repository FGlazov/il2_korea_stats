"""Level-2 stats per aircraft type (FR-WEB-8): `AircraftStats`, `TourAircraftStats`, `AircraftMatchup`,
`AircraftPayload`, `AircraftMods`, `PlayerAircraftScope`. Aggregation in `ingest` only (TD-22); the aircraft pages just
read these rows.

Like the player aggregates they are always recomputed from lower rows, never adjusted by deltas, so incremental ==
rebuild by construction. Two steps (doc 14 "Level-2 refresh"), called by `aggregates.refresh_tours`:

1. The **tour rows** (`recompute_aircraft_tour_rows`, `recompute_matchup_tours`): one tour's rows of a type are counted
   from that tour's level-1 rows only (the counted sorties of its missions; `PlayerTourAircraft` for the `all` role).
   A refresh of a tour never reads another tour's sorties.
2. The **all-time rows** (`rollup_aircraft_stats`, `rollup_matchups`): a roll-up of the tour rows of the type (SUM, and
   an argmax for the side), never of level 1:
   - `AircraftStats` = the sum of the type's `TourAircraftStats` rows (role `all`, no mod pattern); `pilots` = the
     count of its `PlayerAircraft` rows (a union, so it can't be summed); `side` = the larger of the summed
     `sorties_redfor` / `sorties_blufor` counters (ties: REDFOR). No ratio is stored: K/D and the like are computed at
     read time (OQ-98).
   - `TourAircraftStats` with a null tour (role and mod-pattern rows) = the sum of the tour rows of the same role and
     pattern; `pilots` = the count of the scope's all-time `PlayerAircraftScope` rows; `side` as above.
   - `PlayerAircraftScope` all-time rows = the sum of the player's tour rows per (type, role, pattern).
   - `AircraftPayload` / `AircraftMods` all-time rows = the sum of the tour rows per loadout name / weapon-mod set,
     role and pattern (`elo_avg` is not summable: `recompute_payload_elo`, after the ratings).
   - `AircraftMatchup` all-time rows = the sum of the tour rows per scope.

`TourAircraftStats` = per tour (TD-26) and combat role: `all` rows are summed from the type's `PlayerTourAircraft`
rows; `air_superiority` / `attack` rows are counted sorties grouped by `combat_role`, with the distinct pilots. A type
with significant weapon mods has more rows per tour and role, one per filter pattern (`ingest.aircraft_mods`). Each
row counts its sorties per side (`sorties_redfor`, `sorties_blufor`); its `side` is the larger one.
`AircraftPayload` / `AircraftMods` = counted sorties grouped by loadout name / weapon-mod set, tour, combat role and
(for such a type) filter pattern, plus the average pilot Elo of an air superiority group (`recompute_payload_elo`).
`AircraftMatchup` = enemy PvP `Kill` rows between pilot sorties, grouped by (killer type, victim type) and scope (tour,
all kills / intercept fights where both sorties were air superiority), plus the role / mod pattern scopes of the
killer's or the victim's sortie (`scoped_side`). `PlayerAircraftScope` = one player's counters in a type per tour, role
and mod pattern (the top pilots of the aircraft page; the all-time unfiltered one is `PlayerAircraft`).

The **aircraft type Elo** (`elo`, `elo_games`; maintainer 2026-10-05) is stored on the unfiltered `all` and
`air_superiority` role rows of each tour (`TourAircraftStats`) and, all time, on `AircraftStats` and the null-tour
`air_superiority` row. The tour step replays the tour's air superiority duels between types (`rating_games`,
`core.ratings.elo.compute_type_ratings`: a clean slate per tour, the pilot Elo's order); the roll-up takes the best
tour with at least `RatingRules.type_min_games` games (`best_of_tours`) and sums the games. Both only run when the
caller passes the `[ratings]` rules (a live pass passes none and leaves the Elo alone).

A mission always has a tour (`save_mission` gives it one), so the tour rows cover every sortie: the sum over the tours
is the whole history.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from django.db.models import Count, Q, QuerySet, Sum

from il2ks.core.catalog.loader import mod_filter_patterns, side_of_country
from il2ks.core.ratings.elo import RatingRules, best_of_tours, compute_type_ratings
from il2ks.db.models import (
    AircraftEffectiveness,
    AircraftMatchup,
    AircraftMods,
    AircraftPayload,
    AircraftRole,
    AircraftStats,
    CombatRole,
    GameObject,
    Kill,
    KillCredit,
    Player,
    PlayerAircraft,
    PlayerAircraftScope,
    PlayerTourAircraft,
    Propulsion,
    Role,
    Tour,
    TourAircraftStats,
)
from il2ks.ingest.aircraft_mods import pattern_tour_stats, player_scope_stats, scopes_of, significant_mods
from il2ks.ingest.counters import (
    COUNTER_FIELDS,
    FLOAT_COUNTERS,
    SCORE_DECIMALS,
    SORTIE_COUNTERS,
    CounterValues,
    clean_counters,
    counted_sorties,
    round_scores,
)
from il2ks.ingest.dbutil import delete_pks, sync_rows, update_partial_rows, update_rows
from il2ks.ingest.rating_games import rated_games
from il2ks.ingest.rollup import ROUND_DECIMALS

CHUNK = 2000  # types per batch, as aggregates.CHUNK
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


def recompute_aircraft_stats(
    aircraft_ids: Iterable[int], tour_ids: Iterable[int] | None, rules: RatingRules | None = None
) -> None:
    """Both steps for these types: the tour rows of the tours in `tour_ids` (None = every tour), then the all-time rows
    rolled up from them. `refresh_tours` calls the steps apart (`recompute_aircraft_tour_rows`,
    `rollup_aircraft_stats`) because the all-time step runs after every tour step. Rows of types without counted
    sorties are deleted. The payload Elo is not here: it needs the ratings (`recompute_payload_elo`). `rules`: the
    `[ratings]` rules of the aircraft type Elo (None: leave it alone)."""
    ids = sorted(set(aircraft_ids))
    recompute_aircraft_tour_rows(ids, tour_ids, rules)
    rollup_aircraft_stats(ids, rules)


def recompute_aircraft_tour_rows(
    aircraft_ids: Iterable[int], tour_ids: Iterable[int] | None, rules: RatingRules | None = None
) -> None:
    """The tour step: `TourAircraftStats`, `PlayerAircraftScope`, `AircraftPayload` and `AircraftMods` rows of the tours
    in `tour_ids` (None = every tour) for these types, from those tours' level-1 rows (and `PlayerTourAircraft`), and,
    with `rules`, the types' Elo in those tours (`_store_type_elo`: replayed from the tours' kills alone)."""
    ids = sorted(set(aircraft_ids))
    tours = None if tour_ids is None else sorted(set(tour_ids))
    for start in range(0, len(ids), CHUNK):
        chunk = ids[start : start + CHUNK]
        significant = significant_mods(chunk)
        _recompute_effectiveness(chunk, _sortie_groups(chunk, tours), significant, tours)
        _recompute_tour_stats(chunk, tours, significant)
        _recompute_player_scopes(chunk, tours, significant)
    if rules is not None:
        _store_type_elo(ids, tours, rules)


def rollup_aircraft_stats(aircraft_ids: Iterable[int], rules: RatingRules | None = None) -> None:
    """The all-time step: `AircraftStats` and every all-time (null tour) row of these types, rolled up from their tour
    rows. After `recompute_aircraft_tour_rows` and the players' `PlayerAircraft` rows (the pilot count). With `rules`
    also the types' all-time Elo (`_rollup_type_elo`), from their tour rows only."""
    ids = sorted(set(aircraft_ids))
    for start in range(0, len(ids), CHUNK):
        chunk = ids[start : start + CHUNK]
        _rollup_player_scopes(chunk)
        _rollup_stats(chunk)
        if rules is not None:
            _rollup_type_elo(chunk, rules)
        _rollup_effectiveness(chunk)


ELO_ROLES = (AircraftRole.ALL.value, AircraftRole.AIR_SUPERIORITY.value)
"""The role rows that carry the type Elo: every sortie and air superiority sorties (the Elo is air superiority by
definition, so both show the same rating; an attack row has none)."""


def _store_type_elo(aircraft_ids: list[int], tour_ids: list[int] | None, rules: RatingRules) -> None:
    """The types' Elo in each tour of `tour_ids` (None = every tour): that tour's air superiority duels between types
    replayed alone from a clean slate, written to the type's unfiltered `all` and `air_superiority` rows of the tour
    (`rules.start` and 0 games where the type had no duel). Reads that tour's kills only."""
    tours = sorted(Tour.objects.values_list("pk", flat=True)) if tour_ids is None else tour_ids
    for tour in tours:
        computed = compute_type_ratings(rated_games(tour), rules)
        for start in range(0, len(aircraft_ids), CHUNK):
            rows = TourAircraftStats.objects.filter(
                tour_id=tour, aircraft_id__in=aircraft_ids[start : start + CHUNK], role__in=ELO_ROLES, mod_pattern=""
            )
            changed: list[TourAircraftStats] = []
            for pk, aircraft, elo, games in rows.values_list("pk", "aircraft_id", "elo", "elo_games"):
                found = computed.get(aircraft)
                wanted = (rules.start, 0) if found is None else (found.rating, found.games)
                if wanted != (elo, games):
                    changed.append(TourAircraftStats(pk=pk, elo=wanted[0], elo_games=wanted[1]))
            update_partial_rows(TourAircraftStats, changed, ["elo", "elo_games"])


def _rollup_type_elo(chunk: list[int], rules: RatingRules) -> None:
    """`AircraftStats.elo*` and the all-time `air_superiority` row's: the best tour's final rating among the tours with
    at least `rules.type_min_games` games (the best of all tours when none reaches it), the games of all tours summed.
    From the tour rows of the types only, never from the kills."""
    rated = TourAircraftStats.objects.filter(
        aircraft_id__in=chunk, tour__isnull=False, role__in=ELO_ROLES, mod_pattern="", elo_games__gt=0
    )
    best = best_of_tours(
        [
            ((aircraft, role), elo, games)
            for aircraft, role, elo, games in rated.values_list("aircraft_id", "role", "elo", "elo_games")
        ],
        rules.type_min_games,
    )
    changed_all: list[AircraftStats] = []
    for pk, aircraft, elo, games in AircraftStats.objects.filter(aircraft_id__in=chunk).values_list(
        "pk", "aircraft_id", "elo", "elo_games"
    ):
        found = best.get((aircraft, AircraftRole.ALL.value))
        wanted = (rules.start, 0) if found is None else (found.rating, found.games)
        if wanted != (elo, games):
            changed_all.append(AircraftStats(pk=pk, elo=wanted[0], elo_games=wanted[1]))
    update_partial_rows(AircraftStats, changed_all, ["elo", "elo_games"])
    changed_roles: list[TourAircraftStats] = []
    all_time_rows = TourAircraftStats.objects.filter(
        aircraft_id__in=chunk, tour__isnull=True, role=AircraftRole.AIR_SUPERIORITY.value, mod_pattern=""
    )
    for pk, aircraft, elo, games in all_time_rows.values_list("pk", "aircraft_id", "elo", "elo_games"):
        found = best.get((aircraft, AircraftRole.AIR_SUPERIORITY.value))
        wanted = (rules.start, 0) if found is None else (found.rating, found.games)
        if wanted != (elo, games):
            changed_roles.append(TourAircraftStats(pk=pk, elo=wanted[0], elo_games=wanted[1]))
    update_partial_rows(TourAircraftStats, changed_roles, ["elo", "elo_games"])


type _Wanted[K] = dict[K, dict[str, int | float | str]]
type _TourKey = tuple[int, int | None, str, str]  # aircraft, tour (None = all time), role (`AircraftRole`), mod pattern
ALL = AircraftRole.ALL.value
_SUMS = {name: Sum(name) for name in COUNTER_FIELDS}
_STAT_FIELDS = [*COUNTER_FIELDS, "pilots", "side"]
_TOUR_STAT_FIELDS = [*_STAT_FIELDS, "sorties_redfor", "sorties_blufor"]
_TOUR_KEY = ("aircraft_id", "tour_id", "role", "mod_pattern")


def _side_of(redfor: int, blufor: int) -> str:
    """The side with more sorties; a tie: the name that sorts first (REDFOR); no sorties for either: ''."""
    if redfor == 0 and blufor == 0:
        return ""
    return "redfor" if redfor >= blufor else "blufor"


def _tour_stat_values(
    total: Mapping[str, object], pilots: int, redfor: int, blufor: int
) -> dict[str, int | float | str]:
    return {
        **clean_counters(total),
        "pilots": pilots,
        "side": _side_of(redfor, blufor),
        "sorties_redfor": redfor,
        "sorties_blufor": blufor,
    }


def _rollup_stats(chunk: list[int]) -> None:
    """`AircraftStats` and the all-time `TourAircraftStats` rows of these types = sums of their tour rows."""
    sums = {**_SUMS, "red": Sum("sorties_redfor"), "blue": Sum("sorties_blufor")}
    tour_rows = TourAircraftStats.objects.filter(aircraft_id__in=chunk, tour__isnull=False)
    pilots_all = dict(
        PlayerAircraft.objects.filter(aircraft_id__in=chunk)
        .values_list("aircraft_id")
        .annotate(n=Count("pk"))
        .order_by()
    )
    pilots_scope = {
        (aircraft, role, pattern): n
        for aircraft, role, pattern, n in PlayerAircraftScope.objects.filter(
            aircraft_id__in=chunk, tour__isnull=True, sorties__gt=0
        )
        .values_list("aircraft_id", "role", "mod_pattern")
        .annotate(n=Count("pk"))
        .order_by()
    }
    wanted_all: _Wanted[tuple[int]] = {}
    wanted: _Wanted[_TourKey] = {}
    for row in tour_rows.values("aircraft_id", "role", "mod_pattern").annotate(**sums).order_by():
        aircraft, role, pattern = row["aircraft_id"], row["role"], row["mod_pattern"]
        red, blue = row["red"] or 0, row["blue"] or 0
        if role == ALL and not pattern:
            wanted_all[(aircraft,)] = {
                **clean_counters(row),
                "pilots": pilots_all.get(aircraft, 0),
                "side": _side_of(red, blue),
            }
        else:
            wanted[(aircraft, None, role, pattern)] = _tour_stat_values(
                row, pilots_scope.get((aircraft, role, pattern), 0), red, blue
            )
    sync_rows(
        AircraftStats, AircraftStats.objects.filter(aircraft_id__in=chunk), ("aircraft_id",), _STAT_FIELDS, wanted_all
    )
    sync_rows(
        TourAircraftStats,
        TourAircraftStats.objects.filter(aircraft_id__in=chunk, tour__isnull=True),
        _TOUR_KEY,
        _TOUR_STAT_FIELDS,
        wanted,
    )


def _recompute_tour_stats(
    chunk: list[int], tour_ids: list[int] | None, significant: dict[int, tuple[int, ...]]
) -> None:
    """The tour `TourAircraftStats` rows of these types (the tours in `tour_ids`, None = all): the `all` role from the
    players' per-tour aircraft rows, every combat role from the counted sorties of the tour (`pilots` = distinct
    players, which no sum of rows can give); and, for a type with significant weapon mods, every filter pattern of
    those scopes (`aircraft_mods`). Each row also counts its sorties per side."""
    rows = PlayerTourAircraft.objects.filter(aircraft_id__in=chunk)
    tour_sorties = counted_sorties().filter(aircraft_id__in=chunk, tour__isnull=False)
    existing_rows = TourAircraftStats.objects.filter(aircraft_id__in=chunk, tour__isnull=False)
    if tour_ids is not None:
        rows = rows.filter(tour_id__in=tour_ids)
        existing_rows = existing_rows.filter(tour_id__in=tour_ids)
        tour_sorties = tour_sorties.filter(tour_id__in=tour_ids)
    by_side: dict[tuple[int, int, str], list[int]] = {}  # (aircraft, tour, role) -> [REDFOR, BLUFOR] sorties
    grouped = tour_sorties.values("aircraft_id", "tour_id", "combat_role", "country").annotate(n=Count("pk"))
    for found in grouped.order_by():
        side = side_of_country(found["country"])
        if side is not None:
            for role in (ALL, found["combat_role"]):
                if role is not None:
                    counts = by_side.setdefault((found["aircraft_id"], found["tour_id"], role), [0, 0])
                    counts[0 if side == "redfor" else 1] += found["n"]
    wanted: _Wanted[_TourKey] = {}
    for row in rows.values("aircraft_id", "tour_id").annotate(pilots=Count("pk"), **_SUMS).order_by():
        red, blue = by_side.get((row["aircraft_id"], row["tour_id"], ALL), [0, 0])
        wanted[(row["aircraft_id"], row["tour_id"], ALL, "")] = _tour_stat_values(row, row["pilots"], red, blue)
    distinct = {"pilots": Count("player_id", distinct=True), **SORTIE_COUNTERS}
    per_tour = tour_sorties.filter(combat_role__isnull=False).values("aircraft_id", "tour_id", "combat_role")
    for row in per_tour.annotate(**distinct).order_by():
        tour = row["tour_id"]
        red, blue = by_side.get((row["aircraft_id"], tour, row["combat_role"]), [0, 0])
        wanted[(row["aircraft_id"], tour, row["combat_role"], "")] = _tour_stat_values(row, row["pilots"], red, blue)
    for (aircraft, tour, role, pattern), (totals, pilots, red, blue) in pattern_tour_stats(
        significant, tour_ids
    ).items():
        wanted[(aircraft, tour, role, pattern)] = _tour_stat_values(totals, pilots, red, blue)
    sync_rows(TourAircraftStats, existing_rows, _TOUR_KEY, _TOUR_STAT_FIELDS, wanted)


def _recompute_player_scopes(
    chunk: list[int], tour_ids: list[int] | None, significant: dict[int, tuple[int, ...]]
) -> None:
    """The tour `PlayerAircraftScope` rows of these types (the top pilots of the aircraft page): every player's counters
    per tour, role and mod pattern, from the tours' counted sorties. Only the tours in `tour_ids` (None = all)."""
    wanted = player_scope_stats(chunk, significant, tour_ids)
    existing_rows = PlayerAircraftScope.objects.filter(aircraft_id__in=chunk, tour__isnull=False)
    if tour_ids is not None:
        existing_rows = existing_rows.filter(tour_id__in=tour_ids)
    _sync_player_scopes({key: round_scores(totals) for key, totals in wanted.items()}, existing_rows)


def _rollup_player_scopes(chunk: list[int]) -> None:
    """The all-time `PlayerAircraftScope` rows of these types = the sum of each player's tour rows per (type, role,
    pattern), except the every-role, unfiltered one (`PlayerAircraft`)."""
    totals: dict[tuple[int, int, int | None, str, str], CounterValues] = {}
    rows = (
        PlayerAircraftScope.objects.filter(aircraft_id__in=chunk, tour__isnull=False)
        .order_by("aircraft_id", "player_id", "role", "mod_pattern", "tour_id")  # fixed order: the same float additions
        .values("aircraft_id", "player_id", "role", "mod_pattern", *COUNTER_FIELDS)
    )
    for row in rows:
        if row["role"] == ALL and not row["mod_pattern"]:
            continue
        total = totals.setdefault(
            (row["aircraft_id"], row["player_id"], None, row["role"], row["mod_pattern"]),
            {name: 0.0 if name in FLOAT_COUNTERS else 0 for name in COUNTER_FIELDS},
        )
        for name in COUNTER_FIELDS:
            total[name] += row[name]
    wanted = {key: _rounded(total) for key, total in totals.items()}
    _sync_player_scopes(wanted, PlayerAircraftScope.objects.filter(aircraft_id__in=chunk, tour__isnull=True))


def _rounded(totals: CounterValues) -> CounterValues:
    """`totals` summed in Python with every float counter rounded (`ROUND_DECIMALS`, as `rollup.py` does at write)."""
    return {name: round(value, ROUND_DECIMALS) if name in FLOAT_COUNTERS else value for name, value in totals.items()}


def _sync_player_scopes(
    wanted: Mapping[tuple[int, int, int | None, str, str], Mapping[str, int | float]],
    existing_rows: QuerySet[PlayerAircraftScope],
) -> None:
    sync_rows(
        PlayerAircraftScope,
        existing_rows,
        ("aircraft_id", "player_id", "tour_id", "role", "mod_pattern"),
        COUNTER_FIELDS,
        wanted,
    )


@dataclass(frozen=True, slots=True)
class _Group:
    """The counted sorties of one type, tour, loadout, weapon mods and combat role ('' = none), summed."""

    aircraft_id: int
    tour: int
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


def _sortie_groups(chunk: list[int], tour_ids: list[int] | None) -> list[_Group]:
    """The types' counted sorties of these tours (None = all) grouped by tour, payload, weapon mods and role, in ONE
    pass: the loadout and mods rows both come from these groups instead of a grouped query each."""
    sorties = counted_sorties().filter(aircraft_id__in=chunk, tour__isnull=False)
    if tour_ids is not None:
        sorties = sorties.filter(tour_id__in=tour_ids)
    rows = (
        sorties.values("aircraft_id", "tour_id", "payload_name", "weapon_mods", "combat_role")
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
        .order_by("aircraft_id", "tour_id", "payload_name", "weapon_mods", "combat_role")  # fixed float order
    )
    return [
        _Group(
            r["aircraft_id"],
            r["tour_id"],
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


def _recompute_effectiveness(
    chunk: list[int], groups: list[_Group], significant: dict[int, tuple[int, ...]], tour_ids: list[int] | None
) -> None:
    """The tour `AircraftPayload` rows per (type, tour, loadout, role, mod pattern) and `AircraftMods` rows (type, tour,
    weapon-mod set, role, mod pattern), from the same groups.
    `elo_avg` is not touched here (`recompute_payload_elo`)."""
    payloads: dict[_GroupKey, list[float]] = {}
    mods: dict[_GroupKey, list[float]] = {}
    for g in groups:
        for pattern in ("", *mod_filter_patterns(g.mods, significant.get(g.aircraft_id, ()))):
            for sums, value in ((payloads, g.payload_name), (mods, g.mods)):
                total = sums.setdefault(
                    (g.aircraft_id, g.tour, value, g.role, pattern), [0.0] * len(_EFFECTIVENESS_FIELDS)
                )
                for i, name in enumerate(_EFFECTIVENESS_FIELDS):
                    total[i] += getattr(g, name)
    payload_rows = AircraftPayload.objects.filter(aircraft_id__in=chunk, tour__isnull=False)
    mod_rows = AircraftMods.objects.filter(aircraft_id__in=chunk, tour__isnull=False)
    if tour_ids is not None:
        payload_rows = payload_rows.filter(tour_id__in=tour_ids)
        mod_rows = mod_rows.filter(tour_id__in=tour_ids)
    _sync_effectiveness(AircraftPayload, payload_rows, payloads, "payload_name")
    _sync_effectiveness(AircraftMods, mod_rows, mods, "weapon_mods")


def _rollup_effectiveness(chunk: list[int]) -> None:
    """The all-time `AircraftPayload` / `AircraftMods` rows of these types = the sums of their tour rows per (type,
    loadout or weapon-mod set, role, pattern)."""
    for name_field, rows in (
        ("payload_name", AircraftPayload.objects.filter(aircraft_id__in=chunk, tour__isnull=False)),
        ("weapon_mods", AircraftMods.objects.filter(aircraft_id__in=chunk, tour__isnull=False)),
    ):
        summed = (
            rows.values("aircraft_id", name_field, "combat_role", "mod_pattern")
            .annotate(**{f: Sum(f) for f in _EFFECTIVENESS_FIELDS})
            .order_by()
        )
        sums: dict[_GroupKey, list[float]] = {
            (r["aircraft_id"], None, r[name_field], r["combat_role"], r["mod_pattern"]): [
                float(r[f] or 0) for f in _EFFECTIVENESS_FIELDS
            ]
            for r in summed
        }
        if name_field == "payload_name":
            _sync_effectiveness(
                AircraftPayload,
                AircraftPayload.objects.filter(aircraft_id__in=chunk, tour__isnull=True),
                sums,
                name_field,
            )
        else:
            _sync_effectiveness(
                AircraftMods, AircraftMods.objects.filter(aircraft_id__in=chunk, tour__isnull=True), sums, name_field
            )


def _sync_effectiveness[M: AircraftEffectiveness](
    model: type[M], existing_rows: QuerySet[M], sums: dict[_GroupKey, list[float]], name_field: str
) -> None:
    """Make these rows (a tour's or the all-time ones) equal `sums`: the identity of a row is its key."""
    wanted: dict[_GroupKey, dict[str, int | float]] = {
        key: {
            name: round(value, SCORE_DECIMALS) if name in FLOAT_COUNTERS else int(value)
            for name, value in zip(_EFFECTIVENESS_FIELDS, total, strict=True)
        }
        # a fixed order (all time first): new rows get their ids in it
        for key, total in sorted(sums.items(), key=lambda item: (item[0][0], item[0][1] or 0, *item[0][2:]))
    }
    key_fields = ("aircraft_id", "tour_id", name_field, "combat_role", "mod_pattern")
    sync_rows(model, existing_rows, key_fields, _EFFECTIVENESS_FIELDS, wanted)


@dataclass(frozen=True, slots=True)
class PilotElo:
    """The average pilot Elo of air superiority sorties, per group and mod pattern (see `average_pilot_elo`)."""

    by_payload: dict[tuple[int, int | None, str, str], float]  # (aircraft, tour or None, loadout name, pattern)
    by_mods: dict[tuple[int, int | None, int, str], float]  # (aircraft, tour or None, WM, pattern)


def average_pilot_elo() -> PilotElo:
    """The average Elo of the pilots of air superiority sorties, per (aircraft type, loadout) and (aircraft type,
    weapon-mod set) group and mod pattern, one vote per sortie: the reusable half of every "effectiveness by X" table.
    A pilot's Elo is the rating in the type if they have games in it (`PlayerAircraft.elo_games`), else their pool's
    rating (the propulsion of the type) if they have games there; a pilot with neither is left out, and a group with no
    rated pilot is absent. Rounded to `ELO_DECIMALS`. A new grouping adds a field to the vote key below."""
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
    # (aircraft, tour, loadout, WM) -> (sum of Elo, sorties)
    votes: dict[tuple[int, int | None, str, int], tuple[float, int]] = {}
    pilots = (
        counted_sorties()
        .filter(combat_role=CombatRole.AIR_SUPERIORITY)
        .values("aircraft_id", "tour_id", "payload_name", "weapon_mods", "player_id")
        .annotate(n=Count("pk"))
        # a fixed summing order: a rebuild gives the same floats
        .order_by("aircraft_id", "tour_id", "payload_name", "weapon_mods", "player_id")
    )
    for row in pilots:
        aircraft_id = row["aircraft_id"]
        elo = type_elo.get((row["player_id"], aircraft_id))
        if elo is None:
            elo = pool_elo.get((row["player_id"], propulsion.get(aircraft_id, "")))
        if elo is not None:
            key = (aircraft_id, row["tour_id"], row["payload_name"], row["weapon_mods"])
            total, n = votes.get(key, (0.0, 0))
            votes[key] = (total + elo * row["n"], n + row["n"])
    significant = significant_mods({key[0] for key in votes})
    payload_totals: dict[tuple[int, int | None, str, str], tuple[float, int]] = {}
    mods_totals: dict[tuple[int, int | None, int, str], tuple[float, int]] = {}
    ordered_votes = sorted(votes.items(), key=lambda item: (item[0][0], item[0][1] or 0, item[0][2], item[0][3]))
    for (aircraft_id, vote_tour, payload_name, mods), (total, n) in ordered_votes:
        for tour in (None,) if vote_tour is None else (None, vote_tour):
            for pattern in ("", *mod_filter_patterns(mods, significant.get(aircraft_id, ()))):
                p_total, p_n = payload_totals.get((aircraft_id, tour, payload_name, pattern), (0.0, 0))
                payload_totals[(aircraft_id, tour, payload_name, pattern)] = (p_total + total, p_n + n)
                m_total, m_n = mods_totals.get((aircraft_id, tour, mods, pattern), (0.0, 0))
                mods_totals[(aircraft_id, tour, mods, pattern)] = (m_total + total, m_n + n)
    return PilotElo(
        {key: round(total / n, ELO_DECIMALS) for key, (total, n) in payload_totals.items()},
        {key: round(total / n, ELO_DECIMALS) for key, (total, n) in mods_totals.items()},
    )


def recompute_payload_elo() -> None:
    """`elo_avg` of every air superiority loadout and weapon-mod row (`average_pilot_elo`); other roles and groups
    without a rated pilot stay null. Order-dependent like every Elo, so it is recomputed for ALL rows after
    `recompute_ratings` replayed the games (one code path for `save_mission` and a rebuild)."""
    averages = average_pilot_elo()
    changed_payloads: list[AircraftPayload] = []
    for payload in AircraftPayload.objects.all():
        wanted = (
            averages.by_payload.get((payload.aircraft_id, payload.tour_id, payload.payload_name, payload.mod_pattern))
            if payload.combat_role == CombatRole.AIR_SUPERIORITY
            else None
        )
        if payload.elo_avg != wanted:
            payload.elo_avg = wanted
            changed_payloads.append(payload)
    update_rows(AircraftPayload, changed_payloads, ["elo_avg"])
    changed_mods: list[AircraftMods] = []
    for mods in AircraftMods.objects.all():
        wanted = (
            averages.by_mods.get((mods.aircraft_id, mods.tour_id, mods.weapon_mods, mods.mod_pattern))
            if mods.combat_role == CombatRole.AIR_SUPERIORITY
            else None
        )
        if mods.elo_avg != wanted:
            mods.elo_avg = wanted
            changed_mods.append(mods)
    update_rows(AircraftMods, changed_mods, ["elo_avg"])


def recompute_matchups(pairs: Iterable[Pair] | None = None, tour_ids: Iterable[int] | None = None) -> None:
    """Both steps for these (killer type, victim type) pairs, or for all pairs (None, a rebuild): the tour rows of the
    tours in `tour_ids` (None = every tour), then the all-time rows rolled up from them."""
    recompute_matchup_tours(pairs, tour_ids)
    rollup_matchups(pairs)


def recompute_matchup_tours(pairs: Iterable[Pair] | None, tour_ids: Iterable[int] | None = None) -> None:
    """The tour step: `AircraftMatchup` rows (every scope: per tour, all kills and intercept fights only, role and mod
    pattern scopes) of the tours in `tour_ids` (None = every tour) for these pairs, from those tours' `Kill` rows. Rows
    without kills left are deleted."""
    tours = None if tour_ids is None else sorted(set(tour_ids))
    kills = matchup_kills().filter(mission__tour__isnull=False)
    existing = AircraftMatchup.objects.filter(tour__isnull=False)
    if tours is not None:
        kills = kills.filter(mission__tour_id__in=tours)
        existing = existing.filter(tour_id__in=tours)
    if pairs is None:
        _sync_matchups(_scope_counts(kills), {_key_of(r): r for r in existing})
        return
    for chunk in _pair_chunks(pairs):
        match = _any_of([Q(killer_sortie__aircraft_id=k, victim_sortie__aircraft_id=v) for k, v in chunk])
        found = _any_of([Q(killer_aircraft_id=k, victim_aircraft_id=v) for k, v in chunk])
        _sync_matchups(_scope_counts(kills.filter(match)), {_key_of(r): r for r in existing.filter(found)})


def rollup_matchups(pairs: Iterable[Pair] | None = None) -> None:
    """The all-time step: the all-time `AircraftMatchup` rows of these pairs (None = all) = the sums of their tour rows
    per scope (intercept, scoped side, role, mod pattern)."""

    def rolled(tour_rows: QuerySet[AircraftMatchup]) -> dict[ScopedPair, int]:
        grouped = tour_rows.values(
            "killer_aircraft_id", "victim_aircraft_id", "intercept", "scoped_side", "combat_role", "mod_pattern"
        ).annotate(total=Sum("kills"))
        return {
            (
                r["killer_aircraft_id"],
                r["victim_aircraft_id"],
                None,
                r["intercept"],
                r["scoped_side"],
                r["combat_role"],
                r["mod_pattern"],
            ): r["total"]
            for r in grouped.order_by()
        }

    tour_rows = AircraftMatchup.objects.filter(tour__isnull=False)
    all_time = AircraftMatchup.objects.filter(tour__isnull=True)
    if pairs is None:
        _sync_matchups(rolled(tour_rows), {_key_of(r): r for r in all_time})
        return
    for chunk in _pair_chunks(pairs):
        found = _any_of([Q(killer_aircraft_id=k, victim_aircraft_id=v) for k, v in chunk])
        _sync_matchups(rolled(tour_rows.filter(found)), {_key_of(r): r for r in all_time.filter(found)})


def _pair_chunks(pairs: Iterable[Pair]) -> Iterable[list[Pair]]:
    ordered = sorted(set(pairs))
    for start in range(0, len(ordered), PAIR_CHUNK):
        yield ordered[start : start + PAIR_CHUNK]


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
    """The kills counted into every tour scope they belong to: their tour, and (when both sorties were air superiority)
    the same for intercept fights; and, for each side, the role and modification scopes of that side's sortie
    (`scopes_of`, minus the unscoped one). The kills are those of the tours being refreshed, so this reads no other."""
    rows = list(
        kills.values(
            "killer_sortie__aircraft_id",
            "victim_sortie__aircraft_id",
            "mission__tour_id",
            "killer_sortie__combat_role",
            "victim_sortie__combat_role",
            "killer_sortie__weapon_mods",
            "victim_sortie__weapon_mods",
        )
        .annotate(n=Count("pk"))
        .order_by()
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
        for intercept in (False, True) if both_air else (False,):
            for side, role, pattern in scopes:
                key = (killer, victim, tour, intercept, side, role, pattern)
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
    delete_pks(AircraftMatchup.objects, [row.pk for row in existing.values()])
    update_rows(AircraftMatchup, changed, ["kills"])
    AircraftMatchup.objects.bulk_create(new)


def rebuild_aircraft_stats(rules: RatingRules | None = None) -> None:
    """Every aircraft-type row from scratch (`il2ks rebuild-aggregates`, with the type Elo under `rules`); needs the
    player rows rebuilt first."""
    ids = (
        set(PlayerAircraft.objects.values_list("aircraft_id", flat=True))
        | set(AircraftStats.objects.values_list("aircraft_id", flat=True))
        | set(TourAircraftStats.objects.values_list("aircraft_id", flat=True))
        | set(AircraftPayload.objects.values_list("aircraft_id", flat=True))
        | set(AircraftMods.objects.values_list("aircraft_id", flat=True))
        | set(PlayerAircraftScope.objects.values_list("aircraft_id", flat=True))
    )
    recompute_aircraft_stats(ids, None, rules)
    recompute_matchups(None)
