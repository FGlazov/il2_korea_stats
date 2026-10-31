"""Queue access for `ReprocessRequest` (doc 14): the admin files a request, the `watch` loop works it off.

The web process never runs the job itself (it takes minutes to hours); it only calls `request_reprocess`.
"""

from datetime import date, datetime

from django.db import IntegrityError, transaction

from il2ks.db.models import ReprocessRequest, ReprocessStatus, Tour


class AlreadyPendingError(Exception):
    """A request is already waiting to be picked up; there is only ever one."""


def request_reprocess(
    requested_by: str, now: datetime, *, since: date | None = None, until: date | None = None, wipe: bool = False
) -> ReprocessRequest:
    """File a request (every mission unless `since` / `until` narrow it). Raises `AlreadyPendingError` if one waits.
    `wipe`: "Delete all data and reprocess" (every mission, so no span)."""
    if wipe and (since is not None or until is not None):
        raise ValueError("a wipe reprocesses every mission")
    return _file(requested_by, now, since=since, until=until, wipe=wipe)


def request_tour_removal(requested_by: str, now: datetime, tour: Tour, *, keep_missions: bool) -> ReprocessRequest:
    """File "Delete tour" (`ingest.removal`): one queue, so it never runs alongside a reprocess. Raises
    `AlreadyPendingError` if a request waits."""
    return _file(
        requested_by, now, remove_tour_id=tour.pk, remove_tour_title=tour.title, remove_tour_missions=not keep_missions
    )


def request_mission_removal(requested_by: str, now: datetime, mission_ids: list[int]) -> ReprocessRequest:
    """File "Delete missions" (`ingest.removal`). Raises `AlreadyPendingError` if a request waits."""
    return _file(requested_by, now, remove_mission_ids=mission_ids)


def _file(requested_by: str, now: datetime, **fields: date | bool | int | str | list[int] | None) -> ReprocessRequest:
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
