"""`il2ks watch`: run `ingest` every N seconds until stopped (TD-06, doc 04 process model).

The writer lock is taken per tick, not for the whole lifetime, so an admin's `reprocess` can run while `watch` is up:
ticks that find the lock taken are skipped with a log line. A tick that crashes is logged and the loop goes on
(NFR-REL-2: state lives in the DB, so the next tick picks up where this one stopped).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta

from il2ks.config import Config
from il2ks.ingest.lock import LockBusyError
from il2ks.ingest.runner import IngestOptions, Pipeline, ingest_once, utcnow
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


def watch(
    cfg: Config,
    pipeline: Pipeline,
    *,
    stop: threading.Event | None = None,
    max_ticks: int | None = None,
    now: Callable[[], datetime] = utcnow,
) -> int:
    """Loop until `stop` is set, `max_ticks` ticks ran, or Ctrl+C (KeyboardInterrupt propagates). Returns ticks run."""
    stop = stop or threading.Event()
    reconciled = False
    backup_retry_at: datetime | None = None
    ticks = 0
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
        backup_retry_at = daily_backup(cfg, now(), backup_retry_at)
        ticks += 1
        if max_ticks is not None and ticks >= max_ticks:
            break
        stop.wait(cfg.ingest.watch_interval_s)
    return ticks
