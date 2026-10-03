"""Mission start time: local-time file name -> UTC (TD-15).

DServer names log files after the server's *local* start time (`missionReport(2026-09-19_22-34-13)[0].txt`). The
`mission_uid` stays that raw string (it's an identifier); `started_at` is the same instant in UTC. Local times are
ambiguous in the DST fall-back hour and don't exist in the spring-forward gap, so both cases are resolved here, with a
warning for the caller to put on the `IngestRun`.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

_UID = re.compile(r"(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})")


@dataclass(frozen=True, slots=True)
class ResolvedStart:
    started_at: datetime  # aware, UTC
    warnings: tuple[str, ...]


def parse_mission_uid(mission_uid: str) -> datetime:
    """`"2026-09-19_22-34-13"` -> naive `datetime(2026, 9, 19, 22, 34, 13)`. Raises ValueError if malformed."""
    m = _UID.fullmatch(mission_uid)
    if m is None:
        raise ValueError(f"malformed mission uid {mission_uid!r}, expected YYYY-MM-DD_HH-MM-SS")
    year, month, day, hour, minute, second = (int(g) for g in m.groups())
    return datetime(year, month, day, hour, minute, second)  # raises ValueError for e.g. month 13


def _round_trips(local: datetime) -> bool:
    """True if `local` (aware, in its zone) survives UTC -> local unchanged, i.e. the wall time exists."""
    tz = local.tzinfo
    assert tz is not None
    back = local.astimezone(UTC).astimezone(tz)
    return back.replace(tzinfo=None, fold=0) == local.replace(tzinfo=None, fold=0)


def resolve_mission_start(mission_uid: str, tz: ZoneInfo, hint_utc: datetime | None = None) -> ResolvedStart:
    """mission_uid "2026-09-19_22-34-13" is server-local time in `tz`. Ambiguous (fall-back) times pick the candidate
    closest to `hint_utc` (the [0] part's mtime or the zip entry time), else fold=0 with a warning. Nonexistent
    (spring-forward) times shift forward by the gap, with a warning. Raises ValueError on a malformed uid.

    A naive `hint_utc` is taken to be UTC.
    """
    naive = parse_mission_uid(mission_uid)
    early = naive.replace(tzinfo=tz, fold=0)
    late = naive.replace(tzinfo=tz, fold=1)
    early_utc = early.astimezone(UTC)
    late_utc = late.astimezone(UTC)

    if early_utc == late_utc:
        return ResolvedStart(early_utc, ())

    if not _round_trips(early):
        # Spring-forward gap: the wall time never happened. Of the two candidates, the later one (fold=0, the offset
        # from before the transition) is the wall clock moved forward by the gap (02:30 -> 03:30 for a 1 h gap).
        gap = abs(early_utc - late_utc)
        shifted = max(early_utc, late_utc)
        local_shifted = shifted.astimezone(tz).replace(tzinfo=None)
        return ResolvedStart(
            shifted,
            (
                f"mission uid {mission_uid} is not a valid local time in {tz.key} (DST gap); "
                f"shifted forward by {_fmt(gap)} to local {local_shifted:%Y-%m-%d %H:%M:%S}",
            ),
        )

    # Fall-back hour: two real instants share this wall time.
    if hint_utc is not None:
        hint = hint_utc.replace(tzinfo=UTC) if hint_utc.tzinfo is None else hint_utc.astimezone(UTC)
        chosen = min((early_utc, late_utc), key=lambda c: (abs(c - hint), c))
        return ResolvedStart(chosen, ())
    return ResolvedStart(
        early_utc,
        (
            f"mission uid {mission_uid} is ambiguous in {tz.key} (DST fall-back) and no file time was available; "
            f"assumed the earlier instant {early_utc:%Y-%m-%dT%H:%M:%SZ}",
        ),
    )


def _fmt(delta: timedelta) -> str:
    minutes = int(delta.total_seconds()) // 60
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"
