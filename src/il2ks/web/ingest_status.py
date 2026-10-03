"""What the admin's ingestion status page shows (FR-ADM-4). Read-only queries over `IngestRun` and `GameObject`.

This is an admin page, not a public view, so TD-22 doesn't bind it; the queries are still portable and bounded.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from il2ks.db.models import CompletionReason, GameObject, IngestRun, IngestStatus, Mission

RECENT_DAYS = 30  # unknown event types/keys are summed over runs of this many days
MAX_FAILED_SCANNED = 300
MAX_UNKNOWN_OBJECTS = 100
MAX_RECENT_RUNS = 500


@dataclass(frozen=True, slots=True)
class UnknownCount:
    name: str
    count: int  # occurrences over the recent runs
    missions: int  # in how many of them


@dataclass(slots=True)
class IngestOverview:
    missions_stored: int = 0
    last_run: IngestRun | None = None
    last_ok: IngestRun | None = None
    ok_recent: int = 0
    failed_recent: int = 0
    waiting_retry: list[IngestRun] = field(default_factory=list[IngestRun])
    gave_up: list[IngestRun] = field(default_factory=list[IngestRun])
    idle_completions: list[IngestRun] = field(default_factory=list[IngestRun])
    unknown_objects: list[GameObject] = field(default_factory=list[GameObject])
    unknown_atypes: list[UnknownCount] = field(default_factory=list[UnknownCount])
    unknown_keys: list[UnknownCount] = field(default_factory=list[UnknownCount])


def failed_missions(limit: int = MAX_FAILED_SCANNED) -> list[IngestRun]:
    """The latest run of every mission whose latest run failed (a later OK run clears it), newest first."""
    candidates = list(IngestRun.objects.filter(status=IngestStatus.FAILED).order_by("-started_at", "-pk")[:limit])
    uids = {run.mission_uid for run in candidates}
    latest: dict[str, IngestRun] = {}
    for run in IngestRun.objects.filter(mission_uid__in=uids).order_by("started_at", "pk"):
        latest[run.mission_uid] = run
    return sorted(
        (run for run in latest.values() if run.status == IngestStatus.FAILED),
        key=lambda run: (run.started_at, run.pk),
        reverse=True,
    )


def _unknown_counts(per_run: list[dict[str, int]]) -> list[UnknownCount]:
    totals: Counter[str] = Counter()
    missions: Counter[str] = Counter()
    for found in per_run:
        for name, count in found.items():
            totals[name] += count
            missions[name] += 1
    return [UnknownCount(name, count, missions[name]) for name, count in totals.most_common()]


def build_overview(now: datetime) -> IngestOverview:
    overview = IngestOverview(missions_stored=Mission.objects.count())
    overview.last_run = IngestRun.objects.order_by("-started_at", "-pk").first()
    overview.last_ok = IngestRun.objects.filter(status=IngestStatus.OK).order_by("-started_at", "-pk").first()

    since = now - timedelta(days=RECENT_DAYS)
    recent = IngestRun.objects.filter(started_at__gte=since)
    overview.ok_recent = recent.filter(status=IngestStatus.OK).count()
    overview.failed_recent = recent.filter(status=IngestStatus.FAILED).count()

    for run in failed_missions():
        (overview.waiting_retry if run.next_retry_at is not None else overview.gave_up).append(run)

    overview.idle_completions = list(
        IngestRun.objects.filter(status=IngestStatus.OK, completion_reason=CompletionReason.IDLE).order_by(
            "-started_at"
        )[:10]
    )
    overview.unknown_objects = list(
        GameObject.objects.filter(is_known=False).order_by("log_name")[:MAX_UNKNOWN_OBJECTS]
    )
    seen = recent.filter(status=IngestStatus.OK).only("unknown_atypes", "unknown_keys")
    seen_runs = list(seen.order_by("-started_at")[:MAX_RECENT_RUNS])
    overview.unknown_atypes = _unknown_counts([run.unknown_atypes for run in seen_runs])
    overview.unknown_keys = _unknown_counts([run.unknown_keys for run in seen_runs])
    return overview
