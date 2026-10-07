"""Side balance (OQ-134, roadmap 0.2.0): how many pilots were spawned in per side, weighted by time.

A side's strength at a moment is the number of **open pilot sorties** of that side (spawn to the sortie's end; a gunner
flies in a pilot's aircraft and adds nobody). The sides are REDFOR and BLUFOR by country code (`side_of_country`).

- Per mission: the average strength per side over the mission's time (`MissionBalance`).
- Per sortie: the pilot is the **underdog** when, averaged over the sortie's own time (spawn to end), the pilot's side
  had strictly fewer pilots spawned in than the other side (equal is not underdog). A sortie of no time compares the two
  strengths at its spawn tick. A gunner takes the underdog flag of the pilot's sortie.

Everything is integer tick arithmetic (areas under a step function), so two equal averages compare equal exactly.
"""

from bisect import bisect_right
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from itertools import accumulate

from il2ks.core.catalog.loader import Side, side_of_country
from il2ks.core.replay.model import SortieState

_SIDES: tuple[Side, Side] = ("redfor", "blufor")
_DECIMALS = 1


@dataclass(frozen=True, slots=True)
class MissionBalance:
    """The time-weighted average of pilots spawned in per side over the mission, one decimal."""

    redfor_players: float = 0.0
    blufor_players: float = 0.0


@dataclass(frozen=True, slots=True)
class _Interval:
    side: Side
    start: int
    end: int


class _Strength:
    """The number of open intervals of one side as a step function of the tick, with its running area."""

    def __init__(self, intervals: Iterable[_Interval]) -> None:
        steps: dict[int, int] = {}
        for i in intervals:
            if i.end > i.start:
                steps[i.start] = steps.get(i.start, 0) + 1
                steps[i.end] = steps.get(i.end, 0) - 1
        self._ticks = sorted(steps)
        self._counts = list(accumulate(steps[t] for t in self._ticks))
        widths = [b - a for a, b in zip(self._ticks, self._ticks[1:], strict=False)]
        self._area = [0, *accumulate(c * w for c, w in zip(self._counts, widths, strict=False))]

    def area(self, tick: int) -> int:
        """The integral of the strength from the first step up to `tick` (ticks x pilots)."""
        index = bisect_right(self._ticks, tick) - 1
        if index < 0:
            return 0
        return self._area[index] + self._counts[index] * (tick - self._ticks[index])

    def between(self, start: int, end: int) -> int:
        return self.area(end) - self.area(start)


def _intervals(sorties: Iterable[SortieState], end_ticks: Mapping[int, int]) -> dict[int, _Interval]:
    """Sortie index -> interval, for the pilot sorties of a known side."""
    found: dict[int, _Interval] = {}
    for sortie in sorties:
        side = side_of_country(sortie.country)
        if sortie.role == "pilot" and side is not None:
            found[sortie.index] = _Interval(side, sortie.spawn_tick, end_ticks[sortie.index])
    return found


def mission_balance(
    sorties: Iterable[SortieState], end_ticks: Mapping[int, int], mission_end_tick: int
) -> MissionBalance:
    """The average strength per side over `[0, mission_end_tick]`; sorties are cut at the mission end."""
    horizon = max(mission_end_tick, 0)
    if horizon == 0:
        return MissionBalance()
    cut = [
        _Interval(i.side, min(i.start, horizon), min(i.end, horizon)) for i in _intervals(sorties, end_ticks).values()
    ]
    averages = {
        side: round(_Strength(i for i in cut if i.side == side).between(0, horizon) / horizon, _DECIMALS)
        for side in _SIDES
    }
    return MissionBalance(averages["redfor"], averages["blufor"])


def underdog_sorties(sorties: list[SortieState], end_ticks: Mapping[int, int]) -> frozenset[int]:
    """The indices of the sorties flown for the side that had strictly fewer pilots spawned in, averaged over the
    sortie's own time. Pilot sorties are judged by the pilots; a gunner sortie takes its pilot's verdict."""
    intervals = _intervals(sorties, end_ticks)
    strength = {side: _Strength(i for i in intervals.values() if i.side == side) for side in _SIDES}
    under: set[int] = set()
    for index, interval in intervals.items():
        other: Side = "blufor" if interval.side == "redfor" else "redfor"
        if interval.end > interval.start:
            own, rival = (strength[s].between(interval.start, interval.end) for s in (interval.side, other))
        else:
            own, rival = (_count_at(intervals.values(), s, interval.start) for s in (interval.side, other))
        if own < rival:
            under.add(index)
    for sortie in sorties:
        if sortie.role == "gunner" and sortie.parent_sortie is not None and sortie.parent_sortie.index in under:
            under.add(sortie.index)
    return frozenset(under)


def _count_at(intervals: Iterable[_Interval], side: Side, tick: int) -> int:
    return sum(1 for i in intervals if i.side == side and i.start <= tick <= max(i.end, i.start))
