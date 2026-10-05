"""The ingestion pipeline (doc 04): lock, reconcile, discover, then per mission archive -> parse -> replay -> persist.

One mission = one transaction (FR-ING-5, NFR-REL-1). A failing mission records `IngestRun(failed, traceback,
next_retry_at)` and the run goes on with the next one (NFR-REL-3). All state lives in the DB (NFR-REL-2).

The parse/replay/persist functions come in through `Pipeline`, so tests run the orchestration with fakes.
"""

from __future__ import annotations

import logging
import time
import traceback
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from django.db import transaction

from il2ks import __version__
from il2ks.config import AfterArchive, Config
from il2ks.core.catalog.loader import Catalog, country_side_warnings, load_default_catalog
from il2ks.core.logparse.events import LogEvent
from il2ks.core.logparse.files import MissionLog, MissionLogKind, group_mission_files, parse_mission
from il2ks.core.logparse.parser import ParseStats
from il2ks.core.replay.result import MissionResult
from il2ks.core.replay.state import run as replay_run
from il2ks.db.models import IngestRun, IngestStatus, Mission
from il2ks.db.site import bump_data_version
from il2ks.ingest.aggregates import rebuild_aggregates
from il2ks.ingest.archive import (
    ArchiveError,
    archive_matches,
    archive_path_for,
    dispose_originals,
    write_archive,
)
from il2ks.ingest.batch import Level2Batch, finish_quietly, is_batch, repair_pending
from il2ks.ingest.discover import (
    INGEST_DECISIONS,
    Decision,
    Found,
    LastRun,
    classify,
    discover,
    list_log_files,
    next_retry,
)
from il2ks.ingest.lock import WriterLock
from il2ks.ingest.persist import DuplicateSortieError, MissionMeta, save_level1, save_mission
from il2ks.ingest.rule_store import effective_config
from il2ks.ingest.timeutil import ResolvedStart, resolve_mission_start

log = logging.getLogger(__name__)

PROTECTED_LOG_DIR = "sample_data"
"""A log folder with this name in its path is never ingested with `after_archive` move or delete."""

_IN_CHUNK = 500  # stay far below SQLite's bound-parameter limit


@dataclass(frozen=True, slots=True)
class Pipeline:
    """The swappable steps. `default_pipeline` wires the real ones; tests pass fakes."""

    group: Callable[[Iterable[Path], MissionLogKind], list[MissionLog]]  # (paths, what a plain `[0].txt` is)
    parse: Callable[[MissionLog, ParseStats], Iterable[LogEvent]]
    replay: Callable[[Iterable[LogEvent]], MissionResult]
    save: Callable[[MissionResult, MissionMeta], Mission]
    resolve_start: Callable[[str, ZoneInfo, datetime | None], ResolvedStart]
    save_level1: Callable[[MissionResult, MissionMeta], tuple[Mission, set[int]]] | None = None
    """Level 1 only, returning the ids of the tours level 2 must refresh: what a batched run (`ingest.batch`) saves
    with. None (a test's fake pipeline): the run always uses `save`, i.e. level 2 per mission."""


def default_pipeline(cfg: Config, *, defer_ratings: bool = False) -> Pipeline:
    """The real steps. The catalog is loaded on first use, once per process (TD-11).

    `defer_ratings`: `save` doesn't replay the Elo ratings; the caller does it once afterwards (`reprocess`)."""
    catalog: list[Catalog] = []

    def get_catalog() -> Catalog:
        if not catalog:
            catalog.append(load_default_catalog())
        return catalog[0]

    def replay(events: Iterable[LogEvent]) -> MissionResult:
        return replay_run(events, get_catalog(), effective_config(cfg).replay)

    def save(result: MissionResult, meta: MissionMeta) -> Mission:
        rules = effective_config(cfg)  # the admin's applied rules, read per mission: `watch` lives for weeks
        return save_mission(
            result,
            meta,
            get_catalog(),
            None if defer_ratings else rules.ratings,
            rules.tours,
            marks=None if defer_ratings else rules.marks,
            score=rules.score,
        )

    def save_l1(result: MissionResult, meta: MissionMeta) -> tuple[Mission, set[int]]:
        rules = effective_config(cfg)
        mission, touched = save_level1(result, meta, get_catalog(), rules.tours, rules.score)
        bump_data_version()  # TD-28: the pages changed now; level 2 follows at the next 10%
        return mission, touched

    def group(paths: Iterable[Path], txt_as: MissionLogKind) -> list[MissionLog]:
        return group_mission_files(paths, txt_as=txt_as)

    return Pipeline(
        group=group,
        parse=parse_mission,
        replay=replay,
        save=save,
        resolve_start=resolve_mission_start,
        save_level1=save_l1,
    )


