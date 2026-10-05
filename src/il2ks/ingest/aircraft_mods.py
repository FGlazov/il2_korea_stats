"""Weapon-modification scopes of the aircraft-type stats (FR-WEB-8, doc 12): the significant mods and the level-2 rows
per filter pattern.

A type's *significant* modifications are the ones in `weapon_mods.csv` with `significant = true` (a change applies with
`il2ks rebuild-aggregates`). For such a type the aircraft page can filter every table by "with / without / any" of each
of them, so the stored rows exist per **filter pattern**: one character per significant mod in ascending id order, `*`
any, `+` with it, `-` without it (`core.catalog.loader.mod_filter_patterns`; '' = unfiltered, the rows every type
has). 3**n - 1 patterns beyond the unfiltered one: 26 for a type with three significant mods, 2 for one. The page reads
exactly one row of the scope it shows; nothing is summed at read time (TD-22).

Everything here is recomputed from the counted sorties, never adjusted by deltas (incremental == rebuild): one grouped
query per type chunk over (type, tour, role, WM, player, country), folded in Python into per (type, tour, role, WM)
*cells*, and each cell is added to every pattern its WM belongs to. The pilot count of a pattern is the size of the
union of the cells' pilot sets (a pilot who flew with and without a mod counts in both patterns, once in each).
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import cache

from il2ks.core.catalog.loader import Catalog, load_default_catalog, mod_filter_patterns, side_of_country
from il2ks.db.models import AircraftRole, GameObject
from il2ks.ingest.counters import COUNTER_FIELDS, FLOAT_COUNTERS, SORTIE_COUNTERS, counted_sorties

ALL = AircraftRole.ALL.value

type CellKey = tuple[int, int, str, int]  # aircraft, tour, role (`AircraftRole`), WM
type PatternKey = tuple[int, int, str, str]  # aircraft, tour, role, pattern
type Totals = dict[str, int | float]


@cache
def _catalog() -> Catalog:
    return load_default_catalog()


def significant_mods(aircraft_ids: Iterable[int]) -> dict[int, tuple[int, ...]]:
    """The significant mod ids (ascending) of each of these types that has any; types without are absent."""
    catalog = _catalog()
    found: dict[int, tuple[int, ...]] = {}
    for pk, log_name in GameObject.objects.filter(pk__in=list(aircraft_ids)).values_list("pk", "log_name"):
        ids = tuple(mod.mod_id for mod in catalog.significant_mods(log_name))
        if ids:
            found[pk] = ids
    return found


@dataclass(slots=True)
class _Cell:
    totals: Totals = field(
        default_factory=lambda: {name: 0.0 if name in FLOAT_COUNTERS else 0 for name in COUNTER_FIELDS}
    )
    players: set[int] = field(default_factory=set[int])
    sides: dict[str, int] = field(default_factory=dict[str, int])  # side -> sorties


def _fold(sums: Totals, row: dict[str, object]) -> None:
    """Add the counters of an aggregate row to `sums` (a NULL `Sum` adds nothing). Runs tens of thousands of times in a
    refresh, once per (row, scope): a plain `None` test, since a counter is a number or NULL."""
    for name in COUNTER_FIELDS:
        value = row[name]
        if value is not None:
            sums[name] += value  # pyright: ignore[reportOperatorIssue]


def pattern_tour_stats(
    significant: dict[int, tuple[int, ...]], tour_ids: list[int] | None
) -> dict[PatternKey, tuple[Totals, int, int, int]]:
    """(counters, pilots, REDFOR sorties, BLUFOR sorties) of every filter pattern scope of the types in `significant`,
    per tour (those in `tour_ids`, None = every tour) and per role (`all` and each combat role that has sorties). Reads
    only those tours' sorties; the all-time pattern rows are the sum of these (`rollup_aircraft_stats`)."""
    if not significant:
        return {}
    rows = counted_sorties().filter(aircraft_id__in=sorted(significant), tour__isnull=False)
    if tour_ids is not None:
        rows = rows.filter(tour_id__in=tour_ids)
    rows = (
        rows.values("aircraft_id", "tour_id", "combat_role", "weapon_mods", "player_id", "country")
        .annotate(**SORTIE_COUNTERS)
        .order_by("aircraft_id", "tour_id", "combat_role", "weapon_mods", "player_id", "country")
    )
    cells: dict[CellKey, _Cell] = {}
    for row in rows:
        aircraft, tour, mods = row["aircraft_id"], row["tour_id"], row["weapon_mods"]
        side = side_of_country(row["country"])
        roles = (ALL,) if row["combat_role"] is None else (ALL, row["combat_role"])
        for role in roles:
            cell = cells.setdefault((aircraft, tour, role, mods), _Cell())
            _fold(cell.totals, row)
            cell.players.add(row["player_id"])
            if side is not None:
                cell.sides[side] = cell.sides.get(side, 0) + row["sorties"]
    return _fold_patterns(cells, significant)


