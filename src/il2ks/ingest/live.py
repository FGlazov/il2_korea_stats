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

Provisional sorties (FR-ING-15): the same tracked replay also saves the running mission as a real, provisional
`Mission` (`is_live`) every `[live] sorties_interval_s`: `snapshot()` resolves it with the rules of the final pass and
`persist.save_mission` upserts it by the same natural keys the final save uses, so every URL stays the same until the
mission ends. Level 1 (mission, sorties, kills) is written every pass; level 2 (profiles, boards, aircraft pages) is
recomputed for everything touched since the last time every `[live] aggregates_interval_s` (default 300 s, 0 = only at
the end), because it is the heavy part; the Elo ratings and the stat thresholds wait for the final save (they depend
on the order of missions; `ratings._games` skips live kills).
The final save is the ordinary ingest of the complete mission: it rewrites those rows and clears `is_live`. A mission
whose files vanish without being ingested, and every provisional mission when the admin switches
`SiteSettings.show_live_sorties` off, are deleted again (`discard_provisional_mission`).

Locking: the `Live*` writes take **no writer lock**: a normal ingest of a completed mission never waits for a live
snapshot (each is one short transaction) and the reverse is bounded by SQLite's busy timeout. They never bump the data
version (TD-28: live data is outside that scheme). A provisional save writes level 2 like any ingest, so it takes the
writer lock without waiting; if an admin's `reprocess` or an `ingest` holds it, that pass is skipped and the next one
tries again. Each provisional save bumps the data version in its own transaction, so cached pages refresh (TD-28).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from django.db import OperationalError, transaction

from il2ks.config import Config
from il2ks.core.catalog.loader import Catalog, load_default_catalog
from il2ks.core.logparse.events import TICKS_PER_SECOND
from il2ks.core.logparse.files import MissionLog, group_mission_files
from il2ks.core.logparse.tail import PartTail
from il2ks.core.replay.live import LiveReplay, LiveView, OnlinePlayer
from il2ks.core.replay.result import MissionResult
from il2ks.db.models import GameObject, IngestRun, IngestStatus, LiveMission, LivePlayer, Mission, Player, SiteSettings
from il2ks.db.site import bump_data_version
from il2ks.ingest.discover import FileState, completeness, list_log_files
from il2ks.ingest.lock import LockBusyError, WriterLock
from il2ks.ingest.persist import (
    DuplicateSortieError,
    MissionMeta,
    apply_level2,
    discard_provisional_mission,
    save_level1,
)
from il2ks.ingest.rule_store import effective_config
from il2ks.ingest.timeutil import resolve_mission_start

log = logging.getLogger(__name__)

SNAPSHOT_DUE_FRACTION = 0.8
"""A tick that comes a little early (the loop's timing jitters) still takes the snapshot, so a 30 s interval isn't
turned into 60 s by a tick that arrives after 29.9 s."""


SNAPSHOT_MAX_DUTY = 0.025
"""A snapshot re-resolves the whole mission so far, so it gets slower as the mission grows (about 0.8 s for a 20 MB
log). The game server shares this machine (NFR-INS-5): everything the tracker does in a tick (the snapshot, the
provisional level-1 save, the level-2 recompute) is added up, and nothing starts again before that total divided by
this has passed. One shared budget: all of it together uses at most about 2.5 % of one core, however long the mission
runs."""


