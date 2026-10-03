"""`il2ks watch`: run `ingest` every N seconds until stopped (TD-06, doc 04 process model).

With `[live] enabled` it also follows the in-progress mission for "online now": a live tick right after every ingest
tick and then one every `[live] interval_s` while waiting for the next one (`ingest.live`, FR-ING-12). Live ticks take
no writer lock and never stop the loop.

The writer lock is taken per tick, not for the whole lifetime, so an admin's `reprocess` can run while `watch` is up:
ticks that find the lock taken are skipped with a log line. A tick that crashes is logged and the loop goes on
(NFR-REL-2: state lives in the DB, so the next tick picks up where this one stopped).

A running reprocess lets live ticks run between missions (`run_pending_request(between=...)`), so the live data
doesn't go stale while it works.

Each tick also works off the admin's "reprocess all missions" request, if one is pending (`reprocess_requests`): the
web process never runs that long job itself. A tick that starts one is busy until it ends; a busy lock only delays it.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta

from django.db import OperationalError

from il2ks.config import Config
from il2ks.ingest.live import LiveTracker
from il2ks.ingest.lock import LockBusyError
from il2ks.ingest.reprocess import reprocess
from il2ks.ingest.reprocess_requests import ReprocessFn, fail_interrupted_requests, run_pending_request
from il2ks.ingest.runner import IngestOptions, Pipeline, default_pipeline, ingest_once, utcnow
from il2ks.ops.backup import backup_if_due

log = logging.getLogger(__name__)


BACKUP_RETRY = timedelta(hours=1)


def daily_backup(cfg: Config, at: datetime, retry_at: datetime | None) -> datetime | None:
    """Make the daily backup if one is due (FR-OPS-6). A failure is logged and tried again an hour later, so a full
    disk doesn't turn into one error per tick. Returns the time before which no new attempt is made."""
    if retry_at is not None and at < retry_at:
        return retry_at
    try:
        backup_if_due(cfg, at)
    except Exception:
        log.exception("daily backup failed; trying again in an hour")
        return at + BACKUP_RETRY
    return None


def _wait(stop: threading.Event, seconds: float) -> None:
    """`stop.wait(seconds)` in half-second slices. On Windows a signal handler (the Ctrl+Break `il2ks run` stops us
    with) only runs between bytecodes, never inside one long wait."""
    deadline = time.monotonic() + seconds
    while not stop.is_set() and (remaining := deadline - time.monotonic()) > 0:
        stop.wait(min(remaining, 0.5))


def _wait_with_live(stop: threading.Event, seconds: float, every: float, live_tick: Callable[[], None]) -> None:
    """`_wait(stop, seconds)`, but `live_tick()` runs after each `every` seconds of it (not at the very end: the next
    ingest tick is followed by one anyway)."""
    deadline = time.monotonic() + seconds
    while not stop.is_set() and (remaining := deadline - time.monotonic()) > 0:
        _wait(stop, min(every, remaining))
        if not stop.is_set() and deadline - time.monotonic() > 0.01:
            live_tick()


def watch(
    cfg: Config,
    pipeline: Pipeline,
    *,
    stop: threading.Event | None = None,
    max_ticks: int | None = None,
    now: Callable[[], datetime] = utcnow,
    reprocess_pipeline: Callable[[], Pipeline] | None = None,
    reprocess_fn: ReprocessFn = reprocess,
) -> int:
    """Loop until `stop` is set, `max_ticks` ticks ran, or Ctrl+C (KeyboardInterrupt propagates). Returns ticks run."""
    stop = stop or threading.Event()
    reprocess_pipeline = reprocess_pipeline or (lambda: default_pipeline(cfg, defer_ratings=True))
    try:
        fail_interrupted_requests(now())
    except Exception:
        log.exception("could not check for interrupted reprocess requests")
    reconciled = False
    backup_retry_at: datetime | None = None
    ticks = 0
    tracker = LiveTracker(cfg) if cfg.live.enabled and cfg.logs.dir is not None else None

    def live_tick() -> None:
        """One look at the running mission. Whatever goes wrong here is logged; ingest and the loop go on."""
        if tracker is None:
            return
        try:
            tracker.tick(now())
        except OperationalError as exc:  # the database was busy or locked: the next live tick just tries again
            log.warning("live tick skipped: %s", exc)
        except Exception:
            log.exception("live tick failed; retrying next tick")

    last_live = time.monotonic()

    def live_between_missions() -> None:
        """Called by a running reprocess after each mission: a live tick when one is due, so "online now" doesn't go
        stale for the hours a full reprocess takes."""
        nonlocal last_live
        if tracker is not None and time.monotonic() - last_live >= cfg.live.interval_s:
            live_tick()
            last_live = time.monotonic()

    log.info("watching %s every %.0f s", cfg.logs.dir, cfg.ingest.watch_interval_s)
    while not stop.is_set():
        try:
            # Reconcile once per process start (FR-ING-8), not every tick.
            ingest_once(cfg, pipeline, IngestOptions(reconcile=not reconciled), now=now, command="watch")
            reconciled = True
        except LockBusyError as exc:
            log.info("skipping this tick: %s", exc)
        except Exception:
            log.exception("ingest tick failed; retrying next tick")
        try:
            run_pending_request(
                cfg, reprocess_pipeline, reprocess_fn=reprocess_fn, now=now, between=live_between_missions
            )
        except Exception:
            log.exception("reprocess request tick failed; retrying next tick")
        backup_retry_at = daily_backup(cfg, now(), backup_retry_at)
        live_tick()
        ticks += 1
        if max_ticks is not None and ticks >= max_ticks:
            break
        if tracker is None:
            _wait(stop, cfg.ingest.watch_interval_s)
        else:
            _wait_with_live(stop, cfg.ingest.watch_interval_s, cfg.live.interval_s, live_tick)
    return ticks