type Outcome = Literal["ok", "failed"]


@dataclass(slots=True)
class IngestSummary:
    ok: list[str] = field(default_factory=list[str])
    failed: list[str] = field(default_factory=list[str])
    incomplete: list[str] = field(default_factory=list[str])
    skipped: dict[str, list[str]] = field(default_factory=dict[str, list[str]])  # decision -> mission UIDs
    disposed_files: int = 0
    dispose_errors: int = 0

    def skip(self, decision: str, uid: str) -> None:
        self.skipped.setdefault(decision, []).append(uid)

    def describe(self) -> str:
        skipped = ", ".join(f"{k} {len(v)}" for k, v in sorted(self.skipped.items())) or "none"
        return (
            f"ingested {len(self.ok)}, failed {len(self.failed)}, incomplete {len(self.incomplete)}, "
            f"skipped: {skipped}; originals disposed {self.disposed_files}"
            + (f" ({self.dispose_errors} could not be moved yet)" if self.dispose_errors else "")
        )


@dataclass(frozen=True, slots=True)
class IngestOptions:
    source: Path | None = None  # `ingest --from`: a folder or one file; None = the configured log folder
    reconcile: bool = True
    lock_wait: float | None = None


def utcnow() -> datetime:
    return datetime.now(UTC)


def ingest_once(
    cfg: Config,
    pipeline: Pipeline,
    opts: IngestOptions | None = None,
    *,
    now: Callable[[], datetime] = utcnow,
    command: str = "ingest",
) -> IngestSummary:
    """Process everything that's ready, under the writer lock (FR-ING-20). Raises `LockBusyError` if it's taken."""
    opts = opts or IngestOptions()
    with WriterLock(cfg.data_dir, command, wait=opts.lock_wait):
        return _ingest_locked(cfg, pipeline, opts, now, command)


def _ingest_locked(
    cfg: Config, pipeline: Pipeline, opts: IngestOptions, now: Callable[[], datetime], command: str = "ingest"
) -> IngestSummary:
    file_cfg = cfg
    cfg = effective_config(file_cfg)  # the rules the admin applied win over the file
    repair_pending(
        partial(rebuild_aggregates, cfg.ratings, cfg.tours, marks=cfg.marks, score=cfg.score, board=cfg.board),
        cfg.ratings,
        cfg.marks,
    )
    summary = IngestSummary()
    is_import = opts.source is not None
    if opts.source is not None:
        paths = list_log_files(opts.source) if opts.source.is_dir() else [opts.source]
        mode: AfterArchive = "keep"  # an import source is never moved or deleted
    else:
        if cfg.logs.dir is None:
            raise ValueError("logs.dir is not configured (il2ks.toml [logs] dir, or IL2KS_LOGS_DIR)")
        mode = cfg.logs.after_archive
        if mode != "keep" and PROTECTED_LOG_DIR in {part.casefold() for part in cfg.logs.dir.resolve().parts}:
            # The repo's `sample_data/` is real player data kept for tests: moving or deleting it once cost a morning.
            raise ValueError(
                f"logs.dir {cfg.logs.dir} is inside a '{PROTECTED_LOG_DIR}' folder: use after_archive = \"keep\" "
                "or copy the logs elsewhere (ingest would move or delete the originals)"
            )
        paths = list_log_files(cfg.logs.dir)

    txt_as: MissionLogKind = "archive" if is_import else "parts"  # a lone `[0].txt` in an import is a whole mission

    def group(files: Iterable[Path]) -> list[MissionLog]:
        return pipeline.group(files, txt_as)

    found = discover(
        paths,
        group,
        now=now().timestamp(),
        cfg=cfg.ingest,
        remote=cfg.logs.remote,
        treat_all_complete=is_import,
    )
    complete = [f for f in found if f.complete is not None]
    summary.incomplete.extend(f.mission_uid for f in found if f.complete is None)
    last_runs = latest_runs([f.mission_uid for f in complete])

    todo: list[tuple[Found, Decision, IngestRun | None]] = []
    for item in complete:
        last = last_runs.get(item.mission_uid)
        decision = classify(item.fingerprint, _as_last_run(last), version=__version__, now=now())
        if decision == "unchanged" and last is not None and opts.reconcile and not _archive_present(cfg, last):
            # Reconcile (FR-ING-8): ingested, source files still here, archive gone -> redo it from the sources.
            log.warning(
                "%s: archive %s missing; re-ingesting from the source files", item.mission_uid, last.archive_path
            )
            decision = "changed"
        if decision not in INGEST_DECISIONS:
            summary.skip(decision, item.mission_uid)
            if decision == "unchanged" and last is not None:
                _dispose(item, mode, cfg, summary)  # leftovers of an earlier move that failed (file was locked)
            continue
        todo.append((item, decision, last))

    # Batched level 2 (doc 14): a long run saves level 1 only and applies level 2 at every 10% and at the end.
    batch = (
        # the marks are read when the thresholds are computed: the admin may save new minimums during a long run
        Level2Batch(len(todo), cfg.ratings, lambda: effective_config(file_cfg).marks, command)
        if is_batch(len(todo)) and pipeline.save_level1 is not None
        else None
    )
    failing = True
    try:
        if batch is not None:
            batch.start()  # before the first save: a hard kill from here on leaves a marker (`repair_pending`)
        for item, decision, last in todo:
            outcome = ingest_mission(cfg, pipeline, item, decision, last, now=now, batch=batch)
            if outcome == "ok":
                summary.ok.append(item.mission_uid)
                _dispose(item, mode, cfg, summary)
            else:
                summary.failed.append(item.mission_uid)
            if batch is not None:
                batch.mission_done()
        failing = False
    finally:  # also on Ctrl-C and on an error of the level-2 pass itself: what is pending is applied, not lost
        finish_quietly(batch, failing=failing)
    log.info("%s", summary.describe())
    return summary


