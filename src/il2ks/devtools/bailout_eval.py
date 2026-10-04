"""Compare bailout detection methods on real missions: `il2ks dev bailout-eval <log directory>` (FR-ING-14, OQ-39).

Methods (design_doc/13_game_rules.md, "Bailout rule v3"):
- v2: AType 4 `PLID:0`, airborne gate (the live wheels flag), 100 m distance, pilot didn't die with the aircraft.
- ejection: Rufus's ejection spawn: an AType 12 of the pilot bot with `PID:-1` while the aircraft is airborne
  (AType 5/6 state), and the pilot not killed within 0.5 s of it.
- geometry: Rufus's AType 16 geometry rule, 200 m arm only (we have no heightmaps, OQ-39). `geometry-strict` is his
  "never landed" test as worded (no Landing at or before the kill, ever); `geometry` asks for "no Landing since the last
  takeoff" (the aircraft is airborne at the kill by AType 5/6), which is what the rule is for.
- geometry+proxy: `geometry` with a terrain-free PROXY for the height arm (`GroundSamples`).
- v3: the rule `fate.bailout_v3` implements (what the pipeline uses).

It prints a confusion table per method pair, and with `--list` the mission file and tick of every disagreement
(never player names). Features are computed once per sortie, so the methods are cheap to recombine.
"""

import itertools
import math
import pickle
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from il2ks.core.catalog.loader import load_default_catalog
from il2ks.core.logparse.events import Pos
from il2ks.core.logparse.files import group_mission_files, parse_mission
from il2ks.core.logparse.parser import ParseStats
from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.fate import bailout_v2, ejection_spawn_tick, pilot_final_pos
from il2ks.core.replay.judge import judge
from il2ks.core.replay.model import MissionFacts, distance
from il2ks.core.replay.state import Replay

GEOMETRY_MIN_DISTANCE_M = 200.0
PROXY_HEIGHT_M = 30.0
PROXY_RADII_M = (500.0, 2000.0)


@dataclass(frozen=True, slots=True)
class Record:
    """One pilot sortie that took off, with the facts every method needs."""

    mission: str
    sortie_index: int
    end_tick: int
    fate: str
    v2: bool
    v3: bool
    ejection: bool  # an ejection spawn while airborne (AType 5/6 state), pilot not killed within 0.5 s of it
    has_loss: bool
    loss_flight_airborne: bool
    landed_before_kill: bool  # any Landing at or before the kill tick, however long ago
    disconnected: bool
    died: bool
    dist_loss_m: float | None  # teardown position to the aircraft's last logged position
    pilot_y: float | None
    ground_y: dict[float, float | None]  # lowest ground sample within the radius, excluding this sortie's own


