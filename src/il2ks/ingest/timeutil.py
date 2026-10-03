"""Mission start time from the local-time file name (TD-15).

STAND-IN on the ingest-jobs branch: the catalog agent owns the real implementation (DST rules). Same signature.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class ResolvedStart:
    started_at: datetime  # aware, UTC
    warnings: tuple[str, ...] = ()


def resolve_mission_start(mission_uid: str, tz: ZoneInfo, hint_utc: datetime | None = None) -> ResolvedStart:
    """Naive: interpret the timestamp in `tz` (fold=0) and convert to UTC. `hint_utc` is ignored here."""
    local = datetime.strptime(mission_uid, "%Y-%m-%d_%H-%M-%S").replace(tzinfo=tz)
    return ResolvedStart(started_at=local.astimezone(UTC))
