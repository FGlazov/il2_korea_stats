"""Aircraft damage per flight leg (maintainer 2026-10-05, doc 13 "Damage per flight leg").

An aircraft's damage is a fraction of its health. It accumulates up to 100% and, with `replay.resupply_allowed`, resets
to 0 at a landing that is followed by another takeoff in the same sortie: no log event says the aircraft was repaired,
so a landing is assumed to repair it (the same assumption as the ammo resupply). The damage lines of one leg are
counted up to 100% in the order they came, whoever fired them (environment included), so the part of a line that would
pass 100% and every later line count nothing. Damage to a pilot, gunner or crew bot is not aircraft damage and is never
counted here.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.model import DamageRecord, SortieState, TrackedObject

FULL_HEALTH = 1.0


@dataclass(frozen=True, slots=True)
class CountedDamage:
    """One damage line with the part of it that counts (the leg still had that much health left)."""

    record: DamageRecord
    amount: float


def repair_ticks(
    landings: Iterable[tuple[int, object]],
    takeoffs: Iterable[tuple[int, object]],
    *,
    spawn_tick: int,
    until_tick: int,
    rules: ReplayRules,
) -> tuple[int, ...]:
    """Landings (AType 6) of the sortie that are followed by a later takeoff (AType 5): the assumed repairs. Empty when
    `rules.resupply_allowed` is off. Only events inside the sortie's active window count, like the resupply rule's."""
    if not rules.resupply_allowed:
        return ()
    lands = sorted(t for t, _ in landings if spawn_tick <= t <= until_tick)
    ups = [t for t, _ in takeoffs if spawn_tick <= t <= until_tick]
    return tuple(t for t in lands if any(up > t for up in ups))


def sortie_repairs(sortie: SortieState, until_tick: int, rules: ReplayRules) -> tuple[int, ...]:
    airframe = sortie.airframe
    return repair_ticks(
        airframe.landings, airframe.takeoffs, spawn_tick=sortie.spawn_tick, until_tick=until_tick, rules=rules
    )


def counted_damage(
    obj: TrackedObject, upto_tick: int | None = None, repairs: tuple[int, ...] = ()
) -> list[CountedDamage]:
    """The damage lines of one object (up to `upto_tick`) with what counts of each. The leg changes after every repair
    tick (a line on the landing tick itself still belongs to the leg that ended there)."""
    out: list[CountedDamage] = []
    used = 0.0
    leg = 0
    for record in obj.damage_log:
        if upto_tick is not None and record.tick > upto_tick:
            continue
        now = sum(1 for tick in repairs if tick < record.tick)
        if now != leg:
            leg, used = now, 0.0
        counted = min(record.amount, max(0.0, FULL_HEALTH - used))
        used += counted
        out.append(CountedDamage(record, counted))
    return out


def damage_at_end(counted: Iterable[CountedDamage], repairs: tuple[int, ...]) -> float:
    """The damage the aircraft carries at the end: what the last leg counted."""
    last = len(repairs)
    return min(
        FULL_HEALTH, sum(c.amount for c in counted if sum(1 for tick in repairs if tick < c.record.tick) == last)
    )


def damage_at_repairs(counted: Iterable[CountedDamage], repairs: tuple[int, ...]) -> float:
    """The most damage the aircraft carried into one of the repairing landings (0 when it had no repair): each leg that
    ends at a repair tick, its counted damage up to that tick. Maintainer 2026-10-05: the damaged-landing medal counts
    these landings although the repair leaves the last leg unharmed."""
    lines = list(counted)
    worst = 0.0
    for leg, tick in enumerate(repairs):
        carried = sum(
            c.amount for c in lines if c.record.tick <= tick and sum(1 for r in repairs if r < c.record.tick) == leg
        )
        worst = max(worst, min(FULL_HEALTH, carried))
    return worst


def damaged_at(counted: Iterable[CountedDamage], tick: int, repairs: tuple[int, ...]) -> bool:
    """True when the leg that ends at the repair `tick` counted any damage by then."""
    leg = sum(1 for r in repairs if r < tick)
    return any(
        c.amount > 0 and c.record.tick <= tick and sum(1 for r in repairs if r < c.record.tick) == leg for c in counted
    )
