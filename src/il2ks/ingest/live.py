"""Online now: follow the in-progress mission and keep a provisional snapshot of it in the database (FR-ING-12).

`LiveTracker.tick()` runs inside `il2ks watch`, between and after the normal ingest ticks:

1. find the **in-progress mission**: the newest mission in the log folder whose parts are raw (not an archive) and which
   `discover.completeness` doesn't call complete yet;
2. read only the lines written since the last tick (`PartTail`: one byte offset per part, a new part starts at its
   first byte) into a long-lived `LiveReplay`, the same `Replay` ingest uses, so there is no second rule set;
3. every `[live] interval_s` take `snapshot()` and replace the `LiveMission` / `LivePlayer` rows in one transaction.

When the mission completes (AType 7 seen, or discovery calls it complete, or its files are gone because ingest moved
them) the players are deleted and `LiveMission.is_running` turns False. A restarted `watch` simply begins with no
state and re-reads the parts from the start; so does a part that shrinks or appears out of order.

Locking: live writes touch only the two `Live*` tables and take **no writer lock**: a normal ingest of a completed
mission never waits for a live snapshot (each is one short transaction) and the reverse is bounded by SQLite's busy
timeout. They never bump the data version (TD-28: live data is outside that scheme).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from django.db import transaction

from il2ks.config import Config
from il2ks.core.catalog.loader import Catalog, load_default_catalog
from il2ks.core.logparse.events import TICKS_PER_SECOND
from il2ks.core.logparse.files import MissionLog, group_mission_files
from il2ks.core.logparse.tail import PartTail
from il2ks.core.replay.live import LiveReplay, LiveView, OnlinePlayer
from il2ks.db.models import GameObject, LiveMission, LivePlayer, Player
from il2ks.ingest.discover import FileState, completeness, list_log_files
from il2ks.ingest.timeutil import resolve_mission_start

log = logging.getLogger(__name__)

SNAPSHOT_DUE_FRACTION = 0.8
"""A tick that comes a little early (the loop's timing jitters) still takes the snapshot, so a 30 s interval isn't
turned into 60 s by a tick that arrives after 29.9 s."""


@dataclass(slots=True)
class _Running:
    uid: str
    tail: PartTail
    replay: LiveReplay
    started_at: datetime
    last_snapshot: datetime | None = None


def find_in_progress(cfg: Config, now: datetime) -> MissionLog | None:
    """The newest raw-parts mission in the log folder that isn't complete by the ingest rules, or None."""
    if cfg.logs.dir is None or not cfg.logs.dir.is_dir():
        return None
    logs = group_mission_files(list_log_files(cfg.logs.dir), txt_as="parts")
    if not logs or logs[-1].kind != "parts":
        return None
    newest = logs[-1]
    try:
        files = tuple(FileState.of(p) for p in newest.files)
    except FileNotFoundError:
        return None  # moved while we looked
    reason = completeness(
        newest, files, newer_mission_exists=False, now=now.timestamp(), cfg=cfg.ingest, remote=cfg.logs.remote
    )
    return newest if reason is None else None


class LiveTracker:
    """Holds the in-memory live state of one watch process. Not thread safe: `watch` calls it from its one thread."""

    def __init__(self, cfg: Config, load_catalog: Callable[[], Catalog] = load_default_catalog) -> None:
        self._cfg = cfg
        self._load_catalog = load_catalog
        self._catalog: Catalog | None = None
        self._running: _Running | None = None
        self._finished: set[str] = set()  # missions whose AType 7 we saw: not resumed (that would re-read them)
        self._published_running: bool | None = None  # what the DB says; None until the first tick has looked

    @property
    def catalog(self) -> Catalog:
        if self._catalog is None:
            self._catalog = self._load_catalog()
        return self._catalog

    @property
    def following(self) -> str | None:
        """UID of the mission being followed right now."""
        return self._running.uid if self._running is not None else None

    def tick(self, now: datetime) -> None:
        """One look: read what's new, snapshot when due, clear when there's nothing running."""
        mission = find_in_progress(self._cfg, now)
        if mission is None or mission.mission_uid in self._finished:
            self._stop()
            return
        if self._running is None or self._running.uid != mission.mission_uid:
            self._begin(mission)
        running = self._running
        assert running is not None
        lines = running.tail.read_new(mission.files)
        if lines is None:  # the files changed under us: start over from the first byte
            log.info("%s: live reading restarts (a part changed or came out of order)", mission.mission_uid)
            self._begin(mission)
            running = self._running
            assert running is not None
            lines = running.tail.read_new(mission.files) or []
        running.replay.feed_lines(lines)
        interval = timedelta(seconds=self._cfg.live.interval_s * SNAPSHOT_DUE_FRACTION)
        if running.last_snapshot is not None and now - running.last_snapshot < interval:
            return
        view = running.replay.snapshot()
        running.last_snapshot = now
        if view.ended:
            self._finished.add(running.uid)
            self._running = None
        self._publish(running, view, now)

    # --- state ------------------------------------------------------------------------------------------------------

    def _begin(self, mission: MissionLog) -> None:
        first = mission.files[0] if mission.files else None
        hint = datetime.fromtimestamp(first.stat().st_mtime, UTC) if first is not None and first.exists() else None
        started = resolve_mission_start(mission.mission_uid, self._cfg.timezone, hint).started_at
        self._running = _Running(mission.mission_uid, PartTail(), LiveReplay(self.catalog, self._cfg.replay), started)

    def _stop(self) -> None:
        """Nothing is running (any more): forget the replay and tell the database, once."""
        self._running = None
        if self._published_running is not False:
            clear_live(self._cfg.server_uid)
            self._published_running = False

    # --- database ---------------------------------------------------------------------------------------------------

    def _publish(self, running: _Running, view: LiveView, now: datetime) -> None:
        started = time.perf_counter()
        store_snapshot(
            self._cfg,
            running.uid,
            running.started_at,
            view,
            now,
            self.catalog,
        )
        self._published_running = not view.ended
        log.debug(
            "live snapshot of %s: %d players, %d lines read (%d bad), stored in %.0f ms",
            running.uid,
            len(view.players),
            running.replay.lines,
            running.replay.bad_lines,
            1000 * (time.perf_counter() - started),
        )


def clear_live(server_uid: object) -> None:
    """The mission is over or none is running: drop the player rows, keep the mission row as "last seen"."""
    with transaction.atomic():
        LivePlayer.objects.filter(mission__server_uid=server_uid).delete()
        LiveMission.objects.filter(server_uid=server_uid).update(is_running=False)


def store_snapshot(
    cfg: Config, mission_uid: str, started_at: datetime, view: LiveView, now: datetime, catalog: Catalog
) -> None:
    """Replace this server's live rows with `view`, in one transaction. `view.ended` stores it as not running."""
    info = view.mission
    players = [] if view.ended else list(view.players)
    known = {
        account: (pk, name)
        for account, pk, name in Player.objects.filter(account_uuid__in=[p.account_uuid for p in players]).values_list(
            "account_uuid", "pk", "current_name"
        )
    }
    types = {p.aircraft_type for p in players if p.aircraft_type}
    objects = {o.log_name: o for o in GameObject.objects.filter(log_name__in=types)}
    with transaction.atomic():
        mission, _ = LiveMission.objects.update_or_create(
            server_uid=cfg.server_uid,
            defaults={
                "mission_uid": mission_uid,
                "mission_file": info.mission_file if info is not None else "",
                "started_at": started_at,
                "game_date": info.game_date if info is not None else "",
                "game_time": info.game_time if info is not None else "",
                "elapsed_s": view.tick / TICKS_PER_SECOND,
                "updated_at": now,
                "interval_s": cfg.live.interval_s,
                "is_running": not view.ended,
            },
        )
        LivePlayer.objects.filter(mission=mission).delete()
        LivePlayer.objects.bulk_create(
            _row(mission, p, started_at, known.get(p.account_uuid), objects, catalog) for p in players
        )


def _row(
    mission: LiveMission,
    p: OnlinePlayer,
    mission_started: datetime,
    known: tuple[int, str] | None,
    objects: dict[str, GameObject],
    catalog: Catalog,
) -> LivePlayer:
    display = ""
    propulsion = ""
    if p.aircraft_type:
        obj = objects.get(p.aircraft_type)
        if obj is not None:
            display, propulsion = obj.display_name, obj.propulsion
        else:
            info = catalog.lookup(p.aircraft_type)
            display, propulsion = info.display_name, info.propulsion or ""
    return LivePlayer(
        mission=mission,
        player_id=known[0] if known is not None else None,
        account_uuid=p.account_uuid,
        name=p.name or (known[1] if known is not None else ""),
        coalition=p.coalition,
        country=p.country,
        aircraft_type=p.aircraft_type,
        aircraft_name=display,
        propulsion=propulsion,
        state=p.state,
        sortie_started_at=(
            mission_started + timedelta(seconds=p.sortie_spawn_tick / TICKS_PER_SECOND)
            if p.sortie_spawn_tick is not None
            else None
        ),
        flight_time_s=p.flight_time_s,
        kills_air=p.kills_air,
        kills_ground=p.kills_ground,
    )
