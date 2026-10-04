"""Ironman streaks (FR-WEB-23): runs of consecutive sorties a pilot survived. Pure functions, no database.

The rule (doc 13, "Streaks"), applied to a player's pilot sorties in chronological order:

- A sortie is **broken** when the pilot died (`is_death`) or was captured (`is_captured`). It ends the current streak
  and is not part of any streak.
- A sortie that never left the ground (`not_taken_off`) and ended without a death or capture is **neutral**: it neither
  extends nor breaks a streak, and is not counted in it (a pilot who spawned and quit at the parking spot did not fly).
- Every other sortie is **survived** and extends the streak. That includes a lost aircraft with a living pilot (bailout,
  ditching), a disconnect without a death and a sortie the server cut off at mission end: the pilot did not die.

A streak's counters sum its survived sorties only (the fatal sortie that ends it, with its kills, is not part of it).
The **best** streak is the longest by sorties; ties go to more air kills, then more flight time, then the earlier one.
A player also has a best streak by air kills (ties: more sorties, then more flight time) and one by flight time (ties:
more sorties, then more air kills); on a full tie the earlier run wins.
The **current** streak is the run after the last broken sortie (zero sorties when the latest flown sortie was fatal).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Self


@dataclass(frozen=True, slots=True)
class StreakSortie:
    """What the streak rule needs to know about one pilot sortie."""

    spawned_at: datetime
    ended_at: datetime
    kills_air: int
    flight_time_s: float
    is_death: bool
    is_captured: bool
    not_taken_off: bool
    ref: int = 0  # the caller's id for the sortie (the database id), handed back as `StreakRun.ended_by_ref`


@dataclass(frozen=True, slots=True)
class Streak:
    """A run of survived sorties. `since` is the first one's spawn, `until` the last one's end (None when empty)."""

    sorties: int = 0
    kills_air: int = 0
    flight_time_s: float = 0.0
    since: datetime | None = None
    until: datetime | None = None

    def extended(self, s: StreakSortie) -> Self:
        return type(self)(
            self.sorties + 1,
            self.kills_air + s.kills_air,
            self.flight_time_s + s.flight_time_s,
            self.since or s.spawned_at,
            s.ended_at,
        )

    def sort_key(self) -> tuple[int, int, float]:
        return (self.sorties, self.kills_air, self.flight_time_s)

    def kills_key(self) -> tuple[int, int, float]:
        return (self.kills_air, self.sorties, self.flight_time_s)

    def time_key(self) -> tuple[float, int, int]:
        return (self.flight_time_s, self.sorties, self.kills_air)


@dataclass(frozen=True, slots=True)
class StreakSummary:
    current: Streak
    best: Streak  # by sorties
    best_air_kills: Streak
    best_flight_time: Streak


def is_broken(s: StreakSortie) -> bool:
    return s.is_death or s.is_captured


def summarize(sorties: Iterable[StreakSortie]) -> StreakSummary:
    """Current and best streak of one player; `sorties` must be in chronological order."""
    current = Streak()
    best = Streak()
    best_kills = Streak()
    best_time = Streak()
    for s in sorties:
        if is_broken(s):
            current = Streak()
        elif s.not_taken_off:
            continue
        else:
            current = current.extended(s)
            if current.sort_key() > best.sort_key():  # strictly: the earlier streak wins a full tie
                best = current
            if current.kills_key() > best_kills.kills_key():
                best_kills = current
            if current.time_key() > best_time.time_key():
                best_time = current
    return StreakSummary(current, best, best_kills, best_time)


MIN_LISTED_RUN = 2
"""The history of runs lists only runs of at least this many survived sorties (OQ-82: a single survived sortie is
every pilot's normal day and would bury the real streaks)."""


class RunEnd(StrEnum):
    DEATH = "death"
    CAPTURED = "captured"
    OPEN = "open"  # no broken sortie after it: the run is still going (or the scope, a tour, ran out)


@dataclass(frozen=True, slots=True)
class StreakRun:
    """One finished or running streak and what ended it (`ended_by_ref`: the broken sortie's `ref`, None when open)."""

    streak: Streak
    end: RunEnd
    ended_by_ref: int | None


def runs(sorties: Iterable[StreakSortie], minimum: int = MIN_LISTED_RUN) -> list[StreakRun]:
    """Every run of at least `minimum` survived sorties, chronological (OQ-82); same rule as `summarize`."""
    found: list[StreakRun] = []
    current = Streak()
    for s in sorties:
        if is_broken(s):
            if current.sorties >= minimum:
                found.append(StreakRun(current, RunEnd.DEATH if s.is_death else RunEnd.CAPTURED, s.ref))
            current = Streak()
        elif not s.not_taken_off:
            current = current.extended(s)
    if current.sorties >= minimum:
        found.append(StreakRun(current, RunEnd.OPEN, None))
    return found
