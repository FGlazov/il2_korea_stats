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

Two tracks (maintainer, 2026-10-05): a pilot has an **air** ironman run and a **ground** one. A sortie belongs to the
track of its combat role: an attack sortie to the ground track, every other one (air superiority, or no combat role
known) to the air track. The rule above runs on each track's sorties alone: a death in an attack sortie ends the ground
run only, a death in an air sortie the air run only, and a sortie of the other track neither extends nor breaks a run.
The air track's "kills" are air kills, the ground track's ground kills (both are stored in every streak).
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Self


class Track(StrEnum):
    """The two ironman tracks of a pilot (maintainer, 2026-10-05)."""

    AIR = "air"
    GROUND = "ground"


def track_of(combat_role: str | None) -> Track:
    """The track a sortie of this combat role (`CombatRole` value, None = none known) counts for: attack sorties are the
    ground track's, everything else (air superiority, unknown role) the air track's."""
    return Track.GROUND if combat_role == "attack" else Track.AIR


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
    kills_ground: int = 0
    track: Track = Track.AIR


@dataclass(frozen=True, slots=True)
class Streak:
    """A run of survived sorties. `since` is the first one's spawn, `until` the last one's end (None when empty)."""

    sorties: int = 0
    kills_air: int = 0
    flight_time_s: float = 0.0
    since: datetime | None = None
    until: datetime | None = None
    kills_ground: int = 0

    def extended(self, s: StreakSortie) -> Self:
        return type(self)(
            self.sorties + 1,
            self.kills_air + s.kills_air,
            self.flight_time_s + s.flight_time_s,
            self.since or s.spawned_at,
            s.ended_at,
            self.kills_ground + s.kills_ground,
        )

    def sort_key(self, track: Track = Track.AIR) -> tuple[int, int, float]:
        return (self.sorties, self.kills_of(track), self.flight_time_s)

    def kills_of(self, track: Track) -> int:
        """The kills the track counts: air kills on the air track, ground kills on the ground track."""
        return self.kills_ground if track is Track.GROUND else self.kills_air

    def kills_key(self, track: Track = Track.AIR) -> tuple[int, int, float]:
        return (self.kills_of(track), self.sorties, self.flight_time_s)

    def time_key(self, track: Track = Track.AIR) -> tuple[float, int, int]:
        return (self.flight_time_s, self.sorties, self.kills_of(track))


@dataclass(frozen=True, slots=True)
class StreakSummary:
    current: Streak
    best: Streak  # by sorties
    best_kills: Streak  # by the track's kills (air kills on the air track, ground kills on the ground track)
    best_flight_time: Streak


def is_broken(s: StreakSortie) -> bool:
    return s.is_death or s.is_captured


def on_track(sorties: Iterable[StreakSortie], track: Track) -> Iterator[StreakSortie]:
    """The sorties of one track, in the order given."""
    return (s for s in sorties if s.track is track)


def summarize(sorties: Iterable[StreakSortie], track: Track = Track.AIR) -> StreakSummary:
    """Current and best streak of one player on one track; `sorties` (all tracks) must be in chronological order."""
    current = Streak()
    best = Streak()
    best_kills = Streak()
    best_time = Streak()
    for s in on_track(sorties, track):
        if is_broken(s):
            current = Streak()
        elif s.not_taken_off:
            continue
        else:
            current = current.extended(s)
            if current.sort_key(track) > best.sort_key(track):  # strictly: the earlier streak wins a full tie
                best = current
            if current.kills_key(track) > best_kills.kills_key(track):
                best_kills = current
            if current.time_key(track) > best_time.time_key(track):
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


def runs(sorties: Iterable[StreakSortie], track: Track = Track.AIR, minimum: int = MIN_LISTED_RUN) -> list[StreakRun]:
    """Every run of one track of at least `minimum` survived sorties, chronological (OQ-82); the rule of `summarize`."""
    found: list[StreakRun] = []
    current = Streak()
    for s in on_track(sorties, track):
        if is_broken(s):
            if current.sorties >= minimum:
                found.append(StreakRun(current, RunEnd.DEATH if s.is_death else RunEnd.CAPTURED, s.ref))
            current = Streak()
        elif not s.not_taken_off:
            current = current.extended(s)
    if current.sorties >= minimum:
        found.append(StreakRun(current, RunEnd.OPEN, None))
    return found
