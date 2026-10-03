"""Work off the admin's "reprocess all missions" requests (doc 14). Called by every `watch` tick.

The web process only files a `ReprocessRequest` (`il2ks.db.reprocess_requests`). The watch loop picks the oldest pending
one up and runs the ordinary reprocess under the writer lock: if the lock is busy (a tick's ingest, a CLI reprocess) the
request just stays pending and the next tick tries again. The row tells the admin what is going on: pending, running
with a mission count, then done (with how many succeeded, failed or had no archive) or failed (the job itself crashed).
"""

import logging
import traceback
from collections.abc import Callable
from datetime import date, datetime
from typing import Protocol

from django.db import transaction

from il2ks.config import Config
from il2ks.db.models import ReprocessRequest, ReprocessStatus
from il2ks.db.reprocess_requests import pending_request
from il2ks.ingest.lock import LockBusyError
from il2ks.ingest.reprocess import ReprocessSummary, reprocess
from il2ks.ingest.runner import Pipeline, utcnow

log = logging.getLogger(__name__)

ERROR_MAX_CHARS = 4000


class ReprocessFn(Protocol):
    """The part of `reprocess.reprocess` the request runner uses (tests pass a fake)."""

    def __call__(
        self,
        cfg: Config,
        pipeline: Pipeline,
        /,
        *,
        since: date | None,
        until: date | None,
        on_start: Callable[[int], None],
        on_progress: Callable[[ReprocessSummary], None],
    ) -> ReprocessSummary: ...


def fail_interrupted_requests(now: datetime) -> int:
    """Requests left `running` by a process that died. Call once when `watch` starts (nothing else runs requests)."""
    return ReprocessRequest.objects.filter(status=ReprocessStatus.RUNNING).update(
        status=ReprocessStatus.FAILED, finished_at=now, error="interrupted: the process stopped before it finished"
    )


def run_pending_request(
    cfg: Config,
    pipeline_factory: Callable[[], Pipeline],
    *,
    reprocess_fn: ReprocessFn = reprocess,
    now: Callable[[], datetime] = utcnow,
    between: Callable[[], None] | None = None,
) -> ReprocessRequest | None:
    """Run the oldest pending request. Returns it (finished) or None when there was nothing to do or the writer lock
    was busy (the request stays pending). `pipeline_factory` builds the pipeline with the ratings deferred.
    `between` is called after every reprocessed mission (watch uses it to keep live ticks going during the hours a
    full reprocess can take); it must not raise."""
    request = pending_request()
    if request is None:
        return None
    log.info("reprocess request #%s from %s: starting", request.pk, request.requested_by or "?")

    def started(total: int) -> None:  # the writer lock is held now
        request.status = ReprocessStatus.RUNNING
        request.started_at = now()
        request.missions_total = total
        request.save(update_fields=["status", "started_at", "missions_total"])

    def progress(summary: ReprocessSummary) -> None:
        request.missions_ok = len(summary.ok)
        request.missions_failed = len(summary.failed)
        request.save(update_fields=["missions_ok", "missions_failed"])
        if between is not None:
            between()

    try:
        summary = reprocess_fn(
            cfg, pipeline_factory(), since=request.since, until=request.until, on_start=started, on_progress=progress
        )
    except LockBusyError as exc:
        log.info("reprocess request #%s waits: %s", request.pk, exc)
        return None
    except Exception:
        log.exception("reprocess request #%s failed", request.pk)
        return _finish(request, now(), ReprocessStatus.FAILED, error=traceback.format_exc()[-ERROR_MAX_CHARS:])
    request.missions_ok = len(summary.ok)
    request.missions_failed = len(summary.failed)
    request.missions_missing = len(summary.missing)
    return _finish(request, now(), ReprocessStatus.DONE, error="")


def _finish(request: ReprocessRequest, at: datetime, status: ReprocessStatus, *, error: str) -> ReprocessRequest:
    request.status = status
    request.finished_at = at
    request.error = error
    with transaction.atomic():
        request.save()
    return request
