"""Combat role and time on target of a pilot sortie (FR-WEB-19, FR-WEB-20, doc 13; OQ-27, OQ-29).

Both read recorded facts only (`feed()` keeps the loadout, the releases and the ground objects' positions).

**Role** comes from what the aircraft spawned with (AType 10): bombs (napalm tanks count as bombs) or rockets make an
`attack` sortie, guns only (drop tanks have no ammo count) an `air_superiority` one. The aircraft class `attacker` is
always `attack`. The AType 10 ammo counts are used rather than the `PAYLOAD` name because they exist for every spawn,
also for a payload the CSV lacks, and they agree with the payload names (the CSV has two shifted F-51D rows).

**Time on target** counts the time around releases (AType 25 stores, AType 26 rocket salvos) that were made near an
enemy ground object: horizontally within `tot_target_radius_m` (altitude ignored), the object existing and not yet
destroyed at the release. Releases further than `tot_pass_gap_s` apart are separate attacks. An attack runs from
`tot_lead_in_s` before its first release (not before the takeoff that started the leg, nor into the previous attack) to
`tot_trail_s` after its last release (maintainer, 2026-10-05; not past the end of the flight, that is the sortie end,
the aircraft's loss or the landing, nor into the next attack's run-in).
A release made far from any target (a jettison when intercepted) counts for nothing.
"""

import math
from bisect import bisect_right
from collections import defaultdict

from il2ks.core.logparse.events import TICKS_PER_SECOND, Pos
from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.model import GROUND_CLASSES, MissionFacts, SortieState, TrackedObject
from il2ks.core.replay.result import CombatRole

type Cell = tuple[int, int, int]
"""Coalition, x cell, z cell."""


def combat_role(sortie: SortieState) -> CombatRole | None:
    """None for a gunner. Bombs, napalm or rockets in the AType 10 ammo make a pilot sortie `attack`."""
    if sortie.role != "pilot":
        return None
    if sortie.vehicle.info.cls == "attacker":
        return "attack"
    loaded = sortie.ammo_loaded
    return "attack" if loaded.bombs > 0 or loaded.rockets > 0 else "air_superiority"


INTERCEPT_CLASSES: frozenset[str] = frozenset({"bomber", "attacker", "transport"})


def is_interception_victim(victim_class: str | None, victim_role: str | None) -> bool:
    """Is an air victim a bomber, an attacker or a transport (doc 13, interception; transports added 2026-10-04, OQ-102:
    paratrooper and supply planes are important targets)? AI aircraft count by their catalog class, a
    player's sortie also by its combat role (a fighter with bombs or rockets is attacking)."""
    return victim_class in INTERCEPT_CLASSES or victim_role == "attack"


class GroundTargets:
    """Ground objects by coalition and grid cell (cell size = the target radius), built on first use.

    Missions hold about 10,000 objects, mostly statics, so a release is checked against the nine cells around it only.
    An object sits in every cell it ever stood in (vehicles move); the exact check uses its position at the release."""

    def __init__(self, facts: MissionFacts, cell_m: float) -> None:
        self._facts = facts
        self._cell = cell_m
        self._cells: dict[Cell, list[TrackedObject]] | None = None
        self._coalitions: set[int] = set()

    def _build(self) -> dict[Cell, list[TrackedObject]]:
        cells: defaultdict[Cell, list[TrackedObject]] = defaultdict(list)
        for obj in self._facts.objects:
            if obj.info.cls not in GROUND_CLASSES or not obj.track or not obj.coalition:
                continue
            self._coalitions.add(obj.coalition)
            seen: set[tuple[int, int]] = set()
            for _, pos in obj.track:
                xz = self._xz(pos)
                if xz not in seen:
                    seen.add(xz)
                    cells[(obj.coalition, *xz)].append(obj)
        return cells

    def _xz(self, pos: Pos) -> tuple[int, int]:
        return math.floor(pos.x / self._cell), math.floor(pos.z / self._cell)

    def enemy_within(self, pos: Pos, tick: int, coalition: int, radius_m: float) -> bool:
        """Is an enemy (other, non-zero coalition) ground object within `radius_m` of `pos` (x and z only) at `tick`?

        Its position is the last one logged at or before `tick`. An object not yet logged, or destroyed at or before
        `tick`, doesn't count."""
        if self._cells is None:
            self._cells = self._build()
        cx, cz = self._xz(pos)
        for other in self._coalitions:
            if other == coalition:
                continue
            checked: set[int] = set()
            for dx in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for obj in self._cells.get((other, cx + dx, cz + dz), ()):
                        if id(obj) in checked:
                            continue
                        checked.add(id(obj))
                        if _near(obj, pos, tick, radius_m):
                            return True
        return False


def _near(obj: TrackedObject, pos: Pos, tick: int, radius_m: float) -> bool:
    if obj.destroyed_tick is not None and obj.destroyed_tick <= tick:
        return False
    i = bisect_right(obj.track, tick, key=lambda entry: entry[0])
    if i == 0:
        return False  # not in the mission yet
    there = obj.track[i - 1][1]
    return math.hypot(there.x - pos.x, there.z - pos.z) <= radius_m


def _leg_start(sortie: SortieState, tick: int) -> int:
    """The takeoff (or air spawn) of the flight a release at `tick` belongs to."""
    start = sortie.spawn_tick
    for change_tick, airborne in sortie.vehicle.flight_changes:
        if change_tick > tick:
            break
        if airborne:
            start = change_tick
    return start


def _leg_end(sortie: SortieState, tick: int, active_end_tick: int) -> int:
    """The landing that ends the flight a release at `tick` belongs to, else the sortie end or the aircraft's loss."""
    for change_tick, airborne in sortie.vehicle.flight_changes:
        if change_tick > tick and not airborne:
            return min(change_tick, active_end_tick)
    return active_end_tick


def time_on_target_s(sortie: SortieState, targets: GroundTargets, rules: ReplayRules, *, active_end_tick: int) -> float:
    """Seconds spent attacking targets, for an `attack` sortie (0.0 without a release near an enemy ground object).

    Only releases up to `active_end_tick` (the sortie end, or the aircraft's loss when that came first) count."""
    radius = rules.tot_target_radius_m
    hits = [
        t
        for t, pos in sortie.vehicle.releases
        if sortie.spawn_tick <= t <= active_end_tick and targets.enemy_within(pos, t, sortie.coalition, radius)
    ]
    if not hits:
        return 0.0
    gap = round(rules.tot_pass_gap_s * TICKS_PER_SECOND)
    lead = round(rules.tot_lead_in_s * TICKS_PER_SECOND)
    passes: list[list[int]] = [[hits[0]]]
    for t in hits[1:]:
        if t - passes[-1][-1] > gap:
            passes.append([t])
        else:
            passes[-1].append(t)
    trail = round(rules.tot_trail_s * TICKS_PER_SECOND)
    starts: list[int] = []
    previous_last = 0
    for group in passes:
        starts.append(max(group[0] - lead, _leg_start(sortie, group[0]), previous_last))
        previous_last = group[-1]
    total = 0
    for i, group in enumerate(passes):
        last = group[-1]
        end = min(last + trail, _leg_end(sortie, last, active_end_tick))
        if i + 1 < len(passes):
            end = min(end, starts[i + 1])  # the next run-in takes over: no double counting
        total += max(end, last) - starts[i]
    return total / TICKS_PER_SECOND
