"""Online now: the running mission as the last live snapshot saved it (FR-ING-12, FR-WEB-15).

Two plain reads (the newest `LiveMission`, then its `LivePlayer` rows with their `Player`), no aggregation (TD-22).
Staleness is judged here: a snapshot older than three of its own intervals means `watch` isn't running.
Hidden players are decided at read time through the `Player` row, so hiding someone takes effect at once.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from il2ks.core.catalog.loader import Side, side_of_country
from il2ks.db.models import LiveMission, LivePlayer

STALE_AFTER_INTERVALS = 3
DEFAULT_POLL_S = 30
MIN_POLL_S = 15  # the fragment is cached for 15 s: polling faster would only hit the browser cache
_STATE_RANK = {"in_flight": 0, "on_ground": 1, "spawned": 2, "connected": 3}
_SIDE_RANK: dict[Side | None, int] = {"redfor": 0, "blufor": 1, None: 2}
_DIRS = re.compile(r"[\\/]")


@dataclass(frozen=True, slots=True)
class AircraftRef:
    """What `{% aircraft_icon %}` needs: the log name and propulsion of the aircraft."""

    log_name: str
    propulsion: str
    display_name: str


@dataclass(frozen=True, slots=True)
class OnlineRow:
    name: str  # as saved; "" for a hidden player (the template shows "Hidden player") or one still joining
    is_hidden: bool
    player_id: int | None  # for a link; None for hidden or not-yet-known players
    side: Side | None
    aircraft: AircraftRef | None  # None without an open sortie
    state: str  # a `db.models.LiveState` value
    flight_time_s: float
    kills_air: int
    kills_ground: int


@dataclass(frozen=True, slots=True)
class OnlineNow:
    running: bool  # a mission is running and its snapshot is fresh
    mission_name: str = ""
    elapsed_s: float = 0.0
    updated_at: datetime | None = None  # the snapshot's time; "last seen" when not running
    rows: tuple[OnlineRow, ...] = ()
    redfor: int = 0
    blufor: int = 0
    unassigned: int = 0  # online but no side yet (connected, nothing spawned in this mission)
    poll_s: int = DEFAULT_POLL_S

    @property
    def total(self) -> int:
        return len(self.rows)


def mission_name(mission_file: str, fallback: str) -> str:
    """'Missions\\korea_dogfight.msnbin' -> 'korea_dogfight'; the mission UID when the file name is missing."""
    base = _DIRS.split(mission_file.strip())[-1]
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return stem or fallback


def is_stale(mission: LiveMission, now: datetime) -> bool:
    return now - mission.updated_at > timedelta(seconds=STALE_AFTER_INTERVALS * mission.interval_s)


def _row(p: LivePlayer) -> OnlineRow:
    hidden = p.player is not None and p.player.is_hidden
    aircraft = (
        AircraftRef(p.aircraft_type, p.propulsion, p.aircraft_name or p.aircraft_type) if p.aircraft_type else None
    )
    return OnlineRow(
        name="" if hidden else p.name,
        is_hidden=hidden,
        player_id=None if hidden else p.player_id,
        side=side_of_country(p.country) if p.country else None,
        aircraft=aircraft,
        state=p.state,
        flight_time_s=p.flight_time_s,
        kills_air=p.kills_air,
        kills_ground=p.kills_ground,
    )


def current(now: datetime | None = None) -> OnlineNow:
    """The online-now view: players of the running mission, or `running=False` with a "last seen" time."""
    now = now or datetime.now(UTC)
    mission = LiveMission.objects.order_by("-updated_at").first()
    if mission is None:
        return OnlineNow(running=False)
    poll_s = max(MIN_POLL_S, round(mission.interval_s))
    if not mission.is_running or is_stale(mission, now):
        return OnlineNow(running=False, updated_at=mission.updated_at, poll_s=poll_s)
    rows = sorted(
        (_row(p) for p in LivePlayer.objects.filter(mission=mission).select_related("player")),
        key=lambda r: (_SIDE_RANK[r.side], _STATE_RANK.get(r.state, 9), r.is_hidden, r.name.casefold()),
    )
    return OnlineNow(
        running=True,
        mission_name=mission_name(mission.mission_file, mission.mission_uid),
        elapsed_s=mission.elapsed_s,
        updated_at=mission.updated_at,
        rows=tuple(rows),
        redfor=sum(1 for r in rows if r.side == "redfor"),
        blufor=sum(1 for r in rows if r.side == "blufor"),
        unassigned=sum(1 for r in rows if r.side is None),
        poll_s=poll_s,
    )
