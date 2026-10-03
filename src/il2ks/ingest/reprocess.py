"""`il2ks reprocess` and `il2ks rebuild-aggregates` (FR-ING-9, FR-ING-19, NFR-PERF-4, TD-08).

Reprocess re-runs missions from their archives (the source of truth): parse + replay in worker processes at low
priority, one writer (this process) upserting each mission in place, then a single `rebuild_aggregates()` at the end
(level 2 and the Elo ratings; the CLI's pipeline defers the ratings until then). Missions are picked from `IngestRun`
history (the newest run that wrote an archive), so failed missions with an archive can be forced too (`--mission`,
FR-ING-19). `--since/--until` pick missions by date, e.g. "the last 6 months" after a rule change (FR-ING-9, FR-ING-14).
"""

from __future__ import annotations

import logging
import os
import traceback
from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED as _FIRST_COMPLETED
from concurrent.futures import Executor, Future, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import partial
from pathlib import Path

from django.db import transaction

from il2ks import __version__
from il2ks.config import Config
from il2ks.core.logparse.files import mission_uid_from_name
from il2ks.core.logparse.parser import ParseStats
from il2ks.core.replay.config import ReplayRules
from il2ks.core.replay.result import MissionResult
from il2ks.db.models import CompletionReason, IngestRun, IngestStatus
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ingest.archive import archive_matches, file_sha256
from il2ks.ingest.lock import WriterLock
from il2ks.ingest.persist import MissionMeta
from il2ks.ingest.runner import Pipeline, fill_counters, utcnow
from il2ks.ingest.worker import lower_priority, parse_and_replay

log = logging.getLogger(__name__)

type WorkFn = Callable[[str, Path, ReplayRules], tuple[MissionResult, ParseStats]]


def default_executor(workers: int) -> Executor:
    return ProcessPoolExecutor(max_workers=workers, initializer=lower_priority)


def default_workers() -> int:
    return max(1, (os.cpu_count() or 2) - 1)


@dataclass(slots=True)
class ReprocessSummary:
    ok: list[str] = field(default_factory=list[str])
    failed: list[str] = field(default_factory=list[str])
    missing: list[str] = field(default_factory=list[str])  # requested UIDs without an archive

    def describe(self) -> str:
        text = f"reprocessed {len(self.ok)}, failed {len(self.failed)}"
        return text + (f", no archive for {len(self.missing)}: {', '.join(self.missing)}" if self.missing else "")


def mission_in_span(mission_uid: str, since: date | None, until: date | None) -> bool:
    """Is the mission's date within `since..until` (both inclusive, either may be None)?

    The date is the one in the mission UID (`YYYY-MM-DD_HH-MM-SS`), i.e. the server's local time, which is how admins
    think about "missions since 1 April". It is not `Mission.started_at` (UTC): near midnight the two can differ by a
    day. ISO dates compare correctly as text, so no parsing is needed."""
    day = mission_uid[:10]
    return (since is None or day >= since.isoformat()) and (until is None or day <= until.isoformat())


def archived_targets(
    cfg: Config, mission_uids: Sequence[str] | None, since: date | None = None, until: date | None = None
) -> dict[str, IngestRun]:
    """What to reprocess, per mission UID (all missions, or just the given ones), limited to `since..until`.

    With both a UID list and a date span, only the listed missions inside the span are chosen (the intersection).

    The newest archive-writing `IngestRun` of each mission. Archives on disk with no run at all (a lost or rebuilt
    database, FR-ING-9: "rebuild the DB from archived logs") are adopted as unsaved runs, so `reprocess` can bring
    a mission back from its archive alone."""
    runs = IngestRun.objects.exclude(archive_sha256="")
    if mission_uids:
        runs = runs.filter(mission_uid__in=list(mission_uids))
    targets: dict[str, IngestRun] = {}
    for run in runs.order_by("mission_uid", "-started_at", "-id"):
        if mission_in_span(run.mission_uid, since, until):
            targets.setdefault(run.mission_uid, run)
    wanted = set(mission_uids or ())
    for path in sorted(cfg.archive_dir.glob("*/*/*.txt.zip")):
        uid = mission_uid_from_name(path.name)
        if uid is None or uid in targets or (wanted and uid not in wanted) or not mission_in_span(uid, since, until):
            continue
        if IngestRun.objects.filter(mission_uid=uid).exists():
            continue  # has runs, none of which wrote this archive: not ours to adopt
        targets[uid] = IngestRun(
            mission_uid=uid,
            archive_path=path.relative_to(cfg.data_dir).as_posix(),
            archive_sha256=file_sha256(path),
        )
    return targets