def _dispose(item: Found, mode: AfterArchive, cfg: Config, summary: IngestSummary) -> None:
    if mode == "keep":
        return
    result = dispose_originals([f.path for f in item.files], mode, cfg.move_to / item.mission_uid)
    summary.disposed_files += len(result.done)
    summary.dispose_errors += len(result.failed)


def _archive_present(cfg: Config, run: IngestRun) -> bool:
    return bool(run.archive_path) and (cfg.data_dir / run.archive_path).is_file()


def latest_runs(uids: Sequence[str]) -> dict[str, IngestRun]:
    """The newest `IngestRun` per mission UID."""
    latest: dict[str, IngestRun] = {}
    for i in range(0, len(uids), _IN_CHUNK):
        chunk = uids[i : i + _IN_CHUNK]
        for run in IngestRun.objects.filter(mission_uid__in=chunk).order_by("mission_uid", "-started_at", "-id"):
            latest.setdefault(run.mission_uid, run)
    return latest


def last_archived_run(uid: str) -> IngestRun | None:
    """The newest run that wrote an archive: its `files` are exactly what that archive holds."""
    return IngestRun.objects.filter(mission_uid=uid).exclude(archive_sha256="").order_by("-started_at", "-id").first()


def _as_last_run(run: IngestRun | None) -> LastRun | None:
    if run is None:
        return None
    status: Literal["ok", "failed", "skipped"]
    if run.status == IngestStatus.OK:
        status = "ok"
    elif run.status == IngestStatus.FAILED:
        status = "failed"
    else:
        status = "skipped"
    return LastRun(status, run.fingerprint, run.attempts, run.next_retry_at, run.il2ks_version)


@dataclass(frozen=True, slots=True)
class SourcePlan:
    sources: tuple[Path, ...]  # what goes into the archive, in order
    names: tuple[str, ...]  # file names the archive will hold (recorded on IngestRun.files)
    warnings: tuple[str, ...] = ()


def plan_sources(item: Found, previous: IngestRun | None, data_dir: Path) -> SourcePlan:
    """What to archive. Normally the mission's files. For late parts after the originals were moved or deleted
    (FR-ING-18): the previous archive plus the parts it doesn't hold yet, so the new archive is the whole mission."""
    current = tuple(f.path for f in item.files)
    names = tuple(p.name for p in current)
    if previous is None or item.log.kind == "archive" or set(previous.files) <= set(names):
        return SourcePlan(current, names)
    archive = data_dir / previous.archive_path
    if not archive_matches(archive, previous.archive_sha256):
        raise ArchiveError(
            f"{item.mission_uid}: new parts found, but the earlier parts are gone and their archive {archive} is "
            "missing or changed, so the whole mission can't be rebuilt"
        )
    known = set(previous.files)
    new = tuple(p for p in current if p.name not in known)
    warnings: tuple[str, ...] = ()
    reappeared = [p.name for p in current if p.name in known]
    if reappeared:
        warnings = (f"{len(reappeared)} part(s) reappeared after archiving; kept the archived copy: {reappeared[:5]}",)
    return SourcePlan((archive, *new), (*previous.files, *(p.name for p in new)), warnings)


