"""Ram detection: a mid-air collision of two aircraft (OQ-61, `[rules] credit_rams`).

The log has no collision event. A ram shows as two aircraft destroyed in the air at the same moment and place, both by
AID -1 (the environment) with nobody else involved. Rules (design_doc/13_game_rules.md, Rams):

1. both are aircraft (not crew, turrets or ordnance), destroyed while airborne, before the mission end (the despawn at
   AType 7 destroys everything at once, wherever it is);
2. their destruction is within `ram_window_s` of each other and their positions within `ram_distance_m`;
3. neither has an attacker to blame (`credit_kill` finds nobody: no kill line AID, no damage from another party), and
   neither fired a hit on the other.

`detect_rams` finds the signal; `ram_partners` keeps the pairs between enemies, which is what earns a kill.
"""

from dataclasses import dataclass

from il2ks.core.logparse.events import TICKS_PER_SECOND
from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.credit import credit_kill
from il2ks.core.replay.model import MissionFacts, TrackedObject, distance


@dataclass(frozen=True, slots=True)
class Ram:
    first: TrackedObject
    second: TrackedObject
    tick: int  # the earlier of the two destructions
    gap_s: float  # between the two destructions
    distance_m: float

    @property
    def hostile(self) -> bool:
        """Enemies (both coalitions known and different). A collision between friends earns nobody a kill."""
        a, b = self.first.coalition, self.second.coalition
        return a is not None and b is not None and a != 0 and b != 0 and a != b


def _candidate(obj: TrackedObject, mission_end: int | None) -> bool:
    if obj.is_bot or not obj.info.is_air or obj.info.cls in ("ordnance", "gunner", "crew", "equipment"):
        return False
    tick = obj.destroyed_tick
    if tick is None or obj.destroyed_pos is None or not obj.destroyed_airborne:
        return False
    return mission_end is None or tick < mission_end


def _has_attacker(obj: TrackedObject, rules: ReplayRules) -> bool:
    assert obj.destroyed_tick is not None
    return bool(credit_kill(obj, obj.sortie, obj.destroyed_by, obj.destroyed_tick, rules.assist_min_damage))


def _shot_at(victim: TrackedObject, shooter: TrackedObject) -> bool:
    return any(h.attacker is shooter for h in victim.hit_log)


def detect_rams(facts: MissionFacts, rules: ReplayRules) -> list[Ram]:
    """Every pair of aircraft that looks like a mid-air collision, enemies or not, ordered by tick."""
    toggles = rules.toggles
    window = round(toggles.ram_window_s * TICKS_PER_SECOND)
    mission_end = facts.first_mission_end
    air = [o for o in facts.destroyed if _candidate(o, mission_end) and not _has_attacker(o, rules)]
    rams: list[Ram] = []
    for i, a in enumerate(air):
        for b in air[i + 1 :]:
            assert a.destroyed_tick is not None
            assert b.destroyed_tick is not None
            assert a.destroyed_pos is not None
            assert b.destroyed_pos is not None
            if abs(b.destroyed_tick - a.destroyed_tick) > window:
                continue
            apart = distance(a.destroyed_pos, b.destroyed_pos)
            if apart > toggles.ram_distance_m or _shot_at(a, b) or _shot_at(b, a):
                continue
            gap = abs(b.destroyed_tick - a.destroyed_tick) / TICKS_PER_SECOND
            rams.append(Ram(a, b, min(a.destroyed_tick, b.destroyed_tick), gap, apart))
    rams.sort(key=lambda r: r.tick)
    return rams


def ram_partners(facts: MissionFacts, rules: ReplayRules) -> dict[int, TrackedObject]:
    """`id(aircraft)` -> the enemy aircraft it collided with. Empty unless `credit_rams` is on. An aircraft in several
    pairs keeps the nearest in time, then in space."""
    if not rules.toggles.credit_rams:
        return {}
    best: dict[int, tuple[float, float, TrackedObject]] = {}
    for ram in detect_rams(facts, rules):
        if not ram.hostile:
            continue
        for me, other in ((ram.first, ram.second), (ram.second, ram.first)):
            known = best.get(id(me))
            if known is None or (ram.gap_s, ram.distance_m) < known[:2]:
                best[id(me)] = (ram.gap_s, ram.distance_m, other)
    return {key: value[2] for key, value in best.items()}