def reprocess(
    cfg: Config,
    pipeline: Pipeline,
    mission_uids: Sequence[str] | None = None,
    *,
    since: date | None = None,
    until: date | None = None,
    workers: int | None = None,
    work: WorkFn = parse_and_replay,
    executor_factory: Callable[[int], Executor] = default_executor,
    rebuild: Callable[[], None] | None = None,
    now: Callable[[], datetime] = utcnow,
    lock_wait: float | None = None,
) -> ReprocessSummary:
    """Re-run missions from their archives under the writer lock (FR-ING-20). Raises `LockBusyError` if it's taken.

    `mission_uids`, `since`, `until` narrow the selection and combine as an intersection (see `mission_in_span`).
    A requested UID outside the date span is simply not selected; one inside it without an archive is `missing`."""
    with WriterLock(cfg.data_dir, "reprocess", wait=lock_wait):
        summary = ReprocessSummary()
        targets = archived_targets(cfg, mission_uids, since, until)
        wanted = {uid for uid in mission_uids or () if mission_in_span(uid, since, until)}
        summary.missing = sorted(wanted - set(targets))
        n_workers = workers or default_workers()
        window = n_workers * 2  # bound the results waiting in memory for the single writer
        pending: dict[Future[tuple[MissionResult, ParseStats]], IngestRun] = {}
        queue = sorted(targets.values(), key=lambda r: r.mission_uid)
        with executor_factory(n_workers) as executor:
            while queue or pending:
                while queue and len(pending) < window:
                    prev = queue.pop(0)
                    archive = cfg.data_dir / prev.archive_path
                    if not archive_matches(archive, prev.archive_sha256):
                        _record(cfg, pipeline, prev, None, f"archive {archive} is missing or changed", summary, now)
                        continue
                    pending[executor.submit(work, prev.mission_uid, archive, cfg.replay)] = prev
                if not pending:
                    continue
                done, _ = wait(pending, return_when=_FIRST_COMPLETED)
                for future in done:
                    prev = pending.pop(future)
                    try:
                        outcome = future.result()
                    except Exception:
                        _record(cfg, pipeline, prev, None, traceback.format_exc(), summary, now)
                    else:
                        _record(cfg, pipeline, prev, outcome, "", summary, now)
        if summary.ok:
            log.info("rebuilding level-2 aggregates")
            with transaction.atomic():
                (rebuild or partial(rebuild_aggregates, cfg.ratings))()
        log.info("%s", summary.describe())
        return summary


def _record(
    cfg: Config,
    pipeline: Pipeline,
    prev: IngestRun,
    outcome: tuple[MissionResult, ParseStats] | None,
    error: str,
    summary: ReprocessSummary,
    now: Callable[[], datetime],
) -> None:
    """Save one reprocessed mission and its `IngestRun`. Failures schedule no automatic retry (admin-run command)."""
    run = IngestRun(
        mission_uid=prev.mission_uid,
        files=list(prev.files),
        fingerprint=prev.fingerprint,
        archive_path=prev.archive_path,
        archive_sha256=prev.archive_sha256,
        status=IngestStatus.OK,
        completion_reason=CompletionReason.REPROCESS,
        il2ks_version=__version__,
        started_at=now(),
    )
    if outcome is not None:
        result, stats = outcome
        try:
            start = pipeline.resolve_start(prev.mission_uid, cfg.timezone, None)
            meta = MissionMeta(cfg.server_uid, prev.mission_uid, start.started_at, prev.archive_path)
            fill_counters(run, stats, start.warnings)
            with transaction.atomic():
                run.mission = pipeline.save(result, meta)
                run.finished_at = now()
                run.save()
            summary.ok.append(prev.mission_uid)
            return
        except Exception:
            error = traceback.format_exc()
            fill_counters(run, stats, ())
    run.pk = None
    run.mission = None
    run.status = IngestStatus.FAILED
    run.error = error
    run.next_retry_at = None
    run.finished_at = now()
    run.save()
    summary.failed.append(prev.mission_uid)
    log.error("%s: reprocess failed:\n%s", prev.mission_uid, error)


def rebuild_all(cfg: Config, *, rebuild: Callable[[], None] | None = None, lock_wait: float | None = None) -> None:
    """`il2ks rebuild-aggregates`: recompute level 2 and the ratings from level 1 in one transaction, under the writer
    lock."""
    with WriterLock(cfg.data_dir, "rebuild-aggregates", wait=lock_wait), transaction.atomic():
        (rebuild or partial(rebuild_aggregates, cfg.ratings))()
