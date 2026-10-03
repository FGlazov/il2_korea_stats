"""Queue access for `ReprocessRequest` (doc 14): the admin files a request, the `watch` loop works it off.

The web process never runs the job itself (it takes minutes to hours); it only calls `request_reprocess`.
"""

from datetime import date, datetime

from django.db import IntegrityError, transaction

from il2ks.db.models import ReprocessRequest, ReprocessStatus


class AlreadyPendingError(Exception):
    """A request is already waiting to be picked up; there is only ever one."""


def request_reprocess(
    requested_by: str, now: datetime, *, since: date | None = None, until: date | None = None
) -> ReprocessRequest:
    """File a request (every mission unless `since` / `until` narrow it). Raises `AlreadyPendingError` if one waits."""
    try:
        with transaction.atomic():  # the partial unique index also stops two concurrent requests
            if ReprocessRequest.objects.filter(status=ReprocessStatus.PENDING).exists():
                raise AlreadyPendingError
            return ReprocessRequest.objects.create(
                requested_at=now, requested_by=requested_by[:150], since=since, until=until
            )
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