def _fold_patterns(
    cells: dict[CellKey, _Cell], significant: dict[int, tuple[int, ...]]
) -> dict[PatternKey, tuple[Totals, int, int, int]]:
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
        key: (cell.totals, len(cell.players), cell.sides.get("redfor", 0), cell.sides.get("blufor", 0))
        for key, cell in merged.items()
    }


def _cell_order(item: tuple[CellKey, _Cell]) -> tuple[int, int, str, int]:
    """A fixed order to add the cells in: a rebuild sums floats like an incremental run."""
    return item[0]


def scopes_of(
    tour: int | None, combat_role: str, weapon_mods: int, significant: tuple[int, ...]
) -> list[tuple[int | None, str, str]]:
    """Every (tour, role, mod pattern) scope one sortie (or one destroyed aircraft) belongs to, the stored scopes of the
    aircraft page: all time and its tour, every role (`all`) and its own combat role ('' = none recorded: `all` only),
    unfiltered ('') and each filter pattern of its weapon mods (`significant`: the type's significant mod ids, empty
    for most; a negative `weapon_mods` = not a player sortie: unfiltered only)."""
    tours: tuple[int | None, ...] = (None,) if tour is None else (None, tour)
    roles = (ALL, combat_role) if combat_role else (ALL,)
    patterns = ("", *mod_filter_patterns(weapon_mods, significant)) if significant and weapon_mods >= 0 else ("",)
    return [(t, r, p) for t in tours for r in roles for p in patterns]


type PlayerScopeKey = tuple[int, int, int, str, str]  # aircraft, player, tour, role, pattern


def player_scope_stats(
    aircraft_ids: Iterable[int], significant: dict[int, tuple[int, ...]], tour_ids: list[int] | None
) -> dict[PlayerScopeKey, Totals]:
    """The counters of every player in each of these types per tour scope (`scopes_of`): the tours in `tour_ids`
    (None = every tour), every role and each combat role, unfiltered and each mod pattern. Reads only those tours'
    sorties; the all-time rows are the sum of these (`aircraft_stats._rollup_player_scopes`). One grouped query, in a
    fixed order so a
    rebuild sums the floats like an incremental run."""
    rows = counted_sorties().filter(aircraft_id__in=sorted(set(aircraft_ids)), tour__isnull=False)
    if tour_ids is not None:
        rows = rows.filter(tour_id__in=tour_ids)
    rows = (
        rows.values("aircraft_id", "player_id", "tour_id", "combat_role", "weapon_mods")
        .annotate(**SORTIE_COUNTERS)
        .order_by("aircraft_id", "player_id", "tour_id", "combat_role", "weapon_mods")
    )
    found: dict[PlayerScopeKey, Totals] = {}
    for row in rows:
        aircraft, player = row["aircraft_id"], row["player_id"]
        for tour, role, pattern in scopes_of(
            row["tour_id"], row["combat_role"] or "", row["weapon_mods"], significant.get(aircraft, ())
        ):
            if tour is None:
                continue
            _fold(
                found.setdefault(
                    (aircraft, player, tour, role, pattern),
                    {name: 0.0 if name in FLOAT_COUNTERS else 0 for name in COUNTER_FIELDS},
                ),
                row,
            )
    return found
