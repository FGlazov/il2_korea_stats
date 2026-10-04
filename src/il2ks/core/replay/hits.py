"""Hits on the sortie timeline: the significant damage a sortie gave and took (FR-WEB-6, doc 13 "Timeline hits").

`ammo.analyse` hands over every damage line (AType 2) that touched a player sortie as a `DamageEvent`, already labelled
with the ammo of the closest hit (the rule behind the ammo breakdown, `ammo._Analysis.closest`). Here the lines of one
burst (same direction, same counterpart, each line within `ReplayRules.hit_burst_gap_s` of the one before, a burst at
most `hit_burst_max_s` long) become one row; rows whose summed damage is under `ReplayRules.hit_min_damage` are dropped;
at most `TIMELINE_MAX_HITS` rows are kept (the heaviest). Each row is a `TimelineEntry` of kind `hit_given`
or `hit_taken` with the damage fraction, the number of lines and the ammo.

The ammo of a row is the one with the most damage among its lines (an earlier line wins a tie), so a row agrees with the
ammo breakdown, which tallies the same labels. A line with no hit in the ammo window has no ammo and does not vote;
a row where no line has one shows no ammo.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from il2ks.core.logparse.events import TICKS_PER_SECOND
from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.result import Counterpart, TimelineEntry

type AmmoKind = Literal["gun", "ordnance", "other"]
type Label = tuple[AmmoKind, str]
"""What a hit or detonation is: a gun ammo name, an ordnance key or another named ammo (flares)."""

HIT_GIVEN = "hit_given"
HIT_TAKEN = "hit_taken"
TIMELINE_MAX_HITS = 150
"""Rows per sortie (the heaviest are kept). Measured on 14 missions (1,182 sorties): the busiest sortie had 77 rows."""


@dataclass(frozen=True, slots=True)
class DamageEvent:
    """One damage line given or taken by a player sortie, with the ammo the closest-hit rule found for it."""

    tick: int
    dealt: bool  # True: this sortie hurt the counterpart; False: the counterpart hurt this sortie
    party: int  # identity of the counterpart (decides which lines form a burst)
    counterpart: Counterpart
    amount: float  # the DMG fraction of the line
    label: Label | None  # None: no hit within the ammo window


@dataclass(slots=True)
class Burst:
    first: DamageEvent
    last_tick: int
    total: float = 0.0
    lines: int = 0
    by_label: dict[Label, float] = field(default_factory=dict[Label, float])

    def add(self, event: DamageEvent) -> None:
        self.last_tick = event.tick
        self.total += event.amount
        self.lines += 1
        if event.label is not None:
            self.by_label[event.label] = self.by_label.get(event.label, 0.0) + event.amount

    def ammo(self) -> Label | None:
        """The label with the most damage; `max` keeps the first of equals, i.e. the one that came first."""
        return max(self.by_label, key=self.by_label.__getitem__) if self.by_label else None

    def entry(self) -> TimelineEntry:
        label = self.ammo()
        return TimelineEntry(
            self.first.tick,
            HIT_GIVEN if self.first.dealt else HIT_TAKEN,
            counterpart=self.first.counterpart,
            damage=self.total,
            lines=self.lines,
            ammo=label[1] if label else "",
            ammo_kind=label[0] if label else "",
        )


def bursts(events: Iterable[DamageEvent], rules: ReplayRules) -> list[Burst]:
    """Group the lines (in tick order) into bursts, in order of their first line."""
    gap = round(rules.hit_burst_gap_s * TICKS_PER_SECOND)
    span = round(rules.hit_burst_max_s * TICKS_PER_SECOND)
    open_: dict[tuple[bool, int], Burst] = {}
    done: list[Burst] = []
    for event in sorted(events, key=lambda e: e.tick):
        key = (event.dealt, event.party)
        burst = open_.get(key)
        if burst is not None and (event.tick - burst.last_tick > gap or event.tick - burst.first.tick > span):
            burst = None
        if burst is None:
            burst = open_[key] = Burst(event, event.tick)
            done.append(burst)
        burst.add(event)
    return done


def hit_entries(events: Iterable[DamageEvent], rules: ReplayRules) -> list[TimelineEntry]:
    """The timeline rows for a sortie's damage lines: significant bursts only, at most `timeline_max_hits`."""
    kept = [b for b in bursts(events, rules) if b.total >= rules.hit_min_damage]
    if len(kept) > TIMELINE_MAX_HITS:
        heaviest = sorted(range(len(kept)), key=lambda i: (-kept[i].total, i))[:TIMELINE_MAX_HITS]
        kept = [kept[i] for i in sorted(heaviest)]
    return [b.entry() for b in kept]