@dataclass(slots=True)
class _Running:
    uid: str
    tail: PartTail
    replay: LiveReplay
    started_at: datetime
    last_snapshot: datetime | None = None
    last_persist: datetime | None = None  # the last provisional save of the sorties (or an attempt that did not save)
    saved_lines: int | None = None  # lines the replay had read at the last level-1 save: nothing new = no new save
    saving: bool = True  # False once the mission has a final save from another process: only online now goes on
    pending: set[int] = field(default_factory=set[int])  # tours of level-1 passes not yet refreshed in level 2
    last_level2: datetime | None = None


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

    def __init__(
        self,
        cfg: Config,
        load_catalog: Callable[[], Catalog] = load_default_catalog,
        cost_clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        """`cost_clock` measures what a pass costs, for the CPU cap (`SNAPSHOT_MAX_DUTY`): tests pass a fake one, so
        the outcome never depends on how fast or loaded the machine is."""
        self._cost_clock = cost_clock
        self._cfg = cfg
        self._load_catalog = load_catalog
        self._catalog: Catalog | None = None
        self._running: _Running | None = None
        self._finished: set[str] = set()  # missions whose AType 7 we saw: not resumed (that would re-read them)
        self._published_running: bool | None = None  # what the DB says; None until the first tick has looked
        self._quiet_until: datetime | None = None  # the shared CPU budget: no work before this
        self._swept_for: str | None = "-"  # the followed UID the stale provisional missions were last swept for

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
        self._sweep(running.uid)
        if self._quiet_until is not None and now < self._quiet_until:
            return
        live = self._cfg.live
        online_due = running.last_snapshot is None or now - running.last_snapshot >= timedelta(
            seconds=live.interval_s * SNAPSHOT_DUE_FRACTION
        )
        persist_due = running.saving and (
            running.last_persist is None
            or now - running.last_persist >= timedelta(seconds=live.sorties_interval_s * SNAPSHOT_DUE_FRACTION)
        )
        if persist_due and not sorties_enabled():
            persist_due = False
            running.last_persist = now  # asked again after one more interval, not at every tick
            running.saved_lines = None  # switched back on later: saved again, even without new lines
            discard_stale_provisional(self._cfg, keep="")  # the switch is off: no provisional mission stays
        if persist_due and running.saved_lines == running.replay.lines:
            persist_due = False  # nothing new since the last save: no rewrite, no new data version (TD-28)
            running.last_persist = now
        if not (online_due or persist_due or self._level2_due(running, now)):
            return
        spent = 0.0
        if online_due or persist_due:
            began = self._cost_clock()
            view = running.replay.snapshot()
            spent += self._cost_clock() - began
            running.last_snapshot = now
            if view.ended:
                self._finished.add(running.uid)
                self._running = None
                self._flush_pending(running)
                self._publish(running, view, now)
                self._quiet_until = now + timedelta(seconds=spent / SNAPSHOT_MAX_DUTY)
                return
            if online_due:
                self._publish(running, view, now)
            if persist_due:
                spent += self._persist_sorties(running, view, now)
        if self._level2_due(running, now):
            began = self._cost_clock()
            if self._flush_pending(running):
                running.last_level2 = now
            spent += self._cost_clock() - began
        self._quiet_until = now + timedelta(seconds=spent / SNAPSHOT_MAX_DUTY)

    # --- provisional sorties ----------------------------------------------------------------------------------------

    def _persist_sorties(self, running: _Running, view: LiveView, now: datetime) -> float:
        """Save the running mission provisionally, level 1 (FR-ING-15). A busy writer lock skips the pass, nothing
        else. Returns what it cost."""
        began = self._cost_clock()
        running.last_persist = now
        if view.result is None:
            return 0.0  # the snapshot failed to resolve (logged by the replay): the next one tries again
        try:
            status, touched = save_provisional(self._cfg, running.uid, running.started_at, view.result, self.catalog)
        except LockBusyError:
            log.info("%s: provisional save skipped, the writer lock is taken", running.uid)
            return 0.0
        except DuplicateSortieError as exc:
            log.warning("%s: no provisional saves for this mission. %s", running.uid, exc)
            running.saving = False  # the log itself is wrong: saying so once is enough
            return 0.0
        if status == "waiting":
            # The previous mission is not finalised yet. Look again after a whole interval, and meanwhile drop
            # provisional missions nothing will finalise (their files are gone).
            discard_stale_provisional(self._cfg, keep=None)
        elif status == "final_exists":  # `il2ks ingest` finalised it: stop saving, online now goes on
            running.saving = False
            self._flush_pending(running)
        else:
            running.pending |= touched
            running.saved_lines = running.replay.lines
        cost = self._cost_clock() - began
        log.debug("provisional level 1 of %s: %s, %.0f ms", running.uid, status, 1000 * cost)
        return cost

    def _level2_due(self, running: _Running, now: datetime) -> bool:
        every = self._cfg.live.aggregates_interval_s
        if every <= 0 or not running.pending:
            return False  # 0: the totals only move at the final save
        return running.last_level2 is None or now - running.last_level2 >= timedelta(
            seconds=every * SNAPSHOT_DUE_FRACTION
        )

    def _flush_pending(self, running: _Running) -> bool:
        """Recompute level 2 for what the passes touched since the last time (best effort: a busy lock or database
        keeps it pending for the next time). Also called before the work is dropped (end of the mission)."""
        if not running.pending:
            return True
        try:
            apply_provisional_level2(self._cfg, running.pending)
        except (LockBusyError, OperationalError):
            log.info("%s: level-2 update postponed", running.uid)
            return False
        running.pending = set()
        return True

    def _sweep(self, following: str | None) -> None:
        """Once per followed mission (and once when none is): delete provisional missions that no log file backs any
        more (the admin removed the files), and all of them when the switch is off. Provisional rows of a mission that
        still has files are left: the final save will rewrite them."""
        if self._swept_for == following:
            return
        self._swept_for = following
        discard_stale_provisional(self._cfg, keep=None if sorties_enabled() else "")

    # --- state ------------------------------------------------------------------------------------------------------

    def _begin(self, mission: MissionLog) -> None:
        first = mission.files[0] if mission.files else None
        hint = datetime.fromtimestamp(first.stat().st_mtime, UTC) if first is not None and first.exists() else None
        started = resolve_mission_start(mission.mission_uid, self._cfg.timezone, hint).started_at
        if self._running is not None:
            self._flush_pending(self._running)  # work owed to level 2 is not dropped with the old replay
        self._running = _Running(
            mission.mission_uid, PartTail(), LiveReplay(self.catalog, effective_config(self._cfg).replay), started
        )

    def _stop(self) -> None:
        """Nothing is running (any more): forget the replay and tell the database, once."""
        if self._running is not None:
            self._flush_pending(
                self._running
            )  # the mission is complete: ingest finalises it, level 2 is not left behind
        self._running = None
        if self._published_running is not False:
            clear_live(self._cfg.server_uid)
            self._published_running = False
        self._sweep(None)

    # --- database ---------------------------------------------------------------------------------------------------

    def _publish(self, running: _Running, view: LiveView, now: datetime) -> None:
        started = self._cost_clock()
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
            1000 * (self._cost_clock() - started),
        )