class GroundSamples:
    """Terrain-free PROXY for the missing heightmap: positions of things that sit on the ground in this mission (ground
    objects, takeoff and landing points, parking spawns, airfields). Not terrain: a sparse set of known points."""

    def __init__(self, facts: MissionFacts, cell_m: float = 500.0) -> None:
        self._cell = cell_m
        self._grid: dict[tuple[int, int], list[tuple[Pos, int]]] = defaultdict(list)
        for obj in facts.objects:
            for _, pos in obj.track:
                self._add(pos, obj.object_id)
            for _, pos in obj.landings:
                self._add(pos, obj.object_id)
            for _, pos in obj.takeoffs:
                self._add(pos, obj.object_id)
        for sortie in facts.sorties:
            if sortie.spawn_type != "air":
                self._add(sortie.spawn_pos, sortie.aircraft_id)
        for airfield in facts.airfields.values():
            self._add(airfield.pos, -1)

    def _add(self, pos: Pos, owner: int) -> None:
        if pos.y != 0.0 or pos.x != 0.0:
            self._grid[(int(pos.x // self._cell), int(pos.z // self._cell))].append((pos, owner))

    def lowest_within(self, pos: Pos, radius_m: float, exclude: int) -> float | None:
        reach = int(radius_m // self._cell) + 1
        cx, cz = int(pos.x // self._cell), int(pos.z // self._cell)
        found: list[float] = []
        for dx, dz in itertools.product(range(-reach, reach + 1), repeat=2):
            for sample, owner in self._grid.get((cx + dx, cz + dz), ()):
                if owner != exclude and math.hypot(sample.x - pos.x, sample.z - pos.z) <= radius_m:
                    found.append(sample.y)
        return min(found) if found else None


def evaluate(directory: Path, *, stride: int = 1) -> tuple[list[Record], int]:
    logs = group_mission_files(directory.iterdir(), txt_as="archive")[::stride]
    catalog = load_default_catalog()
    rules = ReplayRules()
    records: list[Record] = []
    for log in logs:
        replay = Replay(catalog, rules)
        for event in parse_mission(log, ParseStats()):
            replay.feed(event)
        facts = replay.facts
        ground = GroundSamples(facts)
        for sortie in facts.sorties:
            if sortie.role != "pilot":
                continue
            verdict = judge(sortie, facts, rules, final=True)
            if not verdict.took_off:
                continue
            airframe = sortie.airframe
            loss = verdict.loss
            pilot_pos = pilot_final_pos(sortie, rules)
            records.append(
                Record(
                    mission=log.mission_uid,
                    sortie_index=sortie.index,
                    end_tick=verdict.end_tick,
                    fate=verdict.fate,
                    v2=bailout_v2(sortie, loss, verdict.died_tick, rules)[0],
                    v3=verdict.bailout,
                    ejection=ejection_spawn_tick(sortie, verdict.end_tick, rules) is not None,
                    has_loss=loss is not None,
                    loss_flight_airborne=loss is not None and airframe.airborne_at(loss.tick),
                    landed_before_kill=loss is not None
                    and any(t <= loss.tick for t, _ in airframe.landings if t >= sortie.spawn_tick),
                    disconnected=verdict.disconnected,
                    died=verdict.died_tick is not None,
                    dist_loss_m=(
                        distance(pilot_pos, loss.pos)
                        if pilot_pos is not None and loss is not None and loss.pos is not None
                        else None
                    ),
                    pilot_y=pilot_pos.y if pilot_pos is not None else None,
                    ground_y={
                        r: ground.lowest_within(pilot_pos, r, airframe.object_id) if pilot_pos is not None else None
                        for r in PROXY_RADII_M
                    },
                )
            )
    return records, len(logs)


def save(records: list[Record], path: Path) -> None:
    path.write_bytes(pickle.dumps(records))


def load(path: Path) -> list[Record]:
    return pickle.loads(path.read_bytes())


type Method = Callable[[Record], bool]


def geometry_strict(r: Record) -> bool:
    return (
        r.has_loss
        and not r.landed_before_kill
        and not r.disconnected
        and not r.died
        and (r.dist_loss_m or 0.0) > GEOMETRY_MIN_DISTANCE_M
    )


def geometry(r: Record) -> bool:
    return (
        r.has_loss
        and r.loss_flight_airborne
        and not r.disconnected
        and not r.died
        and (r.dist_loss_m or 0.0) > GEOMETRY_MIN_DISTANCE_M
    )


def geometry_proxy(r: Record, radius: float = 2000.0) -> bool:
    """`geometry`, with the height arm replaced by the proxy (pilot > 30 m above the lowest known ground nearby)."""
    gy = r.ground_y.get(radius)
    high = r.pilot_y is not None and gy is not None and r.pilot_y - gy > PROXY_HEIGHT_M
    return (
        r.has_loss
        and r.loss_flight_airborne
        and not r.disconnected
        and not r.died
        and ((r.dist_loss_m or 0.0) > GEOMETRY_MIN_DISTANCE_M or high)
    )


METHODS: dict[str, Method] = {
    "v2": lambda r: r.v2,
    "ejection": lambda r: r.ejection,
    "geometry-strict": geometry_strict,
    "geometry": geometry,
    "geometry+proxy": geometry_proxy,
    "v3": lambda r: r.v3,
}


def report(records: list[Record], missions: int, *, list_disagreements: bool) -> None:
    print(f"{missions} missions, {len(records)} pilot sorties that took off")
    for name, method in METHODS.items():
        print(f"  {name}: {sum(method(r) for r in records)} bailouts")
    for a, b in itertools.combinations(METHODS, 2):
        table = Counter((METHODS[a](r), METHODS[b](r)) for r in records)
        print(f"{a} vs {b}: both {table[True, True]}, only {a} {table[True, False]}, only {b} {table[False, True]}")
        if list_disagreements:
            for r in records:
                if METHODS[a](r) != METHODS[b](r):
                    who = a if METHODS[a](r) else b
                    print(f"    {r.mission} sortie {r.sortie_index} end tick {r.end_tick} only {who}")


def run(directory: Path, *, list_disagreements: bool, stride: int = 1) -> int:
    records, missions = evaluate(directory, stride=stride)
    report(records, missions, list_disagreements=list_disagreements)
    return 0