def _first_part_mtime(item: Found) -> datetime | None:
    """TD-15 hint: part [0]'s modification time (UTC), written seconds after the mission start."""
    first = item.files[0] if item.files else None
    if first is None or item.log.kind != "parts" or not first.path.name.endswith("[0].txt"):
        return None
    return datetime.fromtimestamp(first.mtime, UTC)


def ingest_mission(
    cfg: Config,
    pipeline: Pipeline,
    item: Found,
    decision: Decision,
    last: IngestRun | None,
    *,
    now: Callable[[], datetime] = utcnow,
    batch: Level2Batch | None = None,
) -> Outcome:
    """Archive, parse, replay and save one mission, recording an `IngestRun` either way. With a `batch` only level 1 is
    saved (one transaction, as ever) and what level 2 must recompute goes to the batch once the save committed."""
    uid = item.mission_uid
    t0 = time.monotonic()
    run = IngestRun(
        mission_uid=uid,
        files=[f.path.name for f in item.files],
        fingerprint=item.fingerprint,
        status=IngestStatus.OK,
        completion_reason=item.complete or "",
        il2ks_version=__version__,
        started_at=now(),
    )
    stats = ParseStats()
    try:
        plan = plan_sources(item, last_archived_run(uid) if decision != "new" else None, cfg.data_dir)
        first = item.files[0].mtime if item.files else None
        target = archive_path_for(cfg.archive_dir, uid)
        archived = write_archive(
            plan.sources, target, uid, datetime.fromtimestamp(first) if first is not None else None
        )
        run.files = list(plan.names)
        run.archive_path = target.relative_to(cfg.data_dir).as_posix()
        run.archive_sha256 = archived.sha256
        events = pipeline.parse(MissionLog(uid, "archive", (archived.path,)), stats)
        result = pipeline.replay(events)
        start = pipeline.resolve_start(uid, cfg.timezone, _first_part_mtime(item))
        meta = MissionMeta(cfg.server_uid, uid, start.started_at, run.archive_path)
        side_warnings = country_side_warnings(result.mission.countries)
        fill_counters(run, stats, (*plan.warnings, *start.warnings, *side_warnings))
        with transaction.atomic():
            if batch is not None and pipeline.save_level1 is not None:
                mission, touched_tours = pipeline.save_level1(result, meta)
                batch.add(touched_tours)  # in the transaction: the marker names the tours when the save is in
            else:
                mission = pipeline.save(result, meta)
            run.mission = mission
            run.finished_at = now()
            run.save()
    except Exception as exc:
        error = str(exc) if isinstance(exc, DuplicateSortieError) else traceback.format_exc()  # a known, explained case
        same_failure = (
            last is not None
            and last.status == IngestStatus.FAILED
            and last.fingerprint == item.fingerprint
            and last.il2ks_version == __version__
        )
        run.pk = None
        run.mission = None
        run.status = IngestStatus.FAILED
        run.attempts = (last.attempts + 1) if (same_failure and last is not None) else 1
        run.next_retry_at = next_retry(run.attempts, cfg.ingest.retry_backoff, now())
        run.error = error
        run.finished_at = now()
        fill_counters(run, stats, ())
        run.save()
        log.error("%s failed (attempt %d, next retry %s):\n%s", uid, run.attempts, run.next_retry_at, error)
        return "failed"
    log.info("%s ingested (%s, %.1f s, %d lines)", uid, decision, time.monotonic() - t0, stats.lines_total)
    return "ok"


def fill_counters(run: IngestRun, stats: ParseStats, extra_warnings: Sequence[str]) -> None:
    run.lines_total = stats.lines_total
    run.lines_bad = stats.lines_bad
    run.log_version = stats.log_version
    run.unknown_atypes = {str(k): v for k, v in stats.unknown_atypes.items()}
    run.unknown_keys = dict(stats.unknown_keys)
    run.warnings = [*extra_warnings, *stats.warnings]