def sorties_enabled() -> bool:
    """The admin's "Show sorties of the running mission" switch, read at every pass (FR-ING-15). On by default."""
    value = SiteSettings.objects.filter(pk=1).values_list("show_live_sorties", flat=True).first()
    return True if value is None else value


type ProvisionalStatus = Literal["saved", "final_exists", "waiting"]


def save_provisional(
    cfg: Config, mission_uid: str, started_at: datetime, result: MissionResult, catalog: Catalog
) -> tuple[ProvisionalStatus, set[int]]:
    """Level 1 of the running mission, upserted by its natural key under the writer lock (`LockBusyError` if it is
    taken). Returns what level 2 still has to recompute for it (`apply_provisional_level2`).

    `final_exists`: the mission has a final save already, nothing is written. `waiting`: another mission is still
    provisional, i.e. the previous one is not finalised yet; its final save (full level 2 and Elo) goes first, so
    nothing is written for this one until it is done. A provisional mission whose last ingest attempt failed is
    discarded instead (it would otherwise block every later mission); its retry, if any, saves it again."""
    with WriterLock(cfg.data_dir, "watch (live sorties)"), transaction.atomic():
        if Mission.objects.filter(server_uid=cfg.server_uid, mission_uid=mission_uid, is_live=False).exists():
            return "final_exists", set()
        waiting = False
        for other in Mission.objects.filter(server_uid=cfg.server_uid, is_live=True).exclude(mission_uid=mission_uid):
            newest = IngestRun.objects.filter(mission_uid=other.mission_uid).order_by("-started_at", "-id").first()
            if newest is not None and newest.status == IngestStatus.FAILED:
                discard_provisional_mission(other)  # its final save failed: nothing will replace it, don't wait for it
            else:
                waiting = True
        if waiting:
            return "waiting", set()
        meta = MissionMeta(cfg.server_uid, mission_uid, started_at, "", live=True)
        rules = effective_config(cfg)  # what the admin applied wins over the file
        _, touched = save_level1(result, meta, catalog, rules.tours, rules.score)
        bump_data_version()
    return "saved", touched


def apply_provisional_level2(cfg: Config, touched: set[int]) -> None:
    """Refresh level 2 for the tours the provisional passes touched since the last time. No ratings, no stat
    thresholds: they wait for the final save."""
    with WriterLock(cfg.data_dir, "watch (live sorties)"), transaction.atomic():
        apply_level2(touched, None)
        bump_data_version()


def discard_stale_provisional(cfg: Config, *, keep: str | None) -> int:
    """Delete the provisional missions of this server that no log file backs any more. `keep=None`: those whose files
    are not in the log folder; `keep=""` (the switch is off): all of them. Returns how many were deleted.

    The candidates are chosen first, but the rows are looked up again under the writer lock: a concurrent `il2ks
    ingest` may have final-saved one meanwhile (and moved its files), and that one must never be deleted."""
    candidates = list(Mission.objects.filter(server_uid=cfg.server_uid, is_live=True).values_list("pk", "mission_uid"))
    if not candidates:
        return 0
    if keep is None:
        if cfg.logs.dir is None or not cfg.logs.dir.is_dir():
            return 0  # no readable log folder: can't tell, keep them
        present = {m.mission_uid for m in group_mission_files(list_log_files(cfg.logs.dir), txt_as="parts")}
        candidates = [(pk, uid) for pk, uid in candidates if uid not in present]
        if not candidates:
            return 0
    try:
        with WriterLock(cfg.data_dir, "watch (live sorties)"), transaction.atomic():
            missions = list(Mission.objects.filter(pk__in=[pk for pk, _ in candidates], is_live=True))
            for mission in missions:
                discard_provisional_mission(mission)
    except LockBusyError:
        return 0
    return len(missions)


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
