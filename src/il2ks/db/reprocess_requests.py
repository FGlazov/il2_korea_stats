"""Queue access for `ReprocessRequest` (doc 14): the admin files a request, the `watch` loop works it off.

The web process never runs the job itself (it takes minutes to hours); it only calls `request_reprocess`.
"""

from datetime import date, datetime, time

from django.db import IntegrityError, transaction

from il2ks.core.logparse.files import SpanBound, mission_in_span
from il2ks.db.models import IngestRun, RemovedMission, ReprocessRequest, ReprocessStatus, Tour


class AlreadyPendingError(Exception):
    """A request is already waiting to be picked up; there is only ever one."""


def request_reprocess(
    requested_by: str,
    now: datetime,
    *,
    since: SpanBound | None = None,
    until: SpanBound | None = None,
    wipe: bool = False,
) -> ReprocessRequest:
    """File a request (every mission unless `since` / `until` narrow it). Raises `AlreadyPendingError` if one waits.
    `wipe`: "Delete all data and reprocess" (every mission, so no span)."""
    if wipe and (since is not None or until is not None):
        raise ValueError("a wipe reprocesses every mission")
    return _file(
        requested_by,
        now,
        since=since.date() if isinstance(since, datetime) else since,
        since_time=since.time() if isinstance(since, datetime) else None,
        until=until.date() if isinstance(until, datetime) else until,
        until_time=until.time() if isinstance(until, datetime) else None,
        wipe=wipe,
    )


def archived_missions_in_span(since: SpanBound | None, until: SpanBound | None) -> int:
    """How many missions a request for this span would reprocess, for the admin's confirmation (the runs that wrote an
    archive; `ingest.reprocess.archived_targets` also looks on the disk for archives the database lost)."""
    uids = set(IngestRun.objects.exclude(archive_sha256="").values_list("mission_uid", flat=True))
    uids -= set(RemovedMission.objects.values_list("mission_uid", flat=True))
    return sum(mission_in_span(uid, since, until) for uid in uids)


def request_tour_removal(requested_by: str, now: datetime, tour: Tour, *, keep_missions: bool) -> ReprocessRequest:
    """File "Delete tour" (`ingest.removal`): one queue, so it never runs alongside a reprocess. Raises
    `AlreadyPendingError` if a request waits."""
    return _file(
        requested_by, now, remove_tour_id=tour.pk, remove_tour_title=tour.title, remove_tour_missions=not keep_missions
    )


def request_mission_removal(requested_by: str, now: datetime, mission_ids: list[int]) -> ReprocessRequest:
    """File "Delete missions" (`ingest.removal`). Raises `AlreadyPendingError` if a request waits."""
    return _file(requested_by, now, remove_mission_ids=mission_ids)


def _file(
    requested_by: str, now: datetime, **fields: date | time | bool | int | str | list[int] | None
) -> ReprocessRequest:
    try:
        with transaction.atomic():  # the partial unique index also stops two concurrent requests
            if ReprocessRequest.objects.filter(status=ReprocessStatus.PENDING).exists():
                raise AlreadyPendingError
            return ReprocessRequest.objects.create(requested_at=now, requested_by=requested_by[:150], **fields)
    except IntegrityError as exc:
        raise AlreadyPendingError from exc


def pending_request() -> ReprocessRequest | None:
    return ReprocessRequest.objects.filter(status=ReprocessStatus.PENDING).order_by("requested_at", "pk").first()


def running_request() -> ReprocessRequest | None:
    return ReprocessRequest.objects.filter(status=ReprocessStatus.RUNNING).order_by("-started_at", "-pk").first()


def last_finished_request() -> ReprocessRequest | None:
    """The newest request that is over (done or failed)."""
    done = [ReprocessStatus.DONE, ReprocessStatus.FAILED]
    return ReprocessRequest.objects.filter(status__in=done).order_by("-finished_at", "-pk").first()
