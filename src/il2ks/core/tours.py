"""Tour periods (TD-26, FR-WEB-10): which period contains an instant. Pure functions, no database.

A tour is a half-open period `[started_at, ended_at)`. The periods of the two automatic modes are a function of the
mission's start time alone:

- `monthly`: calendar months in the tour timezone. Title "October 2026".
- `days:<N>`: consecutive blocks of N days, block 0 starting at local midnight of `TourRules.start` (a date in the
  tour timezone). Blocks before the start date are numbered backwards (0, -1, ...). Title "Tour 7" (block + 1).
- `manual`: no function. The admin starts tours (FR-ADM-8); `ingest.tours` picks the stored tour by start time.

All boundaries are local midnight in `TourRules.timezone_name`, converted to UTC, so a mission at 00:30 local on the 1st
belongs to the new month even though it is still the 31st in UTC. Viewer-local display never changes membership.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

type TourMode = Literal["monthly", "days", "manual"]

MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


@dataclass(frozen=True, slots=True)
class TourRules:
    """The `[tours]` config section after parsing. `timezone_name` is already resolved (default: the server's)."""

    mode: TourMode = "monthly"
    days: int = 0  # days mode only: the length of a tour
    start: date | None = None  # days mode only: first day of tour 1, in the tour timezone
    timezone_name: str = "UTC"
    # The admin's "start a new tour when a mission is won by one side" switch (`SiteSettings.tour_on_win_applied`, not a
    # config key): on top of the mode, every decisive mission ends its tour (`win_cuts`).
    on_win: bool = False

    @property
    def label(self) -> str:
        """The setting as written in the config and stored as `Tour.mode`: "monthly", "days:14", "manual"."""
        return f"days:{self.days}" if self.mode == "days" else self.mode


DEFAULT_TOUR_RULES = TourRules()


@dataclass(frozen=True, slots=True)
class Period:
    started_at: datetime  # aware, UTC, inclusive
    ended_at: datetime  # aware, UTC, exclusive
    title: str


def parse_mode(text: str) -> tuple[TourMode, int]:
    """`"monthly"` -> ("monthly", 0), `"days:14"` -> ("days", 14), `"manual"` -> ("manual", 0). Raises ValueError."""
    value = text.strip().lower()
    if value in ("monthly", "manual"):
        return ("monthly" if value == "monthly" else "manual"), 0
    name, _, number = value.partition(":")
    if name == "days":
        try:
            days = int(number)
        except ValueError:
            days = 0
        if days >= 1:
            return "days", days
    raise ValueError(f"must be monthly, manual or days:<N> with a whole number N of at least 1, got {text!r}")


def _local_midnight_utc(day: date, tz: ZoneInfo) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(UTC)


def period_for(rules: TourRules, instant: datetime) -> Period:
    """The automatic-mode period containing `instant` (aware). Raises ValueError in manual mode (it has no function)."""
    tz = ZoneInfo(rules.timezone_name)
    local_day = instant.astimezone(tz).date()
    if rules.mode == "monthly":
        first = local_day.replace(day=1)
        following = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
        return Period(
            _local_midnight_utc(first, tz),
            _local_midnight_utc(following, tz),
            f"{MONTH_NAMES[first.month - 1]} {first.year}",
        )
    if rules.mode == "days":
        if rules.start is None or rules.days < 1:
            raise ValueError("days mode needs a start date and a length of at least 1 day")
        index = (local_day - rules.start).days // rules.days  # floor: blocks before `start` count backwards
        first = rules.start + timedelta(days=index * rules.days)
        following = first + timedelta(days=rules.days)
        return Period(_local_midnight_utc(first, tz), _local_midnight_utc(following, tz), f"Tour {index + 1}")
    raise ValueError("manual mode has no calendar periods: tours are started by the admin")


@dataclass(frozen=True, slots=True)
class MissionSpan:
    """What the decisive-mission rule needs of a mission: when it ran, and whether one side won it."""

    started_at: datetime
    ended_at: datetime
    decisive: bool


def win_cuts(start: datetime | None, end: datetime | None, missions: Iterable[MissionSpan]) -> tuple[datetime, ...]:
    """Where decisive missions cut the period `[start, end)` (None = unbounded) into tours: the end of every decisive
    mission that lies strictly inside it, ascending, each instant once. The next mission starts the next tour.

    A function of the missions alone (each mission's own end and result), so it gives the same answer whether the
    missions were ingested in order, late, or all at once (`ingest.tours`: incremental == rebuild)."""
    cuts = {
        m.ended_at
        for m in missions
        if m.decisive
        and m.ended_at > m.started_at
        and (start is None or m.ended_at > start)
        and (end is None or m.ended_at < end)
    }
    return tuple(sorted(cuts))


def segment_title(base_title: str, index: int) -> str:
    """Title of the `index`-th part (0-based) of a period cut by decisive missions: "October 2026 (2)"."""
    return base_title if index == 0 else f"{base_title} ({index + 1})"
